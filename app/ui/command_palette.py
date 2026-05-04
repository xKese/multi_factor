"""Globale Ticker-Suche (Cmd+K / `/`).

Rendert das versteckte Overlay-Markup und einen ``dcc.Store`` mit den
durchsuchbaren Ticker-Datensätzen. Keystroke-Handling, Filtern und Navigation
passieren clientseitig in ``assets/ms-theme.js``.
"""

from __future__ import annotations

from dash import dcc, html


def command_palette_layout() -> html.Div:
    return html.Div(
        [
            dcc.Store(id="ms-cmdk-data", data=[]),
            html.Div(
                id="ms-cmdk",
                className="ms-cmdk",
                role="dialog",
                **{"aria-hidden": "true", "aria-label": "Ticker-Suche"},
                children=[
                    html.Div(className="ms-cmdk-backdrop"),
                    html.Div(
                        className="ms-cmdk-dialog",
                        children=[
                            html.Div(
                                className="ms-cmdk-header",
                                children=[
                                    dcc.Input(
                                        id="ms-cmdk-input",
                                        type="text",
                                        placeholder="Ticker oder Name …",
                                        autoComplete="off",
                                        spellCheck=False,
                                        className="ms-cmdk-input",
                                        debounce=False,
                                    ),
                                    html.Kbd("Esc", className="ms-cmdk-kbd"),
                                ],
                            ),
                            html.Div(
                                id="ms-cmdk-results",
                                className="ms-cmdk-results",
                                role="listbox",
                            ),
                            html.Div(
                                className="ms-cmdk-footer",
                                children=[
                                    html.Span(
                                        [
                                            html.Kbd("↑"),
                                            html.Kbd("↓"),
                                            " navigieren",
                                        ]
                                    ),
                                    html.Span(
                                        [html.Kbd("↵"), " öffnen"]
                                    ),
                                    html.Span(
                                        [
                                            html.Kbd("/"),
                                            " oder ",
                                            html.Kbd("Ctrl"),
                                            html.Kbd("K"),
                                        ]
                                    ),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
        ]
    )
