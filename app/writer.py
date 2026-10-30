"""回写层：标签 / 备注块 / 总结清单块（B8 幂等块替换）。
文案语言由调用方传入（owner 的 ui_language，回退 NOTIFY_LANG），见 app/i18n.py。
"""
import json
import time

from . import i18n

NL = chr(10)

LEVELS = ("L1", "L2", "L3")
TAG_NAMES = {"L1": "SW-L1", "L2": "SW-L2", "L3": "SW-L3"}  # 列表页展示用简短英文
# lite 时代遗留的中文标签名：回写时一并摘除，避免双标签并存
LEGACY_TAG_NAMES = {"敏感词-L1", "敏感词-L2", "敏感词-L3"}
TAG_COLORS = {"L1": "#dc2626", "L2": "#ea580c", "L3": "#ca8a04"}

SUMMARY_START = "<!-- sensitive-filter:summary:start -->"
SUMMARY_END = "<!-- sensitive-filter:summary:end -->"
NOTES_START = "<!-- sensitive-lite:start -->"
NOTES_END = "<!-- sensitive-lite:end -->"
DATA_START = "<!-- sensitive-lite:data "
DATA_END = "-->"


def _paren(s: str, lang: str) -> str:
    """注释性文字的括号样式：中文全角，其他半角。"""
    return ("（" + s + "）") if lang == "zh" else (" (" + s + ")")


def fmt_ts(sec):
    if sec is None:
        return "--:--"
    sec = int(float(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# ---------- 标签 ----------

async def _attach_with_fallback(speakr, rid: int, level: str) -> int:
    """挂标签（多账号安全）。

    坑：speakr 的 GET /tags?name= 会跨用户匹配同名 tag，但 attach 别人的 tag
    时返回 HTTP 200 + {"success":true,"errors":["No access to tag N"],"added_tags":[]}
    —— 静默失败。因此 attach 后必须校验 added_tags，被拒则创建自己的同名 tag 重挂。
    """
    tag_id = None
    resp = None
    tok = speakr.token_for(rid)  # 检测该录音时确定可用的账号,tag 操作必须同一账号视角
    tags = await speakr.tags(TAG_NAMES[level], token=tok)
    if tags:
        tag_id = tags[0]["id"]
        resp = await speakr.attach_tag(rid, tag_id)
        if resp.get("success") and resp.get("added_tags"):
            return tag_id
    try:
        made = await speakr.create_tag(TAG_NAMES[level], TAG_COLORS[level], token=tok)
        tag_id = made.get("id")
    except RuntimeError as e:
        if "already exists" not in str(e):
            raise
        # 同账号下已存在(可能被并发创建)→ 用同一视角重新取
        tags = await speakr.tags(TAG_NAMES[level], token=tok)
        tag_id = tags[0]["id"] if tags else None
    if not tag_id:
        raise RuntimeError(f"cannot resolve tag {TAG_NAMES[level]}: {resp}")
    resp = await speakr.attach_tag(rid, tag_id)
    if not (resp.get("success") and resp.get("added_tags")):
        raise RuntimeError(f"tag attach rejected: {resp}")
    return tag_id


async def write_tags_and_notes(speakr, rid: int, hits, lang: str = "zh"):
    """挂最高级别标签 + 写备注块（复用 lite 的备注块格式，widget 兼容）。"""
    max_level = min(h["level"] for h in hits)  # L1 < L2 < L3
    tag_id = await _attach_with_fallback(speakr, rid, max_level)
    detail = await speakr.recording(rid)
    for t in detail.get("tags") or []:
        if (t.get("name") in TAG_NAMES.values() or t.get("name") in LEGACY_TAG_NAMES) \
                and t["id"] != tag_id:
            try:
                await speakr.detach_tag(rid, t["id"])
            except Exception:
                pass  # 别人的 tag 无权摘除，忽略（标签本身已替换）
    if max_level == "L1":
        try:
            await speakr.patch_recording(rid, is_highlighted=True)
        except Exception:
            pass  # 尽力而为

    hits_sorted = sorted(hits, key=lambda h: (h["level"], h.get("start_time") or 0))
    lines = [i18n.t("alert_title", lang).format(
        ts=time.strftime('%Y-%m-%d %H:%M'), n=len(hits_sorted), level=max_level)]
    for h in hits_sorted[:20]:
        lines.append(f'- [{h["level"]}] {fmt_ts(h.get("start_time"))} {h.get("speaker") or "—"}: '
                     f'“{(h.get("text") or "")[:80]}”'
                     + _paren(i18n.t("hit_label", lang).format(term=h["term"]), lang))
    if len(hits_sorted) > 20:
        lines.append(i18n.t("overflow", lang).format(n=len(hits_sorted)))
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

def build_summary_block(hits, lang: str = "zh") -> str:
    """hits 为空 → 返回 ''（调用方删除残留块）。"""
    if not hits:
        return ""
    hits_sorted = sorted(hits, key=lambda h: (h["level"], h.get("start_time") or 0))
    lines = [SUMMARY_START,
             i18n.t("summary_heading", lang).format(
                 ts=time.strftime('%Y-%m-%d %H:%M'), n=len(hits_sorted))]
    for h in hits_sorted:
        src = _paren(i18n.t("llm_verdict", lang), lang) if h.get("source") == "llm" else ""
        snippet = (h.get("snippet") or h.get("text") or "")[:80]
        lines.append(f'- **[{h["level"]}]** {fmt_ts(h.get("start_time"))} '
                     f'{h.get("speaker") or "—"}:“{snippet}”'
                     + _paren(i18n.t("hit_label", lang).format(term=h["term"]), lang) + src)
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
