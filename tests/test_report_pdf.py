import pytest

from services.report_pdf import PdfLayout


def test_layout_page_size_orientation_and_query():
    assert PdfLayout().page_mm() == (210, 297)
    assert PdfLayout("a4", "landscape").page_mm() == (297, 210)
    # 16:9 dọc cho điện thoại, ngang cho TV; vuông bỏ qua hướng.
    assert PdfLayout("wide", "portrait", "mobile").page_mm() == (167, 297)
    assert PdfLayout("wide", "landscape", "tv").page_mm() == (297, 167)
    assert PdfLayout("square", "landscape").page_mm() == (210, 210)
    # Vùng in A4 dọc = 190 × 273 mm.
    assert PdfLayout().content_px() == (718, 1032)
    q = PdfLayout("wide", "portrait", "mobile").query()
    assert q["w"] == "167" and q["h"] == "297" and float(q["zoom"]) > 1
    assert PdfLayout("square", "landscape", "pc").filename_suffix() == "square-pc"


@pytest.mark.parametrize("kwargs", [{"size": "a3"}, {"orientation": "diagonal"}, {"device": "watch"}])
def test_layout_rejects_unknown_options(kwargs):
    with pytest.raises(ValueError):
        PdfLayout(**kwargs)
