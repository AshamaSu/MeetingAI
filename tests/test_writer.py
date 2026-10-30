from app.writer import (SUMMARY_END, SUMMARY_START, build_summary_block,
                        upsert_summary)

HIT = {"level": "L1", "start_time": 201.0, "speaker": "张三",
       "text": "这段讨论武器采购", "term": "武器", "source": "lexicon"}


def test_build_block_empty():
    assert build_summary_block([]) == ""


def test_build_block_content():
    block = build_summary_block([HIT])
    assert block.startswith(SUMMARY_START)
    assert block.endswith(SUMMARY_END)
    assert "[L1]" in block and "武器" in block and "张三" in block


def test_upsert_append():
    block = build_summary_block([HIT])
    out = upsert_summary("会议总结正文", block)
    assert out.startswith("会议总结正文")
    assert out.endswith(block)


def test_upsert_replace():
    block = build_summary_block([HIT])
    once = upsert_summary("正文", block)
    twice = upsert_summary(once, block)  # 幂等：不重复堆积
    assert twice.count(SUMMARY_START) == 1
    assert twice == once


def test_upsert_remove_when_no_hits():
    block = build_summary_block([HIT])
    with_block = upsert_summary("正文", block)
    cleaned = upsert_summary(with_block, "")  # 零命中自愈：删残留块
    assert SUMMARY_START not in cleaned
    assert cleaned.strip() == "正文"


def test_summary_block_lang_en():
    block = build_summary_block([HIT], lang="en")
    assert "Sensitive-word sentence list" in block
    assert " (hit: 武器)" in block
    assert block.startswith(SUMMARY_START) and block.endswith(SUMMARY_END)


def test_summary_block_lang_id():
    block = build_summary_block([HIT], lang="id")
    assert "Daftar kalimat" in block and "temuan: 武器" in block


def test_summary_block_paren_style():
    assert "（命中：武器）" in build_summary_block([HIT], lang="zh")
    assert " (hit: 武器)" in build_summary_block([HIT], lang="en")


def test_tag_fallback_on_silent_rejection():
    """attach 别人的 tag 返回 200 但 added_tags=[] → 自动创建自己的 tag 重挂。"""
    import asyncio
    from app import writer

    class FlakySpeakr:
        def __init__(self):
            self.calls = []

        def token_for(self, rid):
            return "tok-guest"

        async def tags(self, name, token=None):
            return [{"id": 2, "name": name}]  # 同名 tag 是别人的

        async def attach_tag(self, rid, tag_id):
            self.calls.append(("attach", tag_id))
            if tag_id == 2:
                return {"success": True, "added_tags": [],
                        "errors": ["No access to tag 2"]}
            return {"success": True, "added_tags": [{"id": tag_id}]}

        async def create_tag(self, name, color, token=None):
            self.calls.append(("create", name, token))
            return {"id": 7}

        async def recording(self, rid):
            return {"tags": [{"id": 2, "name": "SW-L3"}]}

        async def detach_tag(self, rid, tag_id):
            self.calls.append(("detach", tag_id))

        async def patch_recording(self, rid, **kw):
            self.calls.append(("patch", rid))

        async def notes(self, rid):
            return ""

        async def put_notes(self, rid, notes):
            self.calls.append(("notes", notes))

    sp = FlakySpeakr()
    hits = [{"level": "L3", "start_time": 60.0, "speaker": "S",
             "text": "seminar text", "term": "seminar"}]
    max_level = asyncio.run(writer.write_tags_and_notes(sp, 13, hits, "en"))
    assert max_level == "L3"
    creates = [c for c in sp.calls if c[0] == "create"]
    assert creates and creates[0][2] == "tok-guest"  # create 用录音 token 的视角
    assert ("attach", 7) in sp.calls          # 用新 tag 重挂
    assert "notes" in [c[0] for c in sp.calls]  # 备注照常写入
