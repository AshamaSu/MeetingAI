"""Webhook 验签（对齐 speakr webhook_dispatch.py）。

V2（优先）：Speakr-Signature-V2: t=<unix>,v1=<hex>[,v1=<hex>]
  每个v1 = HMAC-SHA256(secret, f"{t}." + raw_body).hexdigest()
  校验 |now - t| <= tolerance
V1（回退）：Speakr-Signature: sha256=<hex>，HMAC(secret, raw_body)
"""
import hashlib
import hmac
import time


def _sign_v2(secret: str, t: int, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), f"{t}.".encode("utf-8") + body, hashlib.sha256)
    return mac.hexdigest()


def _sign_v1(secret: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def verify(body: bytes, headers, secrets, tolerance: int = 300) -> tuple:
    """headers: 任意大小写不敏感的映射（httpx.Headers / email.message.Message 风格均可）。
    返回 (ok: bool, reason: str)。secrets: 当前+轮换宽限期的 secret 列表。
    """
    if not secrets:
        return False, "no webhook secret configured"

    def get(name):
        try:
            return headers.get(name)
        except AttributeError:
            return None

    v2 = get("Speakr-Signature-V2") or get("speakr-signature-v2")
    if v2:
        t, sigs = None, []
        for part in v2.split(","):
            part = part.strip()
            if part.startswith("t="):
                try:
                    t = int(part[2:])
                except ValueError:
                    return False, "bad timestamp in v2 header"
            elif part.startswith("v1="):
                sigs.append(part[3:])
        if t is None or not sigs:
            return False, "v2 header missing t= or v1="
        if abs(time.time() - t) > tolerance:
            return False, "signature timestamp outside tolerance"
        for sig in sigs:
            for secret in secrets:
                if hmac.compare_digest(sig, _sign_v2(secret, t, body)):
                    return True, "v2"
        return False, "v2 signature mismatch"

    v1 = get("Speakr-Signature") or get("speakr-signature")
    if v1:
        for secret in secrets:
            if hmac.compare_digest(v1, _sign_v1(secret, body)):
                return True, "v1"
        return False, "v1 signature mismatch"

    return False, "no signature header"
