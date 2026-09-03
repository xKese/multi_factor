"""Universumsfilter v2: harte Ausschlüsse (Spec 4).

Ein Titel ist eligible, wenn alle Bedingungen erfüllt sind. Jede verletzte
Bedingung wird in ``filter_reasons`` protokolliert (kein Abbruch bei der
ersten). Filter, die mangels Spalte nicht anwendbar sind, werden
übersprungen und in der Diagnoseliste vermerkt — keine stillen Fallbacks.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from .config import Settings
from .diagnostics import SEV_INFO, Diagnostic
from .piotroski import (
    PIOTROSKI_MAX_CRITERIA,
    is_financial_sector,
    is_real_estate_sector,
)


def _optional_available(df: pd.DataFrame, column: str) -> bool:
    return column in df.columns and df[column].notna().any()


def _num(df: pd.DataFrame, column: str) -> pd.Series:
    if column in df.columns:
        return pd.to_numeric(df[column], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype=float)


def apply_universe_filters(
    df: pd.DataFrame,
    settings: Settings,
    overrides: pd.DataFrame | None = None,
    snapshot_date: date | None = None,
) -> tuple[pd.DataFrame, list[Diagnostic]]:
    """Wendet die 8 Filter aus Spec 4 an.

    Ergänzt ``filter_pass`` (bool) und ``filter_reasons`` (list[str]) und
    liefert die Diagnoseliste. Erwartet die v2-Spalten
    ``data_coverage_v2``/``composite_z`` (Filter 5) im Frame.
    """
    out = df
    diags: list[Diagnostic] = []
    snap = snapshot_date or date.today()

    reasons: list[list[str]] = [[] for _ in range(len(out))]

    def flag(mask: pd.Series, reason: str) -> None:
        for pos in np.flatnonzero(mask.fillna(False).to_numpy(dtype=bool)):
            reasons[pos].append(reason)

    is_fin = (
        out["is_financial"].astype(bool)
        if "is_financial" in out.columns
        else is_financial_sector(out)
    )
    is_re = (
        out["is_real_estate"].astype(bool)
        if "is_real_estate" in out.columns
        else is_real_estate_sector(out) & ~is_fin
    )

    # 1. Marktkapitalisierung (Mio EUR); fehlende Daten → nicht eligible.
    mcap = _num(out, "market_cap")
    flag(mcap < settings.filter_min_market_cap, "market_cap")
    flag(mcap.isna(), "market_cap_na")

    # 2. Piotroski ≥ 5 von 9; Financials proportional (3,33 von 6 — bestehende
    # Skalierung über ``piotroski_max_criteria``). Fehlend → nicht eligible.
    pio = _num(out, "piotroski")
    max_crit = _num(out, "piotroski_max_criteria").fillna(PIOTROSKI_MAX_CRITERIA)
    min_pio = settings.filter_min_piotroski * max_crit / PIOTROSKI_MAX_CRITERIA
    flag(pio.notna() & (pio < min_pio), "piotroski")
    flag(pio.isna(), "piotroski_na")

    # 3. Altman Z ≥ 1,8; übersprungen für Financials und Real Estate.
    altman = _num(out, "altman_z")
    skip_altman = is_fin | is_re
    flag(~skip_altman & altman.notna() & (altman < settings.filter_min_altman), "altman")
    flag(~skip_altman & altman.isna(), "altman_na")

    # 4. Liquidität: adv_3m ≥ 2,0 Mio EUR — nur wenn Spalte vorhanden.
    if _optional_available(out, "adv_3m"):
        adv = _num(out, "adv_3m")
        flag(adv.notna() & (adv < settings.filter_min_adv), "liquidity")
        n_na = int(adv.isna().sum())
        if n_na:
            diags.append(
                Diagnostic(
                    SEV_INFO,
                    "adv_missing_values",
                    f"Liquiditätsfilter: {n_na} Titel ohne adv_3m-Wert — "
                    "Filter für diese Titel nicht anwendbar",
                )
            )
    else:
        diags.append(
            Diagnostic(
                SEV_INFO,
                "filter_skipped_adv",
                "Liquiditätsfilter übersprungen — Spalte adv_3m fehlt",
            )
        )

    # 5. Datenabdeckung: data_coverage_v2 ≥ 0,6 und composite_z nicht NaN.
    coverage = _num(out, "data_coverage_v2")
    composite = _num(out, "composite_z")
    flag(
        (coverage < settings.filter_min_coverage) | composite.isna(),
        "coverage",
    )

    # 6. IPO: Erstnotiz mindestens 365 Tage vor snapshot_date — nur wenn
    # Spalte vorhanden.
    if _optional_available(out, "ipo_date"):
        ipo = pd.to_datetime(out["ipo_date"], errors="coerce")
        cutoff = pd.Timestamp(snap - timedelta(days=settings.filter_min_listing_days))
        flag(ipo.notna() & (ipo > cutoff), "ipo")
        n_na = int(ipo.isna().sum())
        if n_na:
            diags.append(
                Diagnostic(
                    SEV_INFO,
                    "ipo_missing_values",
                    f"IPO-Filter: {n_na} Titel ohne lesbares ipo_date — "
                    "Filter für diese Titel nicht anwendbar",
                )
            )
    else:
        diags.append(
            Diagnostic(
                SEV_INFO,
                "filter_skipped_ipo",
                "IPO-Filter übersprungen — Spalte ipo_date fehlt",
            )
        )

    # 7. Extremverschuldung Nicht-Financials: D/E > 3,0 UND ICR < 2,0.
    # Fehlende Werte → Filter greift nicht.
    de = _num(out, "debt_equity")
    icr = _num(out, "int_coverage")
    flag(
        ~is_fin
        & de.notna()
        & icr.notna()
        & (de > settings.filter_max_de)
        & (icr < settings.filter_min_icr),
        "extreme_leverage",
    )

    # 8. Override-Ausschluss: aktiver Override mit direction = "exclude".
    if overrides is not None and not overrides.empty:
        active = overrides[
            (overrides.get("status") == "active")
            & (overrides.get("direction") == "exclude")
        ]
        if "expires_at" in active.columns:
            exp = pd.to_datetime(active["expires_at"], errors="coerce")
            active = active[exp.isna() | (exp.dt.date >= snap)]
        excluded = set(active.get("uid", pd.Series(dtype=str)).astype(str))
        if excluded and "uid" in out.columns:
            flag(out["uid"].astype(str).isin(excluded), "override_exclude")

    out["filter_reasons"] = reasons
    out["filter_pass"] = [not r for r in reasons]
    return out, diags
