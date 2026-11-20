import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Json, useApi } from "../api";
import Chart, { C, axis } from "../components/Chart";
import { Card, ErrorBox, Loading, PageHead, Section, Seg, Stat } from "../components/ui";
import { int, isNum, num, pct, tone, tstat, usd } from "../format";

const HZ = ["1", "5", "20", "60", "90", "180", "252"];
const HZ_LABEL: Record<string, string> = { "1": "1d", "5": "5d", "20": "20d", "60": "60d", "90": "90d", "180": "180d", "252": "252d" };
type Sample = "full" | "in_sample" | "out_of_sample";
const SAMPLES: { v: Sample; l: string }[] = [{ v: "full", l: "Full" }, { v: "in_sample", l: "In-sample ≤2018" }, { v: "out_of_sample", l: "Out-of-sample 2019+" }];

export const dirLabel = (d: number) => (d > 0 ? <span className="pill pos">Bullish</span> : d < 0 ? <span className="pill neg">Bearish</span> : <span className="pill">Control</span>);

export default function Insiders() {
  const s = useApi("/api/insiders/summary");
  const paths = useApi("/api/insiders/paths");
  const curves = useApi("/api/insiders/curves");
  const recent = useApi("/api/insiders/recent?limit=120");
  const [sel, setSel] = useState("BUY_CLUSTER");
  const [sample, setSample] = useState<Sample>("full");

  if (s.error) return <ErrorBox error={s.error} />;
  if (!s.data) return <><PageHead kicker="Phase 1 · Corporate insiders" title="Insider Activity" /><Loading h={500} /></>;
  const d: Json = s.data;
  const keys: string[] = d.signals.map((x: Json) => x.key).filter((k: string) => d.event_study[k]);
  const es = d.event_study[sel];

  return (
    <div className="fade">
      <PageHead kicker="Phase 1 · Corporate insiders · SEC Form 4" title="Do insider trades predict returns?"
        sub={<>Every discretionary open-market trade by officers, directors and 10% holders, {d.config.event_start.slice(0, 4)}–{d.config.price_data_end.slice(0, 4)}. Returns are measured from the close of the first trading day <i>after</i> the filing became public, versus SPY over identical dates, with standard errors clustered by month.</>}
        right={<Seg value={sample} options={SAMPLES} onChange={setSample} />} />

      <HeadlineStats d={d} />

      <Section title="Signal scoreboard · 60-trading-day excess return vs SPY">
        <Card desc="Click a row to inspect the signal. *, **, *** = |t| ≥ 1.65 / 1.96 / 2.58 (month-clustered). Placebo p = share of 200 same-stock random-date replications that did at least as well. FF6 α = annualised net-of-cost alpha of the calendar-time portfolio vs Fama-French 5 factors + momentum (Newey-West t).">
          <Scoreboard d={d} keys={keys} sel={sel} setSel={setSel} sample={sample} />
        </Card>
      </Section>

      <Section title="Excess return by horizon">
        <Card desc={`Mean excess return vs SPY (${SAMPLES.find((x) => x.v === sample)!.l}). Cell text: mean / clustered t-stat. Colour is direction-adjusted: green = the hypothesis held (bullish signal beat, bearish signal lagged).`}>
          <Heatmap d={d} keys={keys} sample={sample} />
        </Card>
      </Section>

      <Section title={`Signal detail · ${es.meta.name}`}>
        <div className="grid g3" style={{ marginBottom: 14 }}>
          <Card title="Definition" right={dirLabel(es.meta.direction)} className="span2">
            <div style={{ color: "var(--text)" }}>{es.meta.definition}</div>
            <div className="muted" style={{ marginTop: 6 }}>{es.meta.rationale}</div>
            <div className="dim" style={{ marginTop: 8, fontSize: 11.5 }}>{int(es.meta.n_events)} events · {int(es.meta.n_issuers)} issuers · {es.meta.first} → {es.meta.last} · one event per issuer per {d.config.cooldown_days} days</div>
          </Card>
          <SkewCard es={es} />
        </div>
        <div className="grid g2">
          <Card title="Cumulative excess return after disclosure" desc="Mean buy-and-hold return minus SPY, trading days after entry; band = ±1.96 iid standard errors (indicative only).">
            {paths.data ? <PathChart rows={paths.data[sel]} dir={es.meta.direction} /> : <Loading />}
          </Card>
          <Card title="Placebo test · same stocks, random dates" desc="Distribution of the mean 60d excess return when each event is replaced by a random date in the same stock (200 draws). The gold line is the real signal.">
            <PlaceboChart p={d.placebo[sel]} />
          </Card>
          <Card title="Calendar-time portfolio (net of costs)" desc={`Equal-weight portfolio holding each event for ${d.config.portfolio_hold_days} trading days, rebalanced daily, cash when empty. Growth of $1.`}>
            {curves.data ? <CurveChart c={curves.data[sel]} /> : <Loading />}
          </Card>
          <PortfolioCard p={d.portfolios[sel]} sample={sample} />
          <Card title="Stability by year" desc="Mean 60d excess return of events filed in each year. Consistency matters more than the average.">
            <YearChart rows={es.by_year} />
          </Card>
          <Card title="Robustness of the headline number" desc="Same signal, primary horizon, under alternative choices.">
            <Robust es={es} all={d.robust_all_priced[sel]} />
          </Card>
        </div>
      </Section>

      <Section title="Insider Conviction Score · does combining features help?">
        <Composite c={d.composite} />
      </Section>

      <Section title="Threshold sensitivity (no parameter was optimised)">
        <Card desc="Mean 60d excess return when the cluster size or purchase-size threshold is varied. Thresholds in the signals above were fixed in advance at round numbers.">
          <Grid rows={d.robustness_grid} />
        </Card>
      </Section>

      <Section title="Most recent signals">
        <Card desc="From the latest published SEC quarter. Click a ticker to open the Smart Money Radar.">
          {recent.data ? <Recent rows={recent.data} /> : recent.error ? <ErrorBox error={recent.error} /> : <Loading />}
        </Card>
      </Section>
    </div>
  );
}

