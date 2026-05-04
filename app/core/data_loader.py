"""CSV-Import für den Koyfin-Export.

Koyfin exportiert Prozent- und Return-Werte bereits als Dezimalzahlen
(z. B. ``0,1889`` für 18,89 %, ``1,2504`` für 125,04 %). Der Loader nimmt
dieses Format als gegeben an — eine frühere Divisions-Heuristik (|x|>1 →
/100) führte bei Titeln mit Returns > 100 % zu einer fälschlichen
Verkleinerung um Faktor 100.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd

from .schema import KOYFIN_COLUMNS


NUMERIC_COLUMNS = [
    c
    for c in KOYFIN_COLUMNS
    if c not in {"ticker", "name", "sector", "industry", "region", "export_date"}
]


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_koyfin_csv(source: str | bytes | io.StringIO) -> pd.DataFrame:
    """Parst einen Koyfin-CSV-Export.

    Unterstützt sowohl ``;`` als auch ``,`` als Trenner und deutsche
    Dezimalkommata. Erste zwei Zeilen (Metadaten/Original-Überschriften) werden
    übersprungen; die Datenzeilen beginnen bei Zeile 3 (Header) bzw. 4 (Daten).
    """

    if isinstance(source, (bytes, bytearray)):
        raw = source.decode("utf-8", errors="replace")
    elif isinstance(source, io.StringIO):
        raw = source.getvalue()
    else:
        raw = Path(source).read_text(encoding="utf-8", errors="replace")

    sep = ";" if raw.count(";") > raw.count(",") else ","
    df = pd.read_csv(io.StringIO(raw), sep=sep, decimal=",", engine="python")

    # Anzahl Spalten abgleichen: überschüssige ignorieren, fehlende auffüllen.
    df = df.iloc[:, : len(KOYFIN_COLUMNS)].copy()
    df.columns = KOYFIN_COLUMNS[: df.shape[1]]
    for col in KOYFIN_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan

    df = _coerce_numeric(df)
    df = df.dropna(subset=["ticker"]).reset_index(drop=True)
    return df
