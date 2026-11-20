"""Direct Form 4 XML parser + live EDGAR feed for one issuer.

Used for (1) filings newer than the latest published bulk quarter and (2) an
independent check of the SEC's bulk extraction (tests/test_form4_parser.py and
the validation report compare the two field by field).

Timing: EDGAR's ``acceptanceDateTime`` (UTC) is the moment a filing became
public. Section 16 filings accepted up to 10pm ET keep that day's filing date,
i.e. many are disclosed AFTER the close. Signals therefore never trade on the
filing-date close.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from lxml import etree

from .. import config, http
from .sec_insider_bulk import PAT_DRIP, PAT_ESPP, PAT_OFFERING, PAT_PLAN

log = logging.getLogger(__name__)
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
XML_CACHE = config.CACHE / "form4"


def _t(el, path: str) -> str | None:
    if el is None:
        return None
    hit = el.find(path)
    if hit is None:
        return None
    txt = (hit.text or "").strip()
    return txt or None


def _val(el, path: str) -> str | None:
    """Form 4 wraps most fields as <field><value>x</value></field>."""
    return _t(el, f"{path}/value") if el is not None and el.find(f"{path}/value") is not None else _t(el, path)


def _bool(x: str | None) -> bool:
    return (x or "").strip().lower() in {"1", "true", "y", "yes"}


def _num(x: str | None) -> float | None:
    try:
        return float(str(x).replace(",", "")) if x not in (None, "") else None
    except ValueError:
        return None


def parse_form4(xml: bytes, accession: str = "", filing_date=None) -> pd.DataFrame:
    root = etree.fromstring(xml, parser=etree.XMLParser(recover=True, huge_tree=True))
    notes = {f.get("id"): " ".join(f.itertext()).strip() for f in root.iter("footnote")}
    remarks = _t(root, "remarks") or ""
    any_plan = any(PAT_PLAN.search(t) for t in notes.values()) or bool(PAT_PLAN.search(remarks))

    owners = []
    for ro in root.findall("reportingOwner"):
        rel = ro.find("reportingOwnerRelationship")
        owners.append({
            "cik": _t(ro, "reportingOwnerId/rptOwnerCik"), "name": _t(ro, "reportingOwnerId/rptOwnerName"),
            "dir": _bool(_t(rel, "isDirector")), "off": _bool(_t(rel, "isOfficer")),
            "ten": _bool(_t(rel, "isTenPercentOwner")), "oth": _bool(_t(rel, "isOther")),
            "title": _t(rel, "officerTitle"),
        })
    owners.sort(key=lambda o: 0 if o["off"] else 1 if o["dir"] else 2 if o["ten"] else 3)
    prim = owners[0] if owners else {}

    rows = []
    for i, tx in enumerate(root.iterfind("nonDerivativeTable/nonDerivativeTransaction")):
        fids = {f.get("id") for f in tx.iter("footnoteId")}
        texts = [notes.get(f, "") for f in fids]
        rows.append({
            "accession": accession, "trans_idx": i,
            "filing_date": pd.to_datetime(filing_date) if filing_date is not None else pd.NaT,
            "period_of_report": pd.to_datetime(_t(root, "periodOfReport"), errors="coerce"),
            "doc_type": _t(root, "documentType"),
            "issuer_cik": int(_t(root, "issuer/issuerCik") or 0) or None,
            "issuer_name": _t(root, "issuer/issuerName"),
            "issuer_symbol": (_t(root, "issuer/issuerTradingSymbol") or "").upper(),
            "owner_cik": int(prim["cik"]) if prim.get("cik") else None,
            "owner_name": prim.get("name"),
            "owner_ciks": ",".join(sorted({o["cik"] for o in owners if o["cik"]})),
            "n_owners": len({o["cik"] for o in owners}),
            "is_director": any(o["dir"] for o in owners), "is_officer": any(o["off"] for o in owners),
            "is_ten_pct": any(o["ten"] for o in owners), "is_other": any(o["oth"] for o in owners),
            "officer_title": prim.get("title"),
            "security_title": _val(tx, "securityTitle"),
            "trans_date": pd.to_datetime(_val(tx, "transactionDate"), errors="coerce"),
            "trans_code": (_t(tx, "transactionCoding/transactionCode") or "").upper(),
            "equity_swap": _bool(_t(tx, "transactionCoding/equitySwapInvolved")),
            "timeliness": (_val(tx, "transactionTimeliness") or "").upper(),
            "shares": _num(_val(tx, "transactionAmounts/transactionShares")),
            "price": _num(_val(tx, "transactionAmounts/transactionPricePerShare")),
            "acq_disp": (_val(tx, "transactionAmounts/transactionAcquiredDisposedCode") or "").upper(),
            "shares_after": _num(_val(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
            "direct": (_val(tx, "ownershipNature/directOrIndirectOwnership") or "").upper() == "D",
            "nature": _val(tx, "ownershipNature/natureOfOwnership"),
            "aff10b5one": _bool(_t(root, "aff10b5One")),
            "filing_plan_mention": any_plan,
            "fn_plan": any(PAT_PLAN.search(t) for t in texts),
            "fn_drip": any(PAT_DRIP.search(t) for t in texts),
            "fn_espp": any(PAT_ESPP.search(t) for t in texts),
            "fn_offering": any(PAT_OFFERING.search(t) for t in texts),
            "footnotes": " | ".join(t for t in texts if t)[:2000],
        })
    return pd.DataFrame(rows)


def fetch_xml(cik: int, accession: str, primary_doc: str) -> bytes:
    XML_CACHE.mkdir(parents=True, exist_ok=True)
    p = XML_CACHE / f"{accession}.xml"
    if p.exists():
        return p.read_bytes()
    doc = primary_doc.split("/")[-1]  # strip the 'xslF345X0n/' rendering prefix -> raw XML
    xml = http.get(ARCHIVE.format(cik=int(cik), acc=accession.replace("-", ""), doc=doc)).content
    p.write_bytes(xml)
    return xml


def filing_index(cik: int, forms=("4", "4/A")) -> pd.DataFrame:
    """Recent filings for an issuer from data.sec.gov (includes acceptance time)."""
    d = http.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json").json()
    r = pd.DataFrame(d["filings"]["recent"])
    r = r[r["form"].isin(forms)].copy()
    r["filingDate"] = pd.to_datetime(r["filingDate"])
    r["accepted_utc"] = pd.to_datetime(r["acceptanceDateTime"], utc=True, errors="coerce")
    return r


def live_transactions(cik: int, since: str | pd.Timestamp, max_filings: int = 150) -> pd.DataFrame:
    idx = filing_index(cik)
    idx = idx[idx["filingDate"] >= pd.Timestamp(since)].head(max_filings)
    frames = []
    for _, f in idx.iterrows():
        try:
            df = parse_form4(fetch_xml(cik, f["accessionNumber"], f["primaryDocument"]),
                             f["accessionNumber"], f["filingDate"])
        except Exception as e:  # one malformed filing must not break the feed
            log.warning("form4 parse failed %s: %s", f["accessionNumber"], e)
            continue
        df["accepted_utc"] = f["accepted_utc"]
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
