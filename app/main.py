"""sensitive-filter 服务入口（FastAPI）。

路由：
  POST /hooks/speakr    Webhook 接收（验签→幂等→落 delivery/job→202）
  GET  /hits            命中查询（widget 数据源①）
  GET  /widget.js       气泡高亮脚本（loading_overlay.html 引用）
  POST /scan/{rid}      手动触发重扫（本机或 Basic Auth）
  GET  /alerts          告警看板（Basic Auth，可关）
  GET  /healthz         健康检查
"""
import asyncio
import json
import os
import re
import secrets as pysecrets
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import db, verify, worker
from .config import settings
from .lexicon import LEVELS, Lexicon, parse_wordlist
from .speakr import SpeakrClient

app = FastAPI(title="speakr-sensitive-filter", version="0.1.0")
STATIC_DIR = __file__.rsplit("/", 1)[0] + "/../static"


@app.on_event("startup")
async def startup():
    db.init_db(settings.db_path)
    app.state.speakr = SpeakrClient(settings.speakr_url, settings.speakr_token)
    app.state.lexicon = Lexicon(settings.wordlist_path)
    app.state.worker_task = None
    if settings.worker_enabled:
        app.state.worker_task = asyncio.create_task(
            worker.worker_loop(settings, app.state.speakr, app.state.lexicon))


@app.on_event("shutdown")
async def shutdown():
    t = getattr(app.state, "worker_task", None)
    if t:
        t.cancel()


# ---------- webhook ----------

@app.post("/hooks/speakr")
async def hooks(request: Request):
    body = await request.body()
    ok, reason = verify.verify(body, request.headers, settings.webhook_secrets,
                               settings.signature_tolerance)
    if not ok:
        raise HTTPException(status_code=401, detail=f"signature rejected: {reason}")
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")
    event = payload.get("event") or request.headers.get("Speakr-Event") or ""
    delivery_id = request.headers.get("Speakr-Delivery-Id") or payload.get("id")
    if not delivery_id:
        raise HTTPException(status_code=400, detail="missing delivery id")
    rid = (payload.get("data") or {}).get("recording_id")
    with db.connect(settings.db_path) as con:
        if db.delivery_seen(con, delivery_id):
            return {"status": "duplicate"}  # 幂等：已收过
        db.save_delivery(con, str(delivery_id), event, body.decode("utf-8", "replace")[:10000])
        if not rid:
            # 如 webhook.test 连通性/验签测试事件：验签已过，回 202 但不入检测队列
            return JSONResponse({"status": "ignored", "event": event}, status_code=202)
        db.enqueue_job(con, str(delivery_id), int(rid), event)
    return JSONResponse({"status": "accepted"}, status_code=202)


# ---------- widget 数据源 ----------

@app.get("/hits")
async def hits(recording_id: int):
    with db.connect(settings.db_path) as con:
        rows = db.alerts_for(con, recording_id)
    return {"rid": recording_id, "hits": [
        {"i": r["segment_index"], "l": r["level"], "w": r["term"],
         "t": r["start_time"], "speaker": r["speaker"],
         "snippet": r["snippet"], "source": r["source"]} for r in rows]}


@app.get("/widget.js")
async def widget_js():
    return FileResponse(STATIC_DIR + "/widget.js", media_type="application/javascript")


# ---------- 运维 ----------

def _check_admin(request: Request):
    # 信任来源:回环 + 内网网段(端口仅绑 127.0.0.1:8898,宿主机经 docker-proxy
    # 转发后来源显示为网桥网关 172.x.0.1,故需放行私网段);或 Basic Auth。
    host = (request.client.host or "") if request.client else ""
    try:
        trusted = host in ("127.0.0.1", "::1", "testclient") or \
            __import__("ipaddress").ip_address(host).is_private
    except ValueError:
        trusted = False
    if trusted:
        return
    if settings.alert_user and settings.alert_pass:
        auth = request.headers.get("Authorization", "")
        import base64
        if auth.startswith("Basic "):
            try:
                user, _, pwd = base64.b64decode(auth[6:]).decode().partition(":")
                if pysecrets.compare_digest(user, settings.alert_user) and                         pysecrets.compare_digest(pwd, settings.alert_pass):
                    return
            except Exception:
                pass
    raise HTTPException(status_code=403, detail="admin only")


