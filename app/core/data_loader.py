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

from .schema import KOYFIN_COLUMNS, OPTIONAL_COLUMNS


NUMERIC_COLUMNS = [
    c
    for c in KOYFIN_COLUMNS
    if c not in {"ticker", "name", "sector", "industry", "region", "export_date"}
] + list(OPTIONAL_COLUMNS)


def _match_sma20(raw: str, normalized: str) -> bool:
    """Koyfin-Header wie ``SMA (20D)`` oder ``sma_20``; die Distanz-Variante
    ``SMA % (20D)`` wird ausgeschlossen."""
    return "%" not in raw and normalized in {"sma20", "sma20d"}


def _match_fwd_rev_growth(raw: str, normalized: str) -> bool:
    """Erwartetes Umsatzwachstum, z. B. ``Est. Revenue CAGR (3Y)``,
    ``Revenue Est. Growth (NTM)`` oder ``fwd_rev_growth``. Historische
    Umsatz-Header ohne Est/Fwd/NTM-Marker matchen nicht; Revisions-Header
    (``EPS Est. Revision (3M)``) enthalten "rev"+"est" und werden explizit
    ausgeschlossen."""
    if "revision" in normalized:
        return False
    return "rev" in normalized and any(
        marker in normalized for marker in ("est", "fwd", "ntm")
    )


_OPTIONAL_MATCHERS = {
    "sma_20": _match_sma20,
    "fwd_rev_growth": _match_fwd_rev_growth,
}


def _extract_optional_columns(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """Zieht optionale Spalten (``OPTIONAL_COLUMNS``) anhand des Headers heraus.

    Muss VOR dem positionalen Mapping laufen: Die 57 Basisspalten werden rein
    positional benannt, eine zusätzliche Spalte an beliebiger Stelle würde
    alles dahinter verschieben.
    """
    found: dict[str, pd.Series] = {}
    drop: list = []
    for col in df.columns:
        raw = str(col)
        normalized = re.sub(r"[^a-z0-9]", "", raw.lower())
        for name, matcher in _OPTIONAL_MATCHERS.items():
            if name not in found and matcher(raw, normalized):
                found[name] = df[col]
                drop.append(col)
                break
    return df.drop(columns=drop), found


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

    # Optionale Spalten (SMA-20, Forward-Umsatzwachstum) vor dem positionalen
    # Mapping herausziehen.
    df, optional = _extract_optional_columns(df)

    # Anzahl Spalten abgleichen: überschüssige ignorieren, fehlende auffüllen.
    df = df.iloc[:, : len(KOYFIN_COLUMNS)].copy()
    df.columns = KOYFIN_COLUMNS[: df.shape[1]]
    for col in KOYFIN_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    for name in OPTIONAL_COLUMNS:
        series = optional.get(name)
        df[name] = (
            pd.to_numeric(series, errors="coerce").values
            if series is not None
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
