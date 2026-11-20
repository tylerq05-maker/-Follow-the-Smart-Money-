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
                {[["Corporate insiders", "Form 4", "Complete", "live"], ["Institutions", "Form 13F", "Phase 2", ""], ["Reddit · Wikipedia", "Crowd", "Phase 3", ""],
                  ["News · Google Trends", "Crowd", "Phase 4", ""], ["Disagreement · Lead-lag", "Synthesis", "Phase 5", ""]].map(([a, b, c, t]) => (
                  <div key={a} style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
                    <span>{a} <span className="dim">· {b}</span></span><span className={`tag ${t}`}>{c}</span></div>))}
              </Card>
            </div>
          </div>
        </Section>

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
