"""Biên bản QC báo cáo do Claude (vai Jack) chấm qua MCP — bảng `report_qc_reviews` + `report_qc_findings`.

Luồng tự động mỗi ngày (giờ VN): 07:00 sinh bản nháp (scheduler `auto_report_job`) → 07:30 lịch Claude (routine,
token `scope='qc_agent'`) gọi `get_qc_assignment` đọc bản nháp + kết quả Jack rule-based, chấm theo rubric chuẩn
`QC_CRITERIA`, rồi `submit_qc_review` gửi biên bản → 08:00 `auto_qc_fix_job` Jenny tự sửa theo biên bản
(`report_qc_fix.auto_fix_from_review`, bỏ qua nếu admin đã sửa sau lần QC) → 08:15 `auto_publish_job` tự xuất bản,
CHỈ chặn khi biên bản mới nhất `verdict='fail'` mà còn lỗi critical/major chưa xử lý hoặc điểm sau sửa < FAIL_SCORE (`gate_decision`; không có biên
bản QC vẫn xuất bản — quyết định C2).

Server KHÔNG tin điểm Claude tự cộng: `overall_score` tính lại từ điểm từng tiêu chí × trọng số, `verdict` chuẩn hóa
theo `decide_verdict` (Claude đề xuất được lưu riêng ở `claude_verdict`). Biên bản phải kèm `assignment_id` = hash nội
dung lúc Claude đọc — báo cáo bị sửa giữa chừng thì từ chối, buộc Claude đọc lại. Toàn bộ phần chuẩn hóa là hàm thuần
(test: tests/test_report_qc_review.py). Chỉ admin xem được kết quả (api/routers/admin_qc_reviews.py, tool Jenny
`get_qc_review` trong ADMIN_TOOLS).
"""
import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ApiToken, Report, ReportQcFinding, ReportQcReview

logger = logging.getLogger(__name__)

# Rubric chuẩn — thứ tự = thứ tự hiển thị. Trọng số cộng = 100. Điểm mỗi tiêu chí thang 0–10.
QC_CRITERIA: List[Dict[str, Any]] = [
    {"key": "data_accuracy", "label": "Độ chính xác số liệu", "weight": 20,
     "guide": "Giá/%, Δ, khối lượng khớp bảng giá hệ thống; số trích từ tin khớp bài gốc; phép tính đúng."},
    {"key": "analysis_logic", "label": "Logic phân tích", "weight": 25,
     "guide": "Chiều tác động lên EUA đúng cơ chế (fuel switching, MSR, cung/cầu), không mâu thuẫn giữa các mục, "
              "kết luận có cơ sở dữ liệu."},
    {"key": "coverage", "label": "Độ phủ & cập nhật tin", "weight": 15,
     "guide": "Đủ tin quan trọng trong khung tin; không tin cũ/trùng; chính sách ETS/MSR, CBAM được theo dõi."},
    {"key": "transparency", "label": "Minh bạch nguồn & giả định", "weight": 10,
     "guide": "Nguồn trích đúng bài, đúng nhà xuất bản; giả định mô hình ghi rõ; phân biệt số liệu và suy luận."},
    {"key": "trading_hedge", "label": "Kịch bản giao dịch & phòng hộ", "weight": 15,
     "guide": "Entry/mục tiêu/cắt lỗ hợp lệ (R:R, ATR), nhất quán nhãn Tổng hợp; phòng hộ CBAM đúng cấu trúc."},
    {"key": "biz", "label": "Gợi ý kinh doanh (SIM)", "weight": 10,
     "guide": "Bám lăng kính kim loại/CBAM của SIM, có nguồn, có KPI/người phụ trách, định lượng hợp lý."},
    {"key": "presentation", "label": "Trình bày & ngôn ngữ", "weight": 5,
     "guide": "Ngắn gọn, đúng định dạng số VN, không lặp ý, không lỗi chính tả/thuật ngữ."},
]
CRITERIA_KEYS = tuple(c["key"] for c in QC_CRITERIA)
_CRITERIA_BY_KEY = {c["key"]: c for c in QC_CRITERIA}

VERDICTS = ("pass", "pass_with_fixes", "fail")
VERDICT_LABELS = {"pass": "Đạt", "pass_with_fixes": "Đạt, cần sửa", "fail": "Không đạt"}
SEVERITIES = ("critical", "major", "minor")
SEVERITY_LABELS = {"critical": "Nghiêm trọng", "major": "Quan trọng", "minor": "Nhỏ"}
VERIFICATIONS = ("verified", "partially_verified", "unverified")
FINDING_STATUSES = ("open", "fixed", "wont_fix")
# Khoá mục báo cáo (khớp report_qc.SECTION_LABELS) + các khối riêng.
SECTIONS = ("1", "2", "3", "4", "6", "8", "9", "biz", "dev", "forecast", "cbam", "ets_policy", "general")
_SECTION_ALIASES = {
    "diễn biến chính": "dev", "dien bien chinh": "dev", "dự báo trước": "forecast", "forecast_review": "forecast",
    "cbam_estimate": "cbam", "cbam": "cbam", "gợi ý kinh doanh": "biz", "gợi ý sim": "biz", "chung": "general",
    "toàn báo cáo": "general", "chính sách ets": "ets_policy", "ets_policy": "ets_policy",
}

# Ngưỡng chuẩn hóa kết luận.
FAIL_SCORE = 6.0  # điểm tổng < ngưỡng → Không đạt
PASS_MIN_SCORE = 8.0  # "Đạt" thuần cần ≥ ngưỡng và không có lỗi critical/major
MAX_FINDINGS = 60
REPEAT_LOOKBACK_REPORTS = 5  # đếm lỗi lặp trên N báo cáo gần nhất trước đó

_TEXT_LIMITS = {
    "summary": 4000, "body_markdown": 60000, "title": 300, "current_text": 3000, "expected_text": 3000,
    "evidence": 4000, "location": 200, "field_path": 300, "comment": 1500, "reviewer": 120, "status_note": 1000,
}


class QcSubmissionError(ValueError):
    """Biên bản không hợp lệ — thông điệp liệt kê đủ lỗi để Claude sửa rồi gửi lại."""


