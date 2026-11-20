"""Validation report: automated tests + live cross-checks against EDGAR.

    python -m smartmoney.validate            # writes data/results/validation.json

1. Runs the pytest suite and records every test's outcome.
2. Independent re-parse: fetches the raw Form 4 XML for a random sample of
   filings and compares each transaction with the SEC's bulk extraction.
3. Disclosure timing: measures how many Form 4s are accepted after the 4pm
   close (the reason entries are taken on the NEXT trading day).
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, http
from .sources import sec_form4_xml, sec_insider_bulk

log = logging.getLogger(__name__)
OUT = config.RESULTS / "validation.json"
TIMING_CIKS = [320193, 19617, 789019, 1045810, 1318605, 1018724, 1652044, 200406, 34088, 80424, 1067983, 1326801,
               21344, 77476, 310158, 51143, 732717, 1800, 66740, 18230, 97745, 1403161, 1551152, 1090872, 1166691]


def run_tests() -> dict:
    with tempfile.TemporaryDirectory() as td:
        xml = Path(td) / "junit.xml"
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", f"--junitxml={xml}", "-p", "no:cacheprovider"],
                           cwd=config.ROOT, capture_output=True, text=True)
        root = ET.parse(xml).getroot()
    tests = []
    for tc in root.iter("testcase"):
        status = "passed"
        msg = None
        for child in tc:
            if child.tag in ("failure", "error"):
                status, msg = "failed", (child.get("message") or "")[:300]
            elif child.tag == "skipped":
                status, msg = "skipped", (child.get("message") or "")[:200]
        tests.append({"module": tc.get("classname", "").split(".")[-1], "name": tc.get("name"), "status": status,
                      "message": msg, "seconds": float(tc.get("time", 0))})
    counts = pd.Series([t["status"] for t in tests]).value_counts().to_dict()
    return {"exit_code": p.returncode, "counts": counts, "tests": tests}


def xml_vs_bulk(n_filings: int = 60, seed: int = 3) -> dict:
    """Compare SEC bulk rows with our own parse of the original XML."""
    q = sorted(sec_insider_bulk.OUT_DIR.glob("*.parquet"))[-2]  # a complete, recent quarter
    bulk = pd.read_parquet(q)
    bulk = bulk[bulk.doc_type == "4"]
    accs = pd.Series(bulk.accession.unique()).sample(n_filings, random_state=seed)
    fields = ["trans_code", "shares", "price", "trans_date", "acq_disp", "shares_after"]
    checked, mismatches, n_tx, rounding = 0, [], 0, []
    for acc in accs:
        b = bulk[bulk.accession == acc].sort_values("trans_sk")
        cik = int(b.issuer_cik.iloc[0])
        try:
            idx = http.get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/index.json").json()
            doc = next(i["name"] for i in idx["directory"]["item"] if i["name"].endswith(".xml"))
            x = sec_form4_xml.parse_form4(sec_form4_xml.fetch_xml(cik, acc, doc), acc)
        except Exception as e:
            mismatches.append({"accession": acc, "field": "fetch", "detail": str(e)[:120]})
            continue
        checked += 1
        if len(x) != len(b):
            mismatches.append({"accession": acc, "field": "n_transactions", "bulk": len(b), "xml": len(x)})
            continue
        n_tx += len(b)
        for (_, rb), (_, rx) in zip(b.iterrows(), x.iterrows()):
            for f in fields:
                vb, vx = rb[f], rx[f]
                same = (pd.isna(vb) and pd.isna(vx)) or (
                    np.isclose(float(vb), float(vx), rtol=1e-6) if isinstance(vb, (int, float, np.number)) and not pd.isna(vb) and vx is not None and not pd.isna(vx)
                    else str(vb)[:10] == str(vx)[:10])
                if not same and f in ("price", "shares") and abs(float(vb) - float(vx)) < 0.006:
                    rounding.append({"accession": acc, "field": f, "bulk": str(vb), "xml": str(vx)})
                elif not same:
                    mismatches.append({"accession": acc, "field": f, "bulk": str(vb), "xml": str(vx)})
    return {"quarter": q.stem, "filings_sampled": n_filings, "filings_checked": checked, "transactions_compared": n_tx,
            "fields": fields, "field_comparisons": n_tx * len(fields), "mismatches": mismatches[:50],
            "n_mismatches": len(mismatches), "n_rounding_differences": len(rounding), "rounding_examples": rounding[:10],
            "note": "SEC bulk data rounds prices to cents; differences < $0.006 are counted as rounding, not errors.",
            "agreement": 1 - len([m for m in mismatches if m["field"] in fields]) / max(1, n_tx * len(fields))}


def acceptance_timing() -> dict:
    rows = []
    for cik in TIMING_CIKS:
        try:
            idx = sec_form4_xml.filing_index(cik)
        except Exception:
            continue
        rows.append(idx[["filingDate", "accepted_utc"]])
    df = pd.concat(rows).dropna()
    et = df["accepted_utc"].dt.tz_convert("America/New_York")
    same_day = et.dt.normalize().dt.tz_localize(None) == df["filingDate"]
    hour = et.dt.hour + et.dt.minute / 60
    return {"n_filings": int(len(df)), "issuers": len(rows),
            "share_after_close_same_filing_date": float(((hour >= 16) & same_day).mean()),
            "share_before_open": float((hour < 9.5).mean()),
            "share_during_session": float(((hour >= 9.5) & (hour < 16)).mean()),
            "share_after_close": float((hour >= 16).mean()),
            "hour_histogram": {int(h): int(c) for h, c in et.dt.hour.value_counts().sort_index().items()},
            "period": [str(df.filingDate.min().date()), str(df.filingDate.max().date())]}


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    out = {"generated": pd.Timestamp.now().isoformat(timespec="seconds")}
    out["tests"] = run_tests()
    log.info("tests: %s", out["tests"]["counts"])
    out["xml_vs_bulk"] = xml_vs_bulk()
    log.info("xml vs bulk agreement: %.4f", out["xml_vs_bulk"]["agreement"])
    out["acceptance_timing"] = acceptance_timing()
    log.info("after-close share: %.3f", out["acceptance_timing"]["share_after_close"])
    OUT.write_text(json.dumps(out, default=str, indent=1))


if __name__ == "__main__":
    main()
