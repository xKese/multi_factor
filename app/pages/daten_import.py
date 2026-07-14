"""Daten-Import (entspricht Sheet ``Daten_Import``)."""

from __future__ import annotations

import base64
import io
import logging
from datetime import date, datetime

import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, callback, dcc, html, register_page

from app.core.data_loader import load_koyfin_csv
from app.core.persistence import save_sector_score_history, save_universe
from app.core.sector_momentum import aggregate_sectors, aggregates_to_history_records
from app.core.state import STATE
from app.pages.common import page_title
from app.ui import fmt_de, section_header

log = logging.getLogger(__name__)


def _snapshot_date_from_universe(df: pd.DataFrame) -> date:
    """Liefert das Snapshot-Datum für die Score-History.

    Bevorzugt das Maximum von ``export_date`` aus dem Universum (Koyfin liefert
    je Zeile ein Datum mit). Bei fehlendem oder unparsbarem Wert: heutiges
    Datum.
    """
    if "export_date" in df.columns:
        parsed = pd.to_datetime(df["export_date"], errors="coerce").dropna()
        if not parsed.empty:
            return parsed.max().date()
    return date.today()


def _persist_sector_score_history() -> None:
    """Persistiert die aktuellen Sektor-/Industrie-Aggregate als Snapshot.

    Wird nach :func:`STATE.set_raw` aufgerufen. Fehler werden geloggt, aber
    nicht propagiert — die Score-History ist eine Komfortfunktion, ihr
    Ausfall darf den Daten-Import nicht blockieren.
    """
    df = STATE.scored
    if df is None or df.empty:
        return
    try:
        agg = aggregate_sectors(df)
        records = aggregates_to_history_records(agg)
        if not records:
            return
        snap = _snapshot_date_from_universe(df)
        save_sector_score_history(records, snap)
    except Exception as exc:  # noqa: BLE001
        log.warning("Sektor-Score-Historie konnte nicht gespeichert werden: %s", exc)


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
            _persist_sector_score_history()
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
