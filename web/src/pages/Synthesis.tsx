import { useMemo, useState } from "react";
import { Json, useApi } from "../api";
import Chart, { C, axis } from "../components/Chart";
import { Card, ErrorBox, Loading, PageHead, Section, Seg, Stat } from "../components/ui";
import { int, isNum, num, pct, tone, tstat } from "../format";
import { CurveChart, Heatmap, PathChart, PlaceboChart, PortfolioCard, Scoreboard as SignalTable, dirLabel } from "./Insiders";

type Sample = "full" | "in_sample" | "out_of_sample";
const SAMPLES: { v: Sample; l: string }[] = [{ v: "full", l: "Full" }, { v: "in_sample", l: "In-sample ≤2018" }, { v: "out_of_sample", l: "Out-of-sample 2019+" }];
const INS = ["buying", "neutral", "selling"], INST = ["accumulating", "neutral", "distributing"];

function useSynth() { return useApi("/api/synthesis/summary"); }

/* ------------------------------------------------------------------ Disagreement engine */
export function Disagreement() {
  const s = useSynth();
  const paths = useApi("/api/synthesis/paths");
  const curves = useApi("/api/synthesis/curves");
  const [sel, setSel] = useState("ALIGNED_BULL");
  const [sample, setSample] = useState<Sample>("full");
  const head = <PageHead kicker="Phase 5 · Synthesis" title="Disagreement Engine" />;
  if (s.error) return <>{head}<ErrorBox error={s.error} /></>;
  if (!s.data) return <>{head}<Loading h={500} /></>;
  const d: Json = s.data;
  const keys: string[] = d.signals.map((x: Json) => x.key).filter((k: string) => d.event_study[k]);
  const es = d.event_study[sel] ?? d.event_study[keys[0]];
  return (
    <div className="fade">
      <PageHead kicker="Phase 5 · Synthesis" title="What happens when the smart money disagrees?"
        sub="Every stock is classified at each 13F filing deadline — the first date all three groups' behaviour is public — by what insiders, institutions and the crowd were doing. Subsequent 60-day excess returns are then compared across every combination."
        right={<Seg value={sample} options={SAMPLES} onChange={setSample} />} />
      <div className="grid g4">
        <Stat label="Stock-quarters classified" value={int(d.panel.n)} foot={`${int(d.panel.n_with_crowd)} with Wikipedia attention`} />
        <Stat label="Insiders buying" value={int(d.panel.state_counts.ins_state.buying)} foot={`selling ${int(d.panel.state_counts.ins_state.selling)}`} />
        <Stat label="Institutions accumulating" value={int(d.panel.state_counts.inst_state.accumulating)} foot={`distributing ${int(d.panel.state_counts.inst_state.distributing)}`} />
        <Stat label="High crowd attention" value={int(d.panel.state_counts.crowd_state.high ?? 0)} foot="attention ≠ bullish sentiment" />
      </div>

      <Section title="Insiders × institutions · 60-day excess return vs SPY">
        <div className="grid g2">
          <Card title="Combination matrix" desc={`Cell: mean · t-stat (month-clustered) · n. Cells with n < ${d.config.min_cell} are greyed: too small to interpret.`}>
            <Matrix rows={d.tables.ins_x_inst} sample={sample} />
          </Card>
          <Card title="Does agreement help?" desc="Agreement score = insider state (+1 buy / −1 sell) + institutional state (+1 accumulate / −1 distribute).">
            <AgreementChart rows={d.tables.agreement} sample={sample} />
          </Card>
        </div>
      </Section>

      <Section title="Horse race · which group's behaviour carries information?">
        <HorseRace h={d.horse_race} />
      </Section>

      <Section title="Combined-signal strategies">
        <Card desc="Each combination traded as a signal, with the same placebo and factor-adjusted portfolio tests as single-source signals.">
          <SignalTable d={d} keys={keys} sel={sel} setSel={setSel} sample={sample} />
        </Card>
        <div className="grid g3" style={{ marginTop: 14 }}>
          <Card title={es.meta.name} right={dirLabel(es.meta.direction)}>
            <div style={{ color: "var(--text)" }}>{es.meta.definition}</div><div className="muted" style={{ marginTop: 6 }}>{es.meta.rationale}</div>
            <div className="dim" style={{ marginTop: 8, fontSize: 11.5 }}>{int(es.meta.n_events)} events · {int(es.meta.n_issuers)} issuers</div>
          </Card>
          <Card title="Cumulative excess return">{paths.data ? <PathChart rows={paths.data[sel]} dir={es.meta.direction} /> : <Loading />}</Card>
          <Card title="Placebo · same stocks, random dates"><PlaceboChart p={d.placebo[sel]} /></Card>
          <Card title="Calendar-time portfolio" className="span2">{curves.data ? <CurveChart c={curves.data[sel]} /> : <Loading />}</Card>
          <PortfolioCard p={d.portfolios[sel]} sample={sample} />
        </div>
      </Section>

      <Section title="Three-way view (insiders × institutions × crowd attention)">
        <ThreeWay rows={d.tables.three_way} min={d.config.min_cell} />
      </Section>
      <div className="callout" style={{ marginTop: 14 }}><b>Reading these results.</b> Each cell is an association between publicly observable behaviour and later returns, not a causal effect. Many cells are tested at once, so a single cell with |t| ≈ 2 is weak evidence; consistency across samples and the placebo matters more.</div>
    </div>
  );
}

