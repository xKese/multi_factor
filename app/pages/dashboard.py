"""Dashboard-Seite — Variante A "Quote" im M&S-Brand-Refresh.

Re-Design auf Basis des Claude-Design-Handoffs (Multi-Faktor Dashboard.html):
- Quote-Hero mit Ø-Score und Klassifikations-Kontext
- Markt-Regime-Streifen (aus Faktor-Mittelwerten abgeleitet)
- Empfehlungs-Verteilung als gestapelter Balken
- 3 Spalten: Faktor-Säulen · Sektor-Ranking · Bewegungen
- Top-N-Tabelle mit Mini-Faktor-Profil je Zeile
"""

from __future__ import annotations

import dash_bootstrap_components as dbc
import pandas as pd
from dash import ALL, Input, Output, State, callback, ctx, dcc, html, no_update, register_page

from app.core.state import STATE
from app.ui import fmt_de, fmt_percent
from app.ui.formatters import fmt_int


# ── Score → Klassifikations-Mapping (siehe data.js classOf) ────────────────

def _class_of(score: float) -> dict:
    """Score → {code, label, cls}. Spiegelt das Mapping aus dem Design-Prototyp."""
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


_REC_CLASS = {
    "STRONG BUY": "strong",
    "BUY": "buy",
    "HOLD": "hold",
    "SELL": "sell",
    "Filter nicht bestanden": "fail",
}


# ── Faktor-Definitionen (Reihenfolge wie im Design) ────────────────────────

_FACTORS = [
    ("Value",    "value_score",    "value",    "is-deep"),
    ("Quality",  "quality_score",  "quality",  ""),
    ("Growth",   "growth_score",   "growth",   "is-deep"),
    ("Momentum", "momentum_score", "momentum", ""),
    ("Low Vol",  "lowvol_score",   "lowvol",   "is-gold"),
]


# ── Empty-State (unverändert übernommen) ───────────────────────────────────

def _empty_state() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Erste Schritte", className="ms-empty-eyebrow"),
                    html.H2("Noch keine Daten geladen", className="ms-empty-title"),
                    html.P(
                        "Lade einen Koyfin-CSV-Export hoch, um Dashboard, "
                        "Einzelanalyse und Momentum-Monitor zu befüllen.",
                        className="ms-empty-sub",
                    ),
                    html.Div(
                        [
                            dcc.Link(
                                "CSV hochladen",
                                href="/daten-import",
                                className="btn btn-primary ms-empty-cta",
                            ),
                            dcc.Link(
                                "Anleitung lesen",
                                href="/anleitung",
                                className="btn btn-outline-secondary ms-empty-cta",
                            ),
                        ],
                        className="ms-empty-actions",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div("Erwartetes Format", className="ms-empty-hint-title"),
                                    html.Div(
                                        "Koyfin-Screener-Export · Semikolon-getrennt · 57 Spalten · "
                                        "erste zwei Zeilen Metadaten",
                                        className="ms-empty-hint-body",
                                    ),
                                ]
                            ),
                            html.Div(
                                [
                                    html.Div("Was passiert dann?", className="ms-empty-hint-title"),
                                    html.Div(
                                        "Perzentil-Ränge, Faktor-Scores und Empfehlungen werden "
                                        "automatisch berechnet. Filter und Gewichte sind änderbar.",
                                        className="ms-empty-hint-body",
                                    ),
                                ]
                            ),
                        ],
                        className="ms-empty-hints",
                    ),
                ],
                className="ms-empty-card",
            )
        ],
        className="ms-empty-wrap",
    )


# ── Bausteine ──────────────────────────────────────────────────────────────

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


