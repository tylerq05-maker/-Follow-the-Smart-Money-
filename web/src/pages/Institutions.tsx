import { useMemo, useState } from "react";
import { Json, useApi } from "../api";
import Chart, { C, axis } from "../components/Chart";
import { Card, ErrorBox, Loading, PageHead, Section, Seg, Stat } from "../components/ui";
import { int, num, pct, usd } from "../format";
import { CurveChart, Grid, Heatmap, PathChart, PlaceboChart, PortfolioCard, Robust, Scoreboard, SkewCard, YearChart, dirLabel } from "./Insiders";

type Sample = "full" | "in_sample" | "out_of_sample";
const SAMPLES: { v: Sample; l: string }[] = [{ v: "full", l: "Full" }, { v: "in_sample", l: "In-sample ≤2018" }, { v: "out_of_sample", l: "Out-of-sample 2019+" }];

export default function Institutions() {
  const s = useApi("/api/institutions/summary");
  const paths = useApi("/api/institutions/paths");
  const curves = useApi("/api/institutions/curves");
  const [sel, setSel] = useState("HF_CROWD_BUY");
  const [sample, setSample] = useState<Sample>("full");
  if (s.error) return <><PageHead kicker="Phase 2 · Institutions · SEC Form 13F" title="Institutional Holdings" /><ErrorBox error={s.error} /></>;
  if (!s.data) return <><PageHead kicker="Phase 2 · Institutions · SEC Form 13F" title="Institutional Holdings" /><Loading h={500} /></>;
  const d: Json = s.data;
  const keys: string[] = d.signals.map((x: Json) => x.key).filter((k: string) => d.event_study[k]);
  const es = d.event_study[sel] ?? d.event_study[keys[0]];
  const cov = d.coverage, L = d.lookahead;

  return (
    <div className="fade">
      <PageHead kicker="Phase 2 · Institutions · SEC Form 13F" title="Does copying institutional investors work?"
        sub={<>Quarter-end holdings of every 13F filer, {cov.periods[0].slice(0, 4)}–{cov.periods[1].slice(0, 4)}. Each signal is dated when the information became public — the manager's filing date, or the 45-day deadline for signals that need every manager's report — never the quarter end.</>}
        right={<Seg value={sample} options={SAMPLES} onChange={setSample} />} />

      <div className="grid g5">
        <Stat label="13F holdings reports" value={int(cov.n_filings)} foot={`${int(cov.n_managers)} managers`} />
        <Stat label="Median disclosure lag" value={`${num(cov.filing_lag_days.median, 0)} days`} foot={`90th pct ${num(cov.filing_lag_days.p90, 0)} days after quarter end`} />
        <Stat label="Concentrated managers / qtr" value={int(cov.concentrated_per_period)} foot={`≤ ${d.config.concentrated_rule.max_positions} positions, top-10 ≥ ${pct(d.config.concentrated_rule.min_top10_weight, 0, false)}`} />
        <Stat label="CUSIPs price-verified" value={int(Object.entries(cov.cusip_mapping).filter(([k]) => k === "name" || k === "openfigi").reduce((a, [, v]) => a + (v as number), 0))} foot={`of ${int(cov.cusips_considered)} widely-held CUSIPs`} />
        <Stat label="Quarter-end look-ahead, 60d" value={pct((L.naive_quarter_end_60d ?? 0) - (L.proper_deadline_60d ?? 0), 2)} cls="neg" foot="return a backtest would falsely book by trading at quarter end" />
      </div>

      <div className="callout" style={{ marginTop: 14 }}>
        <b>What a 13F is not.</b> It is a snapshot of long US equity positions on the last day of the quarter, published up to 45 days later. It does not say when a position was bought, whether it is hedged, or what the manager holds today. A "new position" may have been built 3 months before anyone can see it.
      </div>

      <Section title="Signal scoreboard · 60-trading-day excess return vs SPY">
        <Card desc="Click a row to inspect. Month-clustered t-stats; placebo = same stocks on random dates; FF6 α = net-of-cost alpha of the calendar-time portfolio vs Fama-French 5 factors + momentum.">
          <Scoreboard d={d} keys={keys} sel={sel} setSel={setSel} sample={sample} />
        </Card>
      </Section>

      <Section title="Excess return by horizon">
        <Card desc="Mean excess return / clustered t-stat. Green = hypothesis held in the direction predicted.">
          <Heatmap d={d} keys={keys} sample={sample} />
        </Card>
      </Section>

      <Section title={`Signal detail · ${es.meta.name}`}>
        <div className="grid g3" style={{ marginBottom: 14 }}>
          <Card title="Definition" right={dirLabel(es.meta.direction)} className="span2">
            <div style={{ color: "var(--text)" }}>{es.meta.definition}</div>
            <div className="muted" style={{ marginTop: 6 }}>{es.meta.rationale}</div>
            <div className="dim" style={{ marginTop: 8, fontSize: 11.5 }}>{int(es.meta.n_events)} events · {int(es.meta.n_issuers)} issuers · {es.meta.first} → {es.meta.last}</div>
          </Card>
          <SkewCard es={es} />
        </div>
        <div className="grid g2">
          <Card title="Cumulative excess return after disclosure" desc="Mean buy-and-hold return minus SPY from the day after the information became public.">
            {paths.data ? <PathChart rows={paths.data[sel]} dir={es.meta.direction} /> : <Loading />}
          </Card>
          <Card title="Placebo · same stocks, random dates" desc="200 replications; gold line = real signal.">
            <PlaceboChart p={d.placebo[sel]} />
          </Card>
          <Card title="Calendar-time portfolio (net of costs)" desc={`Equal weight, ${d.config.portfolio_hold_days}-day holding. Growth of $1.`}>
            {curves.data ? <CurveChart c={curves.data[sel]} /> : <Loading />}
          </Card>
          <PortfolioCard p={d.portfolios[sel]} sample={sample} />
          <Card title="Stability by year"><YearChart rows={es.by_year} /></Card>
          <Card title="Robustness of the headline number"><Robust es={es} /></Card>
        </div>
      </Section>

      <Section title="The disclosure lag is the strategy's biggest cost">
        <div className="grid g2">
          <LagCard L={L} />
          <BerkshireCard rows={d.known_answers.berkshire_aapl} />
        </div>
      </Section>

      <Section title="Threshold sensitivity (no parameter was optimised)">
        <Card><Grid rows={d.robustness_grid} /></Card>
      </Section>
    </div>
  );
}

