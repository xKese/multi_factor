"""M&S-Portfolio-Monitor (entspricht Sheet ``M&S Portfolio``).

Re-Design analog Dashboard/Momentum-Monitor (Claude-Design-Handoff):
- Portfolio-Pflege per Koyfin-Watchlist-Upload (Ticker-Liste, persistiert)
- Handlungs-Flags: Positionen mit SELL/Filter-Fail, Death Cross, unter
  SMA-200, ermüdeter Trend-Phase oder frischem Signalwechsel
- Portfolio vs. Universum: Faktor-Profil und Empfehlungs-Verteilung
- Vollständige Positions-Tabelle mit allen Modell-Kennzahlen
"""

from __future__ import annotations

import base64

import pandas as pd
from dash import Input, Output, State, callback, dcc, html, no_update, register_page

from app.core.persistence import save_ms_portfolio
from app.core.portfolio import (
    FLAG_BEARISH,
    FLAG_DEATH,
    FLAG_FILTER,
    FLAG_NEW,
    FLAG_SELL,
    FLAG_TIRED,
    build_flags,
    load_portfolio_csv,
)
from app.core.signal_events import load_signal_events
from app.core.state import STATE
from app.pages.common import format_scored
from app.ui import fmt_de, fmt_percent
from app.ui.formatters import fmt_int


BULLISH_SIGNALS = {"✓ GOLDEN CROSS", "● Kurs > SMA-200"}
BEARISH_SIGNALS = {"⚠ DEATH CROSS", "▼ Kurs < SMA-200"}

SIGNAL_CLASS = {
    "⚠ DEATH CROSS": "f",
    "▼ Kurs < SMA-200": "c",
    "● Kurs > SMA-200": "bp",
    "✓ GOLDEN CROSS": "a",
}

PHASE_CLASS = {
    "Frisch bullish": "a",
    "Etabliert bullish": "bp",
    "Ermüdet bullish": "c",
    "Ermüdet bearish": "c",
    "Etabliert bearish": "d",
    "Frisch bearish": "f",
}

_REC_CLASS = {
    "STRONG BUY": "strong",
    "BUY": "buy",
    "HOLD": "hold",
    "SELL": "sell",
    "Filter nicht bestanden": "fail",
}

_REC_SEGMENTS = [
    ("STRONG BUY", "strong", "STRONG"),
    ("BUY", "buy", "BUY"),
    ("HOLD", "hold", "HOLD"),
    ("SELL", "sell", "SELL"),
]

_FACTORS = [
    ("Value",    "value_score",    "is-deep"),
    ("Quality",  "quality_score",  ""),
    ("Growth",   "growth_score",   "is-deep"),
    ("Momentum", "momentum_score", ""),
    ("Low Vol",  "lowvol_score",   "is-gold"),
]

# Flag → Chip-Darstellung in der Flag-Spalte.
FLAG_CHIP = {
    FLAG_SELL: ("ms-tt-rec", "is-sell"),
    FLAG_FILTER: ("ms-tt-rec", "is-fail"),
    FLAG_DEATH: ("ms-score-pill", "is-f"),
    FLAG_BEARISH: ("ms-score-pill", "is-c"),
    FLAG_TIRED: ("ms-score-pill", "is-c"),
    FLAG_NEW: ("ms-badge is-warn ms-tt-badge", ""),
}

MISSING_LIST_CAP = 10


# ── Format-/Render-Hilfen (Konvention: lokale Kopien je Seite) ─────────────

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


def _fmt_pp(value: float, decimals: int = 1) -> str:
    if value is None or pd.isna(value):
        return "–"
    sign = "+" if value > 0 else ("−" if value < 0 else "")
    return f"{sign}{fmt_de(abs(float(value)), decimals)} %"


def _stand_str(df: pd.DataFrame) -> str:
    if "export_date" not in df.columns:
        return "—"
    series = df["export_date"].dropna()
    if series.empty:
        return "—"
    try:
        return pd.to_datetime(series.iloc[0]).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return str(series.iloc[0])


def _import_str() -> str | None:
    ts = STATE.ms_portfolio_imported_at
    if ts is None:
        return None
    try:
        return pd.to_datetime(ts).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return str(ts)


