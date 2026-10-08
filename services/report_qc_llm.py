"""Lucy QC — Đợt 2: 2 check bằng LLM, bổ sung cho 5 check rule-based ở services/report_qc.py.

    check        nội dung
    consistency  Nhất quán nội bộ: Mục 1 tóm tắt ↔ Mục 2 bullish/bearish ↔ Mục 3 Tổng hợp/kịch bản
                 ↔ gợi ý kinh doanh; số liệu nhắc trong văn bản ↔ bảng giá.
    causal       Chuỗi nhân quả EUA: suy luận ở Mục 2/3 có đi NGƯỢC khung
                 services/eua_causal_chains.py (đúng bản admin đã custom) không.

Mỗi check = 1 lần gọi Claude (2 check chạy song song), trả JSON theo structured
outputs (`output_config.format`) nên không cần parse lenient như report_generator.
Khung nhân quả (~40K ký tự) nằm trong system prompt có cache_control — giống
nhau giữa các lần QC (chỉ đổi khi admin sửa khung) nên lần QC sau đọc từ cache.

Lỗi LLM (API lỗi, refusal, JSON hỏng) KHÔNG làm hỏng cả lần QC: raise
LLMCheckError, run_report_qc() bỏ điểm check đó (None) và ghi 1 note "info".
"""
import json
import logging
from typing import Any, Dict, List, Optional

import anthropic

from services import eua_causal_chains as chains
from services.report_generator import _eua_framework, _get_anthropic_client, _get_settings

logger = logging.getLogger(__name__)

LLM_CHECKS = ("consistency", "causal")
LLM_CHECK_LABELS = {"consistency": "Nhất quán nội bộ", "causal": "Chuỗi nhân quả EUA"}
LLM_SECTIONS = ("1", "2", "3", "4", "biz")
LLM_MAX_TOKENS = 16000
LLM_TIMEOUT_SECONDS = 300.0
# Server-side fallback khi model từ chối (refusal) — chỉ các model hỗ trợ `fallbacks: "default"`.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string", "enum": list(LLM_SECTIONS)},
                    "severity": {"type": "string", "enum": ["error", "warning", "info"]},
                    "message": {"type": "string"},
                    "field_path": {"type": "string"},
                },
                "required": ["section", "severity", "message", "field_path"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["issues"],
    "additionalProperties": False,
}


class LLMCheckError(Exception):
    pass


# ── Dựng nội dung báo cáo cho prompt ─────────────────────────────────


def _text(v: Any) -> str:
    return v.get("text", "") if isinstance(v, dict) else str(v or "")


def report_digest(content: Dict[str, Any]) -> str:
    """Bản rút gọn các mục cần đối chiếu, mỗi dòng gắn field_path để LLM trích dẫn đúng vị trí.
    Bỏ chart_data, Mục 6 (tóm tắt từng bài), Mục 8, Mục 9 — không liên quan 2 check này."""
    lines: List[str] = []
    sec2 = content.get("2") or {}

    lines.append("### BẢNG GIÁ NHANH (Mục 2 — số liệu chuẩn, đã đối chiếu DB)")
    if sec2.get("price_timestamp"):
        lines.append(sec2["price_timestamp"])
    for i, p in enumerate(sec2.get("prices") or []):
        if isinstance(p, dict):
            lines.append(f"[2.prices[{i}]] {p.get('name')} ({p.get('code')}): {p.get('price')} | Δ ngày {p.get('dday')} | Δ tuần {p.get('dweek')}")
    for i, f in enumerate(sec2.get("key_facts") or []):
        lines.append(f"[2.key_facts[{i}]] {_text(f)}")

    lines.append("\n### MỤC 1 — TÓM TẮT ĐIỀU HÀNH")
    for i, b in enumerate((content.get("1") or {}).get("bullets") or []):
        lines.append(f"[1.bullets[{i}]] {_text(b)}")

    drivers = sec2.get("market_drivers") or {}
    for side, label in (("bullish", "YẾU TỐ HỖ TRỢ GIÁ EUA (bullish)"), ("bearish", "YẾU TỐ GÂY ÁP LỰC GIÁ EUA (bearish)")):
        lines.append(f"\n### MỤC 2 — {label}")
        for i, d in enumerate(drivers.get(side) or []):
            tag = d.get("tag") if isinstance(d, dict) else None
            lines.append(f"[2.market_drivers.{side}[{i}]] {f'[{tag}] ' if tag else ''}{_text(d)}")

    lines.append("\n### MỤC 2 — DIỄN BIẾN CHÍNH (tin tức + chiều tác động lên EUA)")
    for i, d in enumerate(sec2.get("key_developments") or []):
        impact = d.get("impact") if isinstance(d, dict) else None
        lines.append(f"[2.key_developments[{i}]] (tác động: {impact or '?'}) {_text(d)}")
    for i, b in enumerate((content.get("4") or {}).get("bullets") or []):
        lines.append(f"[4.bullets[{i}]] {_text(b)}")

    sec3 = content.get("3") or {}
    lines.append("\n### MỤC 3 — PHÂN TÍCH (dòng \"**Tổng hợp:**\" là kết luận chiều giá EUA của cả báo cáo)")
    for i, blk in enumerate(sec3.get("analysis_blocks") or []):
        if isinstance(blk, dict):
            lines.append(f"[3.analysis_blocks[{i}]] {blk.get('heading') or ''}\n{blk.get('content') or ''}")
    corr = sec3.get("correlation_analysis")
    if isinstance(corr, dict):
        for k, v in corr.items():
            if v:
                lines.append(f"[3.correlation_analysis.{k}] {v}")

    lines.append("\n### MỤC 3 — KỊCH BẢN GIAO DỊCH")
    for i, sc in enumerate(sec3.get("trading_scenarios") or []):
        if isinstance(sc, dict):
            fields = " | ".join(f"{k}: {v}" for k, v in sc.items() if v)
            lines.append(f"[3.trading_scenarios[{i}]] {fields}")

    biz = content.get("biz") or {}
    lines.append("\n### GỢI Ý KINH DOANH")
    for i, it in enumerate(biz.get("short_term") or []):
        if isinstance(it, dict):
            lines.append(f"[biz.short_term[{i}]] Nếu: {it.get('trigger')} → Hành động: {it.get('action')} (Lý do: {it.get('reason')})")
    for i, it in enumerate(biz.get("long_term") or []):
        if isinstance(it, dict):
            lines.append(f"[biz.long_term[{i}]] {it.get('opportunity')} → {it.get('solution')}")
    return "\n".join(lines)


