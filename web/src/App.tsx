import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Overview from "./pages/Overview";
import Radar from "./pages/Radar";
import Insiders from "./pages/Insiders";
import Methodology from "./pages/Methodology";
import Planned from "./pages/Planned";

const PLANNED = [
  { path: "institutions", label: "Institutional Holdings", phase: 2 },
  { path: "crowd", label: "Crowd Sentiment", phase: 3 },
  { path: "disagreement", label: "Disagreement Engine", phase: 5 },
  { path: "lead-lag", label: "Who Moves First?", phase: 5 },
  { path: "scoreboard", label: "Performance Scoreboard", phase: 5 },
];

export default function App() {
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <div className="brand-title">Follow the <span>Smart Money?</span></div>
          <div className="brand-sub">Alternative-data alpha research</div>
        </div>
        <nav className="nav">
          <div className="nav-group">Research</div>
          <NavLink to="/" end>Executive Overview</NavLink>
          <NavLink to="/radar">Smart Money Radar <span className="tag gold">Live</span></NavLink>
          <div className="nav-group">Sources</div>
          <NavLink to="/insiders">Insider Activity <span className="tag live">P1</span></NavLink>
          {PLANNED.slice(0, 2).map((p) => (
            <NavLink key={p.path} to={`/${p.path}`}>{p.label} <span className="tag">P{p.phase}</span></NavLink>
          ))}
          <div className="nav-group">Analytics</div>
          {PLANNED.slice(2).map((p) => (
            <NavLink key={p.path} to={`/${p.path}`}>{p.label} <span className="tag">P{p.phase}</span></NavLink>
          ))}
          <NavLink to="/methodology">Methodology &amp; Validation</NavLink>
        </nav>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/radar" element={<Radar />} />
          <Route path="/radar/:ticker" element={<Radar />} />
          <Route path="/insiders" element={<Insiders />} />
          <Route path="/methodology" element={<Methodology />} />
          {PLANNED.map((p) => <Route key={p.path} path={`/${p.path}`} element={<Planned label={p.label} phase={p.phase} />} />)}
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}
