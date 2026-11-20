"""SEC 'Insider Transactions Data Sets' (Forms 3/4/5) — bulk historical ingestion.

Source: https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets
The SEC publishes one zip per calendar quarter containing the XML Forms 3/4/5
flattened into TSV tables. These are the SEC's own extraction of the filings, so
they are the most authoritative free source for history. Recent filings that are
not yet in a published quarter are fetched live by ``sec_form4_xml``.

Output: data/processed/insider_tx/<yyyy>q<q>.parquet — one row per
non-derivative transaction, joined to its filing (submission) and reporting
owner(s), with footnote-derived quality flags attached to the specific
transaction (not just the filing).
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config, http

log = logging.getLogger(__name__)

# The SEC moved newer quarters to a second path in 2026; try both.
BASES = ["https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{y}q{q}_form345.zip",
         "https://www.sec.gov/files/datastandardsinnovation/data/insider-transactions-data-sets/{y}q{q}_form345.zip"]
RAW_DIR = config.RAW / "sec_form345"
OUT_DIR = config.PROCESSED / "insider_tx"

# Footnote language is free text; these patterns flag transactions that are not
# discretionary open-market decisions. Matching is done on the footnotes that a
# transaction row actually references.
PAT_PLAN = re.compile(r"10\s*b\s*-?\s*5\s*-?\s*1|trading plan", re.I)
PAT_DRIP = re.compile(r"dividend\s+reinvest|\bDRIP\b|\bDSPP\b|direct stock purchase", re.I)
PAT_ESPP = re.compile(r"employee stock purchase|\bESPP\b|401\s*\(?k\)?|deferred compensation|savings plan", re.I)
PAT_OFFERING = re.compile(
    r"initial public offering|\bIPO\b|underwritten|public offering|registered direct|private placement|"
    r"subscription agreement|rights offering|securities purchase agreement|directed share", re.I)

FN_COLS = ["SECURITY_TITLE_FN", "TRANS_DATE_FN", "DEEMED_EXECUTION_DATE_FN", "EQUITY_SWAP_TRANS_CD_FN",
           "TRANS_TIMELINESS_FN", "TRANS_SHARES_FN", "TRANS_PRICEPERSHARE_FN", "TRANS_ACQUIRED_DISP_CD_FN",
           "SHRS_OWND_FOLWNG_TRANS_FN", "DIRECT_INDIRECT_OWNERSHIP_FN", "NATURE_OF_OWNERSHIP_FN"]


def quarters(start: tuple[int, int] = config.INGEST_START, end: tuple[int, int] | None = None):
    if end is None:
        today = dt.date.today()
        end = (today.year, (today.month - 1) // 3 + 1)
    y, q = start
    while (y, q) <= end:
        yield y, q
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)


def download(y: int, q: int, force: bool = False) -> Path | None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{y}q{q}_form345.zip"
    if path.exists() and not force:
        return path
    r = None
    for base in BASES:
        r = http.get(base.format(y=y, q=q), ok_404=True, timeout=300)
        if r is not None and r.content.startswith(b"PK"):
            break
        r = None
    if r is None:
        log.info("quarter %sq%s not published", y, q)
        return None
    path.write_bytes(r.content)
    return path


def _read(z: zipfile.ZipFile, name: str, usecols: list[str] | None = None) -> pd.DataFrame:
    member = next(n for n in z.namelist() if n.upper().endswith(name.upper() + ".TSV"))
    with z.open(member) as f:
        data = f.read()
    df = pd.read_csv(io.BytesIO(data), sep="\t", dtype=str, quoting=3, keep_default_na=False,
                     na_values=[""], on_bad_lines="skip", encoding_errors="replace")
    if usecols:
        for c in usecols:
            if c not in df.columns:
                df[c] = np.nan
        df = df[usecols]
    return df


def _date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, format="%d-%b-%Y", errors="coerce")


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _truthy(s: pd.Series) -> pd.Series:
    return s.fillna("").str.strip().str.lower().isin(["1", "true", "y", "yes"])


def _owners(o: pd.DataFrame) -> pd.DataFrame:
    """Collapse joint filers to one row per filing.

    A filing can list several reporting owners (e.g. a fund and its general
    partner) who report the SAME transactions jointly; transactions are not
    attributable to individual co-filers. The 'primary' owner is the natural
    person with an officer/director relationship when present.
    """
    o = o.copy()
    rel = o["RPTOWNER_RELATIONSHIP"].fillna("")
    o["is_director"] = rel.str.contains("Director")
    o["is_officer"] = rel.str.contains("Officer")
    o["is_ten_pct"] = rel.str.contains("TenPercent")
    o["is_other"] = rel.str.contains("Other")
    o["_rank"] = np.where(o.is_officer, 0, np.where(o.is_director, 1, np.where(o.is_ten_pct, 2, 3)))
    o = o.sort_values(["ACCESSION_NUMBER", "_rank"], kind="stable")
    agg = o.groupby("ACCESSION_NUMBER").agg(
        owner_cik=("RPTOWNERCIK", "first"), owner_name=("RPTOWNERNAME", "first"),
        officer_title=("RPTOWNER_TITLE", "first"), other_text=("RPTOWNER_TXT", "first"),
        owner_ciks=("RPTOWNERCIK", lambda x: ",".join(sorted(set(x.dropna())))),
        n_owners=("RPTOWNERCIK", "nunique"),
        is_director=("is_director", "any"), is_officer=("is_officer", "any"),
        is_ten_pct=("is_ten_pct", "any"), is_other=("is_other", "any"),
    )
    return agg.reset_index()


def _footnote_flags(t: pd.DataFrame, fn: pd.DataFrame) -> pd.DataFrame:
    """Per-transaction flags from the footnotes each transaction references."""
    fn = fn.dropna(subset=["FOOTNOTE_TXT"]).copy()
    txt = fn["FOOTNOTE_TXT"]
    fn["plan"] = txt.str.contains(PAT_PLAN)
    fn["drip"] = txt.str.contains(PAT_DRIP)
    fn["espp"] = txt.str.contains(PAT_ESPP)
    fn["offering"] = txt.str.contains(PAT_OFFERING)
    fn = fn[fn[["plan", "drip", "espp", "offering"]].any(axis=1)]
    flags = ["plan", "drip", "espp", "offering"]
    out = pd.DataFrame({"NONDERIV_TRANS_SK": t["NONDERIV_TRANS_SK"]})
    for f in flags:
        out["fn_" + f] = False
    if fn.empty:
        return out
    ids = t[["ACCESSION_NUMBER", "NONDERIV_TRANS_SK"]].copy()
    ids["fn"] = t[FN_COLS].fillna("").agg(",".join, axis=1)
    ids = ids.assign(fn=ids["fn"].str.split(",")).explode("fn")
    ids["fn"] = ids["fn"].str.strip()
    ids = ids[ids["fn"] != ""]
    m = ids.merge(fn[["ACCESSION_NUMBER", "FOOTNOTE_ID"] + flags], left_on=["ACCESSION_NUMBER", "fn"],
                  right_on=["ACCESSION_NUMBER", "FOOTNOTE_ID"], how="inner")
    g = m.groupby("NONDERIV_TRANS_SK")[flags].any()
    out = out.set_index("NONDERIV_TRANS_SK")
    for f in flags:
        out.loc[g.index, "fn_" + f] = g[f].values
    return out.reset_index()


def normalise_quarter(zpath: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zpath) as z:
        s = _read(z, "SUBMISSION", ["ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT", "DOCUMENT_TYPE",
                                    "ISSUERCIK", "ISSUERNAME", "ISSUERTRADINGSYMBOL", "REMARKS", "AFF10B5ONE"])
        o = _read(z, "REPORTINGOWNER", ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
                                        "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE", "RPTOWNER_TXT"])
        t = _read(z, "NONDERIV_TRANS", ["ACCESSION_NUMBER", "NONDERIV_TRANS_SK", "SECURITY_TITLE", "TRANS_DATE",
                                        "DEEMED_EXECUTION_DATE", "TRANS_FORM_TYPE", "TRANS_CODE",
                                        "EQUITY_SWAP_INVOLVED", "TRANS_TIMELINESS", "TRANS_SHARES",
                                        "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD", "SHRS_OWND_FOLWNG_TRANS",
                                        "DIRECT_INDIRECT_OWNERSHIP", "NATURE_OF_OWNERSHIP"] + FN_COLS)
        fn = _read(z, "FOOTNOTES", ["ACCESSION_NUMBER", "FOOTNOTE_ID", "FOOTNOTE_TXT"])

    s = s[s["DOCUMENT_TYPE"].isin(["4", "4/A", "5", "5/A"])]
    t = t[t["ACCESSION_NUMBER"].isin(s["ACCESSION_NUMBER"])].drop_duplicates("NONDERIV_TRANS_SK")
    flags = _footnote_flags(t, fn)
    filing_plan = (fn.dropna(subset=["FOOTNOTE_TXT"]).loc[lambda d: d.FOOTNOTE_TXT.str.contains(PAT_PLAN),
                                                          "ACCESSION_NUMBER"].unique())

    df = (t.drop(columns=FN_COLS)
          .merge(flags, on="NONDERIV_TRANS_SK", how="left")
          .merge(s, on="ACCESSION_NUMBER", how="inner")
          .merge(_owners(o), on="ACCESSION_NUMBER", how="left"))

    out = pd.DataFrame({
        "accession": df["ACCESSION_NUMBER"],
        "trans_sk": _num(df["NONDERIV_TRANS_SK"]).astype("Int64"),
        "filing_date": _date(df["FILING_DATE"]),
        "period_of_report": _date(df["PERIOD_OF_REPORT"]),
        "doc_type": df["DOCUMENT_TYPE"],
        "issuer_cik": _num(df["ISSUERCIK"]).astype("Int64"),
        "issuer_name": df["ISSUERNAME"],
        "issuer_symbol": df["ISSUERTRADINGSYMBOL"].fillna("").str.strip().str.upper(),
        "owner_cik": _num(df["owner_cik"]).astype("Int64"),
        "owner_name": df["owner_name"],
        "owner_ciks": df["owner_ciks"],
        "n_owners": _num(df["n_owners"]).astype("Int64"),
        "is_director": df["is_director"].fillna(False).astype(bool),
        "is_officer": df["is_officer"].fillna(False).astype(bool),
        "is_ten_pct": df["is_ten_pct"].fillna(False).astype(bool),
        "is_other": df["is_other"].fillna(False).astype(bool),
        "officer_title": df["officer_title"],
        "security_title": df["SECURITY_TITLE"],
        "trans_date": _date(df["TRANS_DATE"]),
        "trans_code": df["TRANS_CODE"].fillna("").str.strip().str.upper(),
        "equity_swap": _truthy(df["EQUITY_SWAP_INVOLVED"]),
        "timeliness": df["TRANS_TIMELINESS"].fillna("").str.strip().str.upper(),
        "shares": _num(df["TRANS_SHARES"]),
        "price": _num(df["TRANS_PRICEPERSHARE"]),
        "acq_disp": df["TRANS_ACQUIRED_DISP_CD"].fillna("").str.strip().str.upper(),
        "shares_after": _num(df["SHRS_OWND_FOLWNG_TRANS"]),
        "direct": df["DIRECT_INDIRECT_OWNERSHIP"].fillna("").str.strip().str.upper().eq("D"),
        "nature": df["NATURE_OF_OWNERSHIP"],
        "aff10b5one": _truthy(df["AFF10B5ONE"]),
        "filing_plan_mention": df["ACCESSION_NUMBER"].isin(filing_plan)
        | df["REMARKS"].fillna("").str.contains(PAT_PLAN),
        "fn_plan": df["fn_plan"].fillna(False).astype(bool),
        "fn_drip": df["fn_drip"].fillna(False).astype(bool),
        "fn_espp": df["fn_espp"].fillna(False).astype(bool),
        "fn_offering": df["fn_offering"].fillna(False).astype(bool),
    })
    out["source_quarter"] = zpath.name[:6]
    return out


def ingest(start=config.INGEST_START, end=None, force: bool = False) -> list[Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for y, q in quarters(start, end):
        dest = OUT_DIR / f"{y}q{q}.parquet"
        if dest.exists() and not force:
            written.append(dest)
            continue
        z = download(y, q)
        if z is None:
            continue
        df = normalise_quarter(z)
        df.to_parquet(dest, index=False)
        log.info("%sq%s: %d transactions", y, q, len(df))
        written.append(dest)
    return written


def load(columns: list[str] | None = None, filters=None) -> pd.DataFrame:
    files = sorted(OUT_DIR.glob("*.parquet"))
    if not files:
        raise FileNotFoundError("No insider data ingested. Run: python -m smartmoney.pipeline ingest")
    return pd.concat([pd.read_parquet(f, columns=columns, filters=filters) for f in files], ignore_index=True)
