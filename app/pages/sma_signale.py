"""SMA-Signal-Monitor (entspricht Sheet ``SMA_Signale``).

Re-Design analog Dashboard/Sektor-Momentum (Claude-Design-Handoff):
- Hero mit Marktbreite-Delta und den vier Signal-Zählern
- Signal-Verteilung als gestapelter Balken
- Sektor-Breite als CSS-Balken je Sektor (bearish zuerst)
- Signal-Tabelle & "Nahe am Kreuz"-Watchlist als ms-toptable mit Ticker-Links
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dash import (
    ALL,
    Input,
    Output,
    State,
    callback,
    ctx,
    dcc,
    html,
    no_update,
    register_page,
)

from app.core.state import STATE
from app.pages.common import format_scored
from app.ui import fmt_de
from app.ui.formatters import fmt_int


PRIORITY = {
    "⚠ DEATH CROSS": 0,
    "▼ Kurs < SMA-200": 1,
    "● Kurs > SMA-200": 2,
    "✓ GOLDEN CROSS": 3,
}

BULLISH_SIGNALS = {"✓ GOLDEN CROSS", "● Kurs > SMA-200"}
BEARISH_SIGNALS = {"⚠ DEATH CROSS", "▼ Kurs < SMA-200"}

# Signal → Score-Pill-Ton (ms-score-pill is-*) bzw. Balken-Segment (ms-rec-seg is-*).
SIGNAL_CLASS = {
    "⚠ DEATH CROSS": "f",
    "▼ Kurs < SMA-200": "c",
    "● Kurs > SMA-200": "bp",
    "✓ GOLDEN CROSS": "a",
}

SIGNAL_SEG = {
    "⚠ DEATH CROSS": "sell",
    "▼ Kurs < SMA-200": "hold",
    "● Kurs > SMA-200": "buy",
    "✓ GOLDEN CROSS": "strong",
}

SIGNAL_SHORT = {
    "⚠ DEATH CROSS": "Death Cross",
    "▼ Kurs < SMA-200": "Kurs < SMA-200",
    "● Kurs > SMA-200": "Kurs > SMA-200",
    "✓ GOLDEN CROSS": "Golden Cross",
}

SIGNAL_OPTIONS = [
    {"label": "Alle", "value": "ALL"},
    {"label": "⚠ Death", "value": "⚠ DEATH CROSS"},
    {"label": "▼ < SMA-200", "value": "▼ Kurs < SMA-200"},
    {"label": "● > SMA-200", "value": "● Kurs > SMA-200"},
    {"label": "✓ Golden", "value": "✓ GOLDEN CROSS"},
]

PORTFOLIO_OPTIONS = [
    {"label": "Gesamt", "value": "all"},
    {"label": "M&S", "value": "ms"},
    {"label": "Mein", "value": "my"},
]

SIGNAL_COLS = [
    "ticker",
    "name",
    "sector",
    "total_score",
    "filter_ok",
    "recommendation",
    "sma_signal",
    "last_price",
    "sma_50",
    "sma_200",
    "sma_50_distance",
    "sma_200_distance",
]

WATCH_THRESHOLD = 0.03  # 3 %: |SMA-50 − SMA-200| / SMA-200
WATCH_TOP_N = 20
WATCH_COLS = [
    "ticker",
    "name",
    "sector",
    "direction",
    "last_price",
    "sma_50",
    "sma_200",
    "sma_gap",
    "total_score",
    "recommendation",
]

PAGE_SIZE = 50

_REC_CLASS = {
    "STRONG BUY": "strong",
    "BUY": "buy",
    "HOLD": "hold",
    "SELL": "sell",
    "Filter nicht bestanden": "fail",
}


# ── Score → Klassifikations-Mapping (siehe data.js classOf) ────────────────

def _class_of(score: float) -> dict:
    if score is None or pd.isna(score):
        return {"code": "–", "label": "Keine Daten", "cls": "f"}
    if score >= 80:
        return {"code": "A", "label": "Exzellent", "cls": "a"}
    if score >= 70:
        return {"code": "B+", "label": "Sehr Gut", "cls": "bp"}
    if score >= 60:
        return {"code": "B", "label": "Gut", "cls": "b"}
    if score >= 50:
        return {"code": "C", "label": "Durchschnitt", "cls": "c"}
    if score >= 40:
        return {"code": "D", "label": "Unterdurch.", "cls": "d"}
    return {"code": "F", "label": "Schwach", "cls": "f"}


# ── Format-Hilfen ──────────────────────────────────────────────────────────

def _fmt_pp(value: float, decimals: int = 1) -> str:
    """Prozent-Punkt-Wert (z. B. 5.2 → \"+5,2 %\") mit Vorzeichen."""
    if value is None or pd.isna(value):
        return "–"
    sign = "+" if value > 0 else ("−" if value < 0 else "")
    return f"{sign}{fmt_de(abs(float(value)), decimals)} %"


def _fmt_num(value, decimals: int = 2) -> str:
    if value is None or pd.isna(value):
        return "–"
    return fmt_de(float(value), decimals)


def _stand_str(df: pd.DataFrame) -> str:
    if "export_date" not in df.columns:
        return "—"
    series = df["export_date"].dropna()
    if series.empty:
        return "—"
    raw = series.iloc[0]
    try:
        return pd.to_datetime(raw).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return str(raw)


# ── Daten-Logik (unverändert zum Vorgänger) ────────────────────────────────

def _apply_portfolio_lens(df: pd.DataFrame, lens: str) -> pd.DataFrame:
    if lens == "ms":
        return df[df["ticker"].isin(STATE.ms_portfolio)]
    if lens == "my":
        return df[df["ticker"].isin(STATE.my_portfolio)]
    return df


def _build_signals(df: pd.DataFrame, signal: str, lens: str) -> pd.DataFrame:
    mask = df["sma_signal"].isin(PRIORITY.keys())
    if signal != "ALL":
        mask &= df["sma_signal"] == signal
    filtered = _apply_portfolio_lens(df.loc[mask], lens)
    signals = format_scored(filtered).copy()
    signals["priority"] = signals["sma_signal"].map(PRIORITY)
    signals = signals.sort_values(
        ["priority", "total_score"], ascending=[True, False]
    )
    cols = [c for c in SIGNAL_COLS if c in signals.columns]
    return signals[cols]


def _build_watchlist(df: pd.DataFrame, lens: str) -> pd.DataFrame:
    """Titel nahe an einem SMA-50 / SMA-200 Crossover."""
    filtered = _apply_portfolio_lens(df, lens)
    has_sma = (
        filtered["sma_50"].notna()
        & filtered["sma_200"].notna()
        & (filtered["sma_200"] > 0)
    )
    gap = (filtered["sma_50"] - filtered["sma_200"]) / filtered["sma_200"]
    mask = has_sma & (gap.abs() < WATCH_THRESHOLD)
    candidates = filtered.loc[mask].copy()
    if candidates.empty:
        return candidates
    raw_gap = (
        (candidates["sma_50"] - candidates["sma_200"]) / candidates["sma_200"]
    )
    candidates["sma_gap"] = raw_gap
    candidates["direction"] = np.where(
        raw_gap >= 0, "↑ Golden voraus", "↓ Death voraus"
    )
    candidates["abs_gap"] = raw_gap.abs()
    candidates = candidates.sort_values("abs_gap").head(WATCH_TOP_N)
    formatted = format_scored(candidates)
    cols = [c for c in WATCH_COLS if c in formatted.columns]
    return formatted[cols]


def _signal_counts(df: pd.DataFrame) -> dict[str, int]:
    return {sig: int((df["sma_signal"] == sig).sum()) for sig in PRIORITY}


# ── Empty-State ────────────────────────────────────────────────────────────

def _empty_state() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Signal-Monitor", className="ms-empty-eyebrow"),
                    html.H2("Noch keine Daten geladen", className="ms-empty-title"),
                    html.P(
                        "Lade einen Koyfin-CSV-Export hoch, um Golden/Death "
                        "Crosses, Trendlage und Watchlist zu befüllen.",
                        className="ms-empty-sub",
                    ),
                    html.Div(
                        dcc.Link(
                            "CSV hochladen",
                            href="/daten-import",
                            className="btn btn-primary ms-empty-cta",
                        ),
                        className="ms-empty-actions",
                    ),
                ],
                className="ms-empty-card",
            )
        ],
        className="ms-empty-wrap",
    )


