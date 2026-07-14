"""Momentum-Monitor (früher SMA-Signal-Monitor, Sheet ``SMA_Signale``).

Re-Design analog Dashboard/Sektor-Momentum (Claude-Design-Handoff) plus
Momentum-Ausbau:
- Hero mit Marktbreite-Delta, Signal-Zählern und Signalwechseln seit Import
- Trend-Phasen (frisch/etabliert/ermüdet je Richtung) aus der SMA-Geometrie
- Echte Cross-Events aus der Import-Historie (NEU-Badge, Signal-Alter)
- 12-1-Momentum-Ranking mit 52-Wochen-Hoch-Distanz
- Signal-Tabelle & "Nahe am Kreuz"-Watchlist als ms-toptable mit Ticker-Links
- Optionale SMA-20-Spalte (Koyfin-Export) als Frühindikator in der Watchlist
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

from app.core.momentum import (
    FRESH_GAP_THRESHOLD,
    PHASE_ESTABLISHED_BEAR,
    PHASE_ESTABLISHED_BULL,
    PHASE_FRESH_BEAR,
    PHASE_FRESH_BULL,
    PHASE_NEUTRAL,
    PHASE_TIRED_BEAR,
    PHASE_TIRED_BULL,
    TIRED_RET_1M,
)
from app.core.signal_events import load_signal_events
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

# Richtungsagnostischer Präfix-Match auf ``trend_phase`` — die Richtung
# liefert bereits der Signal-Filter.
PHASE_OPTIONS = [
    {"label": "Alle", "value": "ALL"},
    {"label": "Frisch", "value": "Frisch"},
    {"label": "Etabliert", "value": "Etabliert"},
    {"label": "Ermüdet", "value": "Ermüdet"},
    {"label": "Neutral", "value": "Neutral"},
]

PORTFOLIO_OPTIONS = [
    {"label": "Gesamt", "value": "all"},
    {"label": "M&S", "value": "ms"},
    {"label": "Mein", "value": "my"},
]

# Trend-Phase → Score-Pill-Ton; Neutral wird als gedämpfter Text gerendert.
PHASE_CLASS = {
    PHASE_FRESH_BULL: "a",
    PHASE_ESTABLISHED_BULL: "bp",
    PHASE_TIRED_BULL: "c",
    PHASE_TIRED_BEAR: "c",
    PHASE_ESTABLISHED_BEAR: "d",
    PHASE_FRESH_BEAR: "f",
}

SIGNAL_COLS = [
    "ticker",
    "name",
    "sector",
    "total_score",
    "filter_ok",
    "recommendation",
    "sma_signal",
    "trend_phase",
    "is_new",
    "state_since",
    "days_in_state",
    "last_price",
    "sma_50_distance",
    "sma_200_distance",
]

WATCH_THRESHOLD = FRESH_GAP_THRESHOLD  # 3 %: |SMA-50 − SMA-200| / SMA-200
WATCH_TOP_N = 20
WATCH_COLS = [
    "ticker",
    "name",
    "sector",
    "direction",
    "last_price",
    "sma_20",
    "sma_50",
    "sma_200",
    "sma_gap",
    "total_score",
    "recommendation",
]

RANK_TOP_N = 20
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


def _has_sma20(df: pd.DataFrame) -> bool:
    return "sma_20" in df.columns and df["sma_20"].notna().any()


# ── Daten-Logik ────────────────────────────────────────────────────────────

def _apply_portfolio_lens(df: pd.DataFrame, lens: str) -> pd.DataFrame:
    if lens == "ms":
        return df[df["ticker"].isin(STATE.ms_portfolio)]
    if lens == "my":
        return df[df["ticker"].isin(STATE.my_portfolio)]
    return df


def _build_signals(
    df: pd.DataFrame, signal: str, phase: str, lens: str
) -> pd.DataFrame:
    mask = df["sma_signal"].isin(PRIORITY.keys())
    if signal != "ALL":
        mask &= df["sma_signal"] == signal
    if phase != "ALL" and "trend_phase" in df.columns:
        mask &= df["trend_phase"].astype(str).str.startswith(phase)
    filtered = _apply_portfolio_lens(df.loc[mask], lens)

    events = load_signal_events(df)
    if not events.empty:
        filtered = filtered.merge(
            events[["ticker", "is_new", "state_since", "days_in_state"]],
            on="ticker",
            how="left",
        )

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


def _build_ranking(df: pd.DataFrame, lens: str) -> pd.DataFrame:
    """Top-N nach klassischem 12-1-Momentum (Return 12M − Return 1M)."""
    filtered = _apply_portfolio_lens(df, lens)
    if "mom_12_1" not in filtered.columns:
        return pd.DataFrame()
    ranked = filtered.dropna(subset=["mom_12_1"]).sort_values(
        "mom_12_1", ascending=False
    )
    return format_scored(ranked.head(RANK_TOP_N))


def _signal_counts(df: pd.DataFrame) -> dict[str, int]:
    return {sig: int((df["sma_signal"] == sig).sum()) for sig in PRIORITY}


# ── Empty-State ────────────────────────────────────────────────────────────

def _empty_state() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Momentum-Monitor", className="ms-empty-eyebrow"),
                    html.H2("Noch keine Daten geladen", className="ms-empty-title"),
                    html.P(
                        "Lade einen Koyfin-CSV-Export hoch, um Trend-Phasen, "
                        "Cross-Events, Momentum-Ranking und Watchlist zu befüllen.",
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

    events = load_signal_events(df)
    has_history = not events.empty and events["momentum_prev"].notna().any()
    meta.append(html.Span("·", className="ms-sep"))
    if has_history:
        n_new = int(events["is_new"].sum())
        if n_new > 0:
            meta.append(
                html.Span(
                    f"{fmt_int(n_new)} Signalwechsel seit Import vom {stand}",
                    className="ms-badge is-warn",
                )
            )
        else:
            meta.append(html.Span("Keine Signalwechsel seit letztem Import"))
    else:
        meta.append(
            html.Span(
                "Signal-Historie baut sich mit dem nächsten Import auf",
                className="ms-muted",
            )
        )

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        f"Momentum-Monitor · Stand {stand}",
                        className="ms-hero-eyebrow",
                    ),
                    html.H1("Momentum-Monitor", className="ms-hero-title"),
                    html.P(
                        "Trend-Phasen, frische Crosses und 12-1-Momentum – "
                        "nicht nur wo ein Signal steht, sondern wie lebendig es ist.",
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


def _filters(signal: str, phase: str, lens: str) -> html.Div:
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
                    html.Div("Phase", className="ms-sma-filter-lbl"),
                    _tabs(PHASE_OPTIONS, phase, "sma-phase"),
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


def _signal_chip(sig, is_new=False) -> html.Td:
    sig = str(sig or "–")
    cls = SIGNAL_CLASS.get(sig)
    children: list = []
    if cls is None:
        children.append(sig)
    else:
        children.append(html.Span(sig, className=f"ms-score-pill is-{cls}"))
    if is_new:
        children.append(
            html.Span("NEU", className="ms-badge is-warn ms-tt-badge")
        )
    return html.Td(children, className="" if cls else "ms-tt-muted")


def _phase_chip(phase) -> html.Td:
    phase = str(phase or "–")
    cls = PHASE_CLASS.get(phase)
    if cls is None:
        return html.Td(phase, className="ms-tt-muted")
    return html.Td(html.Span(phase, className=f"ms-score-pill is-{cls}"))


def _since_cell(row: pd.Series) -> html.Td:
    """Signal-Alter aus der Import-Historie: „seit N T" bzw. NEU-Datum."""
    state_since = row.get("state_since")
    days = row.get("days_in_state")
    if state_since is None or (isinstance(state_since, float) and pd.isna(state_since)):
        return html.Td("–", className="ms-tt-muted")
    since_str = (
        state_since.strftime("%d.%m.%Y")
        if hasattr(state_since, "strftime")
        else str(state_since)
    )
    if bool(row.get("is_new")):
        return html.Td(f"seit Import vom {since_str}", className="ms-tt-muted")
    if days is None or pd.isna(days):
        return html.Td("–", className="ms-tt-muted")
    return html.Td(
        f"seit {fmt_int(int(days))} T",
        title=f"Im Zustand seit Import vom {since_str}",
        className="ms-tt-muted",
    )


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
                    _signal_chip(r.get("sma_signal"), bool(r.get("is_new"))),
                    _phase_chip(r.get("trend_phase")),
                    _since_cell(r),
                    html.Td(_fmt_num(r.get("last_price")), className="is-num"),
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
                            html.Th("Phase"),
                            html.Th("Seit"),
                            html.Th("Kurs", className="is-num"),
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


