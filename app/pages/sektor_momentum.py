"""Sektor-Momentum: woechentliche Momentum-Matrix fuer GICS-Sektoren und
Industrie-ETFs (ersetzt die manuell gepflegte TAA-Conviction-Excel)."""

from __future__ import annotations

import base64
from datetime import date, datetime
from typing import Iterable

import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, callback, dcc, html, register_page

from app.core.momentum import (
    MOMENTUM_DEATH,
    MOMENTUM_DOWN,
    MOMENTUM_GOLDEN,
    MOMENTUM_NONE,
    MOMENTUM_STATES,
    MOMENTUM_UP,
)
from app.core.persistence import load_sector_snapshots, save_sector_snapshot
from app.core.sector_momentum import build_snapshot_frame, load_sector_csv
from app.core.sectors import (
    GROUP_INDUSTRY,
    GROUP_SECTOR,
    INDUSTRY_ETFS,
    SECTOR_ETFS,
)
from app.pages.common import page_title
from app.ui import fmt_de, kpi_band, section_header


MOMENTUM_STYLES: dict[str, dict[str, str]] = {
    MOMENTUM_GOLDEN: {"backgroundColor": "#1b7f3a", "color": "#ffffff"},
    MOMENTUM_UP: {"backgroundColor": "#c8e6c9", "color": "#1b3a22"},
    MOMENTUM_DOWN: {"backgroundColor": "#ffcc80", "color": "#4a2a00"},
    MOMENTUM_DEATH: {"backgroundColor": "#c62828", "color": "#ffffff"},
    MOMENTUM_NONE: {"backgroundColor": "#f0f0f0", "color": "#888888"},
}

_CELL_BASE_STYLE: dict[str, str] = {
    "padding": "8px 10px",
    "fontSize": "13px",
    "fontWeight": "500",
    "textAlign": "center",
    "whiteSpace": "nowrap",
    "borderRight": "1px solid var(--ms-border)",
}


def _format_date(value) -> str:
    try:
        return pd.Timestamp(value).strftime("%d.%m.")
    except (ValueError, TypeError):
        return str(value)


def _group_grid(df: pd.DataFrame, mapping: dict[str, str]) -> html.Div:
    """Rendert eine Gruppe (Sektoren oder Industrien) als farbcodierte Matrix."""

    if df.empty:
        return dbc.Alert(
            "Noch keine Snapshots für diese Gruppe.", color="secondary"
        )

    pivot = df.pivot_table(
        index="ticker",
        columns="snapshot_date",
        values="momentum",
        aggfunc="first",
    )
    # Zeilenreihenfolge = Reihenfolge aus sectors.py; nur vorhandene Ticker.
    tickers = [t for t in mapping.keys() if t in pivot.index]
    if not tickers:
        return dbc.Alert(
            "Keine bekannten Ticker in den Snapshots.", color="secondary"
        )
    pivot = pivot.reindex(tickers)
    date_cols = sorted(pivot.columns)

    header = html.Thead(
        html.Tr(
            [
                html.Th("Ticker", style={"minWidth": "70px"}),
                html.Th("Name", style={"minWidth": "180px"}),
                *[
                    html.Th(
                        _format_date(d),
                        style={"textAlign": "center", "minWidth": "130px"},
                    )
                    for d in date_cols
                ],
            ]
        )
    )

    rows = []
    for t in tickers:
        cells = [
            html.Td(t, style={"fontWeight": "600"}),
            html.Td(mapping.get(t, "")),
        ]
        for d in date_cols:
            value = pivot.at[t, d]
            if pd.isna(value):
                style = {**_CELL_BASE_STYLE, **MOMENTUM_STYLES[MOMENTUM_NONE]}
                cells.append(html.Td("–", style=style))
            else:
                style = {
                    **_CELL_BASE_STYLE,
                    **MOMENTUM_STYLES.get(value, MOMENTUM_STYLES[MOMENTUM_NONE]),
                }
                cells.append(html.Td(value, style=style))
        rows.append(html.Tr(cells))

    return dbc.Table(
        [header, html.Tbody(rows)],
        bordered=True,
        hover=True,
        responsive=True,
        className="mb-0",
        style={"fontSize": "13px"},
    )


def _counts(df: pd.DataFrame, group: str) -> dict[str, int]:
    if df.empty:
        return {s: 0 for s in MOMENTUM_STATES}
    latest_date = df["snapshot_date"].max()
    latest = df[(df["snapshot_date"] == latest_date) & (df["group"] == group)]
    counts = latest["momentum"].value_counts().to_dict()
    return {s: int(counts.get(s, 0)) for s in MOMENTUM_STATES}


def _kpi_cells(counts: dict[str, int], label_prefix: str) -> Iterable[dict]:
    return [
        {
            "label": f"{label_prefix} · Death Cross",
            "value": fmt_de(counts[MOMENTUM_DEATH], 0),
            "tone": "down",
        },
        {
            "label": f"{label_prefix} · Kurs < SMA-200",
            "value": fmt_de(counts[MOMENTUM_DOWN], 0),
            "tone": "warn",
        },
        {
            "label": f"{label_prefix} · Kurs > SMA-200",
            "value": fmt_de(counts[MOMENTUM_UP], 0),
        },
        {
            "label": f"{label_prefix} · Golden Cross",
            "value": fmt_de(counts[MOMENTUM_GOLDEN], 0),
            "tone": "up",
        },
    ]


def _kpi_band(df: pd.DataFrame) -> html.Div:
    sector_counts = _counts(df, GROUP_SECTOR)
    industry_counts = _counts(df, GROUP_INDUSTRY)
    cells = [
        *_kpi_cells(sector_counts, "Sektoren"),
        *_kpi_cells(industry_counts, "Industrien"),
    ]
    return kpi_band(cells)


