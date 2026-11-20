import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Json, fetchJson, useApi } from "../api";
import Chart, { C, axis } from "../components/Chart";
import { Card, ErrorBox, Loading, PageHead, Section, Seg } from "../components/ui";
import { int, isNum, num, pct, tone, usd, usdShort } from "../format";

const POPULAR = ["AAPL", "NVDA", "TSLA", "GME", "AMZN", "JPM", "META", "OXY"];

function Search() {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [res, setRes] = useState<Json[]>([]);
  const [hl, setHl] = useState(0);
  const t = useRef<number>();
  useEffect(() => {
    window.clearTimeout(t.current);
    if (!q.trim()) { setRes([]); return; }
    t.current = window.setTimeout(() => fetchJson(`/api/search?q=${encodeURIComponent(q)}`).then(setRes).catch(() => setRes([])), 120);
  }, [q]);
  const go = (tk: string) => { setQ(""); setRes([]); nav(`/radar/${tk}`); };
  return (
    <div className="search">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
      <input placeholder="Search ticker or company…" value={q} aria-label="Search ticker"
        onChange={(e) => { setQ(e.target.value); setHl(0); }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") setHl((h) => Math.min(h + 1, res.length - 1));
          if (e.key === "ArrowUp") setHl((h) => Math.max(h - 1, 0));
          if (e.key === "Enter") go(res[hl]?.ticker ?? q.trim().toUpperCase());
        }} />
      {res.length > 0 && <div className="dd">{res.map((r, i) => (
        <div key={r.ticker} className={i === hl ? "hl" : ""} onMouseDown={() => go(r.ticker)}>
          <span className="mono" style={{ fontWeight: 600, width: 64 }}>{r.ticker}</span><span className="muted">{r.name}</span></div>))}</div>}
    </div>
  );
}

export default function Radar() {
  const { ticker } = useParams();
  const r = useApi(ticker ? `/api/company/${ticker}` : null);
  return (
    <div className="fade">
      <PageHead kicker="Smart Money Radar" title={ticker ? ticker.toUpperCase() : "Search any US-listed company"}
        sub={!ticker ? "One view of what insiders, institutions and the crowd are doing — with the evidence, the disagreements and what is missing." : undefined}
        right={<Search />} />
      {!ticker && (
        <Card title="Popular" desc="Insider history from SEC bulk data since 2009, plus Form 4 filings fetched live from EDGAR.">
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{POPULAR.map((t) => <Link key={t} to={`/radar/${t}`} className="pill mono" style={{ padding: "6px 12px" }}>{t}</Link>)}</div>
        </Card>)}
      {ticker && r.loading && <><Loading h={120} /><div style={{ height: 14 }} /><Loading h={340} /><div className="dim" style={{ marginTop: 8 }}>Fetching the latest Form 4 filings from EDGAR…</div></>}
      {ticker && r.error && <ErrorBox error={r.error} />}
      {r.data && <Company d={r.data} />}
    </div>
  );
}