def _legend_foot(has_sma20: bool) -> html.Div:
    fresh_pct = int(FRESH_GAP_THRESHOLD * 100)
    tired_pct = int(TIRED_RET_1M * 100)
    parts = [
        "Signale: ✓ Golden Cross — Kurs > SMA-50 und > SMA-200 · "
        "● Kurs > SMA-200 ohne vollständigen Golden Cross · "
        "▼ Kurs < SMA-200 ohne vollständigen Death Cross · "
        "⚠ Death Cross — Kurs < SMA-50 und < SMA-200.",
        html.Br(),
        f"Phasen: Frisch — |SMA-50 − SMA-200| ≤ {fresh_pct} % (Cross liegt "
        "nahe) · Etabliert — Abstand größer, Trend intakt · Ermüdet — Kurs "
        f"kreuzt die SMA-50 gegen den Trend oder 1M-Return dreht (> {tired_pct} % "
        "gegenläufig) · Neutral — Kurs und SMA-50 auf verschiedenen Seiten der "
        "SMA-200.",
        html.Br(),
        "NEU — Signalwechsel gegenüber dem letzten Import · Seit — Alter des "
        "Zustands über die Import-Historie · 12-1-Momentum = Return 12M − "
        "Return 1M (letzter Monat ausgeklammert).",
    ]
    if not has_sma20:
        parts.extend(
            [
                html.Br(),
                "SMA-20 nicht im Export — Kurzfrist-Spalten der Watchlist "
                "sind ausgeblendet (optionale Spalte „SMA (20D)“ im "
                "Koyfin-Screener ergänzen).",
            ]
        )
    return html.Div(parts, className="ms-rrg-foot")


