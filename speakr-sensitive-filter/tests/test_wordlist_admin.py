import pytest

from fastapi.testclient import TestClient

from app import db
from app.config import settings
from app.main import app

CSV_OK = "term,lang,level,category,variants,regex,enabled" + chr(10) + \
    "测试词,zh,L2,x,变体1 变体2,,1" + chr(10) + "新词,zh,L1,y,,,1"
CSV_BAD_REGEX = "term,lang,level,category,variants,regex,enabled" + chr(10) + \
    "坏词,zh,L2,x,,([unclosed,1"
CSV_INVALID = "完全无效的行"


@pytest.fixture()
def wordlist_path(tmp_path):
    p = tmp_path / "sensitive.csv"
    p.write_text("term,lang,level,category,variants,regex,enabled" + chr(10) + "原词,zh,L3,,,1", encoding="utf-8")
    old = settings.wordlist_path
    settings.wordlist_path = p
    yield p
    settings.wordlist_path = old


def test_get_wordlist(tc, wordlist_path):
    d = tc.get("/wordlist").json()
    assert d["terms"] == 1 and "原词" in d["content"]


def test_put_and_roundtrip(tc, wordlist_path):
    r = tc.put("/wordlist", content=CSV_OK.encode())
    assert r.status_code == 200 and r.json()["terms"] == 2
    assert r.json()["enabled_by_level"]["L1"] == 1
    # .bak 保留了旧词表
    assert "原词" in wordlist_path.with_suffix(".csv.bak").read_text(encoding="utf-8")
    # GET 回读
    assert "测试词" in tc.get("/wordlist").json()["content"]


def test_put_rejects_invalid(tc, wordlist_path):
    assert tc.put("/wordlist", content=CSV_INVALID.encode()).status_code == 422
    assert tc.put("/wordlist", content=b"").status_code == 422
    r = tc.put("/wordlist", content=CSV_BAD_REGEX.encode())
    assert r.status_code == 422 and "regex" in r.json()["detail"]


def test_rescan_all(tc):
    with db.connect(settings.db_path) as con:
        db.save_scan(con, 42, 1, "L3")
        db.save_scan(con, 43, 0, None)
    r = tc.post("/rescan-all")
    assert r.status_code == 202
    queued = r.json()["recordings"]
    assert queued >= 2
    with db.connect(settings.db_path) as con:
        jobs = db.due_jobs(con, settings.max_attempts, limit=100)
    ids = {j["recording_id"] for j in jobs}
    assert {42, 43} <= ids
