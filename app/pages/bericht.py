"""Dash-Seite „Bericht" — Investment-Komitee-Bericht erzeugen und exportieren.

Optionen (Portfolio, Vergleichs-Snapshots, Abschnitte), Vorschau der Key
Findings/KPIs und zwei Downloads: PDF (WeasyPrint-Pipeline, bei fehlendem
Renderer eigenständiges HTML) und Excel-Anhang mit allen Tabellen. Die
Berechnung läuft im Dash-freien Builder ``app.core.committee_report``.
"""

from __future__ import annotations

import logging
from datetime import date

import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dcc, html, no_update, register_page

from app.core import persistence
from app.core.committee_report import (
    DEFAULT_TOP_N,
    REPORT_TITLE,
    ReportOptions,
    build_committee_report,
)
from app.core.signal_events import snapshot_date_from_universe
from app.core.state import STATE
from app.pages.common import page_title
from app.ui import fmt_de, fmt_percent, kpi_band, panel
from app.ui.formatters import fmt_int
from app.ui.theme import diagnostics_panel

log = logging.getLogger(__name__)

AUTO = "auto"

# Checklist-Werte ↔ ReportOptions-Felder.
SECTION_OPTIONS: list[tuple[str, str, str]] = [
    ("include_sectors", "sectors", "Sektoren"),
    ("include_model_portfolio", "model_portfolio", "Modellportfolio"),
    ("include_factor_timing", "factor_timing", "Factor Timing"),
    ("include_agents", "agents", "Agenten-Ratings"),
    ("include_diagnostics", "data_quality", "Datenqualität & Diagnose"),
    ("include_parameters", "parameters", "Parameter-Anhang"),
    ("include_risk", "risk", "Risiko & Benchmark (langsam, benötigt Kurs-Cache)"),
]
DEFAULT_SECTIONS = [key for _f, key, _l in SECTION_OPTIONS if key != "risk"]

SECTION_TITLES: dict[str, str] = {
    "universe": "Universum",
    "changes": "Veränderungen (Vorimport / ~1M)",
    "sectors": "Sektoren",
    "portfolio": "Portfolio",
    "model_portfolio": "Modellportfolio",
    "factor_timing": "Factor Timing",
    "risk": "Risiko & Benchmark",
    "agents": "Agenten-Ratings",
    "data_quality": "Datenqualität & Diagnose",
    "parameters": "Parameter-Anhang",
}

_TONE_ICON = {"up": "▲", "down": "▼", "warn": "◆", "info": "●"}


# ── Bausteine ──────────────────────────────────────────────────────────────


def _empty_state() -> html.Div:
    return panel(
        "Kein Universum geladen",
        [
            html.P(
                "Der Bericht braucht ein importiertes Universum. Bitte zuerst "
                "einen Koyfin-Export hochladen.",
                className="mb-2",
            ),
            dcc.Link("Zum Daten-Import", href="/daten-import"),
        ],
    )


def _portfolio_options() -> list[dict]:
    return [
        {
            "label": f"{p['name']} · {fmt_int(int(p.get('n_positions') or 0))} Pos.",
            "value": int(p["id"]),
        }
        for p in STATE.ms_portfolios
    ]


def _snapshot_options(asof: date) -> list[dict]:
    try:
        snaps = persistence.list_snapshots()
    except Exception:  # noqa: BLE001
        snaps = []
    options = [{"label": "automatisch", "value": AUTO}]
    options += [
        {"label": f"{d.strftime('%d.%m.%Y')} · {fmt_int(n)} Titel", "value": d.isoformat()}
        for d, n in snaps
        if d < asof
    ]
    return options


