import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("WORKER_ENABLED", "0")
os.environ.setdefault("WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("SPEAKR_URL", "http://speakr.test")
os.environ.setdefault("SPEAKR_TOKEN", "test-token")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(prefix="swf-test-"), "test.db"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture()
def tc():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c
