"""Historisches Universum je Stichtag aus LISTING_STATUS (Spec 3).

Reihenfolge der Schritte (jede Stufe zählt ihre Ausschlüsse):

1. ``listed(d)``: aktive Listings, ``asset_type == "Stock"``, Börse ∈ Config.
2. Ausschlüsse: Ticker-Suffixe (-P, -WS, -U, -R), OVERVIEW ≠ Common Stock,
   Land ≠ USA (fehlt OVERVIEW: Titel bleibt, Sektor ``Unknown``), weniger
   als ``bt_min_history_days`` Kurstage vor ``d``, Doppelgattungen (nur die
   liquidere, Volumen 3M).
3. Größenfilter ``market_cap(d) ≥ bt_min_market_cap`` und Top-N.

``market_cap(d)`` = Close(d, unadjustiert) · shares_out(aktuell) · fx(d) / 1e6
(Mio EUR) — die Verfügbarkeitsregel für ``shares_out`` ist dieselbe wie im
Snapshot (``dataset.fundamentals_at``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from app.core.uid import slugify_name

from .config import BacktestConfig
from .dataset import BacktestDataset

log = logging.getLogger(__name__)

SECTOR_UNKNOWN = "Unknown"

# Alpha-Vantage-Sektoren (SIC-basiert, Großschreibung) → GICS-nahe Namen des
# Koyfin-Universums. Die Financials-/Real-Estate-Sonderlogik des Modells
# matcht per Substring ("financ", "real estate") und greift damit auch hier.
AV_SECTOR_MAP: dict[str, str] = {
    "TECHNOLOGY": "Information Technology",
    "FINANCE": "Financials",
    "LIFE SCIENCES": "Health Care",
    "MANUFACTURING": "Industrials",
    "TRADE & SERVICES": "Consumer Discretionary",
    "ENERGY & TRANSPORTATION": "Energy",
    "REAL ESTATE & CONSTRUCTION": "Real Estate",
    "UTILITIES": "Utilities",
    "COMMUNICATION SERVICES": "Communication Services",
    "CONSUMER STAPLES": "Consumer Staples",
    "CONSUMER DISCRETIONARY": "Consumer Discretionary",
    "HEALTH CARE": "Health Care",
    "INDUSTRIALS": "Industrials",
    "MATERIALS": "Materials",
    "ENERGY": "Energy",
    "FINANCIALS": "Financials",
    "REAL ESTATE": "Real Estate",
    "INFORMATION TECHNOLOGY": "Information Technology",
}


def map_sector(raw: object) -> str:
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return SECTOR_UNKNOWN
    key = str(raw).strip().upper()
    if not key or key in ("NONE", "N/A", "-"):
        return SECTOR_UNKNOWN
    return AV_SECTOR_MAP.get(key, str(raw).strip().title())


@dataclass
class UniverseResult:
    """Universum zum Stichtag (Index = Ticker) plus Zählstatistik."""

    frame: pd.DataFrame
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def tickers(self) -> list[str]:
        return list(self.frame.index)


def has_excluded_suffix(ticker: str, suffixes: list[str]) -> bool:
    t = str(ticker).upper()
    return any(t.endswith(s.upper()) for s in suffixes)


def _listed(dataset: BacktestDataset, d: date, config: BacktestConfig) -> pd.DataFrame:
    listing = dataset.listing_at(d)
    if listing is None or listing.empty:
        # Ohne Listing-Datei: alle Ticker mit Kurs am Stichtag (Fallback für
        # Tests/Teildaten — wird in der Statistik ausgewiesen).
        prices = dataset.adj_close.loc[:pd.Timestamp(d)]
        alive = prices.columns[prices.tail(1).notna().iloc[0]]
        alive = [t for t in alive if t != dataset.benchmark_ticker]
        return pd.DataFrame(
            {"symbol": alive, "exchange": "NYSE", "asset_type": "Stock",
             "listing_source": "prices"}
        )
    df = listing.copy()
    df["listing_source"] = "listing_status"
    return df


def market_caps(
    dataset: BacktestDataset, tickers: list[str], d: date, config: BacktestConfig
) -> pd.Series:
    """Marktkapitalisierung in Mio EUR je Ticker (NaN ohne Kurs/Shares)."""
    ts = pd.Timestamp(d)
    fx = dataset.fx.rate(d)
    close_window = dataset.close.loc[:ts].tail(config.bt_missing_price_max_days)
    out: dict[str, float] = {}
    for t in tickers:
        if t not in close_window.columns:
            out[t] = np.nan
            continue
        px = close_window[t].dropna()
        if px.empty:
            out[t] = np.nan
            continue
        cur, _ = dataset.fundamentals_at(
            t, d, config.bt_reporting_lag_days, config.bt_fundamentals_max_age_months
        )
        shares = float(cur["shares_out"]) if cur is not None else np.nan
        if not np.isfinite(shares) or shares <= 0:
            out[t] = np.nan
            continue
        out[t] = float(px.iloc[-1]) * shares * fx / 1e6
    return pd.Series(out, dtype=float)


def build_universe(
    d: date, dataset: BacktestDataset, config: BacktestConfig
) -> UniverseResult:
    stats: dict[str, int] = {}
    listed = _listed(dataset, d, config)
    stats["listed_raw"] = int(len(listed))

    # 3.1 Grundmenge.
    asset = listed.get("asset_type", pd.Series("Stock", index=listed.index)).astype(str)
    exch = listed.get("exchange", pd.Series("", index=listed.index)).astype(str).str.upper()
    allowed = {e.upper() for e in config.bt_exchanges}
    base = listed[(asset.str.lower() == "stock") & exch.isin(allowed)].copy()
    base = base.drop_duplicates("symbol")
    base = base[base["symbol"] != dataset.benchmark_ticker]
    stats["after_type_exchange"] = int(len(base))

    # 3.2 Suffixe.
    base = base[~base["symbol"].map(lambda t: has_excluded_suffix(t, config.bt_ticker_suffix_exclude))]
    stats["after_suffix"] = int(len(base))
    # Cache-Abdeckung (Abbruchkriterium der CLI: > 5 % ohne Kursreihe).
    stats["missing_cache"] = int(
        (~base["symbol"].isin(dataset.adj_close.columns)).sum()
    ) if base.get("listing_source", pd.Series(dtype=str)).eq("listing_status").any() else 0

    # 3.2 OVERVIEW: Common Stock, USA; fehlt OVERVIEW → bleibt, Sektor Unknown.
    rows: list[dict] = []
    n_missing_overview = 0
    for sym in base["symbol"]:
        ov = dataset.overview.get(sym)
        if not ov:
            n_missing_overview += 1
            rows.append({"ticker": sym, "name": sym, "sector": SECTOR_UNKNOWN,
                         "industry": SECTOR_UNKNOWN, "overview_missing": True})
            continue
        asset_type = str(ov.get("asset_type") or "").strip().lower()
        country = str(ov.get("country") or "").strip().upper()
        if asset_type and asset_type != "common stock":
            continue
        if country and country not in ("USA", "US", "UNITED STATES"):
            continue
        rows.append(
            {
                "ticker": sym,
                "name": str(ov.get("name") or sym),
                "sector": map_sector(ov.get("sector")),
                "industry": str(ov.get("industry") or SECTOR_UNKNOWN).strip().title() or SECTOR_UNKNOWN,
                "overview_missing": False,
            }
        )
    uni = pd.DataFrame(rows, columns=["ticker", "name", "sector", "industry", "overview_missing"])
    uni = uni.set_index("ticker")
    stats["after_overview"] = int(len(uni))
    stats["missing_overview"] = int(n_missing_overview)

    # 3.2 Mindesthistorie: ≥ bt_min_history_days Kurstage vor d.
    ts = pd.Timestamp(d)
    hist = dataset.adj_close.loc[:ts]
    present = [t for t in uni.index if t in hist.columns]
    uni = uni.loc[present]
    counts = hist[present].notna().sum()
    has_price_now = hist[present].tail(config.bt_missing_price_max_days).notna().any()
    uni = uni[(counts.reindex(uni.index) >= config.bt_min_history_days) & has_price_now.reindex(uni.index)]
    stats["after_history"] = int(len(uni))

    # 3.2 Doppelgattungen: gleicher Firmenname → liquidere Gattung (Volumen 3M).
    if len(uni):
        vol_window = dataset.volume.loc[:ts].tail(63)
        close_window = dataset.close.loc[:ts].tail(63)
        cols = [t for t in uni.index if t in vol_window.columns]
        dollar_vol = (vol_window[cols] * close_window[cols]).mean()
        uni["adv_usd"] = dollar_vol.reindex(uni.index)
        slug = uni["name"].map(slugify_name)
        keep: list[str] = []
        for _, group in uni.groupby(slug.where(slug != "", uni.index.to_series()), sort=False):
            if len(group) == 1 or group["overview_missing"].all():
                keep.extend(group.index)
                continue
            best = group["adv_usd"].fillna(-1.0).sort_values(ascending=False)
            keep.append(str(best.index[0]))
        uni = uni.loc[sorted(keep)]
    stats["after_share_class"] = int(len(uni))

    # 3.3 Größenfilter und Obergrenze.
    uni["market_cap"] = market_caps(dataset, list(uni.index), d, config)
    stats["missing_market_cap"] = int(uni["market_cap"].isna().sum())
    uni = uni[uni["market_cap"] >= config.bt_min_market_cap]
    stats["after_min_mcap"] = int(len(uni))
    uni = uni.sort_values(["market_cap"], ascending=False, kind="mergesort")
    uni = uni.head(int(config.bt_universe_top_n)).sort_index()
    stats["final"] = int(len(uni))
    return UniverseResult(frame=uni, stats=stats)


def union_tickers(listings: dict[date, pd.DataFrame], config: BacktestConfig) -> list[str]:
    """Ticker-Vereinigung aller Stichtage nach 3.1 und Suffix-Ausschluss —
    die Ladeliste für ``fetch`` (Spec 3.3)."""
    allowed = {e.upper() for e in config.bt_exchanges}
    out: set[str] = set()
    for df in listings.values():
        if df is None or df.empty:
            continue
        asset = df.get("asset_type", pd.Series("Stock", index=df.index)).astype(str)
        exch = df.get("exchange", pd.Series("", index=df.index)).astype(str).str.upper()
        sel = df[(asset.str.lower() == "stock") & exch.isin(allowed)]["symbol"].astype(str)
        out.update(s for s in sel if not has_excluded_suffix(s, config.bt_ticker_suffix_exclude))
    return sorted(out)
