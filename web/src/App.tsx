import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Overview from "./pages/Overview";
import Radar from "./pages/Radar";
import Insiders from "./pages/Insiders";
import Methodology from "./pages/Methodology";
import Institutions from "./pages/Institutions";
import Crowd from "./pages/Crowd";
import { Disagreement, LeadLag, PerfScoreboard } from "./pages/Synthesis";

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
          <NavLink to="/institutions">Institutional Holdings <span className="tag live">P2</span></NavLink>
          <NavLink to="/crowd">Crowd Attention <span className="tag live">P3</span></NavLink>
          <div className="nav-group">Analytics</div>
          <NavLink to="/disagreement">Disagreement Engine <span className="tag gold">P5</span></NavLink>
          <NavLink to="/lead-lag">Who Moves First? <span className="tag gold">P5</span></NavLink>
          <NavLink to="/scoreboard">Performance Scoreboard <span className="tag gold">P5</span></NavLink>
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
          <Route path="/institutions" element={<Institutions />} />
          <Route path="/crowd" element={<Crowd />} />
          <Route path="/disagreement" element={<Disagreement />} />
          <Route path="/lead-lag" element={<LeadLag />} />
          <Route path="/scoreboard" element={<PerfScoreboard />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}
