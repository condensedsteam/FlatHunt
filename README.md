# Flat evaluator

A small Streamlit app for comparing flats you find on Rightmove/Zoopla. Enter a
listing's postcode, price and charges and it shows, on one page:

- **Commute** door-to-door from the flat to each office in the list, using live TfL
  journey planning (National Rail, Overground, Elizabeth line, Tube, DLR). Each office is
  sampled at several departure times across your morning window, and the app reports the
  typical (median) and worst-case journey, so infrequent trains show up. There's also a
  weekly total for each person.
- **Monthly costs** in three layers: the mortgage payment, cash out (everything that
  leaves your account) and money gone (the true cost to compare against rent).
- **Buy vs rent odds** from a 20,000-path Monte Carlo, for every hold length from 1 to 10 years.
- **Warnings**:
  - the £500k first-time-buyer stamp duty cliff
  - being just above an LTV pricing band
  - early repayment charges
  - short leases, onerous ground rent and high service charges
  - cash shortfall
  - loan-to-income multiple
- **Shortlist**: add evaluated listings to a table and download it as CSV to compare them side by side.

It is a framework for your own decision, not financial advice.

## Project layout

```
app.py                 Streamlit UI
flatcalc/costs.py      stamp duty, funding, year-1 monthly breakdown
flatcalc/montecarlo.py buy-vs-rent simulation (fix → refix, ERC, selling costs)
flatcalc/commute.py    postcodes.io geocoding + TfL Journey Planner, with an on-disk cache
tests/                 pytest suite (TfL is mocked with a recorded-style fixture)
```

## Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export TFL_APP_KEY=...        # optional but recommended (see below)
streamlit run app.py
pytest -q                     # 20 tests, no network needed
```

## Get a TfL API key (free, ~2 minutes)

1. Sign up at <https://api-portal.tfl.gov.uk/>.
2. Go to **Products**, choose **500 Requests per min**, and subscribe.
3. Go to **Profile**, find **Primary key**, and copy it.

The app works without a key but is heavily rate-limited. Each listing makes about
4 requests per office, and responses are cached for 7 days. postcodes.io needs no key.

## Deploy free on Streamlit Community Cloud

1. Push this folder to a GitHub repo. It can be private. `.gitignore` already excludes secrets.
2. Go to <https://share.streamlit.io>, choose **Create app**, pick the repo, and set the main file to `app.py`.
3. Open **Advanced settings → Secrets** and paste:
   ```toml
   TFL_APP_KEY = "your-key"
   ```
4. Deploy. You can make the app private in its settings and invite your partner by email.

Streamlit Cloud doesn't keep files between restarts. Use **Download shortlist (CSV)**
and **Save / load settings → Download offices** to keep your data. Load them back with the upload boxes.

## Configuring offices

Open **Offices and who commutes there** to edit the table:

- **Person**: anything, e.g. `Me` or `Partner`. Weekly totals are grouped by this.
- **Location**: a full postcode (`EC2Y 5AU`) or `lat,lon`. The defaults are rough
  coordinates for London Wall Place, Tudor Street and Westminster. Replace them with real addresses.
- **Days/week**: how often that person goes there. Set it to `0` to show a commute
  without counting it, which is useful for comparing possible jobs.

## Model assumptions (all editable in the sidebar)

| | Default |
|---|---|
| Mortgage | 5.25% fixed for 5 yrs, then 5.0%, 30-yr repayment |
| Early repayment charge | 3% of the balance if you sell inside the fix |
| Fees | £3k to buy, 1.75% to sell |
| Stamp duty | England rates from April 2025. First-time-buyer relief applies up to £500k |
| House prices | +1.5%/yr, 7% volatility |
| Investments | +5%/yr, 12% volatility, held in an ISA (tax-free) |
| Rent | +3.5%/yr. Service charge +3%/yr |

**How the simulation works.** Each month, whoever pays less (owner or renter) invests the
difference. On exit, the owner's wealth is the sale price minus selling costs, the
remaining loan, any ERC, plus their investments. The renter's wealth is their invested
deposit and costs plus their savings.

**What "money gone" leaves out.** It excludes house price changes. These drive most of
the spread in the odds tab.

**ERC differences.** The year-1 breakdown estimates the ERC on the opening balance. The
simulation uses the balance at exit.

## Ideas for later (from the original brief)

- Land Registry Price Paid and EPC floor areas, to give a £/sq ft comparison against the street median
- A shaded commute map of SE5, SE14, SE15 and SE22 with folium