# ── Bausteine ──────────────────────────────────────────────────────────────

def _hero(df: pd.DataFrame) -> html.Div:
    counts = _signal_counts(df)
    n_bullish = int(df["sma_signal"].isin(BULLISH_SIGNALS).sum())
    n_bearish = int(df["sma_signal"].isin(BEARISH_SIGNALS).sum())
    delta = n_bullish - n_bearish
    sign = "+" if delta >= 0 else "−"
    stand = _stand_str(df)

    meta: list = []
    for i, (sig, n) in enumerate(counts.items()):
        if i:
            meta.append(html.Span("·", className="ms-sep"))
        meta.append(html.Span([html.Strong(fmt_int(n)), f" {SIGNAL_SHORT[sig]}"]))

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        f"Signal-Monitor · Stand {stand}",
                        className="ms-hero-eyebrow",
                    ),
                    html.H1("SMA-Signal-Monitor", className="ms-hero-title"),
                    html.P(
                        "Golden & Death Crosses, Trendlage zur 200-Tage-Linie "
                        "und bevorstehende Kreuzungen – auf einen Blick.",
                        className="ms-hero-subhead",
                    ),
                    html.Div(meta, className="ms-hero-meta"),
                ]
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(f"Δ{sign}{fmt_int(abs(delta))}"),
                            html.Span(" Titel", className="ms-score-suf"),
                        ],
                        className="ms-score-num",
                    ),
                    html.Div(
                        "Marktbreite bullish − bearish",
                        className="ms-score-class",
                    ),
                    html.Div(
                        [
                            html.Span(["Bullish ", html.Strong(fmt_int(n_bullish))]),
                            html.Span(["Bearish ", html.Strong(fmt_int(n_bearish))]),
                        ],
                        className="ms-score-ctx",
                    ),
                ],
                className="ms-hero-score",
            ),
        ],
        className="ms-hero",
    )


