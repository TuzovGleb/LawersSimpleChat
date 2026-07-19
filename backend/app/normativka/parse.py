"""Cutting an act's HTML into articles.

Works on the ИПС document renderer's markup (same for the MHT export and the
``doc_itself`` view):

* paragraphs are ``<p>`` blocks; headers of articles and structural units
  (раздел/глава/параграф) carry ``class="H"``;
* dotted article numbers are typeset as superscripts via
  ``<span class="W9">``: «Статья 346.11» is ``Статья 346<span class="W9">11</span>``,
  possibly chained («15.34.1» = ``15<span>34</span><span>1</span>`` or a mix
  with literal dots). They are normalized to dotted form BEFORE tag
  stripping — this is what makes КоАП/НК numbering searchable;
* amendment marks («(В редакции Федерального закона …)», class="mark") are
  part of the article text and are kept: lawyers rely on them.

The parser deliberately reads ONLY line structure and never guesses beyond
it: an article runs from its H-header to the next article H-header.
"""
import html as html_lib
import re
from dataclasses import dataclass

# Superscript index -> dotted numbering («346<W9>11</W9>» -> «346.11»). The
# index itself may be composite: «284<W9>2.1</W9>», «333<W9>4-1</W9>» — dots
# and dashes inside the superscript are normalized to a dotted chain, giving
# the canonical citation form («284.2.1», «333.4.1»). The digit lookbehind is
# essential: W9 superscripts after a DIGIT are article/chapter indices (both
# in headers and in body cross-references «в соответствии со статьей 346.15»),
# while after a letter they are units of measure («кг/м³» = м<W9>3</W9>) —
# those must NOT gain a dot. Remaining non-index superscripts are unwrapped
# in place («кг/м3») by _SUP_PLAIN_RE.
_SUP_RE = re.compile(
    r'(?<=\d)<span[^>]*class="W9"[^>]*>\s*(\d+(?:\s*[.\-]\s*\d+)*)\s*</span>'
    r"|(?<=\d)<sup[^>]*>\s*(\d+(?:\s*[.\-]\s*\d+)*)\s*</sup>",
    re.I,
)
_SUP_PLAIN_RE = re.compile(r'<span[^>]*class="W9"[^>]*>\s*([^<]*?)\s*</span>', re.I)

# Tables (tax-rate scales, amortization groups, fee schedules) sit BETWEEN
# <p> blocks in the renderer's markup, so a paragraph-only splitter would drop
# them silently (verified on НК ч.2: all 15 tables lost). They are flattened
# into row-per-paragraph text («cell | cell | cell») before paragraph
# splitting, which attaches them to the current article as body text.
_TABLE_RE = re.compile(r"<table[^>]*>(.*?)</table>", re.I | re.S)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)


def _normalize_sup(match: re.Match) -> str:
    index = (match.group(1) or match.group(2) or "").strip()
    return "." + re.sub(r"\s*[.\-]\s*", ".", index)


def _flatten_tables(html: str) -> str:
    def replace(match: re.Match) -> str:
        rows: list[str] = []
        for tr in _TR_RE.findall(match.group(1)):
            cells = [_clean_text(cell) for cell in _CELL_RE.findall(tr)]
            cells = [cell for cell in cells if cell]
            if cells:
                rows.append(" | ".join(cells))
        if not rows:
            return " "
        return "".join(f"<p>{row}</p>" for row in rows)

    return _TABLE_RE.sub(replace, html)
_TAG_RE = re.compile(r"<[^>]+>")
_P_SPLIT_RE = re.compile(r"<p\b([^>]*)>", re.I)
_CLASS_RE = re.compile(r'class="([^"]*)"')

_ARTICLE_HEAD_RE = re.compile(r"^Статья\s+(\d+(?:\.\d+)*)\s*\.?\s*(.*)$", re.S)
# Structural units that form the chapter path, most-significant first.
_STRUCTURE_LEVELS: tuple[tuple[str, re.Pattern], ...] = (
    ("часть", re.compile(r"^Часть\s+(первая|вторая|третья|четвертая|пятая)\b.*$", re.I)),
    ("раздел", re.compile(r"^Раздел\s+\S+.*$", re.I)),
    ("подраздел", re.compile(r"^Подраздел\s+\S+.*$", re.I)),
    ("глава", re.compile(r"^Глава\s+\d+(?:\.\d+)*\b.*$", re.I)),
    ("параграф", re.compile(r"^(§|Параграф)\s*\d+.*$", re.I)),
)


@dataclass(frozen=True)
class Article:
    number: str        # «81», «333.19», «15.34.1»
    title: str         # «Расторжение трудового договора по инициативе работодателя»
    text: str          # plain text of the article body (title line included)
    chapter_path: str  # «Часть третья › Раздел III › Глава 13»


def _clean_text(fragment: str) -> str:
    text = _TAG_RE.sub(" ", fragment)
    text = html_lib.unescape(text)
    return " ".join(text.split())


def _paragraphs(html: str) -> list[tuple[str, str]]:
    """Split document HTML into (css_class, plain_text) paragraphs."""
    html = _SUP_RE.sub(_normalize_sup, html)
    html = _SUP_PLAIN_RE.sub(lambda m: m.group(1), html)
    html = _flatten_tables(html)
    parts = _P_SPLIT_RE.split(html)
    # parts = [prefix, attrs1, body1, attrs2, body2, ...]
    result: list[tuple[str, str]] = []
    for i in range(1, len(parts) - 1, 2):
        attrs, body = parts[i], parts[i + 1]
        body = body.split("</p>")[0]
        text = _clean_text(body)
        if not text:
            continue
        class_match = _CLASS_RE.search(attrs)
        result.append((class_match.group(1) if class_match else "", text))
    return result


def _structure_level(text: str) -> tuple[int, str] | None:
    for level, (_, pattern) in enumerate(_STRUCTURE_LEVELS):
        if pattern.match(text):
            return level, text
    return None


def split_articles(html: str) -> list[Article]:
    articles: list[Article] = []
    path: list[str | None] = [None] * len(_STRUCTURE_LEVELS)

    current: dict | None = None

    def flush() -> None:
        # ``path`` is safe to read here: structure headers always flush()
        # BEFORE mutating it, so an open article sees its own path.
        nonlocal current
        if current is None:
            return
        body = "\n".join(current["lines"]).strip()
        if body:
            articles.append(
                Article(
                    number=current["number"],
                    title=current["title"],
                    text=body,
                    chapter_path=" › ".join(p for p in path if p),
                )
            )
        current = None

    for css_class, text in _paragraphs(html):
        is_header = "H" in css_class.split()
        if is_header:
            article_match = _ARTICLE_HEAD_RE.match(text)
            if article_match:
                flush()
                current = {
                    "number": article_match.group(1),
                    "title": article_match.group(2).strip(),
                    "lines": [text],
                }
                continue
            structure = _structure_level(text)
            if structure is not None:
                flush()
                level, header = structure
                path[level] = header
                for deeper in range(level + 1, len(path)):
                    path[deeper] = None
                continue
            # A non-article, non-structural header inside an article (rare) —
            # treat as article text so nothing is silently dropped.
        if current is not None:
            current["lines"].append(text)

    flush()
    return articles
