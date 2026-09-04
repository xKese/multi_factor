"""Sektor-Momentum: Aggregations-Screen über das Equity-Universum
(Re-Design aus dem Claude-Design-Handoff) plus Legacy-ETF-Snapshot-Workflow
als Akkordeon am Seitenende.
"""

from __future__ import annotations

import base64
from datetime import date, datetime
from typing import Iterable

import dash_bootstrap_components as dbc
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
    MOMENTUM_DEATH,
    MOMENTUM_DOWN,
    MOMENTUM_GOLDEN,
    MOMENTUM_NONE,
    MOMENTUM_STATES,
    MOMENTUM_UP,
)
from app.core.persistence import (
    load_sector_score_history,
    load_sector_snapshots,
    save_sector_snapshot,
)
from app.core.sector_momentum import (
    aggregate_sectors,
    build_snapshot_frame,
    load_sector_csv,
)
from app.core.sectors import (
    GROUP_INDUSTRY,
    GROUP_SECTOR,
    INDUSTRY_ETFS,
    SECTOR_ETFS,
)
from app.core.state import STATE
from app.pages.common import page_title
from app.ui import fmt_de, kpi_band, section_header
from app.ui.formatters import fmt_int


# ── Klassifikations-Mapping (siehe dashboard.py) ───────────────────────────

def _class_of(score: float) -> dict:
    """Versionsbewusste Klassifikation (v1-/v2-Schwellen), siehe score_context."""
    from app.ui.score_context import class_of_score

    return class_of_score(score)


_REC_CLASS = {
    "STRONG BUY": "strong",
    "BUY": "buy",
    "HOLD": "hold",
    "SELL": "sell",
    "Filter nicht bestanden": "fail",
}


_TF_DEFS: list[tuple[str, str, str]] = [
    ("1m", "1M", "ret_1m"),
    ("3m", "3M", "ret_3m"),
    ("6m", "6M", "ret_6m"),
    ("12_1", "12M-1M", "mom_12_1"),
]
_TF_LABEL = {k: f for k, f, _ in _TF_DEFS}
_TF_FIELD = {k: f for k, _, f in _TF_DEFS}


# ── Hilfen für Formatierung & Heat-Tone ────────────────────────────────────


def _fmt_pp(value: float, decimals: int = 1) -> str:
    """Prozent-Punkt-Wert (z. B. 5.2 → \"5,2 %\") mit Vorzeichen."""
    if value is None or pd.isna(value):
        return "–"
    sign = "+" if value > 0 else ("−" if value < 0 else "")
    return f"{sign}{fmt_de(abs(float(value)), decimals)} %"


def _delta_class(value: float) -> str:
    if value is None or pd.isna(value):
        return "is-flat"
    if abs(value) < 0.5:
        return "is-flat"
    return "is-up" if value > 0 else "is-down"


def _heat_tone(v: float, mid: float = 0.0, rng: float = 20.0) -> str:
    """Liefert die Score-Farb-CSS-Variable basierend auf relativer Lage."""
    if v is None or pd.isna(v) or rng == 0:
        return "var(--ms-border-strong)"
    t = max(-1.0, min(1.0, (v - mid) / rng))
    if t > 0.5:
        return "var(--ms-score-a)"
    if t > 0.15:
        return "var(--ms-score-bp)"
    if t > -0.15:
        return "var(--ms-score-c)"
    if t > -0.5:
        return "var(--ms-score-d)"
    return "var(--ms-score-f)"


def _sector_code(name: str) -> str:
    parts = [p for p in str(name).split() if p]
    if not parts:
        return "—"
    return "".join(p[0] for p in parts)[:3].upper()


def _heat_value_label(field: str, value: float) -> str:
    if value is None or pd.isna(value):
        return "–"
    if field == "delta_score":
        sign = "+" if value > 0 else ("−" if value < 0 else "")
        return f"{sign}{fmt_de(abs(value), 1)}"
    return _fmt_pp(value)


# ── Sub-Komponenten (Re-Design) ────────────────────────────────────────────


