"""Tests for the per-page text-layer gate (synthetic PDFs, no network).

The decision cases mirror the validation matrix the gate shipped with:
born-digital text (trust), form chrome — frames/rules (trust: decoration is not
text-like ink), full-page scan (OCR), scan with an OCR'd invisible text layer
(trust — the layer explains the ink), garbled ToUnicode (OCR), blank (trust).
"""
import io

from PIL import Image, ImageDraw

from app.rag_core.recognizers.pdf_gate import DocGate, PageGate, evaluate_pdf

PAGE_W, PAGE_H = 595, 842


# --- minimal PDF writer (hand-rolled xref; enough for pdfium) ---

def build_pdf(objects) -> bytes:
    objs = [o.encode() if isinstance(o, str) else o for o in objects]
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode())
        out.write(body)
        out.write(b"\nendobj\n")
    xref_pos = out.tell()
    n = len(objs) + 1
    out.write(f"xref\n0 {n}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<</Size {n}/Root 1 0 R>>\nstartxref\n{xref_pos}\n%%EOF".encode())
    return out.getvalue()


def stream_obj(dict_prefix: bytes, stream: bytes) -> bytes:
    return dict_prefix + b"/Length " + str(len(stream)).encode() + b">>\nstream\n" + stream + b"\nendstream"


def jpeg_bytes(w: int, h: int) -> bytes:
    img = Image.new("RGB", (w, h), (245, 245, 240))
    d = ImageDraw.Draw(img)
    for i in range(0, h, 26):
        d.text((20, i + 4), f"scanned line {i // 26}", fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=70)
    return buf.getvalue()


def text_page(pre_ops: list[str] | None = None, lines: int = 30) -> bytes:
    ops = list(pre_ops or [])
    y = 740
    for i in range(lines):
        ops.append(f"BT /F1 10 Tf 40 {y} Td (Account statement operation {i} amount 1234.56) Tj ET")
        y -= 24
    return build_pdf([
        "<</Type/Catalog/Pages 2 0 R>>",
        "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        "<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>",
        stream_obj(b"<<", "\n".join(ops).encode()),
    ])


def scanned_page(with_text_layer: bool) -> bytes:
    img_w, img_h = 1000, 1294
    jpg = jpeg_bytes(img_w, img_h)
    ops = [f"q {PAGE_W} 0 0 {PAGE_H} 0 0 cm /Im0 Do Q"]
    if with_text_layer:
        # invisible (Tr 3) text over the drawn lines — what FineReader produces
        sy = PAGE_H / img_h
        for n, img_y in enumerate(range(0, img_h, 26)):
            baseline = PAGE_H - (img_y + 4 + 9) * sy
            ops.append(f"BT /F1 9 Tf 3 Tr 11.9 {baseline:.1f} Td (scanned line {n}) Tj ET")
    return build_pdf([
        "<</Type/Catalog/Pages 2 0 R>>",
        "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
        f"/Resources<</Font<</F1 4 0 R>>/XObject<</Im0 5 0 R>>>>/Contents 6 0 R>>",
        "<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>",
        stream_obj(b"<</Type/XObject/Subtype/Image/Width 1000/Height 1294"
                   b"/ColorSpace/DeviceRGB/BitsPerComponent 8/Filter/DCTDecode", jpg),
        stream_obj(b"<<", "\n".join(ops).encode()),
    ])


def garbled_page() -> bytes:
    tounicode = (
        b"/CIDInit /ProcSet findresource begin\n12 dict begin begincmap\n"
        b"1 begincodespacerange <00> <ff> endcodespacerange\n"
        b"1 beginbfchar <41> <FFFD> endbfchar\n"
        b"endcmap CMapName currentdict /CMap defineresource pop end end"
    )
    lines = [f"BT /F1 10 Tf 40 {740 - i * 24} Td (AAAAAAAAAAAAAAAAAAAAAAAAAAAA) Tj ET"
             for i in range(28)]
    return build_pdf([
        "<</Type/Catalog/Pages 2 0 R>>",
        "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>",
        "<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding/ToUnicode 6 0 R>>",
        stream_obj(b"<<", "\n".join(lines).encode()),
        stream_obj(b"<<", tounicode),
    ])


def _single(data: bytes) -> PageGate:
    gate = evaluate_pdf(data)
    assert isinstance(gate, DocGate) and len(gate.pages) == 1
    return gate.pages[0]