function Matrix({ rows, sample }: { rows: Json[]; sample: Sample }) {
  const get = (a: string, b: string) => rows.find((r) => r.insiders === a && r.institutions === b);
  return (
    <table className="t" style={{ tableLayout: "fixed" }}>
      <thead><tr><th>Insiders ↓ · Institutions →</th>{INST.map((b) => <th key={b} className="r">{b}</th>)}</tr></thead>
      <tbody>{INS.map((a) => <tr key={a}><td style={{ fontWeight: 600 }}>{a}</td>{INST.map((b) => {
        const r0 = get(a, b); const r = sample === "out_of_sample" ? r0?.oos : r0;
        if (!r) return <td key={b} className="r dim">—</td>;
        const bg = !r.sufficient ? "transparent" : (r.mean ?? 0) > 0 ? `rgba(63,182,127,${Math.min(0.35, Math.abs(r.t ?? 0) / 8)})` : `rgba(224,93,93,${Math.min(0.35, Math.abs(r.t ?? 0) / 8)})`;
        return <td key={b} className="r num" style={{ background: bg, opacity: r.sufficient ? 1 : 0.45, whiteSpace: "normal" }}>
          <div className={tone(r.mean)} style={{ fontWeight: 600 }}>{pct(r.mean, 2)}</div><div className="dim" style={{ fontSize: 11 }}>{tstat(r.t)} · n={int(r.n)}</div></td>;
      })}</tr>)}</tbody>
    </table>
  );
}

function AgreementChart({ rows, sample }: { rows: Json[]; sample: Sample }) {
  const option = useMemo(() => {
    const r = rows.map((x) => (sample === "out_of_sample" ? { ...x.oos, score: x.score } : x));
    return {
      grid: { left: 56, right: 16, top: 20, bottom: 40 },
      tooltip: { trigger: "axis", formatter: (p: Json) => { const x = r[p[0].dataIndex]; return `score ${x.score}: ${pct(x.mean, 2)} (t ${num(x.t)}, n=${x.n})`; } },
      xAxis: { type: "category", data: r.map((x) => (x.score > 0 ? `+${x.score}` : `${x.score}`)), name: "agreement score", nameLocation: "middle", nameGap: 26, nameTextStyle: { color: C.dim }, ...axis({ splitLine: { show: false } }) },
      yAxis: { type: "value", ...axis({ axisLabel: { color: C.dim, formatter: (v: number) => pct(v, 1) } }) },
      series: [{ type: "bar", data: r.map((x) => ({ value: x.mean, itemStyle: { color: (x.mean ?? 0) >= 0 ? C.pos : C.neg, opacity: x.sufficient ? 0.9 : 0.35 } })), barWidth: "50%" }],
    };
  }, [rows, sample]);
  return <Chart option={option} height={260} />;
}

