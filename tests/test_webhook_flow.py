import asyncio
import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import db, worker
from app.config import settings
from app.lexicon import Lexicon
from app.main import app


class FakeSpeakr:
    def __init__(self):
        self.calls = []

    async def transcript(self, rid):
        return {"segments": [{"index": 0, "speaker": "张三", "start_time": 201.0,
                              "end_time": 205.0, "text": "这段讨论武器采购"}]}

    async def recording(self, rid):
        return {"tags": []}

    async def tags(self, name):
        return []

    async def create_tag(self, name, color):
        return {"id": 1}

    async def attach_tag(self, rid, tag_id):
        self.calls.append(("attach", rid, tag_id))

    async def patch_recording(self, rid, **kw):
        self.calls.append(("patch", rid, kw))

    async def notes(self, rid):
        return "原始备注"

    async def put_notes(self, rid, notes):
        self.calls.append(("notes", notes))

    async def summary(self, rid):
        return "会议总结正文"

    async def put_summary(self, rid, s):
        self.calls.append(("summary", s))

    async def me(self):
        return {"preferences": {"ui_language": "zh"}}


@pytest.fixture()
def tc():
    with TestClient(app) as c:
        yield c


def signed_headers(body, delivery_id, event):
    t = int(time.time())
    sig = hmac.new(settings.webhook_secret.encode(),
                   f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json",
            "Speakr-Signature-V2": f"t={t},v1={sig}",
            "Speakr-Delivery-Id": delivery_id, "Speakr-Event": event}


def payload(delivery_id, rid=7):
    return json.dumps({"id": delivery_id, "event": "recording.transcription.completed",
                       "data": {"recording_id": rid, "title": "测试录音"}}).encode()


def test_webhook_accept_duplicate_reject(tc):
    body = payload("d1")
    r = tc.post("/hooks/speakr", content=body,
                headers=signed_headers(body, "d1", "recording.transcription.completed"))
    assert r.status_code == 202

    # 幂等：同 delivery_id 重复投递
    r2 = tc.post("/hooks/speakr", content=body,
                 headers=signed_headers(body, "d1", "recording.transcription.completed"))
    assert r2.json() == {"status": "duplicate"}

    # 篡改 body → 验签拒绝
    r3 = tc.post("/hooks/speakr", content=b"tampered",
                 headers=signed_headers(body, "d2", "recording.transcription.completed"))
    assert r3.status_code == 401


def test_full_pipeline(tc):
    body = payload("d10", rid=7)
    assert tc.post("/hooks/speakr", content=body,
                   headers=signed_headers(body, "d10", "recording.transcription.completed")
                   ).status_code == 202

    fake = FakeSpeakr()
    with db.connect(settings.db_path) as con:
        jobs = db.due_jobs(con, settings.max_attempts)
    job = next(j for j in jobs if j["delivery_id"] == "d10")

    asyncio.run(worker.process_job(settings, fake, app.state.lexicon, job))

    kinds = {c[0] for c in fake.calls}
    assert "attach" in kinds and "notes" in kinds and "summary" in kinds

    notes = next(c[1] for c in fake.calls if c[0] == "notes")
    assert "原始备注" in notes and "sensitive-lite:data" in notes

    summary = next(c[1] for c in fake.calls if c[0] == "summary")
    assert summary.startswith("会议总结正文")
    assert "敏感词涉及语句清单" in summary and "武器" in summary

    with db.connect(settings.db_path) as con:
        alerts = db.alerts_for(con, 7)
        assert len(alerts) == 1 and alerts[0]["term"] == "武器" and alerts[0]["level"] == "L1"

    # /hits 接口（widget 数据源①）
    r = tc.get("/hits", params={"recording_id": 7})
    hits = r.json()["hits"]
    assert len(hits) == 1 and hits[0]["w"] == "武器" and hits[0]["l"] == "L1"


def test_resolve_notice_lang_chain(tc):
    """owner ui_language → 未知语言回落 NOTIFY_LANG → me() 失败也回落。"""
    import app.worker as w
    from app.config import settings

    class FakeMe:
        def __init__(self, lang):
            self.lang = lang

        async def me(self):
            if self.lang == "__fail__":
                raise RuntimeError("api down")
            return {"preferences": {"ui_language": self.lang}}

    async def run(lang, notify):
        w._ui_lang.update({"value": None, "fetched_at": 0.0})
        old = settings.notify_lang
        settings.notify_lang = notify
        try:
            return await w.resolve_notice_lang(settings, FakeMe(lang))
        finally:
            settings.notify_lang = old
        w._ui_lang["fetched_at"] = 0.0

    assert asyncio.run(run("en", "zh")) == "en"       # ui_language 命中文案
    assert asyncio.run(run("xx", "zh")) == "zh"       # 未知语言回落配置
    assert asyncio.run(run(None, "id")) == "id"       # 无值回落配置
    assert asyncio.run(run("__fail__", "zh")) == "zh"  # API 失败回落配置
    w._ui_lang["fetched_at"] = 0.0


def test_full_pipeline_uses_ui_language(tc):
    """ui_language=en 时,整条管线产出的备注/清单是英文。"""
    body = payload("d30", rid=8)
    assert tc.post("/hooks/speakr", content=body,
                   headers=signed_headers(body, "d30", "recording.transcription.completed")
                   ).status_code == 202

    class EnSpeakr(FakeSpeakr):
        async def me(self):
            return {"preferences": {"ui_language": "en"}}

    fake = EnSpeakr()
    with db.connect(settings.db_path) as con:
        job = next(j for j in db.due_jobs(con, settings.max_attempts) if j["delivery_id"] == "d30")
    asyncio.run(worker.process_job(settings, fake, app.state.lexicon, job))

    summary = next(c[1] for c in fake.calls if c[0] == "summary")
    assert "Sensitive-word sentence list" in summary
    notes = next(c[1] for c in fake.calls if c[0] == "notes")
    assert "Sensitive-word alert" in notes


def test_summary_event_waits_for_scan(tc):
    # summary.completed 先到、检测未跑 → RetryLater 固定间隔重试
    body = json.dumps({"id": "d20", "event": "recording.summary.completed",
                       "data": {"recording_id": 99}}).encode()
    assert tc.post("/hooks/speakr", content=body,
                   headers=signed_headers(body, "d20", "recording.summary.completed")
                   ).status_code == 202
    with db.connect(settings.db_path) as con:
        job = next(j for j in db.due_jobs(con, settings.max_attempts) if j["delivery_id"] == "d20")
    with pytest.raises(worker.RetryLater):
        asyncio.run(worker.process_job(settings, FakeSpeakr(), app.state.lexicon, job))
