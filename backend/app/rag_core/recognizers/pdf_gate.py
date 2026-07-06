"""Per-page text-layer gate: decide trust-the-text-layer vs OCR for PDF pages.

Instead of guessing from object geometry, measure directly whether the text
layer *explains the visible ink*: render the page (pypdfium2, annotations
included, so e-signature stamps count), binarize ink, erase the (dilated) glyph
boxes of the text layer and look at what's left.

  - residual_frac: unexplained-ink share of the page (raw).
  - textlike_frac: residual minus long horizontal/vertical structures (table
    rules, frames, solid fills) — form decoration must not force OCR. Real bank
    statements are FORMS: frames/grid/field boxes are ~3% ink on every page
    while carrying no text (validated on the production 1563-page Sber file).

Decision per page:
  - usable text layer (>=MIN_TEXT_CHARS, garbage<=MAX_GARBAGE_RATIO) -> OCR only
    when textlike_frac > T_WITH_TEXT (there is text-like ink the layer lacks);
  - chars present but garbled (broken ToUnicode) -> OCR (glyphs render fine);
  - no usable text -> OCR when raw residual_frac > T_NO_TEXT (any real content,
    incl. vector-outlined text); else blank/sparse -> trust as-is.

Fail-safe asymmetry: every heuristic failure mode degrades to an unnecessary
OCR call (correct output, wasted money), never to silently trusting garbage.

Validated (see docs in the OCR-resilience memory / session artifacts):
synthetic matrix 10/10; real `sber 5009.pdf` 1563/1563 trusted (textlike max
0.0053); real 542MB scan volume 61/61 sampled pages -> OCR (residual p50 0.44).

PDFium is NOT thread-safe process-wide (even across different documents) and
pypdfium2 adds no locking, so :func:`evaluate_pdf` serializes ALL pdfium work
behind a module-global lock — concurrent uploads queue on the gate instead of
segfaulting the container (reproduced: 2 concurrent threads on different docs
crash natively without the lock).
"""
from __future__ import annotations

import logging
import threading
import time
import unicodedata
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageOps

logger = logging.getLogger(__name__)

# PDFium forbids concurrent use even on different documents; pypdfium2 5.x has
# no internal locking. Held for the whole open->render->close lifecycle.
_PDFIUM_LOCK = threading.Lock()

MIN_TEXT_CHARS = 10        # fewer non-space chars than this = no usable layer
MAX_GARBAGE_RATIO = 0.30   # more replacement/non-printable chars = broken layer
INK_THRESHOLD = 180        # gray < this = ink (white paper renders ~245-255)
DILATE_PT = 2.0            # grow glyph boxes to absorb antialias/jpeg ringing
T_WITH_TEXT = 0.01         # >1% of page is TEXT-LIKE unexplained ink -> OCR
T_NO_TEXT = 0.003          # no layer: >0.3% raw ink -> real content -> OCR
LINE_KERNEL = 32           # px @72dpi: longer H/V runs = rules/fills, not text
RENDER_SCALE = 1.0         # 72 dpi
MAX_RENDER_PX = 2000       # cap bitmap edge for oversized pages (A0 scans etc.)


@dataclass(frozen=True)
class PageGate:
    text_from_layer: str    # extracted layer text ("" when the page needs OCR)
    should_ocr: bool
    residual_frac: float
    textlike_frac: float
    reason: str


@dataclass(frozen=True)
class DocGate:
    pages: list[PageGate]

    @property
    def ocr_indices(self) -> list[int]:
        return [i for i, p in enumerate(self.pages) if p.should_ocr]


def _text_stats(text: str) -> tuple[int, int, float]:
    """(usable_chars, n_nonspace, garbage_ratio) over non-space chars."""
    non_space = [c for c in text if not c.isspace()]
    if not non_space:
        return 0, 0, 0.0
    bad = sum(1 for c in non_space
              if c == "�" or unicodedata.category(c) in ("Cc", "Cf", "Co", "Cs", "Cn"))
    return len(non_space) - bad, len(non_space), bad / len(non_space)


def _erode_or_dilate(img: Image.Image, length: int, dx: int, dy: int, erode: bool) -> Image.Image:
    """Directional morphology for black-shapes-on-white via shift-and-combine
    doubling (log2(length) ImageChops ops). erode shrinks black runs, else grows."""
    combine = ImageChops.lighter if erode else ImageChops.darker
    out, step = img, 1
    while step < length:
        out = combine(out, ImageChops.offset(out, dx * step, dy * step))
        step *= 2
    return out


def _strip_long_runs(residual: Image.Image) -> Image.Image:
    """Remove long horizontal/vertical structures (table rules, frames, solid
    fills) from binarized residual ink; keep compact, text-like ink."""
    pad = LINE_KERNEL + 2
    img = ImageOps.expand(residual, border=pad, fill=255)
    lines = None
    for dx, dy in ((1, 0), (0, 1)):
        opened = _erode_or_dilate(img, LINE_KERNEL, dx, dy, erode=True)
        # dilate BACK with negated offsets: re-anchors the mask onto the actual
        # run (same-sign offsets leave it shifted by LINE_KERNEL-1, letting the
        # leading edge of every rule survive as "text-like" ink).
        opened = _erode_or_dilate(opened, LINE_KERNEL, -dx, -dy, erode=False)
        lines = opened if lines is None else ImageChops.darker(lines, opened)
    # grow the line mask a little on BOTH sides so anti-aliased edges don't
    # survive as "text"
    lines = _erode_or_dilate(_erode_or_dilate(lines, 3, 1, 0, erode=False), 3, 0, 1, erode=False)
    lines = _erode_or_dilate(_erode_or_dilate(lines, 3, -1, 0, erode=False), 3, 0, -1, erode=False)
    textlike = ImageChops.lighter(img, ImageChops.invert(lines))
    w, h = residual.size
    return textlike.crop((pad, pad, pad + w, pad + h))


