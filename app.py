"""Flat evaluator: enter a listing, see commute times, monthly costs and buy-vs-rent odds.

Run locally:  streamlit run app.py
"""
from __future__ import annotations

import json
import os
from datetime import date

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from flatcalc import commute as cm
from flatcalc.costs import (FTB_PRICE_CAP, Scenario, funding, ltv_band_hint, stamp_duty,
                            stamp_duty_bands, year1_breakdown)
from flatcalc.montecarlo import Market, simulate, summarise

st.set_page_config(page_title="Flat evaluator", page_icon="🏠", layout="wide")

# Streamlit Cloud: a top-level TFL_APP_KEY secret is also exposed as an env var,
# but read st.secrets explicitly in case it isn't.
if not os.environ.get("TFL_APP_KEY"):
    try:
        os.environ["TFL_APP_KEY"] = st.secrets["TFL_APP_KEY"]
    except Exception:
        pass

DEFAULT_OFFICES = pd.DataFrame([
    {"Person": "Me", "Office": "Moorgate", "Location": "51.5186,-0.0886", "Days/week": 3},
    {"Person": "Partner", "Office": "Chancery Lane", "Location": "51.5185,-0.1111", "Days/week": 3},
])


def gbp(x: float, dp: int = 0) -> str:
    return f"-£{abs(x):,.{dp}f}" if x < 0 else f"£{x:,.{dp}f}"


# ------------------------------------------------------------------ state
ss = st.session_state
ss.setdefault("offices", DEFAULT_OFFICES.copy())
ss.setdefault("shortlist", [])
ss.setdefault("result", None)


# ------------------------------------------------------------------ cached work
@st.cache_resource
def http_session():
    return cm.make_session()


@st.cache_data(show_spinner=False, ttl=7 * 24 * 3600)
def geocode_cached(q: str):
    return cm.geocode(q, http_session())


@st.cache_data(show_spinner=False, ttl=7 * 24 * 3600)
def commute_cached(origin, dest, day: date, start: str, window: int, step: int):
    times = cm.departure_times(day, start, window, step)
    return cm.robust_commute(origin, dest, times, http_session(), cm.tfl_key())


@st.cache_data(show_spinner=False)
def mc_cached(scenario: dict, market: dict, max_years: int):
    gaps = simulate(Scenario(**scenario), Market(**market), max_years=max_years)
    return summarise(gaps), gaps


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Your situation")
    cash = st.number_input("Cash available (£)", 0, 2_000_000, 150_000, 5_000)
    buffer = st.number_input("Emergency buffer kept back (£)", 0, 200_000, 10_000, 1_000)
    ftb = st.checkbox("Both first-time buyers", True)
    deposit_in = st.number_input("Deposit (£, 0 = all spare cash after costs + buffer)", 0, 2_000_000, 120_000, 5_000)
    fees = st.number_input("Legal, survey + mortgage product fees (£)", 0, 50_000, 3_995, 500)
    income = st.number_input("Combined gross income (£/yr, optional)", 0, 2_000_000, 0, 5_000,
                             help="Used only to flag the loan-to-income multiple.")

    st.header("Mortgage")
    rate = st.number_input("Initial rate (%)", 0.0, 15.0, 4.44, 0.01) / 100
    fix_years = st.selectbox("Initial deal length (years)", [2, 3, 5, 10], index=0)
    refix = st.number_input("Rate after the fix (%)", 0.0, 15.0, 5.0, 0.25) / 100
    erc = st.number_input("Early repayment charge if sold inside the fix (%)", 0.0, 10.0, 3.0, 0.5) / 100
    term = st.slider("Term (years)", 10, 40, 25)

    st.header("Renting instead")
    rent = st.number_input("Monthly rent of the alternative (£)", 0, 20_000, 2_500, 50)
    hold = st.slider("Expected years before selling", 1, 10, 4)

    with st.expander("Market assumptions"):
        inv_mu = st.number_input("Investment return (%/yr)", -5.0, 15.0, 5.0, 0.5) / 100
        inv_sd = st.number_input("Investment volatility (%)", 0.0, 40.0, 12.0, 1.0) / 100
        h_mu = st.number_input("House price growth (%/yr)", -10.0, 15.0, 1.5, 0.5) / 100
        h_sd = st.number_input("House price volatility (%)", 0.0, 30.0, 7.0, 0.5) / 100
        r_mu = st.number_input("Rent growth (%/yr)", -5.0, 15.0, 3.5, 0.5) / 100
        sc_g = st.number_input("Service charge growth (%/yr)", 0.0, 15.0, 3.0, 0.5) / 100
        sell_pct = st.number_input("Selling costs (% of price)", 0.0, 5.0, 1.75, 0.25) / 100

    st.header("Commute")
    day = st.date_input("Typical weekday", cm.next_weekday())
    start = st.time_input("Leave home from", value=pd.Timestamp("07:30").time(), step=300)
    window = st.slider("…across a window of (minutes)", 0, 60, 30, 10)
    if not cm.tfl_key():
        st.caption("⚠️ No TFL_APP_KEY set: lookups are rate-limited. See README.")

    st.header("Display")
    show_formulas = st.toggle("Show formulas", True,
                              help="Show how each figure is calculated, with your numbers filled in.")

    with st.expander("Save / load settings"):
        settings = {"offices": ss.offices.to_dict("records")}
        st.download_button("Download offices", json.dumps(settings, indent=2),
                           "flat_settings.json", "application/json")
        up = st.file_uploader("Load offices", type="json", key="settings_up")
        if up is not None and not ss.get("_settings_loaded"):
            ss.offices = pd.DataFrame(json.load(up)["offices"])
            ss._settings_loaded = True
            st.rerun()

