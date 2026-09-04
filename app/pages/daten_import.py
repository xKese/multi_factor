"""Daten-Import (entspricht Sheet ``Daten_Import``)."""

from __future__ import annotations

import base64
import io
import logging
from datetime import datetime

import dash_bootstrap_components as dbc
from dash import Input, Output, callback, dcc, html, register_page

from app.core import signal_events
from app.core.data_loader import load_koyfin_csv
from app.core.momentum import classify_momentum
from app.core.persistence import (
    expire_overrides,
    list_snapshots,
    save_sector_score_history,
    save_signal_history,
    save_universe,
)
from app.core.sector_momentum import aggregate_sectors, aggregates_to_history_records
from app.core.signal_events import snapshot_date_from_universe
from app.core.uid import duplicate_ticker_info
from app.core.state import STATE
from app.pages.common import page_title
from app.ui import fmt_de, section_header

log = logging.getLogger(__name__)

# Rückwärtskompatibler Alias (Single Source liegt in signal_events).
_snapshot_date_from_universe = snapshot_date_from_universe


def _persist_sector_score_history(filename: str | None = None) -> None:
    """Persistiert die aktuellen Sektor-/Industrie-Aggregate als Snapshot.

    Wird nach :func:`STATE.set_raw` aufgerufen. ``filename`` dient als
    Fallback-Quelle für das Snapshot-Datum (Koyfin-Dateinamen tragen das
    Export-Datum). Fehler werden geloggt, aber nicht propagiert — die
    Score-History ist eine Komfortfunktion, ihr Ausfall darf den
    Daten-Import nicht blockieren.
    """
    df = STATE.scored
    if df is None or df.empty:
        return
    try:
        # v1- und v2-Aggregate persistieren (getrennte History-Levels),
        # damit beide Anzeige-Versionen konsistente Delta-Reihen haben.
        records = aggregates_to_history_records(aggregate_sectors(df))
        if "composite_score" in df.columns:
            records += aggregates_to_history_records(
                aggregate_sectors(df, score_col="composite_score")
            )
        if not records:
            return
        snap = _snapshot_date_from_universe(df, filename)
        save_sector_score_history(records, snap)
    except Exception as exc:  # noqa: BLE001
        log.warning("Sektor-Score-Historie konnte nicht gespeichert werden: %s", exc)


def _persist_signal_history(filename: str | None = None) -> None:
    """Persistiert die SMA-Signal-Zustände je Aktie als Snapshot.

    Grundlage für die Event-Erkennung des Momentum-Monitors („NEU seit
    Import", Signal-Alter). Fehler werden geloggt, aber nicht propagiert.
    """
    df = STATE.scored
    if df is None or df.empty:
        return
    try:
        # Dedup auf uid statt Ticker: bei Ticker-Kollisionen (z. B. zwei
        # "SAN") bekommt jede Aktie ihre eigene Historien-Zeile. Die
        # ``ticker``-Spalte der History-Tabelle trägt die uid als Schlüssel —
        # für eindeutige Ticker ist das identisch zum bisherigen Wert.
        key = "uid" if "uid" in df.columns else "ticker"
        frame = df.dropna(subset=["ticker"]).drop_duplicates(key).copy()
        if key == "uid":
            frame["ticker"] = frame["uid"]
        frame["momentum"] = frame.apply(
            lambda r: classify_momentum(
                r.get("last_price"), r.get("sma_50"), r.get("sma_200")
            ),
            axis=1,
        )
        cols = [
            "ticker",
            "momentum",
            "trend_phase",
            "last_price",
            "sma_20",
            "sma_50",
            "sma_200",
            "ret_1m",
            "mom_12_1",
            "dist_52w_high",
            "total_score",
        ]
        frame = frame[[c for c in cols if c in frame.columns]]
        save_signal_history(frame, snapshot_date_from_universe(df, filename))
    except Exception as exc:  # noqa: BLE001
        log.warning("Signal-Historie konnte nicht gespeichert werden: %s", exc)
    finally:
        signal_events.clear_cache()