def _hero(agg: list[dict], tf: str) -> html.Div:
    if not agg:
        return html.Div(className="ms-hero")
    sort_field = _TF_FIELD.get(tf, "mom_12_1")
    sortable = [
        d for d in agg if not pd.isna(d.get(sort_field))
    ]
    sortable.sort(key=lambda d: d[sort_field], reverse=True)
    leader = sortable[0] if sortable else agg[0]
    laggard = sortable[-1] if sortable else agg[-1]

    breadth_vals = [d["breadth_sma200"] for d in agg]
    avg_breadth = (
        round(sum(breadth_vals) / len(breadth_vals)) if breadth_vals else 0
    )

    df = STATE.scored
    n_golden = 0
    if not df.empty and "sma_signal" in df.columns:
        n_golden = int(
            df["sma_signal"].astype(str).str.contains("GOLDEN", na=False).sum()
        )

    today = datetime.now().strftime("%d.%m.%Y")

    tab_buttons = []
    for k, label, _ in _TF_DEFS:
        tab_buttons.append(
            html.Button(
                label,
                id={"type": "sm-tf", "value": k},
                n_clicks=0,
                className=("is-active" if k == tf else ""),
            )
        )

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        f"Sektor-Momentum · Stand {today}",
                        className="ms-hero-eyebrow",
                    ),
                    html.H1(
                        "Wer führt, wer dreht, wer fällt",
                        className="ms-hero-title",
                    ),
                    html.P(
                        "Rotations-Quadrant, Heatmap und Breadth – auf einen Blick.",
                        className="ms-hero-subhead",
                    ),
                    html.Div(
                        [
                            html.Span(
                                [
                                    "Führend ",
                                    html.Strong(
                                        leader["sector"],
                                        style={"color": "var(--ms-up)"},
                                    ),
                                    f" ({_fmt_pp(leader.get(sort_field), 1)})",
                                ]
                            ),
                            html.Span("·", className="ms-sep"),
                            html.Span(
                                [
                                    "Nachzügler ",
                                    html.Strong(
                                        laggard["sector"],
                                        style={"color": "var(--ms-down)"},
                                    ),
                                    f" ({_fmt_pp(laggard.get(sort_field), 1)})",
                                ]
                            ),
                            html.Span("·", className="ms-sep"),
                            html.Span(
                                [
                                    "Ø Breadth > SMA-200 ",
                                    html.Strong(f"{avg_breadth} %"),
                                ]
                            ),
                            html.Span("·", className="ms-sep"),
                            html.Span(
                                [
                                    "Golden Crosses ",
                                    html.Strong(fmt_int(n_golden)),
                                ]
                            ),
                        ],
                        className="ms-hero-meta",
                    ),
                ]
            ),
            html.Div(
                [
                    html.Div("Zeitfenster", className="ms-sm-tf-lbl"),
                    html.Div(tab_buttons, className="ms-tabs"),
                    html.Div(
                        f"Sortierung: {_TF_LABEL.get(tf, tf)}",
                        className="ms-sm-tf-sort",
                    ),
                ],
                className="ms-sm-tf",
            ),
        ],
        className="ms-hero ms-sm-hero",
    )


