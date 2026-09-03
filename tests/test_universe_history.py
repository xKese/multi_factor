"""Tests für das Punkt-in-Zeit-Archiv des Koyfin-Universums
(``koyfin_universe_history`` + Roh-CSV-Ablage)."""

from __future__ import annotations

import importlib
from datetime import date

import pandas as pd

from app.core import persistence


def _fresh_db(tmp_path, monkeypatch):
    """Persistenz-Modul auf eine frische SQLite-Datei zeigen lassen."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("KOYFIN_ARCHIVE_DIR", str(tmp_path / "archive"))
    importlib.reload(persistence)
    return persistence


def _universe(export_date: str, price: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "uid": ["AAA", "BBB"],
            "ticker": ["AAA", "BBB"],
            "name": ["Alpha AG", "Beta SE"],
            "export_date": [export_date, export_date],
            "last_price": [price, price + 1.0],
            "total_score": [70.0, 55.0],
        }
    )


def test_two_imports_keep_both_snapshots(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)

    p.save_universe(_universe("2026-08-01"), snapshot_date=date(2026, 8, 1))
    p.save_universe(_universe("2026-09-01"), snapshot_date=date(2026, 9, 1))

    snaps = p.list_snapshots()
    assert [(d, n) for d, n in snaps] == [
        (date(2026, 9, 1), 2),
        (date(2026, 8, 1), 2),
    ]

    old = p.load_universe_snapshot(date(2026, 8, 1))
    new = p.load_universe_snapshot(date(2026, 9, 1))
    assert old is not None and len(old) == 2
    assert new is not None and len(new) == 2
    assert set(old["uid"]) == {"AAA", "BBB"}

    # Bestehendes Verhalten unverändert: koyfin_universe hält nur den
    # letzten Import.
    current = p.load_universe()
    assert current is not None and len(current) == 2
    assert set(current["export_date"]) == {"2026-09-01"}


def test_reimport_same_date_replaces_only_that_snapshot(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)

    p.save_universe(
        _universe("2026-08-01", price=100.0), snapshot_date=date(2026, 8, 1)
    )
    p.save_universe(
        _universe("2026-09-01", price=200.0), snapshot_date=date(2026, 9, 1)
    )
    # Re-Import mit gleichem Datum, geänderten Werten → UPSERT dieses
    # Snapshots, der ältere bleibt unangetastet.
    p.save_universe(
        _universe("2026-09-01", price=300.0), snapshot_date=date(2026, 9, 1)
    )

    snaps = dict(p.list_snapshots())
    assert snaps == {date(2026, 8, 1): 2, date(2026, 9, 1): 2}

    new = p.load_universe_snapshot(date(2026, 9, 1))
    assert float(new.loc[new["uid"] == "AAA", "last_price"].iloc[0]) == 300.0

    old = p.load_universe_snapshot(date(2026, 8, 1))
    assert float(old.loc[old["uid"] == "AAA", "last_price"].iloc[0]) == 100.0


def test_snapshot_date_falls_back_to_export_date_then_today(
    tmp_path, monkeypatch
):
    p = _fresh_db(tmp_path, monkeypatch)

    # export_date im Frame → Snapshot-Datum aus der Spalte.
    p.save_universe(_universe("2026-07-15"))
    assert [d for d, _ in p.list_snapshots()] == [date(2026, 7, 15)]

    # Ohne verwertbares export_date → Importdatum (heute).
    df = _universe("2026-07-15")
    df["export_date"] = None
    p.save_universe(df)
    assert date.today() in {d for d, _ in p.list_snapshots()}


def test_archive_df_takes_precedence(tmp_path, monkeypatch):
    """Das gescorte Frame (Superset mit Score-Spalten) landet im Archiv,
    während koyfin_universe weiterhin das übergebene Roh-Frame hält."""
    p = _fresh_db(tmp_path, monkeypatch)

    raw = _universe("2026-08-01").drop(columns=["total_score"])
    scored = _universe("2026-08-01")
    scored["value_score"] = [80.0, 40.0]

    p.save_universe(raw, snapshot_date=date(2026, 8, 1), archive_df=scored)

    snap = p.load_universe_snapshot(date(2026, 8, 1))
    assert "value_score" in snap.columns
    assert "value_score" not in p.load_universe().columns


def test_schema_drift_adds_columns_without_data_loss(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)

    p.save_universe(_universe("2026-08-01"), snapshot_date=date(2026, 8, 1))

    widened = _universe("2026-09-01")
    widened["new_indicator"] = [1.5, 2.5]
    p.save_universe(widened, snapshot_date=date(2026, 9, 1))

    old = p.load_universe_snapshot(date(2026, 8, 1))
    new = p.load_universe_snapshot(date(2026, 9, 1))
    assert old["new_indicator"].isna().all()
    assert new["new_indicator"].notna().all()


def test_raw_csv_archived_and_overwritten(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)

    p.save_universe(
        _universe("2026-08-01"),
        snapshot_date=date(2026, 8, 1),
        raw_csv=b"alt",
    )
    path = tmp_path / "archive" / "koyfin_2026-08-01.csv"
    assert path.read_bytes() == b"alt"

    # Gleicher Dateiname → wird überschrieben.
    p.save_universe(
        _universe("2026-08-01"),
        snapshot_date=date(2026, 8, 1),
        raw_csv=b"neu",
    )
    assert path.read_bytes() == b"neu"


def test_fail_open_without_engine(monkeypatch):
    monkeypatch.setattr(persistence, "get_engine", lambda: None)
    assert persistence.load_universe_snapshot(date(2026, 8, 1)) is None
    assert persistence.list_snapshots() == []


def test_import_archives_v1_and_v2_scores(tmp_path, monkeypatch):
    """Ein Import erzeugt v1- UND v2-Scores, beide landen im PIT-Archiv
    (Spec 0.1/16); Listen-Spalten werden als JSON-Text abgelegt."""
    from pathlib import Path

    from app.core.config import Settings
    from app.core.data_loader import load_koyfin_csv
    from app.core.scoring import compute_scores
    from app.core.scoring_v2 import compute_scores_v2

    p = _fresh_db(tmp_path, monkeypatch)

    fixture = Path(__file__).resolve().parent / "fixtures" / "koyfin_sample.csv"
    raw = load_koyfin_csv(str(fixture))
    settings = Settings()
    scored = compute_scores(raw, settings)
    scored, _ = compute_scores_v2(scored, settings)

    p.save_universe(raw, snapshot_date=date(2026, 9, 1), archive_df=scored)

    snap = p.load_universe_snapshot(date(2026, 9, 1))
    assert snap is not None and len(snap) == len(raw)
    for col in ("total_score", "composite_z", "composite_pct", "zone_v2",
                "filter_pass", "data_coverage_v2"):
        assert col in snap.columns, col
    assert snap["composite_z"].notna().any()
    # filter_reasons als JSON-Text (leere Liste → "[]").
    assert snap["filter_reasons"].iloc[0] in ("[]",) or snap[
        "filter_reasons"
    ].iloc[0].startswith("[")
