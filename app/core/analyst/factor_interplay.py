"""Deterministische Regeltabelle für das Zusammenspiel der Inputfaktoren.

Die Regeln laufen **vor** dem LLM-Aufruf auf der gescorten Zeile eines
Titels und liefern Hinweise samt Prüffragen, die der Synthese-Prompt und
der Transkript-Pass stellen (z. B. „Value hoch + Momentum tief: nennt das
Management strukturelle Gründe für den Kursdruck?“). Die Tabelle ist Daten:
eine neue Regel ist ein Tupel-Eintrag plus ein Testfall.

Alle Bedingungen arbeiten auf einem flachen Kennzahlen-Dict
(:func:`metrics_from_row`); ``None`` bedeutet „Bedingung nicht erfüllt“.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

Z_HIGH = 0.75
Z_LOW = -0.75
QUALITY_DROP = 0.5

_LEVERAGE_Z = ("z_net_debt_ebitda", "z_debt_ebit", "z_debt_equity")
_METRIC_KEYS = (
    "z_value", "z_quality", "z_momentum", "z_investment",
    "z_eps_revisions_3m", "eps_revisions_3m", "z_accruals", "accruals",
    "z_asset_growth", "asset_growth", "z_share_issuance", "share_issuance",
    "z_mom_12_1_adj", "mom_12_1", "ret_3m", "dist_52w_high",
)


@dataclass(frozen=True)
class InterplayRule:
    code: str
    label: str
    factors: tuple[str, ...]
    quant_condition: Callable[[dict], bool]
    hint: str
    text_question: str | None


@dataclass
class InterplayHint:
    code: str
    label: str
    factors: tuple[str, ...]
    hint: str
    text_question: str | None
    values: dict = field(default_factory=dict)


def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _txt(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def metrics_from_row(row, cursor: dict | None = None) -> dict:
    """Flaches Kennzahlen-Dict (NaN → None) inkl. Leverage-Z (erste
    vorhandene Spalte) und Quality-Z-Veränderung gegenüber dem Cursor."""
    get = row.get if hasattr(row, "get") else (lambda k, d=None: d)
    m: dict = {k: _num(get(k)) for k in _METRIC_KEYS}
    m["leverage_z"] = next((v for v in (_num(get(c)) for c in _LEVERAGE_Z) if v is not None), None)
    m["sma_signal"] = _txt(get("sma_signal"))
    m["trend_phase"] = _txt(get("trend_phase"))
    last_q = _num((cursor or {}).get("last_z_quality"))
    m["z_quality_change"] = (
        m["z_quality"] - last_q if m["z_quality"] is not None and last_q is not None else None
    )
    return m


def _ge(v, t) -> bool:
    return v is not None and v >= t


def _le(v, t) -> bool:
    return v is not None and v <= t


INTERPLAY_RULES: tuple[InterplayRule, ...] = (
    InterplayRule(
        "value_trap",
        "Value-Falle?",
        ("value", "momentum"),
        lambda m: _ge(m["z_value"], Z_HIGH) and _le(m["z_momentum"], Z_LOW),
        "Hohe Value-Attraktivität bei schwachem Momentum — der Markt preist "
        "möglicherweise ein strukturelles Problem ein.",
        "Nennt das Management strukturelle Gründe für den Kursdruck "
        "(Nachfrage, Margen, Wettbewerb) oder nur temporäre Faktoren?",
    ),
    InterplayRule(
        "momentum_without_fundamentals",
        "Momentum ohne Fundament?",
        ("momentum", "quality"),
        lambda m: _ge(m["z_momentum"], Z_HIGH)
        and (_le(m["z_quality"], Z_LOW) or _le(m["z_quality_change"], -QUALITY_DROP)),
        "Starkes Momentum bei schwacher oder sinkender Qualität — Kursanstieg "
        "könnte narrativ statt fundamental getrieben sein.",
        "Stützen Ergebnisqualität, Margen und Cash-Flow den Kursanstieg, oder "
        "argumentiert das Management vor allem mit Zukunftsnarrativen?",
    ),
    InterplayRule(
        "capex_expansion_on_weak_investment",
        "Capex-Ausweitung bei schwachem Investment-Faktor",
        ("investment",),
        lambda m: _le(m["z_investment"], Z_LOW) and _le(m["z_asset_growth"], Z_LOW),
        "Hohes Asset-Wachstum (Investment-Faktor negativ) — weitere "
        "Expansion würde das Muster verstärken.",
        "Kündigt das Management eine Capex-/Investitionsausweitung an? "
        "Mit welchen Renditeerwartungen?",
    ),
    InterplayRule(
        "revisions_vs_guidance_divergence",
        "Revisionen vs. Guidance",
        ("momentum",),
        lambda m: _le(m["z_eps_revisions_3m"], Z_LOW),
        "Negative EPS-Revisionen — eine angehobene oder bestätigte Guidance "
        "stünde im Widerspruch zu den Analystenschätzungen.",
        "Wurde die Guidance angehoben oder bestätigt? Falls ja: Wie begründet "
        "das Management die Divergenz zu den negativen Revisionen?",
    ),
    InterplayRule(
        "accruals_vs_cashflow_commentary",
        "Hohe Accruals vs. Cash-Flow-Kommentar",
        ("quality",),
        lambda m: _le(m["z_accruals"], Z_LOW),
        "Hohe Accruals (Gewinn deutlich über operativem Cash-Flow) — "
        "Ergebnisqualität fraglich.",
        "Ist der Cash-Flow-Kommentar konkret (Zahlen, Working Capital, "
        "Timing) oder vage?",
    ),
    InterplayRule(
        "dilution_with_buyback_talk",
        "Verwässerung vs. Rückkauf-Rhetorik",
        ("investment",),
        lambda m: _le(m["z_share_issuance"], Z_LOW),
        "Steigende Aktienzahl (Verwässerung) — Aussagen zu Rückkäufen oder "
        "Kapitaldisziplin wären widersprüchlich.",
        "Spricht das Management von Rückkäufen oder Kapitaldisziplin trotz "
        "gestiegener Aktienzahl?",
    ),
    InterplayRule(
        "leverage_vs_guidance_confidence",
        "Verschuldung vs. Guidance-Sicherheit",
        ("quality",),
        lambda m: _le(m["leverage_z"], Z_LOW),
        "Hohe Verschuldung relativ zur Peergroup — Refinanzierung und "
        "Zinslast können die Guidance gefährden.",
        "Wie sicher klingt die Guidance angesichts der Verschuldung "
        "(Refinanzierung, Covenants, Zinskosten)?",
    ),
    InterplayRule(
        "quality_value_mismatch",
        "Qualitätsprämie",
        ("quality", "value"),
        lambda m: _ge(m["z_quality"], Z_HIGH) and _le(m["z_value"], Z_LOW),
        "Hohe Qualität bei teurer Bewertung — Prämie muss durch Wachstum "
        "oder Moat gerechtfertigt sein.",
        "Rechtfertigen Wachstum, Preissetzungsmacht oder Moat laut Call die "
        "Bewertungsprämie?",
    ),
    InterplayRule(
        "trend_break_despite_revisions",
        "Trendbruch trotz positiver Revisionen",
        ("momentum",),
        lambda m: _ge(m["z_eps_revisions_3m"], Z_HIGH) and "DEATH" in m["sma_signal"].upper(),
        "Positive EPS-Revisionen, aber Death Cross — Kurs und Schätzungen "
        "laufen auseinander.",
        "Gibt es im Material eine Erklärung für den Trendbruch (Sektorrotation, "
        "Einmaleffekt, Bewertungsrückgang)?",
    ),
)

RULES_BY_CODE = {r.code: r for r in INTERPLAY_RULES}

_VALUE_KEYS_BY_RULE = {
    "value_trap": ("z_value", "z_momentum"),
    "momentum_without_fundamentals": ("z_momentum", "z_quality", "z_quality_change"),
    "capex_expansion_on_weak_investment": ("z_investment", "z_asset_growth", "asset_growth"),
    "revisions_vs_guidance_divergence": ("z_eps_revisions_3m", "eps_revisions_3m"),
    "accruals_vs_cashflow_commentary": ("z_accruals", "accruals"),
    "dilution_with_buyback_talk": ("z_share_issuance", "share_issuance"),
    "leverage_vs_guidance_confidence": ("leverage_z",),
    "quality_value_mismatch": ("z_quality", "z_value"),
    "trend_break_despite_revisions": ("z_eps_revisions_3m", "sma_signal"),
}


def detect_interplay(row, cursor: dict | None = None) -> list[InterplayHint]:
    """Alle zutreffenden Regeln für eine gescorte Zeile."""
    m = metrics_from_row(row, cursor)
    hints: list[InterplayHint] = []
    for rule in INTERPLAY_RULES:
        try:
            hit = bool(rule.quant_condition(m))
        except (TypeError, KeyError):
            hit = False
        if not hit:
            continue
        values = {
            k: (round(m[k], 3) if isinstance(m.get(k), float) else m.get(k))
            for k in _VALUE_KEYS_BY_RULE.get(rule.code, ())
            if m.get(k) not in (None, "")
        }
        hints.append(
            InterplayHint(rule.code, rule.label, rule.factors, rule.hint, rule.text_question, values)
        )
    return hints


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.2f}".replace(".", ",")
    return str(value)


def hints_to_prompt(hints: list[InterplayHint]) -> str:
    """Nummerierte deutsche Liste für den Prompt (leer → Hinweistext)."""
    if not hints:
        return "Keine regelbasierten Auffälligkeiten im Faktor-Zusammenspiel."
    lines = []
    for i, h in enumerate(hints, 1):
        vals = ", ".join(f"{k}={_fmt(v)}" for k, v in h.values.items())
        line = f"{i}. [{h.code}] {h.label} — {h.hint}"
        if vals:
            line += f" (Werte: {vals})"
        if h.text_question:
            line += f"\n   Prüffrage: {h.text_question}"
        lines.append(line)
    return "\n".join(lines)