def _hero(df: pd.DataFrame) -> html.Div:
    n = len(df)
    n_filter_ok = int((df["filter_ok"] == "JA").sum())
    avg_score = float(df["total_score"].dropna().mean())
    cls = _class_of(avg_score)
    sma_signal = df.get("sma_signal", pd.Series(dtype=str))
    n_golden = int(sma_signal.astype(str).str.contains("GOLDEN", na=False).sum())
    n_death = int(sma_signal.astype(str).str.contains("DEATH", na=False).sum())
    pct_filter = (n_filter_ok / max(n, 1)) * 100
    stand = _stand_str(df)

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        f"Universum · Stand {stand}",
                        className="ms-hero-eyebrow",
                    ),
                    html.H1("Multi-Faktor Übersicht", className="ms-hero-title"),
                    html.P(
                        f"Ein ruhiger Blick auf {fmt_int(n)} Aktien – fünf Faktoren, eine Sicht.",
                        className="ms-hero-subhead",
                    ),
                    html.Div(
                        [
                            html.Span([html.Strong(fmt_int(n)), " Aktien geprüft"]),
                            html.Span("·", className="ms-sep"),
                            html.Span([
                                html.Strong(fmt_int(n_filter_ok)),
                                f" Filter bestanden ({fmt_de(pct_filter, 0)} %)",
                            ]),
                            html.Span("·", className="ms-sep"),
                            html.Span([html.Strong(fmt_int(n_golden)), " Golden Crosses"]),
                            html.Span("·", className="ms-sep"),
                            html.Span([html.Strong(fmt_int(n_death)), " Death Crosses"]),
                        ],
                        className="ms-hero-meta",
                    ),
                ]
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(fmt_de(avg_score, 1)),
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
                            html.Span(["Bestes Quartil ", html.Strong("≥ 70")]),
                            html.Span(["Schlechtestes ", html.Strong("≤ 38")]),
                        ],
                        className="ms-score-ctx",
                    ),
                ],
                className="ms-hero-score",
            ),
        ],
        className="ms-hero",
    )


def _hero_v2(df: pd.DataFrame) -> html.Div:
    """Quote-Hero der Composite-v2-Primäranzeige."""
    from app.ui.score_context import class_of_score

    n = len(df)
    zones = df.get("zone_v2", pd.Series(dtype=object)).value_counts()
    n_kand = int(zones.get("KANDIDAT", 0))
    n_filter = int(zones.get("FILTER", 0))
    n_eligible = n - n_filter
    avg_score = float(df["composite_score"].dropna().mean())
    cls = class_of_score(avg_score)
    pct_eligible = (n_eligible / max(n, 1)) * 100
    stand = _stand_str(df)

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        f"Universum · Stand {stand}",
                        className="ms-hero-eyebrow",
                    ),
                    html.H1("Multi-Faktor Übersicht", className="ms-hero-title"),
                    html.P(
                        f"Ein ruhiger Blick auf {fmt_int(n)} Aktien – "
                        "Composite v2, vier Faktoren, eine Sicht.",
                        className="ms-hero-subhead",
                    ),
                    html.Div(
                        [
                            html.Span([html.Strong(fmt_int(n)), " Aktien geprüft"]),
                            html.Span("·", className="ms-sep"),
                            html.Span([
                                html.Strong(fmt_int(n_eligible)),
                                f" Filter bestanden ({fmt_de(pct_eligible, 0)} %)",
                            ]),
                            html.Span("·", className="ms-sep"),
                            html.Span([html.Strong(fmt_int(n_kand)), " Kandidaten"]),
                            html.Span("·", className="ms-sep"),
                            html.Span([
                                html.Strong(fmt_int(zones.get("VERKAUFEN", 0))),
                                " Verkaufen",
                            ]),
                        ],
                        className="ms-hero-meta",
                    ),
                ]
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(fmt_de(avg_score, 1)),
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
                            html.Span(["Kandidat ab ", html.Strong("Perzentil 80")]),
                            html.Span(["Verkauf unter ", html.Strong("66,7")]),
                        ],
                        className="ms-score-ctx",
                    ),
                ],
                className="ms-hero-score",
            ),
        ],
        className="ms-hero",
    )