function Company({ d }: { d: Json }) {
  const o = d.overview, p = d.price, ins = d.insiders;
  return (
    <>
      <div className="card fade" style={{ display: "grid", gridTemplateColumns: "1.4fr repeat(5, 1fr)", gap: 18, alignItems: "center" }}>
        <div>
          <div style={{ fontSize: 17, fontWeight: 650 }}>{o.name}</div>
          <div className="muted" style={{ marginTop: 2 }}>{o.ticker} · {o.exchange ?? "—"} · {o.sector ?? o.sic_description ?? "—"} · CIK {o.cik}</div>
        </div>
        <Kpi label={`Last close · ${p.last_date ?? ""}`} value={p.available ? `$${num(p.last_price)}` : "—"} />
        <Kpi label="Market cap" value={usd(o.market_cap)} foot={o.shares_outstanding_date ? `shares as of ${o.shares_outstanding_date}` : "shares unavailable"} />
        <Kpi label="1 month · YTD" value={<><span className={tone(p.returns?.["1m"])}>{pct(p.returns?.["1m"])}</span> · <span className={tone(p.returns?.ytd)}>{pct(p.returns?.ytd)}</span></>} />
        <Kpi label="1 year" value={<span className={tone(p.returns?.["1y"])}>{pct(p.returns?.["1y"])}</span>} foot={`vol ${pct(p.vol_1y, 0, false)}`} />
        <Kpi label="52-week range" value={p.available ? `${num(p.low_52w)} – ${num(p.high_52w)}` : "—"} />
      </div>

      <Section title="Three groups · current read">
        <div className="grid g3">
          <InsiderRead ins={ins} />
          <Pending name="Institutions" src="SEC Form 13F" phase={2} />
          <Pending name="The crowd" src="Reddit · news · Google Trends · Wikipedia" phase={3} />
        </div>
      </Section>

      {ins.available && <>
        <Section title="Price & insider disclosures">
          <Card desc="Markers at the FILING date (when the market could know), sized by dollar value. Green: discretionary purchases · Red: discretionary sales · Grey: Rule 10b5-1 planned sales.">
            {p.available ? <PriceChart p={p} tx={ins.recent} /> : <div className="empty">No price history available.</div>}
          </Card>
        </Section>
        <div className="grid g2" style={{ marginTop: 14 }}>
          <Card title="Activity windows" desc="Officer & director trades by filing date">
            <Windows w={ins.windows} />
          </Card>
          <Card title="Monthly insider flow (5 years)" desc="Dollar value by filing month; sales shown negative">
            <Flow f={ins.flow} />
          </Card>
        </div>
        <Section title="Signal history for this company">
          <Card desc="The same pre-registered rules used in the backtest, re-evaluated on this company's full filing history. Realised excess returns vs SPY from next-day entry; blank = not yet elapsed.">
            <SignalTable rows={ins.signals} />
          </Card>
        </Section>
        <div className="grid g3" style={{ marginTop: 14 }}>
          <Card title="Insiders" desc="Officers & directors with open-market trades" className="span2">
            <div className="scroll" style={{ maxHeight: 340 }}><table className="t"><thead><tr><th>Name</th><th>Role</th><th>Title</th><th className="r">Bought (disc.)</th><th className="r">Sold</th><th>Last filing</th></tr></thead>
              <tbody>{ins.insiders.map((r: Json) => <tr key={r.owner_cik}><td style={{ fontWeight: 500 }}>{r.name}</td><td className="muted">{r.role}</td><td className="dim">{(r.title || "").slice(0, 30)}</td>
                <td className="r num pos">{r.bought ? usd(r.bought) : "—"}</td><td className="r num neg">{r.sold ? usd(r.sold) : "—"}</td><td className="num muted">{r.last_filing}</td></tr>)}</tbody></table></div>
          </Card>
          <Card title="Filing mix" desc={`${int(ins.n_transactions)} non-derivative transactions since ${ins.first_filing}`}>
            <table className="t"><tbody>{ins.code_mix.slice(0, 9).map((r: Json) => <tr key={r.label}><td className="muted">{r.label}</td><td className="r num">{int(r.n)}</td></tr>)}</tbody></table>
            <div className="dim" style={{ fontSize: 11.5, marginTop: 8 }}>Only discretionary open-market trades are treated as signals.</div>
          </Card>
        </div>
        <Section title="Transactions">
          <Transactions rows={ins.recent} feed={ins.feed} />
        </Section>
      </>}
      <div className="dim" style={{ marginTop: 18, fontSize: 11.5 }}>Sources: <a href={d.sources.sec_filings} target="_blank" rel="noreferrer">SEC EDGAR filings for CIK {o.cik}</a> · {d.sources.prices}. AI research summary arrives in Phase 6; every statement here is computed directly from filings.</div>
    </>
  );
}

function Kpi({ label, value, foot }: { label: string; value: React.ReactNode; foot?: string }) {
  return <div className="stat"><div className="label">{label}</div><div className="num" style={{ fontSize: 16, fontWeight: 600, marginTop: 4 }}>{value}</div>{foot && <div className="dim" style={{ fontSize: 11 }}>{foot}</div>}</div>;
}

