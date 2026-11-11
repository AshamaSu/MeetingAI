#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
speakr 敏感词提示 — Demo 极简版（lite）
轮询 speakr API v1，扫描已完成录音的转写文本，命中后：
  1. 给录音打「敏感词-L1/L2/L3」标签（跨用户可见）
  2. 在备注中写入固定标记块（含命中词/说话人/时间点/原句）
  3. L1 额外置顶高亮（is_highlighted）
  4. 追加 hits.jsonl 审计日志
零第三方依赖（仅标准库）。词表 words.txt 热加载（按 mtime）。

用法:
  python3 lite.py --once                # 跑一轮增量
  python3 lite.py --id 10 --once        # 只扫指定录音
  python3 lite.py --backfill --once     # 忽略 state 全量重扫
  python3 lite.py --dry-run --once      # 只检测不回写
  python3 lite.py                       # 常驻轮询（默认 60s）
配置:
  SPEAKR_URL / SPEAKR_TOKEN 环境变量，或本目录 .env
"""
import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
LEVELS = ("L1", "L2", "L3")  # L1 最严重
TAG_NAMES = {lv: f"敏感词-{lv}" for lv in LEVELS}
TAG_COLORS = {"L1": "#dc2626", "L2": "#ea580c", "L3": "#ca8a04"}
MARK_START = "<!-- sensitive-lite:start -->"
MARK_END = "<!-- sensitive-lite:end -->"
RE_BLOCK = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END), re.S)


# ---------- 基础 ----------
def load_env():
    env = BASE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


class Api:
    def __init__(self, base_url, token):
        self.base = base_url.rstrip("/")
        self.token = token

    def req(self, method, path, body=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        r = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}") from None
        return json.loads(raw) if raw else {}


def log(msg):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


# ---------- 词表 ----------
def load_words(path: Path):
    """支持 '词,级别' / '级别,词' / '词|级别'，# 注释。返回 [(level, term)]"""
    words = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        parts = [p.strip() for p in re.split(r"[,|]", s) if p.strip()]
        if len(parts) < 2:
            log(f"词表忽略无效行: {s!r}")
            continue
        a, b = parts[0], parts[1]
        if a.upper() in LEVELS:
            level, term = a.upper(), b
        elif b.upper() in LEVELS:
            term, level = a, b.upper()
        else:
            log(f"词表行缺少级别(L1/L2/L3)，忽略: {s!r}")
            continue
        words.append((level, term.lower()))
    return words


# ---------- 文本归一化与匹配 ----------
def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t, flags=re.UNICODE)  # 标点→空格
    return re.sub(r"\s+", " ", t).strip()


def match(term: str, norm: str) -> bool:
    if all(ord(c) < 128 for c in term):  # 拉丁文字：词边界匹配
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", norm) is not None
    return term in norm  # 中文等：子串


