"""Paper-Portfolio ab Produktivstart (Spec 11): Live-Track-Record des
produktiven Modells ohne Overrides (``weight_model``) und mit Overrides
(``weight_effective``).

Datenquellen sind ausschließlich produktive Tabellen und Module:

- Zielgewichte: ``model_portfolio`` / ``model_portfolio_meta`` (nur
  ``rebalance_mode ∈ {full, interim}``), Start = erster Snapshot.
- Kurse in EUR: der Alpha-Vantage-Kurscache des Risikomoduls
  (``market_data.load_price_panel`` — globale Ticker, FX-Umrechnung,
  Benchmark = ACWI-EUR-Reihe). Das Ticker-Mapping Koyfin → Alpha Vantage
  ist die bestehende Tabelle ``av_ticker_mappings`` (uid → av_symbol,
  ``confirmed_by_user`` = manuell bestätigt), vorbelegt per SYMBOL_SEARCH
  über ``market_data.resolve_symbols`` — sie erfüllt die Rolle der in der
  Spec genannten ``ticker_map``.
- Bewertung: dieselbe Buchhaltung wie der Backtest
  (``simulator.replay_targets``: Kosten, ganze Aktien, Kursfortschreibung
  mit Zählung fehlender Kurse).

Tabelle ``paper_nav_daily``: ``date, variant ("model" | "effective"), nav,
benchmark_nav, cash, n_positions, missing_prices``.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core import market_data, persistence
from app.core.config import Settings

from . import metrics as mt
from .config import BacktestConfig
from .simulator import replay_targets

log = logging.getLogger(__name__)

PAPER_TABLE = "paper_nav_daily"
VARIANT_MODEL = "model"
VARIANT_EFFECTIVE = "effective"
VARIANTS = (VARIANT_MODEL, VARIANT_EFFECTIVE)
_WEIGHT_COLUMN = {VARIANT_MODEL: "weight_model", VARIANT_EFFECTIVE: "weight_effective"}


def _ensure_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {PAPER_TABLE} ("
            "date DATE NOT NULL, variant TEXT NOT NULL, nav DOUBLE PRECISION, "
            "benchmark_nav DOUBLE PRECISION, cash DOUBLE PRECISION, n_positions INTEGER, "
            "missing_prices INTEGER, updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (date, variant))"
        )
    )


def load_targets() -> dict[str, dict[date, dict[str, float]]]:
    """Zielgewichte je Variante aus allen ``full``/``interim``-Snapshots."""
    out: dict[str, dict[date, dict[str, float]]] = {v: {} for v in VARIANTS}
    for snap in sorted(persistence.list_model_portfolio_dates()):
        meta = persistence.load_model_portfolio_meta(snap) or {}
        if meta.get("rebalance_mode") not in ("full", "interim"):
            continue
        df = persistence.load_model_portfolio(snap)
        if df is None or df.empty:
            continue
        for variant, col in _WEIGHT_COLUMN.items():
            w = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
            weights = {str(u): float(x) for u, x in zip(df["uid"], w) if x > 0}
            total = sum(weights.values())
            if total > 0:
                out[variant][snap] = {u: x / total for u, x in weights.items()}
    return out


def save_paper_nav(frames: dict[str, pd.DataFrame]) -> int:
    engine = persistence.get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    rows: list[dict] = []
    for variant, df in frames.items():
        for d, r in df.iterrows():
            rows.append(
                {
                    "date": pd.Timestamp(d).date(), "variant": variant,
                    "nav": None if pd.isna(r["nav"]) else float(r["nav"]),
                    "benchmark_nav": None if pd.isna(r["benchmark_nav"]) else float(r["benchmark_nav"]),
                    "cash": float(r["cash"]), "n_positions": int(r["n_positions"]),
                    "missing_prices": int(r["missing_prices"]),
                }
            )
    with engine.begin() as conn:
        _ensure_table(conn)
        if rows:
            conn.execute(
                text(
                    f"INSERT INTO {PAPER_TABLE} (date, variant, nav, benchmark_nav, cash, "
                    "n_positions, missing_prices, updated_at) VALUES (:date, :variant, :nav, "
                    ":benchmark_nav, :cash, :n_positions, :missing_prices, CURRENT_TIMESTAMP) "
                    "ON CONFLICT (date, variant) DO UPDATE SET nav = EXCLUDED.nav, "
                    "benchmark_nav = EXCLUDED.benchmark_nav, cash = EXCLUDED.cash, "
                    "n_positions = EXCLUDED.n_positions, missing_prices = EXCLUDED.missing_prices, "
                    "updated_at = CURRENT_TIMESTAMP"
                ),
                rows,
            )
    return len(rows)


def load_paper_nav(variant: str | None = None) -> pd.DataFrame:
    """NAV-Historie (Index ``date``); leer bei DB-Fehlern (fail-open)."""
    engine = persistence.get_engine()
    empty = pd.DataFrame(columns=["variant", "nav", "benchmark_nav", "cash", "n_positions", "missing_prices"])
    if engine is None:
        return empty
    try:
        with engine.begin() as conn:
            _ensure_table(conn)
            sql = f"SELECT date, variant, nav, benchmark_nav, cash, n_positions, missing_prices FROM {PAPER_TABLE}"
            params: dict = {}
            if variant:
                sql += " WHERE variant = :v"
                params["v"] = variant
            df = pd.read_sql(text(sql + " ORDER BY date ASC"), conn, params=params)
    except SQLAlchemyError as exc:
        log.warning("Laden von %s fehlgeschlagen: %s", PAPER_TABLE, exc)
        return empty
    if df.empty:
        return empty
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def update_paper(
    settings: Settings | None = None,
    config: BacktestConfig | None = None,
    fetch: bool = True,
    asof: date | None = None,
) -> dict:
    """Paper-Portfolios neu bewerten und ``paper_nav_daily`` fortschreiben.

    ``fetch=True`` aktualisiert vorher den Kurscache des Risikomoduls
    (Netzwerk); ``fetch=False`` rechnet nur gegen den vorhandenen Cache.
    Liefert eine Zusammenfassung (Zeilen, fehlende Kurse, unauflösbare Ticker).
    """
    settings = settings or persistence.load_settings() or Settings()
    config = config or BacktestConfig()
    asof = asof or date.today()
    targets = load_targets()
    uids = sorted({u for per in targets.values() for w in per.values() for u in w})
    summary: dict = {"n_snapshots": {v: len(t) for v, t in targets.items()}, "uids": len(uids)}
    if not uids:
        summary["rows"] = 0
        summary["note"] = "Kein model_portfolio-Snapshot mit rebalance_mode full/interim vorhanden."
        return summary
    if fetch:
        universe = persistence.load_universe()
        summary["cache_update"] = market_data.update_cache(uids, universe, settings, asof=asof)
    panel = market_data.load_price_panel(uids, settings, asof=asof)
    summary["unresolved"] = list(panel.quality.unresolved)
    summary["missing_cache"] = list(panel.quality.missing_cache)
    frames: dict[str, pd.DataFrame] = {}
    for variant, per_date in targets.items():
        if not per_date:
            continue
        frames[variant] = replay_targets(
            panel.prices_eur, panel.benchmark, per_date, config.bt_initial_capital,
            config.cost_rate(), end=asof,
        )
    summary["rows"] = save_paper_nav(frames)
    summary["missing_prices"] = {v: int(f["missing_prices"].sum()) for v, f in frames.items()}
    summary["start"] = min(min(t) for t in targets.values() if t).isoformat()
    return summary


def track_record(rf: float = 0.0) -> dict | None:
    """Kennzahlen (Spec 7) beider Varianten plus Override-Beitrag
    (effective − model). ``None`` ohne Daten. Dash-frei (für Seite und CLI)."""
    nav = load_paper_nav()
    if nav.empty:
        return None
    out: dict = {"variants": {}, "start": nav.index.min().date(), "end": nav.index.max().date()}
    for variant in VARIANTS:
        sub = nav[nav["variant"] == variant]
        if sub.empty or sub["nav"].dropna().empty:
            continue
        m = mt.summary(sub["nav"], sub["benchmark_nav"], rf=rf)
        m["missing_prices"] = int(sub["missing_prices"].sum())
        m["n_positions_last"] = int(sub["n_positions"].iloc[-1])
        m["nav_last"] = float(sub["nav"].iloc[-1])
        out["variants"][variant] = m
    if all(v in out["variants"] for v in VARIANTS):
        eff = out["variants"][VARIANT_EFFECTIVE]["portfolio"]
        mod = out["variants"][VARIANT_MODEL]["portfolio"]
        out["override_contribution"] = {
            "total_return": eff["total_return"] - mod["total_return"],
            "ann_return": eff["ann_return"] - mod["ann_return"],
        }
    return out


def build_paper_report(rf: float = 0.0) -> str:
    from .report import _date, _table, fmt, fmt_pct

    tr = track_record(rf)
    lines = ["# Paper-Portfolio — Track Record seit Produktivstart", ""]
    if tr is None:
        return "\n".join(lines + ["Keine Daten in paper_nav_daily (zuerst `paper update` ausführen).", ""])
    lines.append(f"Zeitraum: {_date(tr['start'])} – {_date(tr['end'])}")
    lines.append("")
    headers = ["Kennzahl", "Modell (ohne Overrides)", "Effektiv (mit Overrides)"]
    keys = [
        ("Gesamtrendite", "portfolio", "total_return", True),
        ("Rendite p. a.", "portfolio", "ann_return", True),
        ("Volatilität p. a.", "portfolio", "volatility", True),
        ("Sharpe", "portfolio", "sharpe", False),
        ("Max. Drawdown", "portfolio", "max_drawdown", True),
        ("Benchmark-Rendite p. a. (ACWI EUR)", "benchmark", "ann_return", True),
        ("Aktive Rendite p. a.", "active", "ann_return", True),
        ("Tracking Error ex post", "active", "tracking_error", True),
        ("Information Ratio", "active", "information_ratio", False),
        ("Beta", "active", "beta", False),
    ]
    rows = []
    for label, block, key, pct in keys:
        cells = [label]
        for variant in VARIANTS:
            m = tr["variants"].get(variant)
            val = m[block].get(key) if m else None
            cells.append(fmt_pct(val) if pct else fmt(val))
        rows.append(cells)
    rows.append(["Fehlende Kurse (Tage × Titel)",
                 *[fmt(tr["variants"].get(v, {}).get("missing_prices"), 0) for v in VARIANTS]])
    lines += _table(headers, rows) + [""]
    oc = tr.get("override_contribution")
    if oc:
        lines += ["**Override-Beitrag (effektiv − Modell):** Gesamtrendite "
                  f"{fmt_pct(oc['total_return'])}, p. a. {fmt_pct(oc['ann_return'])}", ""]
    return "\n".join(lines)
