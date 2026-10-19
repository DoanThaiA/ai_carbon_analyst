"""Sửa bản nháp báo cáo theo biên bản QC (nút "Sửa báo cáo theo QC" ở panel Kết quả QC — CHỈ admin).

Thiết kế để sửa ĐÚNG CHỖ, không lan sang phần khác:
1. Định vị (Python, không LLM): mỗi lỗi QC (`report_qc_findings`) được neo vào đúng các chuỗi văn xuôi trong
   `reports.content` qua `field_path` + `current_text` (so khớp nguyên văn sau chuẩn hóa khoảng trắng). Không neo được
   (lỗi "thiếu tin", "toàn báo cáo", trường enum/URL/số liệu máy) → trả về "cần sửa tay", KHÔNG gửi cho LLM.
2. Đề xuất (LLM): mỗi nhóm chuỗi bị neo được gửi kèm ĐÚNG các lỗi của nó; model chỉ được trả các cặp thay thế
   {old → new} với `old` là đoạn NGUYÊN VĂN nằm trong chuỗi đó. Model không thấy và không thể sửa chuỗi khác.
3. Kiểm (Python): `old` phải xuất hiện đúng 1 lần, các thay thế không chồng nhau, độ dài hợp lý, không đụng cụm
   "(Nguồn: …)"/link, MỌI số mới phải có căn cứ trong biên bản hoặc chính đoạn gốc (chặn bịa số). Sau đó chuẩn hóa
   trình bày (`report_consistency.apply_consistency_fixes`) và chạy Jack rule-based trước/sau — thay đổi làm phát sinh
   lỗi Jack MỚI ngay tại chỗ sửa bị đánh dấu và bỏ chọn mặc định.
4. Xem trước → áp dụng: `build_fix_preview` không ghi gì; admin chọn thay đổi rồi `apply_fix_changes` ghi với điều
   kiện nội dung chưa đổi (`base_hash` + `before` từng chuỗi) và đánh dấu lỗi "Đã sửa". Hoàn tác = áp lại các thay đổi
   theo chiều ngược (before ↔ after) và trả lỗi về "Chưa xử lý".

Các hàm định vị/kiểm là hàm thuần — test ở tests/test_report_qc_fix.py.
"""
import asyncio
import copy
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Report, ReportQcFinding, ReportQcReview
from services.report_consistency import SKIP_KEYS, apply_consistency_fixes, inline_source_spans
from services.report_qc_review import SEVERITY_LABELS, content_hash, load_findings, update_finding_status

logger = logging.getLogger(__name__)

# Model sửa: dùng đúng model của các mục phân tích (độ chính xác > chi phí, mỗi lần bấm chỉ vài lượt gọi).
FIX_MODEL = "claude-opus-5"
FIX_MAX_TOKENS = 4096
TARGETS_PER_CALL = 6  # số nhóm chuỗi mỗi lượt gọi LLM — nhỏ để model tập trung, chạy song song
LLM_CONCURRENCY = 4
MAX_LEAVES_PER_FINDING = 12  # field_path trỏ cả 1 kịch bản → tối đa chừng này chuỗi con
MAX_TEXT_MATCHES = 3  # current_text khớp nhiều chỗ hơn thế → mơ hồ, sửa tay
MAX_GROWTH_CHARS = 600  # 1 thay thế không được dài hơn `old` quá chừng này ký tự

# Tiêu đề GỐC của bài ở Mục 6 — giữ nguyên ngôn ngữ/nguyên văn bài, không bao giờ sửa.
_ORIGINAL_TITLE_RE = re.compile(r"^6\.(?:international|vietnam)\[\d+\]\.title$")
# Mục QC → tiền tố đường dẫn trong content ("" = cả báo cáo).
SECTION_PREFIXES: Dict[str, Tuple[str, ...]] = {
    "1": ("1",), "2": ("2",), "3": ("3",), "4": ("4",), "6": ("6",), "8": ("8",), "9": ("9",), "biz": ("biz",),
    "dev": ("2.key_developments",), "forecast": ("3.vs_previous", "2.forecast_review"),
    "cbam": ("3.hedge_action", "2.cbam_estimate", "3.cbam"), "ets_policy": ("3.ets_policy",), "general": ("",),
}

_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
_URL_RE = re.compile(r"https?://\S+")
_ELLIPSIS_RE = re.compile(r"\s*(?:…|\.\.\.|\[\.\.\.\])\s*")


class QcFixError(ValueError):
    """Không sửa được (báo cáo không ở trạng thái nháp, nội dung đã đổi...) — thông điệp hiển thị cho admin."""


