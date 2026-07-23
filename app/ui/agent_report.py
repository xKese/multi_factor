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

import re

import pandas as pd
from dash import dcc, html

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

def phase_of(agent_name: str) -> int:
    """Ordnet einen Agentennamen einer der vier Pipeline-Phasen (0–3) zu.

    Unbekannte Namen landen in Phase 0 (Analysten), Reihenfolge des Service
    bleibt erhalten. Risky/Safe/Neutral vor dem generischen "Analyst"-Match
    prüfen — sie heißen beim Service ebenfalls "… Analyst".
    """
    n = (agent_name or "").lower()
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
    agents = list(job.get("agents") or [])
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
    started_label = ""
    if started:
        try:
            started_label = pd.to_datetime(started, unit="s").strftime("%H:%M")
        except (ValueError, TypeError, OverflowError):
            started_label = ""

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
    try:
        started_label = pd.to_datetime(started, unit="s").strftime("%H:%M")
    except (ValueError, TypeError, OverflowError):
        started_label = ""

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

    # Report-Karten mit Inline-Collapse für den vollen Markdown-Report.
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
                    html.Details(
                        [
                            html.Summary(
                                html.Span(
                                    "Vollständig lesen ›",
                                    className="ms-agent-readlink",
                                )
                            ),
                            dcc.Markdown(content, className="ms-agent-report mt-2"),
                        ],
                        className="ms-agent-full",
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
