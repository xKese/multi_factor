"""Anzeige-Kontext (v1/v2-Primäranzeige) — app/ui/score_context.py."""

from __future__ import annotations

import pandas as pd
import pytest

from app.core.state import STATE
from app.ui import score_context


@pytest.fixture()
def v2_frame():
    return pd.DataFrame({"ticker": ["A"], "composite_score": [80.0]})


def test_is_v2_true(monkeypatch, v2_frame):
    monkeypatch.setattr(STATE.settings, "scoring_version", "v2", raising=False)
    monkeypatch.setattr(STATE, "scored", v2_frame, raising=False)
    assert score_context.is_v2() is True
    cols = score_context.primary_cols()
    assert cols["score"] == "composite_score"
    assert cols["zone"] == "zone_v2"
    assert cols["rec"] is None


def test_is_v2_false_when_v1_setting(monkeypatch, v2_frame):
    monkeypatch.setattr(STATE.settings, "scoring_version", "v1", raising=False)
    monkeypatch.setattr(STATE, "scored", v2_frame, raising=False)
    assert score_context.is_v2() is False
    cols = score_context.primary_cols()
    assert cols["score"] == "total_score"
    assert cols["rec"] == "recommendation"


def test_is_v2_false_without_columns(monkeypatch):
    monkeypatch.setattr(STATE.settings, "scoring_version", "v2", raising=False)
    monkeypatch.setattr(
        STATE, "scored", pd.DataFrame({"total_score": [50.0]}), raising=False
    )
    assert score_context.is_v2() is False


def test_class_of_score_v2_thresholds(monkeypatch, v2_frame):
    monkeypatch.setattr(STATE.settings, "scoring_version", "v2", raising=False)
    monkeypatch.setattr(STATE, "scored", v2_frame, raising=False)
    assert score_context.class_of_score(92)["code"] == "A"
    assert score_context.class_of_score(81)["code"] == "B+"
    assert score_context.class_of_score(70)["code"] == "B"
    assert score_context.class_of_score(20)["code"] == "F"
    assert score_context.class_of_score(None)["code"] == "–"


def test_class_of_score_v1_thresholds(monkeypatch):
    monkeypatch.setattr(STATE.settings, "scoring_version", "v1", raising=False)
    assert score_context.class_of_score(81)["code"] == "A"
    assert score_context.class_of_score(71)["code"] == "B+"


def test_class_color_both_forms():
    assert score_context.class_color("A") == score_context.class_color(
        "A - Exzellent"
    )
    assert score_context.class_color("unbekannt") is None


def test_zone_tone():
    assert score_context.zone_tone("KANDIDAT") == "up"
    assert score_context.zone_tone("VERKAUFEN") == "down"
    assert score_context.zone_tone("FILTER") is None