def _controls() -> html.Div:
    STATE.refresh_portfolios()
    asof = snapshot_date_from_universe(STATE.raw)
    snapshot_options = _snapshot_options(asof)
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Label("Portfolio", className="ms-form-label"),
                            dcc.Dropdown(
                                id="cr-portfolio",
                                options=_portfolio_options(),
                                value=STATE.active_portfolio_id,
                                clearable=False,
                                placeholder="Aktives Portfolio",
                            ),
                        ],
                        style={"minWidth": "240px"},
                    ),
                    html.Div(
                        [
                            html.Label("Vergleich Vorimport", className="ms-form-label"),
                            dcc.Dropdown(id="cr-prev", options=snapshot_options, value=AUTO, clearable=False),
                        ],
                        style={"minWidth": "200px"},
                    ),
                    html.Div(
                        [
                            html.Label("Vergleich ~1 Monat", className="ms-form-label"),
                            dcc.Dropdown(id="cr-month", options=snapshot_options, value=AUTO, clearable=False),
                        ],
                        style={"minWidth": "200px"},
                    ),
                    html.Div(
                        [
                            html.Label("Top-N Kandidaten", className="ms-form-label"),
                            dbc.Input(id="cr-topn", type="number", value=DEFAULT_TOP_N, min=5, max=100, step=5, size="sm"),
                        ],
                        style={"width": "130px"},
                    ),
                    html.Div(
                        [
                            html.Label("Erstellt von", className="ms-form-label"),
                            dbc.Input(id="cr-author", type="text", placeholder="optional", size="sm"),
                        ],
                        style={"minWidth": "180px"},
                    ),
                ],
                className="d-flex gap-3 align-items-end flex-wrap mb-2",
            ),
            html.Div(
                [
                    html.Label("Abschnitte", className="ms-form-label me-2"),
                    dbc.Checklist(
                        id="cr-sections",
                        options=[{"label": label, "value": key} for _f, key, label in SECTION_OPTIONS],
                        value=list(DEFAULT_SECTIONS),
                        inline=True,
                        switch=False,
                    ),
                ],
                className="d-flex gap-2 align-items-center flex-wrap mb-2",
            ),
            html.Div(
                [
                    dbc.Button("Vorschau aktualisieren", id="cr-preview", color="dark", outline=True, size="sm", n_clicks=0),
                    dbc.Button("PDF herunterladen", id="cr-pdf", color="dark", size="sm", n_clicks=0),
                    dbc.Button("Excel-Anhang herunterladen", id="cr-xlsx", color="dark", size="sm", n_clicks=0),
                    html.Span(
                        f"Datenstand {asof.strftime('%d.%m.%Y')} · {fmt_int(len(STATE.scored))} Titel",
                        className="ms-tt-muted small ms-2",
                    ),
                ],
                className="d-flex gap-2 align-items-center flex-wrap mb-3",
            ),
        ]
    )


def _options_from_inputs(
    portfolio_id, prev, month, sections, top_n, author
) -> ReportOptions:
    """Dash-Eingaben → :class:`ReportOptions`."""
    selected = set(sections or [])
    kwargs = {field: (key in selected) for field, key, _l in SECTION_OPTIONS}
    try:
        top = int(top_n) if top_n is not None else DEFAULT_TOP_N
    except (TypeError, ValueError):
        top = DEFAULT_TOP_N
    return ReportOptions(
        portfolio_id=int(portfolio_id) if portfolio_id not in (None, "") else None,
        prev_snapshot=date.fromisoformat(prev) if prev and prev != AUTO else None,
        month_snapshot=date.fromisoformat(month) if month and month != AUTO else None,
        top_n=max(5, min(100, top)),
        prepared_by=str(author or "").strip(),
        **kwargs,
    )


def _build_or_error(options: ReportOptions) -> tuple[dict | None, str]:
    if STATE.scored is None or STATE.scored.empty:
        return None, "Kein Universum geladen — bitte erst CSV importieren."
    try:
        return build_committee_report(STATE, options), ""
    except ValueError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001
        log.exception("Bericht konnte nicht erstellt werden")
        return None, f"Bericht konnte nicht erstellt werden: {exc}"


def _alert(text: str, color: str = "danger") -> dbc.Alert:
    return dbc.Alert(text, color=color, dismissable=True, className="mb-3")


