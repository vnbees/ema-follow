"""exchangeInfo must come from volume on deploy — GET /exchangeInfo 418'd Railway IPs."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.exchange import binance
from src.exchange.types import ContractSpec


def _info(*symbols: str) -> dict:
    rows = []
    for sym in symbols:
        rows.append(
            {
                "symbol": sym,
                "contractType": "PERPETUAL",
                "status": "TRADING",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                ],
            }
        )
    return {"symbols": rows}


class TestExchangeInfoPersist(unittest.TestCase):
    def setUp(self) -> None:
        binance._EXCHANGE_INFO_CACHE = None
        binance._SPEC_CACHE.clear()
        binance._rate_limited_until_ms = 0.0
        binance._rate_limit_kind = "ban"

    def tearDown(self) -> None:
        binance._EXCHANGE_INFO_CACHE = None
        binance._SPEC_CACHE.clear()
        binance._rate_limited_until_ms = 0.0

    def test_load_uses_disk_without_rest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binance_exchange_info.json"
            path.write_text(
                json.dumps({"saved_at": time.time(), "info": _info("BTCUSDT")}),
                encoding="utf-8",
            )
            with (
                patch("src.exchange.binance_ws.persist._EXCHANGE_INFO_FILE", path),
                patch("src.exchange.binance._public_get") as get,
            ):
                spec = binance.fetch_contract_spec("BTCUSDT")
            get.assert_not_called()
            self.assertIsInstance(spec, ContractSpec)
            self.assertEqual(spec.symbol, "BTCUSDT")

    def test_rest_saves_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "binance_exchange_info.json"
            with (
                patch("src.exchange.binance_ws.persist._EXCHANGE_INFO_FILE", path),
                patch(
                    "src.exchange.binance_ws.persist.load_exchange_info_snapshot",
                    return_value=None,
                ),
                patch("src.exchange.binance._public_get", return_value=_info("ETHUSDT")) as get,
            ):
                spec = binance.fetch_contract_spec("ETHUSDT")
            get.assert_called_once_with("/fapi/v1/exchangeInfo", {})
            self.assertEqual(spec.symbol, "ETHUSDT")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("ETHUSDT", [s["symbol"] for s in saved["info"]["symbols"]])