function Pending({ name, src, phase }: { name: string; src: string; phase: number }) {
  return (
    <Card title={name} desc={src} right={<span className="tag">Phase {phase}</span>}>
      <div className="muted">Not yet available. This source is added in Phase {phase}; no placeholder or simulated reading is shown.</div>
    </Card>
  );
}

/** Deterministic read of insider behaviour, with what it does and does not imply. */
function InsiderRead({ ins }: { ins: Json }) {
  if (!ins.available) return <Card title="Insiders" desc="SEC Form 4"><div className="muted">No Form 4 filings found for this issuer.</div></Card>;
  const w = ins.windows["180d"];
  const live = ins.signals.filter((s: Json) => { const dd = (Date.now() - new Date(s.filing_date).getTime()) / 864e5; return dd <= 90; });
  let label = "Quiet", cls = "", text = "No discretionary open-market trades by officers or directors in the last 180 days.";
  if (w.buy_value > 0 && w.buy_value >= w.discretionary_sell_value) {
    label = "Net discretionary buying"; cls = "pos";
    text = `${w.buyers} insider${w.buyers === 1 ? "" : "s"} bought ${usd(w.buy_value)} on the open market vs ${usd(w.discretionary_sell_value)} of discretionary sales.`;
  } else if (w.discretionary_sell_value > 0) {
    label = "Discretionary selling"; cls = "neg";
    text = `${w.sellers} insider${w.sellers === 1 ? "" : "s"} sold ${usd(w.discretionary_sell_value)} outside 10b5-1 plans${w.buy_value ? `; purchases ${usd(w.buy_value)}` : "; no purchases"}.`;
  } else if (w.planned_sell_value > 0) {
    label = "Planned selling only"; text = `${usd(w.planned_sell_value)} of sales, all under Rule 10b5-1 plans — historically uninformative in this study.`;
  }
  return (
    <Card title="Insiders" desc="SEC Form 4 · last 180 days" right={<span className={`pill ${cls}`}>{label}</span>}>
      <div>{text}</div>
      {live.length > 0 && <div style={{ marginTop: 8, display: "flex", gap: 6, flexWrap: "wrap" }}>{live.slice(0, 4).map((s: Json, i: number) =>
        <span key={i} className={`pill ${s.direction > 0 ? "pos" : s.direction < 0 ? "neg" : ""}`}>{s.signal_name} · {s.filing_date}</span>)}</div>}
      <div className="dim" style={{ marginTop: 8, fontSize: 11.5 }}>
        {w.planned_sell_value > 0 && <>Planned (10b5-1) sales {usd(w.planned_sell_value)} are excluded from the read. </>}
        Historically, discretionary CEO/CFO sales were the most informative insider signal; purchases showed a same-stock edge but no factor-adjusted alpha.
        {ins.feed.live_error ? <span className="neg"> Live EDGAR feed unavailable: showing data to {ins.feed.last_bulk_filing}.</span> : <> Live through EDGAR ({ins.feed.live_filings} filings since {ins.feed.last_bulk_filing}).</>}
      </div>
    </Card>
  );
}

