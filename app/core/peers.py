"""Peer-Computation: Top-N Titel zu einem Ziel-Ticker.

Zwei Modi auf demselben Layered-Pool (Industry → Sector → Region → Universum):
- ``similar``: Sortierung nach Distanz im Faktor-Score-Raum (V/Q/G/M/LV, 0–100).
- ``top_score``: Sortierung nach ``total_score`` absteigend (beste Alternative im Segment).
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

PeerMode = Literal["similar", "top_score"]


FACTOR_COLUMNS: tuple[str, ...] = (
    "value_score",
    "quality_score",
    "growth_score",
    "momentum_score",
    "lowvol_score",
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
) -> pd.DataFrame:
    """Top-N Peers für ``ticker`` aus dem Layered-Pool.

    ``mode="similar"`` sortiert nach Faktor-Score-Distanz (kleinste zuerst);
    NaN-Faktor-Scores werden mit dem neutralen Wert 50 gefüllt, damit
    fehlende Daten keine künstliche Nähe oder Ferne produzieren.

    ``mode="top_score"`` sortiert denselben Pool nach ``total_score``
    absteigend (NaN ans Ende). Fehlt die Spalte ``total_score``, fällt die
    Sortierung auf Distanz zurück.
    """

    if scored is None or scored.empty or not ticker:
        return pd.DataFrame()

    from .uid import row_by_uid

    target = row_by_uid(scored, ticker)
    if target is None:
        return pd.DataFrame()

    factors = [c for c in FACTOR_COLUMNS if c in scored.columns]
    if not factors:
        return pd.DataFrame()

    pool = _select_pool(scored, target, desired=n)
    if pool.empty:
        return pd.DataFrame()

    target_vec = target[factors].astype(float).fillna(50.0).to_numpy()
    pool_mat = pool[factors].astype(float).fillna(50.0).to_numpy()
    distance = np.sqrt(((pool_mat - target_vec) ** 2).sum(axis=1))

    result_cols = [c for c in RETURN_COLUMNS if c in pool.columns] + factors
    out = pool[result_cols].copy()
    out["distance"] = distance

    if mode == "top_score" and "total_score" in out.columns:
        out = out.sort_values("total_score", ascending=False, na_position="last")
    else:
        out = out.sort_values("distance", ascending=True)

    return out.head(n).reset_index(drop=True)
