import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Json, useApi } from "../api";
import Chart, { C, axis } from "../components/Chart";
import { Card, ErrorBox, Loading, PageHead, Section, Stat } from "../components/ui";
import { int, isNum, num, pct, tone } from "../format";

const sig = (t: unknown) => (isNum(t) && Math.abs(t) >= 1.96);
const sigWord = (t: unknown) => (sig(t) ? "statistically significant" : "not statistically significant");

/** Findings are generated from the computed results so every number is traceable. */
function findings(d: Json) {
  const E = d.event_study, P = d.placebo, F = d.portfolios;
  const f60 = (k: string, s = "full") => E[k]?.[s]?.["60"];
  const ff = (k: string, s = "full") => F[k]?.[s]?.ff6;
  const buys = d.signals.filter((s: Json) => s.direction > 0 && E[s.key]).map((s: Json) => s.key);
  const bestPl = buys.map((k: string) => ({ k, v: P[k]?.difference ?? -1 })).sort((a: Json, b: Json) => b.v - a.v)[0]?.k;
  const nSigFF = buys.filter((k: string) => ff(k)?.alpha_t >= 1.96).length;
  const a = f60("BUY_ANY"), pa = P.BUY_ANY, fa = ff("BUY_ANY");
  const s = f60("SELL_CEO_CFO"), so = f60("SELL_CEO_CFO", "out_of_sample"), fs = ff("SELL_CEO_CFO");
  const pl = ff("SELL_PLANNED"), cmp = d.composite.methods.fitted_in_sample;
  const L = d.lookahead;
  return [
    { h: "Following every insider purchase barely beats the market.",
      b: <>Across {int(a.n)} investable purchase events, the mean 60-trading-day return in excess of SPY is <b className={tone(a.mean)}>{pct(a.mean, 2)}</b> (t = {num(a.t)}, {sigWord(a.t)}); the median event <b className={tone(a.median)}>{pct(a.median, 2)}</b>, and only {pct(a.hit_rate, 0, false)} beat SPY.</> },
    { h: "…but insiders do time their own stock.",
      b: <>Compared with the <i>same stocks on random dates</i>, purchases outperform by <b className="pos">{pct(pa.difference, 2)}</b> per 60 days (placebo p = {num(pa.p_empirical, 3)}). The best buy signal on this test is <b>{E[bestPl].meta.name}</b> ({pct(P[bestPl].difference, 2)}). The stocks insiders buy are typically small and lag the index — the edge is relative, not absolute.</> },
    { h: "After factor exposures and trading costs, the buy-side edge disappears.",
      b: <>The 'buy everything' portfolio loads on small-caps (SMB β = {num(fa.loadings.SMB.beta)}) and earns a Fama-French 5-factor + momentum alpha of <b className={tone(fa.alpha_ann)}>{pct(fa.alpha_ann, 1)}</b>/yr net of costs (t = {num(fa.alpha_t)}). {nSigFF === 0 ? "No" : nSigFF} bullish signal{nSigFF === 1 ? "" : "s"} produce{nSigFF === 1 ? "s" : ""} a significantly positive net factor alpha.</> },
    { h: "Discretionary executive selling is the strongest insider signal — and it is bearish.",
      b: <>Non-plan sales of ≥ $1M by the CEO or CFO precede 60-day underperformance of <b className={tone(s.mean)}>{pct(s.mean, 2)}</b> (t = {num(s.t)}), holding out-of-sample ({pct(so.mean, 2)}, t = {num(so.t)}). A portfolio holding these stocks has factor alpha of {pct(fs.alpha_ann, 1)}/yr (t = {num(fs.alpha_t)}) — i.e. the information is in what executives <i>avoid</i> holding.</> },
    { h: "Pre-scheduled 10b5-1 sales: little information — but not none.",
      b: <>Plan sales should be uninformative. Their factor alpha is {pct(pl.alpha_ann, 1)}/yr (t = {num(pl.alpha_t)}, {sigWord(pl.alpha_t)}) and the 60-day excess return {pct(f60("SELL_PLANNED").mean, 2)} (t = {num(f60("SELL_PLANNED").t)}).
        {P.SELL_PLANNED && P.SELL_PLANNED.p_empirical <= 0.05
          ? <> Against the same stocks on random dates, however, they lag by {pct(P.SELL_PLANNED.difference, 2)} (p = {num(P.SELL_PLANNED.p_empirical, 3)}) — consistent with research showing some plans were timed opportunistically (Jagolinzer 2009), the concern behind the SEC's 2022 cooling-off amendments.</>
          : <> They are also indistinguishable from random dates in the same stocks — the control behaves like a control.</>}</> },
    { h: "Look-ahead bias would have manufactured an edge.",
      b: <>Many Form 4s are accepted after the close yet carry that day's filing date. Buying at the filing-date close books a spurious <b className="neg">{pct(L.naive_entry_mean_1d_excess, 2)}</b> day-one excess return versus <b>{pct(L.proper_entry_mean_1d_excess, 2)}</b> with correct next-day entry. Verified violations in the signal set: {L.violations_entry_not_after_filing}.</> },
    { h: "Combining features overfits.",
      b: <>A score with weights fitted on 2012–2018 data has a rank IC of {num(cmp.in_sample?.spearman_ic, 3)} in-sample but {num(cmp.out_of_sample?.spearman_ic, 3)} out-of-sample.</> },
  ];
}

function Verdict({ d }: { d: Json }) {
  const buys = d.signals.filter((s: Json) => s.direction > 0 && d.portfolios[s.key]).map((s: Json) => s.key);
  const placeboEdge = buys.filter((k: string) => d.placebo[k]?.p_empirical <= 0.05).length;
  const ffWins = buys.filter((k: string) => d.portfolios[k].full.ff6?.alpha_t >= 1.96);
  const sellT = d.portfolios.SELL_CEO_CFO?.full.ff6?.alpha_t;
  return (
    <div style={{ fontSize: 14, lineHeight: 1.55 }}>
      {placeboEdge > 0 ? <>Insider purchases carry <b>timing information about their own stock</b> ({placeboEdge} of {buys.length} buy signals beat same-stock random dates at p ≤ 0.05). </> : <>Insider purchases show <b>no reliable timing edge</b> versus random dates in the same stocks. </>}
      {ffWins.length === 0 ? <>Over {d.config.event_start.slice(0, 4)}–{d.config.price_data_end.slice(0, 4)} this did <b>not</b> translate into significant factor-adjusted, net-of-cost alpha for an outsider buying after disclosure. </>
        : <>{ffWins.length} buy signal{ffWins.length > 1 ? "s" : ""} earned significant net factor alpha. </>}
      {isNum(sellT) && sellT <= -1.96 && <>Discretionary executive <b>sales</b> are more informative than purchases.</>}
    </div>
  );
}

export default function Overview() {
  const s = useApi("/api/insiders/summary");
  const d: Json = s.data;
  const fs = useMemo(() => (d ? findings(d) : []), [d]);
  const chart = useMemo(() => {
    if (!d) return {};
    const keys = d.signals.map((x: Json) => x.key).filter((k: string) => d.placebo[k]);
    const names = keys.map((k: string) => d.event_study[k].meta.name);
    return {
      legend: { top: 0, right: 0, textStyle: { color: C.text } },
      grid: { left: 250, right: 24, top: 30, bottom: 30 },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (v: number) => pct(v, 2) },
      xAxis: { type: "value", ...axis({ axisLabel: { color: C.dim, formatter: (v: number) => pct(v, 1) } }) },
      yAxis: { type: "category", data: names, inverse: true, ...axis({ splitLine: { show: false }, axisLabel: { color: C.text } }) },
      series: [
        { name: "Signal: 60d excess vs SPY", type: "bar", data: keys.map((k: string) => d.placebo[k].real_mean), itemStyle: { color: C.gold }, barGap: "10%" },
        { name: "Same stocks, random dates", type: "bar", data: keys.map((k: string) => d.placebo[k].placebo_mean), itemStyle: { color: C.gray } },
      ],
    };
  }, [d]);

  return (
    <div className="fade">
      <PageHead kicker="Executive overview" title="Does following the smart money generate alpha?"
        sub="This platform compares three sources of market information — corporate insiders, institutional investors and the crowd — on identical, look-ahead-free terms: what was knowable, when it became public, and what happened next." />
      {s.error && <ErrorBox error={s.error} />}
      {!d ? <Loading h={400} /> : <>
        <div className="grid g4">
          <Stat label="Insider trades analysed" value={int(d.data_quality.n_transactions)} foot={`SEC Form 4 · ${d.data_quality.first_filing.slice(0, 4)}–${d.data_quality.last_filing.slice(0, 4)}`} />
          <Stat label="Signal events evaluated" value={int(Object.values(d.event_study).reduce((a: number, e: Json) => a + e.meta.n_events, 0))} foot={`${d.signals.length} pre-registered signals`} />
          <Stat label="Mapping errors caught" value={int(d.coverage.symbol_status.failed)} foot="tickers rejected by price reconciliation" />
          <Stat label="Sample split" value={`≤${d.config.in_sample_end.slice(0, 4)} | 2019+`} foot="in-sample · out-of-sample" />
        </div>

        <Section title="Phase 1 findings · corporate insiders">
          <div className="grid g3">
            <Card className="span2" title="What the evidence says" desc="Generated from the computed results; every number links to the Insider Activity page.">
              {fs.map((f, i) => (
                <div className="finding" key={i}><div className="n">{String(i + 1).padStart(2, "0")}</div>
                  <div><div className="h">{f.h}</div><div className="b">{f.b}</div></div></div>
              ))}
            </Card>
            <div className="grid" style={{ alignContent: "start" }}>
              <Card title="Verdict so far" right={<span className="pill warn">Insiders</span>}>
                <Verdict d={d} />
                <div className="dim" style={{ marginTop: 10, fontSize: 11.5 }}>Survivorship caveat: {pct(1 - d.coverage.buy_events_priced / d.coverage.buy_events_total, 0, false)} of buy events are in companies without free price history (mostly delisted). See Methodology.</div>
              </Card>
              <Card title="Research roadmap">
                {[["Corporate insiders", "Form 4", "Complete", "live"], ["Institutions", "Form 13F", "Complete", "live"], ["Wikipedia attention", "Crowd", "Complete", "live"],
                  ["Reddit", "Crowd", "Blocked (API 403)", ""], ["News · Google Trends", "Crowd", "Blocked (unreachable)", ""],
                  ["Disagreement · Lead-lag", "Synthesis", "Complete", "live"], ["AI summaries", "Phase 6", "Needs API key", ""]].map(([a, b, c, t]) => (
                  <div key={a} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
                    <span>{a} <span className="dim">· {b}</span></span><span className={`tag ${t}`}>{c}</span></div>))}
              </Card>
            </div>
          </div>
        </Section>

        <InstFindings />
        <CrossFindings />
        <Section title="Signal vs. its own baseline">
          <Card desc="Gold: mean 60-day excess return vs SPY after the signal. Grey: the same stocks on random dates. The gap, not the gold bar alone, is the information content of insider timing.">
            <Chart option={chart} height={440} />
            <div style={{ marginTop: 10 }}><Link to="/insiders">Full insider research →</Link> · <Link to="/radar/JPM">Try the Radar on JPM →</Link></div>
          </Card>
        </Section>
      </>}
    </div>
  );
}

