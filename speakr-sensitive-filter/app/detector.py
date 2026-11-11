"""检测管线：拉转写 → 词表粗筛 → (可选) LLM 精判 → hits。"""
from .lexicon import normalize
from . import llm


def _segments_from_transcript(tr: dict) -> list:
    segs = tr.get("segments") or []
    if tr.get("kind") == "plain" or not segs:
        segs = [{"index": 0, "speaker": None, "start_time": 0,
                 "end_time": tr.get("duration"), "text": tr.get("raw") or ""}]
    return segs


def coarse_scan(segments, lexicon) -> list:
    """词表粗筛。返回 lexicon 级 hits（含每句命中形式）。"""
    hits = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        norm = normalize(text)
        for t, form in lexicon.scan_segment(text):
            hits.append({"segment_index": s.get("index"), "speaker": s.get("speaker") or s.get("speaker_label"),
                         "start_time": s.get("start_time"), "end_time": s.get("end_time"),
                         "term": t.term, "level": t.level, "category": t.category,
                         "text": text, "matched": form, "source": "lexicon", "confidence": 1.0})
    return hits


def dedupe(hits):
    seen, out = set(), []
    for h in hits:
        k = (h.get("segment_index"), h["term"], h["level"])
        if k in seen:
            continue
        seen.add(k)
        out.append(h)
    return out


async def detect(settings, speakr, lexicon, rid: int) -> list:
    """完整检测。返回 hits（LLM 结果置信度<0.6 已在 llm 层过滤；
    LLM 失败 → 保留词表命中，source=lexicon，检测永不因 LLM 挂而中断）。"""
    tr = await speakr.transcript(rid)
    segments = _segments_from_transcript(tr)
    hits = dedupe(coarse_scan(segments, lexicon))

    if settings.llm_enabled and hits:
        by_idx = {s.get("index"): s for s in segments}
        order = [s.get("index") for s in segments]
        candidates = []
        for h in hits:
            i = h.get("segment_index")
            pos = order.index(i) if i in order else -1
            candidates.append({
                "segment_index": i,
                "sentence": h["text"],
                "prev": (by_idx[order[pos - 1]].get("text") if pos > 0 else ""),
                "next": (by_idx[order[pos + 1]].get("text") if 0 <= pos < len(order) - 1 else ""),
            })
        llm_hits = await llm.refine(settings, candidates)
        for lh in llm_hits:
            i = lh.get("segment_index")
            base = next((h for h in hits if h.get("segment_index") == i), hits[0] if hits else None)
            hits.append({"segment_index": i,
                         "speaker": (base or {}).get("speaker"),
                         "start_time": (base or {}).get("start_time"),
                         "end_time": (base or {}).get("end_time"),
                         "term": str(lh["term"]).lower(), "level": lh["level"],
                         "category": lh.get("category", ""),
                         "text": (base or {}).get("text", ""), "matched": str(lh["term"]).lower(),
                         "source": "llm", "confidence": float(lh.get("confidence", 0.6)),
                         "reason": lh.get("reason", "")})
        hits = dedupe(hits)
    return hits
