"""Monte Carlo of buy vs rent net wealth.

Each path draws correlated annual house price growth, investment return and rent
growth. Each month, whoever has the lower outgoings invests the difference. At each
year-end we value the buyer's position as if they sold then (selling costs, plus an
early repayment charge if still inside the fix), so one run gives the result for
every hold length up to `max_years`.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .costs import Scenario, funding, monthly_payment


@dataclass
class Market:
    house_mu: float = 0.015
    house_sd: float = 0.07
    invest_mu: float = 0.05
    invest_sd: float = 0.12
    rent_mu: float = 0.035
    rent_sd: float = 0.02
    corr_house_invest: float = 0.3
    corr_house_rent: float = 0.4
    corr_invest_rent: float = 0.1


def simulate(s: Scenario, mkt: Market | None = None, max_years: int = 10,
             n: int = 20_000, seed: int = 1) -> np.ndarray:
    """Return gap = buy wealth - rent wealth, shape (max_years, n); row y-1 = sell after y years."""
    mkt = mkt or Market()
    f = funding(s)
    rng = np.random.default_rng(seed)
    corr = np.array([[1, mkt.corr_house_invest, mkt.corr_house_rent],
                     [mkt.corr_house_invest, 1, mkt.corr_invest_rent],
                     [mkt.corr_house_rent, mkt.corr_invest_rent, 1]], dtype=float)
    z = rng.standard_normal((n, max_years, 3)) @ np.linalg.cholesky(corr).T
    g_house = mkt.house_mu + mkt.house_sd * z[..., 0]
    g_inv = np.maximum(mkt.invest_mu + mkt.invest_sd * z[..., 1], -0.95)
    g_rent = mkt.rent_mu + mkt.rent_sd * z[..., 2]

    term = s.term_years * 12
    rate = s.rate
    pay = monthly_payment(f.loan, rate, term)
    bal = f.loan  # deterministic: same for all paths
    home = np.full(n, float(s.price))
    owner_pot = np.zeros(n)
    renter_pot = np.full(n, f.deposit + f.upfront_costs)
    rent_m = np.full(n, float(s.rent))
    gaps = np.empty((max_years, n))

    month = 0
    for y in range(max_years):
        mh = (1 + g_house[:, y]) ** (1 / 12) - 1
        mi = (1 + g_inv[:, y]) ** (1 / 12) - 1
        running = (s.service_charge / 12) * (1 + s.service_charge_growth) ** y \
            + s.ground_rent / 12 + s.maintenance
        for _ in range(12):
            if month == s.fix_years * 12 and bal > 0:   # refix on the remaining term
                rate = s.refix_rate
                pay = monthly_payment(bal, rate, term - month)
            if bal > 0:
                bal -= pay - bal * rate / 12
                own_m = pay + running
            else:
                own_m = running
            home *= 1 + mh
            owner_pot *= 1 + mi
            renter_pot *= 1 + mi
            diff = rent_m - own_m
            owner_pot += np.maximum(diff, 0)
            renter_pot += np.maximum(-diff, 0)
            month += 1
        rent_m = rent_m * (1 + g_rent[:, y])
        erc = bal * s.erc_pct if (y + 1) < s.fix_years else 0.0
        buy = home * (1 - s.sell_cost_pct) - max(bal, 0) - erc + owner_pot
        gaps[y] = buy - renter_pot
    return gaps


def summarise(gaps: np.ndarray) -> list[dict]:
    """Per hold length: P(buy wins), median and 10th/90th percentile gap."""
    out = []
    for y, g in enumerate(gaps, start=1):
        p10, p50, p90 = np.percentile(g, [10, 50, 90])
        out.append({"hold_years": y, "p_buy_wins": float(np.mean(g > 0)),
                    "median": float(p50), "p10": float(p10), "p90": float(p90)})
    return out