/** Phase 2 findings, generated from the institutional results. */
function InstFindings() {
  const r = useApi("/api/institutions/summary");
  const d: Json = r.data;
  if (!d) return null;
  const ff = (k: string, smp = "full") => d.portfolios[k]?.[smp]?.ff6;
  const bulls = d.signals.filter((s: Json) => s.direction > 0).map((s: Json) => s.key);
  const posFF = bulls.filter((k: string) => (ff(k)?.alpha_t ?? 0) >= 1.96).length;
  const belowPlacebo = bulls.filter((k: string) => (d.placebo[k]?.difference ?? 0) < 0).length;
  const sell = ff("HF_CROWD_SELL"), sellO = ff("HF_CROWD_SELL", "out_of_sample");
  const items = [
    { h: "Copying 13F filings after disclosure does not earn alpha.",
      b: <>{posFF === 0 ? "None" : posFF} of {bulls.length} bullish 13F signals earn a significantly positive net factor alpha; {belowPlacebo} of {bulls.length} do worse than the same stocks on random dates. The median manager files {num(d.coverage.filing_lag_days.median, 0)} days after quarter end — the information is stale by the time it is public.</> },
    { h: "What hedge funds collectively dump keeps underperforming.",
      b: <>Stocks that ≥ 3 concentrated managers exit or cut have a net Fama-French 5 + momentum alpha of <b className={tone(sell?.alpha_ann)}>{pct(sell?.alpha_ann, 1)}</b>/yr (t = {num(sell?.alpha_t)}), and {pct(sellO?.alpha_ann, 1)}/yr out-of-sample (t = {num(sellO?.alpha_t)}).</> },
  ];
  return (
    <Section title="Phase 2 findings · institutional investors (13F)">
      <Card desc="Generated from the institutional results; see Institutional Holdings for every number.">
        {items.map((f, i) => <div className="finding" key={i}><div className="n">{String(i + 8).padStart(2, "0")}</div><div><div className="h">{f.h}</div><div className="b">{f.b}</div></div></div>)}
      </Card>
    </Section>
  );
}

