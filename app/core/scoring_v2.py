"""Composite v2: 4-Faktor-Composite (Z-Score-basiert, Region×Sektor-neutral).

Implementiert die Spec-Abschnitte 1.3 (abgeleitete Kennzahlen), 2
(Faktor-Definitionen), 3 (Standardisierung und Aggregation) und die Zonen
aus 5.2. Läuft parallel zu Scoring v1 (``app.core.scoring``) und verändert
dessen Spalten nicht: Alle Bereinigungen (negative Multiples, Gültigkeits-
bänder) wirken nur auf interne Kopien; ins DataFrame geschrieben werden
ausschließlich neue v2-Spalten (``z_*``, ``cov_*``, ``composite_*``, …).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import (
    Settings,
    V2_CLEAN_BOUNDS,
    V2_NEGATIVE_IS_INVALID,
)
from .diagnostics import SEV_ERROR, SEV_INFO, SEV_WARNING, Diagnostic
from .momentum import MOMENTUM_DEATH, classify_momentum
from .piotroski import is_financial_sector, is_real_estate_sector

# Faktor → Liste (Indikator, Richtung). Richtung −1: niedriger Wert ist gut
# (Z-Score wird mit −1 multipliziert). ``__leverage__`` ist ein Platzhalter,
# der zur Laufzeit durch den verfügbaren Leverage-Indikator ersetzt wird
# (Spec 2.1: ``net_debt_ebitda``, Fallback ``debt_ebit``, zweiter Fallback
# ``debt_equity``) — Spalten-, nicht Titelebene, damit im Z-Score keine
# Kennzahlen unterschiedlicher Skala gemischt werden.
NONFIN_FACTORS: dict[str, list[tuple[str, float]]] = {
    "value": [("ev_ebitda", -1.0), ("fcf_yield", 1.0), ("ev_ebit", -1.0)],
    "quality": [
        ("gp_ta", 1.0),
        ("roic", 1.0),
        ("accruals", -1.0),
        ("__leverage__", -1.0),
    ],
    "momentum": [("mom_12_1_adj", 1.0), ("eps_revisions_3m", 1.0)],
    "investment": [("asset_growth", -1.0), ("share_issuance", -1.0)],
}

# Financials (Spec 2.2): eigene, kürzere Indikatorlisten. Für Financials
# entfallen die Nicht-Fin-Indikatoren vollständig — auch im Nenner der
# Abdeckung.
FINANCIAL_FACTORS: dict[str, list[tuple[str, float]]] = {
    "value": [("pb", -1.0), ("pe", -1.0)],
    "quality": [("roe", 1.0), ("accruals", -1.0)],
    "momentum": [("mom_12_1_adj", 1.0), ("eps_revisions_3m", 1.0)],
    "investment": [("share_issuance", -1.0)],
}

V2_FACTOR_NAMES: tuple[str, ...] = ("value", "quality", "momentum", "investment")

ZONE_FILTER = "FILTER"
ZONE_CANDIDATE = "KANDIDAT"
ZONE_HOLD = "HALTEN"
ZONE_SELL = "VERKAUFEN"


def optional_column_available(df: pd.DataFrame, column: str) -> bool:
    """Optionale Spalte gilt als vorhanden, wenn sie existiert und mindestens
    einen Wert trägt (der Loader legt fehlende Spalten als NaN an)."""
    return column in df.columns and df[column].notna().any()


def _clean_v2(df: pd.DataFrame, column: str) -> pd.Series:
    """Bereinigte Kopie eines Indikators (Spec 1.3, Bereinigung).

    Verändert das DataFrame nicht — v1 arbeitet mit denselben Rohspalten.
    """
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    series = pd.to_numeric(df[column], errors="coerce")
    if column in V2_NEGATIVE_IS_INVALID:
        series = series.where(series > 0)
    if column == "debt_equity":
        # Negative D/E = negatives Eigenkapital; würde mit Richtung −1 als
        # "unverschuldet" gewertet (v1-Konvention übernommen).
        series = series.where(series >= 0)
    bounds = V2_CLEAN_BOUNDS.get(column)
    if bounds is not None:
        lower, upper = bounds
        if lower is not None:
            series = series.where(series >= lower)
        if upper is not None:
            series = series.where(series <= upper)
    return series


def derive_v2_indicators(
    df: pd.DataFrame, settings: Settings
) -> tuple[pd.DataFrame, list[Diagnostic]]:
    """Berechnet die abgeleiteten v2-Kennzahlen (Spec 1.3) in-place auf einer
    Kopie und liefert (DataFrame, Diagnosen)."""
    out = df.copy()
    diags: list[Diagnostic] = []

    def col(name: str) -> pd.Series:
        if name in out.columns:
            return pd.to_numeric(out[name], errors="coerce")
        return pd.Series(np.nan, index=out.index, dtype=float)

    revenue = col("revenue")
    cogs = col("cogs")
    total_assets = col("total_assets")
    total_assets_prev = col("total_assets_prev")
    net_income = col("net_income")
    ocf = col("ocf")
    op_margin = col("op_margin")
    total_debt = col("total_debt")
    shares_out = col("shares_out")
    shares_out_prev = col("shares_out_prev")
    pfcf = col("pfcf")
    ret_12m = col("ret_12m")
    ret_1m = col("ret_1m")
    vola = col("volatility_1y")

    ta_ok = total_assets > 0
    out["gp_ta"] = ((revenue - cogs) / total_assets).where(
        ta_ok & revenue.notna() & cogs.notna()
    )
    out["accruals"] = ((net_income - ocf) / total_assets).where(ta_ok)
    ebit_proxy = (revenue * op_margin).where(revenue.notna() & op_margin.notna())
    out["ebit_proxy"] = ebit_proxy
    debt_ebit = (total_debt / ebit_proxy).where(ebit_proxy > 0)
    debt_ebit = debt_ebit.mask((ebit_proxy > 0) & (total_debt <= 0), 0.0)
    out["debt_ebit"] = debt_ebit
    out["asset_growth"] = (total_assets / total_assets_prev - 1.0).where(
        total_assets_prev > 0
    )
    out["share_issuance"] = (shares_out / shares_out_prev - 1.0).where(
        shares_out_prev > 0
    )

    # FCF-Yield: primär FCF/EV (optionale Spalte), je Titel Fallback 1/pfcf
    # (FCF/Marktkapitalisierung, konzeptionell abweichend). Beide Varianten
    # dürfen im Snapshot gemischt vorkommen; Ursprung in ``fcf_yield_source``.
    out["fcf_yield_calc"] = (1.0 / pfcf).where(pfcf > 0)
    fcf_ev = _clean_v2(out, "fcf_yield")
    fcf_mcap = _clean_v2(out, "fcf_yield_calc")
    combined = fcf_ev.where(fcf_ev.notna(), fcf_mcap)
    source = pd.Series(pd.NA, index=out.index, dtype="object")
    source = source.mask(fcf_ev.notna(), "ev")
    source = source.mask(fcf_ev.isna() & fcf_mcap.notna(), "mcap")
    out["fcf_yield_v2"] = combined
    out["fcf_yield_source"] = source
    n_valid = int(combined.notna().sum())
    if n_valid:
        share_ev = float((source == "ev").sum()) / n_valid
        diags.append(
            Diagnostic(
                SEV_INFO,
                "fcf_yield_source",
                (
                    "FCF-Yield-Ursprung: "
                    f"{share_ev:.0%} FCF/EV, {1 - share_ev:.0%} FCF/Marktkap. "
                    "(Fallback 1/P-FCF)"
                ),
            )
        )

    # 12-1-Momentum, volatilitätsadjustiert. Gültigkeitsbedingung
    # volatility_1y ≥ v2_min_volatility; fehlt die Vola, greift der Fallback
    # ``mom_12_1`` (Diagnose-Info).
    mom = ret_12m - ret_1m
    out["mom_12_1"] = mom
    adj = (mom / vola).where(vola >= settings.v2_min_volatility)
    vol_missing = vola.isna() & mom.notna()
    adj = adj.mask(vol_missing, mom)
    out["mom_12_1_adj"] = adj
    n_fallback = int(vol_missing.sum())
    if n_fallback:
        diags.append(
            Diagnostic(
                SEV_INFO,
                "mom_vol_fallback",
                (
                    f"{n_fallback} Titel ohne Volatilität — Momentum ohne "
                    "Vola-Adjustierung (Fallback mom_12_1)"
                ),
            )
        )

    out["is_financial"] = is_financial_sector(out)
    out["is_real_estate"] = is_real_estate_sector(out) & ~out["is_financial"]

    # Fehlende optionale Spalten einmalig je Import vermerken (Info).
    optional_v2 = ("ev_ebit", "net_debt_ebitda", "fcf_yield", "adv_3m", "ipo_date")
    for name in optional_v2:
        if not optional_column_available(out, name):
            diags.append(
                Diagnostic(
                    SEV_INFO,
                    "optional_column_missing",
                    f"Optionale Spalte '{name}' fehlt — dokumentierter "
                    "Fallback greift",
                )
            )
    return out, diags


def assign_neutralization_group(
    df: pd.DataFrame,
    valid_mask: pd.Series,
    min_group_size: int = 20,
) -> tuple[pd.Series, pd.Series]:
    """Neutralisierungsgruppe je Titel für EINEN Indikator (Spec 3.1).

    Kaskade: ``region × sector`` → ``sector`` (global) → ``global``, je
    nachdem, ob die Gruppe mindestens ``min_group_size`` Titel mit gültigem
    Wert (``valid_mask``) hat. Liefert (Gruppenschlüssel, Ebene) mit Ebene ∈
    {"region_sector", "sector", "global"}.
    """
    region = (
        df["region"].astype(str).fillna("")
        if "region" in df.columns
        else pd.Series("", index=df.index)
    )
    sector = (
        df["sector"].astype(str).fillna("")
        if "sector" in df.columns
        else pd.Series("", index=df.index)
    )
    rs_key = region + "|" + sector
    valid = valid_mask.fillna(False).astype(bool)
    rs_counts = valid.groupby(rs_key).sum()
    sec_counts = valid.groupby(sector).sum()
    rs_n = rs_key.map(rs_counts).fillna(0)
    sec_n = sector.map(sec_counts).fillna(0)

    level = pd.Series("global", index=df.index, dtype="object")
    level = level.mask(sec_n >= min_group_size, "sector")
    level = level.mask(rs_n >= min_group_size, "region_sector")

    groups = pd.Series("__global__", index=df.index, dtype="object")
    groups = groups.mask(level == "sector", "sec:" + sector)
    groups = groups.mask(level == "region_sector", "rs:" + rs_key)
    return groups, level


def zscore_within_group(
    series: pd.Series,
    groups: pd.Series,
    direction: float = 1.0,
    lower_pct: float = 0.03,
    upper_pct: float = 0.97,
    cap: float = 3.0,
    min_valid: int = 5,
) -> tuple[pd.Series, list[str]]:
    """Winsorisierung + Z-Score je Gruppe (Spec 3.2).

    NaN bleibt NaN (keine Median-Imputation). Bei ``std == 0`` oder weniger
    als ``min_valid`` gültigen Werten wird z = 0 gesetzt; die betroffenen
    Gruppen werden zurückgemeldet (Diagnose beim Aufrufer).
    """
    result = pd.Series(np.nan, index=series.index, dtype=float)
    degenerate: list[str] = []
    for key, idx in series.groupby(groups, sort=True).groups.items():
        vals = series.loc[idx]
        valid = vals.dropna()
        if valid.empty:
            continue
        if len(valid) < min_valid:
            result.loc[valid.index] = 0.0
            degenerate.append(str(key))
            continue
        lo = valid.quantile(lower_pct)
        hi = valid.quantile(upper_pct)
        wins = valid.clip(lo, hi)
        std = wins.std(ddof=1)
        if not np.isfinite(std) or std == 0:
            result.loc[valid.index] = 0.0
            degenerate.append(str(key))
            continue
        z = ((wins - wins.mean()) / std).clip(-cap, cap) * direction
        result.loc[valid.index] = z
    return result, degenerate


def factor_zscore(
    df: pd.DataFrame, indicators: list[str], min_valid: int
) -> tuple[pd.Series, pd.Series]:
    """Faktor-Z als Mittel der gültigen Indikator-Z-Scores (Spec 3.3).

    ``indicators`` sind ``z_*``-Spaltennamen. Unterschreitet die Anzahl
    gültiger Werte ``min_valid``, ist der Faktor NaN. Liefert (faktor_z,
    Abdeckungsanteil 0–1).
    """
    if not indicators:
        nan = pd.Series(np.nan, index=df.index, dtype=float)
        return nan, pd.Series(0.0, index=df.index, dtype=float)
    block = df[indicators]
    n_valid = block.notna().sum(axis=1)
    factor = block.mean(axis=1).where(n_valid >= min_valid)
    coverage = n_valid / len(indicators)
    return factor, coverage


def composite_zscore(
    df: pd.DataFrame,
    weights: dict[str, float],
    min_factor_weight: float = 0.7,
    winsor_lower: float = 0.01,
    winsor_upper: float = 0.99,
    cap: float = 3.0,
) -> tuple[pd.DataFrame, list[Diagnostic]]:
    """Gewichtetes Composite aus den Faktor-Z-Scores (Spec 3.4).

    Erwartet Spalten ``z_value, z_quality, z_momentum, z_investment``.
    Ergänzt ``composite_raw, composite_z, composite_pct, composite_score``.
    """
    out = df
    diags: list[Diagnostic] = []
    num = pd.Series(0.0, index=out.index, dtype=float)
    den = pd.Series(0.0, index=out.index, dtype=float)
    for name in V2_FACTOR_NAMES:
        z = out[f"z_{name}"]
        w = float(weights.get(name, 0.0))
        present = z.notna()
        num = num + z.fillna(0.0) * w * present
        den = den + w * present
    vq_present = out["z_value"].notna() | out["z_quality"].notna()
    valid = (den >= min_factor_weight) & vq_present
    out["composite_raw"] = (num / den).where(valid & (den > 0))

    # Zweite Standardisierung global über alle Titel mit gültigem Rohwert.
    raw = out["composite_raw"]
    composite_z, degenerate = zscore_within_group(
        raw,
        pd.Series("__global__", index=out.index),
        direction=1.0,
        lower_pct=winsor_lower,
        upper_pct=winsor_upper,
        cap=cap,
    )
    if degenerate:
        diags.append(
            Diagnostic(
                SEV_WARNING,
                "composite_degenerate",
                "Composite-Standardisierung ohne Streuung oder < 5 Titel — "
                "composite_z = 0 gesetzt",
            )
        )
    out["composite_z"] = composite_z
    out["composite_pct"] = out["composite_z"].rank(pct=True, method="average")
    out["composite_score"] = (out["composite_pct"] * 100).round(1)
    return out, diags


def classify_v2(pct: float) -> str:
    """Klassifikation v2 aus ``composite_pct`` (Spec 3.5, nur Anzeige)."""
    if pct is None or (isinstance(pct, float) and np.isnan(pct)):
        return "-"
    if pct >= 0.90:
        return "A"
    if pct >= 0.80:
        return "B+"
    if pct >= 0.667:
        return "B"
    if pct >= 0.50:
        return "C"
    if pct >= 0.33:
        return "D"
    return "F"


def assign_zones(df: pd.DataFrame, settings: Settings) -> pd.Series:
    """Zone je Titel (Spec 5.2). Erwartet ``filter_pass`` und
    ``composite_pct``."""
    pct = pd.to_numeric(df.get("composite_pct"), errors="coerce")
    eligible = (
        df["filter_pass"].fillna(False).astype(bool)
        if "filter_pass" in df.columns
        else pd.Series(False, index=df.index)
    )
    zone = pd.Series(ZONE_FILTER, index=df.index, dtype="object")
    zone = zone.mask(eligible & (pct < settings.pc_exit_pct), ZONE_SELL)
    zone = zone.mask(
        eligible & (pct >= settings.pc_exit_pct) & (pct < settings.pc_entry_pct),
        ZONE_HOLD,
    )
    zone = zone.mask(eligible & (pct >= settings.pc_entry_pct), ZONE_CANDIDATE)
    return zone


def _resolve_leverage_indicator(df: pd.DataFrame) -> tuple[str, str | None]:
    """Leverage-Indikator für Quality Nicht-Financials (Spec 2.1).

    Spaltenebene: ``net_debt_ebitda`` (optional) → ``debt_ebit`` (abgeleitet)
    → ``debt_equity``. Liefert (Indikatorname, Diagnose-Text oder None).
    """
    if optional_column_available(df, "net_debt_ebitda"):
        return "net_debt_ebitda", None
    if _clean_v2(df, "debt_ebit").notna().any():
        return (
            "debt_ebit",
            "Leverage-Indikator: debt_ebit (net_debt_ebitda fehlt)",
        )
    return (
        "debt_equity",
        "Leverage-Indikator: debt_equity (net_debt_ebitda und debt_ebit fehlen)",
    )


def _factor_indicator_map(
    df: pd.DataFrame,
) -> tuple[dict[str, list[tuple[str, float]]], dict[str, list[tuple[str, float]]],
           dict[str, list[tuple[str, float]]], list[Diagnostic]]:
    """Konkrete Indikatorlisten je Segment (Nicht-Fin, Financials, Real
    Estate) nach Auflösung von Leverage- und ev_ebit-Verfügbarkeit."""
    diags: list[Diagnostic] = []
    leverage, lev_note = _resolve_leverage_indicator(df)
    if lev_note:
        diags.append(Diagnostic(SEV_INFO, "leverage_fallback", lev_note))

    nonfin: dict[str, list[tuple[str, float]]] = {}
    for factor, entries in NONFIN_FACTORS.items():
        resolved: list[tuple[str, float]] = []
        for name, direction in entries:
            if name == "__leverage__":
                resolved.append((leverage, direction))
            elif name == "ev_ebit" and not optional_column_available(df, "ev_ebit"):
                # Fehlt die Spalte, besteht Value aus 2 Indikatoren (Spec 2.1).
                continue
            else:
                resolved.append((name, direction))
        nonfin[factor] = resolved

    # Real Estate wie Nicht-Financials, aber ohne accruals (Spec 2.3).
    real_estate = {
        factor: [(n, d) for n, d in entries if n != "accruals"]
        for factor, entries in nonfin.items()
    }
    return nonfin, FINANCIAL_FACTORS, real_estate, diags


def _min_valid_for(
    settings: Settings, segment: str, factor: str, n_indicators: int
) -> int:
    """Mindestabdeckung je Faktor (Spec 3.3) inkl. Sonderregel Value."""
    table = (
        settings.v2_min_valid_financial
        if segment == "fin"
        else settings.v2_min_valid_nonfin
    )
    min_valid = int(table.get(factor, 1))
    if segment != "fin" and factor == "value" and n_indicators <= 2:
        # Ohne ``ev_ebit`` hat Value nur 2 Indikatoren → 1 genügt.
        min_valid = min(min_valid, 1)
    return max(1, min(min_valid, n_indicators)) if n_indicators else 0


def compute_scores_v2(
    df: pd.DataFrame,
    settings: Settings,
    overrides: pd.DataFrame | None = None,
    snapshot_date=None,
    tactical_weights: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, list[Diagnostic]]:
    """Berechnet alle Composite-v2-Spalten (Spec 1.3, 2, 3, 4, 5.2).

    Deterministisch: gleiche Eingabedaten und Settings ergeben identische
    Ausgabe (stabile Sortierung, Tie-Break ``uid``). ``overrides`` und
    ``snapshot_date`` fließen in die Universumsfilter ein.
    ``tactical_weights`` ersetzt im Modus ``factor_timing_mode="active"``
    die strategischen Faktorgewichte (Spec 9).
    """
    settings.validate_v2_weights()
    out, diags = derive_v2_indicators(df, settings)

    nonfin_map, fin_map, re_map, map_diags = _factor_indicator_map(out)
    diags.extend(map_diags)

    is_fin = out["is_financial"].astype(bool)
    is_re = out["is_real_estate"].astype(bool)
    segment = pd.Series("nonfin", index=out.index, dtype="object")
    segment = segment.mask(is_re, "re")
    segment = segment.mask(is_fin, "fin")

    segment_maps = {"nonfin": nonfin_map, "fin": fin_map, "re": re_map}

    # Anwendbarkeit je Indikator: Vereinigung über die Segmente, in deren
    # Faktorlisten er vorkommt. Nicht anwendbare Titel werden vor dem
    # Z-Score maskiert, damit weder Score noch Gruppenbildung sie sehen.
    indicator_directions: dict[str, float] = {}
    indicator_segments: dict[str, set[str]] = {}
    for seg_name, seg_map in segment_maps.items():
        for entries in seg_map.values():
            for name, direction in entries:
                indicator_directions[name] = direction
                indicator_segments.setdefault(name, set()).add(seg_name)

    # Quellspalte je Indikator (fcf_yield nutzt die kombinierte Serie).
    source_column = {"fcf_yield": "fcf_yield_v2"}

    for name, direction in sorted(indicator_directions.items()):
        series = _clean_v2(out, source_column.get(name, name))
        if name == "roe" and "debt_equity" in out.columns:
            # Bestehende ROE-Maske: bei negativem Eigenkapital keine Aussage.
            de = pd.to_numeric(out["debt_equity"], errors="coerce")
            series = series.where(~(de < 0))
        applicable = segment.isin(indicator_segments[name])
        series = series.where(applicable)
        groups, level = assign_neutralization_group(
            out, series.notna(), min_group_size=settings.v2_min_group_size
        )
        z, degenerate = zscore_within_group(
            series,
            groups.where(applicable, "__na__"),
            direction=direction,
            lower_pct=settings.v2_winsor_lower,
            upper_pct=settings.v2_winsor_upper,
            cap=settings.v2_zscore_cap,
            min_valid=settings.v2_min_group_valid,
        )
        out[f"z_{name}"] = z
        out[f"neut_level_{name}"] = level.where(applicable)
        real_degenerate = [g for g in degenerate if g != "__na__"]
        if real_degenerate:
            diags.append(
                Diagnostic(
                    SEV_INFO,
                    "zscore_degenerate_group",
                    (
                        f"Indikator '{name}': z = 0 gesetzt in Gruppen ohne "
                        "Streuung oder mit < "
                        f"{settings.v2_min_group_valid} gültigen Werten: "
                        + ", ".join(real_degenerate)
                    ),
                )
            )

    # Faktor-Scores je Segment zusammensetzen.
    for factor in V2_FACTOR_NAMES:
        factor_z = pd.Series(np.nan, index=out.index, dtype=float)
        coverage = pd.Series(0.0, index=out.index, dtype=float)
        for seg_name, seg_map in segment_maps.items():
            mask = segment == seg_name
            if not mask.any():
                continue
            indicators = [f"z_{n}" for n, _ in seg_map.get(factor, [])]
            min_valid = _min_valid_for(
                settings, seg_name, factor, len(indicators)
            )
            fz, cov = factor_zscore(out.loc[mask], indicators, min_valid)
            factor_z.loc[mask] = fz
            coverage.loc[mask] = cov
        out[f"z_{factor}"] = factor_z
        out[f"cov_{factor}"] = coverage

    weights = settings.v2_factor_weights()
    if settings.factor_timing_mode == "active" and tactical_weights:
        weights = tactical_weights
        diags.append(
            Diagnostic(
                SEV_INFO,
                "factor_timing_active",
                "Faktor-Timing aktiv: taktische Gewichte ersetzen die "
                "strategischen Faktorgewichte im Composite",
            )
        )
    out, comp_diags = composite_zscore(
        out,
        weights,
        min_factor_weight=settings.v2_min_factor_weight,
        winsor_lower=settings.v2_composite_winsor_lower,
        winsor_upper=settings.v2_composite_winsor_upper,
        cap=settings.v2_zscore_cap,
    )
    diags.extend(comp_diags)

    out["classification_v2"] = out["composite_pct"].map(classify_v2)

    # Datenabdeckung v2 (Spec 3.6): faktorgewichtetes Mittel der cov_*.
    coverage_v2 = pd.Series(0.0, index=out.index, dtype=float)
    for factor in V2_FACTOR_NAMES:
        coverage_v2 = coverage_v2 + out[f"cov_{factor}"].fillna(0.0) * float(
            weights.get(factor, 0.0)
        )
    out["data_coverage_v2"] = coverage_v2

    # Death Cross als reine Information (Spec 9): löst KEINEN Verkauf aus.
    if {"last_price", "sma_50", "sma_200"}.issubset(out.columns):
        out["trend_warning"] = out.apply(
            lambda r: classify_momentum(
                r.get("last_price"), r.get("sma_50"), r.get("sma_200")
            )
            == MOMENTUM_DEATH,
            axis=1,
        )
    else:
        out["trend_warning"] = False

    # Universumsfilter und Zonen (Spec 4, 5.2). Import hier lokal, um einen
    # Zyklus scoring_v2 ↔ universe_filter zu vermeiden.
    from .universe_filter import apply_universe_filters

    out, filter_diags = apply_universe_filters(
        out, settings, overrides=overrides, snapshot_date=snapshot_date
    )
    diags.extend(filter_diags)
    out["zone_v2"] = assign_zones(out, settings)
    return out, diags