def _ticker_link(ticker, uid=None) -> html.Td:
    t = str(ticker or "—")
    target = str(uid) if isinstance(uid, str) and uid else t
    return html.Td(
        html.A(t, href=f"/einzelanalyse?ticker={target}", className="ms-tt-tk")
    )


def _score_pill(score) -> html.Td:
    if score is None or pd.isna(score):
        return html.Td("–", className="is-num")
    from app.ui.score_context import class_of_score

    cls = class_of_score(float(score))
    return html.Td(
        html.Span(fmt_de(float(score), 1), className=f"ms-score-pill is-{cls['cls']}"),
        className="is-num",
    )


def _rec_cell(rec) -> html.Td:
    rec = str(rec or "–")
    rec_cls = _REC_CLASS.get(rec, "fail")
    rec_short = "FAIL" if rec == "Filter nicht bestanden" else rec
    return html.Td(html.Span(rec_short, className=f"ms-tt-rec is-{rec_cls}"))


_ZONE_CLASS = {
    "KANDIDAT": "strong",
    "HALTEN": "hold",
    "VERKAUFEN": "sell",
    "FILTER": "fail",
}


def _zone_cell(zone) -> html.Td:
    zone = str(zone or "–")
    cls = _ZONE_CLASS.get(zone, "fail")
    return html.Td(html.Span(zone, className=f"ms-tt-rec is-{cls}"))


def _score_col() -> str:
    from app.ui.score_context import is_v2

    return "composite_score" if is_v2() else "total_score"


def _score_cell(r) -> html.Td:
    return _score_pill(r.get(_score_col()))


def _action_cell(r) -> html.Td:
    from app.ui.score_context import is_v2

    if is_v2():
        return _zone_cell(r.get("zone_v2"))
    return _rec_cell(r.get("recommendation"))


def _action_header() -> str:
    from app.ui.score_context import is_v2

    return "Zone" if is_v2() else "Empfehlung"


def _signal_chip(sig, is_new=False) -> html.Td:
    sig = str(sig or "–")
    cls = SIGNAL_CLASS.get(sig)
    children: list = []
    if cls is None:
        children.append(sig)
    else:
        children.append(html.Span(sig, className=f"ms-score-pill is-{cls}"))
    if is_new:
        children.append(html.Span("NEU", className="ms-badge is-warn ms-tt-badge"))
    return html.Td(children, className="" if cls else "ms-tt-muted")


def _phase_chip(phase) -> html.Td:
    phase = str(phase or "–")
    cls = PHASE_CLASS.get(phase)
    if cls is None:
        return html.Td(phase, className="ms-tt-muted")
    return html.Td(html.Span(phase, className=f"ms-score-pill is-{cls}"))


def _since_cell(row: pd.Series) -> html.Td:
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


def _mini_factor_bars(row: pd.Series) -> html.Td:
    bars = []
    for i, (_label, col, _tone) in enumerate(_FACTORS):
        v = float(row[col]) if col in row and pd.notna(row[col]) else 0.0
        if i in (0, 2):
            bar_cls = "is-muted"
        elif i == 4:
            bar_cls = "is-gold"
        else:
            bar_cls = ""
        height = max(2.0, v / 100.0 * 18.0)
        bars.append(html.Span(className=bar_cls, style={"height": f"{height}px"}))
    return html.Td(html.Span(bars, className="ms-mini-fac"))


# ── Datenaufbau ────────────────────────────────────────────────────────────

def _portfolio_view(scored: pd.DataFrame, resolved: pd.DataFrame) -> pd.DataFrame:
    """Scored-Zeilen der aufgelösten Portfolio-Positionen inkl. Signal-Events.

    Match über die uid statt ``ticker.isin(...)`` — bei Ticker-Kollisionen
    (z. B. zwei "SAN") zieht eine gehaltene Position sonst beide
    Universums-Zeilen in die Portfolio-Sicht (Doppelzählung)."""
    ok_uids = set(resolved.loc[resolved["status"] == "ok", "uid"].astype(str))
    key = "uid" if "uid" in scored.columns else "ticker"
    view = scored[scored[key].astype(str).isin(ok_uids)]
    events = load_signal_events(scored)
    if not events.empty:
        event_key = "uid" if "uid" in events.columns else "ticker"
        view = view.merge(
            events[[event_key, "is_new", "state_since", "days_in_state"]],
            left_on=key,
            right_on=event_key,
            how="left",
            suffixes=("", "_ev"),
        )
        if event_key != key and f"{event_key}_ev" in view.columns:
            view = view.drop(columns=[f"{event_key}_ev"])
    return format_scored(view)


