"""Einzelanalyse — Variante A "Equity Tearsheet" im M&S-Brand-Refresh.

Re-Design auf Basis des Claude-Design-Handoffs (EinzelA in app.jsx):
- Quote-Hero mit Rang-Kontext (Sektor / Universum)
- Verdict-Streifen (Klartext-Fazit + Badges)
- Stärken / Schwächen (Top/Bottom-3 Indikator-Perzentile)
- Faktor-Decomposition (5 vertikale Säulen)
- 52W-Kursband + Returns-Mini-Bars
- Indikator-Tabellen mit Perzentil-Bars
- Peer-Vergleich als Heatmap-Tabelle
"""

from __future__ import annotations

import dash_bootstrap_components as dbc
import pandas as pd
from datetime import date

from dash import (
    Input,
    Output,
    State,
    callback,
    callback_context,
    dcc,
    html,
    no_update,
    register_page,
)
from dash.exceptions import PreventUpdate

from app.core import agents_client, persistence, ticker_mapping
from app.core.indicators import INDICATOR_GROUPS
from app.core.peers import compute_peers
from app.core.scoring import _indicator_percentile
from app.core.state import STATE
from app.core.uid import base_ticker, row_by_uid, rows_by_uid_index
from app.ui import (
    fmt_de,
    fmt_indicator,
    fmt_int,
    fmt_market_cap,
    fmt_percent,
)
from app.ui.agent_report import fmt_local_dt, progress_checklist, result_view


def _resolve_uid(key: str | None) -> str | None:
    """Beliebigen Schlüssel (uid oder Alt-Ticker) auf die uid der Zeile
    auflösen; ``None``, wenn kein Universum oder kein Treffer."""
    if not key:
        return None
    row = row_by_uid(STATE.scored, key)
    if row is None:
        return None
    return str(row.get("uid") or row.get("ticker"))


# ── Score → Klassifikation (gespiegelt aus dashboard.py / data.js) ─────────

def _class_of(score: float) -> dict:
    if score is None or pd.isna(score):
        return {"code": "–", "label": "Keine Daten", "cls": "f"}
    if score >= 80: return {"code": "A",  "label": "Exzellent",       "cls": "a"}
    if score >= 70: return {"code": "B+", "label": "Sehr Gut",        "cls": "bp"}
    if score >= 60: return {"code": "B",  "label": "Gut",             "cls": "b"}
    if score >= 50: return {"code": "C",  "label": "Durchschnitt",    "cls": "c"}
    if score >= 40: return {"code": "D",  "label": "Unterdurch.",     "cls": "d"}
    return {"code": "F", "label": "Schwach", "cls": "f"}


_FACTORS = [
    ("Value",    "value_score",    "value"),
    ("Quality",  "quality_score",  "quality"),
    ("Growth",   "growth_score",   "growth"),
    ("Momentum", "momentum_score", "momentum"),
    ("Low Vol",  "lowvol_score",   "lowvol"),
]


# ── Toolbar (Dropdown + PDF-Button) ────────────────────────────────────────

def layout(ticker: str = "", **_) -> html.Div:
    options = []
    if not STATE.scored.empty:
        # Value ist die uid (nicht der Ticker): bei Ticker-Kollisionen
        # (z. B. zwei "SAN") bleiben beide Einträge einzeln ansteuerbar.
        options = [
            {"label": f"{t} – {n}", "value": u}
            for t, n, u in STATE.scored[["ticker", "name", "uid"]]
            .head(2000)
            .itertuples(index=False)
        ]

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        dcc.Dropdown(
                            id="ea-ticker",
                            options=options,
                            value=_resolve_uid(ticker)
                            or (options[0]["value"] if options else None),
                            placeholder="Ticker wählen …",
                            searchable=True,
                            clearable=False,
                        ),
                        className="ms-ea-dropdown",
                    ),
                    dbc.Button(
                        "PDF-Factsheet",
                        id="ea-export-pdf",
                        color="dark",
                        outline=True,
                        size="sm",
                        n_clicks=0,
                        title="Editorial-Factsheet als PDF exportieren",
                    ),
                ],
                className="ms-ea-toolbar",
            ),
            dcc.Download(id="ea-pdf-download"),
            html.Div(id="ea-pdf-error", className="text-danger small mb-2"),
            # Sammelreport der Agenten-Tiefenanalyse — statisch, damit die
            # Outputs die Re-Renders von ea-agent-section überleben.
            dcc.Download(id="ea-agent-pdf-download"),
            html.Div(id="ea-content"),
            # Statisch im Layout (nicht im ea-content-Callback-Baum): der
            # Abschnitts-Callback feuert beim Seitenladen parallel zu _render —
            # läge sein Ziel innerhalb von ea-content, ginge sein Output beim
            # verlorenen Rennen verloren und der Abschnitt bliebe leer.
            html.Div(id="ea-agent-section"),
            html.Div(id="ea-agent-pdf-error", className="ms-agent-pdf-error"),
            dcc.Interval(id="ea-agent-poll", interval=2500, disabled=True),
            _mapping_modal(),
        ]
    )


def _mapping_modal() -> dbc.Modal:
    """Modal zur Bestätigung des Yahoo-Tickers vor der ersten Tiefenanalyse."""
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle("Börsen-Ticker bestätigen")),
            dbc.ModalBody(
                [
                    html.P(
                        "Der Koyfin-Ticker enthält keine Börsen-Endung. Für die "
                        "Agenten-Analyse (yfinance/Alpha Vantage) wird das "
                        "Yahoo-Format benötigt (z. B. MBG.F für Frankfurt). "
                        "Bitte den passenden Titel auswählen oder manuell "
                        "eingeben:",
                        className="small",
                    ),
                    html.Div(id="ea-agent-mapping-note", className="small text-warning"),
                    dbc.RadioItems(id="ea-agent-mapping-choice", options=[]),
                    dbc.Input(
                        id="ea-agent-mapping-custom",
                        placeholder="… oder Yahoo-Ticker manuell eingeben (z. B. MBG.DE)",
                        type="text",
                        className="mt-2",
                    ),
                ]
            ),
            dbc.ModalFooter(
                [
                    dbc.Button(
                        "Abbrechen",
                        id="ea-agent-mapping-cancel",
                        color="secondary",
                        outline=True,
                        n_clicks=0,
                    ),
                    dbc.Button(
                        "Bestätigen & Analyse starten",
                        id="ea-agent-mapping-confirm",
                        color="dark",
                        n_clicks=0,
                    ),
                ]
            ),
        ],
        id="ea-agent-mapping-modal",
        is_open=False,
    )


