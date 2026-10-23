"""SMC process loop. Keeps the dashboard process alive and does not trade."""

from __future__ import annotations

import logging
import time

_TICK_SEC = 5.0


def run_forever() -> None:
    """Signal page, plus OP limit orders back toward each live signal."""
    logging.info(
        "OP quay về mức tín hiệu: ký quỹ 1 USDT, đòn bẩy 10, mở và chốt bằng limit. "
        "Mọi mức còn trong 7 ngày đều được theo dõi. "
        "Giá cao hơn mức ít nhất 1% thì mỗi nến 15m xanh short; "
        "giá thấp hơn thì mỗi nến đỏ long. Nhiều mức cùng chiều chỉ một lệnh. "
        "Chốt cách giá khớp 1 ATR(14), tối thiểu đủ để sau phí còn lãi. "
        "Tổng vị thế đang lãi thì không mở thêm. "
        "Hết 7 ngày thì hủy lệnh chờ và đóng vị thế ngay bằng lệnh thị trường."
    )
    while True:
        try:
            from src.smc.revert import manage

            manage()
        except Exception as exc:  # noqa: BLE001
            logging.error("revert manage failed: %s", exc)
        time.sleep(_TICK_SEC)
