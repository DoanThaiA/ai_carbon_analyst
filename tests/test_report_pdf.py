import pytest

from services.report_pdf import PdfLayout


def test_layout_page_size_orientation_and_query():
    assert PdfLayout().page_mm() == (210, 297)
    assert PdfLayout("a4", "landscape").page_mm() == (297, 210)
    # 16:9 dọc/ngang; vuông bỏ qua hướng.
    assert PdfLayout("wide", "portrait", "pc").page_mm() == (167, 297)
    assert PdfLayout("wide", "landscape", "tv").page_mm() == (297, 167)
    assert PdfLayout("square", "landscape").page_mm() == (210, 210)
    # Vùng in A4 dọc = 194 × 279 mm (lề 9/8 mm — PDF gọn).
    assert PdfLayout().content_px() == (733, 1054)
    q = PdfLayout("wide", "portrait", "pc").query()
    assert q["w"] == "167" and q["h"] == "297" and float(q["zoom"]) == 0.76
    # A4 dọc bản máy tính: bề rộng bố cục = vùng in / zoom phải ≥ 768px (@3xl/report) để dàn 2 cột.
    assert PdfLayout().content_px()[0] / float(PdfLayout().query()["zoom"]) >= 768
    assert PdfLayout("square", "landscape", "pc").filename_suffix() == "square-pc"


def test_landscape_layouts_use_wide_mode():
    # Khổ ngang (máy tính lẫn TV): bố cục ≥ 1024px rộng và ≥ 700px cao → trang export bật .pdf-wide
    # (WIDE_LAYOUT_PX / WIDE_MIN_PAGE_PX trong frontend/src/app/reports/[date]/export/page.tsx).
    for size, device in (("a4", "pc"), ("wide", "pc"), ("wide", "tv")):
        layout = PdfLayout(size, "landscape", device)
        w, h = layout.content_px()
        assert w / layout.scale() >= 1024 and h / layout.scale() >= 700
    # Tỉ lệ theo thiết bị, chung mọi khổ/hướng.
    assert float(PdfLayout("a4", "landscape", "pc").query()["zoom"]) == 0.76
    assert float(PdfLayout("square", "landscape", "pc").query()["zoom"]) == 0.76
    assert float(PdfLayout("wide", "landscape", "tv").query()["zoom"]) == 0.8


@pytest.mark.parametrize("kwargs", [{"size": "a3"}, {"orientation": "diagonal"}, {"device": "watch"}, {"device": "mobile"}])
def test_layout_rejects_unknown_options(kwargs):
    with pytest.raises(ValueError):
        PdfLayout(**kwargs)