def _zone_distribution(df: pd.DataFrame) -> html.Div:
    """Zonen-Verteilung (v2) als gestapelter Balken — analog zur
    v1-Empfehlungs-Verteilung, gleiche Segmentklassen (Farbwelt)."""
    zones = df.get("zone_v2", pd.Series(dtype=object)).value_counts()
    n_kand = int(zones.get("KANDIDAT", 0))
    n_halt = int(zones.get("HALTEN", 0))
    n_verk = int(zones.get("VERKAUFEN", 0))
    n_filt = int(zones.get("FILTER", 0))
    qualified = n_kand + n_halt + n_verk

    def _pct(v: int) -> float:
        return (v / qualified) * 100 if qualified else 0.0

    def _seg(v: int, klass: str, label: str) -> html.Div:
        width = _pct(v)
        text = f"{v} {label}" if v > 8 else ""
        return html.Div(
            text,
            className=f"ms-rec-seg is-{klass}",
            style={"width": f"{width}%"},
        )

    def _leg(v: int, klass: str, label: str) -> html.Span:
        return html.Span(
            [html.Span(className="ms-rl-sw"), f"{label} ", html.Strong(fmt_int(v))],
            className=f"ms-rl-it is-{klass}",
        )

    return html.Div(
        [
            html.H3(
                [
                    "Zonen-Verteilung (Composite v2) ",
                    html.Span(
                        f"{fmt_int(qualified)} eligible Titel · "
                        f"{fmt_int(n_filt)} ausgefiltert",
                        className="ms-card-h-meta",
                    ),
                ],
                className="ms-card-h",
            ),
            html.Div(
                [
                    _seg(n_kand, "strong", "KANDIDAT"),
                    _seg(n_halt, "hold", "HALTEN"),
                    _seg(n_verk, "sell", "VERKAUFEN"),
                ],
                className="ms-rec-bar",
            ),
            html.Div(
                [
                    _leg(n_kand, "strong", "Kandidat"),
                    _leg(n_halt, "hold", "Halten"),
                    _leg(n_verk, "sell", "Verkaufen"),
                    _leg(n_filt, "fail", "Filter"),
                ],
                className="ms-rec-legend",
            ),
        ],
        className="ms-card ms-rec-card",
    )


def _regime_strip(df: pd.DataFrame) -> html.Div:
    """Markt-Regime aus Faktor-Mittelwerten ableiten (heuristisch)."""
    fac_means = []
    for label, col, _key, _tone in _FACTORS:
        if col in df.columns:
            v = df[col].dropna().mean()
            if pd.notna(v):
                fac_means.append((label, float(v)))
    fac_means.sort(key=lambda x: x[1], reverse=True)
    top2 = " · ".join(name for name, _ in fac_means[:2]) or "–"
    bot2 = " · ".join(name for name, _ in fac_means[-2:][::-1]) or "–"

    n = max(len(df), 1)
    pct_filter = (df["filter_ok"] == "JA").sum() / n
    sma_signal = df.get("sma_signal", pd.Series(dtype=str)).astype(str)
    n_golden = int(sma_signal.str.contains("GOLDEN", na=False).sum())
    n_death = int(sma_signal.str.contains("DEATH", na=False).sum())
    cross_balance = (n_golden - n_death) / n
    if pct_filter > 0.55 and cross_balance > 0.05:
        regime = "Risk-On"
    elif pct_filter < 0.40 or cross_balance < -0.05:
        regime = "Risk-Off"
    else:
        regime = "Neutral"

    stand = _stand_str(df)

    return html.Div(
        [
            html.Span("Markt-Regime", className="ms-rg-lab"),
            html.Span(
                [html.Span(className="ms-rg-dot"), regime],
                className="ms-rg-pill",
            ),
            html.Span(className="ms-rg-sep"),
            html.Span("Bevorzugte Faktoren", className="ms-rg-lab"),
            html.Span(top2, className="ms-rg-val"),
            html.Span(className="ms-rg-sep"),
            html.Span("Untergewichten", className="ms-rg-lab"),
            html.Span(bot2, className="ms-rg-val"),
            html.Span(className="ms-rg-sep", style={"marginLeft": "auto"}),
            html.Span("Stand", className="ms-rg-lab"),
            html.Span(stand, className="ms-rg-val"),
        ],
        className="ms-regime-strip",
    )


