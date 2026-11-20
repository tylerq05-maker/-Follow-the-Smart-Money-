import { ReactNode } from "react";

export function Card({ title, desc, right, children, className = "" }:
  { title?: ReactNode; desc?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={`card fade ${className}`}>
      {(title || right) && (
        <div className="card-h">
          <div>{title && <div className="card-t">{title}</div>}{desc && <div className="card-d">{desc}</div>}</div>
          {right}
        </div>
      )}
      {children}
    </div>
  );
}

export function Stat({ label, value, foot, cls = "" }: { label: string; value: ReactNode; foot?: ReactNode; cls?: string }) {
  return (
    <div className="card stat fade">
      <div className="label">{label}</div>
      <div className={`value ${cls}`}>{value}</div>
      {foot && <div className="foot">{foot}</div>}
    </div>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return <div className="section"><h3 className="section-t">{title}</h3>{children}</div>;
}

export function Loading({ h = 260 }: { h?: number }) {
  return <div className="skeleton" style={{ height: h }} />;
}

export function ErrorBox({ error }: { error: string }) {
  return <div className="callout warn"><b>Data unavailable.</b> {error}</div>;
}

export function Seg<T extends string>({ value, options, onChange }:
  { value: T; options: { v: T; l: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="seg" role="tablist">
      {options.map((o) => (
        <button key={o.v} role="tab" aria-selected={value === o.v} className={value === o.v ? "on" : ""}
          onClick={() => onChange(o.v)}>{o.l}</button>
      ))}
    </div>
  );
}

export function PageHead({ kicker, title, sub, right }: { kicker: string; title: string; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="topbar">
      <div><div className="kicker">{kicker}</div><h1 className="page-title">{title}</h1>{sub && <p className="page-sub">{sub}</p>}</div>
      {right}
    </div>
  );
}
