"""SEC Form 13F data sets — institutional holdings, ingested from the SEC's structured extracts.

Source: https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets
Files are organised by FILING window (quarterly to 2023, rolling 3-month windows after), so
holdings are re-grouped by PERIODOFREPORT (quarter end) downstream.

What a 13F is — and is not:
  * a quarter-end SNAPSHOT of long US-listed equity positions of managers with >= $100M,
  * filed up to 45 days after quarter end (the information is public only at FILING),
  * silent on when within (or before) the quarter a position was traded,
  * silent on shorts, most derivatives, non-US holdings and positions under confidential treatment.
Accordingly every downstream signal is timed at the filing date, never the quarter end.

Kept: original 13F-HR holdings reports; common-share positions (SSHPRNAMTTYPE = 'SH', no put/call).
Amendments are excluded: they arrive later and would otherwise leak into earlier signals.
Value units: thousands of USD for filings before 2023-01-03, whole USD afterwards (normalised to USD).
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config, http

log = logging.getLogger(__name__)
LIST_URL = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
RAW_DIR = config.RAW / "sec_13f"
OUT_DIR = config.PROCESSED / "f13_holdings"
VALUE_IN_DOLLARS_FROM = pd.Timestamp("2023-01-03")


def list_files() -> list[str]:
    html = http.get(LIST_URL).text
    return sorted(set(re.findall(r'href="(/files/[^"]*form13f\.zip)"', html)))


def download(href: str) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    p = RAW_DIR / href.rsplit("/", 1)[-1]
    if not p.exists():
        r = http.get("https://www.sec.gov" + href, timeout=900)
        if not r.content.startswith(b"PK"):
            raise RuntimeError(f"not a zip: {href}")
        p.write_bytes(r.content)
    return p


def _read(z: zipfile.ZipFile, name: str, cols: list[str]) -> pd.DataFrame:
    member = next(n for n in z.namelist() if n.upper().endswith(name + ".TSV"))
    with z.open(member) as f:
        head = f.readline().decode("utf-8", "replace").rstrip("\r\n").split("\t")
    use = [c for c in cols if c in head]
    with z.open(member) as f:
        df = pd.read_csv(f, sep="\t", dtype=str, usecols=use, quoting=3, keep_default_na=False, na_values=[""],
                         on_bad_lines="skip", encoding_errors="replace")
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan
    return df[cols]


def normalise(zpath: Path) -> pd.DataFrame:
    with zipfile.ZipFile(zpath) as z:
        s = _read(z, "SUBMISSION", ["ACCESSION_NUMBER", "FILING_DATE", "SUBMISSIONTYPE", "CIK", "PERIODOFREPORT"])
        c = _read(z, "COVERPAGE", ["ACCESSION_NUMBER", "ISAMENDMENT", "FILINGMANAGER_NAME"])
        sm = _read(z, "SUMMARYPAGE", ["ACCESSION_NUMBER", "TABLEENTRYTOTAL", "TABLEVALUETOTAL", "ISCONFIDENTIALOMITTED"])
        it = _read(z, "INFOTABLE", ["ACCESSION_NUMBER", "NAMEOFISSUER", "TITLEOFCLASS", "CUSIP", "VALUE",
                                    "SSHPRNAMT", "SSHPRNAMTTYPE", "PUTCALL"])
    s = s[s["SUBMISSIONTYPE"] == "13F-HR"].merge(c, on="ACCESSION_NUMBER", how="left").merge(sm, on="ACCESSION_NUMBER", how="left")
    s["filing_date"] = pd.to_datetime(s["FILING_DATE"], format="%d-%b-%Y", errors="coerce")
    s["period"] = pd.to_datetime(s["PERIODOFREPORT"], format="%d-%b-%Y", errors="coerce")
    it = it[it["ACCESSION_NUMBER"].isin(s["ACCESSION_NUMBER"])]
    it = it[(it["SSHPRNAMTTYPE"].fillna("SH").str.upper() == "SH") & it["PUTCALL"].isna()]
    it = it.assign(cusip=it["CUSIP"].str.strip().str.upper().str.zfill(9).str[:9],
                   shares=pd.to_numeric(it["SSHPRNAMT"], errors="coerce"),
                   value=pd.to_numeric(it["VALUE"], errors="coerce"))
    it = it[(it.shares > 0) & it.cusip.str.match(r"^[0-9A-Z]{9}$")]
    # One position can be split over several rows (discretion / other-manager splits): aggregate.
    g = it.groupby(["ACCESSION_NUMBER", "cusip"], sort=False).agg(
        shares=("shares", "sum"), value=("value", "sum"), issuer_name=("NAMEOFISSUER", "first"),
        title=("TITLEOFCLASS", "first")).reset_index()
    df = g.merge(s[["ACCESSION_NUMBER", "CIK", "FILINGMANAGER_NAME", "filing_date", "period", "ISCONFIDENTIALOMITTED"]],
                 on="ACCESSION_NUMBER", how="inner")
    scale = np.where(df["filing_date"] < VALUE_IN_DOLLARS_FROM, 1000.0, 1.0)
    out = pd.DataFrame({
        "accession": df["ACCESSION_NUMBER"], "filer_cik": pd.to_numeric(df["CIK"], errors="coerce").astype("Int64"),
        "manager": df["FILINGMANAGER_NAME"], "filing_date": df["filing_date"], "period": df["period"],
        "cusip": df["cusip"], "issuer_name": df["issuer_name"], "title": df["title"],
        "shares": df["shares"].astype(float), "value_usd": df["value"].astype(float) * scale,
        "confidential_omitted": df["ISCONFIDENTIALOMITTED"].fillna("").str.upper().isin(["Y", "TRUE", "1"]),
    })
    out["source_file"] = zpath.name
    return out


def ingest_one(href: str) -> tuple[str, int | str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = href.rsplit("/", 1)[-1].replace(".zip", ".parquet")
    dest = OUT_DIR / name
    if dest.exists():
        return name, "cached"
    df = normalise(download(href))
    df.to_parquet(dest, index=False)
    return name, len(df)


def glob() -> str:
    return str(OUT_DIR / "*.parquet")
