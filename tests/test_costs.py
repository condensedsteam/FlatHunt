import pytest

from flatcalc.costs import Scenario, funding, ltv_band_hint, stamp_duty, year1_breakdown
from flatcalc.montecarlo import simulate, summarise


@pytest.mark.parametrize("price,ftb,expected", [
    (300_000, True, 0), (400_000, True, 5_000), (430_000, True, 6_500), (500_000, True, 10_000),
    (500_001, True, 15_000.05),          # the £500k cliff: relief disappears entirely
    (525_000, True, 16_250), (430_000, False, 11_500), (125_000, False, 0),
])
def test_stamp_duty(price, ftb, expected):
    assert stamp_duty(price, ftb) == pytest.approx(expected)


def test_funding_derives_deposit_from_cash():
    f = funding(Scenario(price=500_000, cash=150_000, buffer=10_000, buy_fees=3_000))
    assert f.deposit == 127_000 and f.loan == 373_000 and f.cash_short == 0


def test_cash_short_with_fixed_deposit():
    f = funding(Scenario(price=430_000, cash=120_000, buffer=0, deposit_override=120_000))
    assert f.cash_short == pytest.approx(9_500)


def test_year1_matches_earlier_analysis():
    # £500k share of freehold, no refix inside the hold: ~£2,060 payment, ~£2,792 money gone
    b = year1_breakdown(Scenario(price=500_000, service_charge=1_500, fix_years=30, erc_pct=0))
    assert b["payment"] == pytest.approx(2_060, abs=2)
    assert b["cash_out"] == pytest.approx(2_285, abs=2)
    assert b["money_gone"] == pytest.approx(2_792, abs=3)


def test_erc_added_when_selling_inside_fix():
    base = Scenario(price=500_000, hold_years=3, fix_years=5, erc_pct=0.03)
    no_erc = Scenario(**{**base.to_dict(), "erc_pct": 0})
    assert year1_breakdown(base)["one_off_total"] > year1_breakdown(no_erc)["one_off_total"]


def test_monte_carlo_odds_match_earlier_run():
    s = Scenario(price=500_000, service_charge=1_500, fix_years=30, erc_pct=0)
    out = summarise(simulate(s, max_years=5))
    assert out[2]["p_buy_wins"] == pytest.approx(0.53, abs=0.02)
    assert out[4]["p_buy_wins"] == pytest.approx(0.64, abs=0.02)


def test_longer_holds_favour_buying():
    out = summarise(simulate(Scenario(price=430_000), max_years=10, n=5_000))
    assert out[-1]["p_buy_wins"] > out[0]["p_buy_wins"]


def test_ltv_hint():
    assert ltv_band_hint(525_000, 405_000) is not None
    assert ltv_band_hint(500_000, 373_000) is None