function LagCard({ L }: { L: Json }) {
  const rows: [string, Json, Json][] = [["20 trading days", L.naive_quarter_end_20d, L.proper_deadline_20d], ["60 trading days", L.naive_quarter_end_60d, L.proper_deadline_60d]];
  return (
    <Card title="Hedge-fund crowd buying: quarter end vs. filing deadline" desc={`${int(L.n)} events. The median manager files ${num(L.median_lag_days, 0)} days after quarter end.`}>
      <table className="t"><thead><tr><th>Horizon</th><th className="r">If traded at quarter end (impossible)</th><th className="r">Traded at deadline (realistic)</th></tr></thead>
        <tbody>{rows.map(([h, a, b]) => <tr key={h}><td className="muted">{h}</td><td className="r num neg">{pct(a, 2)}</td><td className="r num">{pct(b, 2)}</td></tr>)}</tbody></table>
      <div className="dim" style={{ marginTop: 10, fontSize: 12 }}>Any return earned between the quarter end and the filing belongs to the manager, not to someone copying them.</div>
    </Card>
  );
}

function BerkshireCard({ rows }: { rows: Json[] }) {
  const option = useMemo(() => ({
    grid: { left: 60, right: 16, top: 20, bottom: 30 },
    tooltip: { trigger: "axis", formatter: (p: Json) => { const r = rows[p[0].dataIndex]; return `Q end ${r.period} · filed ${r.filing_date}<br/>${int(r.shares / 1e6)}M shares · ${usd(r.value_usd)}`; } },
    xAxis: { type: "category", data: rows.map((r) => r.period.slice(0, 7)), ...axis({ splitLine: { show: false } }) },
    yAxis: { type: "value", ...axis({ axisLabel: { color: C.dim, formatter: (v: number) => `${v / 1e6}M` } }) },
    series: [{ type: "bar", data: rows.map((r) => r.shares), itemStyle: { color: C.gold } }],
  }), [rows]);
  return (
    <Card title="Known-answer check: Berkshire Hathaway's reported Apple stake" desc="Shares reported per quarter in Berkshire's own 13F filings (CUSIP 037833100). Hover for the filing date.">
      {rows.length ? <Chart option={option} height={230} /> : <div className="empty">No data.</div>}
    </Card>
  );
}
