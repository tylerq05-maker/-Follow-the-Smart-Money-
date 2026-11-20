import { useMemo } from "react";
import { Json, useApi } from "../api";
import Chart, { C, axis } from "../components/Chart";
import { Card, ErrorBox, Loading, PageHead, Section, Stat } from "../components/ui";
import { int, num, pct, usd } from "../format";

const REASONS: Record<string, string> = {
  "": "Qualifying discretionary trade", rule_10b5_1_plan: "Rule 10b5-1 planned (checkbox or footnote)",
  amendment_or_form5: "Amendment (4/A) or late annual Form 5", drip_espp_plan: "Dividend reinvestment / ESPP / 401(k)",
  non_common_security: "Not common stock (preferred, warrants, units…)", missing_price_or_shares: "Missing price or share count",
  offering_private_placement: "IPO / offering / private placement purchase", invalid_dates: "Invalid or inconsistent dates",
  inconsistent_direction: "Code contradicts acquired/disposed flag", equity_swap: "Equity swap",
};

const STEPS = [
  ["Trade date", "Insider transacts. Not public."],
  ["Filing date", "Form 4 due within 2 business days. Late filings are flagged."],
  ["EDGAR acceptance", "Public moment. Section 16 filings accepted until 10pm ET keep that day's date."],
  ["Signal day", "Last trading day ≤ filing date. Features may use prices up to its close."],
  ["Entry", "Close of the first trading day AFTER the filing date."],
  ["Exit", "Close after 1, 5, 20, 60, 90, 180, 252 trading days."],
];

