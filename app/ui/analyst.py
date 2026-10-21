"""UI-Helfer des KI-Analysten: Labels, Badges, Chips (Dash)."""

from __future__ import annotations

from dash import dcc, html

from app.core.analyst.events import KIND_LABELS
from app.ui.theme import ms_badge

PAGE_PATH = "/ki-analyst"

SUGGESTION_LABELS = {
    "EINSTIEG_PRÜFEN": "Einstieg prüfen",
    "HALTEN": "Halten",
    "ÜBERPRÜFEN": "Überprüfen",
    "REDUZIEREN_VERKAUF_PRÜFEN": "Reduzieren/Verkauf prüfen",
    "INFO": "Info",
}
SUGGESTION_TONES = {
    "EINSTIEG_PRÜFEN": "up",
    "HALTEN": "warn",
    "ÜBERPRÜFEN": "warn",
    "REDUZIEREN_VERKAUF_PRÜFEN": "down",
    "INFO": "info",
}
# Farbklassen der Dashboard-Tabelle (``ms-tt-rec is-*``).
SUGGESTION_REC_CLASS = {
    "EINSTIEG_PRÜFEN": "buy",
    "HALTEN": "hold",
    "ÜBERPRÜFEN": "hold",
    "REDUZIEREN_VERKAUF_PRÜFEN": "sell",
    "INFO": "fail",
}

PRICING_LABELS = {
    "früh": "früh",
    "teilweise_eingepreist": "teilweise",
    "bereits_eingepreist": "bereits eingepreist",
}
PRICING_TONES = {"früh": "up", "teilweise_eingepreist": "warn", "bereits_eingepreist": None}

ROLE_LABELS = {
    "direkt": "direkt",
    "zulieferer": "Zulieferer",
    "enabler": "Enabler",
    "picks_and_shovels": "Picks & Shovels",
    "zweite_ordnung": "2. Ordnung",
}

THEME_KIND_LABELS = {"sektor": "Sektor", "region": "Region", "makro": "Makro", "firmenspezifisch": "Firma"}
DIRECTION_TONES = {"positiv": "up", "negativ": "down", "gemischt": "warn"}



def suggestion_label(kind: str | None) -> str:
    return SUGGESTION_LABELS.get(str(kind or ""), str(kind or "–"))


def kind_label(kind: str | None) -> str:
    return KIND_LABELS.get(str(kind or ""), str(kind or "–"))


def suggestion_badge(record: dict | None, link: bool = True) -> html.Span | dcc.Link | None:
    """Vorschlags-Pill ``KI-ANALYST · Einstieg prüfen`` (verlinkt)."""
    if not record:
        return None
    typ = str(record.get("suggestion_type") or "")
    badge = ms_badge("KI-ANALYST", suggestion_label(typ), SUGGESTION_TONES.get(typ))
    rationale = record.get("rationale")
    if rationale:
        badge.title = str(rationale)
    if not link:
        return badge
    return dcc.Link(badge, href=PAGE_PATH, className="ms-ka-badge-link")


def pricing_chip(value: str | None) -> html.Span:
    label = PRICING_LABELS.get(str(value or ""), str(value or "–"))
    tone = PRICING_TONES.get(str(value or ""))
    cls = "ms-badge" + (f" is-{tone}" if tone else "")
    return html.Span(label, className=cls)


def role_badge(role: str | None) -> html.Span:
    return html.Span(ROLE_LABELS.get(str(role or ""), str(role or "–")), className="ms-badge is-info")


def trend_badge(link: dict | None) -> dcc.Link | None:
    """Trend-Profiteur-Pill ``TREND · <Titel> · Rolle`` mit Begründungs-Tooltip."""
    if not link:
        return None
    title = str(link.get("trend_title") or "Trend")
    role = ROLE_LABELS.get(str(link.get("role") or ""), str(link.get("role") or ""))
    tone = "up" if link.get("pricing_quant") == "früh" else "warn"
    badge = ms_badge("TREND", f"{title[:28]}{'…' if len(title) > 28 else ''} · {role}", tone)
    reasoning = link.get("reasoning") or []
    if reasoning:
        badge.title = " → ".join(str(r) for r in reasoning)
    return dcc.Link(badge, href=PAGE_PATH, className="ms-ka-badge-link")


def theme_kind_badge(kind: str | None, direction: str | None = None) -> html.Span:
    return ms_badge(
        THEME_KIND_LABELS.get(str(kind or ""), str(kind or "–")),
        str(direction or "–"),
        DIRECTION_TONES.get(str(direction or "")),
    )


# ── Darstellungs-Helfer (Seite KI-Analyst und Einzelanalyse) ───────────
def ticker_link(uid: str | None, ticker: str | None) -> html.A | str:
    if not uid:
        return str(ticker or "–")
    return dcc.Link(str(ticker or uid), href=f"/einzelanalyse?ticker={uid}", className="ms-tt-tk")


def fmt_num(value, digits: int = 1) -> str:
    if value is None:
        return "–"
    try:
        return f"{float(value):.{digits}f}".replace(".", ",")
    except (TypeError, ValueError):
        return str(value)


def _kv_table(headers: list[str], rows: list[list]) -> html.Div:
    if not rows:
        return html.Div("–", className="ms-tt-muted small")
    return html.Div(
        html.Table([html.Thead(html.Tr([html.Th(h) for h in headers])), html.Tbody([html.Tr([html.Td(c) for c in r]) for r in rows])], className="ms-toptable"),
        className="ms-toptable-wrap mb-2",
    )


def synthesis_blocks(content: dict | None, links: list[dict] | None = None) -> list:
    """Blöcke einer Synthese (auch von der Einzelanalyse genutzt)."""
    if not content:
        return [html.Div("Synthese nicht verfügbar.", className="ms-tt-muted small")]
    out = [html.Strong("Faktor-Zusammenspiel"),
           _kv_table(["Muster", "Faktoren", "Richtung", "Evidenz", "Interpretation"],
                     [[x.get("muster"), ", ".join(x.get("faktoren") or []), x.get("richtung"), x.get("evidenz_urteil"), x.get("interpretation")] for x in content.get("faktor_zusammenspiel") or [] if isinstance(x, dict)]),
           html.Strong("Quellenübergreifende Zusammenhänge"),
           _kv_table(["Quellen", "Befund", "Richtung", "Bedeutung"],
                     [[" ↔ ".join(z.get("quellen") or []), z.get("befund"), z.get("richtung"), str(z.get("bedeutung") or "–")] for z in content.get("quellen_zusammenhaenge") or [] if isinstance(z, dict)]),
           html.Strong("Nicht eingepreiste Themen"),
           _kv_table(["Thema", "Art", "Richtung", "Horizont", "Konfidenz", "Warum nicht eingepreist", "Bestätigung"],
                     [[t.get("thema"), t.get("art"), t.get("richtung"), t.get("zeithorizont"), fmt_num(t.get("konfidenz"), 2), t.get("warum_nicht_eingepreist"), t.get("bestaetigung_durch")] for t in content.get("nicht_eingepreiste_themen") or [] if isinstance(t, dict)])]
    if links:
        out += [html.Strong("Verbundene Unternehmen"),
                _kv_table(["Unternehmen", "Beziehung", "Kontext", "Quelle"],
                          [[ticker_link(l.get("related_uid"), l.get("related_ticker") or l.get("related_name")) if l.get("direction", "out") == "out" else ticker_link(l.get("uid"), l.get("ticker") or l.get("uid")), l.get("relation"), l.get("context"), l.get("source")] for l in links])]
    return out