market = Market(house_mu=h_mu, house_sd=h_sd, invest_mu=inv_mu, invest_sd=inv_sd, rent_mu=r_mu)


def explain(where, text: str) -> None:
    """Show a formula under a figure when 'Show formulas' is on."""
    if show_formulas:
        where.caption(f"🧮 {text}")

# ------------------------------------------------------------------ main
st.title("Flat evaluator")
st.caption("Commute, monthly cost and buy-vs-rent odds for one listing. "
           "A framework for your own decision, not financial advice.")

with st.expander("Offices and who commutes there", expanded=False):
    st.caption("Location: a full postcode or `lat,lon`. Days/week = 0 shows the commute "
               "but leaves it out of the weekly totals (handy for comparing job options).")
    ss.offices = st.data_editor(
        ss.offices, num_rows="dynamic", width="stretch", key="office_editor",
        column_config={"Days/week": st.column_config.NumberColumn(min_value=0, max_value=7, step=1)})

with st.form("listing"):
    c1, c2, c3 = st.columns(3)
    name = c1.text_input("Listing name or link", "£450k flat")
    postcode = c1.text_input("Postcode", "SE14 5JN")
    price = c2.number_input("Asking price (£)", 50_000, 5_000_000, 450_000, 5_000)
    tenure = c2.selectbox("Tenure", ["Leasehold", "Share of freehold", "Freehold"])
    lease = c2.number_input("Lease years remaining (0 if freehold)", 0, 999, 125)
    svc = c3.number_input("Service charge (£/yr)", 0, 50_000, 1_800, 100)
    ground = c3.number_input("Ground rent (£/yr)", 0, 20_000, 300, 50)
    sqft = c3.number_input("Floor area (sq ft, optional)", 0, 10_000, 0, 10)
    maint = c1.number_input("Maintenance allowance (£/month)", 0, 2_000, 100, 25)
    submitted = st.form_submit_button("Evaluate", type="primary")

if submitted:
    s = Scenario(price=price, service_charge=svc, ground_rent=ground, maintenance=maint,
                 cash=cash, buffer=buffer, deposit_override=deposit_in or None, first_time_buyers=ftb, buy_fees=fees,
                 sell_cost_pct=sell_pct, rate=rate, term_years=term, fix_years=fix_years,
                 refix_rate=refix, erc_pct=erc, rent=rent, service_charge_growth=sc_g,
                 invest_return=inv_mu, hold_years=hold)

    # commutes
    commute_rows, commute_err = [], None
    offices = ss.offices.dropna(subset=["Location"])
    if postcode.strip() and len(offices):
        try:
            with st.spinner("Looking up journeys on TfL…"):
                o_lat, o_lon, o_label = geocode_cached(postcode)
                for _, row in offices.iterrows():
                    rec = {"person": row["Person"], "office": row["Office"],
                           "days": int(row["Days/week"] or 0)}
                    try:
                        d_lat, d_lon, _ = geocode_cached(str(row["Location"]))
                        rec.update(commute_cached((o_lat, o_lon), (d_lat, d_lon), day,
                                                  start.strftime("%H:%M"), window, 10))
                    except Exception as e:  # keep other offices going
                        rec["error"] = str(e)
                    commute_rows.append(rec)
        except Exception as e:
            commute_err = str(e)

    ss.result = {"name": name, "postcode": postcode, "tenure": tenure, "lease": lease,
                 "sqft": sqft, "scenario": s.to_dict(), "income": income,
                 "commutes": commute_rows, "commute_err": commute_err}