def test_born_digital_text_is_trusted():
    page = _single(text_page())
    assert not page.should_ocr
    assert "Account statement operation 0" in page.text_from_layer


def test_rotated_text_page_is_trusted():
    # /Rotate is honoured by render()/get_size() but NOT by FPDFText charboxes;
    # without neutralizing it the erase boxes miss and every rotated
    # text-layer page is misrouted to OCR.
    for rotate in (90, 180, 270):
        data = text_page().replace(
            b"/Type/Page/Parent", f"/Type/Page/Rotate {rotate}/Parent".encode(), 1
        )
        page = _single(data)
        assert not page.should_ocr, f"rotate={rotate} textlike={page.textlike_frac}"
        assert "Account statement operation 0" in page.text_from_layer


def test_form_chrome_does_not_force_ocr():
    # Full-page white bg rect + a stroked frame + rules: the real-bank-form case
    # that broke geometry gates. Decoration must not look like unexplained text.
    chrome = [
        f"q 1 1 1 rg 0 0 {PAGE_W} {PAGE_H} re f Q",
        f"q 0.5 w 0 0 0 RG 30 40 {PAGE_W - 60} 720 re S Q",
    ] + [f"q 0.5 w 0 0 0 RG 30 {y} m {PAGE_W - 30} {y} l S Q" for y in range(100, 700, 48)]
    page = _single(text_page(pre_ops=chrome))
    assert not page.should_ocr, f"textlike={page.textlike_frac}"


def test_scan_without_layer_needs_ocr():
    page = _single(scanned_page(with_text_layer=False))
    assert page.should_ocr and page.reason == "content, no layer"


def test_ocred_scan_with_layer_is_trusted():
    page = _single(scanned_page(with_text_layer=True))
    assert not page.should_ocr, f"residual={page.residual_frac}"
    assert "scanned line 0" in page.text_from_layer


def test_garbled_layer_needs_ocr():
    page = _single(garbled_page())
    assert page.should_ocr and page.reason == "garbled layer"


def test_blank_page_is_trusted_not_ocred():
    blank = build_pdf([
        "<</Type/Catalog/Pages 2 0 R>>",
        "<</Type/Pages/Kids[3 0 R]/Count 1>>",
        f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]/Resources<<>>/Contents 4 0 R>>",
        stream_obj(b"<<", b" "),
    ])
    page = _single(blank)
    assert not page.should_ocr


def test_unopenable_bytes_return_none():
    assert evaluate_pdf(b"this is not a pdf") is None


def test_strip_long_runs_alignment():
    # A bare rule must strip to ~zero (incl. its LEADING edge — the historical
    # off-by-LINE_KERNEL bug), while a compact glyph-like blob right past the
    # rule end must survive.
    from app.rag_core.recognizers.pdf_gate import _strip_long_runs

    img = Image.new("L", (200, 200), 255)
    d = ImageDraw.Draw(img)
    d.rectangle([10, 50, 150, 53], fill=0)   # long horizontal rule
    d.rectangle([160, 48, 168, 56], fill=0)  # 8px blob just past the rule end
    out = _strip_long_runs(img)
    rule_leading = out.crop((10, 50, 42, 54)).histogram()[0]
    assert rule_leading == 0, "leading edge of the rule survived the strip"
    blob = out.crop((159, 47, 170, 58)).histogram()[0]
    assert blob >= 60, "compact blob near the rule end was wrongly erased"


def test_gate_deadline_routes_remaining_pages_to_ocr():
    import time

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for _ in range(3):
        writer.add_page(PdfReader(io.BytesIO(text_page())).pages[0])
    buf = io.BytesIO()
    writer.write(buf)
    gate = evaluate_pdf(buf.getvalue(), stop_at_monotonic=time.monotonic())  # already past
    assert gate is not None and len(gate.pages) == 3
    assert [p.reason for p in gate.pages] == ["gate deadline"] * 3
    assert gate.ocr_indices == [0, 1, 2]


def test_multi_page_mixed_document():
    # page 1 born-digital, page 2 scan -> exactly page 2 needs OCR
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for data in (text_page(), scanned_page(with_text_layer=False)):
        writer.add_page(PdfReader(io.BytesIO(data)).pages[0])
    buf = io.BytesIO()
    writer.write(buf)
    gate = evaluate_pdf(buf.getvalue())
    assert gate is not None and len(gate.pages) == 2
    assert gate.ocr_indices == [1]