# ── Bausteine ──────────────────────────────────────────────────────────────

def _meta_strong(label: str, value: str) -> html.Span:
    return html.Span([html.Strong(value), " ", label])


def _hero_block(r: pd.Series, ranks: dict) -> html.Div:
    """Quote-Hero mit Rang-Kontext."""
    score = r.get("total_score")
    score_val = float(score) if pd.notna(score) else None
    cls = _class_of(score_val) if score_val is not None else _class_of(None)

    region = str(r.get("region") or "")
    industry = str(r.get("industry") or r.get("sector") or "")
    eyebrow_parts = ["Einzelanalyse"]
    if region: eyebrow_parts.append(region)
    if industry: eyebrow_parts.append(industry)
    eyebrow = " · ".join(eyebrow_parts)

    last_price = r.get("last_price")
    market_cap = r.get("market_cap")
    piotr = r.get("piotroski")
    altman = r.get("altman_z")
    beta = r.get("beta")

    meta_row = []
    if pd.notna(last_price):
        meta_row.append(_meta_strong("Kurs", f"{fmt_de(last_price, 2)} €"))
    if pd.notna(market_cap):
        meta_row.append(html.Span("·", className="ms-sep"))
        meta_row.append(_meta_strong("Mkt. Cap", fmt_market_cap(market_cap)))
    if pd.notna(piotr):
        meta_row.append(html.Span("·", className="ms-sep"))
        meta_row.append(_meta_strong("Piotroski", f"{fmt_int(piotr)} / 9"))
    if pd.notna(altman):
        meta_row.append(html.Span("·", className="ms-sep"))
        meta_row.append(_meta_strong("Altman Z", fmt_de(altman, 2)))
    if pd.notna(beta):
        meta_row.append(html.Span("·", className="ms-sep"))
        meta_row.append(_meta_strong("Beta", fmt_de(beta, 2)))

    score_display = fmt_de(score_val, 1) if score_val is not None else "–"

    score_ctx_parts = []
    if ranks.get("sector_total"):
        score_ctx_parts.append(html.Span([
            "Rang Sektor ",
            html.Strong(f"{ranks['sector_rank']} / {ranks['sector_total']}"),
        ]))
    if ranks.get("uni_total"):
        score_ctx_parts.append(html.Span([
            "Rang Universum ",
            html.Strong(f"{ranks['uni_rank']} / {ranks['uni_total']}"),
        ]))
    if ranks.get("sector_avg") is not None:
        score_ctx_parts.append(html.Span([
            "Ø Sektor ",
            html.Strong(fmt_de(ranks["sector_avg"], 1)),
        ]))

    return html.Div(
        [
            html.Div(
                [
                    html.Div(eyebrow, className="ms-hero-eyebrow"),
                    html.H1(
                        [
                            str(r["ticker"]),
                            html.Span(
                                str(r.get("name") or ""),
                                className="ms-hero-subtitle",
                            ),
                        ],
                        className="ms-hero-title",
                    ),
                    html.Div(meta_row, className="ms-hero-meta"),
                ]
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(score_display),
                            html.Span(" / 100", className="ms-score-suf"),
                        ],
                        className="ms-score-num",
                    ),
                    html.Div(
                        f"{cls['code']} – {cls['label']}",
                        className="ms-score-class",
                    ),
                    html.Div(score_ctx_parts, className="ms-score-ctx"),
                ],
                className="ms-hero-score",
            ),
        ],
        className="ms-hero",
    )


def _ranks(df: pd.DataFrame, r: pd.Series) -> dict:
    """Sektor- und Universum-Rang des Tickers nach total_score."""
    out = {}
    score = r.get("total_score")
    if pd.isna(score):
        return out
    sector = r.get("sector")
    uni = df.dropna(subset=["total_score"])
    out["uni_total"] = len(uni)
    out["uni_rank"] = int((uni["total_score"] > score).sum()) + 1
    if sector and sector in df["sector"].values:
        sec = uni[uni["sector"] == sector]
        out["sector_total"] = len(sec)
        out["sector_rank"] = int((sec["total_score"] > score).sum()) + 1
        out["sector_avg"] = float(sec["total_score"].mean())
    return out


def _verdict_text(r: pd.Series) -> str:
    """Heuristisches Klartext-Fazit aus den Faktor-Scores."""
    factors = [(label, float(r[col])) for label, col, _ in _FACTORS if pd.notna(r.get(col))]
    if not factors:
        return "Faktor-Profil unvollständig — Auswertung nicht möglich."
    factors_sorted = sorted(factors, key=lambda x: x[1], reverse=True)
    best, _ = factors_sorted[0]
    worst, _ = factors_sorted[-1]
    rec = str(r.get("recommendation") or "")
    filt = str(r.get("filter_ok") or "")

    pieces = []
    if best == worst:
        pieces.append(f"Ausgeglichenes {best}-Profil")
    else:
        pieces.append(f"{best}-Champion mit {worst}-Schwäche")
    if filt == "JA":
        pieces.append("Filter bestanden")
    elif filt == "NEIN":
        pieces.append("Filter nicht bestanden")
    if rec in ("STRONG BUY", "BUY"):
        pieces.append("klare Kauf-Empfehlung")
    elif rec == "SELL":
        pieces.append("Verkaufs-Signal")
    elif rec == "HOLD":
        pieces.append("neutrale Haltung")
    return " – ".join(pieces) + "."