@app.post("/scan/{rid}")
async def rescan(rid: int, request: Request):
    """手动重扫（等价 lite --id/--backfill 的单条版）。"""
    _check_admin(request)
    delivery_id = f"manual-{rid}-{int(time.time())}"
    with db.connect(settings.db_path) as con:
        db.enqueue_job(con, delivery_id, rid, "recording.transcription.completed")
    return JSONResponse({"status": "queued", "delivery_id": delivery_id}, status_code=202)


# ---------- 词表远程管理 ----------

WORDLIST_MAX_BYTES = 200_000


@app.get("/wordlist")
async def get_wordlist(request: Request):
    _check_admin(request)
    path = settings.wordlist_path
    content = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    words = parse_wordlist(content)
    by_level = {lv: 0 for lv in LEVELS}
    for t in words:
        if t.enabled:
            by_level[t.level] += 1
    return {"terms": len(words), "enabled_by_level": by_level, "path": str(path), "content": content}


@app.put("/wordlist")
async def put_wordlist(request: Request):
    """推送新词表全文（text/plain 或 JSON {"content": ...}）。
    校验通过后原子替换；词表 mtime 热加载，无需重启。"""
    _check_admin(request)
    raw = await request.body()
    if raw[:1] == b"{" and b"content" in raw[:64]:
        try:
            body = json.loads(raw).get("content") or ""
        except Exception:
            raise HTTPException(status_code=400, detail="invalid json body")
    else:
        body = raw.decode("utf-8-sig", "replace")
    if not body.strip():
        raise HTTPException(status_code=422, detail="empty wordlist (用 enabled=0 停用检测,别清空)")
    if len(body.encode()) > WORDLIST_MAX_BYTES:
        raise HTTPException(status_code=413, detail="wordlist too large")
    words = parse_wordlist(body)
    if not words:
        raise HTTPException(status_code=422, detail="no valid terms parsed (格式: term,lang,level,... 或 L1,词)")
    for t in words:
        if t.regex:
            try:
                re.compile(t.regex)
            except re.error as e:
                raise HTTPException(status_code=422, detail=f"invalid regex for term '{t.term}': {e}")
    # 原子替换 + 留一份 .bak
    path = settings.wordlist_path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body if body.endswith(chr(10)) else body + chr(10), encoding="utf-8")
    if path.exists():
        os.replace(path, path.with_suffix(path.suffix + ".bak"))
    os.replace(tmp, path)
    app.state.lexicon._mtime = None  # 强制立即重载
    by_level = {lv: sum(1 for t in words if t.enabled and t.level == lv) for lv in LEVELS}
    return {"status": "saved", "terms": len(words), "enabled_by_level": by_level}


@app.post("/rescan-all")
async def rescan_all(request: Request):
    """词表变更后全量重扫已扫过的录音（每条一个 job，队列逐步消化）。"""
    _check_admin(request)
    ts = int(time.time())
    with db.connect(settings.db_path) as con:
        ids = db.scanned_recording_ids(con)
        for rid in ids:
            db.enqueue_job(con, f"manual-all-{ts}-{rid}", rid, "recording.transcription.completed")
    return JSONResponse({"status": "queued", "recordings": len(ids)}, status_code=202)


@app.get("/alerts", response_class=HTMLResponse)
async def alerts(request: Request):
    _check_admin(request)
    with db.connect(settings.db_path) as con:
        rows = db.recent_alerts(con)
    trs = "".join(
        f"<tr><td>{r['created_at']}</td><td>{r['recording_id']}</td><td>{r['level']}</td>"
        f"<td>{r['term']}</td><td>{r['speaker'] or ''}</td>"
        f"<td>{r['start_time'] or ''}</td><td>{(r['snippet'] or '')[:120]}</td>"
        f"<td>{r['source']}</td></tr>"
        for r in rows)
    return ("<html><head><meta charset='utf-8'><title>敏感词告警</title>"
            "<style>body{font-family:system-ui}table{border-collapse:collapse;width:100%}"
            "td,th{border:1px solid #ddd;padding:4px 8px;font-size:13px}"
            "tr:hover{background:#fafafa}</style></head><body>"
            f"<h3>敏感词告警（最近 {len(rows)} 条）</h3>"
            "<table><tr><th>时间</th><th>录音</th><th>级别</th><th>词</th>"
            "<th>说话人</th><th>时间点</th><th>原句</th><th>来源</th></tr>"
            + trs + "</table></body></html>")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "db": str(settings.db_path),
            "words": len(app.state.lexicon.words()),
            "llm": settings.llm_enabled, "worker": settings.worker_enabled}