# ---------------------------------------------------------------------------
# Đường dẫn JSON
# ---------------------------------------------------------------------------

def parse_path(path: str) -> List[Any]:
    """'3.trading_scenarios[0].price_zone' → ['3', 'trading_scenarios', 0, 'price_zone']. Chấp nhận cả tiền tố
    'content.' và kiểu content["3"]."""
    raw = (path or "").strip()
    raw = re.sub(r"^content(?=[.\[])\.?", "", raw)
    raw = re.sub(r"\[[\"']([^\"'\]]+)[\"']\]", r".\1", raw).lstrip(".")
    tokens: List[Any] = []
    for name, idx in re.findall(r"([^.\[\]]+)|\[(\d+)\]", raw):
        tokens.append(int(idx) if idx else name.strip())
    return tokens


def format_path(tokens: Sequence[Any]) -> str:
    out = ""
    for t in tokens:
        out += f"[{t}]" if isinstance(t, int) else (f".{t}" if out else str(t))
    return out


def normalize_path(path: Optional[str]) -> Optional[str]:
    if not path or not str(path).strip():
        return None
    tokens = parse_path(str(path))
    return format_path(tokens) if tokens else None


def get_at(content: Any, path: str) -> Any:
    node = content
    for t in parse_path(path):
        if isinstance(t, int) and isinstance(node, list) and 0 <= t < len(node):
            node = node[t]
        elif isinstance(node, dict) and str(t) in node:
            node = node[str(t)]
        else:
            raise KeyError(path)
    return node


def set_at(content: Any, path: str, value: Any) -> None:
    tokens = parse_path(path)
    if not tokens:
        raise KeyError(path)
    parent = get_at(content, format_path(tokens[:-1])) if len(tokens) > 1 else content
    last = tokens[-1]
    if isinstance(last, int) and isinstance(parent, list) and 0 <= last < len(parent):
        parent[last] = value
    elif isinstance(parent, dict) and str(last) in parent:
        parent[str(last)] = value
    else:
        raise KeyError(path)


def editable_leaves(content: Dict[str, Any]) -> Dict[str, str]:
    """{đường dẫn: chuỗi} mọi chuỗi văn xuôi ĐƯỢC PHÉP sửa — bỏ khoá không phải văn xuôi (`SKIP_KEYS`: URL, enum, số
    liệu máy, khối Python tự dựng) và tiêu đề gốc Mục 6. Thứ tự = thứ tự trong báo cáo."""
    out: Dict[str, str] = {}

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in SKIP_KEYS:
                    continue
                p = f"{path}.{k}" if path else str(k)
                if isinstance(v, str):
                    if v.strip() and not _ORIGINAL_TITLE_RE.match(p):
                        out[p] = v
                else:
                    _walk(v, p)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                p = f"{path}[{i}]"
                if isinstance(v, str):
                    if v.strip():
                        out[p] = v
                else:
                    _walk(v, p)

    _walk(content or {}, "")
    return out


def _under(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "[")


# ---------------------------------------------------------------------------
# Định vị lỗi → chuỗi
# ---------------------------------------------------------------------------

def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace(" ", " ")).strip()


def _strip_quotes(text: str) -> str:
    return text.strip().strip("\"'“”‘’«»").strip()


def text_fragments(current_text: Optional[str]) -> List[str]:
    """Nguyên văn QC trích có thể bị rút gọn bằng "…" — tách thành các mảnh (bỏ mảnh quá ngắn, dễ khớp nhầm)."""
    if not current_text:
        return []
    parts = [_squash(_strip_quotes(p)) for p in _ELLIPSIS_RE.split(_strip_quotes(current_text))]
    return [p for p in parts if len(p) >= 8]


def contains_fragments(text: str, fragments: Sequence[str]) -> bool:
    """Mọi mảnh có trong `text` (so khớp bỏ khác biệt khoảng trắng, đúng thứ tự)."""
    hay = _squash(text)
    pos = 0
    for frag in fragments:
        i = hay.find(frag, pos)
        if i < 0:
            return False
        pos = i + len(frag)
    return True


@dataclass
class Location:
    finding_id: int
    paths: List[str] = field(default_factory=list)
    reason: Optional[str] = None  # khác None = không tự sửa được (lý do hiển thị)
    method: Optional[str] = None  # "field_path+text" | "field_path" | "text"