def _rrg_card(agg: list[dict], active: str | None) -> html.Div:
    if not agg:
        body: list = [html.Div("Keine Daten", className="ms-empty-eyebrow")]
    else:
        x_vals = [d["mom_12_1"] for d in agg if pd.notna(d["mom_12_1"])]
        y_vals = [d["sma200_dist"] for d in agg if pd.notna(d["sma200_dist"])]
        x_max = max([20.0] + [abs(v) for v in x_vals]) if x_vals else 20.0
        y_max = max([15.0] + [abs(v) for v in y_vals]) if y_vals else 15.0

        dots: list = []
        for d in agg:
            mom = d.get("mom_12_1")
            sma = d.get("sma200_dist")
            if pd.isna(mom) or pd.isna(sma):
                continue
            x = 50 + (mom / x_max) * 42
            y = 50 - (sma / y_max) * 42
            if mom > 0 and sma > 0:
                tone = "is-up"
            elif mom > 0 and sma <= 0:
                tone = "is-warn"
            elif mom <= 0 and sma > 0:
                tone = "is-gold"
            else:
                tone = "is-down"
            classes = ["ms-rrg-dot", tone]
            if active == d["sector"]:
                classes.append("is-active")
            if x > 70:
                classes.append("is-flip-left")
            dots.append(
                html.Div(
                    [
                        html.Div(className="ms-rrg-pt"),
                        html.Div(_sector_code(d["sector"]), className="ms-rrg-code"),
                        html.Div(d["sector"], className="ms-rrg-lab"),
                    ],
                    id={"type": "sm-pick", "sector": d["sector"]},
                    n_clicks=0,
                    className=" ".join(classes),
                    style={"left": f"{x}%", "top": f"{y}%"},
                )
            )

        body = [
            html.Div(className="ms-rrg-grid"),
            html.Div(
                [html.Strong("Improving"), "schwach, aber dreht"],
                className="ms-rrg-q is-tl",
            ),
            html.Div(
                [html.Strong("Leading"), "stark im Trend"],
                className="ms-rrg-q is-tr",
            ),
            html.Div(
                [html.Strong("Lagging"), "schwach & schwach"],
                className="ms-rrg-q is-bl",
            ),
            html.Div(
                [html.Strong("Weakening"), "noch oben, schwächt"],
                className="ms-rrg-q is-br",
            ),
            html.Div("Momentum 12M-1M →", className="ms-rrg-axis is-x"),
            html.Div("↑ Distanz zu SMA-200", className="ms-rrg-axis is-y"),
            *dots,
        ]

    return html.Div(
        [
            html.H3(
                [
                    "Rotations-Quadrant ",
                    html.Span("Momentum × Trend", className="ms-card-h-meta"),
                ],
                className="ms-card-h",
            ),
            html.Div(body, className="ms-rrg-wrap"),
            html.Div(
                "X-Achse: Momentum 12M minus 1M · Y-Achse: Distanz zu SMA-200 · "
                "Farbe: Status der Kombination · Klick wählt Sektor.",
                className="ms-rrg-foot",
            ),
        ],
        className="ms-card",
    )


_HEAT_COLS: list[tuple[str, str, float]] = [
    ("ret_1m", "1M", 20.0),
    ("ret_3m", "3M", 20.0),
    ("ret_6m", "6M", 20.0),
    ("mom_12_1", "12M-1M", 20.0),
    ("sma50_dist", "SMA-50", 18.0),
    ("sma200_dist", "SMA-200", 18.0),
    ("delta_score", "ΔScore", 6.0),
]


_LC_REASON_LABELS: dict[str, str] = {
    "count<3": "weniger als 3 Aktien im Sektor",
    "history<2": "Score-Historie zu kurz für ΔScore",
    "stale>7d": "Daten älter als 7 Tage",
    "prev_stale": "Vormonats-Snapshot außerhalb Toleranzfenster",
}


def _format_lc_tooltip(reasons: list[str]) -> str:
    if not reasons:
        return ""
    labels = [_LC_REASON_LABELS.get(r, r) for r in reasons]
    return "Niedrige Konfidenz: " + " · ".join(labels)


def _lc_badge(reasons: list[str]) -> html.Span:
    return html.Span(
        "!",
        className="ms-lc-badge",
        title=_format_lc_tooltip(reasons),
    )


