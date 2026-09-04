"""Integrationstests der Scoring-v2-Design-Integration (WP6/WP7)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.core.config import Settings
from app.core.data_loader import load_koyfin_csv
from app.core.scoring import compute_scores
from app.core.scoring_v2 import compute_scores_v2
from app.core.sector_momentum import (
    aggregate_sectors,
    aggregates_to_history_records,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "koyfin_sample.csv"


@pytest.fixture(scope="module")
def scored_v2():
    df = load_koyfin_csv(FIXTURE)
    settings = Settings()
    scored = compute_scores(df, settings)
    out, _ = compute_scores_v2(scored, settings)
    return out


def test_aggregate_sectors_v2_levels(scored_v2):
    agg = aggregate_sectors(scored_v2, score_col="composite_score")
    assert agg, "keine Sektor-Aggregate"
    assert all(s["history_level"] == "sector_v2" for s in agg)
    records = aggregates_to_history_records(agg)
    levels = {r["level"] for r in records}
    assert levels <= {"sector_v2", "industry_v2"}


def test_aggregate_sectors_v1_levels_unchanged(scored_v2):
    agg = aggregate_sectors(scored_v2)
    records = aggregates_to_history_records(agg)
    levels = {r["level"] for r in records}
    assert levels <= {"sector", "industry"}


def test_aggregate_sectors_unknown_score_col_falls_back(scored_v2):
    agg = aggregate_sectors(scored_v2, score_col="gibts_nicht")
    assert agg and agg[0]["history_level"] == "sector"


def test_factsheet_context_contains_v2(scored_v2):
    from app.core.factsheet_pdf import build_context

    ticker = str(scored_v2.iloc[0]["uid"])
    ctx = build_context(ticker, scored_v2, Settings(), show_peers=False)
    v2 = ctx["v2"]
    assert v2 is not None
    assert v2["zone"] in {"KANDIDAT", "HALTEN", "VERKAUFEN", "FILTER", "–"}
    assert len(v2["factors"]) == 4
    assert v2["class_accent"].startswith("#")
    assert v2["zone_accent"].startswith("#")


def test_factsheet_context_without_v2_columns():
    from app.core.factsheet_pdf import build_context

    df = load_koyfin_csv(FIXTURE)
    scored = compute_scores(df, Settings())
    ticker = str(scored.iloc[0]["uid"])
    ctx = build_context(ticker, scored, Settings(), show_peers=False)
    assert ctx["v2"] is None


def test_compute_rank_score_col(scored_v2):
    from app.core.factsheet_pdf import compute_rank

    ticker = str(scored_v2.iloc[0]["uid"])
    rank_v1 = compute_rank(scored_v2, ticker)
    rank_v2 = compute_rank(scored_v2, ticker, score_col="composite_score")
    assert rank_v1["sector_total"] == rank_v2["sector_total"]


def test_einzelanalyse_v2_indicator_cards(scored_v2):
    """Faktor-Karten zeigen je Indikator Rohwert + Z-Score (Factsheet-Stil)."""
    import dash
    import dash_bootstrap_components as dbc

    dash.Dash(
        __name__,
        use_pages=True,
        pages_folder="",
        external_stylesheets=[dbc.themes.BOOTSTRAP],
        suppress_callback_exceptions=True,
    )
    from app.pages import einzelanalyse as ea

    r = scored_v2.iloc[0]
    entries = ea._v2_factor_indicators(scored_v2, r)
    # Alle vier Faktoren mit segmentgerechten Indikatoren aufgelöst.
    assert set(entries) == {"value", "quality", "momentum", "investment"}
    assert ("fcf_yield", "fcf_yield_v2") in entries["value"]

    card = ea._v2_indicator_card(scored_v2, r, "value", entries["value"])
    table = card.children[1]
    header = [th.children for th in table.children[0].children.children]
    assert header[0] == "Kennzahl"
    assert header[1] == "Wert"
    assert "Z-Score" in header[2]
    body_rows = table.children[1].children
    assert len(body_rows) == len(entries["value"])
    # Wert-Zelle trägt den formatierten Rohwert (nicht nur den Z-Score).
    first_val = body_rows[0].children[1]
    assert first_val.className == "ms-ind-val"
    assert first_val.children != ""


def test_dashboard_v2_uses_v1_design_template(scored_v2, monkeypatch):
    """Dashboard v2: ms-toptable mit Composite-Pill + Zonen-Chip, Sektor-
    Ranking auf composite_score, eindeutige interaktive IDs im Layout."""
    import dash
    import dash_bootstrap_components as dbc

    dash.Dash(
        __name__,
        use_pages=True,
        pages_folder="",
        external_stylesheets=[dbc.themes.BOOTSTRAP],
        suppress_callback_exceptions=True,
    )
    from app.core.state import STATE
    from app.pages import dashboard as dp

    monkeypatch.setattr(STATE, "scored", scored_v2, raising=False)
    monkeypatch.setattr(STATE.settings, "scoring_version", "v2", raising=False)
    monkeypatch.setattr(STATE, "v2_diagnostics", [], raising=False)

    table = dp._top_table(scored_v2, None, 5)
    header = [
        th.children for th in table.children.children[0].children.children
    ]
    assert "Zone" in header and "Empfehlung" not in header
    row = table.children.children[1].children[0]
    zone_chip = row.children[7].children
    assert zone_chip.className.startswith("ms-tt-rec is-")
    assert zone_chip.children in {"KANDIDAT", "HALTEN", "VERKAUFEN", "FILTER", "–"}
    # Mini-Faktor-Profil: vier v2-Balken.
    assert len(row.children[5].children.children) == 4

    # v1-Vergleichssicht bleibt per Parameter erreichbar.
    table_v1 = dp._top_table(scored_v2, None, 5, version="v1")
    header_v1 = [
        th.children for th in table_v1.children.children[0].children.children
    ]
    assert "Empfehlung" in header_v1

    # Layout: Store/Pattern-IDs genau einmal (Accordion rendert statisch).
    def _count(node, needle):
        n, stack = 0, [node]
        while stack:
            x = stack.pop()
            if getattr(x, "id", None) == needle:
                n += 1
            ch = getattr(x, "children", None)
            if isinstance(ch, (list, tuple)):
                stack.extend(ch)
            elif ch is not None and not isinstance(ch, (str, int, float)):
                stack.append(ch)
        return n

    layout = dp.layout()
    assert _count(layout, "dash-sector-filter") == 1
    assert _count(layout, "dash-top-table") == 1