def _missing_tickers(resolved: pd.DataFrame) -> list[str]:
    return resolved.loc[resolved["status"] == "missing", "ticker"].astype(str).tolist()


def _ambiguous_entries(resolved: pd.DataFrame) -> list[str]:
    """Positionen, deren Ticker mehrfach im Universum vorkommt und die per
    Name nicht eindeutig zugeordnet werden konnten."""
    rows = resolved.loc[resolved["status"] == "ambiguous"]
    out = []
    for _, r in rows.iterrows():
        name = str(r.get("name") or "")
        out.append(f"{r['ticker']} ({name})" if name else str(r["ticker"]))
    return out


def _missing_label(ticker: str) -> str:
    name = STATE.ms_portfolio_names.get(ticker, "")
    return f"{ticker} ({name})" if name else ticker


# ── Bausteine ──────────────────────────────────────────────────────────────

def _upload_card() -> html.Div:
    return html.Div(
        [
            html.H3(
                [
                    "Portfolio aktualisieren ",
                    html.Span(
                        "Koyfin-Watchlist-CSV · nur Ticker-Spalte nötig",
                        className="ms-card-h-meta",
                    ),
                ],
                className="ms-card-h",
            ),
            dcc.Upload(
                id="pf-upload",
                children=html.Div(
                    [
                        "Datei hierher ziehen oder ",
                        html.A("klicken zum Auswählen"),
                    ]
                ),
                className="ms-upload",
                multiple=False,
            ),
            html.Div(id="pf-upload-status", className="ms-portfolio-feedback"),
        ],
        className="ms-card",
        style={"marginTop": "16px"},
    )


def _hero(
    scored: pd.DataFrame,
    view: pd.DataFrame,
    missing: list[str],
    ambiguous: list[str] | None = None,
) -> html.Div:
    n_pos = len(STATE.ms_portfolio)
    n_in_universe = len(view)
    flags = build_flags(view)
    flag_key = "uid" if "uid" in getattr(flags, "columns", []) else "ticker"
    n_flagged = flags[flag_key].nunique() if not flags.empty else 0

    from app.ui.score_context import class_of_score

    score_col = _score_col()
    pf_avg = (
        float(view[score_col].dropna().mean())
        if not view.empty and score_col in view.columns
        else float("nan")
    )
    uni_avg = (
        float(scored[score_col].dropna().mean())
        if not scored.empty and score_col in scored.columns
        else float("nan")
    )
    cls = class_of_score(pf_avg)
    delta = pf_avg - uni_avg if pd.notna(pf_avg) and pd.notna(uni_avg) else None

    meta: list = [
        html.Span([html.Strong(fmt_int(n_pos)), " Positionen"]),
        html.Span("·", className="ms-sep"),
        html.Span([html.Strong(fmt_int(n_in_universe)), " im Universum"]),
        html.Span("·", className="ms-sep"),
    ]
    if n_flagged > 0:
        meta.append(
            html.Span(
                f"{fmt_int(n_flagged)} Handlungs-Flags",
                className="ms-badge is-warn",
            )
        )
    else:
        meta.append(html.Span("Keine Handlungs-Flags"))
    if missing:
        meta.append(html.Span("·", className="ms-sep"))
        meta.append(
            html.Span(
                f"{fmt_int(len(missing))} fehlen im Universum",
                className="ms-badge is-warn",
            )
        )
    if ambiguous:
        meta.append(html.Span("·", className="ms-sep"))
        meta.append(
            html.Span(
                f"{fmt_int(len(ambiguous))} mehrdeutig (Ticker-Kollision)",
                className="ms-badge is-warn",
            )
        )

    eyebrow = f"M&S Portfolio · Stand {_stand_str(scored)}"
    imported = _import_str()
    if imported:
        eyebrow += f" · Import vom {imported}"

    if delta is None:
        delta_span = html.Span(["Δ ", html.Strong("–")])
    else:
        sign = "+" if delta >= 0 else "−"
        delta_span = html.Span(
            [
                "Δ ",
                html.Strong(
                    f"{sign}{fmt_de(abs(delta), 1)}",
                    style={
                        "color": "var(--ms-up)" if delta >= 0 else "var(--ms-down)"
                    },
                ),
            ]
        )

    return html.Div(
        [
            html.Div(
                [
                    html.Div(eyebrow, className="ms-hero-eyebrow"),
                    html.H1("M&S Portfolio", className="ms-hero-title"),
                    html.P(
                        "Die Modell-Sicht auf den Bestand – Scores, Signale "
                        "und Handlungsbedarf je Position.",
                        className="ms-hero-subhead",
                    ),
                    html.Div(meta, className="ms-hero-meta"),
                ]
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Span("–" if pd.isna(pf_avg) else fmt_de(pf_avg, 1)),
                            html.Span(" / 100", className="ms-score-suf"),
                        ],
                        className="ms-score-num",
                    ),
                    html.Div(
                        f"Ø {cls['code']} – {cls['label']}",
                        className="ms-score-class",
                    ),
                    html.Div(
                        [
                            html.Span(
                                [
                                    "Universum Ø ",
                                    html.Strong(
                                        "–" if pd.isna(uni_avg) else fmt_de(uni_avg, 1)
                                    ),
                                ]
                            ),
                            delta_span,
                        ],
                        className="ms-score-ctx",
                    ),
                ],
                className="ms-hero-score",
            ),
        ],
        className="ms-hero",
    )


