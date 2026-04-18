"""Daten-Import (entspricht Sheet ``Daten_Import``)."""

from __future__ import annotations

import base64
import io
from datetime import datetime

import dash_bootstrap_components as dbc
from dash import Input, Output, callback, dcc, html, register_page

from app.core.data_loader import load_koyfin_csv
from app.core.state import STATE


def layout(**_) -> html.Div:
    return html.Div(
        [
            html.H2("Daten-Import"),
            dbc.Alert(
                [
                    html.Strong("Format: "),
                    "Koyfin-CSV-Export mit 57 Spalten (siehe Anleitung). ",
                    "Die ersten zwei Zeilen (Metadaten / Original-Header) werden übersprungen.",
                ],
                color="info",
            ),
            dcc.Upload(
                id="upload-csv",
                children=html.Div(
                    [
                        "Datei hierher ziehen oder ",
                        html.A("klicken zum Auswählen", className="text-primary"),
                    ]
                ),
                style={
                    "width": "100%",
                    "height": "120px",
                    "lineHeight": "120px",
                    "borderWidth": "2px",
                    "borderStyle": "dashed",
                    "borderRadius": "8px",
                    "textAlign": "center",
                    "backgroundColor": "#fafafa",
                },
                multiple=False,
            ),
            html.Div(id="upload-status", className="mt-3"),
            html.Hr(),
            html.H4("Aktuelles Universum"),
            html.Div(id="universe-summary"),
        ],
        className="p-4",
    )


@callback(
    Output("upload-status", "children"),
    Output("universe-summary", "children"),
    Input("upload-csv", "contents"),
    Input("upload-csv", "filename"),
    prevent_initial_call=False,
)
def _handle(contents: str | None, filename: str | None):
    status: object = ""
    if contents:
        _, b64 = contents.split(",", 1)
        raw = base64.b64decode(b64)
        try:
            df = load_koyfin_csv(raw)
            STATE.set_raw(df)
            status = dbc.Alert(
                f"✓ {filename}: {len(df):,} Aktien geladen um "
                f"{datetime.now():%H:%M:%S}.",
                color="success",
            )
        except Exception as exc:  # noqa: BLE001
            status = dbc.Alert(f"Fehler: {exc}", color="danger")

    if STATE.scored.empty:
        return status, dbc.Alert("Kein Universum geladen.", color="warning")

    df = STATE.scored
    summary = dbc.Table(
        [
            html.Tbody(
                [
                    html.Tr([html.Td("Aktien"), html.Td(f"{len(df):,}")]),
                    html.Tr(
                        [
                            html.Td("Filter bestanden"),
                            html.Td(f"{(df['filter_ok']=='JA').sum():,}"),
                        ]
                    ),
                    html.Tr(
                        [
                            html.Td("Ø Gesamt-Score"),
                            html.Td(f"{df['total_score'].dropna().mean():.1f}"),
                        ]
                    ),
                    html.Tr(
                        [
                            html.Td("Sektoren"),
                            html.Td(f"{df['sector'].nunique()}"),
                        ]
                    ),
                    html.Tr(
                        [
                            html.Td("Industrien"),
                            html.Td(f"{df['industry'].nunique()}"),
                        ]
                    ),
                    html.Tr(
                        [
                            html.Td("Regionen"),
                            html.Td(f"{df['region'].nunique()}"),
                        ]
                    ),
                ]
            )
        ],
        bordered=True,
        striped=True,
        style={"maxWidth": "400px"},
    )
    return status, summary


register_page(__name__, path="/daten-import", name="Daten-Import", layout=layout)
