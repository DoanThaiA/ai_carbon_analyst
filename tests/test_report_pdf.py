import pytest

from services.report_pdf import PdfLayout


def test_layout_page_size_orientation_and_query():
    assert PdfLayout().page_mm() == (210, 297)
    assert PdfLayout("a4", "landscape").page_mm() == (297, 210)
    # 16:9 dọc/ngang; vuông bỏ qua hướng.
    assert PdfLayout("wide", "portrait", "pc").page_mm() == (167, 297)
    assert PdfLayout("wide", "landscape", "tv").page_mm() == (297, 167)
    assert PdfLayout("square", "landscape").page_mm() == (210, 210)
    # Vùng in A4 dọc = 190 × 273 mm.
    assert PdfLayout().content_px() == (718, 1032)
    q = PdfLayout("wide", "portrait", "pc").query()
    assert q["w"] == "167" and q["h"] == "297" and float(q["zoom"]) == 0.82
    # A4 dọc bản máy tính: bề rộng bố cục = vùng in / zoom phải ≥ 768px (@3xl/report) để dàn 2 cột.
    assert PdfLayout().content_px()[0] / float(PdfLayout().query()["zoom"]) >= 768
    assert PdfLayout("square", "landscape", "pc").filename_suffix() == "square-pc"


@pytest.mark.parametrize("kwargs", [{"size": "a3"}, {"orientation": "diagonal"}, {"device": "watch"}, {"device": "mobile"}])
def test_layout_rejects_unknown_options(kwargs):
    with pytest.raises(ValueError):
        PdfLayout(**kwargs)