def _flag_chips(flags: list[str]) -> html.Td:
    chips = []
    for f in flags:
        base, tone = FLAG_CHIP.get(f, ("ms-score-pill", "is-c"))
        chips.append(
            html.Span(
                f, className=f"{base} {tone}".strip(), style={"marginRight": "4px"}
            )
        )
    return html.Td(chips)


def _flags_section(view: pd.DataFrame) -> list:
    flags = build_flags(view)
    n_total = len(view)

    head = html.Div(
        [
            html.Div(
                [
                    html.Div("Handlungsbedarf", className="ms-eyebrow"),
                    html.H2("Handlungs-Flags"),
                ]
            ),
            html.Div(
                f"{fmt_int(len(flags))} von {fmt_int(n_total)} Positionen auffällig",
                className="ms-meta",
            ),
        ],
        className="ms-dash-section",
    )

    foot = html.Div(
        "SELL — Modell-Empfehlung Verkaufen · FILTER-FAIL — Qualitätsfilter "
        "(Piotroski/Altman/Market Cap) nicht bestanden · DEATH CROSS — Kurs < "
        "SMA-50 und < SMA-200 · UNTER SMA-200 — Kurs unter der 200-Tage-Linie · "
        "ERMÜDET — Trend-Phase kippt (Kurs kreuzt SMA-50 gegen den Trend oder "
        "1M-Return dreht) · SIGNAL NEU — Signalwechsel seit dem letzten Import. "
        "Sortiert nach Dringlichkeit, dann Score aufsteigend.",
        className="ms-rrg-foot",
    )

    if flags.empty:
        return [
            head,
            html.Div(
                "Keine Handlungs-Flags — alle Positionen unauffällig.",
                className="ms-empty-eyebrow",
                style={"padding": "16px 0"},
            ),
            foot,
        ]

    rows = []
    for _, r in flags.iterrows():
        rows.append(
            html.Tr(
                [
                    _ticker_link(r["ticker"], r.get("uid")),
                    html.Td(str(r.get("name") or "—")),
                    _flag_chips(r["flags"]),
                    _score_cell(r),
                    _action_cell(r),
                    _signal_chip(r.get("sma_signal"), bool(r.get("is_new"))),
                    _phase_chip(r.get("trend_phase")),
                    _since_cell(r),
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
                            html.Th("Flags"),
                            html.Th("Score", className="is-num"),
                            html.Th(_action_header()),
                            html.Th("Signal"),
                            html.Th("Phase"),
                            html.Th("Seit"),
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
    return [head, table, foot]


def _factor_compare_card(view: pd.DataFrame, scored: pd.DataFrame) -> html.Div:
    cols = []
    for label, col, tone in _FACTORS:
        pf_v = (
            float(view[col].dropna().mean())
            if col in view.columns and not view.empty
            else float("nan")
        )
        uni_v = (
            float(scored[col].dropna().mean())
            if col in scored.columns and not scored.empty
            else float("nan")
        )
        pf_h = 0.0 if pd.isna(pf_v) else max(0.0, min(100.0, pf_v))
        uni_h = 0.0 if pd.isna(uni_v) else max(0.0, min(100.0, uni_v))
        delta = pf_v - uni_v if pd.notna(pf_v) and pd.notna(uni_v) else None
        if delta is None:
            delta_str, delta_cls = "Δ –", ""
        else:
            sign = "+" if delta >= 0 else "−"
            delta_str = f"Δ {sign}{fmt_de(abs(delta), 1)}"
            delta_cls = "ms-up" if delta >= 0 else "ms-down"
        cols.append(
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                className=f"ms-fc-bar {tone}".strip(),
                                style={"height": f"{pf_h}%"},
                            ),
                            html.Div(
                                className="ms-fc-bar is-uni",
                                style={"height": f"{uni_h}%"},
                            ),
                        ],
                        className="ms-fc-bar-wrap is-duo",
                    ),
                    html.Div(
                        f"{'–' if pd.isna(pf_v) else fmt_de(pf_v, 0)} · "
                        f"{'–' if pd.isna(uni_v) else fmt_de(uni_v, 0)}",
                        className="ms-fc-val",
                    ),
                    html.Div(label, className="ms-fc-name"),
                    html.Div(delta_str, className=f"ms-fc-w {delta_cls}".strip()),
                ],
                className="ms-fc",
            )
        )
    return html.Div(
        [
            html.H3(
                [
                    "Faktor-Profil (v1) ",
                    html.Span("Portfolio · Universum", className="ms-card-h-meta"),
                ],
                className="ms-card-h",
            ),
            html.Div(cols, className="ms-factor-cols"),
        ],
        className="ms-card",
    )