def content_hash(content: Any) -> str:
    """Hash ổn định của reports.content — dùng làm `assignment_id` và để biết báo cáo đã sửa sau lần QC."""
    raw = json.dumps(content or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clean(value: Any, key: str) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    limit = _TEXT_LIMITS.get(key)
    return text[:limit] if limit else text


def normalize_section(value: Any) -> Optional[str]:
    """'Mục 3' / 'muc 3' / '3' → '3'; 'Diễn biến chính' → 'dev'; giá trị lạ → 'general'."""
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip().lower()
    if raw in SECTIONS:
        return raw
    if raw in _SECTION_ALIASES:
        return _SECTION_ALIASES[raw]
    m = re.match(r"^(?:mục|muc|section|phần)?\s*(\d+)\b", raw)
    if m and m.group(1) in SECTIONS:
        return m.group(1)
    for alias, key in _SECTION_ALIASES.items():
        if alias in raw:
            return key
    return "general"


def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn").replace("đ", "d")


def finding_fingerprint(criterion: str, section: Optional[str], title: str) -> str:
    """Dấu vân tay lỗi để đếm lỗi lặp giữa các báo cáo: tiêu chí + mục + từ khóa tiêu đề (bỏ dấu, bỏ số, bỏ
    từ ngắn, sắp xếp) — "Sai chiều API2 tăng 3,75%" và "API2 tăng 2,1% xếp sai chiều" ra cùng dấu."""
    words = re.findall(r"[a-z]+", _strip_accents(title.lower()))
    keys = sorted({w for w in words if len(w) >= 3})[:12]
    raw = f"{criterion}|{section or 'general'}|{' '.join(keys)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().replace(",", "."))
        except ValueError:
            return None
    return None


def compute_overall_score(criteria: List[Dict[str, Any]]) -> float:
    total_w = sum(c["weight"] for c in criteria)
    if not total_w:
        return 0.0
    return round(sum(c["score"] * c["weight"] for c in criteria) / total_w, 2)


# Trọng số chia điểm bị trừ của 1 tiêu chí cho các lỗi thuộc tiêu chí đó (xem `score_after_fixes`).
SEVERITY_DEDUCTION_WEIGHT = {"critical": 3, "major": 2, "minor": 1}


def score_after_fixes(criteria: List[Dict[str, Any]], findings: Sequence[Any]) -> Tuple[float, List[Dict[str, Any]]]:
    """Điểm ước tính sau khi sửa (hàm thuần, KHÔNG ghi đè `overall_score` gốc của biên bản): phần điểm bị trừ của mỗi
    tiêu chí (10 − điểm) chia cho các lỗi thuộc tiêu chí đó theo `SEVERITY_DEDUCTION_WEIGHT`; lỗi `fixed` được cộng
    lại phần của nó. Lỗi `wont_fix`/`open` không cộng; tiêu chí không có lỗi nào giữ nguyên điểm.
    -> (điểm tổng sau sửa, criteria kèm `score_after_fixes`)."""
    out = []
    for c in criteria:
        own = [f for f in findings if f.criterion == c["key"]]
        total_w = sum(SEVERITY_DEDUCTION_WEIGHT.get(f.severity, 1) for f in own)
        fixed_w = sum(SEVERITY_DEDUCTION_WEIGHT.get(f.severity, 1) for f in own if f.status == "fixed")
        gained = (10 - c["score"]) * fixed_w / total_w if total_w else 0.0
        out.append({**c, "score_after_fixes": round(min(10.0, c["score"] + gained), 1)})
    return compute_overall_score([{**c, "score": c["score_after_fixes"]} for c in out]), out


def decide_verdict(overall: float, counts: Dict[str, int], claude_verdict: Optional[str],
                   verified_critical: int) -> str:
    """Kết luận chuẩn hóa (nghiêm hơn hoặc bằng Claude, không bao giờ nhẹ hơn):
    - Không đạt: Claude kết luận fail, HOẶC điểm < FAIL_SCORE, HOẶC có lỗi nghiêm trọng ĐÃ KIỂM CHỨNG.
    - Đạt, cần sửa: có lỗi nghiêm trọng (chưa kiểm chứng đủ)/quan trọng, điểm < PASS_MIN_SCORE, hoặc Claude nói vậy.
    - Đạt: còn lại."""
    if claude_verdict == "fail" or overall < FAIL_SCORE or verified_critical > 0:
        return "fail"
    if (claude_verdict == "pass_with_fixes" or counts.get("critical", 0) or counts.get("major", 0)
            or overall < PASS_MIN_SCORE):
        return "pass_with_fixes"
    return "pass"


def _normalize_source_refs(value: Any) -> List[Dict[str, str]]:
    refs: List[Dict[str, str]] = []
    for item in value if isinstance(value, list) else ([value] if value else []):
        if isinstance(item, str) and item.strip():
            text = item.strip()
            refs.append({"label": text[:200], "url": text if text.startswith("http") else ""})
        elif isinstance(item, dict):
            label = str(item.get("label") or item.get("name") or item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            if label or url:
                refs.append({"label": (label or url)[:200], "url": url[:1000]})
    return refs[:10]


def normalize_submission(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Kiểm + chuẩn hóa biên bản Claude gửi. Lỗi cấu trúc → QcSubmissionError (gom đủ lỗi một lần)."""
    errors: List[str] = []
    if not isinstance(payload, dict):
        raise QcSubmissionError("Biên bản phải là object JSON.")

    # --- Tiêu chí ---
    raw_criteria = payload.get("criteria")
    by_key: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw_criteria, dict):  # chấp nhận dạng {key: {score, comment}} hoặc {key: score}
        raw_criteria = [{"key": k, **(v if isinstance(v, dict) else {"score": v})} for k, v in raw_criteria.items()]
    if not isinstance(raw_criteria, list):
        errors.append(f"`criteria` phải là danh sách đủ {len(CRITERIA_KEYS)} tiêu chí: {', '.join(CRITERIA_KEYS)}.")
        raw_criteria = []
    for item in raw_criteria:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key not in _CRITERIA_BY_KEY:
            errors.append(f"Tiêu chí lạ `{key}` — chỉ dùng: {', '.join(CRITERIA_KEYS)}.")
            continue
        score = _num(item.get("score"))
        if score is None or not 0 <= score <= 10:
            errors.append(f"Tiêu chí `{key}`: `score` phải là số 0–10.")
            continue
        comment = _clean(item.get("comment") or item.get("note"), "comment")
        if not comment:
            errors.append(f"Tiêu chí `{key}`: thiếu `comment` (lý do cho điểm).")
            continue
        by_key[key] = {"key": key, "label": _CRITERIA_BY_KEY[key]["label"],
                       "weight": _CRITERIA_BY_KEY[key]["weight"], "score": round(score, 1), "comment": comment}
    missing = [k for k in CRITERIA_KEYS if k not in by_key]
    if missing and raw_criteria:
        errors.append(f"Thiếu tiêu chí: {', '.join(missing)}.")
    criteria = [by_key[k] for k in CRITERIA_KEYS if k in by_key]

    # --- Lỗi/nhận xét ---
    raw_findings = payload.get("findings") or []
    if not isinstance(raw_findings, list):
        errors.append("`findings` phải là danh sách.")
        raw_findings = []
    if len(raw_findings) > MAX_FINDINGS:
        errors.append(f"Tối đa {MAX_FINDINGS} findings — gộp các lỗi cùng loại.")
    findings: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_findings[:MAX_FINDINGS], start=1):
        if not isinstance(item, dict):
            errors.append(f"Finding #{idx} phải là object.")
            continue
        severity = str(item.get("severity") or "").strip().lower()
        criterion = str(item.get("criterion") or "").strip()
        title = _clean(item.get("title"), "title")
        problems = []
        if severity not in SEVERITIES:
            problems.append(f"`severity` ∈ {SEVERITIES}")
        if criterion not in _CRITERIA_BY_KEY:
            problems.append(f"`criterion` ∈ {CRITERIA_KEYS}")
        if not title:
            problems.append("thiếu `title`")
        evidence = _clean(item.get("evidence"), "evidence")
        if severity in ("critical", "major") and not evidence:
            problems.append("lỗi critical/major phải có `evidence` (số đối chiếu/phép tính/trích nguồn)")
        verification = str(item.get("verification") or "unverified").strip().lower()
        if verification not in VERIFICATIONS:
            problems.append(f"`verification` ∈ {VERIFICATIONS}")
        if problems:
            errors.append(f"Finding #{idx} ({title or '?'}): " + "; ".join(problems) + ".")
            continue
        section = normalize_section(item.get("section"))
        findings.append({
            "position": len(findings) + 1,
            "severity": severity,
            "criterion": criterion,
            "section": section,
            "location": _clean(item.get("location"), "location"),
            "field_path": _clean(item.get("field_path"), "field_path"),
            "title": title,
            "current_text": _clean(item.get("current_text"), "current_text"),
            "expected_text": _clean(item.get("expected_text"), "expected_text"),
            "evidence": evidence,
            "verification": verification,
            "source_refs": _normalize_source_refs(item.get("source_refs")),
            "fingerprint": finding_fingerprint(criterion, section, title),
        })

    # --- Phần chung ---
    summary = _clean(payload.get("summary"), "summary")
    if not summary:
        errors.append("Thiếu `summary` (nhận xét tổng quan 3–6 câu).")
    claude_verdict = str(payload.get("verdict") or "").strip().lower() or None
    if claude_verdict and claude_verdict not in VERDICTS:
        errors.append(f"`verdict` ∈ {VERDICTS}.")
        claude_verdict = None
    if not claude_verdict:
        errors.append("Thiếu `verdict` (pass | pass_with_fixes | fail).")
    strengths = [s.strip()[:500] for s in (payload.get("strengths") or []) if isinstance(s, str) and s.strip()][:10]
    priorities: List[Dict[str, Any]] = []
    for i, p in enumerate(payload.get("priorities") or [], start=1):
        if isinstance(p, str) and p.strip():
            priorities.append({"rank": i, "text": p.strip()[:800], "expected_gain": None})
        elif isinstance(p, dict) and str(p.get("text") or "").strip():
            gain = _num(p.get("expected_gain"))
            priorities.append({"rank": i, "text": str(p["text"]).strip()[:800],
                               "expected_gain": round(gain, 2) if gain is not None else None})
    priorities = priorities[:10]

    if errors:
        raise QcSubmissionError("Biên bản QC chưa hợp lệ, sửa rồi gọi lại submit_qc_review:\n- " + "\n- ".join(errors))

    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}
    verified_critical = sum(1 for f in findings if f["severity"] == "critical" and f["verification"] == "verified")
    overall = compute_overall_score(criteria)
    return {
        "criteria": criteria,
        "findings": findings,
        "summary": summary,
        "strengths": strengths,
        "priorities": priorities,
        "body_markdown": _clean(payload.get("body_markdown"), "body_markdown"),
        "reviewer": _clean(payload.get("reviewer"), "reviewer"),
        "claude_verdict": claude_verdict,
        "overall_score": overall,
        "counts": counts,
        "verdict": decide_verdict(overall, counts, claude_verdict, verified_critical),
    }


# ---------------------------------------------------------------------------
# Giao việc cho Claude (tool get_qc_assignment)
# ---------------------------------------------------------------------------

def submission_schema_text() -> str:
    """Mô tả định dạng biên bản (đưa vào kết quả get_qc_assignment + mô tả tool)."""
    crit = "\n".join(
        f"  - `{c['key']}` — {c['label']} (trọng số {c['weight']}%): {c['guide']}" for c in QC_CRITERIA
    )
    return (
        "ĐỊNH DẠNG BIÊN BẢN (tham số của submit_qc_review):\n"
        "- `report_date`, `assignment_id` (đúng giá trị nhận ở get_qc_assignment).\n"
        f"- `criteria`: ĐỦ {len(QC_CRITERIA)} tiêu chí, mỗi tiêu chí {{key, score 0–10 (1 số lẻ), comment}}:\n{crit}\n"
        "  Điểm tổng do hệ thống tự tính theo trọng số — không cần gửi.\n"
        "- `findings`: mỗi lỗi {severity: critical|major|minor, criterion, section (\"1\",\"2\",\"3\",\"4\",\"6\",\"8\",\"9\","
        "\"biz\",\"dev\",\"forecast\",\"cbam\",\"ets_policy\",\"general\"), location (vị trí người đọc thấy, vd \"Cần theo dõi #2\"), "
        "field_path (nếu biết, vd \"3.trading_scenarios[0].price_zone\"), title (1 dòng), current_text (báo cáo đang ghi — "
        "trích nguyên văn), expected_text (nên sửa thành), evidence (số đối chiếu/phép tính/trích nguồn — BẮT BUỘC với "
        "critical/major), verification: verified (đã tự đối chiếu bằng tool/dữ liệu) | partially_verified | unverified, "
        "source_refs [{label, url}]}.\n"
        "  critical = sai số liệu/chiều tác động có thể dẫn tới quyết định giao dịch/phòng hộ sai; major = sai logic, "
        "thiếu tin quan trọng, nguồn sai; minor = trình bày, diễn đạt.\n"
        "- `verdict`: pass | pass_with_fixes | fail (fail = chặn tự xuất bản 08:15 nếu lỗi nặng chưa được sửa).\n"
        "- `summary` (3–6 câu), `strengths` [chuỗi], `priorities` [{text, expected_gain (điểm cộng dự kiến)}], "
        "`body_markdown` (biên bản đầy đủ dạng Markdown — bảng điểm, lỗi theo mục, lộ trình cải thiện), "
        "`reviewer` (vd \"Jack (claude-opus-5-5)\").\n"
        f"Hệ thống chuẩn hóa kết luận: điểm < {FAIL_SCORE:g} hoặc có lỗi critical ĐÃ KIỂM CHỨNG → fail; có lỗi "
        f"critical/major hoặc điểm < {PASS_MIN_SCORE:g} → tối thiểu pass_with_fixes."
    )


def report_parts(content: Dict[str, Any]) -> List[Tuple[str, int]]:
    """Danh sách khối nội dung + kích thước (ký tự JSON) để Claude đọc từng phần."""
    return [(k, len(json.dumps(v, ensure_ascii=False))) for k, v in (content or {}).items()]


def part_json(content: Dict[str, Any], part: str) -> Optional[str]:
    if part == "all":
        return json.dumps(content or {}, ensure_ascii=False, indent=1)
    if part not in (content or {}):
        return None
    return json.dumps({part: content[part]}, ensure_ascii=False, indent=1)


_VN_TZ = timezone(timedelta(hours=7))


def today_vn() -> str:
    return datetime.now(_VN_TZ).date().isoformat()


async def _pick_report(session: AsyncSession, report_date: str) -> Optional[Report]:
    return (await session.execute(select(Report).where(Report.report_date == report_date))).scalars().first()


async def _previous_open_findings(session: AsyncSession, report_date: str, limit: int = 15) -> List[ReportQcFinding]:
    """Lỗi của biên bản gần nhất trước ngày này — để Claude kiểm tra lỗi cũ đã được sửa chưa."""
    prev_id = (await session.execute(
        select(ReportQcReview.id).where(ReportQcReview.report_date < report_date)
        .order_by(ReportQcReview.report_date.desc(), ReportQcReview.run_no.desc()).limit(1)
    )).scalar_one_or_none()
    if prev_id is None:
        return []
    return list((await session.execute(
        select(ReportQcFinding).where(ReportQcFinding.review_id == prev_id, ReportQcFinding.severity != "minor")
        .order_by(ReportQcFinding.position).limit(limit)
    )).scalars())


async def _knowledge_part_text(session: AsyncSession, report_date: str, content: Dict[str, Any]) -> str:
    """get_qc_assignment(part="knowledge"): NGUYÊN VĂN các đoạn tài liệu tri thức admin tải lên đã đưa vào prompt từng
    mục (content["knowledge_used"], services/knowledge.py) — để Claude QC đối chiếu số của câu trích "(Nguồn: <tên tài
    liệu>, tr. N)" thay vì coi là nguồn không kiểm chứng được."""
    from services import knowledge
    from services.report_qc import knowledge_chunk_ids  # tránh vòng import lúc khởi động

    used = content.get("knowledge_used") or {}
    if not used:
        return f"[Báo cáo {report_date}] Báo cáo này không dùng tài liệu tri thức nào."
    texts = await knowledge.chunk_texts(session, knowledge_chunk_ids(content))
    lines = [f"[Báo cáo {report_date} — đoạn tài liệu tri thức đã đưa vào prompt — assignment_id {content_hash(content)}]"]
    for sec, entries in used.items():
        lines.append(f"\n## Mục {sec}")
        for e in entries or []:
            if not isinstance(e, dict):
                continue
            body = texts.get(e.get("chunk_id"))
            lines.append(
                f"[{e.get('code')}] {e.get('title')} (tài liệu #{e.get('doc_id')}, đoạn #{e.get('chunk_id')})\n"
                + (body or "(đoạn không còn — tài liệu đã xoá hoặc được xử lý lại)")
            )
    lines.append(
        "\nLƯU Ý: tài liệu là nguồn admin tải lên (không có URL công khai) — số trong câu trích tài liệu phải khớp đúng "
        "đoạn tương ứng ở trên; tài liệu không phải tin trong ngày."
    )
    return "\n".join(lines)


async def qc_assignment_text(session: AsyncSession, report_date: Optional[str], part: Optional[str]) -> str:
    """Kết quả tool get_qc_assignment. `part` trống = tổng quan (trạng thái, assignment_id, rubric, Jack rule-based,
    danh sách khối); `part`="1"/"2"/.../"all" = JSON khối đó của bản nháp."""
    from services.report_qc import format_qc_report, run_report_qc  # tránh vòng import lúc khởi động

    # Không truyền ngày = báo cáo HÔM NAY (giờ VN) — không lấy "nháp mới nhất" để khỏi QC nhầm nháp cũ còn sót.
    report_date = report_date or today_vn()
    report = await _pick_report(session, report_date)
    if report is None:
        return (f"KHÔNG CÓ BÁO CÁO NGÀY {report_date} để QC. Nếu đang trong khung 07:30–07:50, thử lại sau 2 phút "
                "(bản nháp sinh lúc 07:00).")
    if report.status == "generating":
        return (f"Báo cáo ngày {report.report_date} ĐANG ĐƯỢC SINH (status=generating) — chưa QC được. "
                "Đợi 2 phút rồi gọi lại get_qc_assignment.")
    if report.status == "failed" or not report.content:
        return f"Báo cáo ngày {report.report_date} sinh lỗi (status={report.status}) — không có nội dung để QC."

    content = report.content
    if part and part.strip() == "knowledge":
        return await _knowledge_part_text(session, report.report_date, content)
    if part:
        text = part_json(content, part.strip())
        if text is None:
            return f"Không có khối `{part}` — các khối: {', '.join(k for k, _ in report_parts(content))}."
        return f"[Báo cáo {report.report_date} — khối {part} — assignment_id {content_hash(content)}]\n{text}"

    prev_runs = (await session.execute(
        select(func.count()).select_from(ReportQcReview).where(ReportQcReview.report_date == report.report_date)
    )).scalar_one()
    jack = await run_report_qc(session, report.report_date, content)
    previous = await _previous_open_findings(session, report.report_date)
    parts = ", ".join(f"`{k}` ({n:,} ký tự)" for k, n in report_parts(content))
    lines = [
        f"PHIẾU GIAO QC — BÁO CÁO NGÀY {report.report_date} (status={report.status}"
        + (", ĐÃ XUẤT BẢN — biên bản chỉ để lưu tham khảo" if report.status == "published" else "") + ")",
        f"assignment_id: {content_hash(content)}",
        f"Đã có {prev_runs} biên bản QC cho báo cáo này." if prev_runs else "Chưa có biên bản QC nào cho báo cáo này.",
        f"Dữ liệu báo cáo dùng: phiên/ngày dữ liệu trước ngày báo cáo. Khối nội dung (đọc bằng get_qc_assignment(part=...)): {parts}.",
        *([
            "Báo cáo có trích TÀI LIỆU TRI THỨC admin tải lên (nguồn không URL, nhãn \"(Nguồn: <tên tài liệu>, tr. N)\") — "
            "đọc nguyên văn các đoạn đã dùng bằng get_qc_assignment(part=\"knowledge\") trước khi chấm số liệu/nguồn."
        ] if content.get("knowledge_used") else []),
        "",
        "KẾT QUẢ JACK RULE-BASED (Python, đối chiếu DB — dùng làm điểm xuất phát, TỰ KIỂM lại, không chép nguyên):",
        format_qc_report(report.report_date, report.status, jack),
        "",
    ]
    if previous:
        lines.append("LỖI CỦA BIÊN BẢN QC GẦN NHẤT TRƯỚC ĐÓ (kiểm tra đã sửa chưa — còn thì ghi lại, hệ thống tự đếm lặp):")
        for f in previous:
            lines.append(f"- [{SEVERITY_LABELS[f.severity]} · {f.criterion} · Mục {f.section or 'chung'}] {f.title}")
        lines.append("")
    lines.append(submission_schema_text())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Lưu biên bản (tool submit_qc_review)
# ---------------------------------------------------------------------------

async def _repeat_counts(session: AsyncSession, report_date: str, fingerprints: List[str]) -> Dict[str, int]:
    """Số báo cáo (trong REPEAT_LOOKBACK_REPORTS báo cáo gần nhất trước đó, biên bản mới nhất mỗi ngày) có cùng
    dấu vân tay lỗi, +1 cho lần này."""
    if not fingerprints:
        return {}
    latest_per_date = (
        select(ReportQcReview.report_date, func.max(ReportQcReview.run_no).label("run_no"))
        .where(ReportQcReview.report_date < report_date)
        .group_by(ReportQcReview.report_date)
        .order_by(ReportQcReview.report_date.desc())
        .limit(REPEAT_LOOKBACK_REPORTS)
        .subquery()
    )
    rows = (await session.execute(
        select(ReportQcFinding.fingerprint, func.count(func.distinct(ReportQcReview.report_date)))
        .join(ReportQcReview, ReportQcReview.id == ReportQcFinding.review_id)
        .join(latest_per_date, (latest_per_date.c.report_date == ReportQcReview.report_date)
              & (latest_per_date.c.run_no == ReportQcReview.run_no))
        .where(ReportQcFinding.fingerprint.in_(fingerprints))
        .group_by(ReportQcFinding.fingerprint)
    )).all()
    found = {fp: int(n) for fp, n in rows}
    return {fp: found.get(fp, 0) + 1 for fp in fingerprints}


async def save_review(
    session: AsyncSession, payload: Dict[str, Any], *, token: Optional[ApiToken], source: str = "claude_mcp"
) -> ReportQcReview:
    report_date = str(payload.get("report_date") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", report_date):
        raise QcSubmissionError("Thiếu/sai `report_date` (YYYY-MM-DD).")
    report = (await session.execute(select(Report).where(Report.report_date == report_date))).scalars().first()
    if report is None or not report.content or report.status not in ("draft", "published"):
        raise QcSubmissionError(f"Không có báo cáo ngày {report_date} ở trạng thái draft/published để lưu QC.")
    current_hash = content_hash(report.content)
    if str(payload.get("assignment_id") or "").strip() != current_hash:
        raise QcSubmissionError(
            "`assignment_id` không khớp nội dung hiện tại — báo cáo đã bị sửa/sinh lại sau khi bạn đọc (hoặc truyền "
            "sai). Gọi lại get_qc_assignment, kiểm tra lại phần thay đổi rồi gửi biên bản với assignment_id mới."
        )
    data = normalize_submission(payload)

    from services.report_qc import run_report_qc  # Jack rule-based trên đúng bản này, để đối chiếu

    try:
        jack = await run_report_qc(session, report_date, report.content)
        jack_score, jack_issues = int(jack["overall_score"]), len(jack["issues"])
    except Exception:  # Jack lỗi không được chặn việc lưu biên bản của Claude
        logger.exception("[QC-REVIEW] Jack rule-based lỗi khi lưu biên bản %s", report_date)
        jack_score = jack_issues = None

    run_no = (await session.execute(
        select(func.coalesce(func.max(ReportQcReview.run_no), 0)).where(ReportQcReview.report_date == report_date)
    )).scalar_one() + 1
    repeats = await _repeat_counts(session, report_date, [f["fingerprint"] for f in data["findings"]])

    review = ReportQcReview(
        report_date=report_date,
        run_no=run_no,
        source=source,
        reviewer=data["reviewer"],
        token_id=token.id if token is not None else None,
        report_status=report.status,
        content_hash=current_hash,
        verdict=data["verdict"],
        claude_verdict=data["claude_verdict"],
        overall_score=data["overall_score"],
        criteria=data["criteria"],
        critical_count=data["counts"]["critical"],
        major_count=data["counts"]["major"],
        minor_count=data["counts"]["minor"],
        jack_rule_score=jack_score,
        jack_rule_issue_count=jack_issues,
        summary=data["summary"],
        strengths=data["strengths"],
        priorities=data["priorities"],
        body_markdown=data["body_markdown"],
    )
    session.add(review)
    await session.flush()
    for f in data["findings"]:
        session.add(ReportQcFinding(review_id=review.id, repeat_count=repeats.get(f["fingerprint"], 1), **f))
    await session.commit()
    await session.refresh(review)
    logger.info(
        "[QC-REVIEW] Lưu biên bản #%d báo cáo %s: %s, %.2f/10, %d lỗi (token=%s).",
        run_no, report_date, review.verdict, float(review.overall_score), len(data["findings"]),
        token.name if token is not None else "-",
    )
    return review


def submit_result_text(review: ReportQcReview, auto_publish_time: Optional[str]) -> str:
    verdict = VERDICT_LABELS[review.verdict]
    lines = [
        f"ĐÃ LƯU BIÊN BẢN QC #{review.run_no} cho báo cáo {review.report_date}.",
        f"Điểm tổng (hệ thống tính theo trọng số): {float(review.overall_score):.2f}/10 — Kết luận: {verdict}"
        + (f" (bạn đề xuất: {VERDICT_LABELS.get(review.claude_verdict or '', review.claude_verdict)})"
           if review.claude_verdict and review.claude_verdict != review.verdict else ""),
        f"Lỗi: {review.critical_count} nghiêm trọng, {review.major_count} quan trọng, {review.minor_count} nhỏ.",
    ]
    if review.report_status == "draft" and auto_publish_time:
        lines.append(
            f"Lịch tự xuất bản {auto_publish_time}: "
            + ("SẼ BỊ CHẶN (kết luận Không đạt) nếu Jenny không sửa được hết lỗi nghiêm trọng/quan trọng — "
               "khi đó admin xử lý thủ công." if review.verdict == "fail" else "vẫn chạy.")
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cổng tự xuất bản + đọc cho admin / Jenny
# ---------------------------------------------------------------------------

async def latest_review(session: AsyncSession, report_date: str) -> Optional[ReportQcReview]:
    return (await session.execute(
        select(ReportQcReview).where(ReportQcReview.report_date == report_date)
        .order_by(ReportQcReview.run_no.desc()).limit(1)
    )).scalars().first()


BLOCKING_SEVERITIES = ("critical", "major")


def gate_decision(review: Optional[Any], findings: Sequence[Any]) -> Tuple[bool, str]:
    """Hàm thuần của `publish_gate`. C2: không có biên bản → xuất bản. Biên bản Không đạt → chặn, TRỪ KHI đã có lỗi
    được sửa (Jenny tự sửa 08:00 hoặc admin), không còn lỗi critical/major nào ở trạng thái `open` (`wont_fix` =
    admin đã quyết bỏ qua) VÀ điểm sau sửa (`score_after_fixes`) đạt ≥ FAIL_SCORE."""
    if review is None:
        return True, "chưa có biên bản QC — vẫn xuất bản theo quy tắc C2"
    head = f"biên bản QC #{review.run_no}"
    score = f"{float(review.overall_score):.2f}/10"
    if review.verdict != "fail":
        return True, f"{head}: {VERDICT_LABELS[review.verdict]} ({score})"
    open_blocking = [f for f in findings if f.status == "open" and f.severity in BLOCKING_SEVERITIES]
    fixed = sum(1 for f in findings if f.status == "fixed")
    if open_blocking:
        return False, f"{head} kết luận Không đạt ({score}), còn {len(open_blocking)} lỗi nghiêm trọng/quan trọng chưa sửa"
    if not fixed:
        return False, f"{head} kết luận Không đạt ({score}) và chưa có lỗi nào được sửa"
    after, _ = score_after_fixes(review.criteria or [], findings)
    if after < FAIL_SCORE:
        return False, (f"{head} kết luận Không đạt ({score}); đã sửa {fixed} lỗi nhưng điểm sau sửa {after:.2f}/10 "
                       f"vẫn dưới ngưỡng {FAIL_SCORE:g}")
    return True, (f"{head} Không đạt ({score}) nhưng đã sửa hết lỗi nghiêm trọng/quan trọng ({fixed} lỗi đã sửa), "
                  f"điểm sau sửa {after:.2f}/10")


async def publish_gate(session: AsyncSession, report_date: str) -> Tuple[bool, str]:
    """Cổng tự xuất bản — xem `gate_decision`."""
    review = await latest_review(session, report_date)
    findings = (await load_findings(session, [review.id]))[review.id] if review is not None else []
    return gate_decision(review, findings)


async def load_findings(session: AsyncSession, review_ids: List[int]) -> Dict[int, List[ReportQcFinding]]:
    out: Dict[int, List[ReportQcFinding]] = {rid: [] for rid in review_ids}
    if not review_ids:
        return out
    for f in (await session.execute(
        select(ReportQcFinding).where(ReportQcFinding.review_id.in_(review_ids))
        .order_by(ReportQcFinding.review_id, ReportQcFinding.position)
    )).scalars():
        out[f.review_id].append(f)
    return out


def finding_dict(f: ReportQcFinding) -> Dict[str, Any]:
    return {
        "id": f.id, "position": f.position, "severity": f.severity, "criterion": f.criterion,
        "criterion_label": _CRITERIA_BY_KEY.get(f.criterion, {}).get("label", f.criterion),
        "section": f.section, "location": f.location, "field_path": f.field_path, "title": f.title,
        "current_text": f.current_text, "expected_text": f.expected_text, "evidence": f.evidence,
        "verification": f.verification, "source_refs": f.source_refs or [], "repeat_count": f.repeat_count,
        "status": f.status, "status_note": f.status_note,
        "updated_at": f.updated_at.isoformat() if f.updated_at else None,
    }


def review_dict(review: ReportQcReview, findings: Optional[List[ReportQcFinding]] = None,
                current_hash: Optional[str] = None, with_body: bool = True) -> Dict[str, Any]:
    """`findings` có → kèm `score_after_fixes` (điểm sau khi cộng lại phần của lỗi đã sửa); `with_body=False` (danh
    sách) thì không trả chi tiết lỗi/biên bản."""
    out = {
        "id": review.id, "report_date": review.report_date, "run_no": review.run_no, "source": review.source,
        "reviewer": review.reviewer, "report_status": review.report_status, "verdict": review.verdict,
        "claude_verdict": review.claude_verdict, "overall_score": float(review.overall_score),
        "criteria": review.criteria or [], "critical_count": review.critical_count,
        "major_count": review.major_count, "minor_count": review.minor_count,
        "jack_rule_score": review.jack_rule_score, "jack_rule_issue_count": review.jack_rule_issue_count,
        "summary": review.summary, "strengths": review.strengths or [], "priorities": review.priorities or [],
        "created_at": review.created_at.isoformat() if review.created_at else None,
        "is_stale": current_hash is not None and current_hash != review.content_hash,
    }
    if findings is not None:
        after, criteria = score_after_fixes(review.criteria or [], findings)
        out["score_after_fixes"] = after
        out["criteria"] = criteria
        out["fixed_count"] = sum(1 for f in findings if f.status == "fixed")
    if findings is not None and with_body:
        out["body_markdown"] = review.body_markdown
        out["findings"] = [finding_dict(f) for f in findings]
    return out


async def update_finding_status(session: AsyncSession, finding_id: int, status: str,
                                note: Optional[str]) -> Optional[ReportQcFinding]:
    if status not in FINDING_STATUSES:
        raise QcSubmissionError(f"status ∈ {FINDING_STATUSES}")
    finding = await session.get(ReportQcFinding, finding_id)
    if finding is None:
        return None
    finding.status = status
    finding.status_note = _clean(note, "status_note")
    finding.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return finding


async def qc_review_text(session: AsyncSession, report_date: str) -> str:
    """Tool Jenny `get_qc_review` (chỉ admin): biên bản Claude QC mới nhất của 1 báo cáo."""
    review = await latest_review(session, report_date)
    if review is None:
        return f"Chưa có biên bản QC (Claude/Jack qua MCP) nào cho báo cáo ngày {report_date}."
    report = (await session.execute(select(Report.content).where(Report.report_date == report_date))).first()
    stale = report is not None and report[0] is not None and content_hash(report[0]) != review.content_hash
    findings = (await load_findings(session, [review.id]))[review.id]
    lines = [
        f"BIÊN BẢN QC #{review.run_no} — BÁO CÁO {report_date} ({review.reviewer or 'Claude'}, "
        f"{review.created_at:%d/%m/%Y %H:%M} UTC, lúc QC báo cáo đang {review.report_status})",
        f"Điểm tổng {float(review.overall_score):.2f}/10 — {VERDICT_LABELS[review.verdict]}"
        f" · Jack rule-based {review.jack_rule_score if review.jack_rule_score is not None else '—'}/100",
    ]
    after, _ = score_after_fixes(review.criteria or [], findings)
    if after != float(review.overall_score):
        lines.append(f"Điểm sau khi sửa (cộng lại phần của lỗi đã sửa): {after:.2f}/10")
    if stale:
        lines.append("LƯU Ý: báo cáo đã được sửa SAU lần QC này — một số lỗi có thể đã được xử lý.")
    lines.append("Điểm tiêu chí: " + " | ".join(f"{c['label']} {c['score']:g}" for c in review.criteria or []))
    lines.append(f"Tổng quan: {review.summary}")
    if findings:
        lines.append(f"Lỗi ({review.critical_count} nghiêm trọng, {review.major_count} quan trọng, {review.minor_count} nhỏ):")
        for f in findings:
            repeat = f" · lặp {f.repeat_count} báo cáo" if f.repeat_count > 1 else ""
            state = "" if f.status == "open" else f" · {'đã sửa' if f.status == 'fixed' else 'không sửa'}"
            lines.append(f"- [{SEVERITY_LABELS[f.severity]} · Mục {f.section or 'chung'}{repeat}{state}] {f.title}")
            if f.current_text:
                lines.append(f"  Báo cáo ghi: {f.current_text}")
            if f.expected_text:
                lines.append(f"  Nên sửa: {f.expected_text}")
            if f.evidence:
                lines.append(f"  Căn cứ: {f.evidence}")
    if review.priorities:
        lines.append("Ưu tiên: " + " ; ".join(f"{p['rank']}. {p['text']}" for p in review.priorities))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool + prompt MCP cho token qc_agent (api/routers/mcp_gateway.py, api/mcp_http.py)
# ---------------------------------------------------------------------------

_FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": list(SEVERITIES)},
        "criterion": {"type": "string", "enum": list(CRITERIA_KEYS)},
        "section": {"type": "string", "description": "Mục: 1,2,3,4,6,8,9,biz,dev,forecast,cbam,ets_policy,general."},
        "location": {"type": "string", "description": "Vị trí người đọc thấy, vd \"Cần theo dõi #2\"."},
        "field_path": {"type": "string", "description": "Đường dẫn JSON nếu biết, vd \"3.trading_scenarios[0].price_zone\"."},
        "title": {"type": "string"},
        "current_text": {"type": "string", "description": "Nguyên văn báo cáo đang ghi."},
        "expected_text": {"type": "string", "description": "Nên sửa thành."},
        "evidence": {"type": "string", "description": "Số đối chiếu / phép tính / trích nguồn. Bắt buộc với critical/major."},
        "verification": {"type": "string", "enum": list(VERIFICATIONS)},
        "source_refs": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "url": {"type": "string"}}}},
    },
    "required": ["severity", "criterion", "title"],
}

