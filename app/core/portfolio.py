"""M&S-Portfolio: Koyfin-Watchlist-Import und Handlungs-Flag-Logik.

Der Portfolio-Export ist eine reine Ticker-Liste (keine Gewichte). Anders als
der 57-Spalten-Universums-Import (``data_loader.load_koyfin_csv``) ist
Spaltenzahl und -reihenfolge hier unbekannt — der Loader erkennt die
Ticker-/Name-Spalte am Header statt über ein positionales Schema.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd

from .momentum import PHASE_TIRED_BEAR, PHASE_TIRED_BULL


# ── Loader ─────────────────────────────────────────────────────────────────

def _normalize_header(col: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(col).lower())


def _find_column(df: pd.DataFrame, aliases: set[str]) -> str | None:
    for col in df.columns:
        if _normalize_header(col) in aliases:
            return col
    return None


def load_portfolio_csv(source: str | bytes | io.StringIO) -> pd.DataFrame:
    """Parst einen Koyfin-Watchlist-Export (Ticker-Liste, keine Gewichte).

    Liefert einen DataFrame mit den Spalten ``ticker`` und ``name`` (Name kann
    leer sein), in Datei-Reihenfolge, Ticker upper-case und dedupliziert.
    Gruppen-Kopfzeilen (nur die Ticker-Spalte gefüllt, z. B. "MSCI World")
    werden verworfen. Raises ``ValueError``, wenn keine Ticker gefunden werden.
    """

    if isinstance(source, (bytes, bytearray)):
        raw = source.decode("utf-8", errors="replace")
    elif isinstance(source, io.StringIO):
        raw = source.getvalue()
    else:
        raw = Path(source).read_text(encoding="utf-8", errors="replace")

    sep = ";" if raw.count(";") > raw.count(",") else ","
    try:
        df = pd.read_csv(io.StringIO(raw), sep=sep, decimal=",", engine="python")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"CSV konnte nicht gelesen werden: {exc}") from exc
    if df.empty or df.shape[1] == 0:
        raise ValueError("Keine Ticker in der Datei gefunden")

    ticker_col = _find_column(df, {"ticker", "symbol"}) or df.columns[0]
    name_col = _find_column(df, {"name"})
    if name_col is None and df.shape[1] >= 2:
        candidates = [c for c in df.columns if c != ticker_col]
        name_col = candidates[0] if candidates else None

    # Gruppen-Kopfzeilen: außer dem Ticker ist alles leer. Bei einer reinen
    # Ein-Spalten-Ticker-Liste gibt es nichts zu prüfen.
    other_cols = [c for c in df.columns if c != ticker_col]
    if other_cols:
        all_empty = df[other_cols].apply(
            lambda row: all(pd.isna(v) or not str(v).strip() for v in row),
            axis=1,
        )
        df = df.loc[~all_empty]

    tickers = df[ticker_col].astype(str).str.strip().str.upper()
    names = (
        df[name_col].astype(str).str.strip().replace("NAN", "")
        if name_col is not None
        else pd.Series("", index=df.index)
    )
    names = names.where(names.str.lower() != "nan", "")

    out = pd.DataFrame({"ticker": tickers, "name": names})
    out = out[(out["ticker"] != "") & (out["ticker"] != "NAN")]
    out = out.drop_duplicates(subset="ticker", keep="first").reset_index(drop=True)
    if out.empty:
        raise ValueError("Keine Ticker in der Datei gefunden")
    return out


# ── Handlungs-Flags ────────────────────────────────────────────────────────

FLAG_SELL = "SELL"
FLAG_FILTER = "FILTER-FAIL"
FLAG_DEATH = "DEATH CROSS"
FLAG_BEARISH = "UNTER SMA-200"
FLAG_TIRED = "ERMÜDET"
FLAG_NEW = "SIGNAL NEU"

# Severity: kleiner = dringlicher. Bestimmt Sortierung der Flag-Tabelle.
FLAG_SEVERITY = {
    FLAG_SELL: 0,
    FLAG_FILTER: 1,
    FLAG_DEATH: 2,
    FLAG_BEARISH: 3,
    FLAG_TIRED: 4,
    FLAG_NEW: 5,
}


def _row_flags(row: pd.Series) -> list[str]:
    flags: list[str] = []
    rec = str(row.get("recommendation") or "")
    if rec == "SELL":
        flags.append(FLAG_SELL)
    if rec == "Filter nicht bestanden":
        flags.append(FLAG_FILTER)
    sig = str(row.get("sma_signal") or "")
    if sig == "⚠ DEATH CROSS":
        flags.append(FLAG_DEATH)
    if sig == "▼ Kurs < SMA-200":
        flags.append(FLAG_BEARISH)
    if str(row.get("trend_phase") or "") in (PHASE_TIRED_BULL, PHASE_TIRED_BEAR):
        flags.append(FLAG_TIRED)
    if bool(row.get("is_new")):
        flags.append(FLAG_NEW)
    return flags


def build_flags(pf: pd.DataFrame) -> pd.DataFrame:
    """Filtert die Portfolio-Sicht auf Positionen mit Handlungsbedarf.

    ``pf``: scored-Zeilen der Portfolio-Ticker, optional mit ``is_new``.
    Liefert nur Zeilen mit ≥ 1 Flag, ergänzt um ``flags`` (list[str]) und
    ``severity`` (min der Flag-Severities), sortiert nach (severity asc,
    total_score asc) — dringlichste und schwächste Titel zuerst.
    """
    if pf is None or pf.empty:
        return pd.DataFrame(columns=[*getattr(pf, "columns", []), "flags", "severity"])

    result = pf.copy()
    result["flags"] = result.apply(_row_flags, axis=1)
    result = result[result["flags"].map(len) > 0].copy()
    if result.empty:
        return result
    result["severity"] = result["flags"].map(
        lambda fl: min(FLAG_SEVERITY[f] for f in fl)
    )
    return result.sort_values(
        ["severity", "total_score"], ascending=[True, True], na_position="last"
    )