function HorseRace({ h }: { h: Json }) {
  const names: Record<string, string> = { insiders_buying: "Insiders buying", insiders_selling: "Insiders selling", inst_accumulating: "Institutions accumulating",
    inst_distributing: "Institutions distributing", attention_high: "Crowd attention high", attention_low: "Crowd attention low" };
  return (
    <Card desc="Pooled OLS of (winsorised) 60-day excess return on all state dummies at once, month-clustered t-stats. Each coefficient is that group's incremental association, holding the others fixed. Baseline = all neutral / normal.">
      <table className="t"><thead><tr><th>State</th>{(["full", "in_sample", "out_of_sample"] as const).map((s) => <th key={s} className="r" colSpan={2}>{s.replace("_", "-")} (n={int(h[s].n)})</th>)}</tr></thead>
        <tbody>{h.full.coefficients.map((c: Json, i: number) => <tr key={c.name}><td className="muted">{names[c.name] ?? c.name}</td>
          {(["full", "in_sample", "out_of_sample"] as const).map((s) => { const x = h[s].coefficients[i]; return [
            <td key={s + "c"} className={`r num ${tone(x.coef)}`}>{pct(x.coef, 2)}</td>, <td key={s + "t"} className="r num dim">{tstat(x.t)}</td>]; })}</tr>)}</tbody></table>
    </Card>
  );
}

function ThreeWay({ rows, min }: { rows: Json[]; min: number }) {
  const r = [...rows].filter((x) => x.sufficient).sort((a, b) => (b.mean ?? 0) - (a.mean ?? 0));
  return (
    <Card desc={`Combinations with at least ${min} observations, ranked by mean 60-day excess return. ${rows.length - r.length} smaller cells hidden.`}>
      <div className="scroll" style={{ maxHeight: 420 }}><table className="t"><thead><tr><th>Insiders</th><th>Institutions</th><th>Crowd attention</th><th className="r">n</th><th className="r">Mean</th><th className="r">t</th><th className="r">Median</th><th className="r">Beat SPY</th></tr></thead>
        <tbody>{r.map((x, i) => <tr key={i}><td>{x.insiders}</td><td>{x.institutions}</td><td>{x.crowd}</td><td className="r num">{int(x.n)}</td>
          <td className={`r num ${tone(x.mean)}`}>{pct(x.mean, 2)}</td><td className="r num">{tstat(x.t)}</td><td className="r num muted">{pct(x.median, 2)}</td><td className="r num muted">{pct(x.hit_rate, 0, false)}</td></tr>)}</tbody></table></div>
    </Card>
  );
}

/* ------------------------------------------------------------------ Who moves first */
export function LeadLag() {
  const s = useSynth();
  const head = <PageHead kicker="Phase 5 · Synthesis" title="Who Moves First?" />;
  if (s.error) return <>{head}<ErrorBox error={s.error} /></>;
  if (!s.data) return <>{head}<Loading h={500} /></>;
  const w: Json = s.data.who_moves_first;
  const P = w.profiles;
  const ar = w.attention_returns;
  return (
    <div className="fade">
      <PageHead kicker="Phase 5 · Synthesis" title="Who moves first — prices, insiders, institutions or the crowd?"
        sub="Event-time profiles show what prices (and attention) did before and after each group's observable action. Day 0 is when the action became public unless stated. These are lead-lag associations: they establish ordering, not causation." />
      <div className="grid g2">
        {P.insider_cluster_buy && <Profile p={P.insider_cluster_buy} title="Around insider cluster buying" note="Insiders typically buy after a decline (negative drift before day 0). The question is whether the drift reverses after disclosure." />}
        {P.insider_ceo_cfo_sale && <Profile p={P.insider_ceo_cfo_sale} title="Around discretionary CEO/CFO sales" note="Executives tend to sell after run-ups; the post-filing path tests whether the market under-reacts." />}
        {P.hf_crowd_buy_vs_quarter && <Profile p={P.hf_crowd_buy_vs_quarter} title="Hedge-fund crowd buying, anchored at QUARTER END" note="Days −63…0 are the quarter in which funds bought; the 13F becomes public ~31 trading days later. Gains before disclosure belong to the funds, not to copycats." marker={31} />}
        {P.wiki_spike && <Profile p={P.wiki_spike} title="Around Wikipedia attention spikes" note="If the price move happens before day 0, attention follows prices rather than leading them." />}
      </div>
      {P.attention_around_insider_clusters && <Section title="Does the crowd notice insider buying?">
        <AttentionProfile p={P.attention_around_insider_clusters} />
      </Section>}
      <Section title="Attention vs. returns · pooled daily lead-lag">
        <div className="grid g3">
          <Card title="Cross-correlation of abnormal attention and abnormal returns" className="span2"
            desc={`Mean across ${int(ar.n_stocks)} stocks of corr(abnormal attention today, abnormal return k days later). Positive k: attention leads; negative k: returns lead.`}>
            <XCorr ar={ar} />
          </Card>
          <Card title="Granger tests (5 lags, per stock)" desc="Share of stocks where one series improves prediction of the other at the 5% level. Under no predictability ≈ 5% would be significant by chance.">
            <div className="kv"><dt>Attention → returns</dt><dd>{pct(ar.granger.attention_causes_returns_share, 1, false)}</dd>
              <dt>Returns → attention</dt><dd>{pct(ar.granger.returns_cause_attention_share, 1, false)}</dd><dt>Expected by chance</dt><dd>{pct(ar.granger.null_rate, 0, false)}</dd></div>
            <div className="dim" style={{ marginTop: 10, fontSize: 11.5 }}>{ar.interpretation_note}</div>
          </Card>
        </div>
      </Section>
    </div>
  );
}

