"""LLM 精判（OpenAI 兼容 /chat/completions）。只送候选句±1 句；严格 JSON 输出。
失败/非法 JSON 一律降级为纯词表结果（source=lexicon）。
"""
import json

NL = chr(10)

SYSTEM_PROMPT = (
    "你是会议转写敏感词审查助手。对给定的候选句，判断其中的敏感词命中是否真实"
    "（排除白名单误报、委婉语误判、转写错字），并补充词表遗漏的同类敏感词。"
    '只输出 JSON，格式：'
    '{"hits":[{"segment_index":0,"term":"...","level":"L1|L2|L3",'
    '"category":"...","confidence":0.0-1.0,"reason":"简短理由"}]}'
    '。没有命中输出 {"hits":[]}。不要输出任何解释性文字。'
)


async def refine(settings, candidates) -> list:
    """candidates: [{segment_index, sentence, prev, next}]。返回 LLM 判定的 hits 列表，
    出错返回 []（调用方降级）。"""
    if not settings.llm_enabled or not candidates:
        return []
    lines = []
    for c in candidates:
        ctx = ""
        if c.get("prev"):
            ctx += f"[前句] {c['prev']}" + NL
        ctx += f"[候选] ({c['segment_index']}) {c['sentence']}"
        if c.get("next"):
            ctx += NL + f"[后句] {c['next']}"
        lines.append(ctx)
    user_msg = "级别定义：L1 违禁，L2 警示，L3 关注。" + NL + NL + NL.join(lines)

    try:
        import httpx
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            r = await client.post(
                settings.llm_base_url + "/chat/completions",
                headers={"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {},
                json={"model": settings.llm_model, "temperature": 0,
                      "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                   {"role": "user", "content": user_msg}]})
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
    except Exception:
        return []

    try:
        # 容忍模型输出裹 markdown 代码围栏
        content = content.strip()
        fence = chr(96) * 3
        if content.startswith(fence):
            content = content.strip(fence).lstrip("json").strip()
        data = json.loads(content)
        hits = data.get("hits", [])
        return [h for h in hits if isinstance(h, dict) and h.get("term")
                and h.get("level") in ("L1", "L2", "L3")
                and float(h.get("confidence", 0)) >= 0.6]
    except Exception:
        return []