def _verdict_block(r: pd.Series) -> html.Div:
    rec = str(r.get("recommendation") or "–")
    filt = str(r.get("filter_ok") or "–")
    sma = str(r.get("sma_signal") or "–")
    rec_tone = "up" if rec in ("STRONG BUY", "BUY") else "down" if rec == "SELL" else "warn"
    filt_label = "bestanden" if filt == "JA" else "nicht bestanden" if filt == "NEIN" else filt
    filt_tone = "up" if filt == "JA" else "down" if filt == "NEIN" else "warn"
    if "GOLDEN" in sma:
        sma_label, sma_tone = "Golden Cross", "up"
    elif "DEATH" in sma:
        sma_label, sma_tone = "Death Cross", "down"
    elif ">" in sma:
        sma_label, sma_tone = "Kurs > SMA-200", "info"
    elif "<" in sma:
        sma_label, sma_tone = "Kurs < SMA-200", "warn"
    else:
        sma_label, sma_tone = sma, None

    def _badge(label: str, value: str, tone: str | None) -> html.Span:
        klass = f"ms-badge is-{tone}" if tone else "ms-badge"
        return html.Span(
            [html.Span(label, className="ms-badge-label"),
             html.Span(value, className="ms-badge-value")],
            className=klass,
        )

    return html.Div(
        [
            html.P(f"„{_verdict_text(r)}\"", className="ms-verdict-text"),
            html.Div(
                [
                    _badge("Empfehlung", rec, rec_tone),
                    _badge("Filter", filt_label, filt_tone),
                    _badge("SMA", sma_label, sma_tone),
                ],
                className="ms-verdict-badges",
            ),
        ],
        className="ms-verdict",
    )


def _strengths_concerns(df: pd.DataFrame, ticker: str) -> html.Div:
    """Top-3 / Bottom-3 Indikator-Perzentile für den Ticker (uid)."""
    flat: list[dict] = []
    idx = rows_by_uid_index(df, ticker)
    if len(idx) == 0:
        return html.Div()
    i = idx[0]

    for grp in INDICATOR_GROUPS:
        for it in grp.items:
            if it.key not in df.columns:
                continue
            val = df.at[i, it.key]
            if pd.isna(val):
                continue
            try:
                pct_series = _indicator_percentile(df, it.key, STATE.settings)
                pct = float(pct_series.loc[i])
            except Exception:
                continue
            if pd.isna(pct):
                continue
            flat.append({
                "factor": grp.name,
                "label": it.label,
                "value": fmt_indicator(it.key, val),
                "pct": int(round(pct * 100)),
            })

    if not flat:
        return html.Div()

    top3 = sorted(flat, key=lambda x: x["pct"], reverse=True)[:3]
    bot3 = sorted(flat, key=lambda x: x["pct"])[:3]

    def _items(items: list[dict], cls: str) -> html.Div:
        return html.Div(
            [
                html.Div(
                    [
                        html.Span(f"{it['factor']} · {it['label']}",
                                  className="ms-sc-lbl",
                                  title=f"{it['factor']} · {it['label']}"),
                        html.Span(it["value"], className="ms-sc-val"),
                        html.Span(f"P{it['pct']}", className="ms-sc-pct"),
                    ],
                    className=f"ms-sc-it is-{cls}",
                )
                for it in items
            ],
            className="ms-sc-list",
        )

    return html.Div(
        [
            html.Div(
                [
                    html.H3(
                        ["Stärken ",
                         html.Span("höchste Perzentile", className="ms-card-h-meta")],
                        className="ms-card-h",
                    ),
                    _items(top3, "strength"),
                ],
                className="ms-card",
            ),
            html.Div(
                [
                    html.H3(
                        ["Schwächen ",
                         html.Span("niedrigste Perzentile", className="ms-card-h-meta")],
                        className="ms-card-h",
                    ),
                    _items(bot3, "concern"),
                ],
                className="ms-card",
            ),
        ],
        className="ms-row ms-r-2",
    )


def _factor_decomposition(r: pd.Series) -> html.Div:
    weights = STATE.settings.factor_weights
    stacks = []
    for i, (label, col, key) in enumerate(_FACTORS):
        v = float(r[col]) if col in r and pd.notna(r[col]) else 0.0
        height = max(0.0, min(100.0, v))
        w = weights.get(key, 0.0)
        stacks.append(
            html.Div(
                [
                    html.Div(
                        html.Div(
                            className=f"ms-stack-seg s{i+1}",
                            style={"height": f"{height}%"},
                        ),
                        className="ms-stack-bar",
                    ),
                    html.Div(
                        [
                            html.Div(fmt_de(v, 0), className="ms-stack-val"),
                            html.Div(label, className="ms-stack-name"),
                            html.Div(f"Gewicht {round(w * 100)} %",
                                     className="ms-stack-w"),
                        ],
                        className="ms-stack-meta",
                    ),
                ],
                className="ms-fac-stack",
            )
        )
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Div("Score-Zerlegung", className="ms-eyebrow"),
                            html.H2("Faktor-Beiträge zum Gesamtscore"),
                        ]
                    ),
                    html.Div(
                        "Säulenhöhe = Score · Beschriftung = Gewicht",
                        className="ms-meta",
                    ),
                ],
                className="ms-dash-section",
            ),
            html.Div(
                html.Div(stacks, className="ms-fac-stack-wrap"),
                className="ms-card",
            ),
        ]
    )