def locate_finding(finding: Dict[str, Any], leaves: Dict[str, str]) -> Location:
    """Neo 1 lỗi vào các chuỗi cần sửa. Ưu tiên: field_path + current_text → current_text trong mục của lỗi →
    field_path trỏ đúng 1 chuỗi (hoặc 1 khối nhỏ như 1 kịch bản). Mơ hồ/không thấy → lý do để admin sửa tay."""
    loc = Location(finding_id=finding["id"])
    fp = normalize_path(finding.get("field_path"))
    frags = text_fragments(finding.get("current_text"))
    section = finding.get("section") or "general"

    in_fp = [p for p in leaves if _under(p, fp)] if fp else []
    if frags:
        if in_fp:
            hits = [p for p in in_fp if contains_fragments(leaves[p], frags)]
            if hits:
                return _accept(loc, hits, "field_path+text")
        prefixes = SECTION_PREFIXES.get(section, ("",))
        scope = [p for p in leaves if any(_under(p, pre) for pre in prefixes)]
        hits = [p for p in scope if contains_fragments(leaves[p], frags)]
        if not hits and section != "general":  # QC ghi nhầm mục → tìm cả báo cáo
            hits = [p for p in leaves if contains_fragments(leaves[p], frags)]
        if hits:
            return _accept(loc, hits, "text")
        loc.reason = ("Không tìm thấy nguyên văn \"Báo cáo ghi\" trong bản nháp hiện tại — có thể đã được sửa, hoặc "
                      "nằm ở trường không phải văn xuôi (giá, nhãn enum, nguồn, URL).")
        return loc

    if in_fp:
        if len(in_fp) > MAX_LEAVES_PER_FINDING:
            loc.reason = f"Vị trí `{fp}` quá rộng ({len(in_fp)} đoạn) và QC không trích nguyên văn — cần sửa tay."
            return loc
        return _accept(loc, in_fp, "field_path")
    if fp:
        loc.reason = f"Vị trí `{fp}` không phải đoạn văn xuôi sửa được (enum/nguồn/URL/số liệu máy) hoặc không tồn tại."
    else:
        loc.reason = "QC không ghi vị trí (field_path) lẫn nguyên văn — không neo được đoạn cần sửa."
    return loc


def _accept(loc: Location, hits: List[str], method: str) -> Location:
    if method != "field_path" and len(hits) > MAX_TEXT_MATCHES:
        loc.reason = f"Nguyên văn xuất hiện ở {len(hits)} chỗ — quá mơ hồ để tự sửa, cần sửa tay."
        return loc
    loc.paths, loc.method = hits, method
    return loc


def is_fixable(finding: Dict[str, Any]) -> Optional[str]:
    """Lý do KHÔNG đưa lỗi vào luồng tự sửa (None = đưa vào)."""
    if finding.get("status") != "open":
        return "Lỗi đã được đánh dấu xử lý."
    if not (finding.get("expected_text") or "").strip() and not (finding.get("current_text") or "").strip():
        return "QC không đề xuất cách sửa (thiếu cả \"Báo cáo ghi\" lẫn \"Nên sửa\")."
    return None


# ---------------------------------------------------------------------------
# Kiểm đề xuất của LLM
# ---------------------------------------------------------------------------

def _num_variants(token: str) -> Set[str]:
    """Các cách đọc 1 số viết tay: '1.234' = 1234 (phân cách nghìn kiểu vi) hoặc 1,234 (thập phân kiểu en); '79,20' =
    79,2 — so số không phụ thuộc kiểu viết vi/en, bỏ số 0 thừa cuối phần lẻ."""
    out = {str(int(re.sub(r"[.,]", "", token)))}
    m = re.fullmatch(r"(.*)[.,](\d+)", token)
    if m:
        frac = m.group(2).rstrip("0")
        whole = str(int(re.sub(r"[.,]", "", m.group(1))))
        out.add(f"{whole}.{frac}" if frac else whole)
    return out


def _num_tokens(text: str) -> List[str]:
    return _NUM_RE.findall(_URL_RE.sub(" ", text or ""))


def number_keys(text: str) -> Set[str]:
    return {v for tok in _num_tokens(text) for v in _num_variants(tok)}


def novel_numbers(new: str, pool: Set[str]) -> List[str]:
    """Số trong `new` không có cách đọc nào nằm trong `pool` (số có căn cứ)."""
    return [tok for tok in dict.fromkeys(_num_tokens(new)) if not (_num_variants(tok) & pool)]


def _source_ranges(text: str) -> List[Tuple[int, int]]:
    spans = [(s, e) for s, e, _ in inline_source_spans(text)]
    spans += [(m.start(), m.end()) for m in _URL_RE.finditer(text)]
    return spans