res = ss.result
if res is None:
    st.info("Enter a listing and press **Evaluate**.")
    st.stop()

s = Scenario(**res["scenario"])
f = funding(s)
b = year1_breakdown(s)
summary, gaps = mc_cached(s.to_dict(), market.__dict__, max(10, s.hold_years))
at_hold = summary[s.hold_years - 1]

# ------------------------------------------------------------------ warnings
warns = []
if s.first_time_buyers and s.price > 500_000:
    warns.append(f"Over £500k, so no first-time buyer relief: stamp duty is {gbp(f.stamp_duty)} "
                 f"vs {gbp(stamp_duty(500_000))} at £500k. Getting to £500k saves "
                 f"{gbp(s.price - 500_000 + f.stamp_duty - stamp_duty(500_000))} up front.")
if f.cash_short > 0:
    warns.append(f"Cash is {gbp(f.cash_short)} short of deposit + costs + buffer.")
if f.deposit < 0.05 * s.price:
    warns.append("Deposit is under 5%, so most lenders won't lend.")
if hint := ltv_band_hint(s.price, f.loan):
    warns.append(hint)
if res["income"] and f.loan / res["income"] > 4.5:
    warns.append(f"Loan is {f.loan / res['income']:.1f}× income; most lenders cap at 4.5×.")
if res["tenure"] != "Freehold" and res["lease"]:
    if res["lease"] < 80:
        warns.append("Lease under 80 years: extending costs much more (marriage value) and many lenders refuse.")
    elif res["lease"] < 90:
        warns.append("Lease under 90 years: budget to extend it soon, and resale gets harder.")
if s.ground_rent > 0.001 * s.price:
    warns.append("Ground rent is above 0.1% of the price, which some lenders treat as onerous.")
if res["sqft"] and s.service_charge / res["sqft"] > 5:
    warns.append(f"Service charge is £{s.service_charge / res['sqft']:.2f}/sq ft/yr, which is high. Check the reserve fund and planned works.")
if s.hold_years < s.fix_years:
    warns.append(f"Selling after {s.hold_years} yrs inside a {s.fix_years}-yr fix triggers an early "
                 f"repayment charge (~{gbp(f.loan * s.erc_pct)}) unless you port the mortgage.")

st.subheader(res["name"])
for w in warns:
    st.warning(w, icon="⚠️")

m = st.columns(5)
m[0].metric("Stamp duty", gbp(f.stamp_duty))
m[1].metric("Deposit", gbp(f.deposit), f"{f.ltv:.1%} LTV", delta_color="off", delta_arrow="off")
m[2].metric("Cash out / month", gbp(b["cash_out"]), f"{gbp(b['cash_out_vs_rent'])} vs rent",
            delta_color="inverse")
m[3].metric("Money gone / month", gbp(b["money_gone"]), f"{gbp(b['gone_vs_rent'])} vs rent",
            delta_color="inverse")
m[4].metric(f"Buying wins ({s.hold_years}-yr hold)", f"{at_hold['p_buy_wins']:.0%}",
            f"median gap {gbp(at_hold['median'])}", delta_color="off", delta_arrow="off")

ftb_rates = s.first_time_buyers and s.price <= FTB_PRICE_CAP
explain(m[0], ("First-time buyer rates: " if ftb_rates else "Standard rates: ")
        + " + ".join(f"{rate:.0%} of {gbp(lo)}–{gbp(hi)}" for lo, hi, rate, _ in stamp_duty_bands(s.price, s.first_time_buyers))
        + f" = {gbp(f.stamp_duty)}")
if s.deposit_override is not None:
    dep_txt = f"Deposit set in the sidebar: {gbp(f.deposit)}."
else:
    dep_txt = (f"Cash {gbp(s.cash)} − stamp duty {gbp(f.stamp_duty)} − fees {gbp(s.buy_fees)} "
               f"− buffer {gbp(s.buffer)} = {gbp(f.deposit)} (not below £0).")
