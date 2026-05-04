"""CSV-Parser und Snapshot-Builder fuer die Sektor-Momentum-Matrix."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .momentum import classify_momentum
from .sectors import display_name, group_for


SECTOR_CSV_COLUMNS = [
    "ticker",
    "name",
    "last_price",
    "chg_1d_pct",
    "ret_5d_pct",
    "ret_1m_pct",
    "sma50_pct",
    "sma200_pct",
    "sma_50",
    "sma_200",
]

_NUMERIC_COLUMNS = [c for c in SECTOR_CSV_COLUMNS if c not in {"ticker", "name"}]


def load_sector_csv(source: str | bytes | io.StringIO) -> pd.DataFrame:
    """Parst einen Koyfin-Sektor-CSV-Export (10 Spalten).

    Erwartetes Format: Ticker, Name, Last Price, 1d Chg %, Total Return (5D),
    Total Return (1M), SMA % (50D), SMA % (200D), SMA (50D), SMA (200D).

    Unterstuetzt ``;`` und ``,`` als Trenner sowie deutsche Dezimalkommata.
    Zeilen mit Ticker ausserhalb der bekannten Sektor-/Industrie-Liste werden
    stillschweigend verworfen.
    """

    if isinstance(source, (bytes, bytearray)):
        raw = source.decode("utf-8", errors="replace")
    elif isinstance(source, io.StringIO):
        raw = source.getvalue()
    else:
        raw = Path(source).read_text(encoding="utf-8", errors="replace")

    sep = ";" if raw.count(";") > raw.count(",") else ","
    df = pd.read_csv(io.StringIO(raw), sep=sep, decimal=",", engine="python")

    df = df.iloc[:, : len(SECTOR_CSV_COLUMNS)].copy()
    df.columns = SECTOR_CSV_COLUMNS[: df.shape[1]]
    for col in SECTOR_CSV_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan

    for col in _NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["ticker"]).copy()
    df["ticker"] = df["ticker"].astype(str).str.strip()
    df["group"] = df["ticker"].map(group_for)
    df = df.dropna(subset=["group"]).reset_index(drop=True)
    return df


def build_snapshot_frame(df: pd.DataFrame, snapshot_date: date) -> pd.DataFrame:
    """Erzeugt den persistierbaren Snapshot-DataFrame aus den Rohdaten."""

    if df.empty:
        return pd.DataFrame(
            columns=[
                "snapshot_date",
                "ticker",
                "group",
                "display_name",
                "last_price",
                "sma_50",
                "sma_200",
                "momentum",
            ]
        )

    out = df[["ticker", "group", "last_price", "sma_50", "sma_200"]].copy()
    out["display_name"] = out["ticker"].map(display_name)
    out["momentum"] = [
        classify_momentum(p, s50, s200)
        for p, s50, s200 in zip(out["last_price"], out["sma_50"], out["sma_200"])
    ]
    out.insert(0, "snapshot_date", pd.Timestamp(snapshot_date).date())
    return out[
        [
            "snapshot_date",
            "ticker",
            "group",
            "display_name",
            "last_price",
            "sma_50",
            "sma_200",
            "momentum",
        ]
    ]
