"""CSV-Import für den Koyfin-Export.

Koyfin exportiert Prozent- und Return-Werte bereits als Dezimalzahlen
(z. B. ``0,1889`` für 18,89 %, ``1,2504`` für 125,04 %). Der Loader nimmt
dieses Format als gegeben an — eine frühere Divisions-Heuristik (|x|>1 →
/100) führte bei Titeln mit Returns > 100 % zu einer fälschlichen
Verkleinerung um Faktor 100.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .schema import KOYFIN_COLUMNS


NUMERIC_COLUMNS = [
    c
    for c in KOYFIN_COLUMNS
    if c not in {"ticker", "name", "sector", "industry", "region", "export_date"}
] + ["sma_20"]


def _extract_optional_sma20(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series | None]:
    """Zieht eine optionale SMA-20-Spalte anhand des Headers heraus.

    Muss VOR dem positionalen Mapping laufen: Die 57 Basisspalten werden rein
    positional benannt, eine zusätzliche Spalte an beliebiger Stelle würde
    alles dahinter verschieben. Erkannt werden Koyfin-Header wie ``SMA (20D)``
    oder ``sma_20``; die Distanz-Variante ``SMA % (20D)`` wird ausgeschlossen.
    """
    for col in df.columns:
        raw = str(col)
        if "%" in raw:
            continue
        normalized = re.sub(r"[^a-z0-9]", "", raw.lower())
        if normalized in {"sma20", "sma20d"}:
            return df.drop(columns=[col]), df[col]
    return df, None


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

    # Optionale SMA-20-Spalte vor dem positionalen Mapping herausziehen.
    df, sma_20 = _extract_optional_sma20(df)

    # Anzahl Spalten abgleichen: überschüssige ignorieren, fehlende auffüllen.
    df = df.iloc[:, : len(KOYFIN_COLUMNS)].copy()
    df.columns = KOYFIN_COLUMNS[: df.shape[1]]
    for col in KOYFIN_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    df["sma_20"] = (
        pd.to_numeric(sma_20, errors="coerce").values
        if sma_20 is not None
        else np.nan
    )

    df = _coerce_numeric(df)

    # Koyfin liefert die annualisierte Volatilität bereits als Prozent-Wert
    # (z. B. ``28,4`` für 28,4 %), während andere Prozent-Felder (Returns,
    # Margins) als Dezimalanteil exportiert werden. Damit alle Prozent-Felder
    # einheitlich als Dezimalanteil im DataFrame liegen (Konvention von
    # ``PERCENT_FIELDS``/``fmt_percent``), skalieren wir hier um.
    if "volatility_1y" in df.columns:
        df["volatility_1y"] = df["volatility_1y"] / 100.0

    # Koyfin-Watchlist-Exporte enthalten Gruppen-Überschriften (z. B. "MSCI World",
    # "Unclassified", "Watch") als Zeilen, in denen nur die Ticker-Spalte gefüllt ist.
    # Erkennen über fehlenden Namen UND fehlenden Kurs — echte Datenzeilen haben beide.
    if {"name", "last_price"}.issubset(df.columns):
        df = df.loc[~(df["name"].isna() & df["last_price"].isna())]

    df = df.dropna(subset=["ticker"]).reset_index(drop=True)
    return df
