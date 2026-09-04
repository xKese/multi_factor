"""Berechnungs-Orchestrierung und Markdown-Report „Risiko & Benchmark".

``compute_risk_report`` führt alle Berechnungsmodule gegen den Kurscache
aus (keine API-Calls — Daten holt vorher ``market_data.update_cache``) und
liefert ein Ergebnis-Dict, das sowohl der Markdown-Builder als auch die
Dash-Seite konsumieren. ``build_markdown_report`` erzeugt daraus den
deutschen Report (Dezimalkomma, Prozente mit einer Nachkommastelle,
bp ganzzahlig), ``write_report`` schreibt ihn nach
``<report_dir>/risiko_benchmark_YYYY-MM-DD.md``.

Import-Hinweis: ``app.ui.formatters`` ist Dash-frei und wird direkt
importiert (nicht über ``app.ui``, dessen ``__init__`` Dash zieht) —
der CLI-Lauf braucht kein Dash.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from app.ui.formatters import fmt_de, fmt_int, fmt_percent, fmt_signed_percent

from . import market_data, risk_mcte, risk_scenarios
from .config import Settings
from .risk_metrics import (
    TE_WINDOWS,
    active_sector_weights,
    daily_returns,
    ex_post_metrics,
    portfolio_returns,
    rolling_te,
)

log = logging.getLogger(__name__)

DISCLAIMER = (
    "Interne Analyse, keine Anlageberatung; renditebasierte Schätzung, "
    "rückwärtsgerichtet."
)

# Anzahl der hervorgehobenen Top-Risikotreiber im Management Summary.
TOP_DRIVERS = 5


def _benchmark_sector_weights(
    settings: Settings, scored: pd.DataFrame | None
) -> tuple[dict[str, float], str]:
    """Benchmark-Sektorgewichte für die aktive Sektorallokation.

    Folgt ``pc_benchmark_source`` (konsistent zur Portfoliokonstruktion):
    ``"universe"`` = marktkapitalisierungsgewichtete Anteile des geladenen
    Universums; ``"static"`` (oder kein Universum) = manuell gepflegte
    ACWI-Gewichte aus den Einstellungen. Liefert (Gewichte, Quelle).
    """
    if (
        settings.pc_benchmark_source == "universe"
        and scored is not None
        and not scored.empty
        and "sector" in scored.columns
    ):
        from .portfolio_construction import universe_benchmark_weights

        bm = universe_benchmark_weights(scored)
        if bm.sector:
            return bm.sector, "universe"
    return dict(settings.risk_benchmark_sector_weights), "static"


def compute_risk_report(
    tickers: list[str],
    weights: dict[str, float],
    settings: Settings,
    scored: pd.DataFrame,
    asof: date,
    variant: str = "fest",
    only: list[str] | None = None,
) -> dict:
    """Rechnet alle Module gegen den Cache und sammelt die Ergebnisse.

    ``only`` filtert die Szenario-Namen (None = alle). Einzelne Module
    dürfen an dünner Datenlage scheitern — sie hinterlassen dann eine
    deutsche Fehlnotiz statt den Lauf zu stoppen. Raises ``ValueError``
    nur, wenn der Benchmark-Cache komplett fehlt.
    """

    panel = market_data.load_price_panel(tickers, settings, asof)
    quality = panel.quality

    rets = daily_returns(panel.prices_eur)
    bm_rets = panel.benchmark.pct_change(fill_method=None)

    pf = portfolio_returns(rets, weights, variant)
    expost = ex_post_metrics(pf, bm_rets)
    active = (pf - bm_rets).dropna()
    rolling = {
        label: rolling_te(active, window).dropna()
        for label, window in TE_WINDOWS.items()
    }

    sectors: dict[str, str] = {}
    if scored is not None and not scored.empty and "sector" in scored.columns:
        # Keyed by uid (Fallback Ticker): bei Ticker-Kollisionen würde ein
        # Ticker-Key sonst den Sektor der jeweils letzten Zeile erhalten.
        keys = scored["uid"] if "uid" in scored.columns else scored["ticker"]
        sectors = {
            str(t): s
            for t, s in zip(keys, scored["sector"])
            if isinstance(s, str) and s
        }

    mcte = None
    mcte_fehler = ""
    ranking = pd.DataFrame()
    sektor_cte = pd.DataFrame()
    try:
        mcte = risk_mcte.compute_mcte(rets, bm_rets, weights)
        ranking = risk_mcte.join_signals(mcte.ranking, scored)
        sektor_cte = risk_mcte.aggregate_by_sector(mcte.ranking, sectors)
    except ValueError as exc:
        mcte_fehler = str(exc)
        log.warning("MCTE nicht berechenbar: %s", exc)

    bm_sector_weights, sektor_benchmark_quelle = _benchmark_sector_weights(
        settings, scored
    )
    sektor_allokation = active_sector_weights(
        weights, sectors, bm_sector_weights
    )

    szenarien: list[risk_scenarios.ScenarioResult] = []
    unbekannte_szenarien: list[str] = []
    if only is not None:
        unbekannte_szenarien = [
            n for n in only if n not in settings.risk_scenario_windows
        ]
    for name, window in settings.risk_scenario_windows.items():
        if only is not None and name not in only:
            continue
        start, end = (date.fromisoformat(window[0]), date.fromisoformat(window[1]))
        szenarien.append(
            risk_scenarios.replay_scenario(
                panel.prices_eur, panel.benchmark, weights, name, start, end
            )
        )

    macro = market_data.load_macro(asof)
    betas = pd.DataFrame()
    schocks = pd.DataFrame()
    schock_fehler = ""
    if macro.dropna(how="all").empty:
        schock_fehler = (
            "Makro-Reihen (10Y-Yield, WTI, FX) fehlen im Cache — bitte "
            "'update' ausführen."
        )
    else:
        weekly, factors = risk_scenarios.weekly_factor_panel(
            panel.prices_eur, panel.benchmark, macro
        )
        betas = risk_scenarios.estimate_betas(weekly, factors)
        schocks = risk_scenarios.apply_shocks(
            betas, weights, settings.risk_factor_shocks
        )

    return {
        "asof": asof,
        "erstellt": datetime.now(),
        "scoring_version": settings.scoring_version,
        "variante": variant,
        "benchmark": settings.risk_benchmark_symbol,
        "tickers": tickers,
        "weights": weights,
        "quality": quality,
        "expost": expost,
        "rolling_te": rolling,
        "aktive_rendite": active,
        "mcte": mcte,
        "mcte_fehler": mcte_fehler,
        "ranking": ranking,
        "sektor_cte": sektor_cte,
        "sektor_allokation": sektor_allokation,
        "sektor_benchmark_quelle": sektor_benchmark_quelle,
        "szenarien": szenarien,
        "unbekannte_szenarien": unbekannte_szenarien,
        "betas": betas,
        "schocks": schocks,
        "schock_fehler": schock_fehler,
    }


# ── Markdown-Bausteine ─────────────────────────────────────────────────────


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _fmt_bp(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    return f"{fmt_int(round(float(value)))} bp"


def _fmt_date(value) -> str:
    if value is None:
        return "-"
    return pd.Timestamp(value).strftime("%d.%m.%Y")


def _v2_report(res: dict) -> bool:
    """True, wenn Scoring v2 primär ist und das Ranking v2-Werte trägt.

    ``join_signals`` legt die v2-Spalten immer an (ggf. leer) — entscheidend
    ist daher, ob mindestens ein Composite-Wert vorliegt.
    """
    ranking = res.get("ranking")
    return (
        res.get("scoring_version") == "v2"
        and isinstance(ranking, pd.DataFrame)
        and "composite_score" in ranking.columns
        and ranking["composite_score"].notna().any()
    )


def _text(value) -> str:
    """NA-sicherer Zellentext (pd.NA verträgt kein ``or``)."""

    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return "-"
    return str(value) or "-"


def _section_summary(res: dict) -> str:
    expost = res["expost"]
    mcte = res["mcte"]
    lines = ["## 1. Management Summary", ""]
    bullet: list[str] = []
    if mcte is not None:
        bullet.append(
            f"- **Ex-ante Tracking Error: {fmt_percent(mcte.te_ledoit_wolf)}** "
            f"(Ledoit-Wolf; Sample-Schätzung {fmt_percent(mcte.te_sample)})."
        )
    else:
        bullet.append(f"- Ex-ante TE nicht berechenbar: {res['mcte_fehler']}")
    bullet.append(
        f"- Ex-post Tracking Error (1 Jahr): {fmt_percent(expost.get('te_1j'))}, "
        f"Gesamtperiode: {fmt_percent(expost.get('te_gesamt'))} "
        f"über {fmt_int(expost.get('n_tage'))} Handelstage."
    )
    bullet.append(
        f"- Aktive Rendite p. a.: {fmt_signed_percent(expost.get('aktive_rendite_pa'))}, "
        f"Information Ratio: {fmt_de(expost.get('information_ratio'))}, "
        f"aktives Beta: {fmt_de(expost.get('aktives_beta'))}."
    )

    if not res["ranking"].empty:
        top = res["ranking"].head(TOP_DRIVERS)
        action_col = "zone_v2" if _v2_report(res) else "recommendation"
        drivers = []
        for _, r in top.iterrows():
            extra = []
            if isinstance(r.get(action_col), str) and r[action_col]:
                extra.append(r[action_col])
            if isinstance(r.get("sma_signal"), str) and r["sma_signal"] not in ("", "-"):
                extra.append(r["sma_signal"])
            suffix = f" ({', '.join(extra)})" if extra else ""
            drivers.append(f"**{r['ticker']}** {_fmt_bp(r['cte_bp'])}{suffix}")
        bullet.append(f"- Top-{TOP_DRIVERS}-Risikotreiber (CTE): " + "; ".join(drivers) + ".")

    quality = res["quality"]
    if quality.unresolved or quality.missing_cache:
        gewichte = res["weights"]
        anteil = sum(
            gewichte.get(t, 0.0)
            for t in set(quality.unresolved) | set(quality.missing_cache)
        )
        bullet.append(
            f"- ⚠ {len(quality.unresolved) + len(quality.missing_cache)} Titel "
            f"ohne Kursdaten ({fmt_percent(anteil)} des Portfoliogewichts) — "
            "der ausgewiesene TE ist tendenziell zu niedrig (Details in "
            "Abschnitt 7)."
        )
    nicht_belastbar = [s.name for s in res["szenarien"] if not s.belastbar]
    if nicht_belastbar:
        bullet.append(
            "- ⚠ Szenarien als „nicht belastbar“ markiert: "
            + ", ".join(nicht_belastbar)
            + "."
        )
    return "\n".join(lines + bullet)


def _section_expost(res: dict) -> str:
    expost = res["expost"]
    rows = [
        ["Tracking Error (Gesamtperiode)", fmt_percent(expost.get("te_gesamt"))],
        ["Tracking Error (rollierend 1 Jahr, aktuell)", fmt_percent(expost.get("te_1j"))],
        ["Tracking Error (rollierend 3 Jahre, aktuell)", fmt_percent(expost.get("te_3j"))],
        ["Aktive Rendite p. a.", fmt_signed_percent(expost.get("aktive_rendite_pa"))],
        ["Information Ratio", fmt_de(expost.get("information_ratio"))],
        ["Aktives Beta", fmt_de(expost.get("aktives_beta"))],
        ["Korrelation", fmt_de(expost.get("korrelation"))],
        ["Upside Capture", fmt_percent(expost.get("upside_capture"))],
        ["Downside Capture", fmt_percent(expost.get("downside_capture"))],
        ["Max. relativer Drawdown", fmt_percent(expost.get("max_rel_drawdown"))],
        [
            "Zeitraum",
            f"{_fmt_date(expost.get('start'))} – {_fmt_date(expost.get('ende'))} "
            f"({fmt_int(expost.get('n_tage'))} Handelstage)",
        ],
    ]
    parts = [
        "## 2. Tracking Error & Kennzahlen",
        "",
        f"Portfoliovariante: **{res['variante']}** "
        "(fest = fixe Gewichte, täglich rebalanced; buyhold = Drift ab Start). "
        "Alle Werte in EUR ohne Currency-Hedging.",
        "",
        _table(["Kennzahl", "Wert"], rows),
    ]

    roll = res["rolling_te"].get("1J", pd.Series(dtype=float))
    if len(roll):
        monthly = roll.resample("ME").last().dropna().tail(12)
        parts += [
            "",
            "Rollierender 1-Jahres-TE (Monatsend-Stände, letzte 12 Monate):",
            "",
            _table(
                ["Monat", "TE (1J)"],
                [
                    [ts.strftime("%m/%Y"), fmt_percent(v)]
                    for ts, v in monthly.items()
                ],
            ),
        ]
    return "\n".join(parts)


def _section_mcte(res: dict) -> str:
    parts = ["## 3. Risikobeiträge je Einzeltitel (MCTE)", ""]
    mcte = res["mcte"]
    if mcte is None:
        parts.append(f"Nicht berechenbar: {res['mcte_fehler']}")
        return "\n".join(parts)

    parts += [
        f"Ex-ante TE **{fmt_percent(mcte.te_ledoit_wolf)}** (Ledoit-Wolf, "
        f"Shrinkage {fmt_de(mcte.shrinkage)}) vs. "
        f"{fmt_percent(mcte.te_sample)} (Sample-Kovarianz) über "
        f"{fmt_int(mcte.n_tage)} gemeinsame Handelstage (Robustheits-Check).",
        "",
    ]
    if mcte.ausgeschlossen:
        parts += [
            f"⚠ Ohne ausreichende Historie im Kovarianzfenster: "
            f"{', '.join(mcte.ausgeschlossen)} "
            f"({fmt_percent(mcte.ausgeschlossen_gewicht)} Gewicht) — "
            "Gewichte der übrigen Titel renormalisiert.",
            "",
        ]

    v2 = _v2_report(res)
    score_col = "composite_score" if v2 else "total_score"
    action_col = "zone_v2" if v2 else "recommendation"
    rows = []
    for i, (_, r) in enumerate(res["ranking"].iterrows()):
        ticker = f"**{r['ticker']}**" if i < TOP_DRIVERS else str(r["ticker"])
        rows.append(
            [
                ticker,
                fmt_percent(r["gewicht"]),
                _fmt_bp(r["cte_bp"]),
                fmt_percent(r["mcte"]),
                fmt_de(r.get(score_col), 1),
                _text(r.get(action_col)),
                _text(r.get("sma_signal")),
            ]
        )
    parts.append(
        _table(
            [
                "Titel",
                "Gewicht",
                "CTE",
                "MCTE (je 100 % Gewicht)",
                "Composite v2" if v2 else "Score",
                "Zone" if v2 else "Empfehlung",
                "SMA-Signal",
            ],
            rows,
        )
    )

    if not res["sektor_cte"].empty:
        parts += [
            "",
            "CTE je GICS-Sektor:",
            "",
            _table(
                ["Sektor", "Gewicht", "CTE"],
                [
                    [str(r["sektor"]), fmt_percent(r["gewicht"]), _fmt_bp(r["cte_bp"])]
                    for _, r in res["sektor_cte"].iterrows()
                ],
            ),
        ]
    return "\n".join(parts)


def _section_sectors(res: dict) -> str:
    parts = ["## 4. Aktive Sektorallokation", ""]
    df = res["sektor_allokation"]
    if df.empty:
        parts.append("Keine Sektordaten verfügbar (Universum nicht geladen?).")
        return "\n".join(parts)
    if res.get("sektor_benchmark_quelle") == "universe":
        quelle = (
            "Portfolio-Sektorgewichte vs. Universum (marktkapitalisierungs-"
            "gewichtete Anteile des Daten-Imports), aktive Abweichung in "
            "Prozentpunkten:"
        )
    else:
        quelle = (
            "Portfolio-Sektorgewichte vs. MSCI-ACWI-Gewichte "
            "(statisch, quartalsweise gepflegt), aktive Abweichung in "
            "Prozentpunkten:"
        )
    parts += [
        quelle,
        "",
        _table(
            ["Sektor", "Portfolio", "Benchmark", "Aktiv"],
            [
                [
                    str(r["sektor"]),
                    fmt_percent(r["pf_gewicht"]),
                    fmt_percent(r["bm_gewicht"]),
                    fmt_signed_percent(r["aktiv"]) + "-Pkt.",
                ]
                for _, r in df.iterrows()
            ],
        ),
    ]
    return "\n".join(parts)


def _section_scenarios(res: dict) -> str:
    parts = ["## 5. Historische Szenarien (Replay, heutige Gewichte)", ""]
    if not res["szenarien"]:
        parts.append("Keine Szenarien gerechnet.")
        return "\n".join(parts)
    rows = []
    for s in res["szenarien"]:
        label = s.name if s.belastbar else f"{s.name} ⚠ nicht belastbar"
        rows.append(
            [
                label,
                f"{_fmt_date(s.start)} – {_fmt_date(s.ende)}",
                fmt_percent(s.coverage),
                fmt_signed_percent(s.pf_rendite),
                fmt_signed_percent(s.bm_rendite),
                fmt_signed_percent(s.aktiv),
                fmt_percent(s.max_drawdown),
                (
                    f"{_fmt_date(s.schlechtester_tag)} "
                    f"({fmt_signed_percent(s.schlechtester_tag_rendite)})"
                    if s.schlechtester_tag
                    else "-"
                ),
            ]
        )
    parts.append(
        _table(
            [
                "Szenario",
                "Fenster",
                "Abdeckung",
                "Portfolio",
                "Benchmark",
                "Aktiv",
                "Max. Drawdown",
                "Schlechtester Tag",
            ],
            rows,
        )
    )
    hints = [
        f"- {s.name}: fehlende Titel {', '.join(s.fehlende)}"
        for s in res["szenarien"]
        if s.fehlende
    ]
    if hints:
        parts += ["", "Renormalisiert auf verfügbare Titel:"] + hints
    return "\n".join(parts)


def _section_shocks(res: dict) -> str:
    parts = ["## 6. Hypothetische Faktor-Schocks", ""]
    if res["schock_fehler"]:
        parts.append(f"Nicht berechenbar: {res['schock_fehler']}")
        return "\n".join(parts)
    schocks = res["schocks"]
    if schocks.empty:
        parts.append("Keine Schock-Szenarien konfiguriert.")
        return "\n".join(parts)
    parts += [
        "Wochenrenditen (3 Jahre) je Titel regressiert auf Markt "
        "(Benchmark), Δ 10Y-Treasury (bp), WTI und EURUSD; Schocks über die "
        "Betas propagiert. Benchmark reagiert mit Beta 1 auf den "
        "Markt-Schock.",
        "",
        _table(
            ["Szenario", "Portfolio-P&L", "Benchmark-P&L", "Aktiver Effekt", "Abdeckung"],
            [
                [
                    str(r["szenario"]),
                    fmt_signed_percent(r["pf_pnl"]),
                    fmt_signed_percent(r["bm_pnl"]),
                    fmt_signed_percent(r["aktiv"]),
                    fmt_percent(r["abdeckung"]),
                ]
                for _, r in schocks.iterrows()
            ],
        ),
    ]
    betas = res["betas"]
    if not betas.empty:
        low = betas[betas["geringe_guete"]]
        if not low.empty:
            parts += [
                "",
                f"⚠ Geringe Erklärungsgüte (R² < {fmt_de(risk_scenarios.MIN_R2, 1)} "
                "oder zu kurze Historie) — Schock-P&L dieser Titel ist "
                "entsprechend unsicher: "
                + ", ".join(
                    f"{r['ticker']} (R² {fmt_de(r['r2'])})" for _, r in low.iterrows()
                ),
            ]
        rows = [
            [
                str(r["ticker"]),
                fmt_de(r["beta_markt"]),
                fmt_de(r["beta_zins_bp"] * 10_000.0)
                if pd.notna(r["beta_zins_bp"])
                else "-",
                fmt_de(r["beta_oel"]),
                fmt_de(r["beta_usd"]),
                fmt_de(r["r2"]),
                fmt_int(r["n_obs"]),
            ]
            for _, r in betas.iterrows()
        ]
        parts += [
            "",
            "Regressions-Betas je Titel (Zins-Beta skaliert auf 100 bp):",
            "",
            _table(
                ["Titel", "β Markt", "β Zins (100 bp)", "β Öl", "β USD", "R²", "Wochen"],
                rows,
            ),
        ]
    return "\n".join(parts)


def _section_quality(res: dict) -> str:
    quality = res["quality"]
    parts = ["## 7. Datenqualität", ""]
    rows = [
        [
            "API-Abruf (Cache-Stand)",
            quality.fetched_at.strftime("%d.%m.%Y %H:%M")
            if quality.fetched_at
            else "-",
        ],
        ["Nicht auflösbare Ticker", ", ".join(quality.unresolved) or "keine"],
        [
            "Ticker ohne Kurscache",
            ", ".join(quality.missing_cache) or "keine",
        ],
    ]
    parts.append(_table(["Punkt", "Wert"], rows))

    if quality.gaps:
        parts += [
            "",
            "Datenlücken (> 3 Handelstage forward-gefillt → NaN):",
            "",
            _table(
                ["Titel", "Lücken-Tage"],
                [[t, fmt_int(n)] for t, n in sorted(quality.gaps.items())],
            ),
        ]
    if quality.last_price:
        parts += [
            "",
            "Letzter Kurstag je Titel:",
            "",
            _table(
                ["Titel", "Letzter Kurs"],
                [
                    [t, _fmt_date(d)]
                    for t, d in sorted(quality.last_price.items())
                ],
            ),
        ]
    if quality.notes:
        parts += ["", "Hinweise:"] + [f"- {n}" for n in quality.notes]
    parts += [
        "",
        "Methodik: Adjusted Close (Splits/Dividenden bereinigt), Umrechnung "
        "nach EUR über FX_DAILY — die Ergebnisse bilden die EUR-Sicht ohne "
        "Currency-Hedging ab. Gemeinsamer Kalender = Handelstage des "
        "Benchmarks; fehlende Kurse werden max. 3 Tage forward-gefillt, "
        "darüber hinaus als Datenlücke geführt.",
    ]
    return "\n".join(parts)


def build_markdown_report(res: dict) -> str:
    """Setzt den kompletten deutschen Markdown-Report zusammen."""

    header = [
        "# Risiko & Benchmark",
        "",
        f"**Stichtag: {_fmt_date(res['asof'])}** · Benchmark: {res['benchmark']} "
        f"(EUR-Sicht) · erstellt {res['erstellt'].strftime('%d.%m.%Y %H:%M')}",
        "",
        f"> {DISCLAIMER}",
        "",
        "",
    ]
    sections = [
        _section_summary(res),
        _section_expost(res),
        _section_mcte(res),
        _section_sectors(res),
        _section_scenarios(res),
        _section_shocks(res),
        _section_quality(res),
    ]
    return "\n".join(header) + "\n\n".join(sections) + "\n"


def write_report(res: dict, out_dir: str | Path) -> Path:
    """Schreibt den Report als Markdown-Datei; liefert den Pfad."""

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"risiko_benchmark_{res['asof'].isoformat()}.md"
    path.write_text(build_markdown_report(res), encoding="utf-8")
    return path