def _heatmap_card(agg: list[dict], active: str | None) -> html.Div:
    head = html.Thead(
        html.Tr(
            [html.Th("Sektor", className="is-row-h")]
            + [html.Th(label) for _, label, _ in _HEAT_COLS]
        )
    )
    rows: list = []
    for d in agg:
        is_active = active == d["sector"]
        sector_children: list = [d["sector"]]
        if d.get("low_confidence"):
            sector_children.append(_lc_badge(d.get("confidence_reasons", [])))
        cells = [html.Td(sector_children, className="is-row-h")]
        for field, _label, rng in _HEAT_COLS:
            v = d.get(field)
            cells.append(
                html.Td(
                    html.Span(
                        _heat_value_label(field, v),
                        className="ms-heat-tile",
                        style={"background": _heat_tone(v, 0.0, rng)},
                    )
                )
            )
        rows.append(
            html.Tr(
                cells,
                id={"type": "sm-pick", "sector": d["sector"]},
                n_clicks=0,
                className="is-active" if is_active else "",
            )
        )

    return html.Div(
        [
            html.H3(
                [
                    "Heatmap ",
                    html.Span("Sektor × Maß", className="ms-card-h-meta"),
                ],
                className="ms-card-h",
            ),
            html.Div(
                html.Table([head, html.Tbody(rows)], className="ms-heatmatrix"),
                className="ms-heatmatrix-wrap",
            ),
        ],
        className="ms-card",
    )


def _spark_bars(values: list[float]) -> html.Div:
    """Sparkline als 12 vertikale Mini-Säulen (CSS-only, kein SVG nötig)."""
    if not values or len(values) < 2:
        return html.Div("–", className="ms-spark-empty")
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    direction = "is-up" if values[-1] >= values[0] else "is-down"
    bars: list = []
    for v in values:
        h = ((v - lo) / span) * 100.0
        h = max(8.0, min(100.0, h))
        bars.append(html.Span(style={"height": f"{h}%"}))
    return html.Div(bars, className=f"ms-spark {direction}")


def _drilldown_strip(d: dict) -> html.Div:
    return html.Div(
        [
            html.Div("Sektor", className="ms-drill-lbl"),
            html.H3(d["sector"], className="ms-drill-h"),
            html.Div(
                [
                    html.Span(
                        ["Score", html.Strong(fmt_de(d["score"], 1))],
                    ),
                    html.Span(
                        ["12M-1M", html.Strong(_fmt_pp(d["mom_12_1"]))],
                    ),
                    html.Span(
                        ["SMA-200", html.Strong(_fmt_pp(d["sma200_dist"]))],
                    ),
                    html.Span(
                        ["Aktien", html.Strong(fmt_int(d["count"]))],
                    ),
                ],
                className="ms-drill-meta",
            ),
            html.Button(
                "Filter aufheben",
                id="sm-close-drill",
                n_clicks=0,
                className="ms-drill-close",
            ),
        ],
        className="ms-drill-strip",
    )


