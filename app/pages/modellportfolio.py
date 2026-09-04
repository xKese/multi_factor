"""Modellportfolio-Seite (Spec 11.1): Zielportfolio, Diagnosen, Trade-Liste,
Exposures, Override-Register und Historie der Portfoliokonstruktion."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, callback, dcc, html, register_page

from app.core import persistence
from app.core.diagnostics import (
    SEV_ERROR,
    SEV_INFO,
    SEV_WARNING,
    Diagnostic,
    count_by_severity,
    diags_from_json,
)
from app.core.portfolio_construction import (
    ACTION_HOLD,
    build_model_portfolio,
    load_benchmark_weights,
    load_risk_cache,
)
from app.core.scoring_v2 import V2_FACTOR_NAMES
from app.core.signal_events import snapshot_date_from_universe
from app.core.state import STATE
from app.pages.common import page_title, render_basic_table, render_table
from app.ui import fmt_de, section_header
from app.ui.theme import kpi_band

log = logging.getLogger(__name__)

_SEV_TONE = {SEV_ERROR: "down", SEV_WARNING: "warn", SEV_INFO: "info"}


def _fmt_pct(value, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "–"
    return fmt_de(float(value) * 100, digits) + " %"


# ── Layout ──────────────────────────────────────────────────────────────


def _controls() -> html.Div:
    history = persistence.list_model_portfolio_dates()
    options = [{"label": "Aktueller Import (neu berechnen)", "value": "live"}] + [
        {"label": d.strftime("%d.%m.%Y"), "value": d.isoformat()} for d in history
    ]
    return html.Div(
        [
            html.Div(
                dcc.Dropdown(
                    id="mp-history",
                    options=options,
                    value="live",
                    clearable=False,
                ),
                style={"minWidth": "260px"},
            ),
            html.Div(
                dcc.Dropdown(
                    id="mp-mode",
                    options=[
                        {"label": "Modus: automatisch", "value": "auto"},
                        {"label": "Modus: full (manuell)", "value": "full"},
                        {"label": "Modus: interim (manuell)", "value": "interim"},
                        {"label": "Modus: monitor (manuell)", "value": "monitor"},
                    ],
                    value="auto",
                    clearable=False,
                ),
                style={"minWidth": "220px"},
            ),
            dbc.Button(
                "Dry-Run",
                id="mp-dry",
                color="dark",
                outline=True,
                size="sm",
                n_clicks=0,
            ),
            dbc.Button(
                "Zielportfolio berechnen & speichern",
                id="mp-save",
                color="dark",
                size="sm",
                n_clicks=0,
            ),
        ],
        className="d-flex gap-2 align-items-center flex-wrap mb-3",
    )


def layout(**_) -> html.Div:
    return html.Div(
        [
            page_title(
                "Modellportfolio",
                "Regelbasierte Portfoliokonstruktion (Composite v2): "
                "Selektion, Gewichtung, TE-Kontrolle, Overrides.",
            ),
            _controls(),
            html.Div(id="mp-status"),
            dcc.Loading(html.Div(id="mp-content")),
            section_header(
                "Override-Register",
                "Manuelle Eingriffe: Pflichtfelder Begründung (≥ 20 Zeichen), "
                "Verantwortlicher, Ablaufdatum (≤ 180 Tage).",
            ),
            html.Div(id="mp-override-status"),
            html.Div(id="mp-override-table"),
            _override_form(),
        ]
    )


def _override_form() -> dbc.Card:
    return dbc.Card(
        dbc.CardBody(
            [
                html.Div("Override anlegen", className="fw-bold mb-2"),
                html.Div(
                    [
                        dbc.Input(id="mp-ov-uid", placeholder="uid / Ticker",
                                  size="sm", style={"maxWidth": "160px"}),
                        dcc.Dropdown(
                            id="mp-ov-direction",
                            options=[
                                {"label": "exclude", "value": "exclude"},
                                {"label": "include", "value": "include"},
                                {"label": "weight", "value": "weight"},
                            ],
                            value="exclude",
                            clearable=False,
                            style={"minWidth": "140px"},
                        ),
                        dbc.Input(
                            id="mp-ov-weight",
                            placeholder="Zielgewicht (nur weight, z. B. 0,03)",
                            size="sm",
                            style={"maxWidth": "220px"},
                        ),
                        dbc.Input(id="mp-ov-owner", placeholder="Verantwortlich",
                                  size="sm", style={"maxWidth": "160px"}),
                        dcc.DatePickerSingle(
                            id="mp-ov-expires",
                            display_format="DD.MM.YYYY",
                            date=(date.today() + timedelta(days=90)).isoformat(),
                        ),
                    ],
                    className="d-flex gap-2 align-items-center flex-wrap mb-2",
                ),
                dbc.Textarea(
                    id="mp-ov-reason",
                    placeholder="Begründung (mindestens 20 Zeichen, Pflicht)",
                    rows=2,
                    className="mb-2",
                ),
                html.Div(
                    [
                        dbc.Button("Override anlegen", id="mp-ov-create",
                                   color="dark", size="sm", n_clicks=0),
                        dbc.Input(
                            id="mp-ov-close-id",
                            placeholder="Override-ID",
                            size="sm",
                            style={"maxWidth": "120px"},
                        ),
                        dbc.Button("Override schließen", id="mp-ov-close",
                                   color="secondary", outline=True, size="sm",
                                   n_clicks=0),
                    ],
                    className="d-flex gap-2 align-items-center",
                ),
            ]
        ),
        className="mb-4",
    )


# ── Render-Bausteine ────────────────────────────────────────────────────


def _kpi_header(meta: dict, snap: date, diags: list[Diagnostic]) -> html.Div:
    counts = count_by_severity(diags)
    return kpi_band(
        [
            {"label": "Snapshot", "value": snap.strftime("%d.%m.%Y")},
            {"label": "Rebalance-Modus", "value": str(meta.get("rebalance_mode", "–"))},
            {"label": "Titel", "value": fmt_de(meta.get("n_titles") or 0, 0)},
            {"label": "Ex-ante-TE", "value": _fmt_pct(meta.get("te_ex_ante"), 2)},
            {
                "label": "Turnover (einseitig)",
                "value": _fmt_pct(meta.get("turnover_oneway")),
            },
            {
                "label": "Diagnosen",
                "value": (
                    f"{counts[SEV_ERROR]} F · {counts[SEV_WARNING]} W · "
                    f"{counts[SEV_INFO]} I"
                ),
                "tone": "down" if counts[SEV_ERROR] else (
                    "warn" if counts[SEV_WARNING] else None
                ),
            },
        ]
    )


def _diagnostics_block(diags: list[Diagnostic]) -> html.Div:
    if not diags:
        return html.Div(dbc.Alert("Keine Diagnosen.", color="success"))
    from app.ui.theme import diagnostics_panel

    return diagnostics_panel(
        diags, title="Diagnosen der Portfoliokonstruktion", start_collapsed=False
    )


def _portfolio_table(portfolio: pd.DataFrame, universe: pd.DataFrame,
                     trades: pd.DataFrame, current: dict[str, float]) -> html.Div:
    df = portfolio.copy()
    uni = universe.copy()
    if "uid" in uni.columns:
        uni = uni.drop_duplicates("uid").set_index(
            uni.drop_duplicates("uid")["uid"].astype(str)
        )
    for col in ("ticker", "name", "sector", "region", "trend_warning"):
        if col in uni.columns:
            df[col] = df["uid"].map(uni[col])
    df["weight_current"] = df["uid"].map(current).fillna(0.0)
    df["delta_w"] = df["weight_effective"].fillna(0.0) - df["weight_current"]
    df["override"] = df["override_id"].map(
        lambda v: f"#{int(v)}" if pd.notna(v) else ""
    )
    show = [
        c
        for c in (
            "ticker", "uid", "name", "sector", "region", "composite_z",
            "composite_pct", "zone_v2", "weight_current", "weight_model",
            "weight_effective", "delta_w", "cte", "action", "reason",
            "trend_warning", "override",
        )
        if c in df.columns
    ]
    # Formatierung (Prozent, Z-Werte, cTE) übernimmt render_table zentral.
    out = df[show].copy()
    out = out.sort_values("weight_effective", ascending=False)
    table = render_table(out, id="mp-portfolio-table", page_size=40)
    table.export_format = "csv"
    return html.Div(table)


def _trade_table(trades: pd.DataFrame) -> html.Div:
    if trades is None or trades.empty:
        return html.Div(dbc.Alert("Keine Trades.", color="info"))
    active = trades[trades["action"] != ACTION_HOLD].copy()
    if active.empty:
        return html.Div(dbc.Alert("Keine Trades (alles HALTEN).", color="info"))
    # Formatierung/Labels der Gewichtspalten übernimmt render_table zentral.
    show = ["uid", "action", "weight_current", "weight_target", "delta_w",
            "reason", "trend_warning", "zone_v2"]
    table = render_table(
        active[[c for c in show if c in active.columns]],
        id="mp-trade-table",
        page_size=40,
    )
    table.export_format = "csv"
    return html.Div(table)


def _exposures_block(portfolio: pd.DataFrame, universe: pd.DataFrame,
                     settings, snap: date) -> html.Div:
    uni = universe.copy()
    if "uid" in uni.columns:
        uni = uni.drop_duplicates("uid").set_index(
            uni.drop_duplicates("uid")["uid"].astype(str)
        )
    pf = portfolio.set_index("uid")
    benchmark = load_benchmark_weights(
        settings,
        universe_regions=sorted(
            uni.get("region", pd.Series(dtype=str)).dropna().unique()
        ),
        asof=snap,
        universe=uni,
    )
    blocks: list = []
    for dim, bm, band in (
        ("sector", benchmark.sector, settings.pc_sector_band),
        ("region", benchmark.region, settings.pc_region_band),
    ):
        if dim not in uni.columns:
            continue
        title = "Sektoren" if dim == "sector" else "Regionen"
        weights = pf["weight_effective"].astype(float)
        groups = pd.Series(
            [str(uni[dim].get(u, "Unbekannt")) for u in pf.index], index=pf.index
        )
        agg = weights.groupby(groups).sum()
        names = sorted(set(agg.index) | set((bm or {}).keys()))
        rows = pd.DataFrame(
            [
                {
                    title[:-2]: n,
                    "Portfolio": _fmt_pct(agg.get(n, 0.0)),
                    "Benchmark": _fmt_pct((bm or {}).get(n, 0.0)),
                    "aktiv": _fmt_pct(float(agg.get(n, 0.0)) - float((bm or {}).get(n, 0.0))),
                }
                for n in names
            ]
        )
        subtitle = f"Band ± {_fmt_pct(band, 0)}"
        if bm is None:
            subtitle += " · Benchmark-Restriktion ausgesetzt (siehe Diagnosen)"
        blocks.append(
            html.Div(
                [
                    html.Div(f"{title} vs. Benchmark", className="fw-bold"),
                    html.Div(subtitle, className="small text-muted mb-1"),
                    render_basic_table(rows),
                ],
                className="mb-3",
            )
        )

    z_cols = [f"z_{f}" for f in V2_FACTOR_NAMES if f"z_{f}" in uni.columns]
    if z_cols:
        pf_rows = uni.loc[[u for u in pf.index if u in uni.index]]
        rows = pd.DataFrame(
            [
                {
                    "Faktor": c[2:],
                    "Portfolio Ø z": fmt_de(pf_rows[c].mean(), 2),
                    "Universum Ø z": fmt_de(uni[c].mean(), 2),
                }
                for c in z_cols
            ]
        )
        blocks.append(
            html.Div(
                [
                    html.Div("Faktor-Exposure (Plausibilisierung)",
                             className="fw-bold mb-1"),
                    render_basic_table(rows),
                ],
                className="mb-3",
            )
        )
    return html.Div(blocks)


def _render_result(result: dict, snap: date, universe: pd.DataFrame,
                   current: dict[str, float]) -> html.Div:
    diags = result["diagnostics"]
    sections = [
        _kpi_header(result["meta"], snap, diags),
        section_header("Diagnosen", "sortiert nach Schweregrad"),
        _diagnostics_block(diags),
    ]
    if result["portfolio"].empty:
        sections.append(
            dbc.Alert(
                "Monitor-Modus: kein Zielportfolio-Update — nur Zonen und "
                "Diagnosen.",
                color="info",
            )
        )
    else:
        sections.extend(
            [
                section_header("Zielportfolio", "Export als CSV möglich"),
                _portfolio_table(
                    result["portfolio"], universe, result["trades"].trades, current
                ),
                section_header("Trade-Liste", "inkl. VERSCHOBEN (Turnover-Budget)"),
                _trade_table(result["trades"].trades),
                section_header("Exposures"),
                _exposures_block(result["portfolio"], universe,
                                 STATE.settings, snap),
            ]
        )
    return html.Div(sections)


def _render_stored(snap: date) -> html.Div:
    portfolio = persistence.load_model_portfolio(snap)
    meta = persistence.load_model_portfolio_meta(snap) or {}
    if portfolio is None:
        return dbc.Alert("Kein gespeichertes Zielportfolio für dieses Datum.",
                         color="warning")
    diags = diags_from_json(meta.get("diagnostics"))
    # Formatierung (Prozent, Z-Werte, cTE) übernimmt render_table zentral.
    df = portfolio.copy()
    table = render_table(
        df[
            [
                "uid", "composite_z", "composite_pct", "zone_v2",
                "weight_model", "weight_effective", "cte", "action", "reason",
                "override_id",
            ]
        ].sort_values("weight_effective", ascending=False),
        id="mp-portfolio-table",
        page_size=40,
    )
    table.export_format = "csv"
    return html.Div(
        [
            _kpi_header(meta, snap, diags),
            section_header("Diagnosen (damaliger Lauf)"),
            _diagnostics_block(diags),
            section_header("Zielportfolio (historisiert)"),
            table,
        ]
    )


# ── Callbacks ───────────────────────────────────────────────────────────


def _triggered_id():
    """``ctx.triggered_id`` — ``None`` außerhalb eines Callback-Kontexts
    (macht die Callbacks direkt testbar)."""
    try:
        from dash import ctx

        return ctx.triggered_id
    except Exception:  # noqa: BLE001
        return None


@callback(
    Output("mp-content", "children"),
    Output("mp-status", "children"),
    Input("mp-dry", "n_clicks"),
    Input("mp-save", "n_clicks"),
    Input("mp-history", "value"),
    State("mp-mode", "value"),
)
def _run(n_dry, n_save, history, mode_choice):
    trigger = _triggered_id()
    if history and history != "live" and trigger == "mp-history":
        return _render_stored(date.fromisoformat(history)), ""

    df = STATE.scored
    if df is None or df.empty or "composite_z" not in df.columns:
        return (
            dbc.Alert(
                "Kein Universum mit Composite v2 geladen — bitte zuerst auf "
                "/daten-import ein Universum importieren.",
                color="warning",
            ),
            "",
        )
    snap = snapshot_date_from_universe(STATE.raw, None)
    settings = STATE.settings
    overrides = persistence.load_overrides()
    last_meta = persistence.load_model_portfolio_meta()
    current = STATE.portfolio_weights()
    mode = None if (mode_choice or "auto") == "auto" else mode_choice

    save = trigger == "mp-save"
    try:
        uids = sorted(set(df.get("uid", pd.Series(dtype=str)).astype(str)))
        risk_cache = load_risk_cache(uids, settings, asof=snap)
        result = build_model_portfolio(
            df,
            settings,
            current,
            mode=mode,
            snapshot_date=snap,
            overrides=overrides,
            risk_cache=risk_cache,
            last_meta=last_meta,
        )
    except ValueError as exc:
        return dbc.Alert(f"Konfigurationsfehler: {exc}", color="danger"), ""

    if mode is not None:
        # Manueller Modus-Override wird protokolliert (Spec 7.1).
        result["diagnostics"].append(
            Diagnostic(
                SEV_INFO,
                "mode_manual_override",
                f"Rebalance-Modus manuell auf '{mode}' gesetzt",
            )
        )
        log.info("Rebalance-Modus manuell überschrieben: %s", mode)

    status: object = ""
    if save and result["mode"] != "monitor":
        try:
            persistence.save_model_portfolio(result["portfolio"],
                                             result["meta"], snap)
            status = dbc.Alert(
                f"Zielportfolio gespeichert ({snap:%d.%m.%Y}).", color="success"
            )
        except Exception as exc:  # noqa: BLE001
            status = dbc.Alert(
                f"Speichern fehlgeschlagen: {exc}", color="warning"
            )
    elif save:
        status = dbc.Alert(
            "Monitor-Modus: kein Zielportfolio-Update — nichts gespeichert.",
            color="info",
        )
    return _render_result(result, snap, df, current), status


@callback(
    Output("mp-override-table", "children"),
    Output("mp-override-status", "children"),
    Input("mp-ov-create", "n_clicks"),
    Input("mp-ov-close", "n_clicks"),
    Input("mp-content", "children"),
    State("mp-ov-uid", "value"),
    State("mp-ov-direction", "value"),
    State("mp-ov-weight", "value"),
    State("mp-ov-owner", "value"),
    State("mp-ov-expires", "date"),
    State("mp-ov-reason", "value"),
    State("mp-ov-close-id", "value"),
)
def _overrides(n_create, n_close, _content, uid, direction, weight, owner,
               expires, reason, close_id):
    status: object = ""
    trigger = _triggered_id()
    if trigger == "mp-ov-create":
        try:
            target = None
            if weight not in (None, ""):
                target = float(str(weight).replace(",", "."))
            exp = date.fromisoformat(expires[:10]) if expires else None
            oid = persistence.save_override(
                str(uid or "").strip(),
                direction or "",
                str(reason or ""),
                str(owner or ""),
                exp,
                target_weight=target,
            )
            status = dbc.Alert(f"Override #{oid} angelegt.", color="success")
        except (ValueError, RuntimeError) as exc:
            status = dbc.Alert(f"Override abgelehnt: {exc}", color="danger")
    elif trigger == "mp-ov-close":
        try:
            persistence.close_override(int(close_id), owner or "UI", "")
            status = dbc.Alert(f"Override #{close_id} geschlossen.",
                               color="success")
        except (ValueError, TypeError, RuntimeError) as exc:
            status = dbc.Alert(f"Schließen fehlgeschlagen: {exc}",
                               color="danger")

    overrides = persistence.load_overrides()
    if overrides is None or overrides.empty:
        return dbc.Alert("Keine Overrides angelegt.", color="info"), status
    show = overrides[
        [
            "id", "uid", "direction", "target_weight", "reason", "owner",
            "created_at", "expires_at", "status",
        ]
    ].copy()
    return html.Div(render_basic_table(show), className="mb-3"), status


register_page(__name__, path="/modellportfolio", name="Modellportfolio",
              layout=layout)