def _kpis(report: dict) -> html.Div:
    meta = report["meta"]
    cols = meta["cols"]
    uni = report["universe"]
    pf = report["portfolio"]
    mp = report["model_portfolio"]
    vs_prev = report["changes"].get("vs_prev") or {}
    good = next(iter(sorted(cols["good_zones"])))
    cells: list[dict] = []
    if uni.get("available"):
        n_kand = uni["zone_counts"].get(good, 0)
        sub = ""
        if vs_prev.get("available"):
            prev = vs_prev["zone_counts_prev"].get(good, 0)
            sub = f"{'+' if n_kand - prev >= 0 else ''}{fmt_int(n_kand - prev)} vs. Vorimport"
        cells.append({"label": "Universum", "value": fmt_int(uni["n"]), "sub": f"{fmt_int(uni['n_eligible'])} eligible"})
        cells.append({"label": "Kandidaten", "value": fmt_int(n_kand), "sub": sub, "tone": "up"})
    if pf.get("available"):
        d = pf.get("delta_weighted_prev")
        cells.append(
            {
                "label": "Portfolio-Score (gew.)",
                "value": fmt_de(pf["weighted_avg_score"], 1),
                "sub": (f"{'+' if d > 0 else ''}{fmt_de(d, 1)} vs. Vorimport" if d is not None else f"Universum Ø {fmt_de(pf['universe_avg_score'], 1)}"),
                "tone": ("up" if d is not None and d > 0.5 else ("down" if d is not None and d < -0.5 else None)),
            }
        )
        cells.append(
            {
                "label": "Handlungsbedarf",
                "value": fmt_int(pf["n_flagged"]),
                "sub": f"{fmt_int(pf['n_filter_fail'])} FILTER · {fmt_int(pf['n_sell_zone'])} VERKAUFEN",
                "tone": "down" if (pf["n_filter_fail"] or pf["n_sell_zone"]) else None,
            }
        )
    if mp.get("available"):
        cells.append(
            {
                "label": "Modellportfolio-Trades",
                "value": fmt_int(mp["n_active_trades"]),
                "sub": f"{fmt_int(mp['n_buy'])} Kauf · {fmt_int(mp['n_sell'])} Verkauf · Turnover {fmt_percent(mp['turnover_oneway'], 0)}",
            }
        )
    comp = meta["comparison"]
    cells.append(
        {
            "label": "Vergleichsbasis",
            "value": comp["prev"].strftime("%d.%m.%Y") if comp.get("prev") else "–",
            "sub": ("~1M " + comp["month"].strftime("%d.%m.%Y")) if comp.get("month") and not comp.get("same_as_prev") else (comp.get("month_note") or comp.get("prev_note") or ""),
        }
    )
    return kpi_band(cells)


def _findings(report: dict) -> html.Div:
    items = [
        html.Li(
            [
                html.Span(_TONE_ICON.get(f["tone"], "●"), className=f"ms-finding-icon is-{f['tone']} me-2"),
                html.Span(f["text"]),
            ],
            className="ms-finding-row",
        )
        for f in report.get("key_findings", [])
    ]
    body = html.Ul(items, className="ms-finding-list") if items else html.Div("Keine Kernaussagen ableitbar.", className="ms-tt-muted")
    return panel("Key Findings", body, meta=f"{len(items)} Aussagen · regelbasiert")


def _sections_overview(report: dict) -> html.Div:
    rows = []
    for key, title in SECTION_TITLES.items():
        sec = report.get(key) or {}
        if key == "changes":
            available = bool((sec.get("vs_prev") or {}).get("available") or (sec.get("vs_month") or {}).get("available"))
            note = sec.get("note") or " · ".join(
                n for n in ((sec.get("vs_prev") or {}).get("note", ""), (sec.get("vs_month") or {}).get("note", "")) if n
            )
        else:
            available = bool(sec.get("available"))
            note = sec.get("note") or ""
        rows.append(
            html.Tr(
                [
                    html.Td(title),
                    html.Td("✓ enthalten" if available else "– nicht enthalten", className="is-up" if available else "ms-tt-muted"),
                    html.Td(note, className="ms-tt-muted small"),
                ]
            )
        )
    table = html.Table(
        [html.Thead(html.Tr([html.Th("Abschnitt"), html.Th("Status"), html.Th("Hinweis")])), html.Tbody(rows)],
        className="ms-table ms-table--basic",
    )
    return panel("Was wird exportiert", table, meta="PDF-Abschnitte · vollständige Tabellen im Excel-Anhang")


def _preview(report: dict) -> list:
    meta = report["meta"]
    header = html.Div(
        [
            html.Strong(f"{REPORT_TITLE} · {meta['portfolio_name']}"),
            html.Span(
                f" · Stand {meta['asof'].strftime('%d.%m.%Y')} · {meta['scoring_version']} · "
                f"Settings-Hash {meta['settings_hash'][:12]}…",
                className="ms-tt-muted",
            ),
        ],
        className="mb-2",
    )
    return [
        header,
        _kpis(report),
        _findings(report),
        _sections_overview(report),
        diagnostics_panel(report.get("diagnostics", []), title="Hinweise des Berichts"),
    ]


# ── Layout ─────────────────────────────────────────────────────────────────


