"""
extract.py — pull terms, phrases, and identifiers out of free text.

The router's central idea is "relevant AND distinctive". Distinctiveness comes
mostly from *identifiers* (rare, specific tokens like ``PySide6``, ``origin/main``,
``task_store.py``) and exact *phrases* (``DLL load failed``). Plain *terms* are
the weak signal — they only matter once weighted by rarity (see index.py).

Everything here is pure string processing: no app imports, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Words so common they are near-useless as evidence on their own. The rarity
# index will also down-weight frequent terms, but this list lets us penalise
# them even in a tiny corpus where document-frequency stats are unreliable.
COMMON_WORDS: frozenset[str] = frozenset({
    "app", "application", "model", "code", "file", "files", "error", "errors",
    "question", "thing", "things", "stuff", "data", "function", "method",
    "value", "system", "work", "works", "working", "use", "used", "using",
    "make", "made", "way", "ways", "issue", "problem", "thing", "stuff",
    "run", "running", "test", "tests", "line", "lines", "case", "cases",
})

# Ordinary English stopwords — dropped from the term list entirely.
STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "of", "to",
    "in", "on", "at", "by", "for", "with", "from", "as", "is", "are", "was",
    "were", "be", "been", "being", "do", "does", "did", "have", "has", "had",
    "i", "me", "my", "we", "our", "you", "your", "it", "its", "this", "that",
    "these", "those", "he", "she", "they", "them", "his", "her", "their",
    "what", "which", "who", "whom", "when", "where", "why", "how", "can",
    "could", "should", "would", "will", "shall", "may", "might", "must",
    "not", "no", "yes", "so", "than", "too", "very", "just", "about", "again",
    "into", "over", "out", "up", "down", "off", "all", "any", "some", "more",
    "most", "other", "such", "only", "own", "same", "get", "got", "im",
})


@dataclass
class Extracted:
    """Tokens pulled from one piece of text."""

    terms: list[str] = field(default_factory=list)          # lowercased content words
    phrases: list[str] = field(default_factory=list)        # 2-3 word lowercased n-grams
    identifiers: list[str] = field(default_factory=list)    # distinctive, case-preserved

    def all_lower(self) -> set[str]:
        return (
            {t.lower() for t in self.terms}
            | {p.lower() for p in self.phrases}
            | {i.lower() for i in self.identifiers}
        )


# --- identifier patterns (order matters: most specific first) ---------------
_IDENTIFIER_PATTERNS: list[re.Pattern] = [
    # Windows path: D:\Python AI assistant overlay  /  C:\Users\x\file.py
    re.compile(r"[A-Za-z]:\\[^\s\"']+"),
    # POSIX-ish path with at least two segments: /home/user/x or ./core/foo
    re.compile(r"(?:\.?/)?(?:[\w.-]+/){1,}[\w.-]+"),
    # HTTP header / kebab-cased Proper-Names: Access-Control-Allow-Origin
    re.compile(r"\b[A-Z][A-Za-z0-9]+(?:-[A-Z][A-Za-z0-9]+)+\b"),
    # UPPER_SNAKE_CASE env/config: VITE_SUPABASE_URL
    re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b"),
    # dotted file/module: task_store.py, core.audio, foo.bar.baz
    re.compile(r"\b[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)+\b"),
    # CamelCase / trailing-digit tech names: PySide6, BeautifulSoup, sk-ant
    re.compile(r"\b[A-Za-z]+[A-Z0-9][A-Za-z0-9]*\b"),
    # long opaque ids (looks like a key/slug): gndhwhhytyoaudfmmavk
    re.compile(r"\b(?=\w*[a-z])(?=\w*\d|\w{14,})[a-z0-9]{12,}\b"),
]

# Tokens that match an identifier pattern but are too generic to count.
_IDENTIFIER_BLOCKLIST = frozenset({"e.g", "i.e", "etc", "vs"})

_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def _looks_like_decimal(tok: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d+)*", tok))


def extract_identifiers(text: str) -> list[str]:
    """Return distinctive, case-preserved identifiers, de-duplicated in order."""
    found: list[str] = []
    seen: set[str] = set()
    for pat in _IDENTIFIER_PATTERNS:
        for m in pat.finditer(text):
            tok = m.group(0).strip(".,;:!?()[]{}\"'")
            if not tok or _looks_like_decimal(tok):
                continue
            if tok.lower() in _IDENTIFIER_BLOCKLIST:
                continue
            # A bare lowercase word with no separator/digit isn't distinctive.
            if tok.isalpha() and tok.islower():
                continue
            key = tok.lower()
            if key not in seen:
                seen.add(key)
                found.append(tok)
    return found


def extract_terms(text: str) -> list[str]:
    """Lowercased content words: drop stopwords, pure numbers, and 1-char tokens."""
    terms: list[str] = []
    seen: set[str] = set()
    for raw in _WORD_RE.findall(text.lower()):
        w = raw.strip("'")
        if len(w) < 2 or w in STOPWORDS or _looks_like_decimal(w):
            continue
        if w not in seen:
            seen.add(w)
            terms.append(w)
    return terms


def extract_phrases(text: str, max_n: int = 3) -> list[str]:
    """Contiguous 2- and 3-grams of non-stopword tokens, lowercased.

    These approximate the "exact phrase" signal: a query n-gram that also
    appears verbatim in a chunk's phrase list is strong evidence.
    """
    tokens = [w.strip("'") for w in _WORD_RE.findall(text.lower())]
    # Keep stopwords positionally but skip n-grams that are *all* stopwords.
    phrases: list[str] = []
    seen: set[str] = set()
    for n in range(2, max_n + 1):
        for i in range(len(tokens) - n + 1):
            gram = tokens[i:i + n]
            if all(t in STOPWORDS for t in gram):
                continue
            if any(len(t) < 2 for t in gram):
                continue
            phrase = " ".join(gram)
            if phrase not in seen:
                seen.add(phrase)
                phrases.append(phrase)
    return phrases


def extract(text: str) -> Extracted:
    """Run all three extractors over *text*."""
    return Extracted(
        terms=extract_terms(text),
        phrases=extract_phrases(text),
        identifiers=extract_identifiers(text),
    )
