"""Tests für die Event-Ableitung aus der Signal-Historie (ohne Datenbank)."""

from __future__ import annotations

from datetime import date

import pandas as pd

from app.core.signal_events import (
    derive_signal_events,
    snapshot_date_from_universe,
)


TODAY = date(2026, 7, 14)


def _universe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            # AAA: Golden · BBB: Death · CCC: Golden · DDD: Kurs < SMA-200
            "ticker": ["AAA", "BBB", "CCC", "DDD"],
            "last_price": [110, 90, 105, 100],
            "sma_50": [105, 95, 100, 98],
            "sma_200": [100, 100, 98, 101],
        }
    )


def _history() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # AAA: 2 Importe Golden, davor anderer Zustand → Streak = 3 inkl. heute.
            {"snapshot_date": date(2026, 7, 7), "ticker": "AAA", "momentum": "Golden Cross"},
            {"snapshot_date": date(2026, 6, 30), "ticker": "AAA", "momentum": "Golden Cross"},
            {"snapshot_date": date(2026, 6, 23), "ticker": "AAA", "momentum": "Kurs > SMA-200"},
            # BBB: Zustand gewechselt → NEU.
            {"snapshot_date": date(2026, 7, 7), "ticker": "BBB", "momentum": "Kurs < SMA-200"},
            # DDD: heutiger Snapshot bereits geschrieben (muss ignoriert werden).
            {"snapshot_date": TODAY, "ticker": "DDD", "momentum": "Kurs < SMA-200"},
            {"snapshot_date": date(2026, 7, 7), "ticker": "DDD", "momentum": "Kurs < SMA-200"},
        ]
    )


def test_streak_age_and_imports():
    ev = derive_signal_events(_universe(), _history(), TODAY).set_index("ticker")
    assert ev.loc["AAA", "momentum"] == "Golden Cross"
    assert not ev.loc["AAA", "is_new"]
    assert ev.loc["AAA", "state_since"] == date(2026, 6, 30)
    assert ev.loc["AAA", "days_in_state"] == 14
    assert ev.loc["AAA", "imports_in_state"] == 3


def test_state_change_is_new():
    ev = derive_signal_events(_universe(), _history(), TODAY).set_index("ticker")
    assert ev.loc["BBB", "is_new"]
    assert ev.loc["BBB", "momentum"] == "Death Cross"
    assert ev.loc["BBB", "momentum_prev"] == "Kurs < SMA-200"
    assert ev.loc["BBB", "days_in_state"] == 0
    assert ev.loc["BBB", "state_since"] == TODAY


def test_unknown_ticker_has_no_event_data():
    ev = derive_signal_events(_universe(), _history(), TODAY).set_index("ticker")
    assert pd.isna(ev.loc["CCC", "momentum_prev"])
    assert not ev.loc["CCC", "is_new"]
    assert pd.isna(ev.loc["CCC", "days_in_state"])


def test_todays_snapshot_in_history_is_excluded():
    # DDD hat den heutigen Snapshot bereits in der Historie — verglichen wird
    # trotzdem gegen den 7.7., nie gegen sich selbst.
    ev = derive_signal_events(_universe(), _history(), TODAY).set_index("ticker")
    assert not ev.loc["DDD", "is_new"]
    assert ev.loc["DDD", "state_since"] == date(2026, 7, 7)
    assert ev.loc["DDD", "days_in_state"] == 7


def test_streak_broken_by_divergent_state():
    history = pd.DataFrame(
        [
            {"snapshot_date": date(2026, 7, 7), "ticker": "AAA", "momentum": "Golden Cross"},
            {"snapshot_date": date(2026, 6, 30), "ticker": "AAA", "momentum": "Death Cross"},
            {"snapshot_date": date(2026, 6, 23), "ticker": "AAA", "momentum": "Golden Cross"},
        ]
    )
    ev = derive_signal_events(_universe(), history, TODAY).set_index("ticker")
    # Streak endet am 30.6. (Death) — der ältere Golden-Snapshot zählt nicht.
    assert ev.loc["AAA", "state_since"] == date(2026, 7, 7)
    assert ev.loc["AAA", "imports_in_state"] == 2


def test_empty_history_and_empty_universe():
    ev = derive_signal_events(_universe(), pd.DataFrame(), TODAY)
    assert len(ev) == 4
    assert not ev["is_new"].any()
    assert ev["days_in_state"].isna().all()

    assert derive_signal_events(pd.DataFrame(), _history(), TODAY).empty


def test_snapshot_date_from_universe():
    df = _universe()
    df["export_date"] = "2026-07-14"
    assert snapshot_date_from_universe(df) == TODAY
    assert snapshot_date_from_universe(pd.DataFrame()) == date.today()


def test_parse_koyfin_filename_date():
    from app.core.signal_events import parse_koyfin_filename_date

    assert parse_koyfin_filename_date(
        "koyfin_MSCI World_2026.08.07_08.31.02.300.csv"
    ) == date(2026, 8, 7)
    assert parse_koyfin_filename_date("koyfin_export.csv") is None
    assert parse_koyfin_filename_date(None) is None
    # Ungültiges Datum (13. Monat) → None statt Exception.
    assert parse_koyfin_filename_date("koyfin_x_2026.13.07_08.00.00.000.csv") is None


def test_snapshot_date_filename_fallback():
    # export_date vorhanden → führend, Dateiname wird ignoriert.
    df = _universe()
    df["export_date"] = "2026-07-14"
    assert (
        snapshot_date_from_universe(df, "koyfin_MSCI World_2026.08.07_08.31.02.300.csv")
        == TODAY
    )
    # Ohne export_date → Datum aus dem Dateinamen.
    assert (
        snapshot_date_from_universe(
            pd.DataFrame(), "koyfin_MSCI World_2026.08.07_08.31.02.300.csv"
        )
        == date(2026, 8, 7)
    )
    # Weder Spalte noch Dateiname → heute.
    assert snapshot_date_from_universe(pd.DataFrame(), "export.csv") == date.today()


def test_snapshot_date_rejects_implausible_export_dates():
    """Numerische oder unplausible export_date-Werte dürfen nicht als
    Epoche (01.01.1970) fehlinterpretiert werden — die Fallback-Kette
    (Dateiname → heute) greift stattdessen."""
    from datetime import timedelta

    filename = "koyfin_MSCI World_2026.08.07_08.31.02.300.csv"

    # Numerische Spalte (z. B. durch verschobene Spaltenzuordnung).
    df_num = pd.DataFrame({"export_date": [46200.0, 46200.0]})
    assert snapshot_date_from_universe(df_num, filename) == date(2026, 8, 7)
    assert snapshot_date_from_universe(df_num, None) == date.today()

    # Datum vor 2000 (Epoche) → verworfen.
    df_epoch = pd.DataFrame({"export_date": ["1970-01-01"]})
    assert snapshot_date_from_universe(df_epoch, filename) == date(2026, 8, 7)

    # Datum weit in der Zukunft → verworfen.
    far = (date.today() + timedelta(days=400)).isoformat()
    assert (
        snapshot_date_from_universe(pd.DataFrame({"export_date": [far]}), None)
        == date.today()
    )

    # Gültige Strings bleiben führend, auch gemischt mit Zahlen.
    df_mixed = pd.DataFrame({"export_date": [46200.0, "2026-07-14"]})
    assert snapshot_date_from_universe(df_mixed, filename) == date(2026, 7, 14)