def _price_block(r: pd.Series) -> html.Div:
    last = r.get("last_price")
    high = r.get("high_52w")
    low = r.get("low_52w")
    sma50 = r.get("sma_50")
    sma200 = r.get("sma_200")

    range_pos = None
    if pd.notna(last) and pd.notna(high) and pd.notna(low) and high > low:
        range_pos = (float(last) - float(low)) / (float(high) - float(low)) * 100
        range_pos = max(0.0, min(100.0, range_pos))

    if range_pos is None:
        body = [html.Div("Keine Kursdaten verfügbar.", className="ms-tt-muted")]
    else:
        sma_row = []
        if pd.notna(sma50):
            sma_row.append(html.Span("SMA-50", className="ms-sma-lbl"))
            sma_row.append(html.Strong(f"{fmt_de(sma50, 2)} €"))
        if pd.notna(sma200):
            sma_row.append(html.Span("SMA-200", className="ms-sma-lbl"))
            sma_row.append(html.Strong(f"{fmt_de(sma200, 2)} €"))
        if pd.notna(last) and pd.notna(sma200) and float(sma200) > 0:
            dist = (float(last) - float(sma200)) / float(sma200)
            tone = "is-up" if dist >= 0 else "is-down"
            sma_row.append(html.Span("Distanz SMA-200", className="ms-sma-lbl"))
            sma_row.append(html.Strong(fmt_percent(dist, 1), className=tone))

        body = [
            html.Div(
                f"Aktueller Kurs liegt bei {fmt_de(range_pos, 0)} % der Spanne",
                className="ms-tt-muted",
                style={"fontSize": "11px"},
            ),
            html.Div(
                [
                    html.Div(className="ms-range-fill"),
                    html.Div(
                        className="ms-range-marker",
                        style={"left": f"{range_pos}%"},
                    ),
                ],
                className="ms-range-bar",
            ),
            html.Div(
                [
                    html.Span(f"Tief {fmt_de(low, 2)} €"),
                    html.Span(f"{fmt_de(last, 2)} €"),
                    html.Span(f"Hoch {fmt_de(high, 2)} €"),
                ],
                className="ms-range-meta",
            ),
            html.Div(sma_row, className="ms-sma-row") if sma_row else html.Div(),
        ]

    return html.Div(
        [html.H3("Kursband 52 Wochen", className="ms-card-h"), *body],
        className="ms-card",
    )


def _returns_block(r: pd.Series) -> html.Div:
    rows = []
    windows = [("1M", "ret_1m"), ("3M", "ret_3m"), ("6M", "ret_6m"), ("12M", "ret_12m")]
    # Skala bestimmen: max |return| im Universum, mind. 50 %.
    max_abs = 0.50
    for _, col in windows:
        v = r.get(col)
        if pd.notna(v):
            max_abs = max(max_abs, abs(float(v)))
    if max_abs == 0:
        max_abs = 0.50

    for label, col in windows:
        v = r.get(col)
        if pd.isna(v):
            rows.append(html.Div(
                [
                    html.Span(label, className="ms-ret-lbl"),
                    html.Span(html.Span(className="ms-ret-mid"),
                              className="ms-ret-track"),
                    html.Span("–", className="ms-ret-v"),
                ],
                className="ms-ret-row",
            ))
            continue
        v = float(v)
        # max width = halbe Bar-Breite (50 %).
        width_pct = min(50.0, abs(v) / max_abs * 50.0)
        tone = "is-up" if v >= 0 else "is-down"
        rows.append(html.Div(
            [
                html.Span(label, className="ms-ret-lbl"),
                html.Span(
                    [
                        html.Span(className="ms-ret-mid"),
                        html.Span(
                            className=f"ms-ret-fill {tone}",
                            style={"width": f"{width_pct}%"},
                        ),
                    ],
                    className="ms-ret-track",
                ),
                html.Span(fmt_percent(v, 1), className=f"ms-ret-v {tone}"),
            ],
            className="ms-ret-row",
        ))

    return html.Div(
        [
            html.H3("Rückblick Returns", className="ms-card-h"),
            html.Div(rows, className="ms-ret-list"),
        ],
        className="ms-card",
    )


def _indicator_table_card(df: pd.DataFrame, ticker: str, group) -> html.Div:
    idx = rows_by_uid_index(df, ticker)
    if len(idx) == 0:
        return html.Div()
    i = idx[0]

    rows = []
    for it in group.items:
        if it.key not in df.columns:
            continue
        val = df.at[i, it.key]
        try:
            pct_series = _indicator_percentile(df, it.key, STATE.settings)
            pct_val = pct_series.loc[i]
        except Exception:
            pct_val = None
        pct = int(round(float(pct_val) * 100)) if pd.notna(pct_val) else None

        if pct is None:
            fill_cls = "is-weak"
            fill_width = 0
            pct_cell = "–"
        else:
            if pct >= 70:   fill_cls = ""
            elif pct >= 30: fill_cls = "is-warn"
            else:           fill_cls = "is-weak"
            fill_width = pct
            pct_cell = f"P{pct}"

        rows.append(
            html.Tr(
                [
                    html.Td(it.label, className="ms-ind-lbl"),
                    html.Td(fmt_indicator(it.key, val), className="ms-ind-val"),
                    html.Td(
                        html.Div(
                            [
                                html.Div(
                                    html.Div(
                                        className=f"ms-ind-fill {fill_cls}".strip(),
                                        style={"width": f"{fill_width}%"},
                                    ),
                                    className="ms-ind-track",
                                ),
                                html.Span(pct_cell, className="ms-ind-pct"),
                            ],
                            className="ms-ind-pct-cell",
                        ),
                    ),
                ]
            )
        )

    return html.Div(
        [
            html.H3(
                [
                    group.name, " ",
                    html.Span(f"{len(group.items)} Indikatoren",
                              className="ms-card-h-meta"),
                ],
                className="ms-card-h",
            ),
            html.Table(
                [
                    html.Thead(
                        html.Tr([
                            html.Th("Kennzahl"),
                            html.Th("Wert", className="is-num"),
                            html.Th("Perzentil-Rang"),
                        ])
                    ),
                    html.Tbody(rows),
                ],
                className="ms-ind-table",
            ),
        ],
        className="ms-card",
    )


