"""Insider transaction classification: separate genuine decisions from noise.

Most Form 4 rows are NOT investment decisions: grants (A), option exercises
(M/X), tax withholding (F), gifts (G), plan-driven trades. Only discretionary
open-market trades by people with a plausible information edge are candidate
signals. Every exclusion is recorded as a reason code so the funnel can be
audited (see the 'quality funnel' in the research results).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

TX_LABELS = {
    "P": "Open-market purchase", "S": "Open-market sale", "A": "Grant / award", "M": "Option exercise",
    "X": "Option exercise", "C": "Conversion", "F": "Tax withholding", "G": "Gift", "D": "Disposition to issuer",
    "J": "Other", "W": "Will / inheritance", "Z": "Voting trust", "I": "Plan transaction", "E": "Derivative expiry",
    "H": "Derivative expiry", "O": "OTM exercise", "K": "Equity swap", "L": "Small acquisition",
    "U": "Tender (change of control)", "V": "Voluntary report",
}

_SPLIT = re.compile(r"\s*(?:[,;/&|]|\band\b|\s-\s)\s*", re.I)
_CEO = re.compile(r"^(?:interim |acting |co-?\s?|the )?(?:ceo|c\.e\.o\.?|chief executive(?: officer)?)$", re.I)
_CFO = re.compile(r"^(?:interim |acting |the )?(?:cfo|c\.f\.o\.?|chief financial officer|principal financial officer"
                  r"|(?:e|s)?vp(?:,)? (?:and )?(?:cfo|chief financial officer)|executive vice president(?:,)? "
                  r"(?:cfo|chief financial officer))$", re.I)
_PRES = re.compile(r"^(?:co-?\s?)?(?:president|coo|chief operating officer)$", re.I)
_CHAIR = re.compile(r"chair", re.I)
_ROLE_WORD = re.compile(r"^(?:interim |acting |co-?\s?|the |senior |executive |sr\.? )*(?:president|director|chair|"
                        r"chairman|chairwoman|ceo|cfo|coo|cto|treasurer|secretary|officer|e?s?vp|founder|co-founder|"
                        r"member|trustee|manager|chief|principal|executive|vice|board|10%)", re.I)


def _company_level(segs: list[str], pat: re.Pattern) -> bool:
    """A role segment counts only if the following segment is another role,
    not a division name ('CEO, Consumer & Community Banking' is divisional)."""
    for i, s in enumerate(segs):
        if pat.match(s) and (i + 1 == len(segs) or _ROLE_WORD.match(segs[i + 1])):
            return True
    return False
_NON_COMMON = re.compile(r"preferred|warrant|\bnotes?\b|debenture|\bunits?\b|\brights?\b|option|\bbonds?\b|"
                         r"convertible|\bswap\b|restricted stock unit|\brsus?\b|phantom", re.I)


def parse_title(title: str | None) -> dict[str, bool]:
    """Role flags from a free-text officer title, segment by segment.

    'President and CEO' -> CEO; 'EVP & CFO' -> CFO; 'CEO CIB' (a division head)
    -> not company CEO; 'Chairman of the Board' -> chair.
    """
    t = (title or "").strip()
    segs = [s.strip(" .") for s in _SPLIT.split(t) if s and s.strip(" .")]
    return {
        "is_ceo": _company_level(segs, _CEO),
        "is_cfo": _company_level(segs, _CFO),
        "is_pres_coo": any(_PRES.match(s) for s in segs),
        "is_chair": bool(_CHAIR.search(t)),
    }


def role_bucket(r) -> str:
    if r["is_ceo"]:
        return "CEO"
    if r["is_cfo"]:
        return "CFO"
    if r["is_pres_coo"]:
        return "President/COO"
    if r["is_chair"] and (r["is_director"] or r["is_officer"]):
        return "Chair"
    if r["is_officer"]:
        return "Other officer"
    if r["is_director"]:
        return "Director"
    if r["is_ten_pct"]:
        return "10% owner"
    return "Other"


def classify(tx: pd.DataFrame) -> pd.DataFrame:
    """Add role, value, quality class and exclusion reason to transactions."""
    df = tx.copy()
    for c in ("is_director", "is_officer", "is_ten_pct", "is_other", "equity_swap", "direct", "aff10b5one",
              "filing_plan_mention", "fn_plan", "fn_drip", "fn_espp", "fn_offering"):
        if c in df.columns:  # live + bulk concatenation can leave object/NA dtypes
            df[c] = df[c].astype("boolean").fillna(False).astype(bool)
    titles = df["officer_title"].fillna("")
    uniq = pd.Series(titles.unique())
    parsed = pd.DataFrame([parse_title(x) for x in uniq], index=uniq)
    roles = parsed.reindex(titles.values).reset_index(drop=True)
    roles.index = df.index
    # A title alone does not make someone an officer, but a filer flagged
    # Officer with a CEO title is the CEO; directors titled 'Chairman' are chairs.
    df["is_ceo"] = roles["is_ceo"].values & df["is_officer"].values
    df["is_cfo"] = roles["is_cfo"].values & df["is_officer"].values
    df["is_pres_coo"] = roles["is_pres_coo"].values & df["is_officer"].values
    df["is_chair"] = roles["is_chair"].values
    df["role"] = [role_bucket(r) for r in df[["is_ceo", "is_cfo", "is_pres_coo", "is_chair", "is_officer",
                                              "is_director", "is_ten_pct"]].to_dict("records")]
    df["is_insider"] = df["is_officer"] | df["is_director"]  # officers & directors: the informed insiders
    df["value"] = df["shares"] * df["price"]
    df["tx_label"] = df["trans_code"].map(TX_LABELS).fillna("Other")

    fd = df["filing_date"].values.astype("datetime64[D]")
    td = df["trans_date"].values.astype("datetime64[D]")
    ok_dates = ~(pd.isna(df["filing_date"]) | pd.isna(df["trans_date"]))
    lag = np.full(len(df), np.nan)
    lag[ok_dates.values] = np.busday_count(td[ok_dates.values], fd[ok_dates.values])
    df["filing_lag_bdays"] = lag
    df["late_filing"] = df["filing_lag_bdays"] > 2  # Form 4 due within 2 business days (SOX §403)
    df["invalid_date"] = (~ok_dates) | (df["filing_lag_bdays"] < 0) | (df["filing_lag_bdays"] > 750)
    df["planned"] = df["aff10b5one"] | df["fn_plan"] | df["filing_plan_mention"]
    df["is_common"] = ~df["security_title"].fillna("").str.contains(_NON_COMMON)

    reason = np.full(len(df), "", dtype=object)
    code = df["trans_code"].values

    def mark(mask, why):
        m = np.asarray(mask) & (reason == "")
        reason[m] = why

    is_trade = np.isin(code, ["P", "S"])
    mark(~is_trade, "not_open_market_trade")
    mark(~df["doc_type"].isin(["4"]).values, "amendment_or_form5")
    mark(df["invalid_date"].values, "invalid_dates")
    mark(~df["is_common"].values, "non_common_security")
    mark(~((df["price"] > 0) & (df["shares"] > 0)).values, "missing_price_or_shares")
    mark(df["equity_swap"].values, "equity_swap")
    mark(((code == "P") & (df["acq_disp"].values != "A")) | ((code == "S") & (df["acq_disp"].values != "D")),
         "inconsistent_direction")
    mark(df["fn_drip"].values | df["fn_espp"].values, "drip_espp_plan")
    mark((code == "P") & df["fn_offering"].values, "offering_private_placement")
    mark(df["planned"].values, "rule_10b5_1_plan")
    df["exclusion_reason"] = reason
    df["quality"] = np.where(reason == "", np.where(code == "P", "discretionary_buy", "discretionary_sell"),
                             np.where(reason == "rule_10b5_1_plan", np.where(code == "P", "planned_buy",
                                                                             "planned_sell"), "excluded"))
    # Holdings change, for direct holdings where post-trade balance is reported.
    prior = np.where(code == "P", df["shares_after"] - df["shares"], df["shares_after"] + df["shares"])
    with np.errstate(divide="ignore", invalid="ignore"):
        df["holding_change"] = np.where(code == "P",
                                        np.where(prior > 0, df["shares"] / prior, np.inf),
                                        np.where(prior > 0, -df["shares"] / prior, np.nan))
    df.loc[~df["direct"] | df["shares_after"].isna(), "holding_change"] = np.nan
    return df


def dedupe_joint_reports(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Remove the same trade reported in separate filings by affiliated filers.

    E.g. a fund and its manager each file a Form 4 for one purchase. Identical
    (issuer, trade date, code, shares, price) filed on the same day are treated
    as one trade, keeping the filing with an officer/director if any.
    Distinct officers/directors are never merged: three directors buying the
    same lot at the same price are three insiders (and possibly a cluster).
    Removed: (a) the same person's trade re-filed in another accession;
    (b) non-insider entities (funds, trusts) echoing an insider's trade, or
    each other's. Rows within one accession are never touched (split lots).
    """
    key = ["issuer_cik", "filing_date", "trans_date", "trans_code", "shares", "price"]
    d = df.sort_values(["is_insider", "accession"], ascending=[False, True], kind="stable")
    g = d.groupby(key, dropna=False)
    has_insider = g["is_insider"].transform("any")
    first_acc_person = d.groupby(key + ["owner_cik"], dropna=False)["accession"].transform("first")
    first_acc_entity = d[~d.is_insider].groupby(key, dropna=False)["accession"].transform("first")
    ent = ~d["is_insider"]
    drop = (d["is_insider"] & d["accession"].ne(first_acc_person)) | \
           (ent & has_insider) | \
           (ent & ~has_insider & d["accession"].ne(first_acc_entity.reindex(d.index)))
    return d[~drop].sort_index(), int(drop.sum())