def _definitions() -> dbc.Accordion:
    return dbc.Accordion(
        [
            dbc.AccordionItem(
                [
                    html.Div(
                        [
                            html.Strong("Golden Cross"),
                            " — Stark Positives Momentum: Kurs & SMA-50 liegen "
                            "über SMA-200 und Kurs liegt über SMA-50.",
                        ],
                        className="mb-2",
                    ),
                    html.Div(
                        [
                            html.Strong("Kurs > SMA-200"),
                            " — Positives Momentum: Kurs & SMA-50 liegen über "
                            "SMA-200, aber Kurs liegt unter SMA-50.",
                        ],
                        className="mb-2",
                    ),
                    html.Div(
                        [
                            html.Strong("Kurs < SMA-200"),
                            " — Negatives Momentum: Kurs liegt unter SMA-200 "
                            "und SMA-50, aber SMA-50 liegt über SMA-200.",
                        ],
                        className="mb-2",
                    ),
                    html.Div(
                        [
                            html.Strong("Death Cross"),
                            " — Stark Negatives Momentum: Kurs & SMA-50 liegen "
                            "unter SMA-200 und Kurs liegt unter SMA-50.",
                        ]
                    ),
                ],
                title="Definitionen der vier Momentum-Zustände",
            )
        ],
        start_collapsed=True,
        className="mb-3",
    )


def _render_body(df: pd.DataFrame) -> list:
    if df.empty:
        return [
            dbc.Alert(
                "Noch keine Snapshots – CSV hochladen.", color="info"
            )
        ]

    sectors_df = df[df["group"] == GROUP_SECTOR]
    industries_df = df[df["group"] == GROUP_INDUSTRY]

    return [
        _kpi_band(df),
        section_header(
            "GICS-Sektoren",
            subtitle="11 globale iShares-Sektor-ETFs",
        ),
        _group_grid(sectors_df, SECTOR_ETFS),
        section_header(
            "Industrien",
            subtitle="19 Industrie- und Themen-ETFs",
        ),
        _group_grid(industries_df, INDUSTRY_ETFS),
        section_header("Legende & Definitionen"),
        _definitions(),
    ]


def _upload_row() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Small(
                        "Snapshot-Datum", className="text-muted d-block mb-1"
                    ),
                    dcc.DatePickerSingle(
                        id="sm-date",
                        date=date.today().isoformat(),
                        display_format="DD.MM.YYYY",
                        first_day_of_week=1,
                    ),
                ],
                className="me-4",
            ),
            html.Div(
                [
                    html.Small(
                        "Koyfin-CSV (GICS-Sektoren / Industrien)",
                        className="text-muted d-block mb-1",
                    ),
                    dcc.Upload(
                        id="sm-upload",
                        children=html.Div(
                            [
                                "Datei hierher ziehen oder ",
                                html.A("klicken zum Auswählen"),
                            ]
                        ),
                        className="ms-upload",
                        multiple=False,
                    ),
                ],
                className="flex-grow-1",
            ),
        ],
        className="d-flex align-items-end mb-3",
    )


def layout(**_) -> html.Div:
    df = load_sector_snapshots(12)
    return html.Div(
        [
            page_title(
                "Sektor-Momentum",
                "Wöchentliche Momentum-Matrix · GICS-Sektoren & Industrie-ETFs",
            ),
            _upload_row(),
            html.Div(id="sm-status", className="mb-3"),
            html.Div(id="sm-body", children=_render_body(df)),
        ]
    )


@callback(
    Output("sm-body", "children"),
    Output("sm-status", "children"),
    Input("sm-upload", "contents"),
    State("sm-upload", "filename"),
    State("sm-date", "date"),
    prevent_initial_call=True,
)
def _on_upload(contents: str | None, filename: str | None, snap_date: str | None):
    if not contents:
        return _render_body(load_sector_snapshots(12)), ""

    try:
        _, b64 = contents.split(",", 1)
        raw = base64.b64decode(b64)
    except Exception as exc:  # noqa: BLE001
        return _render_body(load_sector_snapshots(12)), dbc.Alert(
            f"Fehler beim Decodieren: {exc}", color="danger"
        )

    try:
        parsed = load_sector_csv(raw)
    except Exception as exc:  # noqa: BLE001
        return _render_body(load_sector_snapshots(12)), dbc.Alert(
            f"Fehler beim Parsen der CSV: {exc}", color="danger"
        )

    if parsed.empty:
        return _render_body(load_sector_snapshots(12)), dbc.Alert(
            "Keine bekannten Ticker (GICS-Sektoren oder Industrie-ETFs) "
            "in der CSV gefunden.",
            color="warning",
        )

    try:
        snap = (
            datetime.fromisoformat(snap_date).date()
            if snap_date
            else date.today()
        )
    except ValueError:
        snap = date.today()

    frame = build_snapshot_frame(parsed, snap)

    try:
        n = save_sector_snapshot(frame, snap)
        alert = dbc.Alert(
            f"✓ {filename or 'Upload'}: {fmt_de(n, 0)} Ticker für "
            f"{snap:%d.%m.%Y} gespeichert.",
            color="success",
        )
    except Exception as exc:  # noqa: BLE001
        alert = dbc.Alert(
            f"Warnung: Datenbank-Speicherung fehlgeschlagen ({exc}). "
            "Snapshot wurde nicht persistiert.",
            color="warning",
        )

    return _render_body(load_sector_snapshots(12)), alert


register_page(
    __name__,
    path="/sektor-momentum",
    name="Sektor-Momentum",
    layout=layout,
)
