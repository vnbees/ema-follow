import base64
import hashlib
import hmac
import time
from urllib.parse import urlencode

from src.config import BITGET_API_KEY, BITGET_PASSPHRASE, BITGET_SECRET_KEY


def build_query_string(params: dict[str, str]) -> str:
    sorted_params = sorted(params.items(), key=lambda item: item[0])
    return urlencode(sorted_params)


def _sign(timestamp: str, method: str, request_path: str, query_string: str, body: str) -> str:
    query_part = f"?{query_string}" if query_string else ""
    message = f"{timestamp}{method.upper()}{request_path}{query_part}{body}"
    signature = hmac.new(
        BITGET_SECRET_KEY.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(signature).decode("utf-8")


def build_auth_headers(
    method: str,
    request_path: str,
    query_string: str = "",
    body: str = "",
) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    return {
        "ACCESS-KEY": BITGET_API_KEY,
        "ACCESS-SIGN": _sign(timestamp, method, request_path, query_string, body),
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": BITGET_PASSPHRASE,
        "Content-Type": "application/json",
        "locale": "en-US",
    }