# ── Prompt ───────────────────────────────────────────────────────────

_COMMON_RULES = """QUY TẮC BÁO LỖI:
- CHỈ báo vấn đề CỤ THỂ, kiểm chứng được bằng chính nội dung báo cáo (và khung phân tích nếu có). Mỗi lỗi phải trích ngắn 2 vế mâu thuẫn hoặc câu suy luận sai, kèm field_path đúng như nhãn [..] đứng đầu dòng trong báo cáo.
- KHÔNG góp ý văn phong, độ dài, cách trình bày, thiếu chi tiết; KHÔNG đề xuất thêm nội dung; KHÔNG suy đoán sự thật thị trường bên ngoài báo cáo.
- severity: "error" = mâu thuẫn/sai trực tiếp người đọc sẽ hiểu sai chiều giá hoặc hành động; "warning" = lệch đáng kể cần admin xem lại; "info" = điểm nhỏ, không ảnh hưởng kết luận.
- section: mục chứa câu SAI cần sửa ("1", "2", "3", "4" hoặc "biz") — nếu 2 mục mâu thuẫn nhau, chọn mục KHÔNG phải nguồn số liệu chuẩn (bảng giá luôn đúng).
- message: tiếng Việt, tối đa 2 câu, nêu rõ vị trí (vd "Bullet 2", "Kịch bản ngắn hạn"), không dùng tên biến nội bộ.
- Không có lỗi → trả "issues": []. Báo cáo tốt thì danh sách rỗng là kết quả đúng, đừng cố tìm lỗi."""

CONSISTENCY_SYSTEM = f"""Bạn là Lucy — biên tập viên QC báo cáo thị trường carbon (EUA) hằng ngày, đọc bản nháp trước khi xuất bản để bắt lỗi NHẤT QUÁN NỘI BỘ.

NHIỆM VỤ: đối chiếu chéo các mục của báo cáo, tìm chỗ các mục nói NGƯỢC nhau:
1. Mục 1 (Tóm tắt điều hành) ↔ dòng "**Tổng hợp:**" Mục 3: chiều giá EUA, nhận định chính phải cùng hướng.
2. Mục 2 yếu tố bullish/bearish ↔ nhãn Tổng hợp [TÍCH CỰC]/[TRUNG LẬP]/[TIÊU CỰC]: kết luận ngược hẳn cán cân yếu tố mà không giải thích là lỗi; một yếu tố xuất hiện ở CẢ bullish lẫn bearish với cùng lập luận là lỗi.
3. Diễn biến chính (chiều tác động từng tin) ↔ cách Mục 3 dùng chính tin đó.
4. Kịch bản giao dịch: "direction" ↔ "trading_strategy" (Entry mua khi direction "giảm" là lỗi); price_zone/Entry/Target/Stop đúng thứ tự logic theo chiều; kịch bản ngắn hạn ↔ Tổng hợp.
5. Gợi ý kinh doanh ↔ quan điểm thị trường (hành động đi ngược kết luận mà không nêu điều kiện).
6. Số liệu nhắc trong văn bản (giá, % thay đổi, chiều tăng/giảm của một mã) ↔ BẢNG GIÁ NHANH. Bảng giá là chuẩn; chỉ báo khi văn bản nói về CÙNG phiên/cùng mã mà lệch rõ (mốc hỗ trợ/kháng cự/mục tiêu không phải giá phiên — không so).

{_COMMON_RULES}"""