export default function Methodology() {
  const s = useApi("/api/insiders/summary");
  const v = useApi("/api/validation");
  const d: Json = s.data;
  const V: Json = v.data;

  const covChart = useMemo(() => !d ? {} : ({
    legend: { top: 0, right: 0, textStyle: { color: C.text } }, grid: { left: 56, right: 18, top: 30, bottom: 30 },
    xAxis: { type: "category", data: d.coverage.by_year.map((r: Json) => r.year), ...axis({ splitLine: { show: false } }) },
    yAxis: { type: "value", ...axis() },
    series: [
      { name: "All insider buy events", type: "bar", data: d.coverage.by_year.map((r: Json) => r.events), itemStyle: { color: "#2a3140" }, barGap: "-100%" },
      { name: "With verified prices", type: "bar", data: d.coverage.by_year.map((r: Json) => r.priced), itemStyle: { color: C.gray }, barGap: "-100%" },
      { name: "Investable", type: "bar", data: d.coverage.by_year.map((r: Json) => r.investable), itemStyle: { color: C.gold }, barGap: "-100%" },
    ],
  }), [d]);

  const hourChart = useMemo(() => !V?.acceptance_timing ? {} : ({
    grid: { left: 44, right: 12, top: 16, bottom: 30 },
    xAxis: { type: "category", data: Array.from({ length: 24 }, (_, h) => h), name: "hour accepted (ET)", nameLocation: "middle", nameGap: 22, nameTextStyle: { color: C.dim }, ...axis({ splitLine: { show: false } }) },
    yAxis: { type: "value", ...axis() },
    series: [{ type: "bar", data: Array.from({ length: 24 }, (_, h) => ({ value: V.acceptance_timing.hour_histogram[h] ?? 0, itemStyle: { color: h >= 16 ? C.neg : h < 9 ? C.gray : C.blue } })) }],
  }), [V]);

  if (s.error) return <ErrorBox error={s.error} />;
  if (!d) return <Loading h={500} />;
  const funnel = d.data_quality.funnel as Json[];
  const fr = (code: string) => funnel.filter((r) => r.trans_code === code);
  const tot = (code: string) => fr(code).reduce((a, r) => a + r.n, 0);

  return (
    <div className="fade">
      <PageHead kicker="Methodology & data validation" title="How the results were produced — and how they were checked"
        sub="Deterministic code computes every number; nothing on this platform is estimated by a language model. Each step below has an automated test or a cross-check against an independent source." />

      <Section title="Disclosure timing · the look-ahead firewall">
        <div className="grid" style={{ gridTemplateColumns: `repeat(${STEPS.length}, minmax(0,1fr))`, gap: 8 }}>
          {STEPS.map(([h, b], i) => (
            <div key={h} className="card" style={{ padding: 12, borderTop: `2px solid ${i < 3 ? "var(--line-2)" : "var(--accent)"}` }}>
              <div className="dim mono" style={{ fontSize: 11 }}>T{i}</div><div style={{ fontWeight: 600, margin: "2px 0 4px" }}>{h}</div><div className="muted" style={{ fontSize: 12 }}>{b}</div>
            </div>))}
        </div>
        <div className="grid g3" style={{ marginTop: 14 }}>
          <Card title="When do Form 4s become public?" desc={V?.acceptance_timing ? `${int(V.acceptance_timing.n_filings)} recent Form 4s for ${V.acceptance_timing.issuers} large issuers, by EDGAR acceptance time` : "Run python -m smartmoney.validate"} className="span2">
            {V?.acceptance_timing ? <Chart option={hourChart} height={200} /> : v.error ? <div className="empty">{v.error}</div> : <Loading h={200} />}
          </Card>
          <div className="grid" style={{ alignContent: "start" }}>
            <Stat label="Accepted after the 4pm close" value={V?.acceptance_timing ? pct(V.acceptance_timing.share_after_close, 0, false) : "—"} cls="neg" foot="Same-day-close entry would trade before the information existed" />
            <Stat label="Spurious day-1 return of naive entry" value={pct(d.lookahead.naive_entry_mean_1d_excess, 2)} foot={`vs ${pct(d.lookahead.proper_entry_mean_1d_excess, 2)} with next-day entry · n=${int(d.lookahead.naive_n)}`} />
          </div>
        </div>
      </Section>

      <Section title="Transaction quality funnel · not every Form 4 is a signal">
        <div className="grid g2">
          {(["P", "S"] as const).map((code) => (
            <Card key={code} title={code === "P" ? "Open-market purchases (code P)" : "Open-market sales (code S)"} desc={`${int(tot(code))} transactions after de-duplication. Grants (A), exercises (M/X), tax withholding (F) and gifts (G) are excluded before this stage.`}>
              <table className="t"><tbody>{fr(code).sort((a, b) => (a.exclusion_reason === "" ? -1 : b.exclusion_reason === "" ? 1 : b.n - a.n)).map((r) => (
                <tr key={r.exclusion_reason}><td className={r.exclusion_reason === "" ? "" : "muted"} style={r.exclusion_reason === "" ? { fontWeight: 600 } : {}}>{REASONS[r.exclusion_reason] ?? r.exclusion_reason}</td>
                  <td className="r num">{int(r.n)}</td><td style={{ width: 120 }}><div className="bar"><i style={{ width: `${(r.n / tot(code)) * 100}%`, background: r.exclusion_reason === "" ? "var(--accent)" : r.exclusion_reason === "rule_10b5_1_plan" ? "var(--accent-2)" : "var(--line-2)" }} /></div></td>
                  <td className="r num dim">{pct(r.n / tot(code), 1, false)}</td></tr>))}</tbody></table>
            </Card>))}
        </div>
        <div className="callout" style={{ marginTop: 12 }}>
          <b>Other safeguards.</b> Joint filings by affiliated entities are collapsed ({int(d.data_quality.joint_filer_duplicates_removed)} echo rows removed) while distinct directors trading identical lots are kept. Role is parsed per title segment, so a divisional "CEO, Consumer Banking" is not treated as the company CEO. {pct(d.data_quality.late_filing_share_open_market, 1, false)} of open-market trades were filed late (&gt; 2 business days); they are kept but always timed by their actual filing date.
        </div>
      </Section>

      <Section title="Identity verification & survivorship">
        <div className="grid g3">
          <Card title="CIK → ticker reconciliation" desc="Tickers get reused after delistings. Each mapping is verified by comparing up to 200 reported trade prices with that day's market close.">
            <table className="t"><tbody>{Object.entries(d.coverage.symbol_status).map(([k, n]) => (
              <tr key={k}><td className="muted" style={{ whiteSpace: "normal" }}>{({ verified: "Verified (≥60% of trades within 20% of close)", failed: "Rejected: prices do not match (reused / wrong ticker)", insufficient: "Too few trades to check", no_prices: "No free price history (delisted)" } as Json)[k] ?? k}</td><td className="r num">{int(n)}</td></tr>))}</tbody></table>
          </Card>
          <Card title="Coverage of insider buy events by year" desc="Events in companies without free price history (mostly delisted) cannot be evaluated — a survivorship bias that is measured, not hidden." className="span2">
            <Chart option={covChart} height={230} />
          </Card>
        </div>
        <div className="callout warn" style={{ marginTop: 12 }}>
          <b>Survivorship bias.</b> {pct(1 - d.coverage.buy_events_priced / d.coverage.buy_events_total, 0, false)} of insider buy events lack free price data, concentrated in earlier years and in companies that were later acquired or failed. Acquisitions (takeover premia) and failures bias returns in opposite directions; the net effect is unknown. When a priced stock stops trading mid-horizon, the return to its last price is used and the event is flagged (excluding them is shown as a robustness check). Reported purchase prices more than 50% from the market close, or implausible sizes, are excluded ({int(d.coverage.buy_events_implausible)} buy events).
        </div>
      </Section>

      <Section title="Independent re-parse of original filings">
        {V?.xml_vs_bulk ? (
          <div className="grid g4">
            <Stat label="Filings re-parsed from XML" value={int(V.xml_vs_bulk.filings_checked)} foot={`random sample · ${V.xml_vs_bulk.quarter}`} />
            <Stat label="Transactions compared" value={int(V.xml_vs_bulk.transactions_compared)} foot={V.xml_vs_bulk.fields.join(", ")} />
            <Stat label="Field agreement" value={pct(V.xml_vs_bulk.agreement, 2, false)} cls="pos" foot={`${int(V.xml_vs_bulk.field_comparisons)} field comparisons`} />
            <Stat label="Discrepancies" value={int(V.xml_vs_bulk.n_mismatches)} foot="see validation.json for details" />
          </div>) : <div className="empty">Run <code>python -m smartmoney.validate</code> to generate the cross-check.</div>}
      </Section>

      <Section title="Automated tests">
        {V?.tests ? (
          <Card desc={`${int(V.tests.counts.passed)} passed · ${int(V.tests.counts.failed ?? 0)} failed · ${int(V.tests.counts.skipped ?? 0)} skipped — generated ${V.generated}`}>
            <div className="scroll" style={{ maxHeight: 420 }}><table className="t"><thead><tr><th>Module</th><th>Test</th><th>Status</th></tr></thead><tbody>
              {V.tests.tests.map((t: Json) => (
                <tr key={t.module + t.name}><td className="muted">{t.module.replace("test_", "")}</td><td className="mono" style={{ fontSize: 11.5 }}>{t.name}</td>
                  <td><span className={`pill ${t.status === "passed" ? "pos" : t.status === "failed" ? "neg" : "warn"}`}><span className="dot" />{t.status}</span></td></tr>))}
            </tbody></table></div>
          </Card>) : <div className="empty">No test report yet.</div>}
      </Section>

      <Section title="Statistical design">
        <div className="grid g2">
          <Card title="Inference">
            <div className="kv" style={{ gridTemplateColumns: "190px 1fr" }}>
              {[["Event returns", "Buy-and-hold, dividend-adjusted, minus SPY over identical dates"],
                ["Standard errors", "Clustered by calendar month (events in the same month share shocks)"],
                ["Confidence intervals", "Month-cluster bootstrap, 2,000 replications"],
                ["Multiple testing", `Holm–Bonferroni across ${d.signals.length} signals at the 60-day horizon`],
                ["Placebo", `${d.config.placebo_draws} same-stock random-date replications per signal`],
                ["Factor model", "Fama-French 5 factors + momentum, Newey-West (10 lags)"],
                ["Sample split", `In-sample ${d.config.event_start.slice(0, 4)}–${d.config.in_sample_end.slice(0, 4)}, out-of-sample 2019–${d.config.price_data_end.slice(0, 4)}`],
                ["Overlap control", `One event per issuer per signal per ${d.config.cooldown_days} days`]].map(([k, val]) => <><dt key={k}>{k}</dt><dd style={{ textAlign: "left", fontFamily: "var(--sans)" }}>{val}</dd></>)}
            </div>
          </Card>
          <Card title="Portfolio assumptions">
            <div className="kv" style={{ gridTemplateColumns: "190px 1fr" }}>
              {[["Construction", `Equal weight, ${d.config.portfolio_hold_days}-trading-day holding, daily rebalance`],
                ["Investability", `Price ≥ $${d.config.min_price}, 20-day median dollar volume ≥ ${usd(d.config.min_median_dollar_volume)}, at signal date`],
                ["Trading costs (one-way)", d.config.cost_tiers_bps.map((t: number[]) => `${t[1]} bps if ADV ≥ ${usd(t[0])}`).join(" · ")],
                ["Turnover", "Target-weight changes (drift ignored), charged on entry, exit and rebalancing"],
                ["Idle capital", "Earns the risk-free rate"],
                ["Benchmarks", "SPY (market), IWM (size); factor data to " + d.config.factor_data_end]].map(([k, val]) => <><dt key={k}>{k}</dt><dd style={{ textAlign: "left", fontFamily: "var(--sans)" }}>{val}</dd></>)}
            </div>
          </Card>
        </div>
      </Section>

      <Section title="Known limitations">
        <Card>
          {["Free price data excludes most delisted companies (survivorship; quantified above).",
            "Form 4 'P' covers open-market and private purchases; offerings are detected from footnote language, which is imperfect.",
            "Rule 10b5-1 flags before April 2023 rely on voluntary footnote disclosure; the checkbox exists only since then.",
            "Daily equal-weight rebalancing is an approximation; capacity and market impact for small caps are not modelled beyond tiered costs.",
            "Market capitalisation history is unavailable for free; liquidity (dollar volume) is used as the size proxy.",
            "Results describe 2012–2026 and are not a forecast. Statistical significance is not economic significance."].map((t, i) =>
            <div key={i} className="finding" style={{ gridTemplateColumns: "28px 1fr" }}><div className="n">{String(i + 1).padStart(2, "0")}</div><div className="b">{t}</div></div>)}
        </Card>
      </Section>

      <Section title="Data sources">
        <Card>
          <div className="kv" style={{ gridTemplateColumns: "220px 1fr" }}>
            <dt>Insider transactions (history)</dt><dd style={{ textAlign: "left" }}><a href="https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets" target="_blank" rel="noreferrer">SEC Insider Transactions Data Sets</a></dd>
            <dt>Insider transactions (latest)</dt><dd style={{ textAlign: "left" }}><a href="https://www.sec.gov/edgar/sec-api-documentation" target="_blank" rel="noreferrer">SEC EDGAR submissions API + Form 4 XML</a></dd>
            <dt>Company identifiers, shares</dt><dd style={{ textAlign: "left" }}>SEC company_tickers_exchange.json · XBRL company facts</dd>
            <dt>Prices</dt><dd style={{ textAlign: "left" }}>Yahoo Finance daily chart data (split & dividend adjusted)</dd>
            <dt>Factors</dt><dd style={{ textAlign: "left" }}><a href="https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html" target="_blank" rel="noreferrer">Kenneth R. French Data Library</a></dd>
            <dt>Run window</dt><dd style={{ textAlign: "left" }}>Prices to {d.config.price_data_end} · results generated {d.generated}</dd>
          </div>
        </Card>
      </Section>
      <div className="dim" style={{ marginTop: 16, fontSize: 11 }}>Methodology references: Seyhun (1986); Lakonishok & Lee (2001); Jenter (2005); Cohen, Malloy & Pomorski (2012); Fama & French (2015). {num(0, 0) && ""}</div>
    </div>
  );
}