def _drill_table(active: str) -> html.Div:
    df = STATE.scored
    if df.empty or "sector" not in df.columns:
        return html.Div("Keine Daten", className="ms-empty-eyebrow")
    from app.ui.score_context import class_of_score, is_v2

    score_col = _score_col() if _score_col() in df.columns else "total_score"
    v2 = is_v2() and score_col == "composite_score"
    sub = df[df["sector"] == active]
    sub = sub.dropna(subset=[score_col]).sort_values(
        score_col, ascending=False
    ).head(10)
    if sub.empty:
        return html.Div("Keine Aktien für diesen Sektor.", className="ms-empty-eyebrow")

    _zone_class = {
        "KANDIDAT": "strong",
        "HALTEN": "hold",
        "VERKAUFEN": "sell",
        "FILTER": "fail",
    }
    rows = []
    for _, r in sub.iterrows():
        cls = class_of_score(float(r[score_col]))
        if v2:
            rec_short = str(r.get("zone_v2") or "–")
            rec_cls = _zone_class.get(rec_short, "fail")
        else:
            rec = str(r.get("recommendation") or "–")
            rec_cls = _REC_CLASS.get(rec, "fail")
            rec_short = "FAIL" if rec == "Filter nicht bestanden" else rec
        ret_12m = r.get("ret_12m")
        ret_1m = r.get("ret_1m")
        if pd.notna(ret_12m) and pd.notna(ret_1m):
            mom = (float(ret_12m) - float(ret_1m)) * 100.0
        else:
            mom = float("nan")
        sma200_raw = r.get("sma_200_distance")
        sma200 = (
            float(sma200_raw) * 100.0 if pd.notna(sma200_raw) else float("nan")
        )
        rows.append(
            html.Tr(
                [
                    html.Td(
                        html.A(
                            str(r["ticker"]),
                            href=f"/einzelanalyse?ticker={r.get('uid') or r['ticker']}",
                            className="ms-tt-tk",
                        ),
                    ),
                    html.Td(str(r.get("name") or "—")),
                    html.Td(
                        str(r.get("industry") or "—"),
                        className="ms-tt-muted",
                        style={"fontSize": "11px"},
                    ),
                    html.Td(
                        html.Span(
                            fmt_de(float(r[score_col]), 1),
                            className=f"ms-score-pill is-{cls['cls']}",
                        ),
                        className="is-num",
                    ),
                    html.Td(
                        _fmt_pp(mom),
                        className=(
                            "is-num "
                            + ("ms-up" if pd.notna(mom) and mom >= 0 else "ms-down")
                        ),
                    ),
                    html.Td(
                        _fmt_pp(float(sma200) if pd.notna(sma200) else float("nan")),
                        className=(
                            "is-num "
                            + (
                                "ms-up"
                                if pd.notna(sma200) and float(sma200) >= 0
                                else "ms-down"
                            )
                        ),
                    ),
                    html.Td(
                        html.Span(rec_short, className=f"ms-tt-rec is-{rec_cls}"),
                    ),
                ]
            )
        )

    return html.Div(
        html.Table(
            [
                html.Thead(
                    html.Tr(
                        [
                            html.Th("Ticker"),
                            html.Th("Name"),
                            html.Th("Industrie"),
                            html.Th("Score", className="is-num"),
                            html.Th("12M-1M", className="is-num"),
                            html.Th("SMA-200", className="is-num"),
                            html.Th("Zone" if v2 else "Empfehlung"),
                        ]
                    )
                ),
                html.Tbody(rows),
            ],
            className="ms-toptable",
        ),
        className="ms-card ms-toptable-wrap",
        style={"padding": 0},
    )


def _breadth_cell(pct: int | float | None) -> html.Span:
    if pct is None or pd.isna(pct):
        return html.Span("–", className="ms-breadth-cell ms-breadth-empty")
    pct = int(pct)
    cls = ""
    if pct < 40:
        cls = "is-weak"
    elif pct < 60:
        cls = "is-warn"
    return html.Span(
        [
            html.Span(
                html.Span(
                    className=f"ms-breadth-fill {cls}".strip(),
                    style={"width": f"{pct}%"},
                ),
                className="ms-breadth-track",
            ),
            html.Span(f"{pct} %", className="ms-breadth-num"),
        ],
        className="ms-breadth-cell",
    )


def _delta_chip(value: float) -> html.Span:
    cls = _delta_class(value)
    return html.Span(
        fmt_de(value, 1) if pd.notna(value) else "–",
        className=f"ms-delta {cls}",
    )