GET_QC_ASSIGNMENT_TOOL = {
    "name": "get_qc_assignment",
    "description": (
        "CHỈ cho tài khoản QC. Nhận phiếu giao QC báo cáo Carbon Analyst: trạng thái bản nháp, `assignment_id`, kết "
        "quả Jack rule-based (đối chiếu DB), lỗi biên bản trước, rubric 7 tiêu chí + định dạng biên bản. Gọi với "
        "`part` (\"1\",\"2\",\"3\",\"4\",\"6\",\"8\",\"9\",\"biz\"... hoặc \"all\") để đọc JSON nội dung bản nháp; "
        "`part`=\"knowledge\" = nguyên văn các đoạn tài liệu tri thức admin tải lên mà báo cáo đã dùng. "
        "Không truyền report_date = báo cáo hôm nay (giờ Việt Nam)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "report_date": {"type": "string", "description": "TUỲ CHỌN — YYYY-MM-DD; trống = báo cáo hôm nay (giờ VN)."},
            "part": {"type": "string", "description": "TUỲ CHỌN — khối nội dung cần đọc, hoặc \"all\"."},
        },
    },
}

SUBMIT_QC_REVIEW_TOOL = {
    "name": "submit_qc_review",
    "description": (
        "CHỈ cho tài khoản QC. Gửi biên bản QC đã chấm để lưu vào hệ thống (bảng report_qc_reviews). Bắt buộc "
        "`report_date`, `assignment_id` (từ get_qc_assignment), đủ 7 `criteria`, `verdict`, `summary`. Hệ thống tự "
        "tính điểm tổng theo trọng số và chuẩn hóa kết luận; Jenny tự sửa theo biên bản lúc 08:00, verdict=fail CHẶN "
        "lịch tự xuất bản 08:15 nếu còn lỗi critical/major chưa sửa. Lỗi định "
        "dạng trả về danh sách cần sửa — sửa rồi gọi lại."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "report_date": {"type": "string", "description": "BẮT BUỘC — YYYY-MM-DD của báo cáo đã QC."},
            "assignment_id": {"type": "string"},
            "reviewer": {"type": "string"},
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "criteria": {"type": "array", "items": {"type": "object", "properties": {
                "key": {"type": "string", "enum": list(CRITERIA_KEYS)},
                "score": {"type": "number", "minimum": 0, "maximum": 10},
                "comment": {"type": "string"}}, "required": ["key", "score", "comment"]}},
            "findings": {"type": "array", "items": _FINDING_SCHEMA},
            "summary": {"type": "string"},
            "strengths": {"type": "array", "items": {"type": "string"}},
            "priorities": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "expected_gain": {"type": "number"}}}},
            "body_markdown": {"type": "string"},
        },
        "required": ["report_date", "assignment_id", "verdict", "criteria", "summary"],
    },
}

