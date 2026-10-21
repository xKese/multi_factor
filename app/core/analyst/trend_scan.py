"""Trend-Scan des KI-Analysten (deterministische Teile).

Ziel: frühzeitig Profiteure aktueller Trends im Universum erkennen. Der
LLM-Teil (:func:`app.core.analyst.tasks.scan_trends`) bekommt drei
kompakte Eingaben, die hier ohne LLM gebaut werden:

1. **Markt-News** je konfiguriertem Alpha-Vantage-Topic (ein Call je
   Topic, Dedupe per URL, nummerierte Zeilen für Referenzen ``[n]``).
2. **Industriegruppen-Profil** des Universums (eine Zeile je GICS-
   Industriegruppe mit Mittelwerten der Faktor-Z) — passt immer in den
   Prompt, auch bei tausenden Titeln.
3. **Kandidatentabelle**: nur Filter-bestandene Titel, Top-N je Gruppe
   plus Bestands-/Themen-Titel, globaler Deckel.

Nach dem LLM-Aufruf taggt :func:`tag_pricing` jeden Profiteur anhand der
Quant-Daten als „früh / teilweise / bereits eingepreist“ (unabhängig vom
LLM-Urteil), :func:`validate_beneficiaries` verwirft uids außerhalb der
Kandidatentabelle und :func:`suggested_action` leitet die Aktion
deterministisch ab.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from ..av_client import AlphaVantageError
from ..diagnostics import SEV_INFO, SEV_WARNING, Diagnostic

PRICING_EARLY = "früh"
PRICING_PARTIAL = "teilweise_eingepreist"
PRICING_PRICED = "bereits_eingepreist"
PRICING_VALUES = (PRICING_EARLY, PRICING_PARTIAL, PRICING_PRICED)
_PRICING_FROM_LLM = {
    "frueh": PRICING_EARLY, "fruh": PRICING_EARLY,
    "teilweise": PRICING_PARTIAL, "teilweise_eingepreist": PRICING_PARTIAL,
    "bereits": PRICING_PRICED, "bereits_eingepreist": PRICING_PRICED,
}

ACTION_CANDIDATE = "Kandidat prüfen"
ACTION_WATCH = "Watchlist"
ACTION_POSITION = "Position prüfen"
ACTION_INFO = "Nur Information"
ACTIONS = (ACTION_CANDIDATE, ACTION_WATCH, ACTION_POSITION, ACTION_INFO)

ROLES = ("direkt", "zulieferer", "enabler", "picks_and_shovels", "zweite_ordnung")

# Industriegruppen, die bei fast jedem Trend als Zulieferer/Enabler in
# Frage kommen und deshalb in Pass B immer mitgegeben werden.
SECOND_ORDER_GROUPS: tuple[str, ...] = (
    "Semiconductors & Semiconductor Equipment",
    "Capital Goods",
    "Materials",
    "Software & Services",
    "Technology Hardware & Equipment",
    "Utilities",
    "Energy",
    "Transportation",
)

CANDIDATE_FIELDS = (
    "uid", "ticker", "name", "industry_group", "industry", "region",
    "composite_score", "zone_v2", "z_value", "z_quality", "z_momentum",
    "z_investment", "mom_12_1", "eps_revisions_3m", "z_eps_revisions_3m",
)


# --------------------------------------------------------------------------- #
# Markt-News
# --------------------------------------------------------------------------- #
def collect_market_news(
    settings, time_from: datetime | None, av, av_calls: list | None = None
) -> tuple[list[dict], list[Diagnostic]]:
    """Ein ``NEWS_SENTIMENT``-Call je Topic; Dedupe per URL. ``av_calls``
    (Liste) zählt die Calls mit. Rate-Limit bricht die Sammlung ab, der
    Rest läuft mit den bis dahin gesammelten Items weiter."""
    topics = [str(t).strip() for t in (getattr(settings, "analyst_trend_topics", []) or []) if str(t).strip()]
    rpm = int(getattr(settings, "risk_av_requests_per_minute", 70))
    limit = int(getattr(settings, "analyst_trend_news_limit", 50))
    items: list[dict] = []
    seen: set[str] = set()
    diags: list[Diagnostic] = []
    for topic in topics:
        try:
            feed = av.fetch_news_sentiment(
                topics=[topic], time_from=time_from, limit=limit, requests_per_minute=rpm
            )
        except AlphaVantageError as exc:
            diags.append(
                Diagnostic(
                    SEV_WARNING,
                    "trend_news_failed",
                    f"Topic-News „{topic}“ nicht abrufbar ({exc.kind}): {exc}",
                )
            )
            if av_calls is not None:
                av_calls.append(topic)
            if exc.kind == "rate_limit":
                break
            continue
        if av_calls is not None:
            av_calls.append(topic)
        for it in feed:
            key = it.get("url") or it.get("title")
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "title": it.get("title"),
                    "url": it.get("url"),
                    "source": it.get("source"),
                    "time_published": it.get("time_published"),
                    "summary": (it.get("summary") or "")[:300],
                    "sentiment_label": it.get("overall_sentiment_label"),
                    "sentiment_score": it.get("overall_sentiment_score"),
                    "topics": [t.get("topic") for t in it.get("topics") or [] if t.get("topic")],
                    "tickers": [
                        t.get("ticker")
                        for t in it.get("ticker_sentiment") or []
                        if (t.get("relevance_score") or 0) >= 0.5 and t.get("ticker")
                    ],
                    "query_topic": topic,
                }
            )
    items.sort(key=lambda i: i.get("time_published") or datetime.min, reverse=True)
    return items, diags


def compact_news_for_prompt(items: list[dict], max_items: int = 80, with_summary: int = 40) -> str:
    """Nummerierte Zeilen ``[n] Datum · Quelle · Titel · Sentiment · Topics``."""
    lines = []
    for n, it in enumerate(items[:max_items], 1):
        ts = it.get("time_published")
        day = ts.strftime("%Y-%m-%d") if isinstance(ts, datetime) else "–"
        topics = ", ".join(it.get("topics") or []) or "–"
        tickers = ", ".join(it.get("tickers") or [])
        line = f"[{n}] {day} · {it.get('source') or '–'} · {it.get('title')} · {it.get('sentiment_label') or '–'} · {topics}"
        if tickers:
            line += f" · Ticker: {tickers}"
        if n <= with_summary and it.get("summary"):
            line += f"\n    {it['summary']}"
        lines.append(line)
    return "\n".join(lines) if lines else "(keine Markt-News im Fenster)"


# --------------------------------------------------------------------------- #
# Kandidaten-Kompaktierung
# --------------------------------------------------------------------------- #
def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _group_col(scored: pd.DataFrame) -> pd.Series:
    """Industriegruppe mit Fallback ``industry`` → ``sector``."""
    out = pd.Series([None] * len(scored), index=scored.index, dtype="object")
    for col in ("industry_group", "industry", "sector"):
        if col in scored.columns:
            vals = scored[col].where(scored[col].notna(), None)
            out = out.where(out.notna() & (out.astype(str) != ""), vals)
    return out.fillna("Unbekannt").astype(str)


def industry_group_profile(scored: pd.DataFrame) -> list[dict]:
    """Eine Zeile je Industriegruppe mit Mittelwerten (Stufe 1)."""
    if scored is None or scored.empty:
        return []
    df = scored.copy()
    df["_group"] = _group_col(df)
    rows = []
    for group, g in df.groupby("_group", sort=True):
        row: dict = {"industry_group": group, "n": int(len(g))}
        if "filter_pass" in g.columns:
            row["n_filter_pass"] = int(g["filter_pass"].fillna(False).astype(bool).sum())
        for col, name in (
            ("composite_score", "avg_composite"), ("z_momentum", "avg_z_momentum"),
            ("z_eps_revisions_3m", "avg_z_eps_rev"), ("z_value", "avg_z_value"),
        ):
            if col in g.columns:
                v = pd.to_numeric(g[col], errors="coerce").mean()
                row[name] = None if pd.isna(v) else round(float(v), 2)
        if "zone_v2" in g.columns:
            row["n_kandidat"] = int((g["zone_v2"] == "KANDIDAT").sum())
        if "region" in g.columns:
            top = g["region"].dropna().astype(str).value_counts().head(3)
            row["regions"] = ", ".join(f"{r} {c}" for r, c in top.items())
        rows.append(row)
    return rows


def render_group_profile(rows: list[dict]) -> str:
    if not rows:
        return "(kein Universum)"
    head = "Industriegruppe | n | Filter ok | Ø Composite | Ø z_mom | Ø z_eps_rev | Ø z_value | Kandidaten | Regionen"
    lines = [head]
    for r in rows:
        lines.append(
            f"{r['industry_group']} | {r['n']} | {r.get('n_filter_pass', '–')} | "
            f"{_f(r.get('avg_composite'), 1)} | {_f(r.get('avg_z_momentum'))} | "
            f"{_f(r.get('avg_z_eps_rev'))} | {_f(r.get('avg_z_value'))} | "
            f"{r.get('n_kandidat', '–')} | {r.get('regions', '–')}"
        )
    return "\n".join(lines)


def _f(value, digits: int = 2) -> str:
    if value is None:
        return "–"
    return f"{float(value):.{digits}f}"


def candidate_table(
    scored: pd.DataFrame,
    settings,
    group_whitelist: list[str] | None = None,
    forced_uids: set[str] | None = None,
    cap: int | None = None,
) -> list[dict]:
    """Kompakte Kandidatenliste (Stufe 2): nur ``filter_pass``, Top-N je
    Gruppe nach Composite, plus ``forced_uids`` (Watchlist/Themen);
    Deckel ``cap`` (Default ``analyst_trend_max_candidates``) — die größten
    Gruppen geben zuerst ihre schwächsten Titel ab."""
    if scored is None or scored.empty:
        return []
    top_n = max(1, int(getattr(settings, "analyst_trend_top_per_group", 4)))
    if cap is None:
        cap = int(getattr(settings, "analyst_trend_max_candidates", 150))
    cap = max(1, int(cap))
    forced = {str(u) for u in (forced_uids or set())}

    df = scored.copy()
    df["_group"] = _group_col(df)
    if "filter_pass" in df.columns:
        passed = df["filter_pass"].fillna(False).astype(bool)
        df = df[passed | df["uid"].astype(str).isin(forced)]
    if group_whitelist:
        wl = {str(g).strip().lower() for g in group_whitelist}
        df = df[df["_group"].str.lower().isin(wl) | df["uid"].astype(str).isin(forced)]
    df["_score"] = pd.to_numeric(df.get("composite_score"), errors="coerce").fillna(-1e9)
    df = df.sort_values(["_group", "_score"], ascending=[True, False])

    chosen: list[dict] = []
    for group, g in df.groupby("_group", sort=True):
        keep = g.head(top_n)
        extra = g[g["uid"].astype(str).isin(forced) & ~g.index.isin(keep.index)]
        for _, r in pd.concat([keep, extra]).iterrows():
            chosen.append(_candidate_row(r, group))

    # Deckel: aus den größten Gruppen die schwächsten nicht-erzwungenen Titel streichen.
    while len(chosen) > cap:
        groups = pd.Series([c["industry_group"] for c in chosen]).value_counts()
        removed = False
        for group in groups.index:
            members = [i for i, c in enumerate(chosen) if c["industry_group"] == group and c["uid"] not in forced]
            if not members:
                continue
            weakest = min(members, key=lambda i: chosen[i]["composite_score"] if chosen[i]["composite_score"] is not None else -1e9)
            chosen.pop(weakest)
            removed = True
            break
        if not removed:
            break
    chosen.sort(key=lambda c: (c["industry_group"], -(c["composite_score"] or -1e9)))
    return chosen


def _candidate_row(r, group: str) -> dict:
    out: dict = {}
    for f in CANDIDATE_FIELDS:
        val = r.get(f) if hasattr(r, "get") else None
        if f in ("uid", "ticker", "name", "industry_group", "industry", "region", "zone_v2"):
            out[f] = None if val is None or (isinstance(val, float) and pd.isna(val)) else str(val)
        else:
            v = _num(val)
            out[f] = None if v is None else round(v, 3)
    out["industry_group"] = group
    return out


def render_candidates(rows: list[dict]) -> str:
    if not rows:
        return "(keine Kandidaten)"
    head = "uid | Ticker | Name | Industriegruppe | Region | Composite | Zone | z_value | z_quality | z_momentum | z_investment | mom_12_1 | eps_rev_3m"
    lines = [head]
    for c in rows:
        lines.append(
            f"{c['uid']} | {c['ticker']} | {c['name']} | {c['industry_group']} | {c.get('region') or '–'} | "
            f"{_f(c.get('composite_score'), 1)} | {c.get('zone_v2') or '–'} | {_f(c.get('z_value'))} | "
            f"{_f(c.get('z_quality'))} | {_f(c.get('z_momentum'))} | {_f(c.get('z_investment'))} | "
            f"{_f(c.get('mom_12_1'))} | {_f(c.get('eps_revisions_3m'), 3)}"
        )
    return "\n".join(lines)


def plan_trend_calls(n_candidates: int, settings) -> str:
    """``"single"`` wenn alle Kandidaten in einen Call passen, sonst ``"two_pass"``."""
    cap = int(getattr(settings, "analyst_trend_max_candidates", 150))
    return "single" if n_candidates <= cap else "two_pass"


# --------------------------------------------------------------------------- #
# Nachbearbeitung
# --------------------------------------------------------------------------- #
def tag_pricing(row) -> str:
    """Quant-Tag: hat der Markt den Effekt schon eingepreist?"""
    get = row.get if hasattr(row, "get") else (lambda k, d=None: d)
    z_mom = _num(get("z_momentum"))
    z_rev = _num(get("z_eps_revisions_3m"))
    mom = _num(get("mom_12_1"))
    z_val = _num(get("z_value"))
    if (z_mom is not None and z_mom >= 0.75 and z_rev is not None and z_rev >= 0.5) or (
        mom is not None and mom >= 0.30
    ):
        return PRICING_PRICED
    if (z_mom is None or z_mom <= 0.25) and (z_rev is None or z_rev <= 0.25):
        if z_mom is None and z_rev is None and z_val is None:
            return PRICING_PARTIAL
        return PRICING_EARLY
    return PRICING_PARTIAL


def normalize_llm_pricing(value) -> str | None:
    """LLM-Urteil „eingepreist“ tolerant auf die drei Tags abbilden."""
    from .normalize import canon

    key = canon(value)
    if not key:
        return None
    if key in _PRICING_FROM_LLM:
        return _PRICING_FROM_LLM[key]
    if key.startswith("frueh") or key.startswith("early") or "nicht_eingepreist" in key or "noch_nicht" in key:
        return PRICING_EARLY
    if key.startswith("teil") or "partial" in key or "partly" in key:
        return PRICING_PARTIAL
    if key.startswith("bereits") or key.startswith("voll") or "fully" in key or "already" in key or key == "eingepreist":
        return PRICING_PRICED
    return None


def suggested_action(row, pricing: str, held: bool) -> str:
    if held:
        return ACTION_POSITION
    get = row.get if hasattr(row, "get") else (lambda k, d=None: d)
    zone = str(get("zone_v2") or "")
    if zone == "FILTER":
        return ACTION_INFO
    if zone in ("KANDIDAT", "HALTEN") and pricing == PRICING_EARLY:
        return ACTION_CANDIDATE
    return ACTION_WATCH


def validate_beneficiaries(
    trend: dict,
    candidates: dict[str, dict],
    scored: pd.DataFrame,
    held_uids: set[str],
    max_n: int,
) -> tuple[list[dict], list[Diagnostic]]:
    """Profiteure eines Trends prüfen und anreichern.

    Unbekannte uids (nicht in der Kandidatentabelle) werden verworfen,
    die Liste auf ``max_n`` gekürzt; jeder Link erhält ``pricing_quant``,
    ``pricing_llm`` und ``action``."""
    diags: list[Diagnostic] = []
    links: list[dict] = []
    raw = trend.get("profiteure") or []
    if not isinstance(raw, list):
        return [], diags
    by_ticker = {str(c.get("ticker") or "").upper(): uid for uid, c in candidates.items()}
    seen: set[str] = set()
    for b in raw:
        if not isinstance(b, dict):
            continue
        uid = str(b.get("uid") or "").strip()
        if uid not in candidates:
            alt = by_ticker.get(str(b.get("ticker") or uid).upper())
            if alt is None:
                diags.append(
                    Diagnostic(
                        SEV_WARNING,
                        "trend_unknown_uid",
                        f"Trend „{trend.get('titel')}“: Profiteur „{b.get('ticker') or uid}“ "
                        "nicht in der Kandidatentabelle — verworfen.",
                    )
                )
                continue
            uid = alt
        if uid in seen:
            continue
        seen.add(uid)
        cand = candidates[uid]
        row = cand
        if scored is not None and not scored.empty and "uid" in scored.columns:
            hit = scored[scored["uid"].astype(str) == uid]
            if not hit.empty:
                row = hit.iloc[0]
        pricing_q = tag_pricing(row)
        pricing_llm = normalize_llm_pricing(b.get("eingepreist"))
        if pricing_llm and pricing_llm != pricing_q:
            diags.append(
                Diagnostic(
                    SEV_INFO,
                    "trend_pricing_disagreement",
                    f"{cand.get('ticker')}: LLM „{pricing_llm}“ vs. Quant „{pricing_q}“ "
                    f"(Trend „{trend.get('titel')}“).",
                    uid,
                )
            )
        from .normalize import norm_enum

        role = norm_enum(b.get("rolle"), ROLES, {"supplier": "zulieferer", "direct": "direkt", "second_order": "zweite_ordnung", "indirekt": "zweite_ordnung"}) or "direkt"
        reasoning = b.get("begruendungskette") or []
        if isinstance(reasoning, str):
            reasoning = [reasoning]
        evidence = b.get("evidenz") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        links.append(
            {
                "uid": uid,
                "ticker": cand.get("ticker"),
                "name": cand.get("name"),
                "industry_group": cand.get("industry_group"),
                "zone": cand.get("zone_v2"),
                "composite_score": cand.get("composite_score"),
                "role": role,
                "pricing_quant": pricing_q,
                "pricing_llm": pricing_llm,
                "pricing_llm_reason": b.get("eingepreist_begruendung"),
                "action": suggested_action(row, pricing_q, uid in held_uids),
                "action_llm": b.get("vorgeschlagene_aktion"),
                "reasoning": [str(x) for x in reasoning][:8],
                "evidence": [str(x) for x in evidence][:8],
            }
        )
        if len(links) >= max_n:
            break
    return links, diags