def _signals_section(
    df: pd.DataFrame, signal: str, phase: str, lens: str, limit: int
) -> list:
    table_df = _build_signals(df, signal, phase, lens)
    title_parts = []
    if signal != "ALL":
        title_parts.append(SIGNAL_SHORT.get(signal, signal))
    if phase != "ALL":
        title_parts.append(phase)
    title = "Signale · " + " · ".join(title_parts) if title_parts else "Alle Signale"

    events = load_signal_events(df)
    has_history = not events.empty and events["momentum_prev"].notna().any()

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

    body: list = []
    if not has_history:
        body.append(
            html.Div(
                "Signal-Historie baut sich mit dem nächsten Import auf — "
                "NEU-Badges und Signal-Alter erscheinen, sobald ein zweiter "
                "Import vorliegt.",
                className="ms-rrg-foot",
            )
        )
    if table_df.empty:
        body.append(
            html.Div(
                "Keine Treffer für die gewählte Kombination.",
                className="ms-empty-eyebrow",
                style={"padding": "16px 0"},
            )
        )
    else:
        body.extend(_signals_table(table_df, limit))

    return [head, _filters(signal, phase, lens), *body, _legend_foot(_has_sma20(df))]


def _ranking_section(df: pd.DataFrame, lens: str) -> list:
    data = _build_ranking(df, lens)

    head = html.Div(
        [
            html.Div(
                [
                    html.Div("Momentum", className="ms-eyebrow"),
                    html.H2("Momentum-Ranking (12-1)"),
                ]
            ),
            html.Div(
                f"Top {RANK_TOP_N} nach Return 12M − Return 1M",
                className="ms-meta",
            ),
        ],
        className="ms-dash-section",
    )

    if data.empty:
        return [
            head,
            html.Div(
                "Keine 12M/1M-Returns für diese Auswahl.",
                className="ms-empty-eyebrow",
                style={"padding": "16px 0"},
            ),
        ]

    rows = []
    for rank, (_, r) in enumerate(data.iterrows(), start=1):
        # dist_52w_high liegt nach format_scored in Prozentpunkten vor.
        d52 = r.get("dist_52w_high")
        if d52 is None or pd.isna(d52):
            d52_cell = html.Td("–", className="is-num ms-tt-muted")
        else:
            tone = (
                "ms-up" if d52 >= -5 else ("ms-down" if d52 <= -20 else "ms-tt-muted")
            )
            d52_cell = html.Td(_fmt_pp(float(d52), 1), className=f"is-num {tone}")
        rows.append(
            html.Tr(
                [
                    html.Td(fmt_int(rank), className="is-num ms-tt-muted"),
                    _ticker_link(r["ticker"]),
                    html.Td(str(r.get("name") or "—")),
                    html.Td(str(r.get("sector") or "—"), className="ms-tt-muted"),
                    _phase_chip(r.get("trend_phase")),
                    _dist_cell(r.get("mom_12_1")),
                    html.Td(
                        _fmt_pp(r.get("ret_12m") * 100, 1)
                        if pd.notna(r.get("ret_12m"))
                        else "–",
                        className="is-num ms-tt-muted",
                    ),
                    html.Td(
                        _fmt_pp(r.get("ret_1m") * 100, 1)
                        if pd.notna(r.get("ret_1m"))
                        else "–",
                        className="is-num ms-tt-muted",
                    ),
                    d52_cell,
                    _score_pill(r.get("total_score")),
                    _signal_chip(r.get("sma_signal")),
                ]
            )
        )

    table = html.Div(
        html.Table(
            [
                html.Thead(
                    html.Tr(
                        [
                            html.Th("#", className="is-num"),
                            html.Th("Ticker"),
                            html.Th("Name"),
                            html.Th("Sektor"),
                            html.Th("Phase"),
                            html.Th("12-1", className="is-num"),
                            html.Th("Ret 12M", className="is-num"),
                            html.Th("Ret 1M", className="is-num"),
                            html.Th("52W-Hoch", className="is-num"),
                            html.Th("Score", className="is-num"),
                            html.Th("Signal"),
                        ]
                    )
                ),
                html.Tbody(rows),
            ],
            className="ms-toptable",
        ),
        className="ms-toptable-wrap",
    )
    foot = html.Div(
        "12-1-Momentum = Return 12M − Return 1M (letzter Monat ausgeklammert, "
        "klassische Momentum-Definition) · 52W-Hoch = Abstand zum "
        "52-Wochen-Hoch.",
        className="ms-rrg-foot",
    )
    return [head, table, foot]


