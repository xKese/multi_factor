"""BacktestConfig: alle Parameter der Backtest-Engine (Spec 0, 2–9).

Jeder Zahlenwert der Spec ist hier ein Default und kann per YAML-Datei
(``configs/backtest_us_default.yaml``) überschrieben werden. Der Abschnitt
``settings`` der YAML überschreibt Felder der produktiven ``Settings``
(z. B. Faktorgewichte für Sensitivität S1) — die Engine selbst kennt keine
eigenen Scoring-Parameter.

Der API-Key ist bewusst KEIN Feld: er kommt ausschließlich aus der
Umgebungsvariable ``ALPHAVANTAGE_API_KEY`` (Spec 2.2).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import date
from pathlib import Path
from typing import Any

from app.core.config import Settings

# Pflicht-Sensitivitäten (Spec 9): Name → (Config-Overrides, Settings-Overrides).
SENSITIVITY_VARIANTS: dict[str, dict[str, dict[str, Any]]] = {
    "S1_equal_factor_weights": {
        "config": {},
        "settings": {
            "v2_weight_value": 0.25,
            "v2_weight_quality": 0.25,
            "v2_weight_momentum": 0.25,
            "v2_weight_investment": 0.25,
        },
    },
    "S2_no_buffer": {"config": {}, "settings": {"pc_exit_pct": "__entry__"}},
    "S3_equal_weight": {"config": {"bt_equal_weight": True}, "settings": {}},
    "S4_no_te_constraint": {
        "config": {"bt_skip_te_constraint": True},
        "settings": {},
    },
    "S5_quarterly_full": {
        "config": {},
        "settings": {"pc_rebalance_months": [3, 6, 9, 12], "pc_interim_months": []},
    },
    "S6_costs_x2": {
        "config": {"bt_commission_bps": 20.0, "bt_slippage_bps": 10.0},
        "settings": {},
    },
    "S7_delisting_haircut": {
        "config": {"bt_delisting_haircut": "__sensitivity__"},
        "settings": {},
    },
    "S8_momentum_proxy": {
        "config": {"bt_momentum_proxy": "mom_6_1_adj"},
        "settings": {},
    },
    "S9_lag_120": {"config": {"bt_reporting_lag_days": 120}, "settings": {}},
    "S10_top500": {"config": {"bt_universe_top_n": 500}, "settings": {}},
}

VARIANT_BASE = "base"


@dataclass
class BacktestConfig:
    """Parameter der Backtest-Engine. Alle Beträge in EUR, Zeiten in Handelstagen."""

    name: str = "us_default"

    # ── Alpha Vantage / Cache (Spec 2) ───────────────────────────────────
    av_requests_per_minute: int = 75
    av_cache_ttl_days_fundamentals: int = 90
    av_cache_ttl_days_prices: int = 7
    av_cache_ttl_days_listing: int = 365
    av_no_data_ttl_days: int = 30
    av_retry_attempts: int = 3
    cache_dir: str = "data/backtest_cache"
    report_dir: str = "reports/backtest"
    # Kenneth-French-Datenbibliothek (Spec 8); Download nur im fetch-Schritt.
    ff_url_5factors: str = (
        "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
        "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
    )
    ff_url_momentum: str = (
        "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
        "F-F_Momentum_Factor_daily_CSV.zip"
    )

    # ── Kalender (Spec 6) ────────────────────────────────────────────────
    bt_start: date = date(2010, 3, 31)
    bt_end: date | None = None  # None = letzter Handelstag im Cache
    bt_history_start: date = date(2008, 1, 1)

    # ── Universum (Spec 3) ───────────────────────────────────────────────
    bt_exchanges: list[str] = field(
        default_factory=lambda: ["NYSE", "NASDAQ", "NYSE ARCA", "NYSE MKT"]
    )
    bt_ticker_suffix_exclude: list[str] = field(
        default_factory=lambda: ["-P", "-WS", "-U", "-R"]
    )
    bt_min_history_days: int = 250
    bt_min_market_cap: float = 1000.0
    bt_universe_top_n: int = 1000
    bt_missing_cache_max_share: float = 0.05

    # ── Snapshot (Spec 4) ────────────────────────────────────────────────
    bt_reporting_lag_days: int = 90
    bt_fundamentals_max_age_months: int = 18
    bt_tax_rate: float = 0.21
    bt_tax_rate_pre_2018: float = 0.35
    bt_int_coverage_cap: float = 100.0
    bt_momentum_proxy: str | None = None  # None | "mom_6_1_adj"
    bt_benchmark_ticker: str = "SPY"
    bt_benchmark_ticker_eur: str | None = None
    bt_benchmark_sector_source: str = "universe_top500"
    bt_benchmark_top_n: int = 500

    # ── Simulation (Spec 5) ──────────────────────────────────────────────
    bt_initial_capital: float = 10_000_000.0
    bt_commission_bps: float = 10.0
    bt_slippage_bps: float = 5.0
    bt_cash_yield: float = 0.0
    bt_delisting_haircut: float = 0.0
    bt_delisting_haircut_sensitivity: float = 0.30
    bt_missing_price_max_days: int = 10
    bt_equal_weight: bool = False
    bt_skip_te_constraint: bool = False
    bt_rf: float = 0.0

    # ── Produktive Settings überschreiben (nur für Sensitivitäten) ───────
    settings_overrides: dict[str, Any] = field(default_factory=dict)

    # ── Abgeleitet ───────────────────────────────────────────────────────
    def cost_rate(self) -> float:
        """Gesamtkosten je Seite als Dezimalanteil des gehandelten Betrags."""
        return (self.bt_commission_bps + self.bt_slippage_bps) / 10_000.0

    def tax_rate_for(self, d: date) -> float:
        return self.bt_tax_rate_pre_2018 if d.year < 2018 else self.bt_tax_rate

    def settings(self) -> Settings:
        """Produktive Settings inkl. der Overrides dieser Config."""
        s = Settings()
        for key, value in self.settings_overrides.items():
            if not hasattr(s, key):
                raise ValueError(f"Unbekanntes Settings-Feld in settings_overrides: {key}")
            if key == "pc_exit_pct" and value == "__entry__":
                value = s.pc_entry_pct
            setattr(s, key, value)
        s.validate_v2_weights()
        return s

    def to_dict(self) -> dict:
        payload = asdict(self)
        for key, value in payload.items():
            if isinstance(value, date):
                payload[key] = value.isoformat()
        return payload

    def config_hash(self) -> str:
        """SHA-256 über die JSON-serialisierte Config (Reproduzierbarkeit, Spec 0.3)."""
        blob = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _coerce_field(name: str, value: Any) -> Any:
    if name in ("bt_start", "bt_end", "bt_history_start") and isinstance(value, str):
        return date.fromisoformat(value)
    return value


def config_from_dict(payload: dict[str, Any]) -> BacktestConfig:
    """BacktestConfig aus einem Dict (YAML-Inhalt). Unbekannte Keys sind ein
    Fehler — keine stillen Tippfehler in Parametern."""
    known = {f.name for f in fields(BacktestConfig)}
    kwargs: dict[str, Any] = {}
    for key, value in (payload or {}).items():
        if key == "settings":
            kwargs["settings_overrides"] = dict(value or {})
            continue
        if key not in known:
            raise ValueError(f"Unbekannter Config-Parameter: {key}")
        kwargs[key] = _coerce_field(key, value)
    return BacktestConfig(**kwargs)


def load_config(path: str | Path) -> BacktestConfig:
    """YAML-Datei laden. ``settings:`` überschreibt produktive Settings-Felder."""
    import yaml

    text = Path(path).read_text(encoding="utf-8")
    payload = yaml.safe_load(text) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Config {path}: erwartet ein YAML-Mapping")
    cfg = config_from_dict(payload)
    if "name" not in payload:
        cfg.name = Path(path).stem
    return cfg


def apply_variant(config: BacktestConfig, variant: str) -> BacktestConfig:
    """Config einer Sensitivitätsvariante (Spec 9). ``base`` = unverändert."""
    if variant == VARIANT_BASE:
        return replace(config, settings_overrides=dict(config.settings_overrides))
    if variant not in SENSITIVITY_VARIANTS:
        raise ValueError(
            f"Unbekannte Variante {variant!r}; bekannt: "
            + ", ".join(SENSITIVITY_VARIANTS)
        )
    spec = SENSITIVITY_VARIANTS[variant]
    overrides = dict(spec["config"])
    if overrides.get("bt_delisting_haircut") == "__sensitivity__":
        overrides["bt_delisting_haircut"] = config.bt_delisting_haircut_sensitivity
    settings_overrides = {**config.settings_overrides, **spec["settings"]}
    out = replace(config, settings_overrides=settings_overrides, **overrides)
    out.name = f"{config.name}__{variant}"
    return out


def all_variants(config: BacktestConfig) -> dict[str, BacktestConfig]:
    """Basisfall plus alle 10 Pflicht-Sensitivitäten."""
    out = {VARIANT_BASE: apply_variant(config, VARIANT_BASE)}
    for name in SENSITIVITY_VARIANTS:
        out[name] = apply_variant(config, name)
    return out
