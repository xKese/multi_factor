"""Event-Ableitung für den Momentum-Monitor.

Vergleicht den aktuellen SMA-Signal-Zustand je Aktie mit der persistierten
Import-Historie (``universe_signal_history``) und liefert echte Ereignisse:
Signalwechsel seit dem letzten Import (``is_new``) sowie das Alter des
aktuellen Zustands (``state_since`` / ``days_in_state``).

Die Historie baut sich mit jedem CSV-Import auf; ohne Datenbank oder vor dem
zweiten Import bleiben die Event-Felder leer (fail-open, raised nie).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .momentum import classify_momentum
from .persistence import load_signal_history


EVENT_COLUMNS = [
    "ticker",
    "momentum",
    "momentum_prev",
    "is_new",
    "prev_snapshot_date",
    "state_since",
    "days_in_state",
    "imports_in_state",
]

# Ein Cache-Eintrag genügt: Die Events ändern sich nur mit einem neuen Import,
# nicht mit Filter-Klicks oder Settings-Änderungen (SMA-Zustände hängen nicht
# an den Gewichten). Key = (Snapshot-Datum, Universumsgröße).
_cache: tuple[tuple[str, int], pd.DataFrame] | None = None


def snapshot_date_from_universe(df: pd.DataFrame) -> date:
    """Snapshot-Datum eines Universums: Maximum von ``export_date``,
    Fallback heutiges Datum."""
    if df is not None and "export_date" in df.columns:
        parsed = pd.to_datetime(df["export_date"], errors="coerce").dropna()
        if not parsed.empty:
            return parsed.max().date()
    return date.today()


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame(columns=EVENT_COLUMNS)


def derive_signal_events(
    current: pd.DataFrame, history: pd.DataFrame, snapshot_date: date
) -> pd.DataFrame:
    """Leitet je Ticker Signalwechsel und Zustands-Alter ab.

    ``current`` braucht ``ticker, last_price, sma_50, sma_200``; der Zustand
    wird kanonisch via :func:`classify_momentum` neu berechnet (kein
    Icon-Label-Vergleich). ``history``-Zeilen mit ``snapshot_date >=``
    aktuellem Datum werden verworfen — der Import-Hook hat den heutigen
    Snapshot bereits geschrieben, gegen sich selbst wird nie verglichen.
    """
    if current is None or current.empty:
        return _empty_events()

    cur = current.dropna(subset=["ticker"]).drop_duplicates("ticker")
    states = {
        str(r["ticker"]): classify_momentum(
            r.get("last_price"), r.get("sma_50"), r.get("sma_200")
        )
        for _, r in cur.iterrows()
    }

    past: dict[str, list[tuple[date, str]]] = {}
    if history is not None and not history.empty:
        hist = history[history["snapshot_date"] < snapshot_date]
        for _, r in hist.iterrows():
            past.setdefault(str(r["ticker"]), []).append(
                (r["snapshot_date"], str(r["momentum"]))
            )

    records: list[dict] = []
    for ticker, state in states.items():
        series = sorted(past.get(ticker, []), key=lambda t: t[0], reverse=True)
        prev_date, prev_state = (series[0] if series else (None, None))
        is_new = prev_state is not None and prev_state != state

        state_since: date | None = None
        imports_in_state = 1  # inkl. heutigem Snapshot
        if is_new or prev_state is None:
            state_since = snapshot_date if is_new else None
        else:
            # Streak rückwärts: solange der Zustand identisch bleibt. Ein
            # abweichender Zustand oder das Serienende (Ticker fehlt in einem
            # älteren Snapshot) beendet den Streak konservativ.
            state_since = snapshot_date
            for snap, mom in series:
                if mom != state:
                    break
                state_since = snap
                imports_in_state += 1

        records.append(
            {
                "ticker": ticker,
                "momentum": state,
                "momentum_prev": prev_state,
                "is_new": bool(is_new),
                "prev_snapshot_date": prev_date,
                "state_since": state_since,
                "days_in_state": (
                    (snapshot_date - state_since).days
                    if state_since is not None
                    else None
                ),
                "imports_in_state": (
                    imports_in_state if prev_state is not None else None
                ),
            }
        )
    return pd.DataFrame(records, columns=EVENT_COLUMNS)


def load_signal_events(scored: pd.DataFrame) -> pd.DataFrame:
    """Events für das aktuelle Universum, DB-Zugriff gecacht pro Import."""
    global _cache
    if scored is None or scored.empty:
        return _empty_events()
    snapshot_date = snapshot_date_from_universe(scored)
    key = (snapshot_date.isoformat(), len(scored))
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    history = load_signal_history()
    events = derive_signal_events(scored, history, snapshot_date)
    _cache = (key, events)
    return events


def clear_cache() -> None:
    """Nach jedem CSV-Import aufrufen (deckt auch Re-Importe derselben
    Datei ab, bei denen der Cache-Key gleich bliebe)."""
    global _cache
    _cache = None
