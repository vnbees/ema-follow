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

    from src.spot_transfer import apply_manual_net_to_markers

    apply_manual_net_to_markers(100.0)  # deposit
    assert abs(db.get_spot_sod_equity() - 1100.0) < 1e-9
    assert abs(db.get_equity_peak() - 1300.0) < 1e-9

    apply_manual_net_to_markers(-50.0)  # manual withdraw
    assert abs(db.get_spot_sod_equity() - 1050.0) < 1e-9
    assert abs(db.get_equity_peak() - 1250.0) < 1e-9


def test_deposit_then_decide_no_false_profit():
    """After SOD+=deposit, same equity rise should not look like trading profit."""
    d = decide_skim_withdraw(sod=1200, equity=1200, peak=1200, available=500)
    assert d.reason == "no_profit"
    assert d.amount == 0.0
