"""Industrie-Perzentil-Übersicht (entspricht Sheet ``Perzentil_Hilfe``)."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html, register_page

from dash import dcc

from app.core.state import STATE
from app.pages.common import page_title, render_table


_V2_TEXT = """
## Composite v2 — Neutralisierung statt Industrie-Perzentil

Das primäre Scoring (Composite v2) arbeitet nicht mit Industrie-Perzentilen,
sondern mit **Region×Sektor-neutralen Z-Scores**:

1. **Neutralisierungsgruppe** je Indikator: primär `Region × Sektor`;
   hat die Gruppe weniger als 20 gültige Werte, fällt sie auf `Sektor
   (global)` und zuletzt auf `Global` zurück. Die verwendete Ebene steht
   je Titel und Indikator in der Einzelanalyse (Spalte „Neutralisierung").
2. **Winsorisierung + Z-Score** je Gruppe: Ausreißer werden auf die
   3 %/97 %-Quantile gestutzt, dann standardisiert (Cap ±3). Fehlende
   Werte bleiben fehlend — es gibt keine Median-Imputation.
3. **Faktor-Score** = Mittel der gültigen Indikator-Z-Scores (Value,
   Quality, Momentum, Investment), **Composite** = gewichtetes Mittel der
   Faktoren (0,30/0,30/0,25/0,15) mit zweiter globaler Standardisierung.
4. **Klassen** aus dem Composite-Perzentil: A ≥ 90 · B+ ≥ 80 · B ≥ 66,7 ·
   C ≥ 50 · D ≥ 33 · F darunter. **Zonen:** KANDIDAT (Perzentil ≥ 80),
   HALTEN (66,7–80), VERKAUFEN (< 66,7), FILTER (Universumsfilter nicht
   bestanden).

Die folgende Industrie-Übersicht gilt für das **Vergleichs-Scoring v1**
(Perzentil-Ranking mit Industrie→Sektor-Fallback).
"""


def layout(**_) -> html.Div:
    if STATE.scored.empty:
        return html.Div(
            [
                page_title("Industrie-Perzentil Übersicht"),
                dbc.Alert("Keine Daten geladen.", color="info"),
            ]
        )

    df = STATE.scored
    min_count = STATE.settings.min_stocks_per_industry
    summary = (
        df.groupby(["industry", "sector"], dropna=False)
        .size()
        .reset_index(name="anzahl")
        .sort_values("anzahl", ascending=False)
    )
    summary["perzentil_typ"] = summary["anzahl"].apply(
        lambda n: "Industrie" if n >= min_count else "Sektor (Fallback)"
    )

    children: list = [
        page_title(
            "Perzentil- & Neutralisierungs-Hilfe",
            f"Fallback auf Sektor-Perzentil bei < {min_count} Aktien "
            "(anpassbar im Einstellungen-Tab).",
        ),
    ]
    if STATE.settings.scoring_version == "v2":
        children.append(dcc.Markdown(_V2_TEXT, className="ms-card p-3 mb-3"))
    children.append(render_table(summary, id="perz-table", page_size=50))
    return html.Div(children)


register_page(__name__, path="/perzentil-hilfe", name="Perzentil-Hilfe", layout=layout)
