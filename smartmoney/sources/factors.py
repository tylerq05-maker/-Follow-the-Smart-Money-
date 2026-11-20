"""Fama-French 5 factors + momentum (daily), Kenneth R. French Data Library.

Used to test whether a signal portfolio earns alpha beyond known risk premia:
insider-purchase portfolios load heavily on small-cap (SMB) and value (HML)
exposures, so raw "excess over SPY" overstates skill.
"""
from __future__ import annotations

import io
import re
import zipfile

import pandas as pd

from .. import config, http

BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FILES = {"ff5": "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip", "mom": "F-F_Momentum_Factor_daily_CSV.zip"}
PATH = config.PROCESSED / "ff_factors_daily.parquet"


def _parse(content: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        text = z.read(z.namelist()[0]).decode("latin-1")
    lines = text.splitlines()
    hdr_i = next(i for i, l in enumerate(lines) if re.match(r"^\s*,", l))
    cols = [c.strip() for c in lines[hdr_i].split(",")[1:]]
    rows = [l.split(",") for l in lines[hdr_i + 1:] if re.match(r"^\s*\d{8}\s*,", l)]
    df = pd.DataFrame([[r[0].strip()] + [float(x) for x in r[1:len(cols) + 1]] for r in rows],
                      columns=["date"] + cols)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    return df.set_index("date") / 100.0  # percent -> decimal


def load(refresh: bool = False) -> pd.DataFrame:
    if PATH.exists() and not refresh:
        return pd.read_parquet(PATH)
    ff5 = _parse(http.get(BASE + FILES["ff5"]).content)
    mom = _parse(http.get(BASE + FILES["mom"]).content)
    mom.columns = ["MOM"]
    df = ff5.join(mom, how="inner")
    df.columns = [c.replace("-", "_") for c in df.columns]  # Mkt-RF -> Mkt_RF
    df.to_parquet(PATH)
    return df