explain(m[1], f"{dep_txt} Loan = price {gbp(s.price)} − deposit = {gbp(f.loan)}. "
              f"LTV = loan ÷ price = {f.ltv:.1%}.")
explain(m[2], f"Mortgage payment {gbp(b['payment'])} + service charge {gbp(b['service_charge'])} "
              f"+ ground rent {gbp(b['ground_rent'])} + maintenance {gbp(b['maintenance'])} "
              f"= {gbp(b['cash_out'])}. Change = that − rent {gbp(s.rent)}.")
explain(m[3], f"Interest {gbp(b['interest'])} + service charge, ground rent and maintenance {gbp(b['running'])} "
              f"+ lost return {gbp(b['opportunity'])} + one-off costs share {gbp(b['one_off_pm'])} "
              f"= {gbp(b['money_gone'])}. Change = that − rent {gbp(s.rent)}. Itemised in Monthly costs.")
explain(m[4], f"Share of 20,000 simulated futures where the buyer ends up wealthier than the renter "
              f"after {s.hold_years} yrs. Median gap = buyer − renter wealth in the middle one. "
              "Wealth formulas are in the odds tab.")

tab_c, tab_m, tab_o = st.tabs(["Commute", "Monthly costs", "Buy vs rent odds"])

# ------------------------------------------------------------------ commute tab
with tab_c:
    if res["commute_err"]:
        st.error(f"Commute lookup failed: {res['commute_err']}")
    elif not res["commutes"]:
        st.info("Add a postcode and at least one office to see commutes.")
    else:
        df = pd.DataFrame(res["commutes"])
        cols = {"person": "Person", "office": "Office", "days": "Days/wk",
                "median_min": "Typical (min)", "worst_min": "Worst (min)",
                "fastest_min": "Fastest (min)", "changes": "Changes", "walk_min": "Walking (min)",
                "route": "Typical route", "error": "Error"}
        st.dataframe(df[[c for c in cols if c in df]].rename(columns=cols),
                     hide_index=True, width="stretch")
        st.caption(f"Door-to-door from leaving home, sampled every 10 min from "
                   f"{start.strftime('%H:%M')} over {window} min on {day:%a %d %b}. "
                   "'Typical' is the median, so waiting for infrequent trains counts.")
        explain(st, "Door-to-door = arrival time − the time you want to leave, so waiting for a train "
                    "counts. TfL is asked for its quickest route at each departure time. Typical = the "
                    "median of those door-to-door times, Worst = the longest. Fastest = the shortest TfL "
                    "journey time, which starts when you set off for the train, so it leaves out waiting. "
                    "Walking = every walking leg of the typical journey added together: to the station, "
                    "between platforms and to the office. Changes = rail/tube legs − 1. Buses aren't searched. "
                    "Home is the centre of the postcode, not the front door.")
        weekly = cm.weekly_summary(res["commutes"])
        if any(p["weekly_min"] for p in weekly):
            wc = st.columns(len(weekly) + 1)
            for col, p in zip(wc, weekly):
                col.metric(f"{p['person']}: hours/week", f"{p['weekly_min'] / 60:.1f}")
                parts = [f"2 × {c['days']} days × {c['median_min']:g} min ({c['office']})"
                         for c in res["commutes"] if c["person"] == p["person"]
                         and c.get("median_min") is not None and c["days"] > 0]
                if parts:
                    explain(col, f"({' + '.join(parts)}) ÷ 60. Typical times, there and back.")
            wc[-1].metric("Combined hours/week", f"{sum(p['weekly_min'] for p in weekly) / 60:.1f}")
            explain(wc[-1], "Sum of each person's hours/week.")