def _peer_heatmap(scored: pd.DataFrame, ticker: str, mode: str) -> html.Div:
    peers = compute_peers(scored, ticker, n=6, mode=mode)
    if peers.empty:
        return html.Div(
            "Keine Comparables verfügbar.",
            className="ms-tt-muted",
            style={"padding": "16px"},
        )

    def _hm(v) -> html.Span:
        if pd.isna(v):
            return html.Span("–", className="ms-hm is-f")
        cls = _class_of(float(v))["cls"]
        return html.Span(fmt_de(float(v), 0), className=f"ms-hm is-{cls}")

    rows = []
    self_uid = _resolve_uid(ticker) or ticker
    for _, p in peers.iterrows():
        peer_uid = str(p.get("uid") or p["ticker"])
        is_self = peer_uid == self_uid
        score = p.get("total_score")
        score_cls = _class_of(float(score))["cls"] if pd.notna(score) else "f"
        ret_12m = p.get("ret_12m")
        ret_str = fmt_percent(float(ret_12m), 1) if pd.notna(ret_12m) else "–"
        ret_cls = "ms-up" if (pd.notna(ret_12m) and float(ret_12m) >= 0) else "ms-down"

        name_cell: list = [str(p.get("name") or "—")]
        if is_self:
            name_cell.append(html.Span("aktuell", className="ms-peer-self-tag"))

        rows.append(
            html.Tr(
                [
                    html.Td(
                        html.A(str(p["ticker"]),
                               href=f"/einzelanalyse?ticker={peer_uid}",
                               className="ms-peer-tk"),
                    ),
                    html.Td(name_cell),
                    html.Td(_hm(p.get("value_score")),    className="is-num"),
                    html.Td(_hm(p.get("quality_score")),  className="is-num"),
                    html.Td(_hm(p.get("growth_score")),   className="is-num"),
                    html.Td(_hm(p.get("momentum_score")), className="is-num"),
                    html.Td(_hm(p.get("lowvol_score")),   className="is-num"),
                    html.Td(
                        html.Span(
                            fmt_de(float(score), 1) if pd.notna(score) else "–",
                            className=f"ms-score-pill is-{score_cls}",
                        ),
                        className="is-num",
                    ),
                    html.Td(ret_str,
                            className=f"is-num {ret_cls}",
                            style={"fontWeight": 600}),
                ],
                className="is-self" if is_self else "",
            )
        )

    return html.Div(
        html.Table(
            [
                html.Thead(
                    html.Tr([
                        html.Th("Ticker"),
                        html.Th("Name"),
                        html.Th("Value",    className="is-num"),
                        html.Th("Quality",  className="is-num"),
                        html.Th("Growth",   className="is-num"),
                        html.Th("Momentum", className="is-num"),
                        html.Th("Low Vol",  className="is-num"),
                        html.Th("Score",    className="is-num"),
                        html.Th("12M",      className="is-num"),
                    ])
                ),
                html.Tbody(rows),
            ],
            className="ms-peer-table",
        ),
        className="ms-toptable-wrap",
    )


def _comparables_controls() -> html.Div:
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
        className="mb-2",
    )


# ── Callbacks ──────────────────────────────────────────────────────────────

@callback(
    Output("ea-ticker", "value"),
    Input("ms-location", "search"),
    prevent_initial_call=True,
)
def _sync_ticker_from_url(search: str | None):
    """Ticker-Dropdown bei URL-Query-Wechsel nachziehen (Deep-Link).

    Der Query-Wert ist die uid; alte Links mit bloßem Ticker werden auf die
    passende uid aufgelöst (erste Zeile — bisheriges Verhalten)."""
    from urllib.parse import parse_qs

    if not search:
        raise PreventUpdate
    qs = parse_qs(search.lstrip("?"))
    tickers = qs.get("ticker")
    if not tickers or not tickers[0]:
        raise PreventUpdate
    return _resolve_uid(tickers[0]) or tickers[0]


def _v2_block(df: pd.DataFrame, r: pd.Series) -> html.Div:
    """Composite-v2-Abschnitt: Score, Klasse, Zone, Faktor-Z und je
    Indikator z_* mit Neutralisierungsebene (Spec 11.2)."""
    from app.core.scoring_v2 import V2_FACTOR_NAMES
    from app.pages.common import render_basic_table
    from app.ui.theme import ms_badge

    def _num(value, decimals=2):
        return fmt_de(float(value), decimals) if pd.notna(value) else "–"

    badges = [
        ms_badge("COMPOSITE v2", _num(r.get("composite_score"), 1)),
        ms_badge("KLASSE", str(r.get("classification_v2") or "–")),
        ms_badge(
            "ZONE",
            str(r.get("zone_v2") or "–"),
            tone={
                "KANDIDAT": "up",
                "VERKAUFEN": "down",
                "FILTER": "warn",
            }.get(str(r.get("zone_v2"))),
        ),
        ms_badge(
            "ABDECKUNG v2",
            _num((r.get("data_coverage_v2") or 0) * 100, 0) + " %",
        ),
    ]
    if bool(r.get("trend_warning")):
        badges.append(ms_badge("TREND", "⚠ Death Cross", tone="warn"))
    reasons = r.get("filter_reasons")
    if isinstance(reasons, list) and reasons:
        badges.append(ms_badge("FILTER", ", ".join(reasons), tone="down"))

    factor_rows = pd.DataFrame(
        [
            {
                "Faktor": name,
                "Z-Score": _num(r.get(f"z_{name}")),
                "Abdeckung": _num((r.get(f"cov_{name}") or 0) * 100, 0) + " %",
            }
            for name in V2_FACTOR_NAMES
        ]
    )
    indicator_rows = []
    for col in sorted(c for c in df.columns if c.startswith("neut_level_")):
        indicator = col.removeprefix("neut_level_")
        z = r.get(f"z_{indicator}")
        level = r.get(col)
        if pd.isna(z) and (level is None or pd.isna(level)):
            continue
        indicator_rows.append(
            {
                "Indikator": indicator,
                "Z-Score": _num(z),
                "Neutralisierung": str(level) if pd.notna(level) else "–",
            }
        )
    children: list = [
        html.Div(
            [
                html.Div("Composite v2", className="ms-eyebrow"),
                html.H2("Faktor-Z-Scores (Region×Sektor-neutral)"),
            ],
            className="ms-dash-section",
        ),
        html.Div(badges, className="d-flex gap-2 flex-wrap mb-2"),
        html.Div(
            [
                html.Div(render_basic_table(factor_rows)),
                html.Div(
                    render_basic_table(pd.DataFrame(indicator_rows))
                    if indicator_rows
                    else html.Div(),
                ),
            ],
            className="ms-row ms-r-2",
        ),
    ]
    return html.Div(children, className="mb-3")


