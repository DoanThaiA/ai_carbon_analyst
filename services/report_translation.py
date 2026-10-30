"""Bản tiếng Anh của báo cáo ngày — dịch bằng Claude `reports.content` → `reports.content_en`.

MỌI chữ hiển thị (tiêu đề mục, tiêu đề khối, nhãn chủ đề, văn xuôi...) do Claude TỰ dịch nguyên chuỗi theo lô
có ngữ cảnh và được nhận NGUYÊN VĂN (`translate_strings_llm`) — không bảng dịch cứng, không kiểm tra/dịch lại.
Prompt chỉ dặn vài neo frontend nhận diện (**Summary:**, [POSITIVE], **Target:**...) — sửa regex ở
`ReportDocument.tsx` thì sửa cả prompt. `REPORT_TRANSLATION_PROVIDER=off` = tắt, báo cáo chỉ có tiếng Việt.

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

import anthropic
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

# Chuỗi "lính gác" mà frontend/backend so khớp nguyên văn — không dịch.
KEEP_VERBATIM = frozenset({
    "Chưa có thông tin kết quả xác nhận",
})

_VI_CHARS_RE = re.compile(
    "[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.IGNORECASE
)

TRANSLATE_MAX_ATTEMPTS = 3


class TranslationError(RuntimeError):
    pass


class Translator(ABC):
    @abstractmethod
    async def translate_batch(self, texts: Sequence[str]) -> List[str]:
        """Dịch vi→en, trả về đúng số phần tử và đúng thứ tự của `texts`."""


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
"Energy & fossil fuels:" / "Allowances & carbon credits:" / "Policy:". The section "Diễn biến chính" is called \
"Key Market Insights"."""


class ClaudeTranslator(Translator):
    """Dịch bằng Claude — lô theo thứ tự tài liệu (có ngữ cảnh), JSON có cấu trúc, chạy song song."""

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
    if settings.report_translation_provider == "claude" and settings.anthropic_api_key:
        return ClaudeTranslator(settings.anthropic_api_key, settings.report_translation_model)
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
    """Chỉ giữ tiếng Việt giá trị enum/định danh mà code so sánh (frontend tự hiện nhãn) và chuỗi lính gác;
    mọi chữ hiển thị khác — tiêu đề mục, nhãn chủ đề ("topics"), tiêu đề khối... — đều do Claude dịch."""
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
    """Bản dịch tiếng Anh của `content` (cùng cấu trúc). Raise TranslationError nếu Claude lỗi."""
    strings: List[str] = []
    _collect_llm(content, None, strings)
    return _replace_llm(content, None, await translate_strings_llm(strings, translator))


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