def validate_edits(text: str, edits: List[Dict[str, Any]], allowed_numbers: Set[str],
                   allowed_findings: Set[int]) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Áp các thay thế {old, new, finding_ids} lên `text` (chuỗi gốc). → (chuỗi mới, edits đã nhận, edits bị loại
    kèm `reason`). Chỉ nhận thay thế đáp ứng MỌI điều kiện:
    - `old` xuất hiện đúng 1 lần trong chuỗi gốc, không chồng thay thế khác, không cắt vào cụm (Nguồn: …)/URL;
    - không phình quá MAX_GROWTH_CHARS, không xoá trắng cả đoạn;
    - mọi số trong `new` mà `old` không có phải có căn cứ (`allowed_numbers`: số trong biên bản lỗi + đoạn gốc);
    - gắn với ít nhất 1 lỗi được giao cho đoạn này."""
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    spans: List[Tuple[int, int]] = []
    protected = _source_ranges(text)
    for e in edits:
        old, new = e.get("old"), e.get("new")
        ids = [i for i in (e.get("finding_ids") or []) if i in allowed_findings]
        reason = None
        if not isinstance(old, str) or not old or not isinstance(new, str):
            reason = "thiếu old/new"
        elif old == new:
            reason = "không thay đổi gì"
        elif not ids:
            reason = "không gắn với lỗi QC nào của đoạn này"
        elif text.count(old) != 1:
            reason = "đoạn cần thay không có trong bản nháp" if old not in text else "đoạn cần thay xuất hiện nhiều lần"
        if reason is None:
            start = text.index(old)
            end = start + len(old)
            if any(start < e2 and s2 < end for s2, e2 in spans):
                reason = "chồng lên thay thế khác"
            elif any(start < pe and ps < end and not (start <= ps and pe <= end and text[ps:pe] in new)
                     for ps, pe in protected):
                reason = "đụng vào trích nguồn/URL"  # chỉ được bao trọn VÀ giữ nguyên văn cụm nguồn
            elif len(new) - len(old) > MAX_GROWTH_CHARS:
                reason = "đoạn thay thế dài bất thường"
            elif not new.strip() and len(old) >= 0.9 * len(text.strip()):
                reason = "xoá trắng cả đoạn"
            else:
                novel = novel_numbers(new, allowed_numbers | number_keys(old))
                if novel:
                    reason = "số mới không có căn cứ trong biên bản QC: " + ", ".join(novel)
        if reason:
            rejected.append({**e, "reason": reason})
            continue
        spans.append((start, end))
        accepted.append({"old": old, "new": new, "finding_ids": ids, "note": e.get("note")})

    # Áp từ cuối lên để vị trí các thay thế trước không bị lệch.
    out = text
    for a in sorted(accepted, key=lambda a: text.index(a["old"]), reverse=True):
        i = text.index(a["old"])
        out = out[:i] + a["new"] + out[i + len(a["old"]):]
    return out, accepted, rejected


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

FIX_SYSTEM = """Bạn là biên tập viên sửa báo cáo thị trường carbon (EUA) theo biên bản QC đã được kiểm chứng.

NHIỆM VỤ: với mỗi ĐOẠN được giao, sửa ĐÚNG các lỗi QC gắn với đoạn đó — không hơn.

QUY TẮC BẮT BUỘC
1. Chỉ trả về các thay thế {old → new}. `old` phải là đoạn NGUYÊN VĂN, liền mạch, chép chính xác từng ký tự từ đoạn
   gốc (giữ nguyên dấu, khoảng trắng, markdown **...**), và đủ dài để xuất hiện DUY NHẤT 1 lần trong đoạn. Chọn `old`
   ngắn nhất bao trọn phần sai (thường là 1 cụm/1 câu) — KHÔNG thay cả đoạn khi chỉ sai 1 cụm.
2. Giữ nguyên mọi phần không liên quan tới lỗi: văn phong, cấu trúc, markdown, nhãn neo ("**Tổng hợp:**", "[TÍCH CỰC]",
   "**Entry:**", "**Mục tiêu:**", "**Quản trị rủi ro:**"...), trích nguồn "(Nguồn: …)", số [N], tiếng Việt.
3. Số liệu: CHỈ dùng số có trong "Nên sửa"/"Căn cứ"/"Báo cáo ghi" của lỗi hoặc trong chính đoạn gốc. Không tự tính
   số mới, không làm tròn khác, viết số kiểu Việt Nam (79,20; 1.234,5).