# ------------------------------------------------------------------ monthly tab
with tab_m:
    st.markdown("**Year 1, per month.** *Payment* is what the bank takes. *Cash out* is "
                "everything leaving your account. *Money gone* removes capital repayment (you "
                "get it back on sale) and adds the return your cash would have earned plus "
                f"one-off costs spread over {s.hold_years} years.")
    layers = pd.DataFrame({
        "Layer": ["Mortgage payment", "Cash out", "Money gone", "Rent instead"],
        "£/month": [b["payment"], b["cash_out"], b["money_gone"], b["rent"]],
    })
    c1, c2 = st.columns([3, 2])
    tied_up = f.deposit + f.upfront_costs
    erc_txt = (f" + early repayment charge {s.erc_pct:.1%} × loan {gbp(f.loan)} = {gbp(b['erc'])}"
               if b["erc"] else "")
    detail = pd.DataFrame([
        ["Mortgage interest", b["interest"], b["interest"], b["interest"],
         f"Average of the 12 monthly interest charges in year 1: balance × {s.rate:.2%} ÷ 12, "
         f"balance starting at {gbp(f.loan)}"],
        ["Capital repayment", b["capital"], b["capital"], 0,
         f"Payment {gbp(b['payment'])} − interest {gbp(b['interest'])}"],
        ["Service charge", 0, b["service_charge"], b["service_charge"],
         f"{gbp(s.service_charge)}/yr ÷ 12"],
        ["Ground rent", 0, b["ground_rent"], b["ground_rent"], f"{gbp(s.ground_rent)}/yr ÷ 12"],
        ["Maintenance", 0, b["maintenance"], b["maintenance"], "Your monthly allowance"],
        [f"Lost return on {gbp(tied_up)}", 0, 0, b["opportunity"],
         f"(Deposit {gbp(f.deposit)} + stamp duty and fees {gbp(f.upfront_costs)}) "
         f"× {s.invest_return:.1%} investment return ÷ 12"],
        [f"One-off costs {gbp(b['one_off_total'])} ÷ {s.hold_years * 12} months", 0, 0, b["one_off_pm"],
         f"(Stamp duty {gbp(f.stamp_duty)} + fees {gbp(s.buy_fees)} + selling costs "
         f"{s.sell_cost_pct:.2%} × price {gbp(s.price)} = {gbp(b['sell_costs'])}{erc_txt}) "
         f"÷ ({s.hold_years} yrs × 12)"],
    ], columns=["Item", "Payment", "Cash out", "Money gone", "How it's calculated"])
    total = detail[["Payment", "Cash out", "Money gone"]].sum()
    detail.loc[len(detail)] = ["Total", *total, "Sum of each column"]
    if not show_formulas:
        detail = detail.drop(columns="How it's calculated")
    c1.dataframe(detail.style.format({k: "£{:,.0f}" for k in ["Payment", "Cash out", "Money gone"]}),
                 hide_index=True, width="stretch")
    c2.altair_chart(alt.Chart(layers).mark_bar().encode(
        x=alt.X("£/month:Q"), y=alt.Y("Layer:N", sort=None, title=None),
        color=alt.condition(alt.datum.Layer == "Rent instead", alt.value("#c9792b"), alt.value("#3b6ea8")),
        tooltip=["Layer", alt.Tooltip("£/month:Q", format=",.0f")]), width="stretch")
    st.caption(f"Funding: {gbp(f.deposit)} deposit + {gbp(f.stamp_duty)} stamp duty + "
               f"{gbp(s.buy_fees)} fees, keeping {gbp(s.buffer)} back. Loan {gbp(f.loan)} at "
               f"{s.rate:.2%} for {s.fix_years} yrs, then {s.refix_rate:.2%}. "
               "House price changes are not included here; see the odds tab.")
    explain(st, f"Mortgage payment = loan × r ÷ (1 − (1 + r)^−n) = {gbp(b['payment'])}, where loan = "
                f"{gbp(f.loan)}, r = {s.rate:.2%} ÷ 12 and n = {s.term_years} yrs × 12 = {s.term_years * 12} "
                "months. Payment = interest + capital. Cash out = payment + service charge + ground rent "
                "+ maintenance. Money gone = interest + service charge + ground rent + maintenance + lost "
                "return + one-off costs share. Capital is left out because you get it back when you sell.")

