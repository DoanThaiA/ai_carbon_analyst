"""Bản tiếng Anh của báo cáo ngày — dịch máy `reports.content` → `reports.content_en`.

Mặc định dịch bằng Claude (`ClaudeTranslator`, `REPORT_TRANSLATION_PROVIDER=claude`): MỌI chữ hiển thị
(tiêu đề mục, tiêu đề khối, nhãn chủ đề, văn xuôi...) do Claude TỰ dịch nguyên chuỗi, nhận nguyên văn — không
bảng dịch cứng, không kiểm tra/sửa lại (`translate_strings_llm`). Prompt chỉ dặn vài neo frontend nhận diện
(**Summary:**, [POSITIVE], **Target:**...) — sửa regex ở `ReportDocument.tsx` thì sửa cả prompt.
`REPORT_TRANSLATION_PROVIDER=libretranslate` = LibreTranslate (Argos, tự host, không gọi LLM) — đi đường cũ
bên dưới: cắt quanh neo + bảng thuật ngữ cố định + che số, chất lượng "đọc hiểu được".

Nguyên tắc dịch đường LibreTranslate (frontend `ReportDocument.tsx` vẫn TÁCH CHUỖI trên bản dịch — xem các
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
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

import anthropic
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from db.models import Report

logger = logging.getLogger(__name__)

# Khoá giữ nguyên tiếng Việt — giá trị enum/định danh mà frontend/QC so sánh trực tiếp.
# Tên nguồn/tên mã/tiêu đề khối KHÔNG nằm đây: chỉ để hiển thị, nên vẫn dịch khi có chữ Việt
# (vd source_name "Ủy ban châu Âu, COM(2026) 616", tiêu đề khối Mục 3 do LLM tự đặt).
PROTECTED_KEYS = frozenset({
    "horizon", "direction", "probability", "impact", "tag", "category", "code",
    "url", "source_url", "date", "datetime_vn", "source_date", "published_date",
    "price_date", "price_time", "region", "type", "role", "op", "id", "trigger_rule", "field_path",
    "topics", "model", "price", "dday", "dweek",
})
UNTRANSLATED_SUBTREES = frozenset({"knowledge_used", "knowledge_ref"})

ENUM_HEADINGS = frozenset({"Phân tích", "Cần theo dõi", "Diễn biến chính"})


def _protected(key: Optional[str], value: str) -> bool:
    return key in PROTECTED_KEYS or (key == "heading" and value in ENUM_HEADINGS)
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

# Chuỗi cố định do bộ sinh viết (tiêu đề mục...) → dịch bằng bảng, khớp NGUYÊN chuỗi. Argos dịch
# rác với cụm ngắn (thử thật: "Lịch sự kiện 7 ngày tới" → "Synchronising folder").
FIXED_STRINGS: Dict[str, str] = {
    "Tóm tắt điều hành": "Executive summary",
    "Bảng giá nhanh": "Price snapshot",
    "Diễn biến chính": "Key developments",
    "Phân tích các yếu tố năng lượng tương quan, chính sách ảnh hưởng đến giá EUA":
        "Analysis of related energy factors and policies affecting EUA prices",
    "Phân tích tác động của tin tức tới EUA (không có phiên giao dịch)":
        "News impact on EUA (no trading session)",
    "Cập nhật tín chỉ carbon & CBAM": "Carbon credits & CBAM update",
    "Chi tiết các tin tức chính": "Key news in detail",
    "Lịch sự kiện 7 ngày tới": "Events calendar — next 7 days",
    "Nguồn tham khảo": "References",
    "Quan điểm thị trường": "Market view",
    "Gợi ý kinh doanh & giải pháp cho SIM": "Business suggestions & solutions for SIM",
    "Không có diễn biến trọng yếu.": "No significant developments.",
    "Không thể sinh nội dung tự động.": "Content could not be generated automatically.",
}

# Thẻ chủ đề cạnh bài viết Mục 6 (khoá "topics", nhãn lấy từ report_generator.TOPIC_DISPLAY_LABELS)
# — nhãn cố định nên dịch bằng bảng, không qua máy dịch. Thêm topic mới thì bổ sung ở đây.
TOPIC_LABELS_EN: Dict[str, str] = {
    "EUA/ETS": "EUA/ETS",
    "Khí gas": "Gas",
    "Điện châu Âu": "European power",
    "Than": "Coal",
    "Dầu": "Oil",
    "Năng lượng tái tạo": "Renewables",
    "Hydrogen": "Hydrogen",
    "Địa chính trị": "Geopolitics",
    "Chính sách EU": "EU policy",
    "CBAM": "CBAM",
    "VCM": "VCM",
    "Thị trường carbon toàn cầu": "Global carbon market",
    "Chính sách carbon VN": "Vietnam carbon policy",
}

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
    # True = máy dịch LLM: tự dịch nguyên chuỗi — xem `translate_strings_llm`.
    # Argos thì không: đi đường cắt mảnh + bảng thuật ngữ.
    whole_strings = False

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


# ── Claude ─────────────────────────────────────────────────────────────────────────────
CLAUDE_BATCH_MAX_CHARS = 8000   # tổng ký tự nguồn / 1 lần gọi — đủ ngữ cảnh, output không bị cắt
CLAUDE_BATCH_MAX_ITEMS = 60
CLAUDE_CONCURRENCY = 4
CLAUDE_MAX_TOKENS = 16000
CLAUDE_EFFORT = "low"  # dịch có hướng dẫn rõ — không cần suy luận sâu

_CLAUDE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}


def _claude_system_prompt() -> str:
    return """You are a professional financial translator. Translate a Vietnamese daily carbon & energy \
