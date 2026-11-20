import { useEffect, useState } from "react";

/* eslint-disable @typescript-eslint/no-explicit-any */
export type Json = any;

const cache = new Map<string, Promise<Json>>();

export function fetchJson(url: string, useCache = true): Promise<Json> {
  if (useCache && cache.has(url)) return cache.get(url)!;
  const p = fetch(url).then(async (r) => {
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`;
      try { msg = (await r.json()).detail ?? msg; } catch { /* not json */ }
      throw new Error(msg);
    }
    return r.json();
  });
  if (useCache) p.catch(() => cache.delete(url));
  cache.set(url, p);
  return p;
}

export function useApi<T = Json>(url: string | null) {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({
    data: null, error: null, loading: !!url,
  });
  useEffect(() => {
    if (!url) return;
    let live = true;
    setState({ data: null, error: null, loading: true });
    fetchJson(url)
      .then((d) => live && setState({ data: d, error: null, loading: false }))
      .catch((e) => live && setState({ data: null, error: String(e.message ?? e), loading: false }));
    return () => { live = false; };
  }, [url]);
  return state;
}