def _rec_distribution(df: pd.DataFrame) -> html.Div:
    counts = df["recommendation"].value_counts()
    n_strong = int(counts.get("STRONG BUY", 0))
    n_buy = int(counts.get("BUY", 0))
    n_hold = int(counts.get("HOLD", 0))
    n_sell = int(counts.get("SELL", 0))
    n_fail = int(counts.get("Filter nicht bestanden", 0))
    qualified = n_strong + n_buy + n_hold + n_sell

    def _pct(v: int) -> float:
        return (v / qualified) * 100 if qualified else 0.0

    def _seg(v: int, klass: str, label: str) -> html.Div:
        width = _pct(v)
        text = f"{v} {label}" if v > 8 else ""
        return html.Div(
            text,
            className=f"ms-rec-seg is-{klass}",
            style={"width": f"{width}%"},
        )

    return html.Div(
        [
            html.H3(
                [
                    "Empfehlungs-Verteilung ",
                    html.Span(
                        f"{fmt_int(qualified)} qualifizierte Titel · "
                        f"{fmt_int(n_fail)} ausgefiltert",
                        className="ms-card-h-meta",
                    ),
                ],
                className="ms-card-h",
            ),
            html.Div(
                [
                    _seg(n_strong, "strong", "STRONG"),
                    _seg(n_buy,    "buy",    "BUY"),
                    _seg(n_hold,   "hold",   "HOLD"),
                    _seg(n_sell,   "sell",   "SELL"),
                ],
                className="ms-rec-bar",
            ),
            html.Div(
                [
                    html.Span(
                        [html.Span(className="ms-rl-sw"), "Strong Buy ", html.Strong(fmt_int(n_strong))],
                        className="ms-rl-it is-strong",
                    ),
                    html.Span(
                        [html.Span(className="ms-rl-sw"), "Buy ", html.Strong(fmt_int(n_buy))],
                        className="ms-rl-it is-buy",
                    ),
                    html.Span(
                        [html.Span(className="ms-rl-sw"), "Hold ", html.Strong(fmt_int(n_hold))],
                        className="ms-rl-it is-hold",
                    ),
                    html.Span(
                        [html.Span(className="ms-rl-sw"), "Sell ", html.Strong(fmt_int(n_sell))],
                        className="ms-rl-it is-sell",
                    ),
                    html.Span(
                        [html.Span(className="ms-rl-sw"), "Filter nicht bestanden ", html.Strong(fmt_int(n_fail))],
                        className="ms-rl-it is-fail",
                    ),
                ],
                className="ms-rec-legend",
            ),
        ],
        className="ms-card",
        style={"marginTop": "16px"},
    )


def _factor_columns_card(df: pd.DataFrame) -> html.Div:
    weights = STATE.settings.factor_weights
    cols = []
    for label, col, key, tone in _FACTORS:
        v = float(df[col].dropna().mean()) if col in df.columns else 0.0
        w = weights.get(key, 0.0)
        height = max(0.0, min(100.0, v))
        cols.append(
            html.Div(
                [
                    html.Div(
                        html.Div(
                            className=f"ms-fc-bar {tone}".strip(),
                            style={"height": f"{height}%"},
                        ),
                        className="ms-fc-bar-wrap",
                    ),
                    html.Div(fmt_de(v, 0), className="ms-fc-val"),
                    html.Div(label, className="ms-fc-name"),
                    html.Div(f"Gewicht {round(w * 100)} %", className="ms-fc-w"),
                ],
                className="ms-fc",
            )
        )
    return html.Div(
        [
            html.H3("Ø Faktor-Scores im Universum", className="ms-card-h"),
            html.Div(cols, className="ms-factor-cols"),
        ],
        className="ms-card",
    )


def _sector_ranking_card(df: pd.DataFrame, active_sector: str | None) -> html.Div:
    grouped = (
        df.dropna(subset=["total_score"])
        .groupby("sector")["total_score"]
        .agg(["mean", "count"])
        .sort_values("mean", ascending=False)
    )
    rows = []
    for sector, row in grouped.iterrows():
        avg = float(row["mean"])
        n = int(row["count"])
        bar_cls = "" if avg >= 60 else ("is-warn" if avg >= 50 else "is-down")
        rows.append(
            html.Button(
                [
                    html.Span(sector, className="ms-sec-nm", title=sector),
                    html.Span(
                        html.Span(
                            className=bar_cls,
                            style={"width": f"{max(4, min(100, avg))}%"},
                        ),
                        className="ms-sec-bar",
                    ),
                    html.Span(fmt_de(avg, 0), className="ms-sec-v"),
                    html.Span(fmt_int(n), className="ms-sec-n"),
                ],
                className=(
                    "ms-sec-row" + (" is-active" if active_sector == sector else "")
                ),
                id={"type": "dash-sec", "sector": sector},
                n_clicks=0,
            )
        )
    return html.Div(
        [
            html.H3(
                [
                    "Sektor-Ranking ",
                    html.Span("Klick filtert Tabelle", className="ms-card-h-meta"),
                ],
                className="ms-card-h",
            ),
            html.Div(rows, className="ms-sec-list"),
        ],
        className="ms-card",
    )


