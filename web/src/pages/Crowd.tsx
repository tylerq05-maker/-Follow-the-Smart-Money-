import { useMemo, useState } from "react";
import { Json, useApi } from "../api";
import Chart, { C, axis } from "../components/Chart";
import { Card, ErrorBox, Loading, PageHead, Section, Seg, Stat } from "../components/ui";
import { int, num, pct } from "../format";
import { CurveChart, Grid, Heatmap, PathChart, PlaceboChart, PortfolioCard, Robust, Scoreboard, SkewCard, YearChart, dirLabel } from "./Insiders";

type Sample = "full" | "in_sample" | "out_of_sample";
const SAMPLES: { v: Sample; l: string }[] = [{ v: "full", l: "Full" }, { v: "in_sample", l: "In-sample ≤2018" }, { v: "out_of_sample", l: "Out-of-sample 2019+" }];

export default function Crowd() {
  const s = useApi("/api/crowd/summary");
  const paths = useApi("/api/crowd/paths");
  const curves = useApi("/api/crowd/curves");
  const [sel, setSel] = useState("WIKI_SPIKE");
  const [sample, setSample] = useState<Sample>("full");
  const head = <PageHead kicker="Phase 3 · The crowd · Wikipedia attention" title="Does crowd attention predict returns?" />;
  if (s.error) return <>{head}<ErrorBox error={s.error} /></>;
  if (!s.data) return <>{head}<Loading h={500} /></>;
  const d: Json = s.data;
  const keys: string[] = d.signals.map((x: Json) => x.key).filter((k: string) => d.event_study[k]);
  const es = d.event_study[sel] ?? d.event_study[keys[0]];
  const cov = d.coverage;
  return (
    <div className="fade">
      <PageHead kicker="Phase 3 · The crowd · Wikipedia attention" title="Does crowd attention predict returns?"
        sub="Daily English-Wikipedia page views for every NYSE/Nasdaq company with an article, 2016 onward. Attention is not sentiment: a spike says people are looking, not that they are buying — so the generic spike signal has no assumed direction and is tested two-sided."
        right={<Seg value={sample} options={SAMPLES} onChange={setSample} />} />
      <div className="grid g4">
        <Stat label="Companies tracked" value={int(cov.tickers_with_views)} foot={`${int(cov.wikidata_mappings)} ticker→article links from Wikidata`} />
        <Stat label="Attention-spike days" value={int(cov.spike_days)} foot={`≥ ${d.config.spike_ratio}x the ${d.config.baseline_days}-day median, ≥ ${int(d.config.spike_min_views)} views`} />
        <Stat label="GameStop peak (known answer)" value={d.known_answers.gme_2021 ? `${num(d.known_answers.gme_2021.ratio, 0)}x` : "—"} foot={d.known_answers.gme_2021 ? `${int(d.known_answers.gme_2021.peak_views)} views on ${d.known_answers.gme_2021.peak_day}` : ""} />
        <Stat label="Timing" value="d + 1" foot="views for a UTC day count as public the next day" />
      </div>

      <Section title="Data-source status (no source is simulated)">
        <Card>
          <table className="t"><tbody>{Object.entries(d.sources_status).map(([k, v]) => (
            <tr key={k}><td style={{ fontWeight: 600, width: 160 }}>{k.replace("_", " ")}</td>
              <td><span className={`pill ${String(v).startsWith("live") ? "pos" : String(v).startsWith("reachable") ? "warn" : "neg"}`}>{String(v).split(":")[0]}</span></td>
              <td className="muted" style={{ whiteSpace: "normal" }}>{String(v).split(":").slice(1).join(":")}</td></tr>))}</tbody></table>
        </Card>
      </Section>

      <Section title="Known answer · GameStop, January 2021"><GmeChart k={d.known_answers.gme_2021} /></Section>

      <Section title="Signal scoreboard · 60-trading-day excess return vs SPY">
        <Card desc="Control-style (no-direction) signals are coloured by sign only; their p-values are two-sided.">
          <Scoreboard d={d} keys={keys} sel={sel} setSel={setSel} sample={sample} />
        </Card>
      </Section>
      <Section title="Excess return by horizon"><Card><Heatmap d={d} keys={keys} sample={sample} /></Card></Section>
      <Section title={`Signal detail · ${es.meta.name}`}>
        <div className="grid g3" style={{ marginBottom: 14 }}>
          <Card title="Definition" right={dirLabel(es.meta.direction)} className="span2">
            <div style={{ color: "var(--text)" }}>{es.meta.definition}</div><div className="muted" style={{ marginTop: 6 }}>{es.meta.rationale}</div>
            <div className="dim" style={{ marginTop: 8, fontSize: 11.5 }}>{int(es.meta.n_events)} events · {int(es.meta.n_issuers)} companies · {es.meta.first} → {es.meta.last}</div>
          </Card>
          <SkewCard es={es} />
        </div>
        <div className="grid g2">
          <Card title="Cumulative excess return after the attention shock">{paths.data ? <PathChart rows={paths.data[sel]} dir={es.meta.direction} /> : <Loading />}</Card>
          <Card title="Placebo · same stocks, random dates"><PlaceboChart p={d.placebo[sel]} /></Card>
          <Card title="Calendar-time portfolio (net of costs)">{curves.data ? <CurveChart c={curves.data[sel]} /> : <Loading />}</Card>
          <PortfolioCard p={d.portfolios[sel]} sample={sample} />
          <Card title="Stability by year"><YearChart rows={es.by_year} /></Card>
          <Card title="Robustness"><Robust es={es} /></Card>
        </div>
      </Section>
      <Section title="Spike-size sensitivity"><Card><Grid rows={d.robustness_grid} /></Card></Section>
    </div>
  );
}

function GmeChart({ k }: { k: Json }) {
  const option = useMemo(() => !k ? {} : ({
    legend: { top: 0, right: 0, textStyle: { color: C.text } }, grid: { left: 64, right: 16, top: 30, bottom: 30 },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: k.series.map((r: Json) => r.date), ...axis({ splitLine: { show: false } }) },
    yAxis: { type: "log", ...axis() },
    series: [
      { name: "Daily views", type: "line", data: k.series.map((r: Json) => r.views), symbol: "none", lineStyle: { color: C.gold, width: 2 } },
      { name: "60-day median baseline", type: "line", data: k.series.map((r: Json) => r.baseline), symbol: "none", lineStyle: { color: C.gray, type: "dashed" } },
    ],
  }), [k]);
  if (!k) return <div className="empty">GameStop page views not available.</div>;
  return <Card desc={`Peak ${int(k.peak_views)} views on ${k.peak_day} — ${pct(k.ratio - 1, 0)} above the trailing baseline (log scale). The system must flag this; tests/test_crowd.py asserts it.`}><Chart option={option} height={260} /></Card>;
}