# ------------------------------------------------------------------ odds tab
with tab_o:
    sm = pd.DataFrame(summary)
    c1, c2 = st.columns(2)
    c1.altair_chart(alt.Chart(sm).mark_line(point=True).encode(
        x=alt.X("hold_years:Q", title="Years before selling", axis=alt.Axis(tickMinStep=1)),
        y=alt.Y("p_buy_wins:Q", title="Chance buying beats renting", axis=alt.Axis(format="%"),
                scale=alt.Scale(domain=[0, 1])),
        tooltip=["hold_years", alt.Tooltip("p_buy_wins:Q", format=".0%")])
        + alt.Chart(pd.DataFrame({"y": [0.5]})).mark_rule(strokeDash=[4, 4], color="grey").encode(y="y"),
        width="stretch")
    g = gaps[s.hold_years - 1] / 1000
    lo, hi = np.percentile(g, [1, 99])
    counts, edges = np.histogram(np.clip(g, lo, hi), bins=50)
    hist = pd.DataFrame({"lo": edges[:-1], "hi": edges[1:], "n": counts,
                         "Outcome": np.where(edges[:-1] + edges[1:] > 0, "Buying ahead", "Renting ahead")})
    c2.altair_chart(alt.Chart(hist).mark_bar().encode(
        x=alt.X("lo:Q", title=f"Buy minus rent wealth after {s.hold_years} yrs (£k)"), x2="hi",
        y=alt.Y("n:Q", title=None, axis=None), y2=alt.datum(0),
        color=alt.Color("Outcome:N", scale=alt.Scale(domain=["Buying ahead", "Renting ahead"],
                                                     range=["#3b6ea8", "#c9792b"]),
                        legend=alt.Legend(orient="top", title=None))),
        width="stretch")
    st.dataframe(sm.rename(columns={"hold_years": "Hold (yrs)", "p_buy_wins": "Buying wins",
                                    "median": "Median gap", "p10": "Bad case (10th pct)",
                                    "p90": "Good case (90th pct)"})
                 .style.format({"Buying wins": "{:.0%}", "Median gap": gbp,
                                "Bad case (10th pct)": gbp, "Good case (90th pct)": gbp}),
                 hide_index=True, width="stretch")
    st.caption(f"20,000 simulated paths. House prices {h_mu:+.1%}/yr ± {h_sd:.0%}, investments "
               f"{inv_mu:.1%}/yr ± {inv_sd:.0%} (ISA, tax-free), rent {r_mu:+.1%}/yr. Whoever pays "
               "less each month invests the difference. Exit includes selling costs and any ERC. Histogram tails clipped at the 1st/99th percentile.")
    explain(st, "For each simulated future, each month: the buyer pays mortgage payment + service charge + "
                "ground rent + maintenance, and the renter pays rent. Whoever pays less invests the "
                "difference. When the property is sold: "
                f"buyer wealth = house value × (1 − {s.sell_cost_pct:.2%} selling costs) − remaining loan "
                f"− early repayment charge ({s.erc_pct:.1%} of the loan if inside the {s.fix_years}-yr fix) "
                "+ buyer's invested savings. "
                f"Renter wealth = deposit + stamp duty and fees ({gbp(f.deposit + f.upfront_costs)}) "
                "invested from day 1 + renter's invested savings. Gap = buyer − renter. Buying wins = "
                "share of futures with gap > 0. Median gap, Bad case and Good case = the 50th, 10th and "
                "90th percentile of the gap.")

# ------------------------------------------------------------------ shortlist
st.divider()
st.subheader("Shortlist")
if st.button("Add this listing to the shortlist"):
    row = {"Listing": res["name"], "Postcode": res["postcode"], "Price": s.price,
           "Tenure": res["tenure"], "Lease": res["lease"], "Stamp duty": f.stamp_duty,
           "LTV": round(f.ltv, 3), "Cash out/mo": round(b["cash_out"]),
           "Money gone/mo": round(b["money_gone"]),
           f"Buy wins": round(at_hold["p_buy_wins"], 2), "Hold yrs": s.hold_years}
    for c in res["commutes"]:
        if c.get("median_min") is not None:
            row[f"{c['person']} → {c['office']} (min)"] = c["median_min"]
    ss.shortlist.append(row)
up = st.file_uploader("Load a saved shortlist (CSV)", type="csv", key="sl_up")
if up is not None and not ss.get("_sl_loaded"):
    ss.shortlist = pd.read_csv(up).to_dict("records")
    ss._sl_loaded = True
    st.rerun()
if ss.shortlist:
    sl = pd.DataFrame(ss.shortlist)
    st.dataframe(sl, hide_index=True, width="stretch")
    st.download_button("Download shortlist (CSV)", sl.to_csv(index=False), "shortlist.csv", "text/csv")
else:
    st.caption("Nothing yet. Evaluate a listing and add it to compare side by side. The hosted app "
               "doesn't keep data between visits, so download the CSV to save it.")