def _signal_bar(counts: dict[str, int]) -> html.Div:
    total = sum(counts.values())

    def _seg(sig: str) -> html.Div:
        v = counts[sig]
        width = (v / total) * 100 if total else 0.0
        text = fmt_int(v) if width > 8 else ""
        return html.Div(
            text,
            className=f"ms-rec-seg is-{SIGNAL_SEG[sig]}",
            style={"width": f"{width}%"},
        )

    return html.Div([_seg(sig) for sig in PRIORITY], className="ms-rec-bar")


def _distribution_card(df: pd.DataFrame) -> html.Div:
    counts = _signal_counts(df)
    total = sum(counts.values())
    legend = [
        html.Span(
            [
                html.Span(className="ms-rl-sw"),
                f"{SIGNAL_SHORT[sig]} ",
                html.Strong(fmt_int(counts[sig])),
            ],
            className=f"ms-rl-it is-{SIGNAL_SEG[sig]}",
        )
        for sig in PRIORITY
    ]
    return html.Div(
        [
            html.H3(
                [
                    "Signal-Verteilung ",
                    html.Span(
                        f"{fmt_int(total)} Titel mit SMA-Signal · bearish → bullish",
                        className="ms-card-h-meta",
                    ),
                ],
                className="ms-card-h",
            ),
            _signal_bar(counts),
            html.Div(legend, className="ms-rec-legend"),
        ],
        className="ms-card",
        style={"marginTop": "16px"},
    )


