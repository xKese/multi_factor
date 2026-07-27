"""Render-Bausteine für Agenten-Tiefenanalysen (TradingAgents).

Umsetzung des Design-Handoffs „Agenten-Tiefenanalyse" (Turn 2):
- ``progress_checklist``  → 4-Phasen-Pipeline-Karte (Zustand „Laufend", 2a)
- ``result_view``         → Rating-Hero + Quant-vs-Agenten-Panel + Report-Karten (2a)
- ``compact_status_card`` → Kompakt-Statuskarte der Ad-hoc-Seite (2b)
- ``rating_badge``        → Badge für Verlaufstabelle und Header-Chip

Alle Farben/Abstände über die bestehenden ``--ms-*``-Tokens bzw. die
``ms-agent-*``-Klassen in ``app/assets/morningstar.css``.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import dash_bootstrap_components as dbc
import pandas as pd
from dash import ALL, Input, Output, State, callback, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from app.core import persistence


def _display_tz() -> ZoneInfo:
    """Anzeige-Zeitzone der App (Server laufen oft in UTC, z. B. Replit)."""
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE", "Europe/Berlin"))
    except Exception:  # noqa: BLE001 — unbekannte Zone: UTC statt Crash
        return ZoneInfo("UTC")


def fmt_local_epoch(epoch, fmt: str = "%H:%M") -> str:
    """Unix-Epoch (``time.time()``) → lokale Wandzeit; ``""`` bei Fehler."""
    if epoch is None:
        return ""
    try:
        return datetime.fromtimestamp(float(epoch), tz=_display_tz()).strftime(fmt)
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def fmt_local_dt(value, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """DB-Zeitstempel → lokale Wandzeit.

    Naive Werte werden als UTC interpretiert (SQLites ``CURRENT_TIMESTAMP``
    schreibt UTC); aware Werte werden konvertiert. Unlesbare Werte kommen
    als Rohtext zurück (fail-open im Render-Pfad).
    """
    if value is None:
        return "–"
    try:
        ts = pd.to_datetime(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.tz_convert(_display_tz()).strftime(fmt)
    except (ValueError, TypeError):
        return str(value)


# Reihenfolge, deutsche Titel und Agentenrollen der Report-Sektionen.
REPORT_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("market_report", "Marktanalyse", "Market Analyst"),
    ("sentiment_report", "Sentiment", "Social Analyst"),
    ("news_report", "News", "News Analyst"),
    ("fundamentals_report", "Fundamentalanalyse", "Fundamentals Analyst"),
    ("bull_history", "Bull-These", "Bull Researcher"),
    ("bear_history", "Bear-These", "Bear Researcher"),
    ("investment_plan", "Research-Fazit", "Research Manager"),
    ("trader_investment_plan", "Trading-Plan", "Trader"),
    ("risk_analysis", "Risiko-Debatte", "Risiko-Team"),
    ("final_trade_decision", "Risiko & Entscheidung", "Portfolio Manager"),
)

_RATING_TONE = {
    "Buy": "up",
    "Overweight": "up",
    "Hold": "warn",
    "Underweight": "down",
    "Sell": "down",
}

_RATING_SHORT = {
    "Buy": "B",
    "Overweight": "OW",
    "Hold": "H",
    "Underweight": "UW",
    "Sell": "S",
}

_PHASE_TITLES = ("1 · Analysten", "2 · Debatte", "3 · Risiko", "4 · Entscheidung")


def rating_tone(rating: str | None) -> str | None:
    return _RATING_TONE.get(rating or "")


def rating_short(rating: str | None) -> str:
    return _RATING_SHORT.get(rating or "", rating or "–")


def rating_badge(rating: str | None, label: str | None = "Agenten-Rating") -> html.Span:
    """Rating-Badge im Stil der bestehenden Verdict-Badges."""
    value = rating or "–"
    tone = rating_tone(value)
    klass = f"ms-badge is-{tone}" if tone else "ms-badge"
    children = []
    if label:
        children.append(html.Span(label, className="ms-badge-label"))
    children.append(html.Span(value, className="ms-badge-value"))
    return html.Span(children, className=klass)


# ── Phasen-Zuordnung & Fortschritt ─────────────────────────────────────────

def _agent_name(agent) -> str:
    """Namens-String eines Agenten-Eintrags.

    Der Service liefert Agenten teils als Dict ({team, name, status}), teils
    als reinen Namen — hier auf den Namen normalisieren, damit kein Dict in
    die (nach Name verschlüsselten) State-Lookups gerät.
    """
    if isinstance(agent, dict):
        return agent.get("name") or ""
    return agent or ""


def phase_of(agent_name: str) -> int:
    """Ordnet einen Agentennamen einer der vier Pipeline-Phasen (0–3) zu.

    Unbekannte Namen landen in Phase 0 (Analysten), Reihenfolge des Service
    bleibt erhalten. Risky/Safe/Neutral vor dem generischen "Analyst"-Match
    prüfen — sie heißen beim Service ebenfalls "… Analyst".
    """
    n = _agent_name(agent_name).lower()
    if "portfolio" in n:
        return 3
    if "trader" in n or "risky" in n or "safe" in n or "neutral" in n:
        return 2
    if "researcher" in n or "research manager" in n:
        return 1
    return 0


def assign_phases(agents: list[str]) -> list[list[str]]:
    """Agentenliste des Service in die vier Phasen einsortieren."""
    phases: list[list[str]] = [[], [], [], []]
    for name in agents or []:
        phases[phase_of(name)].append(name)
    return phases


def progress_stats(job: dict) -> dict:
    """Fortschrittskennzahlen eines laufenden Jobs (rein präsentational)."""
    agents = [_agent_name(a) for a in (job.get("agents") or [])]
    states = job.get("agent_states") or {}
    done = sum(1 for a in agents if states.get(a) == "completed")
    total = len(agents)
    active = next((a for a in agents if states.get(a) == "in_progress"), None)
    if active:
        phase = phase_of(active)
    elif total and done == total:
        phase = 3
    else:
        phase = max(
            [phase_of(a) for a in agents if states.get(a) == "completed"],
            default=0,
        )
    return {"agents": agents, "states": states, "done": done, "total": total,
            "active": active, "phase": phase}


# ── Text-Helfer ────────────────────────────────────────────────────────────

# Markdown-Rauschen: Emphasis/Heading/Code/Tabellen-Zeichen sowie
# Listen-/Zitat-Präfixe am Zeilenanfang — Binnen-Bindestriche (SMA-200)
# bleiben erhalten.
_MD_INLINE_NOISE = re.compile(r"[#*_`>|]+")
_MD_LINE_PREFIX = re.compile(r"(?m)^\s*[-•]\s+")


def first_sentences(text: str | None, n: int = 2, max_chars: int = 260) -> str:
    """Erste ~n Sätze eines Markdown-Reports als Plaintext-Teaser."""
    if not text:
        return ""
    plain = _MD_LINE_PREFIX.sub(" ", str(text))
    plain = _MD_INLINE_NOISE.sub(" ", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    if not plain:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", plain)
    teaser = " ".join(sentences[:n]).strip()
    if len(teaser) > max_chars:
        teaser = teaser[: max_chars - 1].rstrip() + "…"
    return teaser


def _quant_direction(recommendation: str | None) -> str | None:
    rec = (recommendation or "").upper()
    if rec in ("BUY", "STRONG BUY"):
        return "buy"
    if rec == "SELL":
        return "sell"
    if rec == "HOLD":
        return "hold"
    return None


def _agent_direction(rating: str | None) -> str | None:
    tone = rating_tone(rating)
    if tone == "up":
        return "buy"
    if tone == "down":
        return "sell"
    if tone == "warn":
        return "hold"
    return None


def delta_quant(rating: str | None, factor_context: dict | None) -> tuple[str, str] | None:
    """Verhältnis Agenten-Rating ↔ Quant-Empfehlung: ``(label, tonalität)``.

    „bestätigt“ (up) bei gleicher Richtung, „vorsichtiger“ (warn) wenn genau
    eine Seite Hold ist, „widerspricht“ (down) bei Gegenrichtung. ``None``
    ohne Quant-Kontext (Ad-hoc).
    """
    quant = _quant_direction((factor_context or {}).get("recommendation"))
    agents = _agent_direction(rating)
    if quant is None or agents is None:
        return None
    if quant == agents:
        return ("bestätigt", "up")
    if "hold" in (quant, agents):
        return ("vorsichtiger", "warn")
    return ("widerspricht", "down")


def fmt_de_number(value, decimals: int = 1) -> str:
    try:
        return f"{float(value):.{decimals}f}".replace(".", ",")
    except (TypeError, ValueError):
        return "–"


# ── Zustand 2 · Laufend: Pipeline-Karte ────────────────────────────────────

def _agent_row(name: str, status: str) -> html.Div:
    if status == "completed":
        return html.Div(
            [html.Span("✓", className="is-check"), html.Span(name, className="is-name")],
            className="ms-agent-row",
        )
    if status == "in_progress":
        return html.Div(
            [html.Span(className="ms-agent-dot"),
             html.Span(name, className="is-name-active")],
            className="ms-agent-row",
        )
    if status == "error":
        return html.Div(
            [html.Span("✗", className="is-fail"), html.Span(name, className="is-name")],
            className="ms-agent-row",
        )
    return html.Div([html.Span("○"), html.Span(name)],
                    className="ms-agent-row is-pending")


def progress_checklist(job: dict) -> html.Div:
    """4-Phasen-Pipeline eines laufenden Jobs (Design 2a, Zustand 2)."""
    stats = progress_stats(job)
    phases = assign_phases(stats["agents"])
    ticker = job.get("agents_ticker") or job.get("ticker") or ""
    started = job.get("started_at")
    started_label = fmt_local_epoch(started)

    pct = (stats["done"] / stats["total"] * 100) if stats["total"] else 0

    phase_cells = []
    for idx, title in enumerate(_PHASE_TITLES):
        classes = "ms-agent-phase"
        if idx == stats["phase"]:
            classes += " is-active"
        elif idx > stats["phase"]:
            classes += " is-future"
        phase_cells.append(
            html.Div(
                [
                    html.Div(title, className="ms-agent-phase-label"),
                    html.Div(
                        [
                            _agent_row(a, stats["states"].get(a, "pending"))
                            for a in phases[idx]
                        ]
                        or [html.Div([html.Span("○"), html.Span("—")],
                                     className="ms-agent-row is-pending")],
                        className="ms-agent-phase-rows",
                    ),
                ],
                className=classes,
            )
        )

    meta = f"Phase {stats['phase'] + 1} von 4"
    if started_label:
        meta += f" · gestartet {started_label}"

    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(className="ms-agent-dot"),
                            html.Span(
                                f"Tiefenanalyse läuft · {ticker}",
                                className="ms-agent-pipe-title-txt",
                            ),
                            html.Span(
                                f"{stats['active']} arbeitet …" if stats["active"] else
                                (job.get("stage") or "Analyse läuft …"),
                                className="ms-agent-pipe-current",
                            ),
                        ],
                        className="ms-agent-pipe-title",
                    ),
                    html.Div(meta, className="ms-agent-pipe-meta"),
                ],
                className="ms-agent-pipe-head",
            ),
            html.Div(
                html.Div(className="ms-agent-pipe-fill", style={"width": f"{pct:.0f}%"}),
                className="ms-agent-pipe-bar",
            ),
            html.Div(phase_cells, className="ms-agent-pipe-grid"),
            html.Div(
                "Die Tiefenanalyse dauert je nach Modell und Analysetiefe mehrere "
                "Minuten. Die Seite kann verlassen werden — das Ergebnis wird "
                "gespeichert.",
                className="ms-agent-pipe-foot",
            ),
        ],
        className="ms-card ms-agent-pipe",
    )


def compact_status_card(ticker: str, job: dict) -> html.Div:
    """Kompakte Status-Karte der Ad-hoc-Seite (Design 2b, rechte Spalte)."""
    stats = progress_stats(job)
    pct = (stats["done"] / stats["total"] * 100) if stats["total"] else 0
    phases = assign_phases(stats["agents"])
    p1_done = sum(
        1 for a in phases[0] if stats["states"].get(a) == "completed"
    )

    started = job.get("started_at")
    started_label = fmt_local_epoch(started)

    rows = [
        html.Div(
            [
                html.Span("✓", className="is-check"),
                html.Span(
                    f"Analysten — {p1_done} von {len(phases[0]) or 4} fertig",
                    className="is-name",
                ),
            ],
            className="ms-agent-row",
        )
    ]
    if stats["active"]:
        rows.append(
            html.Div(
                [html.Span(className="ms-agent-dot"),
                 html.Span(stats["active"], className="is-name-active")],
                className="ms-agent-row",
            )
        )
    rows.append(
        html.Div(
            [html.Span("○"), html.Span("Debatte · Risiko · Entscheidung")],
            className="ms-agent-row is-pending",
        )
    )

    foot = "Ergebnis wird gespeichert."
    if started_label:
        foot = f"Gestartet {started_label} · " + foot

    return html.Div(
        [
            html.Div(
                [
                    html.H3(f"Laufend · {ticker}", className="ms-card-h"),
                    html.Span(
                        f"Phase {stats['phase'] + 1}/4 · "
                        f"{stats['done']}/{stats['total'] or '–'}",
                        className="ms-agent-pipe-meta",
                    ),
                ],
                className="ms-agent-mini-head",
            ),
            html.Div(
                [
                    html.Div(
                        html.Div(className="ms-agent-mini-fill",
                                 style={"width": f"{pct:.0f}%"}),
                        className="ms-agent-mini-bar",
                    ),
                    html.Div(rows, className="ms-agent-mini-rows"),
                ],
                className="ms-agent-mini-body",
            ),
            html.Div(foot, className="ms-agent-mini-foot"),
        ],
        className="ms-card ms-agent-mini",
    )


# ── Zustand 3 · Ergebnis: Rating-Hero + Report-Karten ─────────────────────

def _verdict_chip(rating: str | None, factor_context: dict | None) -> html.Div | None:
    delta = delta_quant(rating, factor_context)
    if delta is None:
        return None
    label, tone = delta
    text = {
        "bestätigt": "Agenten bestätigen das Vorab-Rating",
        "vorsichtiger": "Agenten sind vorsichtiger",
        "widerspricht": "Agenten widersprechen dem Vorab-Rating",
    }[label]
    icon = {"up": "✓", "warn": "◆", "down": "✕"}[tone]
    return html.Div(
        [html.Span(icon, className="is-icon"), html.Span(text)],
        className=f"ms-agent-verdict-chip is-{tone}",
    )


def result_view(analysis: dict, current_score=None) -> html.Div:
    """Ergebnis-Ansicht: Rating-Hero, Quant-vs-Agenten-Panel, Report-Karten."""
    reports = analysis.get("reports") or {}
    rating = analysis.get("rating")
    tone = rating_tone(rating) or "warn"
    fc = analysis.get("factor_context") or {}

    summary_src = (
        reports.get("final_trade_decision") or analysis.get("executive_summary")
    )
    quote = first_sentences(summary_src, n=3, max_chars=420)

    # Rechtes Panel: Quant vs. Agenten
    total = fc.get("total_score", analysis.get("total_score"))
    classification = str(
        fc.get("classification") or analysis.get("classification") or ""
    ).strip()
    side_children: list = [
        html.Div("Quant vs. Agenten", className="ms-agent-aside-label"),
        html.Div(
            [
                html.Div(
                    [
                        html.Div("Quant-Score", className="ms-agent-side-cap"),
                        html.Div(
                            fmt_de_number(total) if total is not None else "–",
                            className="ms-agent-side-num",
                        ),
                        html.Div(
                            classification.upper() or "–",
                            className="ms-agent-side-class",
                        ),
                    ]
                ),
                html.Div(
                    [
                        html.Div("Agenten", className="ms-agent-side-cap"),
                        html.Div(
                            rating_short(rating),
                            className=f"ms-agent-side-num ms-agent-tone-{tone}",
                        ),
                        html.Div(rating or "–", className="ms-agent-side-plain"),
                    ]
                ),
            ],
            className="ms-agent-side-grid",
        ),
    ]
    if (chip := _verdict_chip(rating, fc)) is not None:
        side_children.append(chip)
    if current_score is not None and total is not None and not pd.isna(current_score):
        try:
            if abs(float(current_score) - float(total)) >= 0.05:
                side_children.append(
                    html.Div(
                        f"Score zum Analysezeitpunkt: {fmt_de_number(total)} · "
                        f"aktuell: {fmt_de_number(current_score)}",
                        className="ms-agent-side-foot",
                    )
                )
        except (TypeError, ValueError):
            pass

    hero = html.Div(
        [
            html.Div(
                [
                    html.Div(
                        "Agenten-Rating · TradingAgents",
                        className="ms-agent-hero-eyebrow",
                    ),
                    html.Div(
                        [
                            html.Div(
                                rating or "–",
                                className=f"ms-agent-hero-rating ms-agent-tone-{tone}",
                            ),
                            html.Div(
                                "12 Agenten · Bull/Bear-Debatte · Risiko-Runde",
                                className="ms-agent-hero-sub",
                            ),
                        ],
                        className="ms-agent-hero-row",
                    ),
                ]
                + ([html.P(f"„{quote}“", className="ms-agent-hero-quote")]
                   if quote else []),
                className="ms-agent-hero-main",
            ),
            html.Div(side_children, className="ms-agent-side"),
        ],
        className="ms-agent-hero",
    )

    # Report-Karten; „Vollständig lesen“ öffnet das zentrale Lese-Modal
    # (Design-Handoff #3a) mit genau dieser Sektion.
    ticker = str(analysis.get("ticker") or "")
    cards = []
    for key, title, role in REPORT_SECTIONS:
        content = reports.get(key)
        if not isinstance(content, str) or not content.strip():
            continue
        cards.append(
            html.Div(
                [
                    html.H3(
                        [title, html.Span(role, className="ms-card-h-meta")],
                        className="ms-card-h",
                    ),
                    html.P(
                        first_sentences(content), className="ms-agent-card-teaser"
                    ),
                    html.Span(
                        "Vollständig lesen ›",
                        id={"type": "agent-read", "ticker": ticker, "key": key},
                        n_clicks=0,
                        className="ms-agent-readlink",
                    ),
                ],
                className="ms-card",
                style={"gap": "10px"},
            )
        )

    children: list = [hero]
    if cards:
        children.append(html.Div(cards, className="ms-agent-cards"))
    return html.Div(children)


# ── Lese-Modal (Design-Handoff #3a) ────────────────────────────────────────

def modal_sections(reports: dict | None) -> list[tuple[str, str, str]]:
    """Sektionen mit Inhalt in ``REPORT_SECTIONS``-Reihenfolge."""
    reports = reports or {}
    return [
        (key, title, role)
        for key, title, role in REPORT_SECTIONS
        if isinstance(reports.get(key), str) and reports[key].strip()
    ]


def read_modal() -> html.Div:
    """Global eingehängtes Lese-Modal (einmal im App-Layout, alle Seiten)."""
    return html.Div(
        [
            dcc.Store(id="ms-agent-read-store"),
            dcc.Download(id="ms-agent-read-pdf-download"),
            dbc.Modal(
                [
                    html.Div(id="ms-agent-read-head"),
                    html.Div(id="ms-agent-read-body", className="ms-agent-modal-scroll"),
                    html.Div(id="ms-agent-read-foot"),
                    html.Div(
                        id="ms-agent-read-pdf-error",
                        className="ms-agent-pdf-error ms-agent-modal-pdf-error",
                    ),
                ],
                id="ms-agent-read-modal",
                is_open=False,
                size="lg",
                centered=True,
                keyboard=True,
                class_name="ms-agent-read",
                backdrop_class_name="ms-agent-read-backdrop",
            ),
        ]
    )


def _read_modal_content(analysis: dict, key: str):
    """Kopf/Body/Fuß des Lese-Modals für eine Sektion. ``(head, body, foot)``."""
    reports = analysis.get("reports") or {}
    sections = modal_sections(reports)
    keys = [k for k, _, _ in sections]
    if key not in keys:
        key = keys[0] if keys else key
    idx = keys.index(key) if key in keys else 0
    _, title, role = sections[idx] if sections else (key, key, "")
    ticker = str(analysis.get("ticker") or "")

    head = html.Div(
        [
            html.Div(
                [
                    html.Div(
                        f"{ticker} · Bericht {idx + 1} von {len(sections)} · {role}",
                        className="ms-agent-modal-eyebrow",
                    ),
                    html.Div(title, className="ms-agent-modal-title"),
                ]
            ),
            html.Div(
                [
                    html.Button(
                        "Als PDF",
                        id="ms-agent-read-pdf",
                        n_clicks=0,
                        className="ms-btn-goldline ms-btn-goldline--compact",
                        title="Diesen Bericht als PDF exportieren",
                    ),
                    html.Button(
                        "×",
                        id="ms-agent-read-close",
                        n_clicks=0,
                        className="ms-agent-modal-close",
                        title="Schließen (Esc)",
                    ),
                ],
                className="ms-agent-modal-actions",
            ),
        ],
        className="ms-agent-modal-head",
    )

    body = html.Div(
        dcc.Markdown(
            reports.get(key) or "",
            className="ms-agent-modal-body",
        ),
        className="ms-agent-modal-bodywrap",
        # key-Wechsel erzwingt einen frischen DOM-Knoten — der Scroll
        # springt beim Blättern nach oben.
        key=f"{ticker}-{key}",
    )

    prev_label = sections[idx - 1][1] if idx > 0 else ""
    next_label = sections[idx + 1][1] if idx + 1 < len(sections) else ""
    dots = [
        html.Span(
            className="ms-agent-read-dot" + (" is-active" if i == idx else "")
        )
        for i in range(len(sections))
    ]
    foot = html.Div(
        [
            html.Button(
                f"‹ {prev_label}",
                id="ms-agent-read-prev",
                n_clicks=0,
                className="ms-agent-modal-nav",
                style={} if prev_label else {"visibility": "hidden"},
            ),
            html.Div(dots, className="ms-agent-read-dots"),
            html.Button(
                f"{next_label} ›",
                id="ms-agent-read-next",
                n_clicks=0,
                className="ms-agent-modal-nav",
                style={} if next_label else {"visibility": "hidden"},
            ),
        ],
        className="ms-agent-modal-foot",
    )
    return head, body, foot


def _shifted_key(reports: dict | None, key: str, step: int) -> str:
    keys = [k for k, _, _ in modal_sections(reports)]
    if not keys:
        return key
    if key not in keys:
        return keys[0]
    return keys[max(0, min(len(keys) - 1, keys.index(key) + step))]


@callback(
    Output("ms-agent-read-modal", "is_open"),
    Output("ms-agent-read-store", "data"),
    Output("ms-agent-read-head", "children"),
    Output("ms-agent-read-body", "children"),
    Output("ms-agent-read-foot", "children"),
    Input({"type": "agent-read", "ticker": ALL, "key": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _open_read_modal(n_clicks_list):
    trigger = ctx.triggered_id
    if not trigger or not any(n_clicks_list or []):
        raise PreventUpdate
    ticker = trigger.get("ticker")
    key = trigger.get("key")
    analysis = persistence.load_agent_analysis(ticker) if ticker else None
    if not analysis:
        raise PreventUpdate
    head, body, foot = _read_modal_content(analysis, key)
    return True, {"ticker": ticker, "key": key}, head, body, foot


def _flip_step(trigger, prev_clicks, next_clicks) -> int | None:
    """Blätter-Richtung eines Nav-Triggers — ``None`` ohne echten Klick.

    Dash feuert Callbacks auch, wenn ihre Input-Komponenten neu ins DOM
    eingefügt werden (``prevent_initial_call`` greift nur beim Seitenladen).
    Beim Öffnen des Modals werden die Nav-Buttons frisch gerendert — ohne
    diesen Klick-Guard blätterte das sofort eine Sektion zurück und jeder
    „Vollständig lesen“-Klick landete beim VORHERIGEN Bericht.
    """
    if trigger == "ms-agent-read-prev":
        return -1 if prev_clicks else None
    if trigger == "ms-agent-read-next":
        return 1 if next_clicks else None
    return None


@callback(
    Output("ms-agent-read-store", "data", allow_duplicate=True),
    Output("ms-agent-read-head", "children", allow_duplicate=True),
    Output("ms-agent-read-body", "children", allow_duplicate=True),
    Output("ms-agent-read-foot", "children", allow_duplicate=True),
    Input("ms-agent-read-prev", "n_clicks"),
    Input("ms-agent-read-next", "n_clicks"),
    State("ms-agent-read-store", "data"),
    prevent_initial_call=True,
)
def _flip_read_modal(_prev, _next, data):
    step = _flip_step(ctx.triggered_id, _prev, _next)
    if step is None:
        raise PreventUpdate
    if not data or not data.get("ticker"):
        raise PreventUpdate
    analysis = persistence.load_agent_analysis(data["ticker"])
    if not analysis:
        raise PreventUpdate
    new_key = _shifted_key(analysis.get("reports"), data.get("key"), step)
    head, body, foot = _read_modal_content(analysis, new_key)
    return {"ticker": data["ticker"], "key": new_key}, head, body, foot


@callback(
    Output("ms-agent-read-modal", "is_open", allow_duplicate=True),
    Input("ms-agent-read-close", "n_clicks"),
    prevent_initial_call=True,
)
def _close_read_modal(n_clicks):
    if not n_clicks:
        raise PreventUpdate
    return False


@callback(
    Output("ms-agent-read-pdf-download", "data"),
    Output("ms-agent-read-pdf-error", "children"),
    Input("ms-agent-read-pdf", "n_clicks"),
    State("ms-agent-read-store", "data"),
    running=[
        (Output("ms-agent-read-pdf", "disabled"), True, False),
        (Output("ms-agent-read-pdf", "children"), "Wird erstellt …", "Als PDF"),
    ],
    prevent_initial_call=True,
)
def _export_read_modal_pdf(n_clicks, data):
    # Klick-Guard wie bei den Nav-Buttons: der Modal-Kopf wird bei jedem
    # Öffnen/Blättern neu gemountet, der Phantom-Fire kommt mit n_clicks=0.
    if not n_clicks:
        raise PreventUpdate
    if not data or not data.get("ticker"):
        raise PreventUpdate
    analysis = persistence.load_agent_analysis(data["ticker"])
    if not analysis:
        return no_update, "Keine gespeicherte Analyse gefunden."
    try:
        from app.core.pdf_export import FactsheetRenderError, render_section_pdf
        from app.core.state import STATE

        pdf_bytes, filename = render_section_pdf(
            analysis, data.get("key"), STATE.scored
        )
    except ImportError as exc:
        return no_update, f"PDF-Export nicht verfügbar: {exc}"
    except (FactsheetRenderError, ValueError) as exc:
        return no_update, str(exc)
    return (
        dcc.send_bytes(lambda buf: buf.write(pdf_bytes), filename=filename),
        "",
    )
