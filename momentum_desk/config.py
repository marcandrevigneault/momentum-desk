"""Load config.yaml into typed config objects and pick the data adapter.

Everything has a safe default, so the app runs with no config file at all
(mode=paper, feed=mock). A real feed or live trading is an explicit edit.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .risk import RiskConfig
from .scanner import ScanConfig


@dataclass
class IBKRConfig:
    host: str = "127.0.0.1"
    port: int = 7497          # TWS paper
    client_id: int = 17


@dataclass
class AppConfig:
    mode: str = "paper"       # paper | live
    data_feed: str = "mock"   # mock | polygon | finnhub | ibkr
    polygon_api_key: str = ""
    finnhub_api_key: str = ""
    scan_interval_s: float = 2.0
    ibkr: IBKRConfig = field(default_factory=IBKRConfig)
    scanner: ScanConfig = field(default_factory=ScanConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)


def _coerce(cls, data: dict[str, Any]):
    """Build a dataclass from a dict, ignoring unknown keys (forward-compatible)."""
    known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return cls(**{k: v for k, v in data.items() if k in known})


def load_config(path: str = "config.yaml") -> AppConfig:
    raw: dict[str, Any] = {}
    if os.path.exists(path):
        try:
            import yaml  # pyyaml is a backend dep; degrade gracefully if absent
            with open(path) as fh:
                raw = yaml.safe_load(fh) or {}
        except Exception as e:  # noqa: BLE001 — config must never hard-crash startup
            print(f"[config] could not read {path} ({e}); using defaults")

    cfg = AppConfig(
        mode=raw.get("mode", "paper"),
        data_feed=raw.get("data_feed", "mock"),
        polygon_api_key=raw.get("polygon_api_key", "") or os.environ.get("POLYGON_API_KEY", ""),
        finnhub_api_key=raw.get("finnhub_api_key", "") or os.environ.get("FINNHUB_API_KEY", ""),
        scan_interval_s=float(raw.get("scan_interval_s", 2.0)),
    )
    if isinstance(raw.get("ibkr"), dict):
        cfg.ibkr = _coerce(IBKRConfig, raw["ibkr"])
    if isinstance(raw.get("scanner"), dict):
        cfg.scanner = _coerce(ScanConfig, raw["scanner"])
    if isinstance(raw.get("risk"), dict):
        cfg.risk = _coerce(RiskConfig, raw["risk"])
    return cfg


def build_adapter(cfg: AppConfig):
    """Return the configured data adapter, falling back to mock with a warning."""
    feed = cfg.data_feed
    if feed == "polygon":
        if not cfg.polygon_api_key:
            print("[config] data_feed=polygon but no API key — falling back to mock")
        else:
            from .adapters.polygon import PolygonAdapter
            return PolygonAdapter(cfg.polygon_api_key, cfg.scanner)
    elif feed not in ("mock", ""):
        print(f"[config] data_feed={feed} not implemented yet — falling back to mock")

    from .adapters.mock import MockReplayAdapter
    return MockReplayAdapter()