market report (EU ETS / EUA, gas, coal, power, oil, CBAM, Vietnam carbon market) into natural, fluent, \
grammatical English, as a commodities analyst would write it.

You receive JSON {"segments": [{"id": n, "text": "..."}]} in document order (use neighbouring segments \
as context). Return every id once with its translation. Keep the markdown (line breaks, bullets, **bold**) \
and the URLs. Use English number format (79,5 → 79.5).

The web page looks for a few exact English markers, so render these exactly: **Tổng hợp:** → **Summary:**, \
**Kết luận:** → **Conclusion:**, [TÍCH CỰC]/[TRUNG LẬP]/[TIÊU CỰC] → [POSITIVE]/[NEUTRAL]/[NEGATIVE], \
**Mục tiêu:** → **Target:**, **Quản trị rủi ro:** → **Risk management:**, **Entry:** stays **Entry:**, and \
lines starting "Năng lượng & nhiên liệu hóa thạch:" / "Hạn ngạch & tín chỉ carbon:" / "Chính sách:" → \
"Energy & fossil fuels:" / "Allowances & carbon credits:" / "Policy:"."""


class ClaudeTranslator(Translator):
    """Dịch bằng Claude — lô theo thứ tự tài liệu (có ngữ cảnh), JSON có cấu trúc, chạy song song."""

    whole_strings = True

    def __init__(self, api_key: str, model: str):
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=TRANSLATE_MAX_ATTEMPTS)
        self._model = model
        self._system = _claude_system_prompt()
        self._sem = asyncio.Semaphore(CLAUDE_CONCURRENCY)

    async def translate_batch(self, texts: Sequence[str]) -> List[str]:
        batches: List[List[int]] = []
        size = 0
        for i, t in enumerate(texts):
            if not batches or size + len(t) > CLAUDE_BATCH_MAX_CHARS or len(batches[-1]) >= CLAUDE_BATCH_MAX_ITEMS:
                batches.append([])
                size = 0
            batches[-1].append(i)
            size += len(t)
        out = list(texts)  # id bị thiếu trong kết quả → giữ nguyên gốc
        results = await asyncio.gather(*(self._call([texts[i] for i in b]) for b in batches))
        for b, res in zip(batches, results):
            for local_id, text in res.items():
                if 0 <= local_id < len(b):
                    out[b[local_id]] = text
        return out

    async def _call(self, chunk: List[str]) -> Dict[int, str]:
        payload = json.dumps({"segments": [{"id": i, "text": t} for i, t in enumerate(chunk)]}, ensure_ascii=False)
        async with self._sem:
            try:
                response = await self._client.beta.messages.create(
                    model=self._model,
                    max_tokens=CLAUDE_MAX_TOKENS,
                    system=self._system,
                    messages=[{"role": "user", "content": payload}],
                    output_config={
                        "effort": CLAUDE_EFFORT,
                        "format": {"type": "json_schema", "schema": _CLAUDE_OUTPUT_SCHEMA},
                    },
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
            except anthropic.APIError as exc:
                raise TranslationError(f"Claude lỗi: {exc}") from exc
        if response.stop_reason == "refusal":
            logger.warning("[TRANSLATE] Claude từ chối lô %d đoạn (refusal) — các đoạn đó giữ tiếng Việt.", len(chunk))
            return {}
        if response.stop_reason == "max_tokens":
            logger.warning("[TRANSLATE] Lô %d đoạn bị cắt do max_tokens — các đoạn thiếu sẽ được gửi lại.", len(chunk))
        raw = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
        try:
            items = json.loads(raw)["translations"]
            return {int(it["id"]): str(it["text"]) for it in items}
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("[TRANSLATE] Claude trả JSON lỗi (%s) — lô %d đoạn sẽ được gửi lại.", exc, len(chunk))
            return {}


def build_translator(settings: Settings) -> Optional[Translator]:
    provider = settings.report_translation_provider
    if provider == "claude" and settings.anthropic_api_key:
        return ClaudeTranslator(settings.anthropic_api_key, settings.report_translation_model)
    if provider == "libretranslate" and settings.libretranslate_url:
        return LibreTranslateTranslator(settings.libretranslate_url, settings.libretranslate_api_key)
    return None


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


# Nhãn đầu vế trong chuỗi "Cơ hội: … → Giải pháp: … → Kỳ vọng: …" (định dạng gợi ý biz cũ).
ARROW_LABELS = {
    "cơ hội": "Opportunity", "giải pháp": "Solution", "kỳ vọng": "Expectation",
    "tình huống": "Situation", "hành động": "Action", "lý do": "Reason",
}
_ARROW_SPLIT_RE = re.compile(r"(\s*→\s*)")
_LEADING_LABEL_RE = re.compile(r"^([^\W\d_][^:]{0,20}?):\s*")


def _line_parts(line: str) -> List[Part]:
    """1 dòng → các phần. "→" là dấu phân cách cấu trúc: mỗi vế dịch riêng (che "→" như token thì
    Argos đẩy nó lệch chỗ), nhãn đầu vế ("Giải pháp:"...) dịch bằng bảng."""
    if "→" in line:
        parts: List[Part] = []
        for i, piece in enumerate(_ARROW_SPLIT_RE.split(line)):
            if i % 2:
                parts.append((False, " → "))
                continue
            lab = _LEADING_LABEL_RE.match(piece)
            if lab and lab.group(1).strip().lower() in ARROW_LABELS:
                parts.append((False, ARROW_LABELS[lab.group(1).strip().lower()] + ": "))
                piece = piece[lab.end():]
            parts.extend(_line_parts_simple(piece))
        return parts
    return _line_parts_simple(line)


def _line_parts_simple(line: str) -> List[Part]:
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
    if text.strip() in FIXED_STRINGS:
        return [(False, FIXED_STRINGS[text.strip()])]
    parts: List[Part] = []
    for i, line in enumerate(text.split("\n")):
        if i:
            parts.append((False, "\n"))
        parts.extend(_line_parts(line))
    return parts


def _collect(node: Any, key: Optional[str], out: List[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k not in UNTRANSLATED_SUBTREES:
                _collect(v, k, out)
    elif isinstance(node, list):
        for v in node:
            _collect(v, key, out)
    elif isinstance(node, str) and not _protected(key, node):
        out.extend(t for needs, t in split_for_translation(node) if needs)


def _collect_whole(node: Any, key: Optional[str], out: List[str]) -> None:
    """Chuỗi cần dịch nguyên vẹn (theo thứ tự tài liệu — Claude dùng các đoạn liền kề làm ngữ cảnh)."""
    if isinstance(node, dict):
        for k, v in node.items():
            _collect_whole(v, k, out)
    elif isinstance(node, list):
        for v in node:
            _collect_whole(v, key, out)
    elif key in UNTRANSLATED_SUBTREES:
        return
    elif (
        isinstance(node, str) and not _protected(key, node) and node not in KEEP_VERBATIM
        and node.strip() not in FIXED_STRINGS and _has_vietnamese(node)
    ):
        out.append(node)


def _rebuild(node: Any, key: Optional[str], mt: Dict[str, str]) -> Any:
    if isinstance(node, dict):
        return {k: (v if k in UNTRANSLATED_SUBTREES else _rebuild(v, k, mt)) for k, v in node.items()}
    if isinstance(node, list):
        return [_rebuild(v, key, mt) for v in node]
    if isinstance(node, str) and key == "topics":
        return TOPIC_LABELS_EN.get(node, node)
    if isinstance(node, str) and not _protected(key, node):
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
    # Argos dịch "đồng" thành "dollar"/"copper" — tiền VND phải giữ đúng đơn vị.
    "nghìn tỷ đồng": "trillion VND",
    "tỷ đồng": "billion VND",
    "triệu đồng": "million VND",
    "nghìn đồng": "thousand VND",
    # Hướng cắt lỗ — frontend/QC đọc "above/below" để biết cắt lỗ phía trên hay dưới.
    "đóng cửa trên": "closes above",
    "đóng cửa dưới": "closes below",
    "nếu trên": "if above",
    "nếu dưới": "if below",
}
_TERMS_RE = re.compile(
    "|".join(re.escape(t) for t in sorted(TERMS, key=len, reverse=True)), re.IGNORECASE
)
_NUM = r"[~≈]?[+\-−]?\d+(?:[.,]\d+)*"
_UNIT = r"[A-Za-z]+/[^\W_]+\b"  # vế sau có thể là chữ Việt: USD/tấn, USD/thùng
_SUPER_UNIT = r"[A-Za-z]{1,3}[²³]"  # m², m³, km² — Argos đổi thành "m2" (bị coi là bịa số)
_MASK_RE = re.compile(
    r"\d+(?:/[^\W\d_][\w]*(?:-[\w]+)*)+"                       # số hiệu văn bản: 781/QĐ-UBND, 15/2025/NĐ-CP
    r"|\d{4}-\d{2}-\d{2}"                                   # ngày ISO 2026-10-13
    r"|\d+/\d+(?:/\d+)?(?![\d.,])"                        # ngày dd/mm(/yyyy)
    r"|[Δ→←↑↓⇒•▲▼]"                                          # ký hiệu: Argos đổi Δ thành ←, → thành "(10)"
    rf"|{_NUM}(?:\s*[–\-−]\s*{_NUM})?(?:\s*%|\s+{_UNIT}|\s*{_SUPER_UNIT})?"  # số/khoảng số (+ %/đơn vị)
    rf"|\b{_SUPER_UNIT}"                                      # m³ đứng riêng ("18 nghìn tỷ m³")
    rf"|\b{_UNIT}"                                           # đơn vị đứng riêng: EUR/tCO2, Bcf/d
    r"|\b[A-Z][a-z]{2}-\d{2}\b"                              # kỳ hạn hợp đồng: Cal-27, Nov-26
    r"|\b[A-Z][A-Za-z0-9]*[A-Z0-9](?:['’]\d+)?\b"           # mã/viết tắt: EUA, TTF, CO2, Dec'26
)
_PLACEHOLDER_RE = re.compile(r"\[(\d+)\]")
# Kiểu placeholder gửi máy dịch, thử theo thứ tự: (định dạng, regex nhận lại).
PLACEHOLDER_STYLES = (
    ("X{}X", re.compile(r"X(\d+)X")),
    ("[{}]", _PLACEHOLDER_RE),
)


# Đơn vị tiếng Việt sau "/" → tiếng Anh (token đơn vị được che nên không qua máy dịch).
UNIT_WORDS_EN = {"tấn": "t", "thùng": "bbl", "ngày": "day", "năm": "year", "tháng": "month", "giờ": "h"}
# Từ chỉ độ lớn/đơn vị đứng sau 1 số — dấu hiệu "120.900" là dấu NGHÌN kiểu Việt (= 120,900).
_MAGNITUDE_AFTER_RE = re.compile(
    r"\s*(?:tỷ|triệu|nghìn|ngàn|đồng|thùng|tấn|người|ha|MW|GW|kWh|USD|EUR|VND|[A-Za-z]{1,3}[²³])(?!\w)"
)
_THOUSANDS_DOT_RE = re.compile(r"^[~≈+\-−]?\d{1,3}(?:\.\d{3})+$")


def _en_number(num: str, thousands_dot: bool) -> str:
    """Số kiểu Việt → kiểu Anh: "79,5" → "79.5"; "120.900" (dấu nghìn) → "120,900"."""
    if thousands_dot:
        return re.sub(r"(?<=\d)([.,])(?=\d)", lambda m: "," if m.group(1) == "." else ".", num)
    return re.sub(r"(?<=\d),(?=\d)", ".", num)


def _is_thousands_dot(num: str, segment: str, after: str) -> bool:
    """`num` dùng "." làm dấu nghìn? — đúng dạng nhóm 3 chữ số VÀ (đoạn dùng dấu phẩy thập phân
    kiểu Việt, hoặc ngay sau là từ chỉ độ lớn như "tỷ", "thùng"...). "81.4400", "88.05" không bị đổi."""
    core = num.split()[0] if num.split() else num
    return bool(_THOUSANDS_DOT_RE.match(core)) and (
        bool(re.search(r"\d,\d", segment)) or bool(_MAGNITUDE_AFTER_RE.match(after))
    )


def _en_token(token: str, segment: str, after: str) -> str:
    """Dạng tiếng Anh của 1 token đã che: thuật ngữ TERMS, số (đổi dấu thập phân/nghìn), đơn vị."""
    term = TERMS.get(token.lower())
    if term is not None:
        return term
    out = _NUMBER_RE.sub(  # ngữ cảnh sau số = phần còn lại của token (vd " m²") + văn bản phía sau
        lambda m: _en_number(m.group(0), _is_thousands_dot(m.group(0), segment, m.string[m.end():] + after)), token
    ) if re.search(r"\d", token) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d+/\d+(?:/\d+)?", token) else token
    if "/" in out:
        head, _, unit = out.rpartition("/")
        out = f"{head}/{UNIT_WORDS_EN.get(unit.lower(), unit)}"
    return out


def mask_segment(text: str) -> tuple:
    """(văn bản có placeholder, token gốc, dạng tiếng Anh của token) — cùng thứ tự placeholder."""
    tokens: List[str] = []
    en: List[str] = []

    def sub(m: "re.Match[str]") -> str:
        tokens.append(m.group(0))
        # Ngữ cảnh phía sau lấy từ văn bản GỐC (đoạn đang che đã thay "tỷ đồng"... bằng placeholder).
        pos = text.find(m.group(0))
        en.append(_en_token(m.group(0), text, text[pos + len(m.group(0)):] if pos >= 0 else ""))
        return f"[{len(tokens) - 1}]"

    masked = _TERMS_RE.sub(sub, text)
    # Che số/mã ở phần còn lại, không đụng vào placeholder vừa tạo.
    pieces = re.split(r"(\[\d+\])", masked)
    masked = "".join(p if _PLACEHOLDER_RE.fullmatch(p) else _MASK_RE.sub(sub, p) for p in pieces)
    return masked, tokens, en


def _styled(masked: str, fmt: str) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: fmt.format(m.group(1)), masked)


def unmask_segment(translated: str, en_tokens: Sequence[str], pattern: "re.Pattern[str]" = _PLACEHOLDER_RE) -> Optional[str]:
    """Trả token (dạng tiếng Anh) về chỗ placeholder; None nếu bản dịch làm mất/nhân đôi/bịa
    placeholder, hoặc BỊA THÊM chữ số (thử thật: "USD/tấn" → "USD/203") — văn bản đã che không
    còn chữ số nào ngoài số thứ tự placeholder."""
    found = [int(i) for i in pattern.findall(translated)]
    if sorted(found) != list(range(len(en_tokens))) or re.search(r"\d", pattern.sub("", translated)):
        return None

    def restore(m: "re.Match[str]") -> str:
        out = en_tokens[int(m.group(1))]
        return out[:1].upper() + out[1:] if m.start() == 0 else out  # thuật ngữ đứng đầu câu

    return pattern.sub(restore, translated)


# Đọc placeholder kiểu CHỊU LỖI: mọi dãy số trong bản dịch chính là placeholder dù Argos làm méo
# dấu bao quanh (thử thật: "[0][1]]", "([4].", "X0X1X").
_LOOSE_PATTERNS = {
    "[{}]": re.compile(r"\[?\s*(\d+)\s*\]?"),
    "X{}X": re.compile(r"X?(\d+)X?"),
}


def _cleanup_markers(out: Optional[str], fmt: str, source: str) -> Optional[str]:
    """Dọn dấu placeholder thừa sau khi trả token. "]"/"[" thừa thường là ")"/"(" bị Argos đổi;
    còn sót chữ "X" đứng riêng (không có trong bản gốc) thì coi như hỏng."""
    if out is None:
        return None
    if fmt == "[{}]" and "[" not in source and "]" not in source:
        if out.count("(") > out.count(")"):
            out = out.replace("]", ")")
        if out.count(")") > out.count("("):
            out = out.replace("[", "(")
        out = out.replace("[", "").replace("]", "")
    if fmt == "X{}X" and re.search(r"\bX\b", out) and not re.search(r"\bX\b", source):
        return None
    return re.sub(r"[ \t]{2,}", " ", out).strip()


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;!?])\s+(?=\S)")
# Tách vế trong 1 câu (dùng khi cả câu vẫn dịch hỏng): sau dấu phẩy/hai chấm, hoặc trước ngoặc mở.
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[,:])\s+(?=\S)|\s+(?=[(→])")
# Có cả dấu +/− để bước sửa số trả lại dấu âm Argos làm rơi ("-0,3%" → "0.3%").
_NUMBER_RE = re.compile(r"[+\-−]?\d+(?:[.,]\d+)*")
_DIGITS_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _digit_signature(text: str) -> Counter:
    """Tập con số bỏ dấu phân cách — so số trong bản dịch với bản gốc bất kể kiểu viết 1.5/1,5."""
    return Counter(re.sub(r"[.,]", "", n) for n in _DIGITS_RE.findall(text))


def _raw_output_ok(source: str, translated: str, tokens: Sequence[str], en_tokens: Sequence[str]) -> bool:
    """Bản dịch KHÔNG che chỉ được nhận khi: không có thuật ngữ TERMS (Argos dịch sai các cụm đó),
    mọi mã/đơn vị còn nguyên, và tập con số y hệt bản gốc (không mất, không bịa số)."""
    if not translated or any(t.lower() in TERMS for t in tokens):
        return False
    if _digit_signature(translated) != _digit_signature(source):
        return False
    return all(e in translated or t in translated for t, e in zip(tokens, en_tokens) if not re.search(r"\d", t))


def _repair_raw(source: str, translated: str, tokens: Sequence[str]) -> Optional[str]:
    """Bước cuối cho 1 vế ngắn: bản dịch nguyên văn có ĐÚNG số lượng con số như bản gốc → ghi đè
    từng con số theo thứ tự bằng số gốc (Argos hay đổi giá trị nhưng giữ vị trí số). Mã viết hoa
    (EUA, TTF...) phải còn nguyên; có thuật ngữ TERMS thì không nhận (Argos dịch sai các cụm đó)."""
    if not translated or any(t.lower() in TERMS for t in tokens):
        return None
    src = list(_NUMBER_RE.finditer(source))
    if len(src) != len(_NUMBER_RE.findall(translated)):
        return None
    codes = [t for t in tokens if re.search(r"[A-Za-z]", t) and not re.search(r"\d", t) and "/" not in t]
    if any(c not in translated for c in codes):
        return None
    it = iter(_en_number(m.group(0), _is_thousands_dot(m.group(0), source, source[m.end():])) for m in src)
    out = _NUMBER_RE.sub(lambda _m: next(it), translated)
    return re.sub(r"[+\-−]\s+(?=[+\-−]\d)", "", out)  # "Day - -1.49%" → "Day -1.49%"


async def _translate_units(
    units: Sequence[str], translator: Translator, *, allow_repair: bool = False
) -> Dict[str, str]:
    """Dịch các đơn vị (đoạn/câu/vế) theo thứ tự chiến lược, đơn vị nào xong thì thôi:
    1-2. che số/mã bằng placeholder kiểu "X0X" rồi "[0]" (đọc lại kiểu chịu lỗi);
    3. gửi nguyên văn, chỉ nhận khi số/mã còn y nguyên (Argos hay trả rác cho đoạn NGẮN có
       placeholder — thử thật: "Lịch sự kiện [0] ngày tới" → "Organisation");
    4. (`allow_repair`, chỉ cho vế ngắn) bản nguyên văn + ghi đè số theo thứ tự — `_repair_raw`.
    Đơn vị không dịch được thì không có trong kết quả."""
    units = list(dict.fromkeys(u for u in units if u.strip()))
    masked = {u: mask_segment(u) for u in units}
    done: Dict[str, str] = {}
    pending = units
    for fmt, pattern in PLACEHOLDER_STYLES:
        if not pending:
            break
        texts = list(dict.fromkeys(_styled(masked[u][0], fmt) for u in pending))
        by_text = dict(zip(texts, await translator.translate_batch(texts)))
        still: List[str] = []
        for u in pending:
            m, _tokens, en = masked[u]
            raw = by_text.get(_styled(m, fmt), "").strip()
            out = _cleanup_markers(unmask_segment(raw, en, pattern), fmt, u) or _cleanup_markers(
                unmask_segment(raw, en, _LOOSE_PATTERNS[fmt]), fmt, u
            )
            if out:
                done[u] = out
            else:
                still.append(u)
        pending = still
    if pending:
        raw_out = dict(zip(pending, await translator.translate_batch(pending)))
        for u in pending:
            out, (_m, tokens, en) = raw_out[u].strip(), masked[u]
            if _raw_output_ok(u, out, tokens, en):
                done[u] = out
            elif allow_repair:
                repaired = _repair_raw(u, out, tokens)
                if repaired:
                    done[u] = repaired
    return done


def _split(text: str, pattern: "re.Pattern[str]") -> List[str]:
    return [p for p in pattern.split(text) if p.strip()]


async def translate_segments(segments: Sequence[str], translator: Translator) -> Dict[str, str]:
    """Dịch nhiều tầng — đoạn → câu → vế; chỉ phần nhỏ nhất vẫn hỏng mới giữ tiếng Việt, không
    để 1 câu hỏng kéo cả đoạn về tiếng Việt. Trả {đoạn gốc: bản dịch} cho mọi đoạn."""
    unique = list(dict.fromkeys(segments))
    result = await _translate_units(unique, translator)

    rest = [seg for seg in unique if seg not in result]
    sentences = {seg: _split(seg, _SENTENCE_SPLIT_RE) for seg in rest}
    sent_done = await _translate_units(
        [p for parts in sentences.values() if len(parts) > 1 for p in parts], translator
    )
    bad_sentences = [p for parts in sentences.values() for p in parts if p not in sent_done]
    clauses = {p: _split(p, _CLAUSE_SPLIT_RE) for p in dict.fromkeys(bad_sentences)}
    clause_done = await _translate_units(
        [c for parts in clauses.values() for c in parts], translator, allow_repair=True
    )

    left: List[str] = []
    for seg, parts in sentences.items():
        out_parts = []
        for p in parts:
            if p in sent_done:
                out_parts.append(sent_done[p])
                continue
            for c in clauses[p]:
                if c in clause_done:
                    out_parts.append(clause_done[c])
                else:
                    left.append(c)
                    out_parts.append(c)
        result[seg] = " ".join(out_parts)
    if left:
        logger.warning(
            "[TRANSLATE] %d vế câu giữ nguyên tiếng Việt (máy dịch làm hỏng số/mã):\n  %s",
            len(left), "\n  ".join(left),
        )
    return result


# ── Dịch bằng LLM (Claude) ──────────────────────────────────────────────────────────────
# Claude tự dịch nguyên từng chuỗi (lô theo thứ tự tài liệu → có ngữ cảnh), kết quả nhận nguyên văn — không
# bảng dịch cứng, không kiểm tra/sửa lại. Chỉ gửi lại các đoạn Claude không trả về (lô lỗi JSON/bị cắt).
async def translate_strings_llm(strings: Sequence[str], translator: Translator) -> Dict[str, str]:
    unique = list(dict.fromkeys(strings))
    done: Dict[str, str] = {}
    pending = unique
    for _attempt in range(2):
        if not pending:
            break
        outs = await translator.translate_batch(pending)
        for s, out in zip(pending, outs):
            if out and out.strip() and out != s:
                done[s] = out.strip("\n")
        pending = [s for s in pending if s not in done]
    if pending:
        logger.warning("[TRANSLATE] %d/%d đoạn Claude không trả bản dịch — giữ tiếng Việt.", len(pending), len(unique))
    return done


def _protected_llm(key: Optional[str], value: str) -> bool:
    """Luồng Claude: chỉ giữ tiếng Việt giá trị enum/định danh mà code so sánh (frontend tự hiện nhãn) và chuỗi
    lính gác; mọi chữ hiển thị khác — tiêu đề mục, nhãn chủ đề ("topics"), tiêu đề khối... — đều do Claude dịch."""
    return (key in PROTECTED_KEYS and key != "topics") or (key == "heading" and value in ENUM_HEADINGS) \
        or value in KEEP_VERBATIM


def _collect_llm(node: Any, key: Optional[str], out: List[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k not in UNTRANSLATED_SUBTREES:
                _collect_llm(v, k, out)
    elif isinstance(node, list):
        for v in node:
            _collect_llm(v, key, out)
    elif isinstance(node, str) and not _protected_llm(key, node) and _has_vietnamese(node):
        out.append(node)


def _replace_llm(node: Any, key: Optional[str], mt: Dict[str, str]) -> Any:
    if isinstance(node, dict):
        return {k: (v if k in UNTRANSLATED_SUBTREES else _replace_llm(v, k, mt)) for k, v in node.items()}
    if isinstance(node, list):
        return [_replace_llm(v, key, mt) for v in node]
    if isinstance(node, str) and not _protected_llm(key, node):
        return mt.get(node, node)
    return node


async def translate_report_content(content: dict, translator: Translator) -> dict:
    """Bản dịch tiếng Anh của `content` (cùng cấu trúc). Raise TranslationError nếu máy dịch lỗi.
    Máy dịch LLM (`whole_strings`): mọi chuỗi hiển thị do LLM tự dịch nguyên vẹn, nhận nguyên văn.
    Máy dịch thường (LibreTranslate): cắt quanh neo + bảng thuật ngữ cố định + che số."""
    if translator.whole_strings:
        strings: List[str] = []
        _collect_llm(content, None, strings)
        return _replace_llm(content, None, await translate_strings_llm(strings, translator))
    segments: List[str] = []
    _collect(content, None, segments)
    out = _rebuild(content, None, await translate_segments(segments, translator))
    left: List[str] = []
    _collect_whole(out, None, left)
    if left:
        logger.warning("[TRANSLATE] Bản tiếng Anh còn %d chuỗi có chữ Việt:\n  %s",
                       len(left), "\n  ".join(t[:160] for t in left))
    return out


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
