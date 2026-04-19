"""Dash-Einstiegspunkt: Multi-Faktor-Scoring-App."""

from __future__ import annotations

import dash
import dash_bootstrap_components as dbc
from dash import html


NAV_ORDER = {
    "Dashboard": 0,
    "Einzelanalyse": 1,
    "SMA-Signale": 2,
    "Portfolios": 3,
    "Factor Timing": 4,
    "Daten-Import": 5,
    "Einstellungen": 6,
    "Perzentil-Hilfe": 7,
    "Anleitung": 8,
}


def _nav_order(page: dict) -> int:
    return NAV_ORDER.get(page["name"], 99)


def create_app() -> dash.Dash:
    app = dash.Dash(
        __name__,
        use_pages=True,
        pages_folder="",
        external_stylesheets=[dbc.themes.FLATLY, dbc.icons.BOOTSTRAP],
        suppress_callback_exceptions=True,
        title="M&S Multi-Faktor-Modell",
    )

    # Seitenmodule erst nach App-Instanziierung importieren, damit
    # ``dash.register_page`` intern validiert werden kann.
    from app.pages import (  # noqa: F401  (seiteneffektvolle Imports)
        anleitung,
        dashboard,
        daten_import,
        einstellungen,
        einzelanalyse,
        factor_timing,
        perzentil_hilfe,
        portfolios,
        sma_signale,
    )

    navbar = dbc.NavbarSimple(
        brand="M&S Multi-Faktor-Modell",
        color="dark",
        dark=True,
        children=[
            dbc.NavItem(dbc.NavLink(p["name"], href=p["path"]))
            for p in sorted(dash.page_registry.values(), key=_nav_order)
        ],
    )

    app.layout = html.Div([navbar, dash.page_container])
    return app


if __name__ == "__main__":
    create_app().run(debug=True, host="127.0.0.1", port=8050)
