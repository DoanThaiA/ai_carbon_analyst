"""Tiền đề sai "TTF/điện Đức tăng → chuyển sang than → cầu EUA tăng" khi chuyển đổi khí→than đã bão hòa (bản chấm
20/10/2026). Số liệu bão hòa dùng lại của báo cáo 17/10: EUA 85,41, TTF 87,55, API2 152,25 USD/t."""
from services import eua_causal_chains as chains
from services import quote_chat
from services import report_generator as rg
from services import report_qc

SATURATED = [
    {"code": "EUA", "close": 85.41, "week_change_pct": -1.76},
    {"code": "TTF", "close": 87.55, "week_change_pct": 7.51},
    {"code": "API2", "close": 152.25, "week_change_pct": 3.75},
]
# TTF 44 / API2 110 đưa chênh khí − than về sát hòa vốn ở EUA 85.
NEAR = [
    {"code": "EUA", "close": 85.0, "week_change_pct": 0.5},
    {"code": "TTF", "close": 44.0, "week_change_pct": 2.0},
    {"code": "API2", "close": 110.0, "week_change_pct": 1.0},
]

WRONG = "TTF tăng 7,5% trong tuần khiến các nhà máy chuyển sang than, đẩy cầu EUA tăng."
WRONG_POWER = "Điện Đức tăng theo TTF → dispatch dịch sang than → hỗ trợ giá EUA."
RIGHT = "TTF tăng 7,5% nhưng chuyển đổi sang than đã bão hòa nên trung lập với cầu EUA."


def test_regime_detection():
    assert rg.fuel_regime(rg.fuel_spread_state(SATURATED)) == "saturated"
    assert rg.fuel_regime(rg.fuel_spread_state(NEAR)) == "near"
    assert rg.fuel_regime(None) is None


def test_framework_requires_regime_step_before_coal_chain():
    fs = chains.FUEL_SWITCHING
    assert fs.index("(0) BƯỚC BẮT BUỘC") < fs.index("(1) CHỈ ở vùng SÁT HÒA VỐN")
    assert "BÃO HÒA" in fs and "Gas↑ thêm KHÔNG tạo thêm coal burn" in fs
    # Các kênh nối vào FUEL_SWITCHING đều phải điều kiện theo vùng.
    for block in (chains.POWER_EUA_TWO_WAY, chains.OIL_GASOIL, chains.GEOPOLITICS_SUPPLY_CHAIN,
                  chains.RELATIVE_FUEL_ECONOMICS, chains.CONFLICT_RESOLUTION):
        assert "bão hòa" in block.lower() or "BÃO HÒA" in block


def test_jenny_gets_regime_brief():
    brief = rg.fuel_regime_brief(SATURATED)
    assert "ĐÃ BÃO HÒA" in brief and "TRUNG LẬP" in brief and "TTF GIẢM" in brief
    assert "SÁT điểm hòa vốn" in rg.fuel_regime_brief(NEAR)
    assert "thiếu giá" in rg.fuel_regime_brief([{"code": "EUA", "close": 85.0}])
    assert "ĐÃ BÃO HÒA" in quote_chat._format_section2({"prices": SATURATED})
    assert "bước (0)" in quote_chat._build_domain_knowledge()


def test_jack_flags_premise_across_sections_when_saturated():
    content = {
        "1": {"bullets": [{"text": WRONG}, {"text": RIGHT}]},
        "2": {"prices": SATURATED},
        "3": {
            "analysis_blocks": [{"heading": "Phân tích", "content": WRONG_POWER}],
            "trading_scenarios": [{"horizon": "ngắn hạn", "condition": "Nếu TTF tiếp tục tăng → chuyển sang than → EUA tăng."}],
        },
    }
    paths = [i["field_path"] for i in report_qc.check_coal_switch_premise(content)]
    assert paths == ["1.bullets[0].text", "3.analysis_blocks[0].content", "3.trading_scenarios[0].condition"]
    assert [i["field_path"] for i in report_qc.check_coal_switch_premise(content, section3_only=True)] == paths[1:]
    # Sát hòa vốn: chuỗi này là đúng cơ chế.
    content["2"]["prices"] = NEAR
    assert report_qc.check_coal_switch_premise(content) == []


def test_jack_flags_power_and_coal_drivers_when_saturated():
    content = {"2": {"prices": SATURATED, "market_drivers": {"bullish": [
        {"text": "Giá điện Đức (DEBY1) tăng 4% trong tuần"},
        {"text": "Than cạnh tranh hơn khí, hỗ trợ cầu EUA"},
        {"text": "Điện Đức tăng do gió thấp, cần thêm điện nhiệt"},
    ], "bearish": []}}}
    issues = report_qc.check_fuel_direction(content)
    assert [i["field_path"] for i in issues] == ["2.market_drivers.bullish[0].text", "2.market_drivers.bullish[1].text"]