@callback(Output("ea-content", "children"), Input("ea-ticker", "value"))
def _render(ticker: str | None):
    if not ticker or STATE.scored.empty:
        return dbc.Alert("Keine Daten verfügbar.", color="info")

    df = STATE.scored
    r = row_by_uid(df, ticker)
    if r is None:
        return dbc.Alert(f"Ticker {ticker} nicht gefunden.", color="warning")

    ranks = _ranks(df, r)
    industry_label = (
        str(r.get("industry") or "").split("·")[-1].strip()
        if r.get("industry") else (r.get("sector") or "")
    )

    indicator_cards: list = []
    pair_a, pair_b = [], []
    for i, grp in enumerate(INDICATOR_GROUPS):
        card = _indicator_table_card(df, ticker, grp)
        if i % 2 == 0: pair_a.append(card)
        else:          pair_b.append(card)
    # Render zweispaltig, ein „letztes" einsames Card-Element bekommt einen
    # eigenen Spalten-Wrapper, damit das Grid sauber bleibt.
    indicator_rows = []
    for a, b in zip(pair_a, pair_b):
        indicator_rows.append(html.Div([a, b], className="ms-row ms-r-2"))
    if len(pair_a) > len(pair_b):
        indicator_rows.append(
            html.Div([pair_a[-1], html.Div()], className="ms-row ms-r-2"),
        )

    # v1-Scoring-Blöcke; bei scoring_version = "v2" wandern sie in einen
    # aufklappbaren Vergleichsbereich (Spec 11.2).
    v1_scoring_blocks = [
        _verdict_block(r),
        _strengths_concerns(df, ticker),
        _factor_decomposition(r),
        html.Div(
            [
                html.Div(
                    [
                        html.Div("Kennzahlen", className="ms-eyebrow"),
                        html.H2("Indikatoren mit Perzentil-Rang"),
                    ]
                ),
                html.Div(
                    f"Referenz: {STATE.settings.percentile_mode}",
                    className="ms-meta",
                ),
            ],
            className="ms-dash-section",
        ),
        *indicator_rows,
    ]
    v2_active = (
        STATE.settings.scoring_version == "v2"
        and "composite_score" in df.columns
    )
    if v2_active:
        scoring_blocks: list = [
            _v2_block(df, r),
            dbc.Accordion(
                [
                    dbc.AccordionItem(
                        v1_scoring_blocks, title="Scoring v1 (Vergleich)"
                    )
                ],
                start_collapsed=True,
                className="mb-3",
            ),
        ]
    else:
        scoring_blocks = v1_scoring_blocks

    return html.Div(
        [
            _hero_block(r, ranks),
            *scoring_blocks,
            html.Div(
                [_price_block(r), _returns_block(r)],
                className="ms-row ms-r-2",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div("Comparables", className="ms-eyebrow"),
                            html.H2(f"Peer-Vergleich · {industry_label}" if industry_label else "Peer-Vergleich"),
                        ]
                    ),
                    html.Div(
                        _comparables_controls(),
                        className="ms-meta",
                    ),
                ],
                className="ms-dash-section",
            ),
            html.Div(id="ea-comparables"),
        ]
    )


# ── Agenten-Tiefenanalyse (Design-Handoff Turn 2, Screen 2a) ───────────────

def _agent_section_head(meta_children) -> html.Div:
    """Section-Kopf mit Gold-Eyebrow und zustandsabhängiger Meta-Zeile."""
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        "Tiefenanalyse",
                        className="ms-eyebrow",
                        style={"color": "var(--ms-gold)"},
                    ),
                    html.H2("Agenten-Tiefenanalyse"),
                ]
            ),
            html.Div(meta_children, className="ms-meta"),
        ],
        className="ms-dash-section",
    )


def _current_quant(ticker: str):
    """(total_score, klassifikations-kurzform) aus dem Universum, sonst None."""
    if STATE.scored.empty:
        return None, None
    r = row_by_uid(STATE.scored, ticker)
    if r is None:
        return None, None
    score = r.get("total_score")
    cls = _class_of(float(score)) if pd.notna(score) else None
    return (float(score) if pd.notna(score) else None), (cls["code"] if cls else None)


def _agent_start_state(ticker: str, service_ok: bool, error: str | None) -> html.Div:
    """Zustand 1 — Start: zweispaltige Karte mit Ablauf-Panel."""
    score, cls_code = _current_quant(ticker)
    if score is not None:
        quant_txt = (
            f"Der Quant-Score dieser Seite ({fmt_de(score, 1)}"
            + (f" · {cls_code}" if cls_code else "")
            + ") wird den Agenten als Vorab-Rating mitgegeben — sie bestätigen "
            "oder widerlegen ihn. "
        )
    else:
        quant_txt = "Für diesen Titel liegt kein Quant-Score vor (Ad-hoc-Analyse). "

    effective = ticker_mapping.resolve(ticker) or ticker
    main_children: list = [
        html.P(
            "Zwölf LLM-Agenten prüfen Markt, Sentiment, News und "
            "Fundamentaldaten, debattieren Bull gegen Bear und liefern ein "
            "Rating samt Begründung.",
            className="ms-agent-serif-intro",
        ),
        html.P(
            quant_txt + "Dauer: mehrere Minuten; das Ergebnis wird gespeichert.",
            className="ms-agent-start-sub",
        ),
        html.Div(
            [
                html.Button(
                    "Tiefenanalyse starten",
                    id="ea-agent-start",
                    n_clicks=0,
                    disabled=not service_ok,
                    className="ms-agent-btn-primary",
                ),
                html.Button(
                    f"Börsen-Ticker ändern … ({effective})",
                    id="ea-agent-mapping-open",
                    n_clicks=0,
                    disabled=not service_ok,
                    className="ms-agent-btn-secondary",
                    title="Yahoo-Ticker für die Agenten-Analyse prüfen/korrigieren",
                ),
            ],
            className="ms-agent-start-actions",
        ),
    ]
    if error:
        main_children.append(
            html.Div(
                [html.Span("⚠"), html.Span(f"Analyse fehlgeschlagen: {error}")],
                className="ms-agent-warnstrip",
            )
        )
    if not service_ok:
        main_children.append(
            html.Div(
                [
                    html.Span("⚠"),
                    html.Span(
                        "TradingAgents-Service nicht erreichbar "
                        "(TRADINGAGENTS_URL prüfen)."
                    ),
                ],
                className="ms-agent-warnstrip",
            )
        )

    ablauf = [
        ("1", "Vier Analysten (Markt, Sentiment, News, Fundamentals)"),
        ("2", "Bull/Bear-Debatte & Research-Fazit"),
        ("3", "Trading-Plan & Risiko-Runde"),
        ("4", "Rating: Buy … Sell"),
    ]
    return html.Div(
        [
            html.Div(main_children, className="ms-agent-start-main"),
            html.Div(
                [
                    html.Div("Ablauf", className="ms-agent-aside-label"),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Span(num, className="ms-agent-ablauf-num"),
                                    html.Span(txt),
                                ],
                                className="ms-agent-ablauf-row",
                            )
                            for num, txt in ablauf
                        ],
                        className="ms-agent-ablauf",
                    ),
                ],
                className="ms-agent-start-aside",
            ),
        ],
        className="ms-agent-start",
    )