/** Phase 3 + 5 findings, generated from the crowd and synthesis results. */
function CrossFindings() {
  const c = useApi("/api/crowd/summary");
  const x = useApi("/api/synthesis/summary");
  const C_: Json = c.data, X: Json = x.data;
  if (!C_ || !X) return null;
  const sigCrowd = Object.entries(C_.portfolios).filter(([, v]: [string, Json]) => Math.abs(v.full?.ff6?.alpha_t ?? 0) >= 1.96).length;
  const sp = C_.event_study.WIKI_SPIKE.full["60"], ra = C_.event_study.WIKI_SPIKE_RALLY?.full["60"];
  const g = X.who_moves_first.attention_returns.granger;
  const hr = Object.fromEntries(X.horse_race.full.coefficients.map((k: Json) => [k.name, k]));
  const hro = Object.fromEntries(X.horse_race.out_of_sample.coefficients.map((k: Json) => [k.name, k]));
  const ab = X.event_study.ALIGNED_BULL?.full["60"], abf = X.portfolios.ALIGNED_BULL?.full?.ff6;
  const LABEL: Record<string, string> = { insiders_buying: "insider buying", insiders_selling: "insider selling", inst_accumulating: "institutional accumulation",
    inst_distributing: "institutional distribution", attention_high: "high crowd attention", attention_low: "low crowd attention" };
  const top = [...X.horse_race.full.coefficients].sort((a: Json, b: Json) => Math.abs(b.t ?? 0) - Math.abs(a.t ?? 0))[0];
  const items = [
    { h: "Crowd attention does not predict returns — prices lead attention.",
      b: <>Wikipedia attention spikes are followed by a {pct(sp.mean, 2)} 60-day excess return (t = {num(sp.t)}); {sigCrowd === 0 ? "no" : sigCrowd} attention signal{sigCrowd === 1 ? " has" : "s have"} a significant net factor alpha.{ra ? <> Spikes after rallies show the meme-stock signature: mean {pct(ra.mean, 1)}, median {pct(ra.median, 1)}.</> : null} In Granger tests, returns predict attention in {pct(g.returns_cause_attention_share, 0, false)} of stocks vs. attention predicting returns in {pct(g.attention_causes_returns_share, 0, false)} (5% expected by chance).</> },
    { h: `When the groups are compared head-to-head, ${LABEL[top.name] ?? top.name} has the strongest incremental association (|t| = ${num(Math.abs(top.t ?? 0))}${Math.abs(top.t ?? 0) >= 1.96 ? "" : ", not significant"}).`,
      b: <>Holding the other groups' states fixed, insider buying is associated with <b className={tone(hr.insiders_buying.coef)}>{pct(hr.insiders_buying.coef, 2)}</b> extra 60-day return (t = {num(hr.insiders_buying.t)}; out-of-sample {pct(hro.insiders_buying.coef, 2)}, t = {num(hro.insiders_buying.t)}), institutional accumulation {pct(hr.inst_accumulating.coef, 2)} (t = {num(hr.inst_accumulating.t)}), and high crowd attention {pct(hr.attention_high.coef, 2)} (t = {num(hr.attention_high.t)}).</> },
    { h: "Agreement between insiders and institutions does not create an edge.",
      b: <>When insiders buy and institutions accumulate at the same time ({int(ab?.n)} cases), the 60-day excess return is {pct(ab?.mean, 2)} (t = {num(ab?.t)}) and the net factor alpha {pct(abf?.alpha_ann, 1)}/yr (t = {num(abf?.alpha_t)}). Combining two weak signals did not make a strong one.</> },
  ];
  return (
    <Section title="Phase 3 & 5 findings · the crowd, and the groups compared">
      <Card desc="Generated from the crowd and synthesis results; see Crowd Attention, Disagreement Engine and Who Moves First?">
        {items.map((f, i) => <div className="finding" key={i}><div className="n">{String(i + 10).padStart(2, "0")}</div><div><div className="h">{f.h}</div><div className="b">{f.b}</div></div></div>)}
      </Card>
    </Section>
  );
}