def _evaluate_page(page, textpage) -> PageGate:
    pw, ph = page.get_size()
    n_chars = textpage.count_chars()
    text = textpage.get_text_range() if n_chars else ""
    usable, n_nonspace, garbage = _text_stats(text)
    text_usable = usable >= MIN_TEXT_CHARS and garbage <= MAX_GARBAGE_RATIO

    scale = min(RENDER_SCALE, MAX_RENDER_PX / max(pw, ph))
    bmp = page.render(scale=scale, grayscale=True, draw_annots=True)
    try:
        img = bmp.to_pil().convert("L")
    finally:
        bmp.close()
    W, H = img.size
    ink = img.point(lambda v: 0 if v < INK_THRESHOLD else 255)

    erased = ink.copy()
    draw = ImageDraw.Draw(erased)
    sx, sy = W / pw, H / ph
    d = DILATE_PT
    for i in range(n_chars):
        if textpage.get_text_range(i, 1).isspace():
            continue
        try:
            l, b, r, t = textpage.get_charbox(i)
        except Exception:  # noqa: BLE001 - a bad charbox must not kill the gate
            continue
        draw.rectangle(
            [(l - d) * sx, (ph - t - d) * sy, (r + d) * sx, (ph - b + d) * sy], fill=255
        )
    area = W * H
    residual_ink = erased.histogram()[0]
    residual_frac = residual_ink / area

    if text_usable:
        textlike_frac = (
            _strip_long_runs(erased).histogram()[0] / area if residual_ink else 0.0
        )
        if textlike_frac > T_WITH_TEXT:
            return PageGate("", True, residual_frac, textlike_frac, "unexplained ink w/ text")
        return PageGate(text, False, residual_frac, textlike_frac, "layer explains the ink")
    if garbage > MAX_GARBAGE_RATIO and n_nonspace >= MIN_TEXT_CHARS:
        return PageGate("", True, residual_frac, 0.0, "garbled layer")
    # No usable text: ANY real content (raw residual, incl. vector-drawn text)
    # -> OCR. Only pure decoration on an otherwise empty page is skipped.
    if residual_frac > T_NO_TEXT:
        return PageGate("", True, residual_frac, 0.0, "content, no layer")
    return PageGate(text, False, residual_frac, 0.0, "blank/sparse")


def evaluate_pdf(data: bytes, stop_at_monotonic: float | None = None) -> DocGate | None:
    """Gate every page of a PDF. Returns None when the bytes are not an openable
    PDF (caller falls back to the non-per-page path). A page whose evaluation
    blows up is routed to OCR (fail-safe), not raised. When the cooperative
    ``stop_at_monotonic`` deadline (``time.monotonic()`` domain) passes mid-doc,
    the remaining pages are routed to OCR so the caller's own deadline/partial
    machinery takes over instead of the gate blowing the whole budget."""
    import pypdfium2 as pdfium  # local import: keep module importable without it

    started = time.monotonic()
    with _PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except Exception:  # noqa: BLE001 - unparseable/encrypted -> not our path
            logger.warning("pdf gate: cannot open document", exc_info=True)
            return None
        try:
            total = len(pdf)
            if total == 0:
                return None
            pages: list[PageGate] = []
            for i in range(total):
                if stop_at_monotonic is not None and time.monotonic() >= stop_at_monotonic:
                    logger.warning(
                        "pdf gate: deadline reached; routing remaining pages to OCR",
                        extra={"pages_gated": i, "pages_skipped": total - i},
                    )
                    pages.extend(
                        PageGate("", True, 1.0, 1.0, "gate deadline")
                        for _ in range(total - i)
                    )
                    break
                page = textpage = None
                try:
                    page = pdf[i]
                    # Neutralize /Rotate: render/get_size honour it but FPDFText
                    # charboxes do not, so erase boxes would miss on rotated pages
                    # (forcing OCR on perfectly good text layers). Ink metrics are
                    # orientation-invariant; in-memory doc only, source untouched.
                    if page.get_rotation():
                        page.set_rotation(0)
                    textpage = page.get_textpage()
                    pages.append(_evaluate_page(page, textpage))
                except Exception:  # noqa: BLE001 - one bad page must not kill the doc
                    logger.warning("pdf gate: page %d evaluation failed; routing to OCR",
                                   i + 1, exc_info=True)
                    pages.append(PageGate("", True, 1.0, 1.0, "gate error"))
                finally:
                    if textpage is not None:
                        textpage.close()
                    if page is not None:
                        page.close()
        finally:
            pdf.close()

    gate = DocGate(pages=pages)
    logger.info(
        "pdf gate evaluated",
        extra={
            "pages_total": total,
            "pages_ocr": len(gate.ocr_indices),
            "gate_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return gate