def fmt_ts(sec):
    if sec is None:
        return "--:--"
    sec = int(float(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ---------- 扫描 ----------
def scan_recording(api: Api, rec: dict, words):
    rid = rec["id"]
    tr = api.req("GET", f"/api/v1/recordings/{rid}/transcript?format=json")
    segs = tr.get("segments") or []
    if tr.get("kind") == "plain" or not segs:
        segs = [{"index": 0, "speaker": None, "start_time": 0,
                 "end_time": tr.get("duration"), "text": tr.get("raw") or ""}]
    hits, seen = [], set()
    for s in segs:
        norm = normalize(s.get("text", ""))
        if not norm:
            continue
        for level, term in words:
            if match(term, norm) and (s.get("index"), term) not in seen:
                seen.add((s.get("index"), term))
                hits.append({"recording_id": rid, "title": rec.get("title"),
                             "segment_index": s.get("index"),
                             "speaker": s.get("speaker") or s.get("speaker_label"),
                             "start_time": s.get("start_time"), "end_time": s.get("end_time"),
                             "term": term, "level": level, "text": (s.get("text") or "").strip()})
    return hits


# ---------- 回写 ----------
def ensure_tag(api: Api, level: str) -> int:
    name = TAG_NAMES[level]
    found = api.req("GET", "/api/v1/tags?" + urllib.parse.urlencode({"name": name}))
    tags = found.get("tags") or []
    if tags:
        return tags[0]["id"]
    made = api.req("POST", "/api/v1/tags", {"name": name, "color": TAG_COLORS[level]})
    return made["id"]


def current_tags(api: Api, rid: int):
    detail = api.req("GET", f"/api/v1/recordings/{rid}")
    tags = detail.get("tags")
    if tags is None:  # 详情无 tags 时退回列表接口
        lst = api.req("GET", "/api/v1/recordings?per_page=100&status=completed")
        tags = next((r.get("tags") for r in lst.get("recordings", []) if r["id"] == rid), [])
    return tags or []


def write_back(api: Api, rec: dict, hits):
    rid = rec["id"]
    max_level = min(h["level"] for h in hits)  # L1 < L2 < L3，min 即最高级别
    used = {h["level"] for h in hits}

    tag_id = ensure_tag(api, max_level)
    for t in current_tags(api, rid):
        if t.get("name") in TAG_NAMES.values() and t["id"] != tag_id:
            api.req("DELETE", f"/api/v1/recordings/{rid}/tags/{t['id']}")
    api.req("POST", f"/api/v1/recordings/{rid}/tags", {"tag_ids": [tag_id]})
    if max_level == "L1":
        api.req("PATCH", f"/api/v1/recordings/{rid}", {"is_highlighted": True})

    hits_sorted = sorted(hits, key=lambda h: (h["level"], h.get("start_time") or 0))
    lines = [f"⚠ 敏感词提示 {time.strftime('%Y-%m-%d %H:%M')} — 命中 {len(hits)} 处（最高 {max_level}）"]
    for h in hits_sorted[:20]:
        lines.append(f'- [{h["level"]}] {fmt_ts(h.get("start_time"))} {h.get("speaker") or "—"}: '
                     f'“{h["text"][:80]}”（词: {h["term"]}）')
    if len(hits_sorted) > 20:
        lines.append(f"…共 {len(hits_sorted)} 处，其余见 hits.jsonl")
    # 机器可读命中数据（供前端 widget 高亮气泡用；'--' 会破坏 HTML 注释，需规避）
    data = {"rid": rid, "hits": [
        {"i": h["segment_index"], "l": h["level"], "w": (h["term"] or "").replace("--", "-"),
         "t": round(float(h["start_time"]), 1) if h.get("start_time") is not None else None}
        for h in hits_sorted]}
    block = (MARK_START + "\n" + "\n".join(lines) + "\n"
             + "<!-- sensitive-lite:data " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + " -->\n"
             + MARK_END)

    notes = api.req("GET", f"/api/v1/recordings/{rid}/notes").get("notes") or ""
    cleaned = RE_BLOCK.sub("", notes).strip()
    new_notes = (cleaned + "\n\n" + block) if cleaned else block
    api.req("PUT", f"/api/v1/recordings/{rid}/notes", {"notes": new_notes})
    return max_level


# ---------- 主流程 ----------
def fetch_completed(api: Api, only_id=None):
    out, page = [], 1
    while True:
        q = urllib.parse.urlencode({"status": "completed", "sort_by": "updated_at",
                                    "sort_order": "desc", "per_page": 100, "page": page})
        d = api.req("GET", "/api/v1/recordings?" + q)
        recs = d.get("recordings", [])
        out.extend(recs)
        if not d.get("pagination", {}).get("has_next"):
            break
        page += 1
    if only_id:
        out = [r for r in out if r["id"] == only_id]
    return out


def process(api: Api, rec: dict, words, state, data_dir: Path, dry=False, backfill=False):
    rid = str(rec["id"])
    prev = state.get(rid)
    if not backfill and prev and prev.get("updated_at") == rec.get("updated_at"):
        return None
    hits = scan_recording(api, rec, words)
    entry = {"recording_id": rec["id"], "updated_at": rec.get("updated_at"),
             "hits": len(hits),
             "max_level": min((h["level"] for h in hits), default=None)}
    if hits:
        by_level = {lv: sum(1 for h in hits if h["level"] == lv) for lv in LEVELS}
        log(f'录音 {rid}《{rec.get("title")}》命中 {len(hits)} 处 '
            + " ".join(f"{k}×{v}" for k, v in by_level.items() if v))
        if dry:
            for h in hits[:5]:
                log(f'  [{h["level"]}] {fmt_ts(h.get("start_time"))} {h.get("speaker") or "—"}: '
                    f'{h["text"][:60]}（词: {h["term"]}）')
            entry["dry_run"] = True
        else:
            entry["max_level"] = write_back(api, rec, hits)
            log(f'  已回写: 标签「敏感词-{entry["max_level"]}」+ 备注 + '
                + ("置顶高亮" if entry["max_level"] == "L1" else "无高亮"))
    else:
        log(f'录音 {rid}《{rec.get("title")}》无命中')
    if not dry:
        with (data_dir / "hits.jsonl").open("a", encoding="utf-8") as f:
            for h in hits:
                f.write(json.dumps(h, ensure_ascii=False) + "\n")
        state[rid] = entry
        (data_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return entry


def main():
    ap = argparse.ArgumentParser(description="speakr 敏感词提示 lite demo")
    ap.add_argument("--once", action="store_true", help="跑一轮后退出")
    ap.add_argument("--backfill", action="store_true", help="忽略已扫记录，全量重扫")
    ap.add_argument("--id", type=int, help="只处理指定录音")
    ap.add_argument("--interval", type=int, default=60, help="轮询间隔秒（默认60）")
    ap.add_argument("--dry-run", action="store_true", help="只检测不回写")
    args = ap.parse_args()

    load_env()
    url = os.environ.get("SPEAKR_URL", "http://localhost:8899")
    token = os.environ.get("SPEAKR_TOKEN", "")
    if not token:
        sys.exit("缺少 SPEAKR_TOKEN（环境变量或 .env）")
    api = Api(url, token)

    words_path = BASE / "words.txt"
    words, words_mtime = load_words(words_path), None
    data_dir = BASE / "data"
    data_dir.mkdir(exist_ok=True)
    state_p = data_dir / "state.json"
    state = json.loads(state_p.read_text(encoding="utf-8")) if state_p.exists() else {}

    log(f"speakr={url}  词表 {len(words)} 条  模式={'dry-run' if args.dry_run else '回写'}"
        f"  范围={'id=' + str(args.id) if args.id else ('全量backfill' if args.backfill else '增量')}")
    while True:
        if words_path.exists() and words_path.stat().st_mtime != words_mtime:
            words, words_mtime = load_words(words_path), words_path.stat().st_mtime
            log(f"词表热加载: {len(words)} 条")
        try:
            recs = fetch_completed(api, args.id)
            log(f"待查已完成录音 {len(recs)} 条")
            for rec in recs:
                process(api, rec, words, state, data_dir, args.dry_run, args.backfill)
        except Exception as e:  # 单轮失败不退出，下轮重试
            log(f"本轮出错（下轮重试）: {e}")
        if args.backfill:
            args.backfill = False  # backfill 只作用一轮，之后恢复增量
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