def layout(**_) -> html.Div:
    return html.Div(
        [
            page_title(
                "Daten-Import",
                "Koyfin-CSV-Export mit 57 Spalten (+ optional SMA-20, siehe "
                "Anleitung). Die ersten zwei Zeilen werden übersprungen.",
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
            # Ticker-Kollisionen sichtbar machen: gleiche Symbole für
            # verschiedene Firmen (z. B. "SAN" = Sanofi und Banco Santander)
            # werden intern über eindeutige Kennungen getrennt geführt.
            dupes = duplicate_ticker_info(df)
            if dupes:
                lines = [
                    html.Li(
                        f"{ticker}: "
                        + " · ".join(
                            f"{name or '(ohne Name)'} → {uid}" for uid, name in pairs
                        )
                    )
                    for ticker, pairs in dupes
                ]
                alerts.append(
                    dbc.Alert(
                        [
                            html.Strong(
                                f"⚠ {len(dupes)} Ticker mehrfach vergeben — "
                                "die Titel werden intern getrennt geführt "
                                "(eindeutige Kennung in Links/Suche):"
                            ),
                            html.Ul(lines, className="mb-0 mt-2"),
                        ],
                        color="warning",
                    )
                )
            try:
                # PIT-Archiv: gescortes Universum (Rohkennzahlen + Scores)
                # wird zusätzlich als Punkt-in-Zeit-Snapshot archiviert,
                # die Roh-CSV unverändert unter data/archive/ abgelegt.
                snap = snapshot_date_from_universe(df, filename)
                save_universe(
                    df,
                    snapshot_date=snap,
                    archive_df=STATE.scored,
                    raw_csv=raw,
                )
                snapshots = list_snapshots()
                if snapshots:
                    alerts.append(
                        dbc.Alert(
                            f"PIT-Archiv: {fmt_de(len(snapshots), 0)} Snapshot"
                            f"{'s' if len(snapshots) != 1 else ''} archiviert "
                            f"(dieser Import: {snap:%d.%m.%Y}).",
                            color="info",
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                alerts.append(
                    dbc.Alert(
                        f"Warnung: Datenbank-Speicherung fehlgeschlagen ({exc}). "
                        "Daten sind nur in dieser Session verfügbar.",
                        color="warning",
                    )
                )
            try:
                # Abgelaufene Overrides bei jedem Import deaktivieren (Spec 8).
                expired = expire_overrides(snapshot_date_from_universe(df, filename))
                if expired:
                    alerts.append(
                        dbc.Alert(
                            f"{len(expired)} Override(s) abgelaufen — erneuern "
                            "oder schließen (siehe /modellportfolio): "
                            + ", ".join(str(e["uid"]) for e in expired),
                            color="warning",
                        )
                    )
            except Exception:  # noqa: BLE001
                log.warning("Override-Ablaufprüfung beim Import fehlgeschlagen")
            try:
                # Hinweis, welcher Rebalance-Modus erkannt wurde (Spec 11.2).
                from app.core.persistence import load_model_portfolio_meta
                from app.core.portfolio_construction import detect_rebalance_mode

                mode = detect_rebalance_mode(
                    snapshot_date_from_universe(df, filename),
                    STATE.settings,
                    load_model_portfolio_meta(),
                )
                alerts.append(
                    dbc.Alert(
                        [
                            "Erkannter Rebalance-Modus: ",
                            html.Strong(mode),
                            " — ",
                            dcc.Link(
                                "zum Modellportfolio",
                                href="/modellportfolio",
                            ),
                            ".",
                        ],
                        color="info",
                    )
                )
            except Exception:  # noqa: BLE001
                log.warning("Rebalance-Modus-Erkennung beim Import fehlgeschlagen")
            _persist_sector_score_history(filename)
            _persist_signal_history(filename)
            if STATE.v2_diagnostics:
                # Leitprinzip Composite v2: keine stillen Fallbacks —
                # Scoring-Diagnosen direkt nach dem Import anzeigen.
                from app.ui.theme import diagnostics_panel

                alerts.append(diagnostics_panel(STATE.v2_diagnostics))
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
