"""回写层：标签 / 备注块 / 总结清单块（B8 幂等块替换）。"""
import json
import time

NL = chr(10)

LEVELS = ("L1", "L2", "L3")
TAG_NAMES = {lv: f"敏感词-{lv}" for lv in LEVELS}
TAG_COLORS = {"L1": "#dc2626", "L2": "#ea580c", "L3": "#ca8a04"}

SUMMARY_START = "<!-- sensitive-filter:summary:start -->"
SUMMARY_END = "<!-- sensitive-filter:summary:end -->"
NOTES_START = "<!-- sensitive-lite:start -->"
NOTES_END = "<!-- sensitive-lite:end -->"
DATA_START = "<!-- sensitive-lite:data "
DATA_END = "-->"


def fmt_ts(sec):
    if sec is None:
        return "--:--"
    sec = int(float(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ---------- 标签 ----------

async def ensure_tag(speakr, level: str) -> int:
    name = TAG_NAMES[level]
    tags = await speakr.tags(name)
    if tags:
        return tags[0]["id"]
    made = await speakr.create_tag(name, TAG_COLORS[level])
    return made["id"]


async def write_tags_and_notes(speakr, rid: int, hits):
    """挂最高级别标签 + 写备注块（复用 lite 的备注块格式，widget 兼容）。"""
    max_level = min(h["level"] for h in hits)  # L1 < L2 < L3
    tag_id = await ensure_tag(speakr, max_level)
    detail = await speakr.recording(rid)
    for t in detail.get("tags") or []:
        if t.get("name") in TAG_NAMES.values() and t["id"] != tag_id:
            await speakr.detach_tag(rid, t["id"])
    await speakr.attach_tag(rid, tag_id)
    if max_level == "L1":
        await speakr.patch_recording(rid, is_highlighted=True)

    hits_sorted = sorted(hits, key=lambda h: (h["level"], h.get("start_time") or 0))
    lines = [f"⚠ 敏感词提示 {time.strftime('%Y-%m-%d %H:%M')} — 命中 {len(hits_sorted)} 处（最高 {max_level}）"]
    for h in hits_sorted[:20]:
        lines.append(f'- [{h["level"]}] {fmt_ts(h.get("start_time"))} {h.get("speaker") or "—"}: '
                     f'“{(h.get("text") or "")[:80]}”（词: {h["term"]}）')
    if len(hits_sorted) > 20:
        lines.append(f"…共 {len(hits_sorted)} 处，其余见审计日志")
    data = {"rid": rid, "hits": [
        {"i": h.get("segment_index"), "l": h["level"],
         "w": (h["term"] or "").replace("--", "-"), "v": [h.get("matched")] if h.get("matched") else [],
         "t": round(float(h["start_time"]), 1) if h.get("start_time") is not None else None}
        for h in hits_sorted]}
    block = (NOTES_START + NL + NL.join(lines) + NL
             + DATA_START + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + DATA_END + NL
             + NOTES_END)

    notes = await speakr.notes(rid)
    cleaned = _replace_block(notes, NOTES_START, NOTES_END, "").strip()
    new_notes = (cleaned + NL + NL + block) if cleaned else block
    await speakr.put_notes(rid, new_notes)
    return max_level


# ---------- 总结清单块（B8） ----------

def build_summary_block(hits) -> str:
    """hits 为空 → 返回 ''（调用方删除残留块）。"""
    if not hits:
        return ""
    hits_sorted = sorted(hits, key=lambda h: (h["level"], h.get("start_time") or 0))
    lines = [SUMMARY_START,
             f"## ⚠️ 敏感词涉及语句清单（生成于 {time.strftime('%Y-%m-%d %H:%M')}，共 {len(hits_sorted)} 处）"]
    for h in hits_sorted:
        src = "（LLM 判定）" if h.get("source") == "llm" else ""
        snippet = (h.get("snippet") or h.get("text") or "")[:80]
        lines.append(f'- **[{h["level"]}]** {fmt_ts(h.get("start_time"))} '
                     f'{h.get("speaker") or "—"}:“{snippet}”（命中：{h["term"]}）{src}')
    lines.append(SUMMARY_END)
    return NL.join(lines)


def _replace_block(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    """替换 marker 包裹的块；replacement 为 '' 时删除块及其残留空行。"""
    i = text.find(start_marker)
    if i < 0:
        return text
    j = text.find(end_marker, i)
    if j < 0:
        return text[:i].rstrip()
    j += len(end_marker)
    out = text[:i] + replacement + text[j:]
    if not replacement:
        out = out.rstrip() + (NL if text[:i].strip() else "")
    return out


def upsert_summary(summary: str, block: str) -> str:
    """block='' 表示删除残留块（零命中自愈）。"""
    cleaned = _replace_block(summary, SUMMARY_START, SUMMARY_END, "")
    cleaned = cleaned.rstrip()
    if not block:
        return cleaned
    return (cleaned + NL + NL + block) if cleaned else block