def _sector_card(df: pd.DataFrame, lens: str) -> html.Div:
    view = _apply_portfolio_lens(df, lens)
    view = view[view["sma_signal"].isin(PRIORITY)]
    if view.empty or "sector" not in view.columns:
        body: list = [
            html.Div(
                "Keine Sektor-Daten für diese Auswahl.",
                className="ms-empty-eyebrow",
                style={"padding": "12px 0"},
            )
        ]
    else:
        grid = (
            view.groupby(["sector", "sma_signal"]).size().unstack(fill_value=0)
        )
        zeros = pd.Series(0, index=grid.index)
        bear = grid.get("⚠ DEATH CROSS", zeros) + grid.get(
            "▼ Kurs < SMA-200", zeros
        )
        grid = grid.loc[bear.sort_values(ascending=False).index]

        body = []
        for sector, row in grid.iterrows():
            counts = {sig: int(row.get(sig, 0)) for sig in PRIORITY}
            n = sum(counts.values())
            body.append(
                html.Div(
                    [
                        html.Span(str(sector), className="ms-sma-sect-nm", title=str(sector)),
                        _signal_bar(counts),
                        html.Span(fmt_int(n), className="ms-sma-sect-n"),
                    ],
                    className="ms-sma-sect",
                )
            )

    return html.Div(
        [
            html.H3(
                [
                    "Sektor-Breite ",
                    html.Span(
                        "Signal-Verteilung pro Sektor — bearish zuerst",
                        className="ms-card-h-meta",
                    ),
                ],
                className="ms-card-h",
            ),
            html.Div(body),
        ],
        className="ms-card",
        style={"marginTop": "16px"},
    )


def _tabs(options: list[dict], active: str, id_type: str) -> html.Div:
    return html.Div(
        [
            html.Button(
                o["label"],
                id={"type": id_type, "value": o["value"]},
                n_clicks=0,
                className=("is-active" if o["value"] == active else ""),
            )
            for o in options
        ],
        className="ms-tabs",
    )


def _filters(signal: str, lens: str) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Signal", className="ms-sma-filter-lbl"),
                    _tabs(SIGNAL_OPTIONS, signal, "sma-sig"),
                ],
                className="ms-sma-filter",
            ),
            html.Div(
                [
                    html.Div("Portfolio", className="ms-sma-filter-lbl"),
                    _tabs(PORTFOLIO_OPTIONS, lens, "sma-lens"),
                ],
                className="ms-sma-filter",
            ),
        ],
        className="ms-sma-filters",
    )


def _ticker_link(ticker) -> html.Td:
    t = str(ticker or "—")
    return html.Td(
        html.A(t, href=f"/einzelanalyse?ticker={t}", className="ms-tt-tk")
    )


def _score_pill(score) -> html.Td:
    if score is None or pd.isna(score):
        return html.Td("–", className="is-num")
    cls = _class_of(float(score))
    return html.Td(
        html.Span(fmt_de(float(score), 1), className=f"ms-score-pill is-{cls['cls']}"),
        className="is-num",
    )


def _rec_cell(rec) -> html.Td:
    rec = str(rec or "–")
    rec_cls = _REC_CLASS.get(rec, "fail")
    rec_short = "FAIL" if rec == "Filter nicht bestanden" else rec
    return html.Td(html.Span(rec_short, className=f"ms-tt-rec is-{rec_cls}"))


def _signal_chip(sig) -> html.Td:
    sig = str(sig or "–")
    cls = SIGNAL_CLASS.get(sig)
    if cls is None:
        return html.Td(sig, className="ms-tt-muted")
    return html.Td(html.Span(sig, className=f"ms-score-pill is-{cls}"))


def _dist_cell(value) -> html.Td:
    """Distanz in Prozentpunkten (bereits ×100 durch ``format_scored``)."""
    if value is None or pd.isna(value):
        return html.Td("–", className="is-num ms-tt-muted")
    tone = "ms-up" if float(value) >= 0 else "ms-down"
    return html.Td(_fmt_pp(float(value), 2), className=f"is-num {tone}")