function Profile({ p, title, note, marker }: { p: Json; title: string; note: string; marker?: number }) {
  const option = useMemo(() => ({
    grid: { left: 56, right: 16, top: 20, bottom: 36 },
    tooltip: { trigger: "axis", valueFormatter: (v: number) => pct(v, 2) },
    xAxis: { type: "category", data: p.days, name: "trading days", nameLocation: "middle", nameGap: 22, nameTextStyle: { color: C.dim }, ...axis({ splitLine: { show: false } }) },
    yAxis: { type: "value", ...axis({ axisLabel: { color: C.dim, formatter: (v: number) => pct(v, 0) } }) },
    series: [
      { type: "line", data: p.lo, lineStyle: { opacity: 0 }, stack: "b", symbol: "none" },
      { type: "line", data: p.hi.map((h: number, i: number) => (isNum(h) && isNum(p.lo[i]) ? h - p.lo[i] : null)), lineStyle: { opacity: 0 }, stack: "b", symbol: "none", areaStyle: { color: C.blue, opacity: 0.12 } },
      { type: "line", data: p.mean, symbol: "none", lineStyle: { color: C.gold, width: 2 },
        markLine: { symbol: "none", silent: true, label: { color: C.dim, formatter: "{b}" }, lineStyle: { color: C.gray, type: "dashed" },
          data: [{ xAxis: String(0), name: "day 0" }, ...(marker ? [{ xAxis: String(marker), name: "13F public" }] : [])] } },
    ],
  }), [p, marker]);
  return <Card title={title} desc={`Cumulative excess return vs SPY relative to ${p.label} · n=${int(p.n)}`}><Chart option={option} height={250} /><div className="dim" style={{ fontSize: 11.5, marginTop: 6 }}>{note}</div></Card>;
}

function AttentionProfile({ p }: { p: Json }) {
  const option = useMemo(() => ({
    grid: { left: 56, right: 16, top: 20, bottom: 36 }, tooltip: { trigger: "axis", valueFormatter: (v: number) => `${num(Math.exp(v), 2)}x baseline` },
    xAxis: { type: "category", data: p.days, name: "calendar days from Form 4 filing", nameLocation: "middle", nameGap: 22, nameTextStyle: { color: C.dim }, ...axis({ splitLine: { show: false } }) },
    yAxis: { type: "value", ...axis({ axisLabel: { color: C.dim, formatter: (v: number) => `${num(Math.exp(v), 2)}x` } }) },
    series: [{ type: "bar", data: p.mean, itemStyle: { color: C.violet } }],
  }), [p]);
  return <Card desc={`Mean log(views / pre-event median) around ${int(p.n)} insider cluster-buy filings. A bump after day 0 means the crowd reacts to insiders; before day 0, insiders act on news the crowd already saw.`}><Chart option={option} height={220} /></Card>;
}

