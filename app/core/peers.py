"""Peer-Computation: Top-N Titel zu einem Ziel-Ticker.

Zwei Modi auf demselben Layered-Pool (Industry → Sector → Region → Universum):
- ``similar``: Sortierung nach Distanz im Faktor-Score-Raum — v1: V/Q/G/M/LV
  (0–100, neutral 50), v2: Faktor-Z-Scores (z_value/…, neutral 0).
- ``top_score``: Sortierung nach dem Primär-Score absteigend (v1
  ``total_score``, v2 ``composite_score``) — beste Alternative im Segment.

``version`` wählt das Scoring; die Seiten geben die aktive Version aus
``settings.scoring_version`` durch, damit Peers und Anzeige konsistent sind.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

PeerMode = Literal["similar", "top_score"]
PeerVersion = Literal["v1", "v2"]


FACTOR_COLUMNS: tuple[str, ...] = (
    "value_score",
    "quality_score",
    "growth_score",
    "momentum_score",
    "lowvol_score",
)

FACTOR_COLUMNS_V2: tuple[str, ...] = (
    "z_value",
    "z_quality",
    "z_momentum",
    "z_investment",
)

RETURN_COLUMNS: tuple[str, ...] = (
    "uid",
    "ticker",
    "name",
    "sector",
    "industry",
    "region",
    "total_score",
    "classification",
    "composite_score",
    "classification_v2",
    "zone_v2",
    "ret_12m",
)


def _select_pool(scored: pd.DataFrame, target: pd.Series, desired: int) -> pd.DataFrame:
    """Kandidatenpool durch layering auffüllen: Industry → Sector → Region → Universum.

    Startet eng (gleiche ``industry``) und ergänzt schrittweise mit breiteren
    Kandidaten, bis ``desired`` Einträge erreicht sind. So bleiben die engen
    Treffer erhalten, auch wenn die Industrie zu klein für eine volle Liste ist.
    """

    # Ausschluss und Dedup über die uid: bei Ticker-Kollisionen bleibt die
    # jeweils andere Aktie ein legitimer Peer-Kandidat.
    key = "uid" if "uid" in scored.columns else "ticker"
    others = scored[scored[key] != target.get(key, target["ticker"])]
    if others.empty:
        return others

    layers: list[pd.DataFrame] = []
    seen: set[str] = set()

    def _add(layer: pd.DataFrame) -> None:
        layer = layer[~layer[key].isin(seen)]
        if not layer.empty:
            layers.append(layer)
            seen.update(layer[key].tolist())

    industry = target.get("industry")
    if pd.notna(industry) and industry != "":
        _add(others[others["industry"] == industry])

    if len(seen) < desired:
        sector = target.get("sector")
        if pd.notna(sector) and sector != "":
            _add(others[others["sector"] == sector])

    if len(seen) < desired:
        region = target.get("region")
        if pd.notna(region) and region != "":
            _add(others[others["region"] == region])

    if len(seen) < desired:
        _add(others)

    if not layers:
        return others
    return pd.concat(layers, ignore_index=False)


def compute_peers(
    scored: pd.DataFrame,
    ticker: str,
    n: int = 6,
    mode: PeerMode = "similar",
    version: PeerVersion = "v1",
) -> pd.DataFrame:
    """Top-N Peers für ``ticker`` aus dem Layered-Pool.

    ``mode="similar"`` sortiert nach Faktor-Score-Distanz (kleinste zuerst);
    NaN-Faktor-Werte werden mit dem neutralen Wert gefüllt (v1: 50 auf der
    0–100-Skala, v2: 0 im Z-Score-Raum), damit fehlende Daten keine
    künstliche Nähe oder Ferne produzieren.

    ``mode="top_score"`` sortiert denselben Pool nach dem Primär-Score
    absteigend (NaN ans Ende) — v1 ``total_score``, v2 ``composite_score``.
    Fehlt die Score-Spalte, fällt die Sortierung auf Distanz zurück.

    ``version="v2"`` rechnet Distanz und Ranking auf den Composite-v2-Spalten
    (``z_value`` … ``z_investment``, ``composite_score``); fehlen diese,
    greift der v1-Fallback.
    """

    if scored is None or scored.empty or not ticker:
        return pd.DataFrame()

    from .uid import row_by_uid

    target = row_by_uid(scored, ticker)
    if target is None:
        return pd.DataFrame()

    factor_cols, neutral, score_col = FACTOR_COLUMNS, 50.0, "total_score"
    if version == "v2":
        v2_factors = [c for c in FACTOR_COLUMNS_V2 if c in scored.columns]
        if v2_factors:
            factor_cols, neutral, score_col = (
                FACTOR_COLUMNS_V2,
                0.0,
                "composite_score",
            )

    factors = [c for c in factor_cols if c in scored.columns]
    if not factors:
        return pd.DataFrame()

    pool = _select_pool(scored, target, desired=n)
    if pool.empty:
        return pd.DataFrame()

    target_vec = target[factors].astype(float).fillna(neutral).to_numpy()
    pool_mat = pool[factors].astype(float).fillna(neutral).to_numpy()
    distance = np.sqrt(((pool_mat - target_vec) ** 2).sum(axis=1))

    result_cols = [c for c in RETURN_COLUMNS if c in pool.columns] + [
        c for c in factors if c not in RETURN_COLUMNS
    ]
    out = pool[result_cols].copy()
    out["distance"] = distance

    if mode == "top_score" and score_col in out.columns:
        out = out.sort_values(score_col, ascending=False, na_position="last")
    else:
        out = out.sort_values("distance", ascending=True)

    return out.head(n).reset_index(drop=True)