def _movers_card(df: pd.DataFrame) -> html.Div:
    if "ret_12m" not in df.columns:
        return html.Div(
            [html.H3("Bewegungen", className="ms-card-h"),
             html.Div("Keine 12M-Returns verfügbar.", className="ms-tt-muted")],
            className="ms-card",
        )
    have = df.dropna(subset=["ret_12m"])
    winners = have.sort_values("ret_12m", ascending=False).head(5)
    losers = have.sort_values("ret_12m", ascending=True).head(3)

    def _row(r: pd.Series, tone: str) -> html.A:
        return html.A(
            [
                html.Span(str(r["ticker"]), className="ms-mv-tk"),
                html.Span(
                    [str(r.get("name") or "—"), html.Small(str(r.get("sector") or ""))],
                    className="ms-mv-nm",
                ),
                html.Span(
                    fmt_percent(float(r["ret_12m"]), 1),
                    className=f"ms-mv-v is-{tone}",
                ),
            ],
            href=f"/einzelanalyse?ticker={_uid_of(r)}",
            className="ms-mover",
        )

    children: list = [_row(r, "up") for _, r in winners.iterrows()]
    if not losers.empty:
        children.append(html.Div(className="ms-movers-divider"))
        children.extend(_row(r, "down") for _, r in losers.iterrows())

    return html.Div(
        [
            html.H3(
                [
                    "Bewegungen ",
                    html.Span("Top / Bottom 12M", className="ms-card-h-meta"),
                ],
                className="ms-card-h",
            ),
            html.Div(children, className="ms-movers"),
        ],
        className="ms-card",
    )


def _classification_short(class_str: str) -> str:
    """\"B+ - Sehr Gut\" → \"B+ – Sehr Gut\"."""
    if not isinstance(class_str, str) or "-" not in class_str:
        return class_str or "–"
    code, _, label = class_str.partition("-")
    return f"{code.strip()} – {label.strip()}"


def _uid_of(r: pd.Series) -> str:
    """Link-/Schlüsselwert einer Zeile: uid, Fallback Ticker (eindeutig auch
    bei Ticker-Kollisionen wie zwei "SAN")."""
    return str(r.get("uid") or r.get("ticker") or "")


_AGENT_RATING_CLASS = {
    "Buy": "buy",
    "Overweight": "buy",
    "Hold": "hold",
    "Underweight": "sell",
    "Sell": "sell",
}