def _industry_subrow(industries: list[dict]) -> html.Tr:
    rows = []
    for ind in industries:
        rows.append(
            html.Tr(
                [
                    html.Td(ind["industry"], className="ms-ind-nm"),
                    html.Td(
                        html.Span(
                            fmt_de(ind["score"], 1),
                            className=(
                                "ms-score-pill is-"
                                + _class_of(ind["score"])["cls"]
                            ),
                        ),
                        className="is-num",
                    ),
                    html.Td(_delta_chip(ind["delta_score"]), className="is-num"),
                    html.Td(
                        _fmt_pp(ind["mom_12_1"]),
                        className=(
                            "is-num "
                            + (
                                "ms-up"
                                if pd.notna(ind["mom_12_1"]) and ind["mom_12_1"] >= 0
                                else "ms-down"
                            )
                        ),
                    ),
                    html.Td(
                        _fmt_pp(ind["sma200_dist"]),
                        className=(
                            "is-num "
                            + (
                                "ms-up"
                                if pd.notna(ind["sma200_dist"])
                                and ind["sma200_dist"] >= 0
                                else "ms-down"
                            )
                        ),
                    ),
                    html.Td(_breadth_cell(ind["breadth_sma200"]), className="is-num"),
                    html.Td(fmt_int(ind["count"]), className="is-num ms-tt-muted"),
                ]
            )
        )
    return html.Tr(
        html.Td(
            html.Div(
                html.Table(
                    [
                        html.Thead(
                            html.Tr(
                                [
                                    html.Th("Industrie"),
                                    html.Th("Score", className="is-num"),
                                    html.Th("ΔScore", className="is-num"),
                                    html.Th("12M-1M", className="is-num"),
                                    html.Th("SMA-200", className="is-num"),
                                    html.Th("Breadth", className="is-num"),
                                    html.Th("Aktien", className="is-num"),
                                ]
                            )
                        ),
                        html.Tbody(rows),
                    ],
                    className="ms-ind-table-mini",
                ),
                className="ms-ind-rows-inner",
            ),
            colSpan=9,
        ),
        className="ms-ind-rows",
    )


def _detail_table(agg: list[dict], active: str | None, open_: str | None) -> html.Div:
    body: list = []
    for d in agg:
        is_open = open_ == d["sector"]
        is_active = active == d["sector"]
        cls_score = _class_of(d["score"])["cls"]
        row_classes = "ms-sect-row"
        if is_open:
            row_classes += " is-open"
        if is_active:
            row_classes += " is-active"

        sect_name_children: list = [
            html.Span(className="ms-sect-chev"),
            html.Span(
                d["sector"],
                id={"type": "sm-pick", "sector": d["sector"]},
                n_clicks=0,
                className="ms-sect-name-text",
            ),
        ]
        if d.get("low_confidence"):
            sect_name_children.append(_lc_badge(d.get("confidence_reasons", [])))

        body.append(
            html.Tr(
                [
                    html.Td(
                        html.Span(
                            sect_name_children,
                            className="ms-sect-name",
                        ),
                    ),
                    html.Td(
                        html.Span(
                            fmt_de(d["score"], 1),
                            className=f"ms-score-pill is-{cls_score}",
                        ),
                        className="is-num",
                    ),
                    html.Td(_delta_chip(d["delta_score"]), className="is-num"),
                    html.Td(
                        _fmt_pp(d["mom_12_1"]),
                        className=(
                            "is-num "
                            + (
                                "ms-up"
                                if pd.notna(d["mom_12_1"]) and d["mom_12_1"] >= 0
                                else "ms-down"
                            )
                        ),
                        style={"fontWeight": "600"},
                    ),
                    html.Td(
                        _fmt_pp(d["sma50_dist"]),
                        className=(
                            "is-num "
                            + (
                                "ms-up"
                                if pd.notna(d["sma50_dist"]) and d["sma50_dist"] >= 0
                                else "ms-down"
                            )
                        ),
                    ),
                    html.Td(
                        _fmt_pp(d["sma200_dist"]),
                        className=(
                            "is-num "
                            + (
                                "ms-up"
                                if pd.notna(d["sma200_dist"]) and d["sma200_dist"] >= 0
                                else "ms-down"
                            )
                        ),
                    ),
                    html.Td(
                        _breadth_cell(d["breadth_sma200"]),
                        className="is-num",
                    ),
                    html.Td(_spark_bars(d["spark"]), className="is-ctr"),
                    html.Td(fmt_int(d["count"]), className="is-num ms-tt-muted"),
                ],
                id={"type": "sm-open", "sector": d["sector"]},
                n_clicks=0,
                className=row_classes,
            )
        )
        if is_open and d["industries"]:
            body.append(_industry_subrow(d["industries"]))

    return html.Div(
        html.Table(
            [
                html.Thead(
                    html.Tr(
                        [
                            html.Th("Sektor"),
                            html.Th("Score", className="is-num"),
                            html.Th("ΔScore", className="is-num"),
                            html.Th("12M-1M", className="is-num"),
                            html.Th("SMA-50", className="is-num"),
                            html.Th("SMA-200", className="is-num"),
                            html.Th("Breadth > SMA-200", className="is-num"),
                            html.Th(
                                "Verlauf 12W",
                                className="is-ctr",
                                title=(
                                    "Score-Verlauf der letzten 12 Kalenderwochen "
                                    "(letzter Import je Woche)"
                                ),
                            ),
                            html.Th("Aktien", className="is-num"),
                        ]
                    )
                ),
                html.Tbody(body),
            ],
            className="ms-sect-table",
        ),
        className="ms-card",
        style={"padding": 0},
    )


