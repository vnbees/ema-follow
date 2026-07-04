import hashlib
import hmac
import time
from urllib.parse import urlencode


def sign_params(secret_key: str, params: dict[str, str]) -> str:
    query = urlencode(params)
    return hmac.new(secret_key.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()


def signed_params(api_key: str, secret_key: str, params: dict[str, str | int | float | bool]) -> dict[str, str]:
    payload: dict[str, str] = {k: str(v) for k, v in params.items()}
    payload["timestamp"] = str(int(time.time() * 1000))
    payload["recvWindow"] = "5000"
    payload["signature"] = sign_params(secret_key, payload)
    return payload


def auth_headers(api_key: str) -> dict[str, str]:
    return {"X-MBX-APIKEY": api_key}
