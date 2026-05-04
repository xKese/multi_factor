"""Daten-Import (entspricht Sheet ``Daten_Import``)."""

from __future__ import annotations

import base64
import io
from datetime import datetime

import dash_bootstrap_components as dbc
from dash import Input, Output, callback, dcc, html, register_page

from app.core.data_loader import load_koyfin_csv
from app.core.persistence import save_universe
from app.core.state import STATE
from app.pages.common import page_title
from app.ui import fmt_de, section_header


def layout(**_) -> html.Div:
    return html.Div(
        [
            page_title(
                "Daten-Import",
                "Koyfin-CSV-Export mit 57 Spalten (siehe Anleitung). Die ersten "
                "zwei Zeilen werden übersprungen.",
            ),
            dcc.Upload(
                id="upload-csv",
                children=html.Div(
                    [
                        "Datei hierher ziehen oder ",
                        html.A("klicken zum Auswählen"),
                    ]
                ),
                className="ms-upload",
                multiple=False,
            ),
            html.Div(id="upload-status", className="mt-3"),
            section_header("Aktuelles Universum"),
            html.Div(id="universe-summary"),
        ]
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
            alerts = [
                dbc.Alert(
                    f"✓ {filename}: {fmt_de(len(df), 0)} Aktien geladen um "
                    f"{datetime.now():%H:%M:%S}.",
                    color="success",
                )
            ]
            try:
                save_universe(df)
            except Exception as exc:  # noqa: BLE001
                alerts.append(
                    dbc.Alert(
                        f"Warnung: Datenbank-Speicherung fehlgeschlagen ({exc}). "
                        "Daten sind nur in dieser Session verfügbar.",
                        color="warning",
                    )
                )
            status = html.Div(alerts)
        except Exception as exc:  # noqa: BLE001
            status = dbc.Alert(f"Fehler: {exc}", color="danger")

    if STATE.scored.empty:
        return status, dbc.Alert("Kein Universum geladen.", color="warning")

    df = STATE.scored
    summary = dbc.Table(
        [
            html.Tbody(
                [
                    html.Tr([html.Td("Aktien"), html.Td(fmt_de(len(df), 0))]),
                    html.Tr(
                        [
                            html.Td("Filter bestanden"),
                            html.Td(fmt_de((df["filter_ok"] == "JA").sum(), 0)),
                        ]
                    ),
                    html.Tr(
                        [
                            html.Td("Ø Gesamt-Score"),
                            html.Td(fmt_de(df["total_score"].dropna().mean(), 1)),
                        ]
                    ),
                    html.Tr(
                        [
                            html.Td("Sektoren"),
                            html.Td(fmt_de(df["sector"].nunique(), 0)),
                        ]
                    ),
                    html.Tr(
                        [
                            html.Td("Industrien"),
                            html.Td(fmt_de(df["industry"].nunique(), 0)),
                        ]
                    ),
                    html.Tr(
                        [
                            html.Td("Regionen"),
                            html.Td(fmt_de(df["region"].nunique(), 0)),
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