_ZONE_SEGMENTS = [
    ("KANDIDAT", "strong", "Kandidat"),
    ("HALTEN", "hold", "Halten"),
    ("VERKAUFEN", "sell", "Verkaufen"),
]


def _dist_segments() -> tuple[list, str, str]:
    """(Segmente, Wertespalte, Fail-Wert) der Verteilungsbalken je Version."""
    from app.ui.score_context import is_v2

    if is_v2():
        return _ZONE_SEGMENTS, "zone_v2", "FILTER"
    return _REC_SEGMENTS, "recommendation", "Filter nicht bestanden"


def _rec_bar(df: pd.DataFrame) -> html.Div:
    segments, col, _fail = _dist_segments()
    counts = (
        df[col].value_counts()
        if not df.empty and col in df.columns
        else pd.Series(dtype=int)
    )
    qualified = sum(int(counts.get(rec, 0)) for rec, _, _ in segments)

    def _seg(rec: str, klass: str, label: str) -> html.Div:
        v = int(counts.get(rec, 0))
        width = (v / qualified) * 100 if qualified else 0.0
        text = f"{v} {label}" if width > 12 else (str(v) if width > 5 else "")
        return html.Div(
            text,
            className=f"ms-rec-seg is-{klass}",
            style={"width": f"{width}%"},
        )

    return html.Div(
        [_seg(rec, klass, label) for rec, klass, label in segments],
        className="ms-rec-bar",
    )