def _signals_table(table_df: pd.DataFrame, limit: int) -> list:
    shown = table_df.head(limit)
    rows = []
    for _, r in shown.iterrows():
        filter_ok = str(r.get("filter_ok") or "–")
        filter_cls = "ms-up" if filter_ok == "JA" else "ms-down"
        rows.append(
            html.Tr(
                [
                    _ticker_link(r["ticker"]),
                    html.Td(str(r.get("name") or "—")),
                    html.Td(str(r.get("sector") or "—"), className="ms-tt-muted"),
                    _score_pill(r.get("total_score")),
                    html.Td(filter_ok, className=filter_cls),
                    _rec_cell(r.get("recommendation")),
                    _signal_chip(r.get("sma_signal")),
                    html.Td(_fmt_num(r.get("last_price")), className="is-num"),
                    html.Td(_fmt_num(r.get("sma_50")), className="is-num ms-tt-muted"),
                    html.Td(_fmt_num(r.get("sma_200")), className="is-num ms-tt-muted"),
                    _dist_cell(r.get("sma_50_distance")),
                    _dist_cell(r.get("sma_200_distance")),
                ]
            )
        )

    table = html.Div(
        html.Table(
            [
                html.Thead(
                    html.Tr(
                        [
                            html.Th("Ticker"),
                            html.Th("Name"),
                            html.Th("Sektor"),
                            html.Th("Score", className="is-num"),
                            html.Th("Filter"),
                            html.Th("Empfehlung"),
                            html.Th("Signal"),
                            html.Th("Kurs", className="is-num"),
                            html.Th("SMA-50", className="is-num"),
                            html.Th("SMA-200", className="is-num"),
                            html.Th("Dist. 50", className="is-num"),
                            html.Th("Dist. 200", className="is-num"),
                        ]
                    )
                ),
                html.Tbody(rows),
            ],
            className="ms-toptable",
        ),
        className="ms-toptable-wrap",
    )

    children: list = [table]
    remaining = len(table_df) - len(shown)
    if remaining > 0:
        children.append(
            html.Div(
                html.Button(
                    f"Mehr anzeigen ({fmt_int(remaining)} weitere)",
                    id="sma-more-btn",
                    n_clicks=0,
                    className="btn btn-outline-secondary",
                ),
                className="ms-sma-more",
            )
        )
    return children


def _legend_foot() -> html.Div:
    return html.Div(
        "✓ Golden Cross — Kurs > SMA-50 und Kurs > SMA-200 (stark bullish) · "
        "● Kurs > SMA-200 — über der 200-Tage-Linie, aber kein vollständiger "
        "Golden Cross · ▼ Kurs < SMA-200 — unter der 200-Tage-Linie, aber "
        "kein vollständiger Death Cross · ⚠ Death Cross — Kurs < SMA-50 und "
        "Kurs < SMA-200 (stark bearish).",
        className="ms-rrg-foot",
    )


def _signals_section(df: pd.DataFrame, signal: str, lens: str, limit: int) -> list:
    table_df = _build_signals(df, signal, lens)
    if signal == "ALL":
        title = "Alle Signale"
    else:
        title = f"Signale · {SIGNAL_SHORT.get(signal, signal)}"

    head = html.Div(
        [
            html.Div(
                [
                    html.Div("Signale", className="ms-eyebrow"),
                    html.H2(title),
                ]
            ),
            html.Div(
                f"{fmt_int(len(table_df))} Titel · sortiert nach Priorität und Score",
                className="ms-meta",
            ),
        ],
        className="ms-dash-section",
    )

    if table_df.empty:
        body: list = [
            html.Div(
                "Keine Treffer für die gewählte Kombination.",
                className="ms-empty-eyebrow",
                style={"padding": "16px 0"},
            )
        ]
    else:
        body = _signals_table(table_df, limit)

    return [head, _filters(signal, lens), *body, _legend_foot()]