def layout(**_) -> html.Div:
    if STATE.scored is None or STATE.scored.empty:
        return html.Div([page_title(REPORT_TITLE, "Exportierbarer Bericht für die interne Besprechung"), _empty_state()])
    initial, error = _build_or_error(_options_from_inputs(STATE.active_portfolio_id, AUTO, AUTO, DEFAULT_SECTIONS, DEFAULT_TOP_N, ""))
    return html.Div(
        [
            page_title(
                REPORT_TITLE,
                "Alle Erkenntnisse des Modells auf einen Blick: Universum, Veränderungen, "
                "Sektoren, Portfolio mit Scores der Einzeltitel, Modellportfolio, Diagnosen "
                "und Parameter — als PDF und Excel-Anhang.",
            ),
            _controls(),
            html.Div(_alert(error) if error else None, id="cr-status"),
            dcc.Download(id="cr-pdf-download"),
            dcc.Download(id="cr-xlsx-download"),
            dcc.Loading(html.Div(_preview(initial) if initial else None, id="cr-preview-body")),
        ]
    )


# ── Callbacks ──────────────────────────────────────────────────────────────

_CONTROL_STATES = [
    State("cr-portfolio", "value"),
    State("cr-prev", "value"),
    State("cr-month", "value"),
    State("cr-sections", "value"),
    State("cr-topn", "value"),
    State("cr-author", "value"),
]


@callback(
    Output("cr-preview-body", "children"),
    Output("cr-status", "children"),
    Input("cr-preview", "n_clicks"),
    *_CONTROL_STATES,
    prevent_initial_call=True,
    running=[
        (Output("cr-preview", "disabled"), True, False),
        (Output("cr-preview", "children"), "Wird berechnet …", "Vorschau aktualisieren"),
    ],
)
def _render_preview(n_clicks, portfolio_id, prev, month, sections, top_n, author):
    if not n_clicks:
        return no_update, no_update
    report, error = _build_or_error(_options_from_inputs(portfolio_id, prev, month, sections, top_n, author))
    if report is None:
        return no_update, _alert(error)
    return _preview(report), None


@callback(
    Output("cr-pdf-download", "data"),
    Output("cr-status", "children", allow_duplicate=True),
    Input("cr-pdf", "n_clicks"),
    *_CONTROL_STATES,
    prevent_initial_call=True,
    running=[
        (Output("cr-pdf", "disabled"), True, False),
        (Output("cr-pdf", "children"), "Wird erstellt …", "PDF herunterladen"),
    ],
)
def _download_pdf(n_clicks, portfolio_id, prev, month, sections, top_n, author):
    if not n_clicks:
        return no_update, no_update
    report, error = _build_or_error(_options_from_inputs(portfolio_id, prev, month, sections, top_n, author))
    if report is None:
        return no_update, _alert(error)
    from app.core.committee_report_pdf import (
        FactsheetRenderError,
        committee_filename,
        render_committee_fallback_html,
        render_committee_pdf,
    )

    try:
        pdf = render_committee_pdf(report)
    except FactsheetRenderError as exc:
        log.warning("PDF-Renderer nicht verfügbar, HTML-Fallback: %s", exc)
        html_bytes = render_committee_fallback_html(report)
        return (
            dcc.send_bytes(lambda buf: buf.write(html_bytes), filename=committee_filename(report, "html")),
            _alert(f"PDF-Renderer nicht verfügbar ({exc}) — Bericht als HTML-Datei exportiert.", "warning"),
        )
    return dcc.send_bytes(lambda buf: buf.write(pdf), filename=committee_filename(report, "pdf")), None


@callback(
    Output("cr-xlsx-download", "data"),
    Output("cr-status", "children", allow_duplicate=True),
    Input("cr-xlsx", "n_clicks"),
    *_CONTROL_STATES,
    prevent_initial_call=True,
    running=[
        (Output("cr-xlsx", "disabled"), True, False),
        (Output("cr-xlsx", "children"), "Wird erstellt …", "Excel-Anhang herunterladen"),
    ],
)
def _download_xlsx(n_clicks, portfolio_id, prev, month, sections, top_n, author):
    if not n_clicks:
        return no_update, no_update
    report, error = _build_or_error(_options_from_inputs(portfolio_id, prev, month, sections, top_n, author))
    if report is None:
        return no_update, _alert(error)
    try:
        from app.core.committee_report_xlsx import build_committee_xlsx

        data = build_committee_xlsx(report)
    except ImportError as exc:
        return no_update, _alert(f"Excel-Export nicht verfügbar (openpyxl fehlt): {exc}")
    except Exception as exc:  # noqa: BLE001
        log.exception("Excel-Export fehlgeschlagen")
        return no_update, _alert(f"Excel-Export fehlgeschlagen: {exc}")
    from app.core.committee_report_pdf import committee_filename

    return dcc.send_bytes(lambda buf: buf.write(data), filename=committee_filename(report, "xlsx")), None


register_page(__name__, path="/bericht", name="Bericht", layout=layout)