4. Lỗi chỉ ra mâu thuẫn số học (R:R, cắt lỗ, mục tiêu...) trong 1 khối có nhiều đoạn: sửa đủ các đoạn liên quan để
   khối nhất quán, nhưng chỉ bằng số đã có căn cứ.
5. KHÔNG sửa nếu: "Nên sửa" mơ hồ/chỉ là nhận xét chung, cần dữ liệu không có trong biên bản (vd bổ sung tin mới,
   tra giá), hoặc lỗi thực ra không nằm trong đoạn được giao → đưa vào `skipped` kèm lý do ngắn.
6. Mỗi thay thế ghi `finding_ids` (id lỗi nó xử lý) và `note` (≤ 1 câu: đã sửa gì).

Trả về DUY NHẤT 1 object JSON:
{"edits": [{"target": "T1", "old": "...", "new": "...", "finding_ids": [12], "note": "..."}],
 "skipped": [{"finding_id": 13, "reason": "..."}]}"""


def _finding_block(f: Dict[str, Any]) -> str:
    lines = [f"- Lỗi #{f['id']} [{SEVERITY_LABELS.get(f['severity'], f['severity'])} · {f.get('criterion')}] {f['title']}"]
    for key, label in (("location", "Vị trí"), ("current_text", "Báo cáo ghi"), ("expected_text", "Nên sửa"),
                       ("evidence", "Căn cứ")):
        if f.get(key):
            lines.append(f"  {label}: {f[key]}")
    return "\n".join(lines)


def build_fix_prompt(groups: List[Dict[str, Any]]) -> str:
    """`groups`: [{"target": "T1", "paths": [...], "texts": {path: text}, "findings": [finding dict]}]."""
    parts = []
    for g in groups:
        seg = [f"=== {g['target']} ==="]
        if len(g["paths"]) == 1:
            seg.append(f"Đoạn gốc ({g['paths'][0]}):\n<<<\n{g['texts'][g['paths'][0]]}\n>>>")
        else:
            seg.append("Khối gồm nhiều đoạn — mỗi thay thế ghi `target` dạng \"" + g["target"] + ".k\" (k = số thứ tự đoạn):")
            for k, p in enumerate(g["paths"], start=1):
                seg.append(f"[{g['target']}.{k}] ({p}):\n<<<\n{g['texts'][p]}\n>>>")
        seg.append("Lỗi QC cần sửa trong đoạn này:")
        seg.extend(_finding_block(f) for f in g["findings"])
        parts.append("\n".join(seg))
    return "\n\n".join(parts)


async def _call_fix_llm(prompt: str) -> Optional[Dict[str, Any]]:
    from services.report_generator import _call_llm, _extract_json  # tránh vòng import lúc khởi động

    raw = await _call_llm(prompt, system=FIX_SYSTEM, model=FIX_MODEL, max_tokens=FIX_MAX_TOKENS)
    parsed = _extract_json(raw) if raw else None
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# Xem trước
# ---------------------------------------------------------------------------

def _group_locations(locations: List[Location], findings_by_id: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Gộp các lỗi neo vào chuỗi chung (union-find theo đường dẫn) → mỗi nhóm = 1 đơn vị gửi LLM, để 2 lỗi cùng 1
    đoạn được sửa trong cùng 1 lượt (không ghi đè nhau)."""
    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for loc in locations:
        for p in loc.paths[1:]:
            parent[find(p)] = find(loc.paths[0])
        find(loc.paths[0])
    groups: Dict[str, Dict[str, Any]] = {}
    for loc in locations:
        root = find(loc.paths[0])
        g = groups.setdefault(root, {"paths": [], "finding_ids": []})
        for p in loc.paths:
            if p not in g["paths"]:
                g["paths"].append(p)
        g["finding_ids"].append(loc.finding_id)
    out = []
    for n, g in enumerate(groups.values(), start=1):
        out.append({"target": f"T{n}", "paths": g["paths"],
                    "findings": [findings_by_id[i] for i in g["finding_ids"]]})
    return out


def _target_path(group: Dict[str, Any], target: Any) -> Optional[str]:
    target = str(target or "").strip()
    if len(group["paths"]) == 1 and target in (group["target"], f"{group['target']}.1"):
        return group["paths"][0]
    m = re.fullmatch(re.escape(group["target"]) + r"\.(\d+)", target)
    if m and 1 <= int(m.group(1)) <= len(group["paths"]):
        return group["paths"][int(m.group(1)) - 1]
    return None


