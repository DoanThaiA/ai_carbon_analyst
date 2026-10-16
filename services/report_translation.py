"""Bản tiếng Anh của báo cáo ngày — dịch máy `reports.content` → `reports.content_en`.

Dùng LibreTranslate (Argos Translate, tự host — service `libretranslate` trong
docker-compose.yml), KHÔNG gọi LLM. Chất lượng vi→en ở mức "đọc hiểu được", đủ dùng tạm;
muốn đổi sang dịch vụ khác (Cohere/Azure/Google...) chỉ cần thêm 1 lớp con `Translator`.

Nguyên tắc dịch (frontend `ReportDocument.tsx` vẫn TÁCH CHUỖI trên bản dịch — xem các
regex song ngữ ở đó và `report_qc.parse_strategy`):
- Chỉ dịch giá trị chuỗi CÓ dấu tiếng Việt; khoá trong `PROTECTED_KEYS` (enum so sánh ===
  ở frontend như horizon/direction/impact/heading, mã, URL, ngày...) giữ nguyên tiếng Việt —
  frontend tự đổi nhãn hiển thị theo ngôn ngữ.
- Các "neo" cấu trúc trong văn bản (`**Tổng hợp:**`, `[TÍCH CỰC]`, tiền tố nhóm Mục 3,
  `**Mục tiêu:**`...) dịch bằng `GLOSSARY` cố định, không qua máy dịch — máy dịch có thể
  đổi chữ/vỡ markdown làm frontend không nhận ra.
- Văn bản thường được cắt quanh các neo, đoạn bold `**...**`, URL và gạch đầu dòng, chỉ phần
  chữ ở giữa đi qua máy dịch (gom + khử trùng lặp, gửi theo lô).

Bản dịch gắn với hash của `content` lúc dịch (`content_en_source_hash`): admin sửa bản
nháp → hash lệch → API coi như chưa có bản tiếng Anh cho tới khi dịch lại.
Lỗi dịch KHÔNG bao giờ làm hỏng báo cáo tiếng Việt — chỉ log (xem `ensure_report_translation`).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from db.models import Report

logger = logging.getLogger(__name__)

# Khoá giữ nguyên tiếng Việt — giá trị enum/định danh mà frontend/QC so sánh trực tiếp.
PROTECTED_KEYS = frozenset({
    "horizon", "direction", "probability", "impact", "tag", "heading", "category", "code", "name",
    "url", "source_url", "source_name", "source", "date", "datetime_vn", "source_date", "published_date",
    "price_date", "price_time", "region", "type", "role", "op", "id", "trigger_rule", "field_path",
    "topics", "model", "price", "dday", "dweek",
})

# Chuỗi "lính gác" mà frontend/backend so khớp nguyên văn — không dịch.
KEEP_VERBATIM = frozenset({
    "Chưa có thông tin kết quả xác nhận",
})

# Nhãn cố định (đoạn bold hoặc tiền tố dòng) → tiếng Anh. Khoá viết thường, không dấu ":".
GLOSSARY: Dict[str, str] = {
    # Neo kết luận Mục 3 (CONCLUSION_TAG_RE / EUA_SUMMARY_TAG_RE ở frontend)
    "tổng hợp": "Summary",
    "kết luận": "Conclusion",
    # Thẻ chiến lược (parseStrategy / report_qc.parse_strategy)
    "entry": "Entry",
    "mục tiêu": "Target",
    "quản trị rủi ro": "Risk management",
    "cắt lỗ": "Stop-loss",
    # Nhãn bullet Mục 1/4
    "eua": "EUA",
    "chính sách": "Policy",
    "địa chính trị": "Geopolitics",
    "năng lượng": "Energy",
    "khác": "Other",
    "vcm": "VCM",
    "cbam": "CBAM",
    # Tiền tố nhóm Mục 3 (GROUP_HEADLINE_PREFIXES ở frontend)
    "năng lượng & nhiên liệu hóa thạch": "Energy & fossil fuels",
    "hạn ngạch & tín chỉ carbon": "Allowances & carbon credits",
}

# Nhãn xu hướng EUA ngay sau "**Tổng hợp:**" (EUA_SENTIMENT_RE ở frontend).
SENTIMENT_TAGS = {"TÍCH CỰC": "POSITIVE", "TRUNG LẬP": "NEUTRAL", "TIÊU CỰC": "NEGATIVE"}

# Tiền tố dòng tiêu đề nhóm Mục 3 — dòng bắt đầu bằng "<Tên nhóm>:" (không bold).
GROUP_PREFIXES = ("Năng lượng & nhiên liệu hóa thạch:", "Hạn ngạch & tín chỉ carbon:", "Chính sách:")

_VI_CHARS_RE = re.compile(
    "[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.IGNORECASE
)
# Token giữ nguyên/xử lý riêng trong 1 dòng: bold, nhãn xu hướng, URL.
_TOKEN_RE = re.compile(
    r"(\*\*[^*\n]+?\*\*|\[\s*(?:TÍCH CỰC|TRUNG LẬP|TIÊU CỰC)\s*\]|https?://\S+)", re.IGNORECASE
)
_BULLET_RE = re.compile(r"^(\s*(?:[-–—•*]|\d+[.)])\s+)")

TRANSLATE_BATCH_SIZE = 40
TRANSLATE_TIMEOUT_SECONDS = 180.0
TRANSLATE_MAX_ATTEMPTS = 3


class TranslationError(RuntimeError):
    pass


class Translator(ABC):
    @abstractmethod
    async def translate_batch(self, texts: Sequence[str]) -> List[str]:
        """Dịch vi→en, trả về đúng số phần tử và đúng thứ tự của `texts`."""


class LibreTranslateTranslator(Translator):
    def __init__(self, base_url: str, api_key: str = "", batch_size: int = TRANSLATE_BATCH_SIZE):
        self._url = base_url.rstrip("/") + "/translate"
        self._api_key = api_key
        self._batch_size = batch_size

    async def translate_batch(self, texts: Sequence[str]) -> List[str]:
        out: List[str] = []
        async with httpx.AsyncClient(timeout=TRANSLATE_TIMEOUT_SECONDS) as client:
            for i in range(0, len(texts), self._batch_size):
                out.extend(await self._post(client, list(texts[i:i + self._batch_size])))
        return out

    async def _post(self, client: httpx.AsyncClient, chunk: List[str]) -> List[str]:
        payload: Dict[str, Any] = {"q": chunk, "source": "vi", "target": "en", "format": "text"}
        if self._api_key:
            payload["api_key"] = self._api_key
        last_error: Optional[Exception] = None
        for attempt in range(1, TRANSLATE_MAX_ATTEMPTS + 1):
            try:
                resp = await client.post(self._url, json=payload)
                resp.raise_for_status()
                translated = resp.json().get("translatedText")
                if not isinstance(translated, list) or len(translated) != len(chunk):
                    raise TranslationError(f"LibreTranslate trả về sai định dạng: {str(translated)[:200]}")
                return [str(t) for t in translated]
            except (httpx.HTTPError, ValueError, TranslationError) as exc:
                last_error = exc
                logger.warning("[TRANSLATE] Lần %d/%d lỗi: %s", attempt, TRANSLATE_MAX_ATTEMPTS, exc)
                if attempt < TRANSLATE_MAX_ATTEMPTS:
                    await asyncio.sleep(2 ** attempt)
        raise TranslationError(f"LibreTranslate lỗi sau {TRANSLATE_MAX_ATTEMPTS} lần: {last_error}")


def build_translator(settings: Settings) -> Optional[Translator]:
    if not settings.libretranslate_url:
        return None
    return LibreTranslateTranslator(settings.libretranslate_url, settings.libretranslate_api_key)


def content_hash(content: Optional[dict]) -> str:
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fresh_content_en(report: Report) -> Optional[dict]:
    """`content_en` nếu được dịch từ đúng `content` hiện tại, ngược lại None."""
    if not report.content or not report.content_en or not report.content_en_source_hash:
        return None
    return report.content_en if report.content_en_source_hash == content_hash(report.content) else None


def _has_vietnamese(text: str) -> bool:
    return bool(_VI_CHARS_RE.search(text))


# ── Cắt chuỗi thành các phần: (False, giữ nguyên) | (True, cần máy dịch) ──────────────

Part = tuple  # (needs_mt: bool, text: str)


_LETTER_RE = re.compile(r"[^\W\d_]")


def _split_plain(text: str) -> List[Part]:
    """Đoạn chữ thường (trong 1 chuỗi đã biết là tiếng Việt): giữ khoảng trắng 2 đầu, phần lõi
    đi máy dịch nếu có chữ cái — không đòi có dấu, vì từ như "xem", "theo" không có dấu."""
    if not text:
        return []
    core = text.strip()
    if not core or not _LETTER_RE.search(core):
        return [(False, text)]
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return [(False, lead), (True, core), (False, trail)]


def _bold_parts(token: str) -> List[Part]:
    inner = token[2:-2]
    colon = inner.rstrip().endswith(":")
    label = inner.rstrip().rstrip(":").strip()
    en = GLOSSARY.get(label.lower())
    if en is not None:
        return [(False, f"**{en}{':' if colon else ''}**")]
    if not _has_vietnamese(label):
        return [(False, token)]
    return [(False, "**"), (True, label), (False, ":**" if colon else "**")]


def _line_parts(line: str) -> List[Part]:
    parts: List[Part] = []
    m = _BULLET_RE.match(line)
    if m:
        parts.append((False, m.group(1)))
        line = line[m.end():]
    for prefix in GROUP_PREFIXES:
        if line.startswith(prefix):
            parts.append((False, GLOSSARY[prefix[:-1].lower()] + ":"))
            line = line[len(prefix):]
            break
    pos = 0
    for tok in _TOKEN_RE.finditer(line):
        parts.extend(_split_plain(line[pos:tok.start()]))
        t = tok.group(0)
        if t.startswith("**"):
            parts.extend(_bold_parts(t))
        elif t.startswith("["):
            parts.append((False, f"[{SENTIMENT_TAGS[t.strip('[] ').upper()]}]"))
        else:
            parts.append((False, t))
        pos = tok.end()
    parts.extend(_split_plain(line[pos:]))
    return parts


def split_for_translation(text: str) -> List[Part]:
    if text in KEEP_VERBATIM or not _has_vietnamese(text):
        return [(False, text)]
    parts: List[Part] = []
    for i, line in enumerate(text.split("\n")):
        if i:
            parts.append((False, "\n"))
        parts.extend(_line_parts(line))
    return parts


def _collect(node: Any, key: Optional[str], out: List[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            _collect(v, k, out)
    elif isinstance(node, list):
        for v in node:
            _collect(v, key, out)
    elif isinstance(node, str) and key not in PROTECTED_KEYS:
        out.extend(t for needs, t in split_for_translation(node) if needs)


def _rebuild(node: Any, key: Optional[str], mt: Dict[str, str]) -> Any:
    if isinstance(node, dict):
        return {k: _rebuild(v, k, mt) for k, v in node.items()}
    if isinstance(node, list):
        return [_rebuild(v, key, mt) for v in node]
    if isinstance(node, str) and key not in PROTECTED_KEYS:
        return "".join(mt.get(t, t) if needs else t for needs, t in split_for_translation(node))
    return node


# ── Che số/mã/thuật ngữ trước khi gửi máy dịch ─────────────────────────────────────────
# Argos làm SAI SỐ khá thường xuyên (thử thật: "79,0–79,5" → "720–79,5", "2.1%" → "2.3%"),
# đọc nhầm mã ("EUA" → "EUU") và dịch sai thuật ngữ ("cắt lỗ" → "cut holes"). Nên mọi số,
# mã viết hoa, đơn vị và thuật ngữ trong TERMS được thay bằng placeholder, dịch xong trả lại.
# Thử 2 kiểu placeholder (đo trên Argos vi→en): "X0X" giữ câu đầy đủ nhất nên thử trước, đoạn
# nào hỏng thì dịch lại bằng "[0]"; vẫn thiếu/thừa placeholder → giữ nguyên đoạn tiếng Việt
# (thà sót tiếng Việt còn hơn sai số trong báo cáo giao dịch).

# Thuật ngữ giao dịch → tiếng Anh cố định (khớp không phân biệt hoa thường, cụm dài trước).
# Chỉ đưa vào đây cụm Argos dịch SAI (đã thử) — che cả cụm danh từ thông thường làm câu
# mất nghĩa (thử: che "khí đốt" thì Argos bỏ luôn placeholder).
TERMS: Dict[str, str] = {
    "hạn ngạch carbon": "carbon allowances",
    "hạn ngạch": "allowances",
    "cắt lỗ": "stop-loss",
    "chốt lời": "take-profit",
    "bán quanh": "Sell around",
    "mua quanh": "Buy around",
    "kháng cự": "resistance",
    # Hướng cắt lỗ — frontend/QC đọc "above/below" để biết cắt lỗ phía trên hay dưới.
    "đóng cửa trên": "closes above",
    "đóng cửa dưới": "closes below",
    "nếu trên": "if above",
    "nếu dưới": "if below",
}
_TERMS_RE = re.compile(
    "|".join(re.escape(t) for t in sorted(TERMS, key=len, reverse=True)), re.IGNORECASE
)
_NUM = r"[+\-−]?\d+(?:[.,]\d+)*"
_UNIT = r"[A-Za-z]+/[A-Za-z0-9]+\b"
_MASK_RE = re.compile(
    r"\d+/\d+(?:/\d+)?(?![\d.,])"                         # ngày dd/mm(/yyyy)
    rf"|{_NUM}(?:\s*[–\-−]\s*{_NUM})?(?:\s*%|\s+{_UNIT})?"  # số/khoảng số (+ % hoặc đơn vị)
    rf"|\b{_UNIT}"                                           # đơn vị đứng riêng: EUR/tCO2, Bcf/d
    r"|\b[A-Z][A-Za-z0-9]*[A-Z0-9](?:['’]\d+)?\b"           # mã/viết tắt: EUA, TTF, CO2, Dec'26
)
_PLACEHOLDER_RE = re.compile(r"\[(\d+)\]")
# Kiểu placeholder gửi máy dịch, thử theo thứ tự: (định dạng, regex nhận lại).
PLACEHOLDER_STYLES = (
    ("X{}X", re.compile(r"X(\d+)X")),
    ("[{}]", _PLACEHOLDER_RE),
)


def _restore_token(token: str) -> str:
    term = TERMS.get(token.lower())
    if term is not None:
        return term
    # Dấu thập phân kiểu Việt "79,5" → "79.5" cho bản tiếng Anh (frontend đọc được cả 2).
    return re.sub(r"(?<=\d),(?=\d)", ".", token)


def mask_segment(text: str) -> tuple:
    """(văn bản có placeholder, danh sách token gốc theo thứ tự placeholder)."""
    tokens: List[str] = []

    def sub(m: "re.Match[str]") -> str:
        tokens.append(m.group(0))
        return f"[{len(tokens) - 1}]"

    masked = _TERMS_RE.sub(sub, text)
    # Che số/mã ở phần còn lại, không đụng vào placeholder vừa tạo.
    pieces = re.split(r"(\[\d+\])", masked)
    masked = "".join(p if _PLACEHOLDER_RE.fullmatch(p) else _MASK_RE.sub(sub, p) for p in pieces)
    return masked, tokens


def _styled(masked: str, fmt: str) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: fmt.format(m.group(1)), masked)


def unmask_segment(translated: str, tokens: Sequence[str], pattern: "re.Pattern[str]" = _PLACEHOLDER_RE) -> Optional[str]:
    """Trả token về chỗ placeholder; None nếu bản dịch làm mất/nhân đôi/bịa placeholder."""
    found = [int(i) for i in pattern.findall(translated)]
    if sorted(found) != list(range(len(tokens))):
        return None
    def restore(m: "re.Match[str]") -> str:
        out = _restore_token(tokens[int(m.group(1))])
        return out[:1].upper() + out[1:] if m.start() == 0 else out  # thuật ngữ đứng đầu câu

    return pattern.sub(restore, translated)


async def translate_report_content(content: dict, translator: Translator) -> dict:
    """Bản dịch tiếng Anh của `content` (cùng cấu trúc). Raise TranslationError nếu máy dịch lỗi."""
    segments: List[str] = []
    _collect(content, None, segments)
    masked = {seg: mask_segment(seg) for seg in dict.fromkeys(segments)}
    mt: Dict[str, str] = {}
    pending = list(masked)
    for fmt, pattern in PLACEHOLDER_STYLES:
        if not pending:
            break
        texts = list(dict.fromkeys(_styled(masked[seg][0], fmt) for seg in pending))
        by_text = dict(zip(texts, await translator.translate_batch(texts)))
        still: List[str] = []
        for seg in pending:
            m, tokens = masked[seg]
            out = unmask_segment(by_text.get(_styled(m, fmt), "").strip(), tokens, pattern)
            if out:
                mt[seg] = out
            else:
                still.append(seg)
        pending = still
    if pending:
        logger.warning("[TRANSLATE] %d/%d đoạn giữ nguyên tiếng Việt do máy dịch làm hỏng placeholder.",
                       len(pending), len(masked))
    return _rebuild(content, None, mt)


async def ensure_report_translation(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    report_date: str,
    *,
    force: bool = False,
) -> bool:
    """Dịch báo cáo `report_date` nếu chưa có bản tiếng Anh khớp `content` hiện tại.
    Trả True nếu sau khi chạy đã có bản dịch mới nhất. KHÔNG raise — lỗi chỉ log."""
    translator = build_translator(settings)
    if translator is None:
        return False
    try:
        async with session_factory() as session:
            report = (
                await session.execute(select(Report).where(Report.report_date == report_date))
            ).scalars().first()
            if report is None or not report.content or report.status == "generating":
                return False
            if not force and fresh_content_en(report) is not None:
                return True
            content = report.content
            source_hash = content_hash(content)

        content_en = await translate_report_content(content, translator)

        async with session_factory() as session:
            report = (
                await session.execute(select(Report).where(Report.report_date == report_date))
            ).scalars().first()
            if report is None or content_hash(report.content) != source_hash:
                # Admin sửa trong lúc đang dịch — bản dịch này đã cũ; lần sửa đó tự lên lịch dịch lại.
                logger.info("[TRANSLATE] Báo cáo %s đổi nội dung trong lúc dịch — bỏ kết quả.", report_date)
                return False
            report.content_en = content_en
            report.content_en_source_hash = source_hash
            await session.commit()
        logger.info("[TRANSLATE] Đã dịch báo cáo %s sang tiếng Anh.", report_date)
        return True
    except Exception:  # noqa: BLE001 — bản tiếng Anh là phụ, không được làm hỏng luồng chính
        logger.exception("[TRANSLATE] Dịch báo cáo %s thất bại — báo cáo tiếng Việt không bị ảnh hưởng.", report_date)
        return False
