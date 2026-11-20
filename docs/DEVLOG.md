# Development log

Short record of what exists, key decisions and why, so later phases don't rediscover them.

## Phase 1 — Insiders (complete)

### Data
- **History:** SEC Insider Transactions Data Sets, 2009Q1–2026Q3. The SEC moved quarters from 2026 onward to
  `/files/datastandardsinnovation/...`, and the downloader tries both paths.
- **Live:** `data.sec.gov/submissions` + raw Form 4 XML. The submissions JSON is paginated; only `filings.recent` is used,
  which is enough because bulk data covers anything older.
- **`acceptanceDateTime` is genuine UTC.** A filing accepted at 22:47Z kept its same-day date, which is only possible
  before 10 pm ET. Section 16 filings accepted up to 10 pm ET keep that day's filing date.
- **Prices:** Yahoo chart API, stored as close (split-adjusted), adjclose (total return) and raw_close (splits undone),
  with bad-tick removal. Delisted symbols return 404, which creates survivorship bias (measured).
- **Ticker reuse is real.** Yahoo `FB` and `BBBY` are now other securities. The fix is CIK-keyed identity plus price
  reconciliation against reported trade prices, which rejects 409 mappings.
- **Not reachable from this environment:** Reddit API (403), Stooq. Wikimedia rate-limits this IP; Phase 3 needs a
  retry/backoff plan or another host. No `ANTHROPIC_API_KEY` is set, which Phase 3/6 needs.

### Decisions
- Research store = P/S trades only (2.2M rows). The Radar classifies an issuer's full history (all codes) on demand via DuckDB.
- Joint-filer de-duplication never merges distinct officers or directors. An earlier version did, and removed 124k rows instead of 85k.
- CEO/CFO is detected per title segment, with a check that a following segment isn't a division name.
- 10b5-1 = checkbox OR transaction footnote OR any plan language in the filing. This is conservative.
- Routine/opportunistic (Cohen, Malloy & Pomorski) uses FILING months of prior calendar years, so it is strictly point-in-time.
- Thresholds are fixed round numbers. The sensitivity grid is reported, not optimised.
- Arithmetic event means are inflated by right skew. The study reports median, mean excluding the top 1%, and log excess.

### Results snapshot
See the README. Key facts: buys have a same-stock edge but no net factor alpha; discretionary CEO/CFO sales are
significantly bearish out-of-sample; 10b5-1 sales show a small same-stock placebo gap (consistent with Jagolinzer 2009);
79% of large-issuer Form 4s are accepted after the close.

### Validation
42 pytest tests, including known answers (Dimon 500k JPM shares 2016-02-11; Berkshire OXY 2022), look-ahead tests and
a recomputation of cluster counts from raw filings. The XML re-parse matches the SEC bulk data field for field except
for cent rounding: the SEC bulk data rounds prices to cents.

### Runtimes (4 vCPU)
ingest 5 min · signals 1 min · prices 25 min · insiders study 8.5 min · validate 3 min.

## (Superseded plan) Phase 2 — 13F
- Use the SEC Form 13F data sets (structured, quarterly) and map CUSIP→CIK (needs a mapping source; investigate the
  SEC 13F securities list plus CUSIPs in filings).
- Signal date = 13F **filing** date. Never the quarter end.
- Known answer: Berkshire's reported AAPL position in the right quarter.
- Reuse research/returns.py, stats.py and the portfolio/placebo code in insider_study.py. Factor these into shared
  modules when the second source arrives.

## Phase 2 — Institutions (complete)
- SEC 13F data sets: 54 files, mixing quarterly and rolling 3-month filing windows; regrouped by PERIODOFREPORT. Newer files live under `/files/datastandardsinnovation/`.
- Values were reported in thousands before 2023-01-03 and in dollars after. This is verified: the implied AAPL price equals the close in both regimes (test).
- CUSIP→ticker: issuer-name match against the SEC ticker table, plus OpenFIGI (keyless, 25 requests/min, cached in data/cache/openfigi.json). A candidate is accepted only if value ÷ shares matches the market close at ≥ 70% of quarter ends. 7,864 CUSIPs were verified and about 1,000 candidates rejected.
- "Hedge-fund-like" = AUM ≥ $100M, 10–150 positions, top-10 ≥ 50%, ETF share < 20%. Without the ETF and position-floor conditions, 37% of managers qualified (ETF-heavy advisers and foundations); with them, 17%.
- Prior-quarter shares are split-adjusted. NVDA's 10:1 split is tested.
- Timing: manager signals use the manager's filing date; aggregate signals use the 45-day deadline (business-day rolled). Excluded: late originals, amendments, 583 filings with impossible periods, and 4 filed on their own quarter-end date (caught by a test).
- Gotchas: `both` is reserved in DuckDB; NaN tickers come from both OpenFIGI and the SEC table; the build needs about 6 GB of RAM (stop the API server first).

## Phase 3 — Crowd (Wikipedia attention; complete with caveats)
- Wikidata SPARQL maps NYSE/Nasdaq tickers to enwiki articles; ambiguous tickers are dropped. 3,108 links.
- Pageviews API: severe 429 throttling on the shared IP. Use one worker and patient backoff. 2,394 of 3,108 were fetched; resumable.
- Views for day d are public on d+1. A spike is ≥ 3× the trailing 60-day median and ≥ 1,000 views. The unconditional spike signal has no assumed direction (two-sided test).
- Not reachable: Reddit (403), PullPush (refuses agents), Arctic Shift search (timeouts; its aggregate endpoint works), GDELT, Google Trends. Stocktwits works (recent messages only); its self-labelled Bullish/Bearish tags could validate sentiment in Phase 6.

## Phase 5 — Synthesis (complete)
- Panel: stock × 13F quarter dated at the deadline. Insider state uses filings in the prior 90 days; crowd state compares the last 30 days with the prior 182.
- Outputs: combination tables (cells with n < 30 flagged), a month-clustered horse-race regression, combo signals through the same engine, event-time profiles, attention↔return cross-correlation, and Granger tests (returns→attention 17%, attention→returns 9%).
- Bug caught: statsmodels removed `verbose=` from grangercausalitytests, and a bare except hid it, producing 0% shares. Now only degenerate-series errors are caught and the tested count is reported.

## Process lessons
- `pgrep`/`pkill -f` patterns are regexes: parentheses in `w.build(workers=1)` never matched, which left parallel fetchers running and let a waiter exit early. Match fixed substrings instead.
- Every narrative sentence in the UI is generated from result files; hard-coded claims were removed twice.
