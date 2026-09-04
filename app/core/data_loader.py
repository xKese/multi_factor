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

from .schema import KOYFIN_COLUMNS, OPTIONAL_COLUMNS, OPTIONAL_TEXT_COLUMNS
from .uid import assign_uids


NUMERIC_COLUMNS = [
    c
    for c in KOYFIN_COLUMNS
    if c not in {"ticker", "name", "sector", "industry", "region", "export_date"}
] + [c for c in OPTIONAL_COLUMNS if c not in OPTIONAL_TEXT_COLUMNS]


def _match_sma20(raw: str, normalized: str) -> bool:
    """Koyfin-Header wie ``SMA (20D)`` oder ``sma_20``; die Distanz-Variante
    ``SMA % (20D)`` wird ausgeschlossen."""
    return "%" not in raw and normalized in {"sma20", "sma20d"}


def _match_fwd_rev_growth(raw: str, normalized: str) -> bool:
    """Erwartetes Umsatzwachstum, z. B. ``Est Rev CAGR (1Y)``,
    ``Revenue Est. Growth (NTM)`` oder ``fwd_rev_growth``.

    Bewusst eng gefasst: zusätzlich zu "rev" + Est/Fwd/NTM-Marker muss
    "cagr" oder "growth" im Namen stehen, und EPS-/Revisions-Header sind
    ausgeschlossen. Koyfin kürzt "Revision" zu "Rev" ab — der reale
    EPS-Revisions-Header ``EPS Est Avg Rev % (FY1E - 3M)`` enthält
    "rev"+"est" und würde sonst fälschlich extrahiert, was alle
    nachfolgenden Basisspalten positional verschiebt."""
    if "eps" in normalized or "revision" in normalized:
        return False
    if not any(marker in normalized for marker in ("cagr", "growth")):
        return False
    return "rev" in normalized and any(
        marker in normalized for marker in ("est", "fwd", "ntm")
    )


def _match_ev_ebit(raw: str, normalized: str) -> bool:
    """EV/EBIT, z. B. ``EV / EBIT (LTM)`` — die EBITDA-Variante ist eine
    Basisspalte und wird ausgeschlossen."""
    return "evebit" in normalized and "evebitda" not in normalized


def _match_net_debt_ebitda(raw: str, normalized: str) -> bool:
    """Nettoverschuldung/EBITDA, z. B. ``Net Debt / EBITDA (LTM)``."""
    return "netdebt" in normalized and "ebitda" in normalized


def _match_fcf_yield(raw: str, normalized: str) -> bool:
    """FCF-Yield (FCF/EV), z. B. ``FCF Yield (EV)`` oder ``fcf_yield``."""
    return "fcf" in normalized and "yield" in normalized


def _match_adv_3m(raw: str, normalized: str) -> bool:
    """Durchschnittlicher Tagesumsatz 3M, z. B. ``ADV (3M)`` oder
    ``Avg Daily Value Traded 3M``."""
    if "adv" in normalized and "3m" in normalized:
        return True
    return normalized.startswith("avgdaily") and "3m" in normalized


def _match_ipo_date(raw: str, normalized: str) -> bool:
    """Datum der Erstnotiz, z. B. ``IPO Date`` oder ``ipo_date``."""
    return "ipo" in normalized


_OPTIONAL_MATCHERS = {
    "sma_20": _match_sma20,
    "fwd_rev_growth": _match_fwd_rev_growth,
    "ev_ebit": _match_ev_ebit,
    "net_debt_ebitda": _match_net_debt_ebitda,
    "fcf_yield": _match_fcf_yield,
    "adv_3m": _match_adv_3m,
    "ipo_date": _match_ipo_date,
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


def validate_universe_plausibility(df: pd.DataFrame) -> list[str]:
    """Sanity-Checks gegen verrutschte Spaltenzuordnung (positionales Mapping).

    Eine falsch extrahierte oder zusätzliche Spalte verschiebt alle
    nachfolgenden Basisspalten — dann landen z. B. Beta-Werte in ``ret_12m``
    oder Wachstumsraten in ``sma_200`` (SMA-Distanzen in Millionen %).
    Median-basierte, bewusst laxe Schwellen; geprüft wird nur, wo Daten
    vorliegen. Liefert eine Liste verletzter Checks (leer = plausibel).
    """
    def _col(name: str) -> pd.Series:
        series = df[name] if name in df.columns else pd.Series(dtype=float)
        return pd.to_numeric(series, errors="coerce")

    problems: list[str] = []

    ret = _col("ret_12m").dropna()
    if not ret.empty and ret.abs().median() > 5:
        problems.append(
            f"Median |Return 12M| = {ret.abs().median():.1f} (> 5,0 = 500 %)"
        )

    if "last_price" in df.columns and "sma_200" in df.columns:
        price = _col("last_price")
        sma = _col("sma_200")
        both = (price > 0) & (sma > 0)
        if both.any():
            ratio = (price[both] / sma[both]).median()
            if not 0.2 <= ratio <= 5:
                problems.append(
                    f"Median Kurs/SMA-200 = {ratio:.2f} (außerhalb 0,2–5)"
                )

    beta = _col("beta").dropna()
    if not beta.empty and beta.abs().median() > 5:
        problems.append(f"Median |Beta| = {beta.abs().median():.1f} (> 5)")

    return problems


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
        if series is None:
            df[name] = np.nan
        elif name in OPTIONAL_TEXT_COLUMNS:
            # ``ipo_date`` bleibt Text (ISO-Datum); Parsen übernimmt der
            # IPO-Filter, damit ein unlesbares Datum keinen Import blockiert.
            df[name] = series.astype("string").str.strip().values
        else:
            df[name] = pd.to_numeric(series, errors="coerce").values

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

    # Eindeutige interne Kennung je Zeile — Koyfin-Ticker sind ohne
    # Börsensuffix nicht garantiert eindeutig (z. B. "SAN" = Sanofi UND
    # Banco Santander). Details in app/core/uid.py.
    df = assign_uids(df)

    problems = validate_universe_plausibility(df)
    if problems:
        raise ValueError(
            "Import abgelehnt — Spaltenzuordnung unplausibel (vermutlich "
            "weicht die Spaltenreihenfolge vom 57-Spalten-Schema ab oder eine "
            "Zusatzspalte verschiebt das Mapping): " + " · ".join(problems)
        )
    return df
