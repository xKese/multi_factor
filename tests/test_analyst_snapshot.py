"""Tests des vollständigen Koyfin-Steckbriefs und des Titel-Verlaufs."""

from __future__ import annotations

import importlib
from datetime import date

import pandas as pd
import pytest

from app.core.analyst import snapshot as sn
from app.core.config import Settings
from app.core.schema import KOYFIN_COLUMNS, OPTIONAL_COLUMNS


def _row():
    row = {c: 1.5 for c in KOYFIN_COLUMNS}
    row.update({c: 2.5 for c in OPTIONAL_COLUMNS})
    row.update({
        "uid": "SAP", "ticker": "SAP", "name": "SAP SE", "sector": "IT", "industry": "Software",
        "industry_group": "Software & Services", "industry_group_source": "gics", "region": "EU",
        "export_date": date(2026, 10, 17), "ipo_date": "1988-11-04", "div_yield": 0.021, "roe": float("nan"),
        "market_cap": 250000.0, "net_income": 6000.0, "composite_score": 88.0, "composite_pct": 0.9,
        "composite_z": 1.1, "classification_v2": "A", "zone_v2": "KANDIDAT", "data_coverage_v2": 0.95,
        "z_value": 0.4, "cov_value": 1.0, "z_quality": 0.8, "z_momentum": -0.2, "z_investment": 0.1,
        "filter_pass": True, "filter_reasons": [], "piotroski": 7.0, "altman_z": 3.2,
        "sma_signal": "✓ GOLDEN CROSS", "trend_phase": "etabliert", "trend_warning": False,
        "total_score": 71.0, "classification": "B+ · Sehr Gut", "recommendation": "BUY",
        "accruals": -0.02, "asset_growth": 0.05, "mom_12_1": 0.12, "dist_52w_high": -0.04,
    })
    return pd.Series(row)


def test_every_koyfin_column_appears_exactly_once():
    snap = sn.koyfin_snapshot(_row(), Settings())
    keys = [it["key"] for g in snap["groups"] for it in g["items"]]
    for col in list(KOYFIN_COLUMNS) + list(OPTIONAL_COLUMNS):
        assert keys.count(col) == 1, col
    titles = [g["title"] for g in snap["groups"]]
    assert titles[0] == "Identität" and sn.OTHER_GROUP not in titles


def test_labels_percent_and_formatting():
    snap = sn.koyfin_snapshot(_row(), Settings())
    items = {it["key"]: it for g in snap["groups"] for it in g["items"]}
    assert items["div_yield"]["label"] == "Dividendenrendite"
    assert items["div_yield"]["percent"] is True and items["div_yield"]["text"] == "2,1 %"
    assert items["roe"]["value"] is None and items["roe"]["text"] == "–"
    assert items["market_cap"]["text"] == "250.000"
    assert items["pe"]["text"] == "1,50" and items["export_date"]["text"] == "2026-10-17"
    assert items["ipo_date"]["text"] == "1988-11-04"
    sc = snap["scores"]
    assert sc["v2"]["zone_v2"] == "KANDIDAT" and sc["v2"]["factors"]["quality"]["z"] == 0.8
    assert sc["v1"]["recommendation"] == "BUY" and sc["piotroski"] == 7.0
    assert sc["filter"]["pass"] is True and sc["filter"]["reasons"] == []


def test_render_snapshot_text_and_truncation():
    text = sn.render_snapshot_text(sn.koyfin_snapshot(_row(), Settings()))
    assert text.startswith("**SAP — SAP SE** (uid SAP)")
    assert "Composite v2: 88,0 / 100" in text and "Zone KANDIDAT" in text
    assert "### Bewertung" in text and "- Dividendenrendite: 2,1 %" in text
    assert "- ROE: –" in text and "### Piotroski-Rohdaten/Bilanz" in text
    short = sn.render_snapshot_text(sn.koyfin_snapshot(_row(), Settings()), max_chars=400)
    assert len(short) <= 440 and "gekürzt" in short


def test_other_group_catches_unknown_columns():
    row = _row()
    # Spalte künstlich aus allen Gruppen entfernen → landet in „Weitere“.
    sn_groups = sn._GROUPED
    sn._GROUPED = sn_groups - {"beta"}
    try:
        snap = sn.koyfin_snapshot(row, None)
    finally:
        sn._GROUPED = sn_groups
    other = [g for g in snap["groups"] if g["title"] == sn.OTHER_GROUP]
    assert other and other[0]["items"][0]["key"] == "beta"


@pytest.fixture()
def history_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/hist.db")
    monkeypatch.setenv("KOYFIN_ARCHIVE_DIR", str(tmp_path / "archive"))
    from app.core import persistence

    importlib.reload(persistence)
    raw = pd.DataFrame({"uid": ["SAP", "MBG"], "ticker": ["SAP", "MBG"], "name": ["SAP SE", "Mercedes"], "pe": [20.0, 6.0]})
    base = raw.assign(composite_score=[80.0, 60.0], zone_v2=["HALTEN", "VERKAUFEN"], filter_reasons=[[], ["x"]])
    persistence.save_universe(raw, snapshot_date=date(2026, 9, 1), archive_df=base)
    newer = base.assign(composite_score=[88.0, 55.0], zone_v2=["KANDIDAT", "VERKAUFEN"])
    persistence.save_universe(raw, snapshot_date=date(2026, 10, 1), archive_df=newer)
    return persistence


def test_uid_history_and_render(history_db):
    rows = sn.uid_history("SAP", n=5)
    assert [r["snapshot_date"] for r in rows] == ["2026-09-01", "2026-10-01"]
    assert [r["composite_score"] for r in rows] == [80.0, 88.0]
    assert "zone_v2" in rows[0] and "z_value" not in rows[0]
    rows2 = sn.uid_history("SAP", columns=("composite_score",), n=1)
    assert len(rows2) == 1 and set(rows2[0]) == {"snapshot_date", "composite_score"}
    text = sn.render_history_text(rows)
    assert text.splitlines()[0].startswith("| Stand |") and "| 2026-10-01 |" in text and "88,00" in text
    assert "nur ein Snapshot" in sn.render_history_text(rows2)
    assert sn.uid_history("NOPE") == [] and sn.render_history_text([]).startswith("(kein")
    assert history_db.load_uid_history("SAP").shape[0] == 2