def _rec_compare_card(view: pd.DataFrame, scored: pd.DataFrame) -> html.Div:
    _segments, col, fail_value = _dist_segments()
    counts = (
        view[col].value_counts()
        if not view.empty and col in view.columns
        else pd.Series(dtype=int)
    )
    n_fail = int(counts.get(fail_value, 0))
    sig = view.get("sma_signal", pd.Series(dtype=str)).astype(str)
    n_bull = int(sig.isin(BULLISH_SIGNALS).sum())
    n_bear = int(sig.isin(BEARISH_SIGNALS).sum())

    legend = [
        html.Span(
            [
                html.Span(className="ms-rl-sw"),
                f"{label.title()} ",
                html.Strong(fmt_int(int(counts.get(rec, 0)))),
            ],
            className=f"ms-rl-it is-{klass}",
        )
        for rec, klass, label in _segments
    ]
    legend.append(
        html.Span(
            [
                html.Span(className="ms-rl-sw"),
                "Filter nicht bestanden ",
                html.Strong(fmt_int(n_fail)),
            ],
            className="ms-rl-it is-fail",
        )
    )

    return html.Div(
        [
            html.H3(
                [
                    (
                        "Zonen-Verteilung (v2) "
                        if col == "zone_v2"
                        else "Empfehlungs-Verteilung "
                    ),
                    html.Span(
                        f"{fmt_int(n_bull)} bullish · {fmt_int(n_bear)} bearish "
                        "im Portfolio",
                        className="ms-card-h-meta",
                    ),
                ],
                className="ms-card-h",
            ),
            html.Div("Portfolio", className="ms-pf-bar-lbl"),
            _rec_bar(view),
            html.Div("Universum", className="ms-pf-bar-lbl"),
            _rec_bar(scored),
            html.Div(legend, className="ms-rec-legend"),
        ],
        className="ms-card",
    )


