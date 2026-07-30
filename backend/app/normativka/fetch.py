"""Fetching one act from the ИПС: metadata (wrapper) and full-document HTML.

Text strategy, verified live:

* ``?savertf=&nd=…&page=all`` — the PRIMARY source. Despite the name it
  returns an MHT web archive (multipart/related, quoted-printable HTML in
  windows-1251), and it is the only endpoint that returns the WHOLE act: the
  HTML view (``?doc_itself=…&page=all``) silently caps multi-volume acts at
  the first volume (НК ч.2: page=all is byte-identical to page=1, and
  page=2/3 return the same bytes again). Export of the largest acts takes
  minutes server-side (НК ч.2: ~2 min, 6.8МБ), hence the dedicated timeout.
* ``?doc_itself=&nd=…&page=all&rdk=…`` — fallback when the export fails;
  fine for every single-volume act (same paragraph markup, same parser).

The wrapper (``?docbody=&nd=…``) carries the current rdk (redaction id) in
its frame src and the complete redaction picker — the change-detection key:
an act whose current rdk equals the indexed rdk has not changed.
"""
import email
import logging
import re
from dataclasses import dataclass

from app.normativka.ips_client import IpsClient, IpsError

logger = logging.getLogger(__name__)

# Truncation is detected STRUCTURALLY, not by size. A byte floor calibrated on
# codices (hundreds of КБ) rejected legitimately short acts — «Об утверждении
# Указов Президиума Верховного Совета РСФСР» is 6-14КБ, and 19 such acts failed
# a 20КБ floor. Completeness is instead proven by the document's own closing
# marker: a stalled стream (HTTP 200 with a partial body — a known portal
# failure mode) never reaches it. The floor that remains only catches empty
# and error-page responses.
_MIN_BYTES = 1_000
_EXPORT_TIMEOUT = 600.0

# Unbounded on purpose: a capped pattern silently loses the title of any act
# with a long name, and the scraper verifies the fetched title against the
# expected one — a lost title would fail the whole act.
_TITLE_RE = re.compile(r"<title>\s*([^<]+?)\s*</title>", re.I)
def _current_rdk_re(nd: str) -> re.Pattern:
    # The text-frame src carries «…&nd=<nd>&page=1&rdk=<N>». For most acts the
    # URL starts with doc_itself=, but the biggest ones (КоАП, НК ч.1, БК)
    # use a variant with an encoded-warning ``fostr=…`` prefix instead — so
    # the anchor is the act's own nd, not the endpoint name. Anchoring on nd
    # also guarantees we never pick up a foreign document's rdk.
    return re.compile(rf"nd={re.escape(nd)}&page=1&rdk=(\d+)")
_REDACTION_OPT_RE = re.compile(r'<option id="s1o\d+" value="(\d+)"[^>]*>\s*([^<]+?)\s*</option>')


@dataclass(frozen=True)
class ActMeta:
    nd: str
    title: str
    current_rdk: str
    # (rdk, label) pairs from the redaction picker, e.g. ("84", "84 - от 09.06.2026 № …").
    # NB: label dates are SIGNING dates of amending laws, not entry-into-force.
    redactions: tuple[tuple[str, str], ...]


def fetch_act_meta(client: IpsClient, nd: str, *, parse_retries: int = 2) -> ActMeta:
    # Parse-level retry: the portal is known to serve truncated-but-HTTP-200
    # wrapper pages under load. A page that decoded fine but lacks the rdk
    # frame src is indistinguishable from a structural change, so refetch
    # before giving up.
    rdk_re = _current_rdk_re(nd)
    html = ""
    rdk_match = None
    for _ in range(parse_retries):
        html = client.get_text(f"docbody=&nd={nd}", min_bytes=_MIN_BYTES, echo=f"nd={nd}")
        rdk_match = rdk_re.search(html)
        if rdk_match:
            break
    if not rdk_match:
        raise IpsError(f"Обёртка nd={nd}: не найден текущий rdk (структура страницы изменилась?)")
    title_match = _TITLE_RE.search(html)
    seen: dict[str, str] = {}
    for rdk, label in _REDACTION_OPT_RE.findall(html):
        seen.setdefault(rdk, " ".join(label.split()))
    return ActMeta(
        nd=nd,
        title=" ".join(title_match.group(1).split()) if title_match else "",
        current_rdk=rdk_match.group(1),
        redactions=tuple(seen.items()),
    )


def _mht_to_html(raw: bytes) -> str:
    """Extract and concatenate the text/html parts of an MHT web archive.

    The archive's terminating boundary («--<boundary>--») proves the response
    arrived whole: a stalled stream ends mid-part with HTTP 200 and would
    otherwise be parsed into a silently truncated act.
    """
    boundary_match = re.search(rb'boundary="?([^"\r\n]+)', raw[:2048])
    if boundary_match:
        terminator = b"--" + boundary_match.group(1).strip() + b"--"
        if terminator not in raw:
            raise IpsError("MHT-экспорт оборван: нет завершающей границы MIME")
    message = email.message_from_bytes(raw)
    chunks: list[str] = []
    for part in message.walk():
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True)
        if payload:
            charset = part.get_content_charset() or "cp1251"
            chunks.append(payload.decode(charset, errors="replace"))
    if not chunks:
        raise IpsError("MHT-экспорт не содержит HTML-частей")
    return "".join(chunks)


def _fetch_html_export(client: IpsClient, nd: str) -> str:
    # The export endpoint always serves the CURRENT redaction; the caller
    # records which rdk the wrapper reported alongside the fetched text. The
    # per-request timeout (instead of a second client) keeps the module's
    # one-serial-client-per-IP invariant intact.
    raw = client.get_raw(f"savertf=&nd={nd}&page=all", min_bytes=_MIN_BYTES, timeout=_EXPORT_TIMEOUT)
    head = raw[:512].lstrip()
    if head.startswith(b"MIME-Version") or b"multipart/related" in raw[:512]:
        return _mht_to_html(raw)
    if head.startswith(b"<"):
        return _require_complete_html(raw.decode("cp1251", errors="replace"), nd)
    raise IpsError(f"savertf nd={nd}: неизвестный формат ответа ({raw[:20]!r})")


def _require_complete_html(html: str, nd: str) -> str:
    """Guard against a stalled stream: a whole page ends with </html>."""
    if "</html>" not in html[-2048:].lower():
        raise IpsError(f"nd={nd}: ответ оборван (нет закрывающего </html>), {len(html)} симв.")
    return html


def _fetch_html_view(client: IpsClient, nd: str, rdk: str) -> str:
    html = client.get_text(f"doc_itself=&nd={nd}&page=all&rdk={rdk}", min_bytes=_MIN_BYTES)
    return _require_complete_html(html, nd)


def fetch_act_text(client: IpsClient, nd: str, *, rdk: str) -> str:
    """Full-document HTML of an act's current redaction. Export first, view fallback."""
    try:
        return _fetch_html_export(client, nd)
    except IpsError as exc:
        logger.warning(
            "MHT-экспорт не удался, переходим на doc_itself",
            extra={"nd": nd, "error": str(exc)},
        )
    return _fetch_html_view(client, nd, rdk)