def apply_llm_result(group: Dict[str, Any], texts: Dict[str, str], result: Optional[Dict[str, Any]]
                     ) -> Tuple[Dict[str, Dict[str, Any]], Dict[int, str]]:
    """Kiểm + áp kết quả LLM của 1 nhóm. → ({path: {"after", "edits", "rejected"}}, {finding_id: lý do bỏ qua})."""
    ids = {f["id"] for f in group["findings"]}
    skipped: Dict[int, str] = {}
    if result is None:
        return {}, {i: "Không nhận được đề xuất hợp lệ từ AI (lỗi gọi/parse) — thử lại hoặc sửa tay." for i in ids}
    for s in result.get("skipped") or []:
        if isinstance(s, dict) and s.get("finding_id") in ids:
            skipped[s["finding_id"]] = str(s.get("reason") or "AI không sửa")[:300]

    allowed = set()
    for f in group["findings"]:
        for key in ("expected_text", "evidence", "current_text", "title"):
            allowed |= number_keys(f.get(key) or "")
    for p in group["paths"]:
        allowed |= number_keys(texts[p])

    by_path: Dict[str, List[Dict[str, Any]]] = {}
    orphan: List[Dict[str, Any]] = []
    for e in result.get("edits") or []:
        if not isinstance(e, dict):
            continue
        e = {**e, "finding_ids": [i for i in (e.get("finding_ids") or []) if isinstance(i, int)]}
        path = _target_path(group, e.get("target"))
        if path is None and len(group["paths"]) > 1 and isinstance(e.get("old"), str):
            # model quên ghi số thứ tự đoạn → nhận nếu `old` chỉ thuộc đúng 1 đoạn của khối
            owners = [p for p in group["paths"] if e["old"] in texts[p]]
            path = owners[0] if len(owners) == 1 else None
        if path is None:
            orphan.append({**e, "reason": "không xác định được đoạn đích"})
            continue
        by_path.setdefault(path, []).append(e)

    out: Dict[str, Dict[str, Any]] = {}
    for path, edits in by_path.items():
        after, accepted, rejected = validate_edits(texts[path], edits, allowed, ids)
        if accepted or rejected:
            out[path] = {"after": after if accepted else texts[path], "edits": accepted, "rejected": rejected}
    if orphan:
        first = group["paths"][0]
        out.setdefault(first, {"after": texts[first], "edits": [], "rejected": []})["rejected"].extend(orphan)
    return out, skipped


def _jack_key(issue: Dict[str, Any]) -> Tuple[str, str, str]:
    return issue.get("check", ""), issue.get("field_path") or "", issue.get("message", "")


def _jack_regressions(before: List[Dict[str, Any]], after: List[Dict[str, Any]], path: str) -> List[str]:
    """Lỗi/cảnh báo Jack MỚI xuất hiện ngay tại `path` (hoặc khối cha/con của nó) sau khi sửa."""
    old = {_jack_key(i) for i in before}
    out = []
    for it in after:
        if it.get("severity") == "info" or _jack_key(it) in old:
            continue
        fp = normalize_path(it.get("field_path")) or ""
        if fp and (_under(path, fp) or _under(fp, path)):
            out.append(it["message"])
    return out


async def _latest_review(session: AsyncSession, report_date: str, review_id: Optional[int]) -> ReportQcReview:
    stmt = select(ReportQcReview).where(ReportQcReview.report_date == report_date)
    if review_id is not None:
        stmt = stmt.where(ReportQcReview.id == review_id)
    review = (await session.execute(stmt.order_by(ReportQcReview.run_no.desc()).limit(1))).scalars().first()
    if review is None:
        raise QcFixError("Không có biên bản QC cho báo cáo này.")
    return review


async def _draft_report(session: AsyncSession, report_date: str) -> Report:
    report = (await session.execute(select(Report).where(Report.report_date == report_date))).scalars().first()
    if report is None:
        raise QcFixError("Không tìm thấy báo cáo.")
    if report.status != "draft" or not report.content:
        raise QcFixError("Chỉ sửa theo QC được khi báo cáo đang là bản nháp (draft).")
    return report


def _finding_view(f: ReportQcFinding) -> Dict[str, Any]:
    return {"id": f.id, "position": f.position, "severity": f.severity, "criterion": f.criterion,
            "section": f.section, "location": f.location, "field_path": f.field_path, "title": f.title,
            "current_text": f.current_text, "expected_text": f.expected_text, "evidence": f.evidence,
            "status": f.status}


