"""Dash-Einstiegspunkt: Multi-Faktor-Scoring-App (Morningstar-Look)."""

from __future__ import annotations

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import (
    ClientsideFunction,
    Input,
    Output,
    State,
    callback,
    clientside_callback,
    dcc,
    html,
)

from app.core.state import STATE
from app.ui import command_palette_layout, fmt_de, register_plotly_templates


NAV_ORDER = {
    "Dashboard": 0,
    "Einzelanalyse": 1,
    "Agenten-Analyse": 2,
    "Momentum-Monitor": 3,
    "Sektor-Momentum": 4,
    "M&S Portfolio": 5,
    "Factor Timing": 6,
    "Daten-Import": 7,
    "Einstellungen": 8,
    "Perzentil-Hilfe": 9,
    "Anleitung": 10,
}


INDEX_STRING = """<!DOCTYPE html>
<html data-theme="light">
    <head>
        {%metas%}
        <title>{%title%}</title>
        {%favicon%}
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link rel="stylesheet"
              href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;0,700;1,400&display=swap">
        {%css%}
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
"""


def _nav_order(page: dict) -> int:
    return NAV_ORDER.get(page["name"], 99)


def _header() -> html.Header:
    links = [
        dcc.Link(p["name"], href=p["path"], className="ms-nav-link", id=f"ms-nav-{i}")
        for i, p in enumerate(sorted(dash.page_registry.values(), key=_nav_order))
    ]
    return html.Header(
        [
            html.Div(
                [
                    html.Div(className="ms-brand-mark"),
                    html.Span("M&S · Multi-Faktor"),
                ],
                className="ms-brand",
            ),
            html.Nav(links, className="ms-nav", id="ms-nav"),
            html.Div(
                [
                    html.Div(id="ms-agent-status"),
                    html.Div(id="ms-data-status", className="ms-data-status"),
                    html.Button(
                        [
                            html.Span("☀", className="sun", **{"aria-hidden": "true"}),
                            html.Span("☾", className="moon", **{"aria-hidden": "true"}),
                        ],
                        id="ms-theme-btn",
                        className="ms-theme-toggle",
                        title="Theme umschalten",
                        n_clicks=0,
                        **{
                            "aria-label": "Theme umschalten",
                            "aria-pressed": "false",
                        },
                    ),
                ],
                className="ms-header-tools",
            ),
        ],
        className="ms-header",
    )