function HeadlineStats({ d }: { d: Json }) {
  const cov = d.coverage;
  const dq = d.data_quality;
  return (
    <div className="grid g5">
      <Stat label="Open-market trades parsed" value={int(dq.n_transactions)} foot={`${dq.first_filing} → ${dq.last_filing}`} />
      <Stat label="Insider buy events" value={int(cov.buy_events_total)} foot={`${pct(cov.buy_events_priced / cov.buy_events_total, 0, false)} with verified prices`} />
      <Stat label="Investable buy events" value={int(cov.buy_events_investable)} foot={`price ≥ $${d.config.min_price}, ADV ≥ ${usd(d.config.min_median_dollar_volume)}`} />
      <Stat label="10b5-1 planned sales" value={int(dq.funnel.filter((r: Json) => r.trans_code === "S" && r.exclusion_reason === "rule_10b5_1_plan")[0]?.n)} foot="separated from discretionary sales" />
      <Stat label="Look-ahead bias, day 1" value={pct(d.lookahead.naive_entry_mean_1d_excess - d.lookahead.proper_entry_mean_1d_excess, 2)} cls="neg" foot="return a naive same-day entry would falsely book" />
    </div>
  );
}

export function Scoreboard({ d, keys, sel, setSel, sample }: { d: Json; keys: string[]; sel: string; setSel: (k: string) => void; sample: Sample }) {
  const ff = (k: string) => d.portfolios[k]?.[sample]?.ff6;
  return (
    <div className="scroll" style={{ maxHeight: "none" }}>
      <table className="t">
        <thead><tr>
          <th>Signal</th><th>Hypothesis</th><th className="r">Events</th><th className="r">Mean</th><th className="r">t-stat</th>
          <th className="r">Median</th><th className="r">Beat SPY</th><th className="r">OOS mean</th><th className="r">OOS t</th>
          <th className="r">vs placebo</th><th className="r">Placebo p</th><th className="r">FF6 α (net)</th><th className="r">α t</th><th className="r">Holm p</th>
        </tr></thead>
        <tbody>
          {keys.map((k) => {
            const e = d.event_study[k];
            const r = e[sample]["60"];
            const o = e.out_of_sample["60"];
            const p = d.placebo[k];
            const f = ff(k);
            const dir = e.meta.direction || 1;
            return (
              <tr key={k} className={`click ${k === sel ? "sel" : ""}`} onClick={() => setSel(k)}>
                <td style={{ fontWeight: 600 }}>{e.meta.name}</td>
                <td>{dirLabel(e.meta.direction)}</td>
                <td className="r num">{int(r.n)}</td>
                <td className={`r num ${tone(r.mean * dir)}`}>{pct(r.mean, 2)}</td>
                <td className="r num">{tstat(r.t)}</td>
                <td className="r num muted">{pct(r.median, 2)}</td>
                <td className="r num muted">{pct(r.hit_rate, 0, false)}</td>
                <td className={`r num ${tone(o.mean * dir)}`}>{pct(o.mean, 2)}</td>
                <td className="r num">{tstat(o.t)}</td>
                <td className={`r num ${tone(p && p.difference * dir)}`}>{p ? pct(p.difference, 2) : "—"}</td>
                <td className="r num">{p ? num(p.p_empirical, 3) : "—"}</td>
                <td className={`r num ${tone(f && f.alpha_ann * dir)}`}>{f ? pct(f.alpha_ann, 1) : "—"}</td>
                <td className="r num">{f ? tstat(f.alpha_t) : "—"}</td>
                <td className="r num muted">{num(e.p_holm_primary, 3)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function Heatmap({ d, keys, sample }: { d: Json; keys: string[]; sample: Sample }) {
  const option = useMemo(() => {
    const names = keys.map((k) => d.event_study[k].meta.name);
    const data: [number, number, number, string][] = [];
    keys.forEach((k, y) => HZ.forEach((h, x) => {
      const r = d.event_study[k][sample][h];
      const dir = d.event_study[k].meta.direction || 1;
      if (isNum(r?.mean)) data.push([x, y, Math.max(-3, Math.min(3, (r.t ?? 0) * dir)), `${pct(r.mean, 1)}\n${isNum(r.t) ? r.t.toFixed(1) : ""}`]);
    }));
    return {
      grid: { left: 250, right: 20, top: 10, bottom: 40 },
      tooltip: { trigger: "item", formatter: (p: Json) => `${names[p.data[1]]} · ${HZ_LABEL[HZ[p.data[0]]]}<br/>mean ${p.data[3].replace("\n", " · t ")}` },
      xAxis: { type: "category", data: HZ.map((h) => HZ_LABEL[h]), ...axis({ splitLine: { show: false } }) },
      yAxis: { type: "category", data: names, inverse: true, ...axis({ splitLine: { show: false }, axisLabel: { color: C.text, fontSize: 11.5 } }) },
      visualMap: { min: -3, max: 3, show: false, dimension: 2, inRange: { color: ["#7a2e33", "#3a2328", "#161b23", "#1c3a2c", "#2a7a52"] } },
      series: [{ type: "heatmap", data, label: { show: true, formatter: (p: Json) => p.data[3], fontSize: 10.5, fontFamily: "JetBrains Mono", color: "#d7dbe3", lineHeight: 13 },
        itemStyle: { borderColor: "#12161d", borderWidth: 2 } }],
    };
  }, [d, keys, sample]);
  return <Chart option={option} height={46 + keys.length * 38} />;
}

export function PathChart({ rows, dir }: { rows: Json[]; dir: number }) {
  const option = useMemo(() => {
    const col = (dir >= 0 ? C.pos : C.neg);
    const x = rows.map((r) => r.day);
    return {
      tooltip: { trigger: "axis", valueFormatter: (v: number) => pct(v, 2) },
      xAxis: { type: "category", data: x, name: "trading days", nameLocation: "middle", nameGap: 24, nameTextStyle: { color: C.dim }, ...axis({ splitLine: { show: false } }) },
      yAxis: { type: "value", ...axis({ axisLabel: { color: C.dim, formatter: (v: number) => pct(v, 1) } }) },
      series: [
        { type: "line", data: rows.map((r) => r.lo), lineStyle: { opacity: 0 }, stack: "ci", symbol: "none" },
        { type: "line", data: rows.map((r) => (isNum(r.hi) && isNum(r.lo) ? r.hi - r.lo : null)), lineStyle: { opacity: 0 }, stack: "ci", symbol: "none", areaStyle: { color: col, opacity: 0.12 } },
        { name: "Mean excess", type: "line", data: rows.map((r) => r.mean), symbol: "none", lineStyle: { color: col, width: 2 },
          markLine: { silent: true, symbol: "none", data: [{ yAxis: 0 }], lineStyle: { color: C.gray, type: "dashed" }, label: { show: false } } },
      ],
    };
  }, [rows, dir]);
  return <Chart option={option} />;
}

export function PlaceboChart({ p }: { p: Json }) {
  const option = useMemo(() => {
    if (!p) return {};
    const draws: number[] = p.draws.filter(isNum);
    const lo = Math.min(...draws, p.real_mean), hi = Math.max(...draws, p.real_mean);
    const nb = 30, w = (hi - lo) / nb || 1e-4;
    const bins = Array.from({ length: nb + 1 }, (_, i) => lo + i * w);
    const counts = bins.map((b) => draws.filter((x) => x >= b && x < b + w).length);
    return {
      tooltip: { trigger: "axis", formatter: (ps: Json) => `${pct(ps[0].axisValue, 2)}: ${ps[0].data} draws` },
      xAxis: { type: "category", data: bins, ...axis({ splitLine: { show: false }, axisLabel: { color: C.dim, formatter: (v: string) => pct(+v, 1), interval: 5 } }) },
      yAxis: { type: "value", ...axis() },
      series: [{ type: "bar", data: counts, barCategoryGap: "8%", itemStyle: { color: C.gray },
        markLine: { symbol: "none", silent: true, label: { formatter: `real ${pct(p.real_mean, 2)}`, color: C.gold, position: "insideEndTop" },
          lineStyle: { color: C.gold, width: 2 }, data: [{ xAxis: Math.min(nb, Math.max(0, Math.floor((p.real_mean - lo) / w))) }] } }],
    };
  }, [p]);
  if (!p) return <div className="empty">Too few events for a placebo test.</div>;
  return (
    <>
      <Chart option={option} height={230} />
      <div className="kv" style={{ marginTop: 8 }}>
        <dt>Real mean (events with placebo match)</dt><dd className="pos">{pct(p.real_mean, 2)}</dd>
        <dt>Placebo mean · 90% range</dt><dd>{pct(p.placebo_mean, 2)} · [{pct(p.placebo_p05, 2)}, {pct(p.placebo_p95, 2)}]</dd>
        <dt>Empirical p-value</dt><dd>{num(p.p_empirical, 3)}</dd>
      </div>
    </>
  );
}

export function CurveChart({ c }: { c: Json }) {
  const option = useMemo(() => !c ? {} : ({
    legend: { top: 0, right: 0, textStyle: { color: C.text }, icon: "roundRect", itemHeight: 3 },
    grid: { left: 52, right: 18, top: 30, bottom: 30 },
    tooltip: { trigger: "axis", valueFormatter: (v: number) => num(v, 2) },
    xAxis: { type: "category", data: c.dates, ...axis({ splitLine: { show: false } }) },
    yAxis: { type: "log", logBase: 2, ...axis({ axisLabel: { color: C.dim, formatter: (v: number) => `${v.toFixed(1)}x` } }) },
    series: [
      { name: "Signal (net)", type: "line", data: c.signal, symbol: "none", lineStyle: { color: C.gold, width: 2 } },
      { name: "SPY", type: "line", data: c.spy, symbol: "none", lineStyle: { color: C.blue, width: 1.3 } },
      { name: "IWM (small caps)", type: "line", data: c.iwm, symbol: "none", lineStyle: { color: C.gray, width: 1.3 } },
    ],
  }), [c]);
  if (!c) return <div className="empty">No portfolio.</div>;
  return <Chart option={option} height={260} />;
}

export function PortfolioCard({ p, sample }: { p: Json; sample: Sample }) {
  if (!p) return <Card title="Portfolio statistics"><div className="empty">No portfolio.</div></Card>;
  const s = p[sample];
  const f = s.ff6;
  const rows: [string, string, string][] = [
    ["CAGR", pct(s.gross.cagr), pct(s.net.cagr)], ["Volatility", pct(s.gross.vol, 1, false), pct(s.net.vol, 1, false)],
    ["Sharpe", num(s.gross.sharpe), num(s.net.sharpe)], ["Max drawdown", pct(s.gross.max_drawdown), pct(s.net.max_drawdown)],
    ["Information ratio vs SPY", num(s.gross.information_ratio), num(s.net.information_ratio)],
  ];
  return (
    <Card title="Portfolio statistics & factor attribution" desc={`Avg ${num(p.avg_holdings, 0)} holdings · turnover ${num(p.annual_turnover, 1)}x/yr · cost drag ${pct(p.avg_cost_drag, 1, false)}/yr · ${pct(p.pct_days_lt5_holdings, 0, false)} of days with <5 names`}>
      <div className="grid g2" style={{ gap: 18 }}>
        <table className="t"><thead><tr><th></th><th className="r">Gross</th><th className="r">Net</th></tr></thead>
          <tbody>{rows.map(([a, b, c]) => <tr key={a}><td className="muted">{a}</td><td className="r num">{b}</td><td className="r num">{c}</td></tr>)}</tbody></table>
        <div>
          {f ? <>
            <div className="kv" style={{ marginBottom: 10 }}>
              <dt>FF5 + momentum α (ann.)</dt><dd className={tone(f.alpha_ann)}>{pct(f.alpha_ann, 2)} <span className="dim">t {num(f.alpha_t)}</span></dd>
              <dt>R²</dt><dd>{num(f.r2, 2)}</dd>
            </div>
            <table className="t"><thead><tr><th>Factor</th><th className="r">β</th><th className="r">t</th></tr></thead>
              <tbody>{Object.entries(f.loadings).map(([k, v]: [string, Json]) => (
                <tr key={k}><td className="muted">{k.replace("_", "−")}</td><td className="r num">{num(v.beta)}</td><td className="r num dim">{num(v.t, 1)}</td></tr>))}</tbody></table>
          </> : <div className="empty">Insufficient overlap with factor data.</div>}
        </div>
      </div>
    </Card>
  );
}

export function YearChart({ rows }: { rows: Json[] }) {
  const option = useMemo(() => ({
    tooltip: { trigger: "axis", formatter: (ps: Json) => { const r = rows[ps[0].dataIndex]; return `${r.year}: mean ${pct(r.mean, 2)} · median ${pct(r.median, 2)} · n=${r.n}`; } },
    xAxis: { type: "category", data: rows.map((r) => r.year), ...axis({ splitLine: { show: false } }) },
    yAxis: { type: "value", ...axis({ axisLabel: { color: C.dim, formatter: (v: number) => pct(v, 0) } }) },
    series: [{ type: "bar", data: rows.map((r) => ({ value: r.mean, itemStyle: { color: r.mean >= 0 ? C.pos : C.neg, opacity: 0.85 } })), barWidth: "55%" }],
  }), [rows]);
  return <Chart option={option} height={240} />;
}

export function SkewCard({ es }: { es: Json }) {
  const k = es.skew;
  return (
    <Card title="Mean vs. typical outcome" desc="Small-cap event returns are right-skewed: a few large winners lift the mean.">
      <div className="kv">
        <dt>Mean 60d excess</dt><dd className={tone(k.mean)}>{pct(k.mean, 2)}</dd>
        <dt>Median</dt><dd className={tone(k.median)}>{pct(k.median, 2)}</dd>
        <dt>Mean excluding top 1%</dt><dd className={tone(k.mean_ex_top1pct)}>{pct(k.mean_ex_top1pct, 2)}</dd>
        <dt>Contribution of top 1%</dt><dd>{pct(k.top1pct_contribution, 2)}</dd>
        <dt>Mean log excess (compounded)</dt><dd className={tone(k.mean_log_excess)}>{pct(k.mean_log_excess, 2)}</dd>
      </div>
    </Card>
  );
}

export function Robust({ es, all }: { es: Json; all?: Json }) {
  const r = es.full["60"];
  const rows: [string, Json, Json][] = [
    ["Headline: investable, vs SPY", r.mean, r.t],
    ["Winsorised 1% / 99%", r.mean_winsorized, r.t_winsorized],
    ["vs IWM (size-matched benchmark)", r.mean_exc_iwm, null],
    ["Beta-adjusted (pre-event β)", r.mean_exc_beta, null],
    ["Excluding delisted / truncated", es.robust_ex_truncated?.mean, es.robust_ex_truncated?.t],
    ["All priced events (no liquidity filter)", all?.mean, all?.t],
  ];
  return (
    <table className="t"><thead><tr><th>Variant</th><th className="r">Mean 60d</th><th className="r">t</th></tr></thead>
      <tbody>{rows.map(([a, m, t]) => <tr key={a}><td className="muted">{a}</td><td className={`r num ${tone(m)}`}>{pct(m, 2)}</td><td className="r num">{tstat(t)}</td></tr>)}</tbody>
      <tfoot><tr><td className="dim" colSpan={3} style={{ paddingTop: 8 }}>Raw (unadjusted) mean 60d return {pct(r.raw_mean, 2)}; {int(r.n_truncated)} events truncated by delisting.</td></tr></tfoot>
    </table>
  );
}

function Composite({ c }: { c: Json }) {
  const [m, setM] = useState<"equal_weight" | "fitted_in_sample">("equal_weight");
  const v = c.methods[m];
  const option = useMemo(() => ({
    legend: { top: 0, right: 0, textStyle: { color: C.text } },
    grid: { left: 52, right: 18, top: 30, bottom: 30 },
    tooltip: { trigger: "axis", valueFormatter: (x: number) => pct(x, 2) },
    xAxis: { type: "category", data: ["Q1 (low)", "Q2", "Q3", "Q4", "Q5 (high)"], ...axis({ splitLine: { show: false } }) },
    yAxis: { type: "value", ...axis({ axisLabel: { color: C.dim, formatter: (x: number) => pct(x, 1) } }) },
    series: [
      { name: "In-sample", type: "bar", data: v.in_sample?.quintiles.map((q: Json) => q.mean), itemStyle: { color: C.gray } },
      { name: "Out-of-sample", type: "bar", data: v.out_of_sample?.quintiles.map((q: Json) => q.mean), itemStyle: { color: C.gold } },
    ],
  }), [v]);
  return (
    <div className="grid g2">
      <Card title="60d excess return by score quintile" desc="Features standardised with in-sample moments only. 'Fitted' weights are estimated on 2012–2018 events and then frozen."
        right={<Seg value={m} onChange={setM} options={[{ v: "equal_weight", l: "Equal weight" }, { v: "fitted_in_sample", l: "Fitted" }]} />}>
        <Chart option={option} height={250} />
        <div className="kv" style={{ marginTop: 8 }}>
          <dt>Rank IC · in-sample → out-of-sample</dt><dd>{num(v.in_sample?.spearman_ic, 3)} → {num(v.out_of_sample?.spearman_ic, 3)}</dd>
          <dt>Q5 − Q1 · in-sample → out-of-sample</dt><dd>{pct(v.in_sample?.q5_minus_q1, 2)} → {pct(v.out_of_sample?.q5_minus_q1, 2)}</dd>
        </div>
      </Card>
      <Card title="Feature information coefficients" desc="Spearman rank correlation of each standardised feature with 60d excess return.">
        <table className="t"><thead><tr><th>Feature</th><th className="r">Fitted w</th><th className="r">IC in-sample</th><th className="r">IC out-of-sample</th></tr></thead>
          <tbody>{c.features.map((f: Json) => (
            <tr key={f.key}><td className="muted">{f.label}</td><td className="r num">{num(f.weight_fitted, 4)}</td>
              <td className={`r num ${tone(f.ic_in_sample)}`}>{num(f.ic_in_sample, 3)}</td><td className={`r num ${tone(f.ic_out_of_sample)}`}>{num(f.ic_out_of_sample, 3)}</td></tr>))}</tbody></table>
        <div className="callout" style={{ marginTop: 12 }}>A feature that predicts in-sample but flips sign out-of-sample is the signature of overfitting. The fitted score's in-sample edge is reported alongside its out-of-sample decay rather than in place of it.</div>
      </Card>
    </div>
  );
}

export function Grid({ rows }: { rows: Json[] }) {
  const params = Array.from(new Set(rows.map((r) => `${r.param}|${r.value}`)));
  const get = (k: string, s: string) => rows.find((r) => `${r.param}|${r.value}` === k && r.sample === s);
  const LBL: Record<string, (v: string) => string> = {
    cluster_min_insiders: (v) => `≥ ${v} insiders in 30 days`, min_purchase_value: (v) => `purchase ≥ ${usd(+v)}`,
    hf_crowd_min_buyers: (v) => `≥ ${v} concentrated managers buying (net)`, new_position_min_weight: (v) => `new position ≥ ${pct(+v, 0, false)} of portfolio`,
  };
  const lbl = (k: string) => { const [p, v] = k.split("|"); return (LBL[p] ?? ((x: string) => `${p} ${x}`))(v); };
  return (
    <table className="t"><thead><tr><th>Variant</th><th className="r">IS n</th><th className="r">IS mean</th><th className="r">IS t</th><th className="r">OOS n</th><th className="r">OOS mean</th><th className="r">OOS t</th></tr></thead>
      <tbody>{params.map((k) => { const a = get(k, "in_sample"), b = get(k, "out_of_sample"); return (
        <tr key={k}><td className="muted">{lbl(k)}</td><td className="r num">{int(a?.n)}</td><td className={`r num ${tone(a?.mean)}`}>{pct(a?.mean, 2)}</td><td className="r num">{tstat(a?.t)}</td>
          <td className="r num">{int(b?.n)}</td><td className={`r num ${tone(b?.mean)}`}>{pct(b?.mean, 2)}</td><td className="r num">{tstat(b?.t)}</td></tr>); })}</tbody></table>
  );
}

function Recent({ rows }: { rows: Json[] }) {
  return (
    <div className="scroll">
      <table className="t"><thead><tr><th>Filed</th><th>Company</th><th>Signal</th><th>Insiders</th><th className="r">Value</th><th className="r">Prior 60d</th><th className="r">Score</th><th className="r">20d exc.</th><th>Filings</th></tr></thead>
        <tbody>{rows.map((r, i) => (
          <tr key={i}>
            <td className="num muted">{r.filing_date}</td>
            <td><Link to={`/radar/${r.symbol}`} className="mono" style={{ fontWeight: 600 }}>{r.symbol}</Link> <span className="muted">{(r.issuer_name || "").slice(0, 28)}</span></td>
            <td><span className={`pill ${r.direction > 0 ? "pos" : r.direction < 0 ? "neg" : ""}`}>{r.signal_name}</span></td>
            <td className="muted" title={r.insiders.map((x: Json) => `${x.name} (${x.role}) ${usd(x.value)}`).join("\n")}>{r.insiders.slice(0, 2).map((x: Json) => `${x.name} (${x.role})`).join(", ")}{r.insiders.length > 2 ? ` +${r.insiders.length - 2}` : ""}</td>
            <td className="r num">{usd(r.value)}</td>
            <td className={`r num ${tone(r.prior_ret_60d)}`}>{pct(r.prior_ret_60d, 0)}</td>
            <td className="r num">{isNum(r.conviction_score) ? r.conviction_score.toFixed(0) : "—"}</td>
            <td className={`r num ${tone(r.exc_20)}`}>{pct(r.exc_20, 1)}</td>
            <td>{r.accessions.slice(0, 2).map((a: string) => <a key={a} className="mono" style={{ fontSize: 11, marginRight: 6 }} target="_blank" rel="noreferrer" href={`https://www.sec.gov/Archives/edgar/data/${r.issuer_cik}/${a.replace(/-/g, "")}/`}>{a.slice(-6)}</a>)}</td>
          </tr>))}</tbody></table>
    </div>
  );
}
