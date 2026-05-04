"""Industrie-Perzentil-Übersicht (entspricht Sheet ``Perzentil_Hilfe``)."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html, register_page

from app.core.state import STATE
from app.pages.common import page_title, render_table


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

    return html.Div(
        [
            page_title(
                "Industrie-Perzentil Übersicht",
                f"Fallback auf Sektor-Perzentil bei < {min_count} Aktien "
                "(anpassbar im Einstellungen-Tab).",
            ),
            render_table(summary, id="perz-table", page_size=50),
        ]
    )


register_page(__name__, path="/perzentil-hilfe", name="Perzentil-Hilfe", layout=layout)