def _top_table(df: pd.DataFrame, active_sector: str | None, n: int = 25) -> html.Div:
    src = df if not active_sector else df[df["sector"] == active_sector]
    src = src.dropna(subset=["total_score"]).sort_values("total_score", ascending=False).head(n)

    # Neueste Agenten-Bewertung je Ticker (leer bei DB-Fehler / ohne Analysen).
    agent_ratings: dict[str, str] = {}
    try:
        from app.core.persistence import load_agent_ratings

        ratings_df = load_agent_ratings()
        if not ratings_df.empty:
            agent_ratings = {
                str(t): str(rating)
                for t, rating in ratings_df[["ticker", "rating"]].itertuples(index=False)
                if isinstance(rating, str) and rating
            }
    except Exception:  # noqa: BLE001 — Spalte ist rein additiv
        pass

    rows = []
    for _, r in src.iterrows():
        uid = _uid_of(r)
        # Rating-Lookup: uid zuerst (neue Analysen), Fallback bloßer Ticker
        # (Bestandsanalysen von vor der uid-Einführung).
        rating = agent_ratings.get(uid) or agent_ratings.get(str(r["ticker"]))
        cls = _class_of(float(r["total_score"]))
        rec = str(r.get("recommendation") or "–")
        rec_cls = _REC_CLASS.get(rec, "fail")
        rec_short = "FAIL" if rec == "Filter nicht bestanden" else rec

        # Mini-Faktor-Bars (5 Faktoren)
        bars = []
        for i, (_label, col, _key, _tone) in enumerate(_FACTORS):
            v = float(r[col]) if col in r and pd.notna(r[col]) else 0.0
            if i in (0, 2):       # Value, Growth → muted (Hintergrund)
                bar_cls = "is-muted"
            elif i == 4:           # Low Vol → gold
                bar_cls = "is-gold"
            else:                  # Quality, Momentum → primary green
                bar_cls = ""
            height = max(2.0, v / 100.0 * 18.0)
            bars.append(html.Span(className=bar_cls, style={"height": f"{height}px"}))

        ret_12m = r.get("ret_12m")
        ret_str = fmt_percent(float(ret_12m), 1) if pd.notna(ret_12m) else "–"
        ret_cls = "ms-up" if (pd.notna(ret_12m) and float(ret_12m) >= 0) else "ms-down"

        rows.append(
            html.Tr(
                [
                    html.Td(
                        html.A(str(r["ticker"]), href=f"/einzelanalyse?ticker={uid}",
                               className="ms-tt-tk"),
                    ),
                    html.Td(str(r.get("name") or "—")),
                    html.Td(str(r.get("sector") or "—"), className="ms-tt-muted"),
                    html.Td(
                        html.Span(fmt_de(float(r["total_score"]), 1),
                                  className=f"ms-score-pill is-{cls['cls']}"),
                        className="is-num",
                    ),
                    html.Td(
                        html.Span(_classification_short(r.get("classification")),
                                  className="ms-tt-muted",
                                  style={"fontSize": "11px"}),
                    ),
                    html.Td(html.Span(bars, className="ms-mini-fac")),
                    html.Td(ret_str, className=f"is-num {ret_cls}"),
                    html.Td(
                        html.Span(rec_short, className=f"ms-tt-rec is-{rec_cls}"),
                    ),
                    html.Td(
                        html.Span(
                            rating or "–",
                            className=(
                                "ms-tt-rec is-"
                                + _AGENT_RATING_CLASS.get(rating, "fail")
                            )
                            if rating
                            else "ms-tt-muted",
                        ),
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
                            html.Th("Sektor"),
                            html.Th("Score", className="is-num"),
                            html.Th("Klassif."),
                            html.Th("Faktor-Profil"),
                            html.Th("12M", className="is-num"),
                            html.Th("Empfehlung"),
                            html.Th("Agenten"),
                        ]
                    )
                ),
                html.Tbody(rows),
            ],
            className="ms-toptable",
        ),
        className="ms-toptable-wrap",
    )


def _section_head(active_sector: str | None, n_rows: int) -> html.Div:
    if active_sector:
        title = f"Top-Aktien · {active_sector}"
        meta = html.Span([
            "Filter aktiv – ",
            html.A("zurücksetzen", id="dash-sector-reset", n_clicks=0,
                   style={"cursor": "pointer"}),
        ])
    else:
        title = f"Top-{n_rows} Aktien nach Gesamt-Score"
        meta = "Klick auf Zeile öffnet Einzelanalyse"

    return html.Div(
        [
            html.Div(
                [
                    html.Div("Ranking", className="ms-eyebrow"),
                    html.H2(title),
                ]
            ),
            html.Div(meta, className="ms-meta"),
        ],
        className="ms-dash-section",
    )


# ── Layout ─────────────────────────────────────────────────────────────────