def _detail_section(agg: list[dict], active: str | None, open_: str | None) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Div("Detailtabelle", className="ms-eyebrow"),
                            html.H2("Alle Sektoren mit Industrie-Drilldown"),
                        ]
                    ),
                    html.Div(
                        "Klick auf Zeile öffnet Industrien · Klick auf Sektorname "
                        "wählt Sektor für Drilldown oben",
                        className="ms-meta",
                    ),
                ],
                className="ms-dash-section",
            ),
            _detail_table(agg, active, open_),
        ]
    )


def _empty_state() -> html.Div:
    return html.Div(
        [
            html.Div("Sektor-Momentum", className="ms-empty-eyebrow"),
            html.H2(
                "Noch keine Daten geladen",
                className="ms-empty-title",
            ),
            html.P(
                "Lade einen Koyfin-CSV-Export hoch, damit das Universum "
                "sektorweise aggregiert werden kann.",
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
        className="ms-empty",
    )


# ── Legacy ETF-Snapshot-Workflow (unverändert übernommen) ───────────────────


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


def _kpi_band_legacy(df: pd.DataFrame) -> html.Div:
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


def _legacy_body(df: pd.DataFrame) -> list:
    if df.empty:
        return [
            dbc.Alert(
                "Noch keine Snapshots – CSV hochladen.", color="info"
            )
        ]

    sectors_df = df[df["group"] == GROUP_SECTOR]
    industries_df = df[df["group"] == GROUP_INDUSTRY]

    return [
        _kpi_band_legacy(df),
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


def _legacy_upload_row() -> html.Div:
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


def _legacy_section() -> dbc.Accordion:
    df = load_sector_snapshots(12)
    return dbc.Accordion(
        [
            dbc.AccordionItem(
                [
                    html.P(
                        "Wöchentliche TAA-Conviction-Pflege via Koyfin-Sektor-CSV "
                        "(11 GICS-Sektor-ETFs + 19 Industrie-ETFs). Diese Ansicht "
                        "ist unabhängig vom Equity-Universum oben.",
                        className="ms-empty-sub",
                    ),
                    _legacy_upload_row(),
                    html.Div(id="sm-status", className="mb-3"),
                    html.Div(id="sm-body", children=_legacy_body(df)),
                ],
                title="ETF-Snapshot-Matrix (TAA Conviction)",
            )
        ],
        start_collapsed=True,
        className="ms-sm-legacy",
    )


# ── Layout & Render ────────────────────────────────────────────────────────


def _render_main(agg: list[dict], tf: str, active: str | None, open_: str | None) -> list:
    if not agg:
        return [_empty_state()]
    sort_field = _TF_FIELD.get(tf, "mom_12_1")
    sorted_agg = sorted(
        agg,
        key=lambda d: (
            d.get(sort_field) if pd.notna(d.get(sort_field)) else float("-inf")
        ),
        reverse=True,
    )
    children: list = [
        _hero(sorted_agg, tf),
        html.Div(
            [
                _rrg_card(sorted_agg, active),
                _heatmap_card(sorted_agg, active),
            ],
            className="ms-row ms-sm-r-2",
        ),
    ]
    if active is not None:
        d = next((x for x in sorted_agg if x["sector"] == active), None)
        if d is not None:
            children.append(_drilldown_strip(d))
            children.append(_drill_table(active))
    children.append(_detail_section(sorted_agg, active, open_))
    return children


def _score_col() -> str:
    """Primäre Score-Spalte der Aggregate (v1 Gesamt-Score / Composite v2)."""
    from app.ui.score_context import is_v2

    return "composite_score" if is_v2() else "total_score"


def layout(**_) -> html.Div:
    df = STATE.scored
    history = load_sector_score_history() if not df.empty else None
    agg = (
        aggregate_sectors(df, history=history, score_col=_score_col())
        if not df.empty
        else []
    )

    return html.Div(
        [
            page_title(
                "Sektor-Momentum",
                "Rotation, Heatmap, Breadth — Equity-Universum sektorweise.",
            ),
            dcc.Store(id="sm-tf-store", data="12_1"),
            dcc.Store(id="sm-active-store", data=None),
            dcc.Store(id="sm-open-store", data=None),
            html.Div(
                _render_main(agg, "12_1", None, None),
                id="sm-main",
            ),
            html.Div(_legacy_section(), className="mt-5"),
        ]
    )


# ── Callbacks ──────────────────────────────────────────────────────────────


@callback(
    Output("sm-tf-store", "data"),
    Input({"type": "sm-tf", "value": ALL}, "n_clicks"),
    State("sm-tf-store", "data"),
    prevent_initial_call=True,
)
def _on_tf_click(n_clicks_list, current):
    if not n_clicks_list or not any(n_clicks_list):
        return no_update
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict):
        return no_update
    return triggered.get("value", current)


@callback(
    Output("sm-active-store", "data", allow_duplicate=True),
    Input({"type": "sm-pick", "sector": ALL}, "n_clicks"),
    State("sm-active-store", "data"),
    prevent_initial_call=True,
)
def _on_pick_sector(n_clicks_list, current):
    if not n_clicks_list or not any(n_clicks_list):
        return no_update
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict):
        return no_update
    sector = triggered.get("sector")
    if sector == current:
        return None
    return sector


@callback(
    Output("sm-open-store", "data"),
    Input({"type": "sm-open", "sector": ALL}, "n_clicks"),
    State("sm-open-store", "data"),
    prevent_initial_call=True,
)
def _on_open_row(n_clicks_list, current):
    if not n_clicks_list or not any(n_clicks_list):
        return no_update
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict):
        return no_update
    sector = triggered.get("sector")
    if sector == current:
        return None
    return sector