function PriceChart({ p, tx }: { p: Json; tx: Json[] }) {
  const option = useMemo(() => {
    const dates: string[] = p.series.dates;
    const first = dates[0];
    const pts = (q: string) => tx.filter((t) => t.quality === q && t.filing_date >= first)
      .reduce((m: Map<string, number>, t) => m.set(t.filing_date, (m.get(t.filing_date) ?? 0) + (t.value ?? 0)), new Map());
    const close = new Map<string, number>(dates.map((d, i) => [d, p.series.close[i]]));
    const near = (d: string) => { const i = dates.findIndex((x) => x >= d); return i < 0 ? dates.length - 1 : i; };
    const mk = (q: string, color: string, sym: string, name: string) => ({
      name, type: "scatter", symbol: sym, itemStyle: { color, opacity: 0.85 },
      symbolSize: (v: number[]) => Math.max(7, Math.min(26, 4 + Math.log10(Math.max(v[2], 1)) * 2.4)),
      data: [...pts(q).entries()].map(([d, v]) => { const i = near(d); return [dates[i], close.get(dates[i]), v]; }),
      tooltip: { trigger: "item", formatter: (x: Json) => `${name}<br/>filed ${x.data[0]} · ${usd(x.data[2])}` },
    });
    return {
      legend: { top: 0, right: 0, textStyle: { color: C.text } }, grid: { left: 56, right: 18, top: 30, bottom: 50 },
      tooltip: { trigger: "axis" },
      dataZoom: [{ type: "inside" }, { type: "slider", height: 16, bottom: 6, borderColor: C.line, textStyle: { color: C.dim }, fillerColor: "rgba(201,169,110,.12)" }],
      xAxis: { type: "category", data: dates, ...axis({ splitLine: { show: false } }) },
      yAxis: { type: "value", scale: true, ...axis({ axisLabel: { color: C.dim, formatter: (v: number) => `$${v}` } }) },
      series: [
        { name: "Close", type: "line", data: p.series.close, symbol: "none", lineStyle: { color: C.blue, width: 1.4 }, areaStyle: { color: "rgba(143,168,214,.06)" } },
        mk("discretionary_buy", C.pos, "triangle", "Discretionary buy"),
        mk("discretionary_sell", C.neg, "pin", "Discretionary sale"),
        mk("planned_sell", C.gray, "circle", "10b5-1 sale"),
      ],
    };
  }, [p, tx]);
  return <Chart option={option} height={340} />;
}

function Windows({ w }: { w: Json }) {
  const rows: [string, string][] = [["buy_value", "Discretionary purchases"], ["buyers", "Distinct buyers"], ["discretionary_sell_value", "Discretionary sales"],
    ["sellers", "Distinct discretionary sellers"], ["planned_sell_value", "10b5-1 planned sales"]];
  return (
    <table className="t"><thead><tr><th></th><th className="r">90 days</th><th className="r">180 days</th><th className="r">1 year</th></tr></thead>
      <tbody>{rows.map(([k, l]) => <tr key={k}><td className="muted">{l}</td>{["90d", "180d", "365d"].map((x) =>
        <td key={x} className={`r num ${k === "buy_value" && w[x][k] ? "pos" : k === "discretionary_sell_value" && w[x][k] ? "neg" : ""}`}>{k.endsWith("value") ? usd(w[x][k]) : int(w[x][k])}</td>)}</tr>)}</tbody></table>
  );
}

function Flow({ f }: { f: Json }) {
  const option = useMemo(() => ({
    legend: { top: 0, right: 0, textStyle: { color: C.text } }, grid: { left: 70, right: 12, top: 30, bottom: 30 },
    tooltip: { trigger: "axis", valueFormatter: (v: number) => usd(v) },
    xAxis: { type: "category", data: f.months, ...axis({ splitLine: { show: false } }) },
    yAxis: { type: "value", ...axis({ axisLabel: { color: C.dim, formatter: (v: number) => usdShort(v) } }) },
    series: [
      { name: "Disc. buys", type: "bar", stack: "f", data: f.discretionary_buy, itemStyle: { color: C.pos } },
      { name: "Disc. sales", type: "bar", stack: "f", data: (f.discretionary_sell || []).map((x: number) => -x), itemStyle: { color: C.neg } },
      { name: "10b5-1 sales", type: "bar", stack: "f", data: (f.planned_sell || []).map((x: number) => -x), itemStyle: { color: C.gray } },
    ],
  }), [f]);
  return f.months.length ? <Chart option={option} height={240} /> : <div className="empty">No open-market activity in 5 years.</div>;
}