def _agent_section_view(ticker: str) -> tuple[html.Div, bool]:
    """Aktuellen Zustand des Agenten-Abschnitts rendern.

    Rückgabe ``(inhalt, poll_disabled)``.
    """
    job = agents_client.get_status(ticker)
    if job and job.get("status") == "running":
        return (
            html.Div(
                [
                    _agent_section_head("Analyse läuft …"),
                    progress_checklist(job),
                ]
            ),
            False,
        )

    error = job.get("error") if job and job.get("status") == "error" else None
    analysis = persistence.load_agent_analysis(ticker)
    service_ok = agents_client.service_available()

    if analysis and not error:
        created = analysis.get("created_at")
        created_label = fmt_local_dt(created)
        meta: list = [
            "Analyse vom ",
            html.Strong(created_label, style={"color": "var(--ms-text)"}),
        ]
        if analysis.get("provider"):
            meta.append(f" · {analysis['provider']}")
        meta.append(
            html.Button(
                "Alle Berichte als PDF",
                id="ea-agent-pdf-all",
                n_clicks=0,
                className="ms-btn-goldline",
                style={"margin": "0 14px"},
                title="Sammelreport aller Agenten-Berichte als PDF",
            )
        )
        if service_ok:
            meta.append(
                html.A(
                    "Neu analysieren",
                    id="ea-agent-start",
                    n_clicks=0,
                    style={"color": "var(--ms-gold)", "cursor": "pointer"},
                )
            )

        current, _ = _current_quant(ticker)
        return (
            html.Div(
                [
                    _agent_section_head(meta),
                    result_view(analysis, current_score=current),
                ]
            ),
            True,
        )

    return (
        html.Div(
            [
                _agent_section_head(
                    f"Noch keine Analyse für {base_ticker(ticker) or ticker}"
                ),
                _agent_start_state(ticker, service_ok, error),
            ]
        ),
        True,
    )


def _start_agent_run(ticker: str, agents_ticker: str) -> tuple[bool, str]:
    """Faktor-Kontext bauen und den Hintergrund-Lauf starten."""
    factor_context = None
    in_universe = False
    if not STATE.scored.empty:
        row = row_by_uid(STATE.scored, ticker)
        if row is not None:
            factor_context = agents_client.build_factor_context(row)
            in_universe = True
    return agents_client.start_analysis(
        ticker,
        agents_ticker,
        STATE.settings,
        factor_context=factor_context,
        in_universe=in_universe,
    )


@callback(
    Output("ea-agent-section", "children"),
    Output("ea-agent-poll", "disabled"),
    Input("ea-ticker", "value"),
    Input("ea-agent-poll", "n_intervals"),
)
def _agent_section(ticker: str | None, _n):
    if not ticker:
        return html.Div(), True
    return _agent_section_view(ticker)


def _universe_row(ticker: str):
    if not STATE.scored.empty:
        return row_by_uid(STATE.scored, ticker)
    return None


def _mapping_modal_content(ticker: str, row) -> tuple[list, str | None, str]:
    """Vorschlagsliste für das Mapping-Modal bauen.

    Rückgabe ``(options, default, note)`` aus der Symbol-Suche des Service,
    gerankt nach Namens-/Regionsähnlichkeit.
    """
    name = str(row.get("name") or "") if row is not None else ""
    region = row.get("region") if row is not None else None
    query = name or ticker
    results, note = agents_client.symbol_search(query)
    if not results and name:
        results, note = agents_client.symbol_search(ticker)
    ranked = ticker_mapping.rank_suggestions(results, name=name, region=region)
    options = [
        {
            "label": f"{r.get('symbol')} — {r.get('name') or '?'}"
            + (f" ({r.get('region')})" if r.get("region") else ""),
            "value": r.get("symbol"),
        }
        for r in ranked[:8]
        if r.get("symbol")
    ]
    default = options[0]["value"] if options else None
    if not note and not options:
        note = "Keine Vorschläge gefunden — bitte manuell eingeben."
    return options, default, note or ""


@callback(
    Output("ea-agent-section", "children", allow_duplicate=True),
    Output("ea-agent-poll", "disabled", allow_duplicate=True),
    Output("ea-agent-mapping-modal", "is_open"),
    Output("ea-agent-mapping-choice", "options"),
    Output("ea-agent-mapping-choice", "value"),
    Output("ea-agent-mapping-note", "children"),
    Input("ea-agent-start", "n_clicks"),
    State("ea-ticker", "value"),
    prevent_initial_call=True,
)
def _agent_start(n_clicks: int | None, ticker: str | None):
    if not n_clicks or not ticker:
        raise PreventUpdate

    row = _universe_row(ticker)
    region = row.get("region") if row is not None else None
    resolved = ticker_mapping.resolve(ticker, region)

    if resolved is None:
        # Titel braucht mutmaßlich eine Börsen-Endung — Nutzer bestätigen lassen.
        options, default, note = _mapping_modal_content(ticker, row)
        return no_update, no_update, True, options, default, note

    ok, msg = _start_agent_run(ticker, resolved)
    if not ok:
        content = html.Div(
            [dbc.Alert(msg, color="warning", className="small")]
        )
        return content, True, False, no_update, no_update, ""
    view, poll_disabled = _agent_section_view(ticker)
    return view, poll_disabled, False, no_update, no_update, ""


