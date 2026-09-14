"""Test trích chữ từ file .docx — services/quote_chat.py::_extract_docx_text
(dùng python-docx, đọc trực tiếp từ bytes trong RAM, không ghi file tạm)."""
import io

from docx import Document

from services.quote_chat import MAX_DOCX_CHARS_FOR_PROMPT, _extract_docx_text


def _make_docx_bytes(paragraphs: list[str]) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_extracts_paragraph_text_in_order():
    data = _make_docx_bytes(["Đoạn văn thử nghiệm đầu tiên.", "Đoạn thứ hai."])

    text = _extract_docx_text(data)

    assert text.index("Đoạn văn thử nghiệm đầu tiên.") < text.index("Đoạn thứ hai.")


def test_skips_empty_paragraphs():
    data = _make_docx_bytes(["", "   ", "Nội dung không rỗng."])

    text = _extract_docx_text(data)

    assert text == "Nội dung không rỗng."


def test_truncates_very_long_document():
    data = _make_docx_bytes(["a" * (MAX_DOCX_CHARS_FOR_PROMPT * 2)])

    text = _extract_docx_text(data)

    assert len(text) <= MAX_DOCX_CHARS_FOR_PROMPT + 1  # +1 cho dấu "…" nối vào
    assert text.endswith("…")
