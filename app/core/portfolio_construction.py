"""Regelbasierte Portfoliokonstruktion (Spec 5–8).

Schichten: Selektion (5) → Gewichtung (6.1–6.2) → TE-Kontrolle (6.3) →
Overrides (8) → Trade-Liste mit Rebalancing-Kalender und Turnover-Budget
(7). Jede Schicht ist eine eigene Funktion mit eigenem Test; jede
Regelaussetzung erzeugt einen Diagnoseeintrag. Deterministisch: stabile
Sortierung, Tie-Break ``uid`` alphabetisch.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .config import (
    PC_CAPFLOOR_MAX_ITER,
    PC_TE_MAX_ITER,
    PC_TE_STEP,
    Settings,
)
from .diagnostics import SEV_ERROR, SEV_INFO, SEV_WARNING, Diagnostic
from .scoring_v2 import ZONE_CANDIDATE, ZONE_FILTER, ZONE_HOLD, ZONE_SELL

ACTION_BUY = "KAUF"
ACTION_SELL = "VERKAUF"
ACTION_INCREASE = "AUFSTOCKEN"
ACTION_REDUCE = "REDUZIEREN"
ACTION_HOLD = "HALTEN"
ACTION_DEFERRED = "VERSCHOBEN"

MODE_FULL = "full"
MODE_INTERIM = "interim"
MODE_MONITOR = "monitor"


# ── Benchmark-Gewichte (Spec 5.3) ───────────────────────────────────────


@dataclass
class BenchmarkWeights:
    """Benchmark-Gewichte je Dimension; ``None`` = Restriktion ausgesetzt."""

    sector: dict[str, float] | None
    region: dict[str, float] | None
    diagnostics: list[Diagnostic] = field(default_factory=list)


def universe_benchmark_weights(
    universe: pd.DataFrame,
) -> BenchmarkWeights:
    """Benchmark-Gewichte aus dem importierten Universum (Quelle
    ``pc_benchmark_source = "universe"``).

    Sektor- und Regionsgewichte = marktkapitalisierungsgewichtete Anteile
    über die Gesamtheit des Daten-Imports (alle Zeilen, nicht nur
    eligible). Titel ohne Marktkapitalisierung zählen mit Gewicht 0 und
    werden als Info ausgewiesen; trägt kein Titel eine Marktkapitalisierung,
    wird gleichgewichtet (Warnung).
    """
    diags: list[Diagnostic] = []
    if universe is None or universe.empty:
        diags.append(
            Diagnostic(
                SEV_WARNING,
                "benchmark_universe_empty",
                "Benchmark-Quelle 'universe': kein Universum geladen — "
                "Bandbreiten-Restriktionen nicht angewendet",
            )
        )
        return BenchmarkWeights(sector=None, region=None, diagnostics=diags)

    if "market_cap" in universe.columns:
        mcap = pd.to_numeric(universe["market_cap"], errors="coerce").clip(lower=0)
    else:
        mcap = pd.Series(np.nan, index=universe.index, dtype=float)
    n_missing = int(mcap.isna().sum())
    if float(mcap.fillna(0).sum()) <= 0:
        weights = pd.Series(1.0, index=universe.index)
        diags.append(
            Diagnostic(
                SEV_WARNING,
                "benchmark_universe_equal_weight",
                "Benchmark-Quelle 'universe': keine Marktkapitalisierungen "
                "vorhanden — Gewichte gleichgewichtet berechnet",
            )
        )
    else:
        weights = mcap.fillna(0.0)
        if n_missing:
            diags.append(
                Diagnostic(
                    SEV_INFO,
                    "benchmark_universe_mcap_missing",
                    f"Benchmark-Quelle 'universe': {n_missing} Titel ohne "
                    "Marktkapitalisierung (Gewicht 0 in der Benchmark)",
                )
            )
    total = float(weights.sum())

    def _dim_weights(column: str) -> dict[str, float] | None:
        if column not in universe.columns or total <= 0:
            return None
        groups = universe[column].fillna("Unbekannt").astype(str)
        return (weights.groupby(groups).sum() / total).to_dict()

    sector = _dim_weights("sector")
    region = _dim_weights("region")
    diags.append(
        Diagnostic(
            SEV_INFO,
            "benchmark_universe",
            "Benchmark = Universum (marktkapitalisierungsgewichtet, "
            f"{len(universe)} Titel)",
        )
    )
    return BenchmarkWeights(sector=sector, region=region, diagnostics=diags)


def load_benchmark_weights(
    settings: Settings,
    universe_regions: list[str] | None = None,
    asof: date | None = None,
    universe: pd.DataFrame | None = None,
) -> BenchmarkWeights:
    """Benchmark-Gewichte je nach Quelle (``pc_benchmark_source``).

    ``"universe"``: marktkapitalisierungsgewichtete Anteile des übergebenen
    Universums (:func:`universe_benchmark_weights`) — kein Staleness-Check.
    ``"static"`` (oder fehlendes Universum): Sektoren aus dem Settings-Dict
    + asof, Regionen aus der Tabelle; fehlt eine Quelle oder ist sie älter
    als ``pc_benchmark_max_age_days``, wird die zugehörige Bandbreiten-
    Restriktion NICHT angewendet (Warnung). Unbekannte Regionen erhalten
    Benchmark-Gewicht 0 und werden gelistet.
    """
    if settings.pc_benchmark_source == "universe":
        if universe is not None and not universe.empty:
            return universe_benchmark_weights(universe)
        # Ohne Universums-Frame (z. B. Alt-Aufrufer) auf die statische
        # Quelle zurückfallen — mit Hinweis, keine stillen Fallbacks.
        fallback = _static_benchmark_weights(settings, universe_regions, asof)
        fallback.diagnostics.insert(
            0,
            Diagnostic(
                SEV_WARNING,
                "benchmark_universe_unavailable",
                "Benchmark-Quelle 'universe' gewählt, aber kein Universum "
                "übergeben — statische Gewichte verwendet",
            ),
        )
        return fallback
    return _static_benchmark_weights(settings, universe_regions, asof)


def _static_benchmark_weights(
    settings: Settings,
    universe_regions: list[str] | None = None,
    asof: date | None = None,
) -> BenchmarkWeights:
    diags: list[Diagnostic] = []
    today = asof or date.today()
    max_age = timedelta(days=settings.pc_benchmark_max_age_days)

    sector: dict[str, float] | None = dict(settings.risk_benchmark_sector_weights)
    sector_asof: date | None = None
    raw_asof = (settings.risk_benchmark_sector_weights_asof or "").strip()
    if raw_asof:
        try:
            sector_asof = date.fromisoformat(raw_asof)
        except ValueError:
            sector_asof = None
    if not sector or sector_asof is None or today - sector_asof > max_age:
        grund = (
            "kein Stand (asof) gepflegt"
            if sector_asof is None
            else f"Stand {sector_asof.isoformat()} älter als "
            f"{settings.pc_benchmark_max_age_days} Tage"
        )
        diags.append(
            Diagnostic(
                SEV_WARNING,
                "benchmark_sector_stale",
                f"Sektor-Bandbreite nicht angewendet — {grund}",
            )
        )
        sector = None

    from .persistence import load_region_weights

    region_weights, region_asof = load_region_weights()
    region: dict[str, float] | None = dict(region_weights)
    if not region or region_asof is None or today - region_asof > max_age:
        grund = (
            "Tabelle risk_benchmark_region_weights fehlt oder ist leer"
            if not region
            else f"Stand {region_asof.isoformat() if region_asof else '?'} älter "
            f"als {settings.pc_benchmark_max_age_days} Tage"
        )
        diags.append(
            Diagnostic(
                SEV_WARNING,
                "benchmark_region_stale",
                f"Regions-Bandbreite nicht angewendet — {grund}",
            )
        )
        region = None
    elif universe_regions:
        unknown = sorted(set(universe_regions) - set(region))
        for name in unknown:
            region[name] = 0.0
        if unknown:
            diags.append(
                Diagnostic(
                    SEV_INFO,
                    "benchmark_region_unknown",
                    "Regionen ohne Benchmark-Eintrag (Gewicht 0): "
                    + ", ".join(unknown),
                )
            )
    return BenchmarkWeights(sector=sector, region=region, diagnostics=diags)


# ── Gewichtung (Spec 6.1–6.2) ───────────────────────────────────────────


def compute_weights(
    portfolio_df: pd.DataFrame,
    settings: Settings,
    diagnostics: list[Diagnostic] | None = None,
) -> pd.Series:
    """Rohgewichte (Score-Tilt / Volatilität) plus iteratives Cap/Floor.

    ``portfolio_df`` ist uid-indiziert mit ``composite_z`` und
    ``volatility_1y``. Liefert Gewichte (Summe 1) in stabiler uid-Ordnung.
    """
    diags = diagnostics if diagnostics is not None else []
    df = portfolio_df.sort_index()
    n = len(df)
    if n == 0:
        return pd.Series(dtype=float)

    z = pd.to_numeric(df.get("composite_z"), errors="coerce")
    # Score-Tilt: nur positive Abweichung; ohne Score (z. B. Include-Override
    # eines FILTER-Titels) neutraler Tilt 1.
    tilt = 1.0 + z.clip(0.0, 3.0).fillna(0.0)
    vol = pd.to_numeric(df.get("volatility_1y"), errors="coerce").clip(
        settings.pc_vol_floor, settings.pc_vol_cap
    )
    missing_vol = vol.isna()
    if missing_vol.any():
        median = float(vol.dropna().median()) if vol.notna().any() else (
            (settings.pc_vol_floor + settings.pc_vol_cap) / 2
        )
        vol = vol.fillna(median)
        diags.append(
            Diagnostic(
                SEV_INFO,
                "weight_vol_fallback",
                f"{int(missing_vol.sum())} Titel ohne Volatilität — "
                f"Portfolio-Median {median:.2f} angesetzt".replace(".", ","),
            )
        )
    w_raw = tilt / vol
    w = w_raw / w_raw.sum()

    floor = settings.pc_weight_floor
    cap = settings.pc_weight_cap
    if floor * n > 1.0 or cap * n < 1.0:
        diags.append(
            Diagnostic(
                SEV_ERROR,
                "weight_bounds_infeasible",
                (
                    f"Cap/Floor inkonsistent für {n} Titel "
                    f"(floor·N = {floor * n:.2f}, cap·N = {cap * n:.2f}) — "
                    "Gewichte nur normalisiert"
                ).replace(".", ","),
            )
        )
        return w

    # Iterativ kappen und Überschuss/Defizit proportional auf nicht
    # gebundene Titel verteilen (Spec 6.2, max. 50 Iterationen).
    eps = 1e-12
    for _ in range(PC_CAPFLOOR_MAX_ITER):
        w = w.clip(floor, cap)
        residual = 1.0 - float(w.sum())
        if abs(residual) <= 1e-9:
            break
        if residual > 0:
            free = w < cap - eps
        else:
            free = w > floor + eps
        if not free.any():
            break
        base = w[free].sum()
        if base <= 0:
            w[free] = w[free] + residual / int(free.sum())
        else:
            w[free] = w[free] + residual * w[free] / base
    w = w.clip(floor, cap)
    if abs(float(w.sum()) - 1.0) > 1e-9:
        # Sollte bei konsistenten Grenzen nicht eintreten.
        diags.append(
            Diagnostic(
                SEV_ERROR,
                "weight_capfloor_not_converged",
                "Cap/Floor-Umverteilung nicht konvergiert — Gewichte "
                "renormalisiert",
            )
        )
        w = w / w.sum()
    return w


# ── Overrides (Spec 8) ──────────────────────────────────────────────────


def active_overrides(
    overrides: pd.DataFrame | None, snapshot_date: date | None = None
) -> pd.DataFrame:
    """Aktive, nicht abgelaufene Overrides."""
    if overrides is None or overrides.empty:
        return pd.DataFrame(
            columns=["id", "uid", "direction", "target_weight", "status"]
        )
    snap = snapshot_date or date.today()
    out = overrides[overrides.get("status") == "active"].copy()
    if "expires_at" in out.columns:
        exp = pd.to_datetime(out["expires_at"], errors="coerce")
        out = out[exp.isna() | (exp.dt.date >= snap)]
    return out


def override_expiry_diagnostics(
    overrides: pd.DataFrame | None, snapshot_date: date | None = None
) -> list[Diagnostic]:
    """Diagnosen für abgelaufene Overrides (Spec 8): werden nicht mehr
    angewendet, aber gelistet ("erneuern oder schließen")."""
    if overrides is None or overrides.empty or "expires_at" not in overrides.columns:
        return []
    snap = snapshot_date or date.today()
    exp = pd.to_datetime(overrides["expires_at"], errors="coerce")
    stale = overrides[
        overrides.get("status").isin(["active", "expired"])
        & exp.notna()
        & (exp.dt.date < snap)
    ]
    return [
        Diagnostic(
            SEV_WARNING,
            "override_expired",
            f"Override #{int(r['id'])} ({r.get('direction')}) abgelaufen — "
            "erneuern oder schließen",
            uid=str(r.get("uid")),
        )
        for _, r in stale.iterrows()
    ]


def apply_overrides(
    weights: pd.Series,
    overrides: pd.DataFrame | None,
    settings: Settings,
    diagnostics: list[Diagnostic] | None = None,
    snapshot_date: date | None = None,
) -> tuple[pd.Series, pd.Series, dict[str, int]]:
    """Weight-Overrides fixieren (Spec 8).

    Liefert (``weight_model``, ``weight_effective``, uid → Override-ID).
    ``weight_model`` bleibt das Modellgewicht ohne Overrides; in
    ``weight_effective`` sind Weight-Overrides fixiert und die übrigen
    Titel auf ``1 − Σ Override-Gewichte`` renormiert.
    """
    diags = diagnostics if diagnostics is not None else []
    weight_model = weights.copy()
    weight_effective = weights.copy()
    override_ids: dict[str, int] = {}
    act = active_overrides(overrides, snapshot_date)
    if act.empty:
        return weight_model, weight_effective, override_ids

    for _, row in act.iterrows():
        uid = str(row.get("uid"))
        if uid in weights.index and "id" in row.index and pd.notna(row.get("id")):
            override_ids[uid] = int(row["id"])

    fixed = {
        str(r["uid"]): float(r["target_weight"])
        for _, r in act.iterrows()
        if r.get("direction") == "weight"
        and pd.notna(r.get("target_weight"))
        and str(r.get("uid")) in weights.index
    }
    if fixed:
        total_fixed = sum(fixed.values())
        if total_fixed >= 1.0:
            diags.append(
                Diagnostic(
                    SEV_ERROR,
                    "override_weights_infeasible",
                    "Summe der Weight-Overrides ≥ 100 % — Overrides nicht "
                    "angewendet",
                )
            )
            return weight_model, weight_effective, override_ids
        rest_index = [u for u in weights.index if u not in fixed]
        rest_sum = float(weights.loc[rest_index].sum())
        for uid, target in fixed.items():
            weight_effective.loc[uid] = target
        if rest_sum > 0:
            scale = (1.0 - total_fixed) / rest_sum
            weight_effective.loc[rest_index] = weights.loc[rest_index] * scale
        diags.append(
            Diagnostic(
                SEV_INFO,
                "override_weights_applied",
                f"{len(fixed)} Weight-Override(s) fixiert "
                f"(Σ = {total_fixed * 100:.1f} %)".replace(".", ","),
            )
        )
    return weight_model, weight_effective, override_ids


# ── Selektion (Spec 5.4) ────────────────────────────────────────────────


@dataclass
class SelectionResult:
    """Ergebnis der Selektion: uid-indiziertes Portfolio-Frame, Exits mit
    Grund, übersprungene Kandidaten und Diagnosen."""

    portfolio: pd.DataFrame
    exits: pd.DataFrame
    skipped: list[dict]
    diagnostics: list[Diagnostic] = field(default_factory=list)


def _group_weights(w: pd.Series, groups: pd.Series) -> dict[str, float]:
    return w.groupby(groups.reindex(w.index).fillna("Unbekannt")).sum().to_dict()


def _band_ok(
    w: pd.Series,
    groups: pd.Series,
    benchmark: dict[str, float] | None,
    band: float,
    candidate_group: str | None = None,
    cap: float | None = None,
) -> bool:
    """Bandprüfung bei der Kandidaten-Aufnahme (Spec 5.4).

    Geprüft wird nur die Gruppe des Kandidaten (``candidate_group``): Die
    Gewichte aller anderen Gruppen können durch die Aufnahme nur sinken
    (Verwässerung) — eine bereits bestehende Verletzung aus der Pufferzone
    darf Zukäufe in anderen Sektoren nicht blockieren ("das Modell verkauft
    nie wegen einer Bandbreite, es kauft nur nicht nach"). Solange das
    Portfolio kleiner als ``1/cap`` Titel ist, ist ein Band mathematisch
    unerfüllbar (das kleinste Einzelgewicht liegt über der Benchmark-
    Spanne) — dann wird nicht geprüft; verbleibende Verletzungen werden am
    Ende als Warnung ausgewiesen. Ohne ``candidate_group`` (finale
    Kontrolle) werden alle Gruppen geprüft.
    """
    if benchmark is None:
        return True
    if cap is not None and len(w) * cap < 1.0 - 1e-9:
        return True
    agg = _group_weights(w, groups)
    if candidate_group is not None:
        weight = float(agg.get(candidate_group, 0.0))
        return abs(weight - float(benchmark.get(candidate_group, 0.0))) <= band + 1e-9
    for name, weight in agg.items():
        bm = float(benchmark.get(name, 0.0))
        if abs(weight - bm) > band + 1e-9:
            return False
    return True


def _band_violations(
    w: pd.Series,
    groups: pd.Series,
    benchmark: dict[str, float] | None,
    band: float,
) -> list[str]:
    if benchmark is None:
        return []
    agg = _group_weights(w, groups)
    out = []
    for name, weight in sorted(agg.items()):
        bm = float(benchmark.get(name, 0.0))
        if abs(weight - bm) > band + 1e-9:
            out.append(
                f"{name}: {weight * 100:.1f} % (Benchmark {bm * 100:.1f} %)".replace(
                    ".", ","
                )
            )
    return out


def select_portfolio(
    universe: pd.DataFrame,
    current_holdings: dict[str, float],
    benchmark_weights: BenchmarkWeights,
    settings: Settings,
    overrides: pd.DataFrame | None = None,
    snapshot_date: date | None = None,
) -> SelectionResult:
    """Selektionsalgorithmus (Spec 5.4).

    ``universe``: v2-gescortes Universum mit ``uid, sector, region,
    composite_z, composite_pct, zone_v2, volatility_1y``.
    ``current_holdings``: aktuelle Gewichte je uid.
    """
    diags: list[Diagnostic] = list(benchmark_weights.diagnostics)
    uni = universe.copy()
    if "uid" in uni.columns:
        uni.index = pd.Index(uni["uid"].astype(str), name="_uid")
    uni = uni[~uni.index.duplicated(keep="first")]
    sectors = uni.get("sector", pd.Series("", index=uni.index)).astype(str)
    regions = uni.get("region", pd.Series("", index=uni.index)).astype(str)
    zones = uni.get("zone_v2", pd.Series(ZONE_FILTER, index=uni.index))

    act = active_overrides(overrides, snapshot_date)
    include_uids = [
        str(r["uid"]) for _, r in act.iterrows() if r.get("direction") == "include"
    ]

    # Schritt 1: Beibehaltung (Pufferzone) und Verkäufe.
    retained: list[str] = []
    exit_rows: list[dict] = []
    for uid in sorted(current_holdings):
        if uid not in uni.index:
            exit_rows.append({"uid": uid, "reason": "nicht_im_universum"})
            diags.append(
                Diagnostic(
                    SEV_WARNING,
                    "holding_not_in_universe",
                    "Gehaltener Titel nicht im Universum — wird verkauft",
                    uid=uid,
                )
            )
            continue
        zone = zones.loc[uid]
        if zone in (ZONE_CANDIDATE, ZONE_HOLD) or uid in include_uids:
            retained.append(uid)
        else:
            exit_rows.append({"uid": uid, "reason": f"zone_{zone}"})
    for uid in include_uids:
        if uid in uni.index and uid not in retained:
            retained.append(uid)
            diags.append(
                Diagnostic(
                    SEV_INFO,
                    "override_include",
                    f"Include-Override: Titel aufgenommen (Zone {zones.loc[uid]})",
                    uid=uid,
                )
            )
        elif uid not in uni.index:
            diags.append(
                Diagnostic(
                    SEV_WARNING,
                    "override_include_missing",
                    "Include-Override zeigt auf einen Titel außerhalb des "
                    "Universums — ignoriert",
                    uid=uid,
                )
            )
    retained = sorted(set(retained))

    # Schritt 2: Kandidaten, composite_z absteigend, Tie-Break uid.
    def _candidate_frame(mask: pd.Series) -> list[str]:
        cand = uni[mask & ~uni.index.isin(retained)]
        cand = cand.sort_values(
            ["composite_z", "uid"], ascending=[False, True], kind="mergesort"
        )
        return [str(u) for u in cand.index]

    candidates = _candidate_frame(zones == ZONE_CANDIDATE)

    portfolio: list[str] = list(retained)
    skipped: list[dict] = []

    def _try_fill(cands: list[str]) -> None:
        for uid in cands:
            if len(portfolio) >= settings.pc_target_n:
                break
            if uid in portfolio:
                continue
            test = sorted(portfolio + [uid])
            w = compute_weights(uni.loc[test], settings, diagnostics=[])
            sector_count = int((sectors.loc[test] == sectors.loc[uid]).sum())
            if sector_count > settings.pc_max_per_sector:
                skipped.append({"uid": uid, "reason": "max_per_sector"})
                continue
            if not _band_ok(
                w,
                sectors,
                benchmark_weights.sector,
                settings.pc_sector_band,
                candidate_group=str(sectors.loc[uid]),
                cap=settings.pc_weight_cap,
            ):
                skipped.append({"uid": uid, "reason": "sector_band"})
                continue
            if not _band_ok(
                w,
                regions,
                benchmark_weights.region,
                settings.pc_region_band,
                candidate_group=str(regions.loc[uid]),
                cap=settings.pc_weight_cap,
            ):
                skipped.append({"uid": uid, "reason": "region_band"})
                continue
            portfolio.append(uid)
            portfolio.sort()

    # Schritte 3–4.
    _try_fill(candidates)

    # Schritt 5: Notfüllzone.
    if len(portfolio) < settings.pc_min_n:
        pct = pd.to_numeric(uni.get("composite_pct"), errors="coerce")
        fill_mask = (
            (zones != ZONE_FILTER)
            & (pct >= settings.pc_fill_pct)
            & (pct < settings.pc_entry_pct)
        )
        fill_candidates = _candidate_frame(fill_mask)
        fill_candidates = [u for u in fill_candidates if u not in portfolio]
        if fill_candidates:
            diags.append(
                Diagnostic(
                    SEV_WARNING,
                    "fill_zone_used",
                    (
                        "Notfüllung: Einstiegszone reicht nicht für "
                        f"{settings.pc_min_n} Titel — Kandidaten ab Perzentil "
                        f"{settings.pc_fill_pct * 100:.0f} % nachgezogen"
                    ).replace(".", ","),
                )
            )
            _try_fill(fill_candidates)

    # Schritt 6: Obergrenze (nur durch Override-Includes überschreitbar).
    if len(portfolio) > settings.pc_max_n:
        diags.append(
            Diagnostic(
                SEV_WARNING,
                "portfolio_above_max",
                f"{len(portfolio)} Titel > pc_max_n = {settings.pc_max_n} "
                "(Override-Includes) — kein automatisches Entfernen",
            )
        )

    # Schritt 7: Mindestanzahl.
    if len(portfolio) < settings.pc_min_n:
        diags.append(
            Diagnostic(
                SEV_ERROR,
                "portfolio_below_min",
                f"Mindestanzahl nicht erreichbar: {len(portfolio)} Titel "
                f"< pc_min_n = {settings.pc_min_n} — Zielportfolio trotzdem "
                "ausgegeben",
            )
        )

    # Pufferzone hat Vorrang vor Bandbreiten: Verletzungen durch gehaltene
    # Titel nur als Warnung (das Modell verkauft nie wegen einer Bandbreite).
    final = sorted(portfolio)
    if final:
        w_final = compute_weights(uni.loc[final], settings, diagnostics=[])
        for dim, groups, benchmark, band in (
            ("Sektor", sectors, benchmark_weights.sector, settings.pc_sector_band),
            ("Region", regions, benchmark_weights.region, settings.pc_region_band),
        ):
            violations = _band_violations(w_final, groups, benchmark, band)
            if violations:
                diags.append(
                    Diagnostic(
                        SEV_WARNING,
                        "band_violation_retained",
                        f"{dim}-Bandbreite verletzt (Pufferzone hat Vorrang, "
                        "kein Verkauf): " + "; ".join(violations),
                    )
                )

    exits = pd.DataFrame(exit_rows, columns=["uid", "reason"])
    return SelectionResult(
        portfolio=uni.loc[final].copy(),
        exits=exits,
        skipped=skipped,
        diagnostics=diags,
    )


# ── Ex-ante-TE-Kontrolle (Spec 6.3) ─────────────────────────────────────


def load_risk_cache(
    uids: list[str], settings: Settings, asof: date | None = None
) -> dict | None:
    """Kursdaten aus dem Alpha-Vantage-Cache als Renditepanel.

    Liefert ``{"returns": DataFrame, "bm_returns": Series}`` oder ``None``,
    wenn der Cache leer ist (Update nur per ``risk_report update``-CLI).
    """
    try:
        from . import market_data
        from .risk_metrics import daily_returns

        panel = market_data.load_price_panel(uids, settings, asof=asof)
    except Exception:  # noqa: BLE001
        return None
    if panel.prices_eur.empty:
        return None
    return {
        "returns": daily_returns(panel.prices_eur),
        "bm_returns": daily_returns(panel.benchmark.to_frame("bm"))["bm"],
    }


def apply_te_constraint(
    weights: pd.Series,
    settings: Settings,
    risk_cache: dict | None,
    fixed_uids: set[str] | frozenset[str] = frozenset(),
) -> tuple[pd.Series, float | None, dict]:
    """Ex-ante-TE-Kontrolle (Spec 6.3).

    ``risk_cache`` = ``{"returns": DataFrame, "bm_returns": Series}``.
    ``fixed_uids`` (Weight-Overrides) nehmen nicht an Anpassungen teil.
    Liefert (Gewichte, TE oder None, Details mit ``diagnostics``,
    ``coverage``, ``cte`` (Series), ``iterations``).
    """
    diags: list[Diagnostic] = []
    details: dict = {"diagnostics": diags, "coverage": None, "cte": None,
                     "iterations": 0}
    w = weights.copy()

    if risk_cache is None or "returns" not in risk_cache:
        diags.append(
            Diagnostic(
                SEV_WARNING,
                "te_skipped_no_data",
                "TE nicht prüfbar — kein Kurs-Cache (python -m "
                "app.tools.risk_report update ausführen); Gewichte aus "
                "Cap/Floor gelten",
            )
        )
        return w, None, details

    returns: pd.DataFrame = risk_cache["returns"]
    bm: pd.Series = risk_cache["bm_returns"]
    covered = [u for u in w.index if u in returns.columns
               and returns[u].notna().sum() >= 30]
    coverage = float(w.loc[covered].sum()) if covered else 0.0
    details["coverage"] = coverage
    if coverage < settings.pc_te_min_coverage:
        diags.append(
            Diagnostic(
                SEV_WARNING,
                "te_skipped_low_coverage",
                (
                    f"TE nicht prüfbar, Abdeckung {coverage * 100:.0f} % < "
                    f"{settings.pc_te_min_coverage * 100:.0f} % — Gewichte "
                    "aus Cap/Floor gelten"
                ).replace(".", ","),
            )
        )
        return w, None, details

    from .risk_mcte import compute_mcte

    def _mcte(current: pd.Series):
        res = compute_mcte(returns, bm, current.to_dict())
        ranking = res.ranking.set_index("ticker")
        return res.te_ledoit_wolf, ranking["cte"]

    try:
        te, cte = _mcte(w)
    except ValueError as exc:
        diags.append(
            Diagnostic(
                SEV_WARNING,
                "te_skipped_no_data",
                f"TE nicht prüfbar — {exc}; Gewichte aus Cap/Floor gelten",
            )
        )
        return w, None, details

    floor = settings.pc_weight_floor
    cap = settings.pc_weight_cap
    iterations = 0
    while iterations < PC_TE_MAX_ITER:
        share = float((cte / te).max()) if te > 0 else 0.0
        if te <= settings.pc_te_max and share <= settings.pc_max_cte_share:
            break
        adjustable = [
            u for u in cte.index
            if u not in fixed_uids and u in w.index and w.loc[u] > floor + 1e-12
        ]
        receivers = [
            u for u in cte.index
            if u not in fixed_uids and u in w.index and w.loc[u] < cap - 1e-12
        ]
        if not adjustable or not receivers:
            break
        worst = cte.loc[adjustable].idxmax()
        reduction = min(PC_TE_STEP, float(w.loc[worst]) - floor)
        if reduction <= 0:
            break
        w.loc[worst] -= reduction
        # Gleichmäßig auf die drei Titel mit niedrigstem CTE (nicht über cap).
        best = cte.loc[[u for u in receivers if u != worst]].nsmallest(3).index
        remaining = reduction
        for uid in best:
            add = min(remaining / max(len(best), 1), cap - float(w.loc[uid]))
            w.loc[uid] += add
            remaining -= add
        if remaining > 1e-12 and worst in w.index:
            # Nicht unterbringbarer Rest zurück (cap überall erreicht).
            w.loc[worst] += remaining
        iterations += 1
        te, cte = _mcte(w)

    details["iterations"] = iterations
    details["cte"] = cte
    share = float((cte / te).max()) if te and te > 0 else 0.0
    if te > settings.pc_te_max or share > settings.pc_max_cte_share:
        diags.append(
            Diagnostic(
                SEV_ERROR,
                "te_constraint_unmet",
                (
                    f"TE-Restriktion nicht erfüllbar, TE = {te * 100:.2f} % "
                    f"(max. CTE-Anteil {share * 100:.0f} %) — Pflichtpunkt "
                    "für das Investmentkomitee"
                ).replace(".", ","),
            )
        )
    elif te < settings.pc_te_target_low:
        diags.append(
            Diagnostic(
                SEV_INFO,
                "te_below_target",
                (
                    f"Ex-ante-TE {te * 100:.2f} % unter Zielband "
                    f"{settings.pc_te_target_low * 100:.1f} % — keine Aktion"
                ).replace(".", ","),
            )
        )
    return w, te, details


# ── Rebalancing-Kalender und Turnover-Budget (Spec 7) ───────────────────


def _last_business_day(year: int, month: int) -> date:
    """Letzter Werktag (Mo–Fr) des Monats — Näherung für den letzten
    Handelstag (ohne Feiertagskalender, dokumentiert in MODEL_DESCRIPTION)."""
    day = date(year, month, calendar.monthrange(year, month)[1])
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _latest_trigger(snapshot_date: date, months: list[int]) -> date | None:
    """Jüngster Monatsend-Trigger (letzter Werktag) vor ``snapshot_date``."""
    candidates: list[date] = []
    for year in (snapshot_date.year - 1, snapshot_date.year):
        for month in months:
            trigger = _last_business_day(year, int(month))
            if trigger < snapshot_date:
                candidates.append(trigger)
    return max(candidates) if candidates else None


def detect_rebalance_mode(
    snapshot_date: date, settings: Settings, last_meta: dict | None
) -> str:
    """Modus aus Snapshot-Datum und letzter ``model_portfolio``-Version.

    ``full``, wenn seit dem jüngsten Halbjahres-Trigger (letzter Werktag
    März/September) kein Zielportfolio gebaut wurde; ``interim`` analog für
    Juni/Dezember; sonst ``monitor``. Ohne Vorversion: ``full``.
    """
    if last_meta is None or last_meta.get("snapshot_date") is None:
        return MODE_FULL
    last_build: date = last_meta["snapshot_date"]
    full_trigger = _latest_trigger(snapshot_date, settings.pc_rebalance_months)
    if full_trigger is not None and last_build <= full_trigger:
        return MODE_FULL
    interim_trigger = _latest_trigger(snapshot_date, settings.pc_interim_months)
    if interim_trigger is not None and last_build <= interim_trigger:
        return MODE_INTERIM
    return MODE_MONITOR


@dataclass
class TradeList:
    """Trade-Liste mit Turnover-Kennzahlen (Spec 7.2/7.3)."""

    trades: pd.DataFrame
    turnover_oneway: float
    n_deferred: int
    diagnostics: list[Diagnostic] = field(default_factory=list)


def _trade_priority(row: pd.Series) -> tuple:
    """Streich-Priorität (Spec 7.2): niedriger = wird zuerst behalten."""
    action = row["action"]
    reason = str(row.get("reason") or "")
    z = row.get("composite_z")
    z = float(z) if pd.notna(z) else 0.0
    if action == ACTION_SELL and ("FILTER" in reason or "filter" in reason):
        return (0, 0.0, row["uid"])
    if action == ACTION_SELL and "override_exclude" in reason:
        return (1, 0.0, row["uid"])
    if action == ACTION_SELL:
        # Aufsteigend nach composite_z (schlechteste zuerst).
        return (2, z, row["uid"])
    if action == ACTION_BUY:
        # Absteigend nach composite_z.
        return (3, -z, row["uid"])
    # Gewichtsanpassungen absteigend nach |Δw|.
    return (4, -abs(float(row.get("delta_w") or 0.0)), row["uid"])


def build_trade_list(
    target: pd.DataFrame,
    current: dict[str, float],
    settings: Settings,
    mode: str,
    universe: pd.DataFrame | None = None,
    exit_reasons: dict[str, str] | None = None,
    tax_lots: pd.DataFrame | None = None,
) -> TradeList:
    """Erzeugt die Trade-Liste gegen die aktuellen Gewichte (Spec 7).

    ``target``: uid-indiziert mit ``weight_effective`` (Zielgewicht),
    ``composite_z``, ``zone_v2``, optional ``trend_warning``/``reason``.
    ``universe`` liefert Zone/Score/Trend für verkaufte (nicht mehr im Ziel
    enthaltene) Titel; ``exit_reasons`` deren Verkaufsgrund (wichtig für
    die Turnover-Priorität: FILTER-Verkäufe sind Pflicht).
    ``tax_lots`` ist eine Schnittstelle für die spätere steuerliche
    Optimierung und wird derzeit ignoriert (Spec 15).
    """
    del tax_lots  # Schnittstelle angelegt, Logik bewusst nicht implementiert.
    diags: list[Diagnostic] = []
    rows: list[dict] = []
    exit_reasons = exit_reasons or {}
    target_w = (
        pd.to_numeric(target.get("weight_effective"), errors="coerce").fillna(0.0)
        if not target.empty
        else pd.Series(dtype=float)
    )

    def _lookup(uid: str, column: str, default):
        if uid in target.index and column in target.columns:
            value = target.loc[uid, column]
            if not (isinstance(value, float) and pd.isna(value)):
                return value
        if (
            universe is not None
            and uid in universe.index
            and column in universe.columns
        ):
            value = universe.loc[uid, column]
            if not (isinstance(value, float) and pd.isna(value)):
                return value
        return default

    all_uids = sorted(set(current) | set(target.index.astype(str)))
    for uid in all_uids:
        w_cur = float(current.get(uid, 0.0))
        in_target = uid in target.index
        w_tgt = float(target_w.get(uid, 0.0)) if in_target else 0.0
        delta = w_tgt - w_cur
        zone = str(_lookup(uid, "zone_v2", ""))
        z = _lookup(uid, "composite_z", np.nan)
        trend = bool(_lookup(uid, "trend_warning", False))
        reason = str(_lookup(uid, "reason", "") or "")
        if w_cur <= 0 and w_tgt > 0:
            action = ACTION_BUY
            reason = reason or f"zone_{zone}"
        elif w_cur > 0 and not in_target:
            action = ACTION_SELL
            reason = exit_reasons.get(uid) or reason or (
                f"zone_{zone}" if zone else "zone_unbekannt"
            )
        elif abs(delta) < settings.pc_min_trade_size:
            action = ACTION_HOLD
            delta = 0.0 if abs(delta) < settings.pc_min_trade_size else delta
        elif delta > 0:
            action = ACTION_INCREASE
            reason = reason or "gewichtsanpassung"
        else:
            action = ACTION_REDUCE
            reason = reason or "gewichtsanpassung"
        rows.append(
            {
                "uid": uid,
                "action": action,
                "reason": reason,
                "weight_current": w_cur,
                "weight_target": w_tgt,
                "delta_w": w_tgt - w_cur,
                "composite_z": z,
                "zone_v2": zone,
                "trend_warning": trend,
            }
        )
    trades = pd.DataFrame(rows)
    if trades.empty:
        return TradeList(trades, 0.0, 0, diags)

    active = trades[trades["action"] != ACTION_HOLD]
    turnover = 0.5 * float(active["delta_w"].abs().sum())
    budget = (
        settings.pc_turnover_budget_full
        if mode == MODE_FULL
        else settings.pc_turnover_budget_interim
    )

    n_deferred = 0
    if mode in (MODE_FULL, MODE_INTERIM) and turnover > budget + 1e-12:
        # Priorisiert behalten, Rest streichen (Status VERSCHOBEN, Spec 7.2).
        ordered = active.copy()
        ordered["_prio"] = ordered.apply(_trade_priority, axis=1)
        ordered = ordered.sort_values("_prio", kind="mergesort")
        kept: list[str] = []
        used = 0.0
        for _, row in ordered.iterrows():
            cost = 0.5 * abs(float(row["delta_w"]))
            mandatory = row["_prio"][0] in (0, 1)
            if mandatory or used + cost <= budget + 1e-12:
                kept.append(row["uid"])
                used += cost
        deferred_mask = trades["uid"].isin(
            set(active["uid"]) - set(kept)
        ) & (trades["action"] != ACTION_HOLD)
        n_deferred = int(deferred_mask.sum())
        trades.loc[deferred_mask, "action"] = ACTION_DEFERRED
        trades.loc[deferred_mask, "reason"] = (
            trades.loc[deferred_mask, "reason"].astype(str)
            + " · turnover_budget"
        ).str.lstrip(" ·")

        # Zielgewichte auf die tatsächlich umgesetzten Positionen
        # renormieren: verschobene Trades behalten ihr aktuelles Gewicht.
        deferred_uids = set(trades.loc[deferred_mask, "uid"])
        effective = {}
        for _, row in trades.iterrows():
            uid = row["uid"]
            if uid in deferred_uids:
                w = float(row["weight_current"])
            else:
                w = float(row["weight_target"])
            if w > 0:
                effective[uid] = w
        total = sum(effective.values())
        if total > 0:
            trades["weight_effective_after_budget"] = trades["uid"].map(
                {u: w / total for u, w in effective.items()}
            )
        diags.append(
            Diagnostic(
                SEV_WARNING,
                "turnover_budget_exceeded",
                (
                    f"Turnover {turnover * 100:.1f} % > Budget "
                    f"{budget * 100:.0f} % — {n_deferred} Trade(s) "
                    "verschoben"
                ).replace(".", ","),
            )
        )
        turnover = min(turnover, used)

    return TradeList(trades, turnover, n_deferred, diags)


# ── Gesamtorchestrator ──────────────────────────────────────────────────


def build_model_portfolio(
    scored: pd.DataFrame,
    settings: Settings,
    current_holdings: dict[str, float],
    mode: str | None = None,
    snapshot_date: date | None = None,
    overrides: pd.DataFrame | None = None,
    risk_cache: dict | None = None,
    last_meta: dict | None = None,
) -> dict:
    """Erzeugt das Zielportfolio (Spec 5–8) für CLI und Seite.

    Liefert ein Dict mit ``portfolio`` (Frame mit den Spalten der Tabelle
    ``model_portfolio``), ``meta``, ``trades`` (:class:`TradeList`),
    ``diagnostics`` und ``mode``.
    """
    from .diagnostics import diags_to_json
    from .persistence import settings_hash_v2

    settings.validate_v2_weights()
    snap = snapshot_date or date.today()
    diags: list[Diagnostic] = []

    if mode is None:
        mode = detect_rebalance_mode(snap, settings, last_meta)
    diags.extend(override_expiry_diagnostics(overrides, snap))

    uni = scored.copy()
    if "uid" in uni.columns:
        uni.index = pd.Index(uni["uid"].astype(str), name="_uid")
    uni = uni[~uni.index.duplicated(keep="first")]
    zones = uni.get("zone_v2", pd.Series(dtype=object))

    def _reason_for(uid: str, zone: str) -> str:
        if zone == ZONE_FILTER and uid in uni.index:
            reasons = uni.loc[uid].get("filter_reasons")
            if isinstance(reasons, list) and reasons:
                return "FILTER: " + ", ".join(reasons)
            return "FILTER"
        return f"zone_{zone}"

    if mode == MODE_MONITOR:
        # Kein Zielportfolio-Update: nur Zonen und Diagnosen; Filter-Fails
        # gehaltener Titel als Sofortmaßnahme-Vorschlag (Spec 7.1).
        for uid in sorted(current_holdings):
            if uid in uni.index and zones.get(uid) == ZONE_FILTER:
                diags.append(
                    Diagnostic(
                        SEV_WARNING,
                        "monitor_filter_fail",
                        "Sofortmaßnahme-Vorschlag: gehaltener Titel besteht "
                        f"die Filter nicht ({_reason_for(uid, ZONE_FILTER)}) — "
                        "nicht automatisch in der Trade-Liste",
                        uid=uid,
                    )
                )
        empty_trades = TradeList(pd.DataFrame(), 0.0, 0, [])
        meta = {
            "rebalance_mode": mode,
            "n_titles": len(current_holdings),
            "te_ex_ante": None,
            "te_coverage": None,
            "turnover_oneway": 0.0,
            "n_trades": 0,
            "n_deferred": 0,
            "settings_hash": settings_hash_v2(settings),
            "diagnostics": diags_to_json(diags),
        }
        return {
            "portfolio": pd.DataFrame(),
            "meta": meta,
            "trades": empty_trades,
            "diagnostics": diags,
            "mode": mode,
        }

    benchmark = load_benchmark_weights(
        settings,
        universe_regions=sorted(uni.get("region", pd.Series(dtype=str)).dropna().unique()),
        asof=snap,
        universe=uni,
    )

    if mode == MODE_INTERIM:
        # Nur Verkäufe und deren Ersatz aus KANDIDAT; bestehende Gewichte
        # bleiben, nur Renormierung (Spec 7.1). Freiwerdendes Gewicht geht
        # gleichmäßig an die Ersatztitel.
        act = active_overrides(overrides, snap)
        include_uids = {
            str(r["uid"]) for _, r in act.iterrows()
            if r.get("direction") == "include"
        }
        keep: dict[str, float] = {}
        sold: list[tuple[str, str]] = []
        for uid in sorted(current_holdings):
            zone = zones.get(uid) if uid in uni.index else None
            if uid in include_uids or zone in (ZONE_CANDIDATE, ZONE_HOLD):
                keep[uid] = current_holdings[uid]
            else:
                sold.append((uid, zone if zone is not None else "nicht_im_universum"))
        freed = sum(current_holdings[u] for u, _ in sold)
        n_replace = len(sold)
        cand = uni[(zones == ZONE_CANDIDATE) & ~uni.index.isin(keep)]
        cand = cand.sort_values(
            ["composite_z", "uid"], ascending=[False, True], kind="mergesort"
        )
        replacements = [str(u) for u in cand.index[:n_replace]]
        for uid in replacements:
            keep[uid] = freed / len(replacements) if replacements else 0.0
        total = sum(keep.values())
        weights_effective = pd.Series(
            {u: w / total for u, w in keep.items()} if total > 0 else keep
        ).sort_index()
        weight_model = weights_effective.copy()
        override_ids: dict[str, int] = {}
        te, te_details = None, {"coverage": None, "cte": None}
        selection_diags: list[Diagnostic] = list(benchmark.diagnostics)
        exit_reasons = {u: f"zone_{z}" for u, z in sold}
    else:
        selection = select_portfolio(
            uni, current_holdings, benchmark, settings, overrides, snap
        )
        selection_diags = selection.diagnostics
        weight_model = compute_weights(
            selection.portfolio, settings, diagnostics=selection_diags
        )
        weight_model_series, weights_effective, override_ids = apply_overrides(
            weight_model, overrides, settings, selection_diags, snap
        )
        weight_model = weight_model_series
        act = active_overrides(overrides, snap)
        fixed = (
            set(act.loc[act["direction"] == "weight", "uid"].astype(str))
            if not act.empty
            else set()
        )
        weights_effective, te, te_details = apply_te_constraint(
            weights_effective, settings, risk_cache, fixed_uids=fixed
        )
        selection_diags.extend(te_details["diagnostics"])
        exit_reasons = {
            str(r["uid"]): _reason_for(str(r["uid"]), str(r["reason"]).replace("zone_", ""))
            if str(r["reason"]).startswith("zone_")
            else str(r["reason"])
            for _, r in selection.exits.iterrows()
        }

    diags.extend(selection_diags)

    # Ziel-Frame für Trade-Liste und Persistenz.
    target = uni.loc[[u for u in weights_effective.index if u in uni.index]].copy()
    target["weight_model"] = weight_model.reindex(target.index)
    target["weight_effective"] = weights_effective.reindex(target.index)
    cte = te_details.get("cte")
    target["cte"] = (
        cte.reindex(target.index) if isinstance(cte, pd.Series) else np.nan
    )
    target["reason"] = [
        f"zone_{zones.get(u, '?')}" for u in target.index
    ]
    target["override_id"] = pd.Series(override_ids).reindex(target.index)

    # Trade-Liste: Verkäufe (nicht mehr im Ziel) bekommen Zone/Score aus dem
    # Universum und ihren Grund aus der Selektion.
    trades = build_trade_list(
        target,
        current_holdings,
        settings,
        mode,
        universe=uni,
        exit_reasons=exit_reasons,
    )
    diags.extend(trades.diagnostics)

    # Persistenz-Frame (Spalten der Tabelle model_portfolio).
    portfolio = pd.DataFrame(
        {
            "uid": target.index,
            "composite_z": pd.to_numeric(
                target.get("composite_z"), errors="coerce"
            ).values,
            "composite_pct": pd.to_numeric(
                target.get("composite_pct"), errors="coerce"
            ).values,
            "zone_v2": target.get("zone_v2", "").values,
            "weight_model": target["weight_model"].values,
            "weight_effective": target["weight_effective"].values,
            "cte": target["cte"].values,
            "rebalance_mode": mode,
            "override_id": target["override_id"].values,
        }
    )
    if not trades.trades.empty:
        action_map = trades.trades.set_index("uid")["action"]
        reason_map = trades.trades.set_index("uid")["reason"]
        portfolio["action"] = portfolio["uid"].map(action_map).fillna(ACTION_HOLD)
        portfolio["reason"] = portfolio["uid"].map(reason_map).fillna("")
    else:
        portfolio["action"] = ACTION_HOLD
        portfolio["reason"] = ""

    n_trades = (
        int((trades.trades["action"] != ACTION_HOLD).sum())
        if not trades.trades.empty
        else 0
    )
    meta = {
        "rebalance_mode": mode,
        "n_titles": len(portfolio),
        "te_ex_ante": te,
        "te_coverage": te_details.get("coverage"),
        "turnover_oneway": trades.turnover_oneway,
        "n_trades": n_trades,
        "n_deferred": trades.n_deferred,
        "settings_hash": settings_hash_v2(settings),
        "diagnostics": diags_to_json(diags),
    }
    return {
        "portfolio": portfolio,
        "meta": meta,
        "trades": trades,
        "diagnostics": diags,
        "mode": mode,
    }
