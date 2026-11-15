"""Deterministic cost model: stamp duty, funding, and year-1 monthly breakdown.

All figures are nominal £. England / Northern Ireland SDLT rates from 1 April 2025.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# (upper bound of band, rate) — standard residential rates
SDLT_STANDARD = [(125_000, 0.00), (250_000, 0.02), (925_000, 0.05),
                 (1_500_000, 0.10), (float("inf"), 0.12)]
# First-time buyer relief, only if price <= £500k and every buyer is a FTB
SDLT_FTB = [(300_000, 0.00), (500_000, 0.05)]
FTB_PRICE_CAP = 500_000

LTV_BANDS = (0.60, 0.75, 0.80, 0.85, 0.90)


def _banded(price: float, bands) -> float:
    tax, lower = 0.0, 0.0
    for upper, rate in bands:
        if price > lower:
            tax += (min(price, upper) - lower) * rate
        lower = upper
    return tax


def stamp_duty(price: float, first_time_buyers: bool = True) -> float:
    """SDLT for a main residence (no additional-property surcharge)."""
    if first_time_buyers and price <= FTB_PRICE_CAP:
        return _banded(price, SDLT_FTB)
    return _banded(price, SDLT_STANDARD)


def monthly_payment(principal: float, annual_rate: float, months: int) -> float:
    if principal <= 0:
        return 0.0
    r = annual_rate / 12
    if r == 0:
        return principal / months
    return principal * r / (1 - (1 + r) ** -months)


@dataclass
class Scenario:
    """Everything the cost model needs about one listing + your situation."""
    # listing
    price: float = 500_000
    service_charge: float = 1_500          # £/yr
    ground_rent: float = 0                 # £/yr
    maintenance: float = 100               # £/month
    # your situation
    cash: float = 150_000
    buffer: float = 10_000                 # cash kept back, not invested in the flat
    deposit_override: float | None = None  # set to fix the deposit instead of deriving it
    first_time_buyers: bool = True
    buy_fees: float = 3_000                # legal + survey
    sell_cost_pct: float = 0.0175
    # mortgage
    rate: float = 0.0525
    term_years: int = 30
    fix_years: int = 5
    refix_rate: float = 0.05
    erc_pct: float = 0.03                  # early repayment charge if sold inside the fix
    # renting alternative and markets
    rent: float = 2_500                    # £/month for the flat you'd rent instead
    invest_return: float = 0.05
    service_charge_growth: float = 0.03
    hold_years: int = 5

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Funding:
    stamp_duty: float
    upfront_costs: float      # SDLT + fees
    deposit: float
    loan: float
    ltv: float
    cash_short: float         # > 0 if cash can't cover deposit + costs + buffer
    notes: list[str] = field(default_factory=list)


def funding(s: Scenario) -> Funding:
    sd = stamp_duty(s.price, s.first_time_buyers)
    upfront = sd + s.buy_fees
    available = s.cash - upfront - s.buffer
    deposit = s.deposit_override if s.deposit_override is not None else max(available, 0.0)
    deposit = min(deposit, s.price)
    loan = s.price - deposit
    short = max(deposit + upfront + s.buffer - s.cash, 0.0)
    return Funding(sd, upfront, deposit, loan, loan / s.price if s.price else 0.0, short)


def year1_breakdown(s: Scenario) -> dict:
    """Year-1 monthly figures in three layers: payment, cash out, money gone."""
    f = funding(s)
    months = s.term_years * 12
    pay = monthly_payment(f.loan, s.rate, months)
    bal, interest = f.loan, 0.0
    for _ in range(12):
        i = bal * s.rate / 12
        interest += i
        bal -= pay - i
    interest /= 12
    capital = pay - interest
    svc = s.service_charge / 12
    gr = s.ground_rent / 12
    running = svc + gr + s.maintenance
    cash_out = pay + running
    tied_up = f.deposit + f.upfront_costs
    opportunity = tied_up * s.invest_return / 12
    one_off = f.upfront_costs + s.price * s.sell_cost_pct
    if s.hold_years < s.fix_years:
        one_off += f.loan * s.erc_pct  # approx: ERC on the opening balance
    one_off_pm = one_off / (s.hold_years * 12)
    gone = interest + running + opportunity + one_off_pm
    return {
        "payment": pay, "interest": interest, "capital": capital,
        "service_charge": svc, "ground_rent": gr, "maintenance": s.maintenance,
        "running": running, "cash_out": cash_out,
        "opportunity": opportunity, "one_off_total": one_off, "one_off_pm": one_off_pm,
        "money_gone": gone, "rent": s.rent,
        "cash_out_vs_rent": cash_out - s.rent, "gone_vs_rent": gone - s.rent,
    }


def ltv_band_hint(price: float, loan: float, margin: float = 0.03) -> str | None:
    """If LTV sits just above a common pricing band, say how much more deposit gets under it."""
    ltv = loan / price
    for band in LTV_BANDS:
        if band < ltv <= band + margin:
            extra = loan - band * price
            return (f"LTV is {ltv:.1%}, just above the {band:.0%} band. "
                    f"About £{extra:,.0f} more deposit would get you under it, "
                    f"which usually means a cheaper rate.")
    return None
