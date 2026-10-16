from services import eua_causal_chains as chains
from services import quote_chat as qc


def test_framework_blocks_moved_out_of_static_prompt():
    static = qc._build_static_instructions("2026-10-15")
    for bid in qc.FRAMEWORK_ON_DEMAND_BLOCKS:
        # NON_EUA_CARBON_MARKETS trùng nội dung luật L11 trong INFERENCE_RULES (vẫn giữ) — bỏ qua.
        if bid != "NON_EUA_CARBON_MARKETS":
            assert chains.get_block(bid).strip()[:80] not in static, bid
        assert f"- {bid}: " in static  # mục lục để model biết tra kênh nào
    # Lõi suy luận vẫn luôn có sẵn.
    for bid in ("INFERENCE_RULES", "FUEL_SWITCHING", "CONFLICT_RESOLUTION"):
        assert chains.get_block(bid).strip()[:80] in static, bid


def test_framework_tool_returns_blocks_with_admin_overrides():
    text = qc._framework_tool_text({"blocks": ["OIL_GASOIL", "MACRO", "KHONG_CO"]}, {"MACRO": "MACRO tuỳ chỉnh"})
    assert chains.get_block("OIL_GASOIL")[:60] in text and "MACRO tuỳ chỉnh" in text
    assert "Mã hợp lệ" in qc._framework_tool_text({"blocks": ["INFERENCE_RULES"]})


def test_framework_tool_never_exposed_to_mcp():
    """Khung phân tích EUA là nội bộ — gateway Claude Desktop/mobile chỉ phục vụ CLIENT_TOOLS + jack_qc."""
    from api.routers import mcp_gateway

    assert qc.FRAMEWORK_TOOL["name"] not in {t["name"] for t in qc.CLIENT_TOOLS}
    assert qc.FRAMEWORK_TOOL["name"] not in mcp_gateway._TOOLS_BY_NAME


def test_cache_ttl_setting(monkeypatch):
    from core.config import Settings

    monkeypatch.setenv("QUOTE_CHAT_CACHE_TTL", "5m")
    assert Settings.from_env().quote_chat_cache_ttl == "5m"
    monkeypatch.setenv("QUOTE_CHAT_CACHE_TTL", "bất kỳ")
    assert Settings.from_env().quote_chat_cache_ttl == "1h"