def _v2_overview(df: pd.DataFrame) -> html.Div:
    """Composite-v2-Übersicht (primäre Anzeige bei scoring_version = v2)."""
    from app.pages.common import render_table
    from app.ui.theme import kpi_band, section_header

    zones = df.get("zone_v2", pd.Series(dtype=object)).value_counts()
    avg = df.get("composite_score", pd.Series(dtype=float)).dropna().mean()
    band = kpi_band(
        [
            {"label": "Ø Composite-Score (v2)", "value": fmt_de(avg, 1)},
            {"label": "Kandidaten", "value": fmt_int(zones.get("KANDIDAT", 0)),
             "tone": "up"},
            {"label": "Halten", "value": fmt_int(zones.get("HALTEN", 0))},
            {"label": "Verkaufen", "value": fmt_int(zones.get("VERKAUFEN", 0)),
             "tone": "down"},
            {"label": "Filter", "value": fmt_int(zones.get("FILTER", 0)),
             "tone": "warn"},
        ]
    )
    cols = [
        c
        for c in (
            "ticker", "uid", "name", "sector", "composite_score",
            "classification_v2", "zone_v2", "data_coverage_v2",
        )
        if c in df.columns
    ]
    top = (
        df.dropna(subset=["composite_score"])
        .sort_values(["composite_score", "uid"], ascending=[False, True])
        .head(20)[cols]
        .copy()
    )
    children: list = [
        section_header(
            "Composite v2",
            "Top-Titel nach Composite-Score · Details auf /modellportfolio",
        ),
        band,
    ]
    if STATE.v2_diagnostics:
        from app.core.diagnostics import SEV_ERROR, SEV_WARNING, count_by_severity
        from app.ui.theme import diagnostics_panel

        counts = count_by_severity(STATE.v2_diagnostics)
        if counts.get(SEV_ERROR) or counts.get(SEV_WARNING):
            children.append(diagnostics_panel(STATE.v2_diagnostics))
    children.append(render_table(top, id="dash-v2-table", page_size=20))
    return html.Div(children, className="mb-3")


def layout(**_) -> html.Div:
    df = STATE.scored
    if df.empty:
        return html.Div([_empty_state()])

    v1_blocks = [
        _regime_strip(df),
        _rec_distribution(df),
        html.Div(
            [
                _factor_columns_card(df),
                _sector_ranking_card(df, None),
                _movers_card(df),
            ],
            className="ms-row ms-r-3",
            id="dash-row-3",
        ),
        dcc.Store(id="dash-sector-filter", data=None),
        html.Div(_section_head(None, 25), id="dash-top-section"),
        html.Div(
            _top_table(df, None, 25),
            id="dash-top-table",
        ),
    ]
    if (
        STATE.settings.scoring_version == "v2"
        and "composite_score" in df.columns
    ):
        # v2 primär, v1 als aufklappbarer Vergleichsbereich (Spec 11.2).
        return html.Div(
            [
                _hero_v2(df),
                _zone_distribution(df),
                _v2_overview(df),
                dbc.Accordion(
                    [
                        dbc.AccordionItem(
                            [_hero(df), *v1_blocks],
                            title="Scoring v1 (Vergleich)",
                        )
                    ],
                    start_collapsed=True,
                    className="mb-3",
                ),
            ]
        )
    return html.Div([_hero(df), *v1_blocks])


# ── Callbacks ──────────────────────────────────────────────────────────────

@callback(
    Output("dash-sector-filter", "data", allow_duplicate=True),
    Input({"type": "dash-sec", "sector": ALL}, "n_clicks"),
    State("dash-sector-filter", "data"),
    prevent_initial_call=True,
)
def _on_sector_click(n_clicks_list, current):
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
    Output("dash-sector-filter", "data", allow_duplicate=True),
    Input("dash-sector-reset", "n_clicks"),
    prevent_initial_call=True,
)
def _on_sector_reset(_n_clicks):
    return None


@callback(
    Output("dash-row-3", "children"),
    Output("dash-top-section", "children"),
    Output("dash-top-table", "children"),
    Input("dash-sector-filter", "data"),
    prevent_initial_call=True,
)
def _render_filtered(active_sector):
    df = STATE.scored
    if df.empty:
        return no_update, no_update, no_update
    n = 25
    src = df if not active_sector else df[df["sector"] == active_sector]
    n_rows = min(n, len(src.dropna(subset=["total_score"])))
    return (
        [
            _factor_columns_card(df),
            _sector_ranking_card(df, active_sector),
            _movers_card(df),
        ],
        _section_head(active_sector, n_rows),
        _top_table(df, active_sector, n),
    )


register_page(__name__, path="/", name="Dashboard", layout=layout)
