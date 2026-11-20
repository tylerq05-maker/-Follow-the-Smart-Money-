"""Wikipedia attention: ticker -> article (Wikidata) and daily pageviews (Wikimedia REST API).

Pageviews for UTC day d are published after the day ends, so a value for day d is
treated as public on d + 1 (see research/crowd_study.py). Counts are 'user' agent
traffic (Wikimedia's bot/spider filter applied) for the exact canonical article title.
Data from 2015-07-01 (start of the pageviews API).
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, unquote

import pandas as pd

from .. import config, http

log = logging.getLogger(__name__)
UA = {"User-Agent": "FollowTheSmartMoney/0.2 (academic research project; contact admin@followthesmartmoney.dev)"}
SPARQL = "https://query.wikidata.org/sparql"
PV = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
      "{title}/daily/20150701/{end}")
MAP_PATH = config.PROCESSED / "wiki_articles.parquet"
RAW = config.RAW / "wiki"
EXCHANGES = {"Q13677": "NYSE", "Q82059": "NASDAQ", "Q1194269": "NYSE American"}


def ticker_articles(refresh: bool = False) -> pd.DataFrame:
    if MAP_PATH.exists() and not refresh:
        return pd.read_parquet(MAP_PATH)
    vals = " ".join(f"wd:{q}" for q in EXCHANGES)
    q = f"""SELECT ?ticker ?article ?exch WHERE {{ ?item p:P414 ?ex . ?ex pq:P249 ?ticker . ?ex ps:P414 ?exch .
            VALUES ?exch {{ {vals} }} FILTER NOT EXISTS {{ ?ex pq:P582 ?end }}
            ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> . }}"""
    r = http.get(SPARQL, params={"query": q, "format": "json"}, headers={**UA, "Accept": "application/sparql-results+json"},
                 timeout=180)
    rows = [{"ticker": b["ticker"]["value"].strip().upper(), "exchange": EXCHANGES[b["exch"]["value"].rsplit("/", 1)[-1]],
             "title": unquote(b["article"]["value"].rsplit("/wiki/", 1)[-1])} for b in r.json()["results"]["bindings"]]
    df = pd.DataFrame(rows).drop_duplicates(["ticker", "title"])
    # Ambiguous tickers (several articles) are dropped rather than guessed.
    df = df[~df.duplicated("ticker", keep=False)].reset_index(drop=True)
    df.to_parquet(MAP_PATH, index=False)
    log.info("wikidata: %d ticker->article mappings", len(df))
    return df


def fetch_views(ticker: str, title: str) -> dict:
    p = RAW / f"{ticker}.parquet"
    if p.exists():
        return {"ticker": ticker, "status": "cached"}
    end = (pd.Timestamp.utcnow() - pd.Timedelta(days=1)).strftime("%Y%m%d")
    url = PV.format(title=quote(title.replace(" ", "_"), safe=""), end=end)
    for attempt in range(30):  # Wikimedia throttles shared IPs: back off patiently instead of dropping tickers
        try:
            r = http._session().get(url, headers=UA, timeout=60)
        except Exception as e:
            time.sleep(10)
            continue
        if r.status_code == 429:
            time.sleep(min(300, 60 * (1 + attempt // 3)))
            continue
        break
    else:
        return {"ticker": ticker, "status": "throttled"}
    if r.status_code == 404:
        return {"ticker": ticker, "status": "not_found"}
    if r.status_code != 200:
        return {"ticker": ticker, "status": f"http {r.status_code}"}
    items = r.json().get("items", [])
    df = pd.DataFrame({"date": pd.to_datetime([i["timestamp"][:8] for i in items]), "views": [i["views"] for i in items]})
    RAW.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    time.sleep(1.0)  # ~1 request/second: polite for a shared IP
    return {"ticker": ticker, "status": "ok", "n": len(df)}


def build(tickers: list[str] | None = None, workers: int = 4) -> pd.DataFrame:
    m = ticker_articles()
    if tickers is not None:
        m = m[m.ticker.isin(tickers)]
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda r: fetch_views(r.ticker, r.title), m.itertuples()))
    st = pd.DataFrame(res)
    log.info("wiki pageviews: %s", st.status.value_counts().to_dict())
    return st


def load(ticker: str) -> pd.DataFrame | None:
    p = RAW / f"{ticker}.parquet"
    return pd.read_parquet(p) if p.exists() else None