function SignalTable({ rows }: { rows: Json[] }) {
  if (!rows.length) return <div className="empty">No signal has triggered for this company.</div>;
  return (
    <div className="scroll" style={{ maxHeight: 360 }}><table className="t"><thead><tr><th>Filed</th><th>Signal</th><th className="r">Insiders (30d)</th><th className="r">Value</th><th className="r">Prior 60d</th><th className="r">+5d</th><th className="r">+20d</th><th className="r">+60d</th><th className="r">+180d</th><th>Universe</th></tr></thead>
      <tbody>{rows.map((r, i) => {
        const dir = r.direction || 1;
        return (<tr key={i}><td className="num muted">{r.filing_date}</td>
          <td><span className={`pill ${r.direction > 0 ? "pos" : r.direction < 0 ? "neg" : ""}`}>{r.signal_name}</span></td>
          <td className="r num">{int(r.n_insiders_30d)}</td><td className="r num">{usd(r.value)}</td>
          <td className={`r num ${tone(r.prior_ret_60d)}`}>{pct(r.prior_ret_60d, 0)}</td>
          {["exc_5", "exc_20", "exc_60", "exc_180"].map((k) => <td key={k} className={`r num ${tone(isNum(r[k]) ? r[k] * dir : null)}`}>{pct(r[k], 1)}</td>)}
          <td>{r.investable ? <span className="dim">investable</span> : <span className="pill warn">illiquid</span>}</td></tr>);
      })}</tbody></table></div>
  );
}

function Transactions({ rows, feed }: { rows: Json[]; feed: Json }) {
  const [f, setF] = useState<"om" | "disc" | "all">("om");
  const shown = rows.filter((r) => f === "all" || (f === "om" ? ["P", "S"].includes(r.trans_code) : ["discretionary_buy", "discretionary_sell"].includes(r.quality)));
  const ql: Record<string, [string, string]> = { discretionary_buy: ["Discretionary buy", "pos"], discretionary_sell: ["Discretionary sale", "neg"], planned_sell: ["10b5-1 sale", ""], planned_buy: ["10b5-1 buy", ""], excluded: ["Not a signal", "warn"] };
  return (
    <Card desc={`Latest 200 transactions · bulk data through ${feed.last_bulk_filing}, ${feed.live_filings} newer filings parsed live from EDGAR`}
      right={<Seg value={f} onChange={setF} options={[{ v: "om", l: "Open market" }, { v: "disc", l: "Discretionary" }, { v: "all", l: "All codes" }]} />}>
      <div className="scroll"><table className="t"><thead><tr><th>Filed</th><th>Traded</th><th>Insider</th><th>Role</th><th>Type</th><th className="r">Shares</th><th className="r">Price</th><th className="r">Value</th><th className="r">Δ holdings</th><th>Classification</th><th>Source</th></tr></thead>
        <tbody>{shown.map((r, i) => {
          const [lbl, c] = ql[r.quality] ?? ["—", ""];
          return (<tr key={i}><td className="num muted">{r.filing_date}</td><td className="num dim">{r.trans_date}{r.late_filing ? <span className="pill warn" style={{ marginLeft: 4 }}>late</span> : null}</td>
            <td style={{ fontWeight: 500 }}>{r.owner_name}</td><td className="muted">{r.role}</td><td className="muted">{r.tx_label}</td>
            <td className="r num">{int(r.shares)}</td><td className="r num">{isNum(r.price) && r.price > 0 ? `$${num(r.price)}` : "—"}</td>
            <td className="r num">{r.price > 0 ? usd(r.value) : "—"}</td><td className={`r num ${tone(r.holding_change)}`}>{isNum(r.holding_change) ? pct(r.holding_change, 0) : "—"}</td>
            <td><span className={`pill ${c}`} title={r.exclusion_reason || ""}>{lbl}</span></td>
            <td><a href={r.accession_url} target="_blank" rel="noreferrer" className="mono" style={{ fontSize: 11 }}>{r.source === "edgar_live" ? "EDGAR·live" : "SEC"}</a></td></tr>);
        })}</tbody></table>{!shown.length && <div className="empty">No transactions for this filter.</div>}</div>
    </Card>
  );
}
