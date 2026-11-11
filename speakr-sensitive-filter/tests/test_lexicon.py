from pathlib import Path

from app.lexicon import Lexicon, match_form, normalize

WORDLIST = Path(__file__).resolve().parent.parent / "wordlists" / "sensitive.csv"


def test_normalize():
    assert normalize("HellO,  World!!") == "hello world"
    assert normalize("ＦＵＬＬ-ＷＩＤＴＨ") == "full width"  # NFKC


def test_word_boundary():
    assert not match_form("ass", normalize("this is a class"))  # 防 class→ass
    assert match_form("ass", normalize("that was an ass"))


def test_cjk_substring():
    assert match_form("武器", normalize("讨论武器采购"))
    assert not match_form("武器", normalize("讨论瓷器采购"))


def test_lexicon_load_and_scan():
    lex = Lexicon(WORDLIST)
    hits = lex.scan_segment("这里涉及武器与回扣的问题")
    levels = {t.term: t.level for t, _ in hits}
    assert levels.get("武器") == "L1"
    assert levels.get("回扣") == "L2"


def test_variants():
    lex = Lexicon(WORDLIST)
    hits = lex.scan_segment("dia menerima suap dari vendor")
    assert any(t.term == "suap" for t, _ in hits)
    hits2 = lex.scan_segment("pasien itu mengkomplain pelayanan")
    assert any(t.term == "komplain" for t, _ in hits2)


def test_wordlist_hot_reload(tmp_path):
    p = tmp_path / "w.csv"
    p.write_text("term,lang,level,category,variants,regex,enabled" + chr(10) + "测试词,zh,L2,x,,,1", encoding="utf-8")
    lex = Lexicon(p)
    assert not lex.scan_segment("不含该词的无关句子")
    import os, time as _t
    _t.sleep(0.02)
    p.write_text("term,lang,level,category,variants,regex,enabled" + chr(10) + "测试词,zh,L2,x,,,1" + chr(10) + "新词,zh,L1,y,,,1", encoding="utf-8")
    os.utime(p, (p.stat().st_atime + 2, p.stat().st_mtime + 2))
    assert any(t.term == "新词" for t, _ in lex.scan_segment("包含新词的句子"))



def test_id_profanity_rows_loaded():
    lex = Lexicon(WORDLIST)
    id_terms = [t for t in lex.words() if t.lang == "id"]
    assert len(id_terms) == 38
    by_term = {t.term: t for t in id_terms}
    assert by_term["goblok"].level == "L3"
    assert by_term["goblok"].category == "insult"
    assert by_term["kontol"].level == "L2"
    assert by_term["kontol"].category == "sexual-vulgarity"
    assert "kntl" in by_term["kontol"].variants
    # variants 按空格切分，必须是单 token，否则会被拆成多个误匹配词
    assert all(" " not in f for t in id_terms for f in t.variants)


def test_id_profanity_hits():
    lex = Lexicon(WORDLIST)
    cases = {
        "dasar goblok banget": ("goblok", "L3"),
        "ini esek-esek": ("esek esek", "L3"),
        "kntl lu": ("kontol", "L2"),
        "dia tolol": ("tolol", "L3"),
    }
    for text, (term, level) in cases.items():
        hits = {t.term: t for t, _ in lex.scan_segment(text)}
        assert term in hits, text
        assert hits[term].level == level, text


def test_id_profanity_false_positive_probes():
    lex = Lexicon(WORDLIST)
    for text in ("Baju Bodo", "E. coli", "ikan bakar", "anjing peliharaan", "babi", "jancuk", "cuki mai"):
        assert lex.scan_segment(text) == [], text


def test_ldnoobw_rows_loaded():
    lex = Lexicon(WORDLIST)
    words = lex.words()
    assert sum(1 for t in words if t.lang == "en") == 404
    assert sum(1 for t in words if t.lang == "zh") == 298
    # 归一化后为空的词形会匹配任意文本，必须在导入时剔除
    assert all(len(f) >= 2 for t in words for f in t.all_forms)


def test_ldnoobw_benign_probes():
    lex = Lexicon(WORDLIST)
    probes = (
        "今天下午三点开会，讨论季度预算和人员安排。",
        "这条录音的转写质量还需要人工校对。",
        "please review the quarterly assessment report before the meeting.",
        "we need to discuss the marketing plan for next quarter.",
    )
    for text in probes:
        assert lex.scan_segment(text) == [], text


def test_ldnoobw_sample_self_match():
    lex = Lexicon(WORDLIST)
    sample = [t for t in lex.words() if t.category == "profanity"][::40]
    assert sample
    for t in sample:
        assert t.term in {x.term for x, _ in lex.scan_segment("前文 " + t.term + " 后文")}, t.term
