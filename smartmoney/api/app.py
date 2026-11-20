"""Read-only HTTP API for the web UI.

    uvicorn smartmoney.api.app:app --port 8000
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import config
from . import company as co

log = logging.getLogger(__name__)
app = FastAPI(title="Follow the Smart Money? API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])
RES = config.RESULTS / "insiders"
MODULES = {"insiders", "institutions"}


@lru_cache(maxsize=16)
def _json(path: str, mtime: float):
    return json.loads(open(path).read())


def result(name: str, module: str = "insiders"):
    p = config.RESULTS / module / name
    if not p.exists():
        raise HTTPException(404, f"{module}/{name} not built. Run: python -m smartmoney.pipeline {module}")
    return _json(str(p), p.stat().st_mtime)


@app.get("/api/institutions/{name}")
def institutions_result(name: str):
    if name not in {"summary", "paths", "curves"}:
        raise HTTPException(404)
    return result(f"{name}.json", "institutions")


@app.get("/api/synthesis/{name}")
def synthesis_result(name: str):
    if name not in {"summary", "paths", "curves", "scoreboard"}:
        raise HTTPException(404)
    return result(f"{name}.json", "synthesis")


@app.get("/api/crowd/{name}")
def crowd_result(name: str):
    if name not in {"summary", "paths", "curves"}:
        raise HTTPException(404)
    return result(f"{name}.json", "crowd")


@app.get("/api/health")
def health():
    return {"ok": True, "results": sorted(x.name for x in RES.glob("*.json")) if RES.exists() else []}


@app.get("/api/insiders/summary")
def insiders_summary():
    return result("summary.json")


@app.get("/api/insiders/paths")
def insiders_paths():
    return result("paths.json")


@app.get("/api/insiders/curves")
def insiders_curves():
    return result("curves.json")


@app.get("/api/insiders/recent")
def insiders_recent(limit: int = Query(60, le=300), signal: str | None = None):
    p = RES / "recent_signals.json"
    if not p.exists():
        raise HTTPException(404, "recent signals not built")
    rows = _json(str(p), p.stat().st_mtime)
    if signal:
        rows = [r for r in rows if r["signal"] == signal]
    return rows[:limit]


@app.get("/api/validation")
def validation():
    p = config.RESULTS / "validation.json"
    if not p.exists():
        raise HTTPException(404, "validation not run. Run: python -m smartmoney.validate")
    return json.loads(p.read_text())


@app.get("/api/search")
def search(q: str = Query(..., min_length=1, max_length=20)):
    return co.search(q)


@app.get("/api/company/{ticker}")
def company(ticker: str):
    try:
        data = co.company(ticker)
    except Exception as e:  # surface the failure rather than a blank page
        log.exception("company %s", ticker)
        raise HTTPException(502, f"data source error: {e}") from e
    if data is None:
        raise HTTPException(404, f"Unknown ticker {ticker!r} (not in SEC company_tickers)")
    return JSONResponse(json.loads(json.dumps(data, default=str).replace("NaN", "null")))


# Serve the built frontend when present (single-process deployment).
_DIST = config.ROOT / "web" / "dist"
if _DIST.exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = _DIST / path
        return FileResponse(f if path and f.is_file() else _DIST / "index.html")
