export const isNum = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);

export function pct(x: unknown, d = 1, sign = true): string {
  if (!isNum(x)) return "—";
  const v = x * 100;
  return `${sign && v > 0 ? "+" : ""}${v.toFixed(d)}%`;
}
export function num(x: unknown, d = 2): string {
  return isNum(x) ? x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }) : "—";
}
export function int(x: unknown): string {
  return isNum(x) ? Math.round(x).toLocaleString("en-US") : "—";
}
export function usd(x: unknown): string {
  if (!isNum(x)) return "—";
  const a = Math.abs(x);
  const s = x < 0 ? "-" : "";
  if (a >= 1e12) return `${s}$${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(1)}K`;
  return `${s}$${a.toFixed(a < 10 ? 2 : 0)}`;
}
export function usdShort(x: number): string {
  const a = Math.abs(x), s = x < 0 ? "-" : "";
  return a >= 1e9 ? `${s}$${+(a / 1e9).toFixed(1)}B` : a >= 1e6 ? `${s}$${+(a / 1e6).toFixed(0)}M` : a >= 1e3 ? `${s}$${+(a / 1e3).toFixed(0)}K` : `${s}$${a}`;
}
export const tone = (x: unknown, flip = false) => (!isNum(x) || x === 0 ? "" : (x > 0) !== flip ? "pos" : "neg");
export function tstat(t: unknown): string {
  if (!isNum(t)) return "—";
  const a = Math.abs(t);
  return `${t.toFixed(2)}${a >= 2.58 ? "***" : a >= 1.96 ? "**" : a >= 1.65 ? "*" : ""}`;
}
