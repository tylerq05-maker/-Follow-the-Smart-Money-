import { PageHead } from "../components/ui";

const SCOPE: Record<number, string[]> = {
  2: ["SEC Form 13F-HR ingestion and CUSIP → issuer mapping", "Quarter-over-quarter position changes: new, increased, reduced, exited",
    "Signals timed at the 13F FILING date (up to 45 days after quarter end) — never the unknown trade date",
    "Known-answer test: Berkshire Hathaway's reported Apple stake in the correct historical quarter"],
  3: ["Reddit mention volume, de-duplicated and bot-filtered", "LLM sentiment with a hand-labelled validation set (precision / recall / F1)",
    "Wikipedia page-view attention spikes", "Known-answer test: GameStop, January 2021"],
  5: ["Disagreement engine: insiders vs. institutions vs. crowd", "Combined-signal portfolios with walk-forward testing",
    "Lead-lag analysis: cross-correlation, lagged regressions, Granger tests (with stated caveats)", "Cross-source performance scoreboard"],
};

export default function Planned({ label, phase }: { label: string; phase: number }) {
  return (
    <div className="fade">
      <PageHead kicker={`Phase ${phase} · in development`} title={label}
        sub="This module is not built yet. Nothing on this page is simulated — when it ships, it will use the same timing, return and validation framework as the insider study." />
      <div className="card" style={{ maxWidth: 760 }}>
        <div className="card-t" style={{ marginBottom: 10 }}>Planned scope</div>
        {(SCOPE[phase] || []).map((s) => <div key={s} className="finding" style={{ gridTemplateColumns: "18px 1fr" }}><div className="n">·</div><div className="b">{s}</div></div>)}
      </div>
    </div>
  );
}