async def build_fix_preview(session: AsyncSession, report_date: str, finding_ids: Optional[List[int]] = None,
                            review_id: Optional[int] = None) -> Dict[str, Any]:
    """Đề xuất sửa (KHÔNG ghi DB). `finding_ids` trống = mọi lỗi còn mở của biên bản mới nhất."""
    from services.report_qc import run_report_qc  # tránh vòng import lúc khởi động

    report = await _draft_report(session, report_date)
    review = await _latest_review(session, report_date, review_id)
    content = report.content
    base_hash = content_hash(content)
    rows = (await load_findings(session, [review.id]))[review.id]
    if finding_ids:
        wanted = set(finding_ids)
        rows = [f for f in rows if f.id in wanted]
    findings = [_finding_view(f) for f in rows]
    by_id = {f["id"]: f for f in findings}

    leaves = editable_leaves(content)
    outcomes: Dict[int, Dict[str, Any]] = {}
    locations: List[Location] = []
    for f in findings:
        why = is_fixable(f)
        if why:
            outcomes[f["id"]] = {"status": "manual", "reason": why}
            continue
        loc = locate_finding(f, leaves)
        if loc.reason:
            outcomes[f["id"]] = {"status": "manual", "reason": loc.reason}
        else:
            locations.append(loc)

    groups = _group_locations(locations, by_id) if locations else []
    for g in groups:
        g["texts"] = {p: leaves[p] for p in g["paths"]}
    batches = [groups[i:i + TARGETS_PER_CALL] for i in range(0, len(groups), TARGETS_PER_CALL)]
    sem = asyncio.Semaphore(LLM_CONCURRENCY)

    async def _run(batch: List[Dict[str, Any]]) -> List[Optional[Dict[str, Any]]]:
        async with sem:
            # Mỗi nhóm có tiền tố target riêng trong 1 lượt gọi; tách kết quả về từng nhóm theo tiền tố.
            result = await _call_fix_llm(build_fix_prompt(batch))
        if result is None:
            return [None] * len(batch)
        per = []
        for g in batch:
            pre = g["target"]
            ids = {f["id"] for f in g["findings"]}
            per.append({
                "edits": [e for e in result.get("edits") or []
                          if isinstance(e, dict) and re.fullmatch(re.escape(pre) + r"(?:\.\d+)?", str(e.get("target") or "").strip())],
                "skipped": [s for s in result.get("skipped") or [] if isinstance(s, dict) and s.get("finding_id") in ids],
            })
        return per

    results = [r for batch_res in await asyncio.gather(*(_run(b) for b in batches)) for r in batch_res]

    candidate = copy.deepcopy(content)
    changes: List[Dict[str, Any]] = []
    for g, res in zip(groups, results):
        per_path, skipped = apply_llm_result(g, g["texts"], res)
        touched: Set[int] = set()
        for path in g["paths"]:
            info = per_path.get(path)
            if not info:
                continue
            for r in info["rejected"]:
                logger.info("[QC-FIX] %s %s — loại đề xuất: %s", report_date, path, r["reason"])
            if not info["edits"]:
                continue
            set_at(candidate, path, info["after"])
            ids = sorted({i for e in info["edits"] for i in e["finding_ids"]})
            touched.update(ids)
            changes.append({"path": path, "before": g["texts"][path], "after": info["after"], "finding_ids": ids,
                            "edits": info["edits"], "rejected": [{"old": r.get("old"), "new": r.get("new"),
                                                                  "reason": r["reason"]} for r in info["rejected"]]})
        for f in g["findings"]:
            if f["id"] in touched:
                outcomes[f["id"]] = {"status": "proposed", "reason": None}
            else:
                reasons = [r["reason"] for path in g["paths"] for r in (per_path.get(path) or {}).get("rejected", [])
                           if f["id"] in (r.get("finding_ids") or [])]
                outcomes[f["id"]] = {
                    "status": "rejected" if reasons else "skipped",
                    "reason": ("Đề xuất của AI bị loại khi kiểm: " + "; ".join(dict.fromkeys(reasons))) if reasons
                    else skipped.get(f["id"], "AI không đưa ra thay thế cho lỗi này."),
                }

    # Chuẩn hóa trình bày giống bộ sinh — CHỈ nhận kết quả ở các đoạn vừa sửa, không đụng phần còn lại.
    if changes:
        normalized = copy.deepcopy(candidate)
        apply_consistency_fixes(normalized)
        for ch in changes:
            try:
                ch["after"] = get_at(normalized, ch["path"])
            except KeyError:
                pass
            set_at(candidate, ch["path"], ch["after"])
        changes = [ch for ch in changes if ch["after"] != ch["before"]]

    jack_before = jack_after = None
    if changes:
        try:
            jb = await run_report_qc(session, report_date, content)
            ja = await run_report_qc(session, report_date, candidate)
            jack_before, jack_after = int(jb["overall_score"]), int(ja["overall_score"])
            for ch in changes:
                ch["jack_regressions"] = _jack_regressions(jb["issues"], ja["issues"], ch["path"])
        except Exception:  # Jack lỗi không chặn xem trước — chỉ mất phần đối chiếu
            logger.exception("[QC-FIX] Jack rule-based lỗi khi đối chiếu %s", report_date)
    for n, ch in enumerate(changes, start=1):
        ch["id"] = n
        ch.setdefault("jack_regressions", [])
        ch["selected"] = not ch["jack_regressions"]

    logger.info("[QC-FIX] Xem trước %s (biên bản #%d): %d thay đổi, %d lỗi đề xuất / %d lỗi.",
                report_date, review.run_no, len(changes),
                sum(1 for o in outcomes.values() if o["status"] == "proposed"), len(findings))
    return {
        "report_date": report_date,
        "review_id": review.id,
        "run_no": review.run_no,
        "base_hash": base_hash,
        "review_is_stale": review.content_hash != base_hash,
        "changes": changes,
        "findings": [{"id": f["id"], "title": f["title"], "severity": f["severity"], "section": f["section"],
                      **outcomes.get(f["id"], {"status": "manual", "reason": "—"})} for f in findings],
        "jack_score_before": jack_before,
        "jack_score_after": jack_after,
    }