def _positions_section(
    view: pd.DataFrame, missing: list[str], ambiguous: list[str] | None = None
) -> list:
    head = html.Div(
        [
            html.Div(
                [
                    html.Div("Bestand", className="ms-eyebrow"),
                    html.H2("Alle Positionen"),
                ]
            ),
            html.Div(
                f"{fmt_int(len(view))} Titel · sortiert nach "
                + (
                    "Composite-Score (v2)"
                    if _score_col() == "composite_score"
                    else "Gesamt-Score"
                ),
                className="ms-meta",
            ),
        ],
        className="ms-dash-section",
    )

    children: list = [head]
    if view.empty:
        children.append(
            html.Div(
                "Keine Portfolio-Position im Universum gefunden.",
                className="ms-empty-eyebrow",
                style={"padding": "16px 0"},
            )
        )
    else:
        sort_col = _score_col() if _score_col() in view.columns else "total_score"
        data = view.sort_values(sort_col, ascending=False, na_position="last")
        rows = []
        for _, r in data.iterrows():
            ret_12m = r.get("ret_12m")
            ret_str = fmt_percent(float(ret_12m), 1) if pd.notna(ret_12m) else "–"
            ret_cls = (
                "ms-up" if (pd.notna(ret_12m) and float(ret_12m) >= 0) else "ms-down"
            )
            rows.append(
                html.Tr(
                    [
                        _ticker_link(r["ticker"], r.get("uid")),
                        html.Td(str(r.get("name") or "—")),
                        html.Td(str(r.get("sector") or "—"), className="ms-tt-muted"),
                        _score_cell(r),
                        _mini_factor_bars(r),
                        _action_cell(r),
                        _signal_chip(r.get("sma_signal"), bool(r.get("is_new"))),
                        _phase_chip(r.get("trend_phase")),
                        _since_cell(r),
                        html.Td(ret_str, className=f"is-num {ret_cls}"),
                        _dist_cell(r.get("sma_200_distance")),
                    ]
                )
            )
        children.append(
            html.Div(
                html.Table(
                    [
                        html.Thead(
                            html.Tr(
                                [
                                    html.Th("Ticker"),
                                    html.Th("Name"),
                                    html.Th("Sektor"),
                                    html.Th("Score", className="is-num"),
                                    html.Th("Faktor-Profil (v1)"),
                                    html.Th(_action_header()),
                                    html.Th("Signal"),
                                    html.Th("Phase"),
                                    html.Th("Seit"),
                                    html.Th("12M", className="is-num"),
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
        )

    if missing:
        shown = ", ".join(_missing_label(t) for t in missing[:MISSING_LIST_CAP])
        if len(missing) > MISSING_LIST_CAP:
            shown += ", …"
        children.append(
            html.Div(f"Nicht im Universum: {shown}", className="ms-rrg-foot")
        )
    if ambiguous:
        shown = ", ".join(ambiguous[:MISSING_LIST_CAP])
        if len(ambiguous) > MISSING_LIST_CAP:
            shown += ", …"
        children.append(
            html.Div(
                "Mehrdeutig — Ticker mehrfach im Universum, per Name nicht "
                f"zuzuordnen (Name in der Watchlist ergänzen): {shown}",
                className="ms-rrg-foot",
            )
        )
    return children


def _empty_universe() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div("M&S Portfolio", className="ms-empty-eyebrow"),
                    html.H2("Kein Universum geladen", className="ms-empty-title"),
                    html.P(
                        "Das Portfolio wird gespeichert und angewendet, sobald "
                        "ein Koyfin-Universum importiert ist — erst dann können "
                        "Scores, Signale und Flags berechnet werden.",
                        className="ms-empty-sub",
                    ),
                    html.Div(
                        dcc.Link(
                            "Universum importieren",
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


# ── Layout & Render ────────────────────────────────────────────────────────

def _render_main() -> list:
    scored = STATE.scored
    tickers = STATE.ms_portfolio

    if scored.empty:
        return [_empty_universe()]
    if not tickers:
        return [
            html.Div(
                [
                    html.Div("M&S Portfolio", className="ms-empty-eyebrow"),
                    html.H2(
                        "Noch kein Portfolio geladen", className="ms-empty-title"
                    ),
                    html.P(
                        "Lade eine Koyfin-Watchlist als CSV hoch, um die "
                        "Modell-Sicht auf den Bestand zu erhalten.",
                        className="ms-empty-sub",
                    ),
                ],
                className="ms-empty-card",
            )
        ]

    resolved = STATE.resolve_portfolio()
    view = _portfolio_view(scored, resolved)
    missing = _missing_tickers(resolved)
    ambiguous = _ambiguous_entries(resolved)

    return [
        _hero(scored, view, missing, ambiguous),
        html.Div(
            [
                _factor_compare_card(view, scored),
                _rec_compare_card(view, scored),
            ],
            className="ms-row ms-r-2",
            style={"marginTop": "16px"},
        ),
        *_flags_section(view),
        *_positions_section(view, missing, ambiguous),
    ]


def layout(**_) -> html.Div:
    return html.Div(
        [
            html.Div(_render_main(), id="pf-main"),
            _upload_card(),
        ]
    )


# ── Callback ───────────────────────────────────────────────────────────────

@callback(
    Output("pf-main", "children"),
    Output("pf-upload-status", "children"),
    Output("pf-upload-status", "className"),
    Input("pf-upload", "contents"),
    State("pf-upload", "filename"),
    prevent_initial_call=True,
)
def _on_upload(contents: str | None, filename: str | None):
    base = "ms-portfolio-feedback"
    if not contents:
        return no_update, no_update, no_update
    try:
        _, b64 = contents.split(",", 1)
        raw = base64.b64decode(b64)
        df = load_portfolio_csv(raw)
    except Exception as exc:  # noqa: BLE001
        return no_update, f"Fehler: {exc}", f"{base} is-warn"

    STATE.set_ms_portfolio(df, imported_at=pd.Timestamp.now())

    parts = [f"{fmt_int(len(df))} Positionen aus {filename or 'Upload'} übernommen."]
    warn = False
    try:
        save_ms_portfolio(df)
    except Exception as exc:  # noqa: BLE001
        parts.append(
            f"DB-Speicherung fehlgeschlagen ({exc}) — Portfolio nur in "
            "dieser Session."
        )
        warn = True

    scored = STATE.scored
    if scored.empty:
        parts.append("Kein Universum geladen — Kennzahlen folgen nach dem Import.")
        warn = True
    else:
        resolved = STATE.resolve_portfolio()
        missing = _missing_tickers(resolved)
        if missing:
            shown = ", ".join(missing[:MISSING_LIST_CAP])
            if len(missing) > MISSING_LIST_CAP:
                shown += ", …"
            parts.append(f"{fmt_int(len(missing))} nicht im Universum: {shown}")
            warn = True
        ambiguous = _ambiguous_entries(resolved)
        if ambiguous:
            shown = ", ".join(ambiguous[:MISSING_LIST_CAP])
            if len(ambiguous) > MISSING_LIST_CAP:
                shown += ", …"
            parts.append(
                f"{fmt_int(len(ambiguous))} mehrdeutig (Ticker-Kollision): {shown}"
            )
            warn = True

    status_cls = f"{base} {'is-warn' if warn else 'is-ok'}"
    return _render_main(), " · ".join(parts), status_cls


register_page(__name__, path="/portfolios", name="M&S Portfolio", layout=layout)
