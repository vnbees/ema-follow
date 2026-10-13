"""Unit tests for skim spot-transfer decision."""

from __future__ import annotations

from src.spot_transfer import decide_skim_withdraw


def test_no_profit_skips():
    d = decide_skim_withdraw(sod=1000, equity=990, peak=1000, available=500)
    assert d.reason == "no_profit"
    assert d.amount == 0.0


def test_dd_pause_skips():
    # Green day but still deep DD from peak
    d = decide_skim_withdraw(sod=1000, equity=1050, peak=1400, available=500, dd_pause=0.20)
    assert d.reason == "dd_pause"
    assert d.dd_pct >= 0.20
    assert d.amount == 0.0


def test_skim_and_cap():
    # day_pnl=100 → skim 40; SOD cap 1.5% of 1000 = 15 → min = 15
    d = decide_skim_withdraw(
        sod=1000, equity=1100, peak=1100, available=500, skim=0.4, day_cap_pct=0.015
    )
    assert d.reason == "transfer"
    assert d.amount == 15.0


def test_skim_when_cap_not_binding():
    # day_pnl=20 → skim 8; cap 1.5%*1000=15 → take 8
    d = decide_skim_withdraw(
        sod=1000, equity=1020, peak=1020, available=500, skim=0.4, day_cap_pct=0.015
    )
    assert d.reason == "transfer"
    assert d.amount == 8.0


def test_no_cash_when_available_too_low():
    d = decide_skim_withdraw(
        sod=1000, equity=1100, peak=1100, available=0.005, skim=0.4, day_cap_pct=0.015
    )
    assert d.reason == "no_cash"
    assert d.amount == 0.0


def test_available_limits_transfer():
    d = decide_skim_withdraw(
        sod=1000, equity=1100, peak=1100, available=10.0, skim=0.4, day_cap_pct=0.015
    )
    assert d.reason == "transfer"
    assert d.amount == 10.0


def test_apply_manual_net_adjusts_sod_and_peak(tmp_path, monkeypatch):
    import src.database as db

    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "t.db")
    db.init_db()
    db.set_spot_sod_equity(1000.0)
    db.set_equity_peak(1200.0)
    db.set_baseline_equity(1000.0)

    from src.spot_transfer import apply_manual_net_to_markers

    apply_manual_net_to_markers(100.0)  # deposit
    assert abs(db.get_spot_sod_equity() - 1100.0) < 1e-9
    assert abs(db.get_equity_peak() - 1300.0) < 1e-9
    assert abs(db.get_baseline_equity() - 1100.0) < 1e-9

    apply_manual_net_to_markers(-50.0)  # manual withdraw
    assert abs(db.get_spot_sod_equity() - 1050.0) < 1e-9
    assert abs(db.get_equity_peak() - 1250.0) < 1e-9
    assert abs(db.get_baseline_equity() - 1050.0) < 1e-9


def test_month_withdraw_caps_at_8_percent():
    from src.spot_transfer import decide_month_withdraw

    # profit 149 > 8% of 1051 = 84.08
    d = decide_month_withdraw(month_start=1051, equity=1200, available=500, cap_pct=0.08)
    assert d.reason == "transfer"
    assert d.amount == 84.08


def test_month_withdraw_takes_all_profit_under_cap():
    from src.spot_transfer import decide_month_withdraw

    d = decide_month_withdraw(month_start=1051, equity=1100, available=500, cap_pct=0.08)
    assert d.reason == "transfer"
    assert d.amount == 49.0


def test_month_withdraw_skips_loss():
    from src.spot_transfer import decide_month_withdraw

    d = decide_month_withdraw(month_start=1051, equity=1000, available=500, cap_pct=0.08)
    assert d.reason == "no_profit"
    assert d.amount == 0.0


def test_month_history_groups_by_month(tmp_path, monkeypatch):
    import src.database as db

    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "t.db")
    db.init_db()
    db.insert_spot_transfer(transfer_date="2026-08-02", amount=10, status="success", reason="transfer")
    db.insert_spot_transfer(transfer_date="2026-08-03", amount=5, status="success", reason="transfer")
    db.insert_spot_transfer(transfer_date="2026-09-01", amount=20, status="success", reason="month_transfer")
    db.insert_spot_transfer(transfer_date="2026-10-08", amount=15, status="success", reason="return_to_futures")
    rows = db.list_spot_withdraw_months()
    assert [(r["month"], round(r["amount"], 2)) for r in rows] == [
        ("2026-09", 20.0),
        ("2026-08", 15.0),
    ]
    total, count = db.sum_successful_spot_transfers()
    assert round(total, 2) == 20.0
    assert count == 3


def test_deposit_then_decide_no_false_profit():
    """After SOD+=deposit, same equity rise should not look like trading profit."""
    d = decide_skim_withdraw(sod=1200, equity=1200, peak=1200, available=500)
    assert d.reason == "no_profit"
    assert d.amount == 0.0
