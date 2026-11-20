"""Central configuration: paths, network identity, research constants.

Every research constant that affects results lives here so that it is documented
in one place and can be varied in robustness tests.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("SMARTMONEY_DATA", ROOT / "data"))
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
RESULTS = DATA / "results"
CACHE = DATA / "cache"

for _p in (RAW, PROCESSED, RESULTS, CACHE):
    _p.mkdir(parents=True, exist_ok=True)


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# SEC fair-access policy: declare who you are, stay under 10 requests/second.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "FollowTheSmartMoney research-bot admin@followthesmartmoney.dev")
SEC_MAX_RPS = 8.0
YAHOO_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) FollowTheSmartMoney/0.1"

# ---------------------------------------------------------------- research design
# Sample: bulk data from 2009Q1 so that the 3-year "routine trader" history
# (Cohen, Malloy & Pomorski 2012) is available for every event from 2012 onward.
INGEST_START = (2009, 1)
EVENT_START = "2012-01-01"
# Chronological split. Weights/thresholds may only be tuned on in-sample data.
IN_SAMPLE_END = "2018-12-31"

# Forward horizons in TRADING days (1d, 1w, 1m, 3m, ~4.5m, ~9m, 1y).
HORIZONS = [1, 5, 20, 60, 90, 180, 252]
PRIMARY_HORIZON = 60

# Investability filter, evaluated point-in-time on the signal date.
MIN_PRICE = 3.0                 # unadjusted close, USD
MIN_MEDIAN_DOLLAR_VOLUME = 5e5  # 20-day median, USD

# Signal parameters (pre-registered from the literature, not optimised).
CLUSTER_WINDOW_DAYS = 30        # calendar days, by FILING date
CLUSTER_MIN_INSIDERS = 3
SIGNAL_COOLDOWN_DAYS = 30       # one event per issuer per signal per 30 days
MIN_PURCHASE_VALUE = 10_000     # USD; filters token / odd-lot purchases

# Transaction costs (one-way, bps) by 20-day median dollar volume tier.
COST_TIERS = [(5e7, 10.0), (5e6, 25.0), (0.0, 50.0)]

BENCHMARK = "SPY"
SIZE_BENCHMARK = "IWM"
