"""Deutsche Zahlenformatierung für die UI.

Regeln:
- Dezimaltrenner ``,``
- Tausender-Trenner ``.``
- Prozent-Spalten (siehe :data:`PERCENT_FIELDS`) sind im DataFrame als
  Dezimalwerte gespeichert (0,755 = 75,5 %) und werden beim Anzeigen
  entsprechend umgerechnet.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.core.schema import PERCENT_COLUMNS


# PERCENT_COLUMNS aus dem Schema + im Scoring berechnete Ratios
# (``range_52w``, ``rev_growth_1y``, ``mom_12_1``, ``data_coverage``) —
# zentrale Quelle für "ist Prozentfeld".
PERCENT_FIELDS: set[str] = PERCENT_COLUMNS | {
    "range_52w",
    "rev_growth_1y",
    "mom_12_1",
    "data_coverage",
}

# Felder, die als ganze Zahl angezeigt werden.
INT_FIELDS: set[str] = {"piotroski"}

# Felder, die sinnvoll mit zwei Dezimalstellen angezeigt werden (Kennzahlen).
TWO_DEC_FIELDS: set[str] = {
    "altman_z",
    "beta",
    "current_ratio",
    "debt_equity",
    "peg",
    "ev_ebitda",
    "ocf_ni",
}

# Einfache Multiplikatoren / Ratios mit einer Nachkommastelle.
ONE_DEC_FIELDS: set[str] = {
    "pb",
    "pe",
    "pfcf",
    "ps",
    "int_coverage",
}


def fmt_de(value: Any, decimals: int = 2) -> str:
    """Zahl im deutschen Format: ``1.234,56``.

    Liefert ``"-"`` für NaN / None / nicht numerisch.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    if pd.isna(v):
        return "-"
    # Python formatiert mit ``,`` als Tausender und ``.`` als Dezimal —
    # wir tauschen nachträglich.
    s = f"{v:,.{decimals}f}"
    return s.replace(",", "_TMP_").replace(".", ",").replace("_TMP_", ".")


def fmt_percent(value: Any, decimals: int = 1) -> str:
    """Dezimalwert (0,755) → ``"75,5 %"``."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{fmt_de(v * 100, decimals)} %"


def fmt_signed_percent(value: Any, decimals: int = 1) -> str:
    """Wie :func:`fmt_percent`, aber mit Vorzeichen (auch bei positiven)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if v > 0 else ("−" if v < 0 else "")
    return f"{sign}{fmt_de(abs(v) * 100, decimals)} %"


def fmt_int(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    try:
        return fmt_de(int(round(float(value))), 0)
    except (TypeError, ValueError):
        return "-"


def fmt_market_cap(mio: Any) -> str:
    """Market Cap (Eingabe in Mio Währungseinheiten)."""
    if mio is None or (isinstance(mio, float) and pd.isna(mio)):
        return "-"
    try:
        v = float(mio)
    except (TypeError, ValueError):
        return "-"
    a = abs(v)
    if a >= 1_000_000:
        return f"{fmt_de(v / 1_000_000, 2)} Bio."
    if a >= 1_000:
        return f"{fmt_de(v / 1_000, 2)} Mrd."
    return f"{fmt_de(v, 0)} Mio."


def fmt_indicator(column: str, value: Any) -> str:
    """Dispatch: formatiert einen Wert passend zu seiner Spalte."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    if column == "market_cap":
        return fmt_market_cap(value)
    if column in PERCENT_FIELDS:
        return fmt_percent(value)
    if column in INT_FIELDS:
        return fmt_int(value)
    if column in TWO_DEC_FIELDS:
        return fmt_de(value, 2)
    if column in ONE_DEC_FIELDS:
        return fmt_de(value, 1)
    # Default: 2 Nachkommastellen
    return fmt_de(value, 2)


def parse_de(text: Any) -> float | None:
    """Deutsche Zahleneingabe (1.234,56) → float. Für Input-Felder."""
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().replace(" ", "")
    # Tausender-Punkte entfernen, Dezimalkomma in Punkt
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None
