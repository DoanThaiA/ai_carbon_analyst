"""Giá CBAM lưu DB: không được kéo lệch ngày giá của các mã khác, và tra được as-of ngày."""
import asyncio

import pytest

from services import report_generator as rg


class FakeResult:
    def __init__(self, v): self.v = v
    def first(self): return self.v
    def scalar_one_or_none(self): return self.v


class FakeSession:
    def __init__(self, row): self.row = row
    async def execute(self, _stmt): return FakeResult(self.row)


class P:  # Price/Instrument giả
    close_price = 82.32; price_date = "2026-10-05"; note = "Giá chốt theo quý"
    name = "CBAM Certificate"; unit = "EUR/tCO2"; category = "carbon"


def test_stored_cbam_price_dict_has_no_deltas():
    out = asyncio.run(rg._get_stored_cbam_price(FakeSession((P, P)), "2026-10-06"))
    assert out["price"] == "82.32 EUR/tCO2" and out["close"] == 82.32
    assert out["day_change_pct"] is None and out["week_change_pct"] is None
    assert out["code"] == "CBAM"


def test_stored_cbam_price_none_when_missing():
    assert asyncio.run(rg._get_stored_cbam_price(FakeSession(None), "2026-10-06")) is None
