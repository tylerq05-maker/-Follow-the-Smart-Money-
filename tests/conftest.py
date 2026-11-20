import pytest
from smartmoney import config

HAS_DATA = (config.PROCESSED / "insider_tx_classified.parquet").exists()
HAS_RESULTS = (config.PROCESSED / "insider_signal_events.parquet").exists()
needs_data = pytest.mark.skipif(not HAS_DATA, reason="run `python -m smartmoney.pipeline ingest` first")
needs_results = pytest.mark.skipif(not HAS_RESULTS, reason="run `python -m smartmoney.pipeline insiders` first")