QC_AGENT_TOOLS = [GET_QC_ASSIGNMENT_TOOL, SUBMIT_QC_REVIEW_TOOL]
QC_AGENT_TOOL_NAMES = frozenset(t["name"] for t in QC_AGENT_TOOLS)

QC_ROUTINE_PROMPT = """Bạn là Jack — chuyên viên kiểm soát chất lượng (QC) báo cáo ngày của Carbon Analyst (thị trường EUA, năng lượng, CBAM cho SIM). Nhiệm vụ: QC bản NHÁP báo cáo hôm nay (sinh lúc 07:00, giờ Việt Nam), rồi gửi biên bản qua MCP carbon-analyst trước 07:55 — lúc 08:00 Jenny tự sửa báo cáo theo biên bản của bạn, 08:15 hệ thống tự xuất bản. KHÔNG sửa báo cáo, chỉ chấm và ghi biên bản.

QUY TRÌNH
1. Gọi get_qc_assignment (không truyền report_date). Nếu kết quả báo "ĐANG ĐƯỢC SINH" hoặc "KHÔNG CÓ BÁO CÁO", đợi 2 phút rồi gọi lại; quá 07:50 giờ VN vẫn chưa có thì DỪNG, không gửi gì.
2. Đọc toàn bộ bản nháp: get_qc_assignment(report_date=<ngày>, part="all") (hoặc từng khối nếu quá dài). Ghi lại assignment_id.
3. Đối chiếu bằng tool, ưu tiên phần ảnh hưởng quyết định giao dịch/phòng hộ:
   - Giá, %Δ, ATR, biên độ: get_market_prices, get_price_history, calc_price_stats, get_eua_details (truyền report_date).
   - Số liệu trích tin: get_article theo URL nguồn; độ phủ: browse_news / search_news trong khung tin.
   - Đấu giá, CBAM, lịch: get_eua_auctions, get_cbam_estimate, get_calendar_events.
   - Kết quả Jack rule-based trong phiếu giao là gợi ý — tự kiểm lại, bỏ báo động giả, bổ sung lỗi logic mà rule không bắt được (chiều tác động lên EUA, cơ chế MSR/fuel switching, mâu thuẫn giữa các mục, kịch bản ↔ nhãn Tổng hợp, R:R, nguồn sai nhà xuất bản).
   - Lỗi của biên bản trước còn tồn tại thì ghi lại (hệ thống tự đếm số lần lặp).
4. Chấm ĐỦ 7 tiêu chí theo rubric (0–10, có comment). Mỗi lỗi: trích nguyên văn current_text, đề xuất expected_text, evidence bằng số/phép tính/nguồn; verification="verified" CHỈ khi đã tự đối chiếu bằng tool, không thì partially_verified/unverified. Không bịa số; không chắc thì ghi rõ là nghi vấn (minor/unverified).
5. Kết luận: fail CHỈ khi có sai sót khiến báo cáo không nên phát hành (sai giá/chiều tác động trọng yếu, khuyến nghị vị thế/phòng hộ sai số học, nguồn bịa) — fail chặn tự xuất bản tới khi các lỗi critical/major được sửa. Jenny chỉ tự sửa được lỗi có current_text trích NGUYÊN VĂN và field_path đúng, nên ghi 2 trường này thật chính xác. Có lỗi cần sửa nhưng vẫn phát hành được → pass_with_fixes.
6. Gọi submit_qc_review với report_date, assignment_id, reviewer="Jack (<tên model>)", criteria, findings, verdict, summary, strengths, priorities (kèm expected_gain), body_markdown (biên bản đầy đủ: bảng điểm, lỗi theo mục, lộ trình cải thiện). Nếu bị trả lỗi định dạng thì sửa và gửi lại; nếu assignment_id không khớp thì đọc lại báo cáo (bước 2) rồi gửi lại.
Phải gửi biên bản trước 07:55 giờ VN — ưu tiên kiểm phần trọng yếu trước khi hết giờ. Viết tiếng Việt, ngắn gọn, chính xác."""

QC_PROMPT_DEF = {
    "name": "jack_qc_review",
    "title": "Jack QC báo cáo nháp (lưu biên bản)",
    "description": "Claude đóng vai Jack QC bản nháp báo cáo hôm nay theo rubric chuẩn rồi gửi biên bản vào hệ thống.",
    "arguments": [],
}
