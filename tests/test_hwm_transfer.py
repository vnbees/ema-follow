import unittest
from unittest.mock import MagicMock, patch

from src.exchange.types import ExchangeClientError
from src.spot_transfer import (
    _execute_transfer,
    compute_hwm_transfer_amount,
    ensure_hwm_initialized,
    get_transfer_mode,
    sync_hwm_manual_transfers,
)


class TestHwmAmount(unittest.TestCase):
    def test_amount_is_share_of_excess(self):
        # equity 1100, hwm 1000, share 50% → 50.00
        self.assertAlmostEqual(
            compute_hwm_transfer_amount(1100.0, 1000.0, 50.0), 50.0
        )

    def test_amount_floors_to_cents(self):
        self.assertAlmostEqual(
            compute_hwm_transfer_amount(1000.333, 1000.0, 50.0), 0.16
        )

    def test_drawdown_gives_zero(self):
        self.assertEqual(compute_hwm_transfer_amount(900.0, 1000.0, 50.0), 0.0)

    def test_zero_share_gives_zero(self):
        self.assertEqual(compute_hwm_transfer_amount(1100.0, 1000.0, 0.0), 0.0)


class TestTransferMode(unittest.TestCase):
    @patch("src.spot_transfer.EXCHANGE", "binance")
    @patch("src.spot_transfer.SPOT_TRANSFER_MODE", "hwm")
    def test_hwm_on_binance(self):
        self.assertEqual(get_transfer_mode(), "hwm")

    @patch("src.spot_transfer.EXCHANGE", "bitget")
    @patch("src.spot_transfer.SPOT_TRANSFER_MODE", "hwm")
    def test_hwm_falls_back_to_pct_on_bitget(self):
        self.assertEqual(get_transfer_mode(), "pct")


class TestHwmInit(unittest.TestCase):
    @patch("src.spot_transfer.db.set_equity_hwm")
    @patch("src.spot_transfer.db.get_equity_hwm", return_value=None)
    def test_seeds_with_equity_on_first_run(self, _get, set_hwm):
        self.assertEqual(ensure_hwm_initialized(1339.0), 1339.0)
        set_hwm.assert_called_once_with(1339.0)

    @patch("src.spot_transfer.db.set_equity_hwm")
    @patch("src.spot_transfer.db.get_equity_hwm", return_value=1500.0)
    def test_keeps_existing_hwm(self, _get, set_hwm):
        self.assertEqual(ensure_hwm_initialized(1339.0), 1500.0)
        set_hwm.assert_not_called()


