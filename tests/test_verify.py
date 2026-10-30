import hashlib
import hmac
import time

from app.verify import verify

SECRET = "test-secret"


def sig_v2(secret, t, body):
    return hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()


class H(dict):
    def get(self, k, default=None):
        for kk, vv in self.items():
            if kk.lower() == k.lower():
                return vv
        return default


def test_v2_ok():
    body = b'{"a": 1}'
    t = int(time.time())
    h = H({"Speakr-Signature-V2": f"t={t},v1={sig_v2(SECRET, t, body)}"})
    assert verify(body, h, [SECRET], 300)[0] is True


def test_v2_rotation_grace():
    body = b"payload"
    t = int(time.time())
    h = H({"Speakr-Signature-V2": f"t={t},v1={sig_v2('old', t, body)}"})
    assert verify(body, h, [SECRET, "old"], 300)[0] is True


def test_v2_stale_timestamp():
    body = b"payload"
    t = int(time.time()) - 400
    h = H({"Speakr-Signature-V2": f"t={t},v1={sig_v2(SECRET, t, body)}"})
    ok, reason = verify(body, h, [SECRET], 300)
    assert not ok and "tolerance" in reason


def test_v2_bad_signature():
    body = b"payload"
    t = int(time.time())
    h = H({"Speakr-Signature-V2": f"t={t},v1={'0' * 64}"})
    assert verify(body, h, [SECRET], 300)[0] is False


def test_v1_fallback():
    body = b"payload"
    mac = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    h = H({"Speakr-Signature": f"sha256={mac}"})
    assert verify(body, h, [SECRET], 300)[0] is True


def test_missing_headers():
    assert verify(b"x", H({}), [SECRET], 300)[0] is False
