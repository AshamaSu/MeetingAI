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