def create_app() -> dash.Dash:
    register_plotly_templates()
    STATE.load_from_db()

    app = dash.Dash(
        __name__,
        use_pages=True,
        pages_folder="",
        external_stylesheets=[dbc.themes.BOOTSTRAP, dbc.icons.BOOTSTRAP],
        suppress_callback_exceptions=True,
        title="M&S Multi-Faktor-Modell",
        update_title=None,
    )
    app.index_string = INDEX_STRING

    # Seitenmodule erst nach App-Instanziierung importieren.
    from app.pages import (  # noqa: F401
        agenten_analyse,
        anleitung,
        dashboard,
        daten_import,
        einstellungen,
        einzelanalyse,
        factor_timing,
        perzentil_hilfe,
        portfolios,
        sektor_momentum,
        sma_signale,
    )

    app.layout = html.Div(
        [
            dcc.Location(id="ms-location"),
            dcc.Store(id="ms-theme-store", storage_type="local"),
            dcc.Store(id="ms-agent-fp"),
            dcc.Interval(id="ms-agent-poll", interval=4000),
            _header(),
            html.Main(dash.page_container, className="ms-page"),
            command_palette_layout(),
        ],
        className="ms-app",
    )

    # Aktive Nav-Verlinkung per Pathname setzen.
    clientside_callback(
        """
        function(pathname) {
            var links = document.querySelectorAll('.ms-nav-link');
            links.forEach(function (a) {
                var href = a.getAttribute('href') || '';
                var isActive = (pathname === href) ||
                               (href !== '/' && pathname && pathname.indexOf(href) === 0);
                if (href === '/' && pathname === '/') isActive = true;
                else if (href === '/' && pathname !== '/') isActive = false;
                a.classList.toggle('active', isActive);
            });
            return window.dash_clientside.no_update;
        }
        """,
        Output("ms-nav", "data-active"),
        Input("ms-location", "pathname"),
    )

    # Theme-Toggle
    clientside_callback(
        ClientsideFunction(namespace="ms", function_name="toggleTheme"),
        Output("ms-theme-store", "data"),
        Input("ms-theme-btn", "n_clicks"),
        prevent_initial_call=True,
    )

    # Command-Palette: Datenquelle je Seitenwechsel aus STATE.scored füllen.
    @callback(
        Output("ms-cmdk-data", "data"),
        Input("ms-location", "pathname"),
    )
    def _cmdk_items(_path: str | None):
        df = STATE.scored
        if df.empty:
            return []
        cols = [c for c in ("ticker", "name", "sector") if c in df.columns]
        return df[cols].fillna("").to_dict("records")

    # Clientside-Brücke: Store-Inhalt in ``window.msCmdk.items`` spiegeln.
    clientside_callback(
        ClientsideFunction(namespace="ms", function_name="cmdkSyncData"),
        Output("ms-cmdk", "data-ready"),
        Input("ms-cmdk-data", "data"),
    )

    # Agenten-Status-Chip in der Kopfzeile: auf jeder Seite sichtbar, zeigt
    # laufende Tiefenanalysen samt aktuell arbeitendem Agenten (Klick führt
    # zur Agenten-Analyse-Seite). Fingerprint-Gate verhindert DOM-Churn.
    @callback(
        Output("ms-agent-status", "children"),
        Output("ms-agent-fp", "data"),
        Input("ms-agent-poll", "n_intervals"),
        Input("ms-location", "pathname"),
        State("ms-agent-fp", "data"),
    )
    def _agent_status_chip(_n, _path, prev_fp):
        from dash import no_update

        from app.core import agents_client

        jobs = agents_client.list_jobs()
        running = {t: j for t, j in jobs.items() if j.get("status") == "running"}

        if running:
            ticker, job = next(iter(running.items()))
            agent = agents_client.current_agent(job)
            label = f"{ticker} · {agent}" if agent else f"{ticker} · Analyse läuft"
            fp = f"run|{ticker}|{agent}"
            if fp == prev_fp:
                return no_update, no_update
            return (
                dcc.Link(
                    [
                        html.Span(className="ms-agent-chip-dot"),
                        html.Span(label, className="ms-agent-chip-label"),
                    ],
                    href="/agenten-analyse",
                    className="ms-agent-chip is-running",
                    title="Agenten-Tiefenanalyse läuft — Klick für Details",
                ),
                fp,
            )

        # Nichts läuft: jüngsten Abschluss der Sitzung dezent anzeigen.
        if jobs:
            ticker, job = next(iter(jobs.items()))
            ok = job.get("status") == "done"
            icon, tone = ("✓", "is-done") if ok else ("⚠", "is-error")
            fp = f"idle|{ticker}|{job.get('status')}"
            if fp == prev_fp:
                return no_update, no_update
            return (
                dcc.Link(
                    [
                        html.Span(icon, className="ms-agent-chip-icon"),
                        html.Span(ticker, className="ms-agent-chip-label"),
                    ],
                    href="/agenten-analyse",
                    className=f"ms-agent-chip {tone}",
                    title=(
                        "Letzte Tiefenanalyse abgeschlossen — Klick für Details"
                        if ok
                        else "Letzte Tiefenanalyse fehlgeschlagen — Klick für Details"
                    ),
                ),
                fp,
            )

        if prev_fp == "none":
            return no_update, no_update
        return html.Div(), "none"

    # Datenstatus-Chip: wird bei jedem Seitenwechsel aktualisiert.
    @callback(
        Output("ms-data-status", "children"),
        Input("ms-location", "pathname"),
    )
    def _data_status(_path: str | None):
        df = STATE.scored
        if df.empty:
            return html.Span("Keine Daten geladen", className="ms-data-status-empty")
        stand = ""
        if "export_date" in df.columns:
            series = df["export_date"].dropna()
            if not series.empty:
                raw = series.iloc[0]
                try:
                    stand = pd.to_datetime(raw).strftime("%d.%m.%Y")
                except (ValueError, TypeError):
                    stand = str(raw)
        return html.Span(
            [
                html.Span("Stand ", className="ms-data-status-label"),
                html.Span(stand or "—", className="ms-data-status-value"),
                html.Span(" · ", className="ms-data-status-sep"),
                html.Span(fmt_de(len(df), 0), className="ms-data-status-value"),
                html.Span(" Aktien", className="ms-data-status-label"),
            ]
        )

    return app


if __name__ == "__main__":
    create_app().run(debug=True, host="127.0.0.1", port=8050)