@callback(
    Output("ea-agent-mapping-modal", "is_open", allow_duplicate=True),
    Output("ea-agent-mapping-choice", "options", allow_duplicate=True),
    Output("ea-agent-mapping-choice", "value", allow_duplicate=True),
    Output("ea-agent-mapping-note", "children", allow_duplicate=True),
    Input("ea-agent-mapping-open", "n_clicks"),
    State("ea-ticker", "value"),
    prevent_initial_call=True,
)
def _agent_mapping_open(n_clicks: int | None, ticker: str | None):
    """Escape-Hatch: Mapping-Modal manuell öffnen (auch für US-Titel),
    um eine automatische Zuordnung zu prüfen oder zu korrigieren."""
    if not n_clicks or not ticker:
        raise PreventUpdate
    row = _universe_row(ticker)
    options, default, note = _mapping_modal_content(ticker, row)
    effective = ticker_mapping.resolve(
        ticker, row.get("region") if row is not None else None
    )
    hint = f"Aktuell verwendeter Yahoo-Ticker: {effective or ticker}."
    return True, options, default, f"{hint} {note}".strip()


@callback(
    Output("ea-agent-section", "children", allow_duplicate=True),
    Output("ea-agent-poll", "disabled", allow_duplicate=True),
    Output("ea-agent-mapping-modal", "is_open", allow_duplicate=True),
    Output("ea-agent-mapping-note", "children", allow_duplicate=True),
    Input("ea-agent-mapping-confirm", "n_clicks"),
    Input("ea-agent-mapping-cancel", "n_clicks"),
    State("ea-agent-mapping-choice", "value"),
    State("ea-agent-mapping-custom", "value"),
    State("ea-ticker", "value"),
    prevent_initial_call=True,
)
def _agent_mapping_confirm(n_confirm, n_cancel, choice, custom, ticker):
    trigger = callback_context.triggered_id
    if trigger == "ea-agent-mapping-cancel":
        return no_update, no_update, False, ""
    if not ticker:
        raise PreventUpdate

    agents_ticker = (custom or "").strip().upper() or (choice or "").strip().upper()
    if not agents_ticker:
        return no_update, no_update, True, "Bitte einen Ticker wählen oder eingeben."

    try:
        persistence.save_ticker_mapping(ticker, agents_ticker, confirmed=True)
    except Exception:  # noqa: BLE001 — Mapping nur im Speicher ist auch ok
        pass

    ok, msg = _start_agent_run(ticker, agents_ticker)
    if not ok:
        return (
            html.Div([dbc.Alert(msg, color="warning", className="small")]),
            True,
            False,
            "",
        )
    view, poll_disabled = _agent_section_view(ticker)
    return view, poll_disabled, False, ""


@callback(
    Output("ea-comparables", "children"),
    Input("ea-ticker", "value"),
    Input("ea-comparables-mode", "value"),
)
def _render_comparables(ticker: str | None, mode: str | None):
    if not ticker or STATE.scored.empty:
        return html.Div("Keine Comparables verfügbar.",
                        className="ms-tt-muted",
                        style={"padding": "16px"})
    return _peer_heatmap(STATE.scored, ticker, mode or "similar")


@callback(
    Output("ea-pdf-download", "data"),
    Output("ea-pdf-error", "children"),
    Input("ea-export-pdf", "n_clicks"),
    State("ea-ticker", "value"),
    prevent_initial_call=True,
)
def _export_factsheet_pdf(n_clicks: int | None, ticker: str | None):
    """Editorial-Factsheet (A4, 1 Seite) für den gewählten Ticker erzeugen."""
    if not n_clicks or not ticker:
        raise PreventUpdate
    if STATE.scored.empty:
        return no_update, "Keine Daten geladen — bitte erst CSV importieren."

    try:
        from app.core.factsheet_pdf import (
            FactsheetRenderError,
            render_editorial_factsheet,
        )
    except Exception as exc:  # pragma: no cover — import-time issues
        return no_update, f"PDF-Modul nicht verfügbar: {exc!s}"

    try:
        pdf_bytes = render_editorial_factsheet(ticker, STATE.scored, STATE.settings)
    except FactsheetRenderError as exc:
        return no_update, str(exc)
    except ValueError as exc:
        return no_update, str(exc)

    filename = f"factsheet_{ticker}_{date.today():%Y%m%d}.pdf"
    return (
        dcc.send_bytes(lambda buf: buf.write(pdf_bytes), filename=filename),
        "",
    )


@callback(
    Output("ea-agent-pdf-download", "data"),
    Output("ea-agent-pdf-error", "children"),
    Input("ea-agent-pdf-all", "n_clicks"),
    State("ea-ticker", "value"),
    running=[
        (Output("ea-agent-pdf-all", "disabled"), True, False),
        (
            Output("ea-agent-pdf-all", "children"),
            "Wird erstellt …",
            "Alle Berichte als PDF",
        ),
    ],
    prevent_initial_call=True,
)
def _export_agent_full_pdf(n_clicks: int | None, ticker: str | None):
    """Sammelreport „Alle Berichte" der Agenten-Tiefenanalyse als PDF."""
    # Re-Mount-Guard: die Agenten-Section wird bei Poll/Tickerwechsel neu
    # gerendert — der Button feuert dann mit n_clicks=0.
    if not n_clicks or not ticker:
        raise PreventUpdate
    analysis = persistence.load_agent_analysis(ticker)
    if not analysis:
        return no_update, "Keine gespeicherte Analyse gefunden."

    try:
        from app.core.pdf_export import FactsheetRenderError, render_full_pdf
    except Exception as exc:  # pragma: no cover — import-time issues
        return no_update, f"PDF-Modul nicht verfügbar: {exc!s}"

    try:
        pdf_bytes, filename = render_full_pdf(analysis, STATE.scored)
    except (FactsheetRenderError, ValueError) as exc:
        return no_update, str(exc)

    return (
        dcc.send_bytes(lambda buf: buf.write(pdf_bytes), filename=filename),
        "",
    )


register_page(__name__, path="/einzelanalyse", name="Einzelanalyse", layout=layout)