def _watchlist_section(df: pd.DataFrame, lens: str) -> list:
    data = _build_watchlist(df, lens)
    has_sma20 = _has_sma20(df)

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
        cells = [
            _ticker_link(r["ticker"]),
            html.Td(str(r.get("name") or "—")),
            html.Td(str(r.get("sector") or "—"), className="ms-tt-muted"),
            html.Td(html.Span(dir_label, className=f"ms-delta {dir_cls}")),
            html.Td(_fmt_num(r.get("last_price")), className="is-num"),
        ]
        if has_sma20:
            sma_20 = r.get("sma_20")
            sma_50 = r.get("sma_50")
            cells.append(
                html.Td(_fmt_num(sma_20), className="is-num ms-tt-muted")
            )
            if pd.notna(sma_20) and pd.notna(sma_50) and sma_50 > 0:
                gap2050 = (float(sma_20) - float(sma_50)) / float(sma_50) * 100
                tone = "ms-up" if gap2050 >= 0 else "ms-down"
                cells.append(
                    html.Td(_fmt_pp(gap2050, 2), className=f"is-num {tone}")
                )
            else:
                cells.append(html.Td("–", className="is-num ms-tt-muted"))
        cells.extend(
            [
                html.Td(_fmt_num(r.get("sma_50")), className="is-num ms-tt-muted"),
                html.Td(_fmt_num(r.get("sma_200")), className="is-num ms-tt-muted"),
                _dist_cell(r.get("sma_gap")),
                _score_pill(r.get("total_score")),
                _rec_cell(r.get("recommendation")),
            ]
        )
        rows.append(html.Tr(cells))

    header_cells = [
        html.Th("Ticker"),
        html.Th("Name"),
        html.Th("Sektor"),
        html.Th("Richtung"),
        html.Th("Kurs", className="is-num"),
    ]
    if has_sma20:
        header_cells.extend(
            [
                html.Th("SMA-20", className="is-num"),
                html.Th("Gap 20/50", className="is-num"),
            ]
        )
    header_cells.extend(
        [
            html.Th("SMA-50", className="is-num"),
            html.Th("SMA-200", className="is-num"),
            html.Th("Gap", className="is-num"),
            html.Th("Score", className="is-num"),
            html.Th("Empfehlung"),
        ]
    )

    table = html.Div(
        html.Table(
            [html.Thead(html.Tr(header_cells)), html.Tbody(rows)],
            className="ms-toptable",
        ),
        className="ms-toptable-wrap",
    )
    return [head, table]


# ── Layout & Render ────────────────────────────────────────────────────────

def _render_main(
    df: pd.DataFrame, signal: str, phase: str, lens: str, limit: int
) -> list:
    return [
        *_ranking_section(df, lens),
        _sector_card(df, lens),
        *_signals_section(df, signal, phase, lens, limit),
        *_watchlist_section(df, lens),
    ]


def layout(**_) -> html.Div:
    df = STATE.scored
    if df.empty:
        return html.Div([_empty_state()])

    return html.Div(
        [
            dcc.Store(id="sma-signal-store", data="ALL"),
            dcc.Store(id="sma-phase-store", data="ALL"),
            dcc.Store(id="sma-lens-store", data="all"),
            dcc.Store(id="sma-limit-store", data=PAGE_SIZE),
            _hero(df),
            _distribution_card(df),
            html.Div(
                _render_main(df, "ALL", "ALL", "all", PAGE_SIZE),
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
    Output("sma-phase-store", "data"),
    Output("sma-limit-store", "data", allow_duplicate=True),
    Input({"type": "sma-phase", "value": ALL}, "n_clicks"),
    State("sma-phase-store", "data"),
    prevent_initial_call=True,
)
def _on_phase_click(n_clicks_list, current):
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
    Input("sma-phase-store", "data"),
    Input("sma-lens-store", "data"),
    Input("sma-limit-store", "data"),
    prevent_initial_call=True,
)
def _render(signal, phase, lens, limit):
    df = STATE.scored
    if df.empty:
        return [_empty_state()]
    return _render_main(
        df,
        signal or "ALL",
        phase or "ALL",
        lens or "all",
        int(limit or PAGE_SIZE),
    )


register_page(__name__, path="/sma", name="Momentum-Monitor", layout=layout)