def causal_system(framework: str) -> str:
    # Khung đặt TRƯỚC phần nhiệm vụ: phần dài + ổn định nằm đầu prefix để cache hiệu quả.
    return f"""Bạn là Lucy — chuyên gia QC phân tích giá carbon EU ETS (EUA). Dưới đây là KHUNG PHÂN TÍCH CHUẨN mà mọi suy luận về giá EUA trong báo cáo BẮT BUỘC phải tuân theo.

{framework}

NHIỆM VỤ: kiểm tra từng chuỗi suy luận nhân quả trong báo cáo (Mục 2 yếu tố bullish/bearish và diễn biến chính, Mục 3 phân tích/correlation/kịch bản, Mục 1 nếu có nêu cơ chế) so với khung trên. Báo lỗi khi:
1. Chiều tác động NGƯỢC khung (vd suy ra EUA giảm từ một cơ chế mà khung nói làm EUA tăng) → "error".
2. Áp cơ chế ra ngoài phạm vi khung: dùng thị trường không fungible với EUA (VCM, China ETS, CORSIA...) hoặc ETS2 (đường bộ, toà nhà) để kết luận cung/cầu EUA; dùng cơ chế dài hạn để giải thích biến động ngày/tuần → "warning".
3. Vi phạm luật suy luận/phân xử của khung: kết luận chiều giá dứt khoát khi chỉ có 1 yếu tố hoặc các yếu tố trái chiều mà không phân xử; đếm trùng cùng một nguyên nhân qua 2 kênh; bỏ qua điều kiện bác bỏ mà chính báo cáo đã nêu → "warning".
4. Chuỗi thiếu mắt xích nối tới cung/cầu EUA nhưng vẫn kết luận chiều giá → "info" (hoặc "warning" nếu đó là luận điểm chính của Tổng hợp).
KHÔNG báo lỗi chỉ vì báo cáo không nhắc tới một cơ chế; KHÔNG đánh giá dự báo đúng/sai so với thị trường thật.

{_COMMON_RULES}"""


USER_TEMPLATE = """Báo cáo ngày {report_date} (bản nháp):

{digest}

Trả về danh sách lỗi theo đúng schema."""


# ── Gọi Claude ───────────────────────────────────────────────────────


async def run_llm_check(check: str, system: str, user: str) -> List[Dict[str, str]]:
    model = _get_settings().report_qc_model
    kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": LLM_MAX_TOKENS,
        "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
    }
    # 1 lần retry (429/5xx/mất kết nối) — tối đa ~2×timeout, nằm trong QC_STALE_AFTER của router.
    client = _get_anthropic_client().with_options(timeout=LLM_TIMEOUT_SECONDS, max_retries=1)
    try:
        if model in FALLBACK_MODELS:
            response = await client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **kwargs)
        else:
            response = await client.messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        # Chi tiết đầy đủ (request_id...) chỉ ghi log; note hiển thị cho admin giữ ngắn.
        logger.error("[REPORT-QC-LLM] %s: %s", check, e)
        raise LLMCheckError(f"API lỗi {e.status_code}") from e
    except anthropic.APIConnectionError as e:
        logger.error("[REPORT-QC-LLM] %s: %s", check, e)
        raise LLMCheckError("không kết nối được Anthropic") from e

    if response.stop_reason == "refusal":
        raise LLMCheckError("Model từ chối xử lý (refusal).")
    if response.stop_reason == "max_tokens":
        raise LLMCheckError(f"Output bị cắt do đạt max_tokens={LLM_MAX_TOKENS}.")
    raw = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMCheckError(f"JSON trả về không hợp lệ: {e}") from e

    usage = response.usage
    logger.info(
        "[REPORT-QC-LLM] %s (%s): in=%s cache_read=%s cache_write=%s out=%s",
        check, response.model, usage.input_tokens, getattr(usage, "cache_read_input_tokens", None),
        getattr(usage, "cache_creation_input_tokens", None), usage.output_tokens,
    )
    return normalize_issues(check, parsed.get("issues"))


def normalize_issues(check: str, raw: Optional[List[Any]]) -> List[Dict[str, str]]:
    """Gắn tên check + lọc phần tử sai định dạng (phòng khi model/khung đổi schema)."""
    issues = []
    for it in raw or []:
        if not isinstance(it, dict) or it.get("section") not in LLM_SECTIONS:
            continue
        if it.get("severity") not in ("error", "warning", "info") or not str(it.get("message") or "").strip():
            continue
        issues.append({
            "check": check,
            "section": it["section"],
            "severity": it["severity"],
            "message": it["message"].strip(),
            "field_path": str(it.get("field_path") or it["section"]),
        })
    return issues


def build_prompts(content: Dict[str, Any], report_date: str, overrides: Optional[Dict[str, str]]) -> Dict[str, tuple]:
    """-> {check: (system, user)}. Khung nhân quả nạp ĐỦ mọi topic (không chỉ topic có tin
    trong ngày như lúc sinh báo cáo) — QC cần đối chiếu được mọi cơ chế mà báo cáo có thể đã viện dẫn."""
    user = USER_TEMPLATE.format(report_date=report_date, digest=report_digest(content))
    framework = _eua_framework(list(chains.MECHANISM_REGISTRY), full=True, overrides=overrides)
    return {
        "consistency": (CONSISTENCY_SYSTEM, user),
        "causal": (causal_system(framework), user),
    }
