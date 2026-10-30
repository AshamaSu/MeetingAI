"""词表加载与匹配。兼容两种格式：
1) CSV（B3 全 schema）：term,lang,level,category,variants,regex,enabled（首行为表头）
2) lite words.txt：'级别,词' / '词,级别' / '词|级别'
按 mtime 热加载。
"""
import re
import time
import unicodedata
from pathlib import Path

LEVELS = ("L1", "L2", "L3")


class Term:
    __slots__ = ("term", "level", "lang", "category", "variants", "regex", "enabled")

    def __init__(self, term, level, lang="", category="", variants=(), regex="", enabled=True):
        self.term = term.lower()
        self.level = level
        self.lang = lang
        self.category = category
        self.variants = tuple(v.lower() for v in variants if v)
        self.regex = regex
        self.enabled = enabled

    @property
    def all_forms(self):
        return [self.term] + list(self.variants)


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = re.sub(r"[^\w\u4e00-\u9fff]+", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def match_form(form: str, norm: str) -> bool:
    if all(ord(c) < 128 for c in form):  # 拉丁文：词边界，防 class→ass
        return re.search(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])", norm) is not None
    return form in norm  # 中文等：子串


def match_term(t: Term, norm: str) -> bool:
    if t.regex:
        try:
            return re.search(t.regex, norm) is not None
        except re.error:
            return False
    return any(match_form(f, norm) for f in t.all_forms)


def load_wordlist(path: Path):
    words = []
    if not path.exists():
        return words
    raw = path.read_text(encoding="utf-8-sig").splitlines()
    lines = [ln for ln in raw if ln.strip() and not ln.strip().startswith("#")]
    is_csv = bool(lines) and lines[0].strip().lower().startswith("term,")
    for ln in lines:
        s = ln.strip()
        if is_csv:
            cols = [c.strip() for c in s.split(",")]
            cols += [""] * (7 - len(cols))
            term, lang, level, category, variants, regex, enabled = cols[:7]
            if level.upper() not in LEVELS or not term:
                continue
            words.append(Term(term, level.upper(), lang, category,
                              [v.strip() for v in variants.split() if v.strip()],
                              regex, enabled.lower() not in ("0", "false", "no")))
        else:
            parts = [p.strip() for p in re.split(r"[,|]", s) if p.strip()]
            if len(parts) < 2:
                continue
            a, b = parts[0], parts[1]
            if a.upper() in LEVELS:
                level, term = a.upper(), b
            elif b.upper() in LEVELS:
                term, level = a, b.upper()
            else:
                continue
            words.append(Term(term, level))
    return words


class Lexicon:
    """带 mtime 热加载的词表。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._words = []
        self._mtime = None

    def words(self):
        try:
            m = self.path.stat().st_mtime
        except OSError:
            m = None
        if m != self._mtime:
            self._words = load_wordlist(self.path)
            self._mtime = m
            self.loaded_at = time.strftime("%H:%M:%S")
        return self._words

    def scan_segment(self, text: str):
        """返回该句命中的 [(Term, form)]。"""
        norm = normalize(text)
        if not norm:
            return []
        out = []
        for t in self.words():
            if not t.enabled:
                continue
            if t.regex:
                try:
                    if re.search(t.regex, norm):
                        out.append((t, t.regex))
                except re.error:
                    pass
            else:
                for f in t.all_forms:
                    if match_form(f, norm):
                        out.append((t, f))
                        break
        return out
