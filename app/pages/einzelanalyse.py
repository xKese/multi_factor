"""Einzelanalyse je Ticker im Morningstar-Quote-Layout."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html, register_page
from dash.exceptions import PreventUpdate

from app.core.peers import compute_peers
from app.core.state import STATE
from app.pages.common import page_title
from app.ui import (
    MS_LIGHT,
    factor_breakdown,
    fmt_de,
    fmt_indicator,
    fmt_int,
    fmt_market_cap,
    fmt_percent,
    ms_badge,
    quote_header,
    section_header,
)


INDICATOR_GROUPS = {
    "Value": [
        ("P/B", "pb"),
        ("P/E", "pe"),
        ("P/FCF", "pfcf"),
        ("EV/EBITDA", "ev_ebitda"),
        ("P/S", "ps"),
        ("PEG", "peg"),
        ("Dividend Yield", "div_yield"),
    ],
    "Quality": [
        ("ROE", "roe"),
        ("ROIC", "roic"),
        ("ROA", "roa"),
        ("Gross Margin", "gross_margin"),
        ("Operating Margin", "op_margin"),
        ("Debt/Equity", "debt_equity"),
        ("Interest Coverage", "int_coverage"),
        ("Current Ratio", "current_ratio"),
        ("Piotroski", "piotroski"),
        ("Altman Z", "altman_z"),
    ],
    "Growth": [
        ("Revenue CAGR 3Y", "rev_cagr_3y"),
        ("EPS CAGR 3Y", "eps_cagr_3y"),
        ("FCF CAGR 3Y", "fcf_cagr_3y"),
        ("Forward EPS Growth", "fwd_eps_growth"),
    ],
    "Momentum": [
        ("Return 1M", "ret_1m"),
        ("Return 3M", "ret_3m"),
        ("Return 6M", "ret_6m"),
        ("Return 12M", "ret_12m"),
        ("EPS Revisions 3M", "eps_revisions_3m"),
    ],
    "Low Volatility": [
        ("Beta", "beta"),
        ("Volatilität 1Y", "volatility_1y"),
        ("52W Range %", "range_52w"),
    ],
}


def _kv_table(pairs: list[tuple[str, str]], row: pd.Series) -> dbc.Table:
    rows = []
    for label, col in pairs:
        display = fmt_indicator(col, row.get(col))
        rows.append(
            html.Tr(
                [
                    html.Td(label, className="ms-muted"),
                    html.Td(display, className="ms-tabular text-end"),
                ]
            )
        )
    return dbc.Table(
        [html.Tbody(rows)],
        bordered=False,
        striped=True,
        hover=True,
        size="sm",
        className="mb-0",
    )


def layout(ticker: str = "", **_) -> html.Div:
    options = []
    if not STATE.scored.empty:
        options = [
            {"label": f"{t} – {n}", "value": t}
            for t, n in STATE.scored[["ticker", "name"]].head(2000).itertuples(index=False)
        ]

    return html.Div(
        [
            page_title("Einzelanalyse", "Wähle einen Titel für das vollständige Profil."),
            dbc.Row(
                [
                    dbc.Col(
                        dcc.Dropdown(
                            id="ea-ticker",
                            options=options,
                            value=ticker or (options[0]["value"] if options else None),
                            placeholder="Ticker wählen …",
                            searchable=True,
                        ),
                        md=5,
                    ),
                ],
                className="mb-3",
            ),
            html.Div(id="ea-content"),
        ]
    )


_RUECKBLICK_WINDOWS = (
    ("ret_12m", "12M"),
    ("ret_6m", "6M"),
    ("ret_3m", "3M"),
    ("ret_1m", "1M"),
)


def _rueckblick(r: pd.Series) -> html.Div | None:
    """Kompakte Visualisierung der vier Rückblicks-Returns (1M/3M/6M/12M).

    Keine Sparkline: mangels monatlicher Schlusskurse im Koyfin-Export sind
    das vier disjunkte Zeitfenster. Gepunktete Verbindungslinie + expliziter
    Label machen klar, dass zwischen den Punkten nicht interpoliert ist.
    """

    points: list[tuple[str, float]] = []
    for col, label in _RUECKBLICK_WINDOWS:
        v = r.get(col)
        if pd.notna(v):
            points.append((label, float(v) * 100.0))
    if not points:
        return None

    ret12 = r.get("ret_12m")
    if pd.notna(ret12) and ret12 >= 0:
        color = "#1B7F3A"
    elif pd.notna(ret12) and ret12 < 0:
        color = "#C2281E"
    else:
        color = "#0B3D91"

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    fig = go.Figure()
    fig.add_shape(
        type="line",
        xref="paper",
        yref="y",
        x0=0,
        x1=1,
        y0=0,
        y1=0,
        line=dict(color="#CFCFCB", width=1, dash="dot"),
    )
    fig.add_trace(
        go.Scatter(
            x=xs,
            y=ys,
            mode="lines+markers",
            line=dict(color=color, width=1.4, dash="dot"),
            marker=dict(size=6, color=color),
            hovertemplate="Return %{x}: %{y:.1f}%<extra></extra>",
            showlegend=False,
        )
    )
    fig.update_layout(
        template=MS_LIGHT,
        height=48,
        width=160,
        margin=dict(l=4, r=4, t=4, b=4),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False, zeroline=False),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )

    return html.Div(
        [
            html.Span(
                "Rückblicksfenster · 12M · 6M · 3M · 1M",
                className="ms-rueckblick-label",
            ),
            dcc.Graph(
                figure=fig,
                config={"displayModeBar": False, "scrollZoom": False},
                className="ms-rueckblick-graph",
            ),
        ],
        className="ms-rueckblick",
    )


def _filter_badges(r: pd.Series) -> html.Div:
    badges: list = []

    piotr = r.get("piotroski")
    if pd.notna(piotr):
        ok = piotr >= STATE.settings.min_piotroski
        badges.append(
            ms_badge("Piotroski", f"{fmt_int(piotr)} / 9", tone="up" if ok else "down")
        )
    else:
        badges.append(ms_badge("Piotroski", "-"))

    altman = r.get("altman_z")
    if pd.notna(altman):
        ok = altman >= STATE.settings.min_altman_z
        badges.append(
            ms_badge("Altman Z", fmt_de(altman, 2), tone="up" if ok else "down")
        )
    else:
        badges.append(ms_badge("Altman Z", "-"))

    sma = r.get("sma_signal") or "-"
    sma_tone = None
    if "GOLDEN" in str(sma):
        sma_tone = "up"
    elif "DEATH" in str(sma):
        sma_tone = "down"
    elif "<" in str(sma):
        sma_tone = "warn"
    elif ">" in str(sma):
        sma_tone = "info"
    badges.append(ms_badge("SMA-Signal", str(sma), tone=sma_tone))

    rec = r.get("recommendation") or "-"
    rec_tone = {
        "STRONG BUY": "up",
        "BUY": "up",
        "HOLD": "warn",
        "SELL": "down",
    }.get(rec)
    badges.append(ms_badge("Empfehlung", str(rec), tone=rec_tone))

    filt = r.get("filter_ok") or "-"
    badges.append(
        ms_badge(
            "Filter",
            "bestanden" if filt == "JA" else "nicht bestanden",
            tone="up" if filt == "JA" else "down",
        )
    )

    return html.Div(badges, className="ms-badge-row")


def _peer_card(peer: pd.Series) -> dcc.Link:
    """Eine klickbare Comparable-Karte (Link zur Einzelanalyse des Peers)."""

    ticker = str(peer["ticker"])
    name = str(peer.get("name") or "")

    meta_parts: list[str] = []
    if peer.get("industry"):
        meta_parts.append(str(peer["industry"]))
    elif peer.get("sector"):
        meta_parts.append(str(peer["sector"]))

    score = peer.get("total_score")
    score_display = fmt_de(score, 1) if pd.notna(score) else "-"

    ret_12m = peer.get("ret_12m")
    if pd.notna(ret_12m):
        ret_tone = "up" if float(ret_12m) >= 0 else "down"
        ret_badge = ms_badge("12M", fmt_percent(ret_12m), tone=ret_tone)
    else:
        ret_badge = ms_badge("12M", "-")

    factors = {
        "Value": peer.get("value_score"),
        "Quality": peer.get("quality_score"),
        "Growth": peer.get("growth_score"),
        "Momentum": peer.get("momentum_score"),
        "Low Vol": peer.get("lowvol_score"),
    }

    card = dbc.Card(
        dbc.CardBody(
            [
                html.Div(
                    [
                        html.Span(ticker, className="ms-peer-ticker"),
                        html.Span(score_display, className="ms-peer-score"),
                    ],
                    className="ms-peer-head",
                ),
                html.Div(name, className="ms-peer-name"),
                html.Div(" · ".join(meta_parts) or " ", className="ms-peer-meta"),
                html.Div(
                    [
                        ms_badge("Score", score_display),
                        ret_badge,
                    ],
                    className="ms-peer-badges",
                ),
                factor_breakdown(factors),
            ]
        ),
        className="ms-peer-card h-100",
    )

    return dcc.Link(
        card,
        href=f"/einzelanalyse?ticker={ticker}",
        className="ms-peer-link",
    )


def _comparables_controls() -> html.Div:
    """Toggle: ähnliches Profil (Distanz) vs. Top-Score in der Industrie."""
    return html.Div(
        dbc.RadioItems(
            id="ea-comparables-mode",
            options=[
                {"label": "Ähnliches Profil", "value": "similar"},
                {"label": "Top-Score in Industrie", "value": "top_score"},
            ],
            value="similar",
            inline=True,
            class_name="btn-group",
            input_class_name="btn-check",
            label_class_name="btn btn-sm btn-outline-secondary",
        ),
        className="mb-3",
    )


@callback(
    Output("ea-ticker", "value"),
    Input("ms-location", "search"),
    prevent_initial_call=True,
)
def _sync_ticker_from_url(search: str | None):
    """Ticker-Dropdown bei URL-Query-Wechsel nachziehen (Deep-Link).

    Beim Klick auf eine Peer-Karte wechselt ``dcc.Location.search`` clientseitig
    auf ``?ticker=XYZ``. Da der Pfadname gleich bleibt, ruft Dash das Layout
    nicht erneut auf — wir aktualisieren darum die Dropdown-Auswahl manuell,
    was den bestehenden ``_render``-Callback auslöst.
    """
    from urllib.parse import parse_qs

    if not search:
        raise PreventUpdate
    qs = parse_qs(search.lstrip("?"))
    tickers = qs.get("ticker")
    if not tickers or not tickers[0]:
        raise PreventUpdate
    return tickers[0]


@callback(Output("ea-content", "children"), Input("ea-ticker", "value"))
def _render(ticker: str | None):
    if not ticker or STATE.scored.empty:
        return dbc.Alert("Keine Daten verfügbar.", color="info")

    row = STATE.scored.loc[STATE.scored["ticker"] == ticker]
    if row.empty:
        return dbc.Alert(f"Ticker {ticker} nicht gefunden.", color="warning")
    r = row.iloc[0]

    score = r.get("total_score")
    score_display = fmt_de(score, 1) if pd.notna(score) else "-"
    classification = r.get("classification") or ""

    meta: list[str] = []
    if r.get("sector"):
        meta.append(str(r["sector"]))
    if r.get("industry"):
        meta.append(str(r["industry"]))
    if r.get("region"):
        meta.append(str(r["region"]))
    if pd.notna(r.get("market_cap")):
        meta.append(f"Market Cap {fmt_market_cap(r['market_cap'])}")

    header = quote_header(
        ticker=str(r["ticker"]),
        name=str(r.get("name") or ""),
        meta=meta,
        score_value=score_display,
        score_label=classification or "Gesamt-Score",
    )

    factors = {
        "Value": r.get("value_score"),
        "Quality": r.get("quality_score"),
        "Growth": r.get("growth_score"),
        "Momentum": r.get("momentum_score"),
        "Low Vol": r.get("lowvol_score"),
    }

    radar = go.Figure(
        go.Scatterpolar(
            r=[0 if pd.isna(v) else float(v) for v in factors.values()],
            theta=list(factors.keys()),
            fill="toself",
            name=str(r["ticker"]),
            line=dict(color="#0B3D91"),
            fillcolor="rgba(11,61,145,0.18)",
        )
    )
    radar.update_layout(
        template=MS_LIGHT,
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=False,
        height=320,
        margin=dict(l=16, r=16, t=8, b=8),
    )

    factor_card = dbc.Card(
        [
            dbc.CardHeader("Faktor-Breakdown"),
            dbc.CardBody(factor_breakdown(factors)),
        ]
    )

    cards = []
    for factor, pairs in INDICATOR_GROUPS.items():
        cards.append(
            dbc.Col(
                dbc.Card(
                    [
                        dbc.CardHeader(factor),
                        dbc.CardBody(_kv_table(pairs, r)),
                    ],
                    className="mb-3 h-100",
                ),
                md=6,
            )
        )

    sections: list = [header]
    rueckblick = _rueckblick(r)
    if rueckblick is not None:
        sections.append(rueckblick)
    sections.append(_filter_badges(r))

    comparables_block = [
        section_header("Comparables"),
        _comparables_controls(),
        html.Div(id="ea-comparables"),
    ]

    return html.Div(
        sections + [
            section_header("Faktor-Profil"),
            dbc.Row(
                [
                    dbc.Col(
                        dcc.Graph(figure=radar, config={"displayModeBar": False}),
                        md=6,
                    ),
                    dbc.Col(factor_card, md=6),
                ]
            ),
            section_header("Kennzahlen"),
            dbc.Row(cards),
        ] + comparables_block
    )


@callback(
    Output("ea-comparables", "children"),
    Input("ea-ticker", "value"),
    Input("ea-comparables-mode", "value"),
)
def _render_comparables(ticker: str | None, mode: str | None):
    empty = dbc.Alert("Keine Comparables verfügbar.", color="light", className="mb-0")
    if not ticker or STATE.scored.empty:
        return empty
    peers = compute_peers(STATE.scored, ticker, n=6, mode=mode or "similar")
    if peers.empty:
        return empty
    return dbc.Row(
        [
            dbc.Col(_peer_card(peer), xs=12, sm=6, md=4, lg=2, className="mb-3")
            for _, peer in peers.iterrows()
        ],
        className="ms-peer-row",
    )


register_page(__name__, path="/einzelanalyse", name="Einzelanalyse", layout=layout)
