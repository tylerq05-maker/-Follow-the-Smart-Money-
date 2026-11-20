# Follow the Smart Money?

**Does following corporate insiders, institutional investors or the crowd generate investment alpha?**

An institutional-style research platform that tests the question on look-ahead-free terms: *what was knowable, when it became public, and what happened next.* It has a Python research engine, a FastAPI service and a React research terminal.

| Phase | Source | Status |
|---|---|---|
| 1 | Corporate insiders (SEC Form 4) | **Complete**: ingestion, classification, signals, event study, placebo, factor-adjusted portfolios, dashboard, validation |
| 2 | Institutions (SEC Form 13F) | Planned |
| 3 | Reddit · Wikipedia | Planned |
| 4 | News · Google Trends | Planned |
| 5 | Disagreement engine · lead-lag · combined backtests | Planned |
| 6 | AI research summaries · final polish | Planned |

## Phase 1 headline results (2012–2026, investable universe)

All figures are produced by `python -m smartmoney.pipeline insiders` and shown live in the UI. The UI text is generated from the result files, so it updates when the data does.

* **Insider purchases barely beat SPY.** The mean 60-trading-day excess return is about 0.1% (t ≈ 0.2). The median event lags the index.
* **They do time their own stock.** Against the *same stocks on random dates*, purchases outperform by about 1pp per 60 days. The placebo p-value is 0.005 for 8 of 9 buy signals.
* **That edge is not investable alpha.** Purchase portfolios load heavily on small caps. Net of costs, their Fama-French 5-factor + momentum alpha is ≤ 0 for every bullish signal except the 10% blockholder signal, which is positive but not significant.
* **Discretionary CEO/CFO sales are the strongest signal, and it's bearish.** The 60-day excess is −0.9% (t = −2.5). It holds out-of-sample (t = −2.7). The factor alpha of these stocks is −4.7%/yr (t = −3.8).
* **Look-ahead bias is real.** 79% of Form 4s from large issuers are accepted *after* the 4 pm close yet carry that day's filing date. A naive same-day entry books a spurious +1.2% day-one return.
* **Combining features overfits.** In-sample-fitted weights flip sign out-of-sample. The platform reports this instead of hiding it.

## Methodology in one paragraph

Historical Form 3/4/5 data comes from the SEC's own structured datasets (2009Q1–2026Q3, 2.2M open-market trades). Filings too recent for the bulk datasets are parsed live from EDGAR XML. Each trade is classified: grants, exercises, tax withholding, gifts, 10b5-1 plans, DRIP/ESPP purchases and offering purchases are separated from discretionary open-market trades, and every exclusion gets a reason code. Events are keyed on the **filing date**. Entry is the **close of the next trading day**, and every window feature uses only filings dated on or before the event. CIK→ticker mappings are verified against the prices insiders reported paying, which rejects 409 reused or wrong tickers. Returns are dividend-adjusted and measured against SPY over identical dates. Inference uses month-clustered standard errors, cluster bootstrap confidence intervals, Holm correction, a 200-draw same-stock random-date placebo, and calendar-time portfolios with tiered transaction costs regressed on FF5 + momentum (Newey-West). The sample is split in-sample (2012–2018) and out-of-sample (2019+).

The full methodology, the quality funnel, survivorship accounting and the test report are on the **Methodology & Validation** page.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env            # set SEC_USER_AGENT to your name + email (SEC fair-access policy)
python -m smartmoney.pipeline all       # ~25 min: SEC bulk data, prices, research (≈1 GB raw data)
python -m smartmoney.validate           # tests + live cross-checks against EDGAR
cd web && npm install && npm run build && cd ..
uvicorn smartmoney.api.app:app --port 8000   # open http://localhost:8000
```

For frontend development, run `npm run dev` in `web/`. It proxies `/api` to port 8000.

Pipeline steps can be run individually: `ingest`, `signals`, `reference`, `prices`, `insiders`.

## Layout

```
smartmoney/
  sources/      SEC bulk Form 4 · Form 4 XML (live) · CIK/ticker reference · prices · Fama-French factors
  insiders/     classify.py (transaction quality, roles) · signals.py (point-in-time events, signal registry)
  research/     returns.py (timing convention) · stats.py · insider_study.py (event study, placebo, portfolios)
  api/          FastAPI: research results + per-ticker Radar
  validate.py   test suite + XML-vs-bulk re-parse + disclosure-timing study
web/            React + TypeScript + ECharts research terminal
data/results/   research outputs consumed by the UI (committed; raw/processed data are reproducible, not committed)
tests/          unit, look-ahead, known-answer (Dimon/JPM 2016, Berkshire/OXY 2022) tests
docs/DEVLOG.md  development log and decisions
```

## Limitations

Free price data excludes most delisted companies. About 35% of insider buy events cannot be priced; this is measured and shown. 10b5-1 flags before 2023 rely on footnotes. Equal-weight daily rebalancing and tiered costs approximate real execution. Results describe 2012–2026 and are not investment advice.

## Data sources

SEC EDGAR (Insider Transactions Data Sets, submissions API, Form 4 XML, XBRL company facts) · Yahoo Finance daily prices · Kenneth R. French Data Library.