# ---------------------------------------------------------------------------
# Áp dụng / hoàn tác
# ---------------------------------------------------------------------------

async def apply_fix_changes(session: AsyncSession, report_date: str, base_hash: str, changes: List[Dict[str, Any]],
                            finding_status: str, actor: str, run_no: Optional[int] = None) -> Dict[str, Any]:
    """Ghi các thay đổi đã chọn. Từ chối nếu báo cáo đã đổi kể từ lúc xem trước (`base_hash`) hoặc `before` của 1
    đoạn không còn khớp — tránh ghi đè bản admin vừa sửa tay. `finding_status`: "fixed" (áp dụng) | "open" (hoàn tác)."""
    if finding_status not in ("fixed", "open"):
        raise QcFixError("finding_status ∈ (fixed, open)")
    if not changes:
        raise QcFixError("Chưa chọn thay đổi nào.")
    report = await _draft_report(session, report_date)
    if content_hash(report.content) != base_hash:
        raise QcFixError("Báo cáo đã thay đổi sau khi xem trước — tạo lại đề xuất sửa.")
    leaves = editable_leaves(report.content)
    new_content = copy.deepcopy(report.content)
    seen: Set[str] = set()
    for ch in changes:
        path = normalize_path(ch.get("path"))
        if not path or path in seen:
            raise QcFixError(f"Đường dẫn không hợp lệ hoặc trùng: {ch.get('path')}")
        seen.add(path)
        if path not in leaves or leaves[path] != ch.get("before"):
            raise QcFixError(f"Đoạn `{path}` không còn khớp bản xem trước — tạo lại đề xuất sửa.")
        if not isinstance(ch.get("after"), str):
            raise QcFixError(f"Thiếu nội dung mới cho `{path}`.")
        set_at(new_content, path, ch["after"])

    report.content = new_content  # gán object MỚI — SQLAlchemy không tự nhận thay đổi lồng trong JSONB
    await session.commit()

    finding_ids = sorted({int(i) for ch in changes for i in (ch.get("finding_ids") or []) if isinstance(i, int)})
    stamp = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    note = (f"Tự sửa theo QC{f' #{run_no}' if run_no else ''} bởi {actor} ({stamp})" if finding_status == "fixed"
            else None)
    own_ids = set((await session.execute(
        select(ReportQcFinding.id).join(ReportQcReview, ReportQcReview.id == ReportQcFinding.review_id)
        .where(ReportQcReview.report_date == report_date, ReportQcFinding.id.in_(finding_ids or [-1]))
    )).scalars())
    for fid in finding_ids:
        if fid in own_ids:  # chỉ lỗi thuộc biên bản của CHÍNH báo cáo này
            await update_finding_status(session, fid, finding_status, note)
    logger.info("[QC-FIX] %s %s: %d đoạn, lỗi %s → %s.", actor, report_date, len(changes), finding_ids, finding_status)
    return {"content": new_content, "content_hash": content_hash(new_content), "finding_ids": finding_ids,
            "changed_paths": sorted(seen)}