def _watchlist_section(df: pd.DataFrame, lens: str) -> list:
    data = _build_watchlist(df, lens)

    head = html.Div(
        [
            html.Div(
                [
                    html.Div("Watchlist", className="ms-eyebrow"),
                    html.H2("Nahe am Kreuz"),
                ]
            ),
            html.Div(
                f"|SMA-50 − SMA-200| < {int(WATCH_THRESHOLD * 100)} % · "
                f"Top {WATCH_TOP_N} nach Nähe · bevorstehende Kreuzungen",
                className="ms-meta",
            ),
        ],
        className="ms-dash-section",
    )

    if data.empty:
        return [
            head,
            html.Div(
                f"Keine Titel mit |SMA-50 − SMA-200| < {int(WATCH_THRESHOLD * 100)} %.",
                className="ms-empty-eyebrow",
                style={"padding": "16px 0"},
            ),
        ]

    rows = []
    for _, r in data.iterrows():
        direction = str(r.get("direction") or "–")
        dir_cls = "is-up" if direction.startswith("↑") else "is-down"
        dir_label = direction.lstrip("↑↓ ")
        rows.append(
            html.Tr(
                [
                    _ticker_link(r["ticker"]),
                    html.Td(str(r.get("name") or "—")),
                    html.Td(str(r.get("sector") or "—"), className="ms-tt-muted"),
                    html.Td(html.Span(dir_label, className=f"ms-delta {dir_cls}")),
                    html.Td(_fmt_num(r.get("last_price")), className="is-num"),
                    html.Td(_fmt_num(r.get("sma_50")), className="is-num ms-tt-muted"),
                    html.Td(_fmt_num(r.get("sma_200")), className="is-num ms-tt-muted"),
                    _dist_cell(r.get("sma_gap")),
                    _score_pill(r.get("total_score")),
                    _rec_cell(r.get("recommendation")),
                ]
            )
        )

    table = html.Div(
        html.Table(
            [
                html.Thead(
                    html.Tr(
                        [
                            html.Th("Ticker"),
                            html.Th("Name"),
                            html.Th("Sektor"),
                            html.Th("Richtung"),
                            html.Th("Kurs", className="is-num"),
                            html.Th("SMA-50", className="is-num"),
                            html.Th("SMA-200", className="is-num"),
                            html.Th("Gap", className="is-num"),
                            html.Th("Score", className="is-num"),
                            html.Th("Empfehlung"),
                        ]
                    )
                ),
                html.Tbody(rows),
            ],
            className="ms-toptable",
        ),
        className="ms-toptable-wrap",
    )
    return [head, table]


# ── Layout & Render ────────────────────────────────────────────────────────

def _render_main(df: pd.DataFrame, signal: str, lens: str, limit: int) -> list:
    return [
        _sector_card(df, lens),
        *_signals_section(df, signal, lens, limit),
        *_watchlist_section(df, lens),
    ]


def layout(**_) -> html.Div:
    df = STATE.scored
    if df.empty:
        return html.Div([_empty_state()])

    return html.Div(
        [
            dcc.Store(id="sma-signal-store", data="ALL"),
            dcc.Store(id="sma-lens-store", data="all"),
            dcc.Store(id="sma-limit-store", data=PAGE_SIZE),
            _hero(df),
            _distribution_card(df),
            html.Div(
                _render_main(df, "ALL", "all", PAGE_SIZE),
                id="sma-main",
            ),
        ]
    )


# ── Callbacks ──────────────────────────────────────────────────────────────

@callback(
    Output("sma-signal-store", "data"),
    Output("sma-limit-store", "data", allow_duplicate=True),
    Input({"type": "sma-sig", "value": ALL}, "n_clicks"),
    State("sma-signal-store", "data"),
    prevent_initial_call=True,
)
def _on_signal_click(n_clicks_list, current):
    if not n_clicks_list or not any(n_clicks_list):
        return no_update, no_update
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict):
        return no_update, no_update
    return triggered.get("value", current), PAGE_SIZE


@callback(
    Output("sma-lens-store", "data"),
    Output("sma-limit-store", "data", allow_duplicate=True),
    Input({"type": "sma-lens", "value": ALL}, "n_clicks"),
    State("sma-lens-store", "data"),
    prevent_initial_call=True,
)
def _on_lens_click(n_clicks_list, current):
    if not n_clicks_list or not any(n_clicks_list):
        return no_update, no_update
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict):
        return no_update, no_update
    return triggered.get("value", current), PAGE_SIZE


@callback(
    Output("sma-limit-store", "data", allow_duplicate=True),
    Input("sma-more-btn", "n_clicks"),
    State("sma-limit-store", "data"),
    prevent_initial_call=True,
)
def _on_more(n_clicks, current):
    if not n_clicks:
        return no_update
    return int(current or PAGE_SIZE) + PAGE_SIZE


@callback(
    Output("sma-main", "children"),
    Input("sma-signal-store", "data"),
    Input("sma-lens-store", "data"),
    Input("sma-limit-store", "data"),
    prevent_initial_call=True,
)
def _render(signal, lens, limit):
    df = STATE.scored
    if df.empty:
        return [_empty_state()]
    return _render_main(df, signal or "ALL", lens or "all", int(limit or PAGE_SIZE))


register_page(__name__, path="/sma", name="SMA-Signale", layout=layout)