class TestHwmAutoSync(unittest.TestCase):
    @patch("src.spot_transfer.db.set_equity_hwm_synced_at_ms")
    @patch("src.spot_transfer.db.get_equity_hwm_synced_at_ms", return_value=None)
    @patch("src.spot_transfer.fetch_futures_transfers")
    def test_first_run_only_sets_watermark(self, fetch, _get, set_synced):
        self.assertTrue(sync_hwm_manual_transfers())
        fetch.assert_not_called()
        set_synced.assert_called_once()

    @patch("src.spot_transfer.db.get_spot_transfers", return_value=[])
    @patch("src.spot_transfer.db.set_equity_hwm_synced_at_ms")
    @patch("src.spot_transfer.db.set_equity_hwm")
    @patch("src.spot_transfer.db.get_equity_hwm", return_value=1000.0)
    @patch("src.spot_transfer.db.get_equity_hwm_synced_at_ms", return_value=1)
    @patch("src.spot_transfer.fetch_futures_transfers")
    def test_manual_deposit_raises_hwm(
        self, fetch, _synced, _get_hwm, set_hwm, set_synced, _rows
    ):
        fetch.return_value = [
            {"tranId": "555", "asset": "USDT", "income": 200.0, "time": 1_700_000_000_000},
        ]
        self.assertTrue(sync_hwm_manual_transfers())
        set_hwm.assert_called_once_with(1200.0)
        set_synced.assert_called_once()

    @patch("src.spot_transfer.db.set_equity_hwm_synced_at_ms")
    @patch("src.spot_transfer.db.set_equity_hwm")
    @patch("src.spot_transfer.db.get_equity_hwm", return_value=1000.0)
    @patch("src.spot_transfer.db.get_equity_hwm_synced_at_ms", return_value=1)
    @patch("src.spot_transfer.fetch_futures_transfers")
    def test_bot_tran_id_is_filtered(
        self, fetch, _synced, _get_hwm, set_hwm, _set_synced
    ):
        fetch.return_value = [
            {"tranId": "bot-1", "asset": "USDT", "income": -13.39, "time": 1_700_000_000_000},
        ]
        bot_row = {
            "status": "success",
            "tran_id": "bot-1",
            "transfer_date": "2026-08-03",
            "amount": 13.39,
        }
        with patch(
            "src.spot_transfer.db.get_spot_transfers", return_value=[bot_row]
        ):
            self.assertTrue(sync_hwm_manual_transfers())
        set_hwm.assert_not_called()

    @patch("src.spot_transfer.db.set_equity_hwm_synced_at_ms")
    @patch("src.spot_transfer.db.set_equity_hwm")
    @patch("src.spot_transfer.db.get_equity_hwm_synced_at_ms", return_value=1)
    @patch(
        "src.spot_transfer.fetch_futures_transfers",
        side_effect=ExchangeClientError("income down"),
    )
    def test_api_error_returns_false_and_keeps_watermark(
        self, _fetch, _synced_get, set_hwm, set_synced
    ):
        self.assertFalse(sync_hwm_manual_transfers())
        set_hwm.assert_not_called()
        set_synced.assert_not_called()


class _Balance:
    def __init__(self, available: float, equity: float):
        self.available = available
        self.account_equity = equity


class TestExecuteTransferHwm(unittest.TestCase):
    @patch("src.spot_transfer.db.insert_spot_snapshot")
    @patch("src.spot_transfer.db.set_equity_hwm")
    @patch("src.spot_transfer.db.insert_spot_transfer")
    @patch("src.spot_transfer.fetch_spot_balance", return_value=500.0)
    @patch(
        "src.spot_transfer.transfer_futures_to_spot",
        return_value={"tranId": "42"},
    )
    @patch(
        "src.spot_transfer.fetch_futures_balance",
        return_value=_Balance(available=300.0, equity=1100.0),
    )
    @patch(
        "src.spot_transfer.ensure_available_for_transfer", return_value=(True, 0)
    )
    @patch("src.spot_transfer.db.has_successful_transfer_on_date", return_value=False)
    @patch("src.spot_transfer.get_transfer_mode", return_value="hwm")
    def test_success_updates_hwm_to_equity_minus_amount(
        self,
        _mode,
        _has_success,
        _ensure,
        _balance,
        transfer,
        _spot,
        insert_transfer,
        set_hwm,
        _snapshot,
    ):
        _execute_transfer("BTCUSDT", 50.0, "2026-08-03", equity=1100.0)
        transfer.assert_called_once()
        insert_transfer.assert_called_once()
        self.assertEqual(insert_transfer.call_args.kwargs["status"], "success")
        set_hwm.assert_called_once_with(1050.0)

    @patch("src.spot_transfer.db.insert_spot_transfer")
    @patch("src.spot_transfer.db.has_transfer_row_on_date", return_value=False)
    @patch("src.spot_transfer.db.has_successful_transfer_on_date", return_value=False)
    @patch("src.spot_transfer.get_transfer_mode", return_value="hwm")
    def test_drawdown_records_skip_once(
        self, _mode, _has_success, has_row, insert_transfer
    ):
        _execute_transfer("BTCUSDT", 0.0, "2026-08-03", equity=900.0)
        insert_transfer.assert_called_once()
        self.assertEqual(insert_transfer.call_args.kwargs["status"], "skipped")

        insert_transfer.reset_mock()
        has_row.return_value = True
        _execute_transfer("BTCUSDT", 0.0, "2026-08-03", equity=900.0)
        insert_transfer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