function XCorr({ ar }: { ar: Json }) {
  const option = useMemo(() => ({
    grid: { left: 60, right: 16, top: 20, bottom: 36 }, tooltip: { trigger: "axis", valueFormatter: (v: number) => num(v, 4) },
    xAxis: { type: "category", data: ar.lags, name: "lag k (days)", nameLocation: "middle", nameGap: 22, nameTextStyle: { color: C.dim }, ...axis({ splitLine: { show: false } }) },
    yAxis: { type: "value", ...axis({ axisLabel: { color: C.dim, formatter: (v: number) => num(v, 3) } }) },
    series: [{ type: "bar", data: ar.mean_corr.map((v: number, i: number) => ({ value: v, itemStyle: { color: ar.lags[i] < 0 ? C.blue : ar.lags[i] === 0 ? C.gold : C.violet } })) }],
  }), [ar]);
  return <Chart option={option} height={240} />;
}

/* ------------------------------------------------------------------ Performance scoreboard */
export function PerfScoreboard() {
  const s = useApi("/api/synthesis/scoreboard");
  const [sortKey, setSortKey] = useState<"oos_t_60" | "ff6_t" | "placebo_p">("oos_t_60");
  const head = <PageHead kicker="Phase 5 · Synthesis" title="Performance Scoreboard" />;
  if (s.error) return <>{head}<ErrorBox error={s.error} /></>;
  if (!s.data) return <>{head}<Loading h={400} /></>;
  const rows: Json[] = [...s.data].sort((a, b) => {
    if (sortKey === "placebo_p") return (a.placebo_p ?? 1) - (b.placebo_p ?? 1);
    const da = a.direction || 1, db = b.direction || 1;
    return (b[sortKey] ?? -99) * db - (a[sortKey] ?? -99) * da;
  });
  const verdict = (r: Json) => {
    const d = r.direction || 1;
    const oos = (r.oos_t_60 ?? 0) * d >= 1.96, ff = (r.ff6_t ?? 0) * d >= 1.96, pl = (r.placebo_p ?? 1) <= 0.05;
    if (r.direction === 0) return <span className="pill">control / no direction</span>;
    if (oos && ff) return <span className="pill pos">investable evidence</span>;
    if (oos || ff || pl) return <span className="pill warn">partial evidence</span>;
    return <span className="pill">no evidence</span>;
  };
  return (
    <div className="fade">
      <PageHead kicker="Phase 5 · Synthesis" title="Which source of 'smart money' actually works?"
        sub="Every signal from every source on identical terms: 60-day excess return vs SPY, out-of-sample (2019+), same-stock placebo, and net-of-cost Fama-French 5 + momentum alpha. A signal earns 'investable evidence' only if it survives both out-of-sample and factor adjustment in its hypothesised direction."
        right={<Seg value={sortKey} onChange={setSortKey} options={[{ v: "oos_t_60", l: "Sort: OOS t" }, { v: "ff6_t", l: "FF6 t" }, { v: "placebo_p", l: "Placebo p" }]} />} />
      <Card>
        <div className="scroll" style={{ maxHeight: "none" }}><table className="t"><thead><tr><th>Source</th><th>Signal</th><th>Hypothesis</th><th className="r">n</th><th className="r">60d mean</th><th className="r">t</th><th className="r">OOS mean</th><th className="r">OOS t</th><th className="r">vs placebo</th><th className="r">placebo p</th><th className="r">FF6 α</th><th className="r">α t</th><th className="r">Net Sharpe</th><th>Verdict</th></tr></thead>
          <tbody>{rows.map((r) => { const d = r.direction || 1; return (
            <tr key={r.source + r.key}><td><span className="tag">{r.source}</span></td><td style={{ fontWeight: 600 }}>{r.name}</td><td>{dirLabel(r.direction)}</td>
              <td className="r num">{int(r.n)}</td><td className={`r num ${tone(r.mean_60 * d)}`}>{pct(r.mean_60, 2)}</td><td className="r num">{tstat(r.t_60)}</td>
              <td className={`r num ${tone(r.oos_mean_60 * d)}`}>{pct(r.oos_mean_60, 2)}</td><td className="r num">{tstat(r.oos_t_60)}</td>
              <td className={`r num ${tone(r.placebo_diff * d)}`}>{pct(r.placebo_diff, 2)}</td><td className="r num">{num(r.placebo_p, 3)}</td>
              <td className={`r num ${tone(r.ff6_alpha * d)}`}>{pct(r.ff6_alpha, 1)}</td><td className="r num">{tstat(r.ff6_t)}</td><td className="r num">{num(r.net_sharpe)}</td><td>{verdict(r)}</td></tr>); })}</tbody></table></div>
      </Card>
    </div>
  );
}

export { Heatmap };