@callback(
    Output("sm-active-store", "data", allow_duplicate=True),
    Input("sm-close-drill", "n_clicks"),
    prevent_initial_call=True,
)
def _on_close_drill(n_clicks):
    if not n_clicks:
        return no_update
    return None


@callback(
    Output("sm-main", "children"),
    Input("sm-tf-store", "data"),
    Input("sm-active-store", "data"),
    Input("sm-open-store", "data"),
)
def _render(tf, active, open_):
    df = STATE.scored
    history = load_sector_score_history() if not df.empty else None
    agg = (
        aggregate_sectors(df, history=history, score_col=_score_col())
        if not df.empty
        else []
    )
    return _render_main(agg, tf or "12_1", active, open_)


# Legacy CSV-Upload (unverändert).
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
        return _legacy_body(load_sector_snapshots(12)), ""

    try:
        _, b64 = contents.split(",", 1)
        raw = base64.b64decode(b64)
    except Exception as exc:  # noqa: BLE001
        return _legacy_body(load_sector_snapshots(12)), dbc.Alert(
            f"Fehler beim Decodieren: {exc}", color="danger"
        )

    try:
        parsed = load_sector_csv(raw)
    except Exception as exc:  # noqa: BLE001
        return _legacy_body(load_sector_snapshots(12)), dbc.Alert(
            f"Fehler beim Parsen der CSV: {exc}", color="danger"
        )

    if parsed.empty:
        return _legacy_body(load_sector_snapshots(12)), dbc.Alert(
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

    return _legacy_body(load_sector_snapshots(12)), alert


register_page(
    __name__,
    path="/sektor-momentum",
    name="Sektor-Momentum",
    layout=layout,
)
