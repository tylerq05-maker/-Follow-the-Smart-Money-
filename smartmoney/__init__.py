"""Follow the Smart Money? — quantitative research engine.

Layers (strictly one-directional):
    sources/   -> fetch raw data, store with provenance (data/raw)
    insiders/  -> normalise + classify + build point-in-time signals (data/processed)
    research/  -> forward returns, statistics, portfolios (data/results)
    api/       -> read-only HTTP layer for the web UI
"""
__version__ = "0.1.0"
