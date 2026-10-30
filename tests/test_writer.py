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
