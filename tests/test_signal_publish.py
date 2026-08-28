"""Unit tests for coins-signal publish helpers (no network)."""

from src.signal_publish import equity_pct_from_size_mult, pnl_pct_on_margin


def test_equity_pct_from_size_mult(monkeypatch):
    monkeypatch.setattr("src.signal_publish.MARGIN_PCT", 0.01)
    assert equity_pct_from_size_mult(1.0) == 1.0
    assert equity_pct_from_size_mult(1.5) == 1.5
    assert equity_pct_from_size_mult(0.5) == 0.5
    assert equity_pct_from_size_mult(3.0) == 2.0  # clamp
    assert equity_pct_from_size_mult(None) == 1.0


def test_pnl_pct_on_margin():
    assert pnl_pct_on_margin(2.0, 10.0) == 20.0
    assert pnl_pct_on_margin(-1.0, 10.0) == -10.0
    assert pnl_pct_on_margin(1.0, 0.0) is None
    assert pnl_pct_on_margin(1.0, None) is None
