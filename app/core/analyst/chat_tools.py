"""Werkzeuge des KI-Chats: gespeicherte Daten des Tools und Live-Abrufe
bei Alpha Vantage.

Jedes Werkzeug ist eine reine Funktion ``fn(args, ctx) -> str`` und
liefert Text (Markdown), den das Modell in seine Antwort einbaut.
:func:`run_tool` raised nie — Fehler werden als Text zurückgegeben,
Ergebnisse werden auf ``ctx.max_tool_chars`` gekürzt, jeder Aufruf landet
im ``ctx.trace`` und Quellen (URLs/Datenstände) in ``ctx.sources``.

Die Werkzeug-Definitionen (``tool_specs``) folgen dem Ollama-/OpenAI-
Format ``{"type": "function", "function": {name, description,
parameters}}``.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd

from app.core import persistence
from app.core.uid import row_by_uid, slugify_name
from app.ui.formatters import fmt_de
from app.ui.labels import label_for

from . import snapshot as snap
from . import store

log = logging.getLogger(__name__)

ZONES = ("KANDIDAT", "HALTEN", "VERKAUFEN", "FILTER")
MAX_RESOLVE_HITS = 8
PRICE_CACHE_MAX_AGE_DAYS = 3
TRUNCATION_NOTE = "\n\n[… gekürzt: Ausgabe überschreitet das Zeichenlimit des Werkzeugs]"


# --------------------------------------------------------------------------- #
# Kontext und Werkzeug-Typ
# --------------------------------------------------------------------------- #
@dataclass
class ChatContext:
    settings: Any
    scored: pd.DataFrame | None
    state: Any = None
    today: date = field(default_factory=date.today)
    page_uid: str | None = None
    use_page_context: bool = True
    max_tool_chars: int = 12000
    av_enabled: bool = False
    rpm: int = 70
    n_tool_calls: int = 0
    n_av_calls: int = 0
    sources: list[str] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)

    def add_source(self, text: str) -> None:
        text = str(text or "").strip()
        if text and text not in self.sources:
            self.sources.append(text)


@dataclass(frozen=True)
class ChatTool:
    name: str
    description: str
    parameters: dict
    fn: Callable[[dict, ChatContext], str]
    live: bool = False


# --------------------------------------------------------------------------- #
# Hilfsfunktionen: Formatierung
# --------------------------------------------------------------------------- #
def _is_na(value) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _fmt(value, decimals: int = 2) -> str:
    """Skalar als Text (Dezimalkomma); None/NaN → „–“."""
    if _is_na(value):
        return "–"
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, (int,)) and not isinstance(value, bool):
        return fmt_de(value, 0)
    if isinstance(value, float):
        if value.is_integer() and abs(value) >= 1000:
            return fmt_de(value, 0)
        return fmt_de(value, decimals)
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_fmt(v) for v in value) if value else "–"
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return str(value)


def _label(key: str) -> str:
    """Spalten-IDs (snake_case) über ``label_for``; bereits lesbare Keys unverändert."""
    key = str(key)
    if " " in key or "-" in key or key != key.lower():
        return key
    return label_for(key)


def _row_cell(value) -> str:
    text = _fmt(value)
    return text.replace("|", "/").replace("\n", " ")


def _table(rows: list[dict], columns: list[str] | None = None, labels: bool = True) -> str:
    """Markdown-Tabelle aus Dict-Zeilen."""
    if not rows:
        return "(keine Zeilen)"
    cols = columns or list(rows[0].keys())
    head = "| " + " | ".join((_label(c) if labels else c) for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = ["| " + " | ".join(_row_cell(r.get(c)) for c in cols) + " |" for r in rows]
    return "\n".join([head, sep, *body])


def _render(value, depth: int = 0, max_rows: int = 40) -> str:
    """Generische Darstellung verschachtelter Dicts/Listen als Markdown."""
    indent = "  " * depth
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            if k in ("available", "stock_columns", "position_columns", "portfolio_columns", "trade_columns"):
                continue
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{indent}- **{_label(k)}:**")
                lines.append(_render(v, depth + 1, max_rows))
            elif isinstance(v, (dict, list)):
                continue
            else:
                lines.append(f"{indent}- {_label(k)}: {_fmt(v)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return f"{indent}(leer)"
        if all(isinstance(v, dict) for v in value):
            rows = value[:max_rows]
            cols = list(rows[0].keys())
            table = _table(rows, cols)
            if depth:
                table = "\n".join(indent + line for line in table.splitlines())
            if len(value) > max_rows:
                table += f"\n{indent}… {len(value) - max_rows} weitere Zeilen"
            return table
        return "\n".join(f"{indent}- {_fmt(v)}" for v in value[:max_rows])
    if isinstance(value, pd.DataFrame):
        return _table(value.head(max_rows).to_dict("records"))
    return f"{indent}{_fmt(value)}"


def _int_arg(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(args.get(key, default))
    except (TypeError, ValueError):
        v = default
    return max(lo, min(hi, v))


def _str_arg(args: dict, key: str) -> str | None:
    v = args.get(key)
    if v is None:
        return None
    v = str(v).strip()
    return v or None


def _list_arg(args: dict, key: str) -> list[str]:
    v = args.get(key)
    if v is None:
        return []
    if isinstance(v, str):
        return [p.strip() for p in re.split(r"[,\s]+", v) if p.strip()]
    if isinstance(v, (list, tuple)):
        return [str(p).strip() for p in v if str(p).strip()]
    return []


# --------------------------------------------------------------------------- #
# Firmen-Auflösung
# --------------------------------------------------------------------------- #
def _hit(row) -> dict:
    return {
        "uid": str(row.get("uid")),
        "ticker": str(row.get("ticker") or ""),
        "name": str(row.get("name") or ""),
        "sector": str(row.get("sector") or ""),
        "region": str(row.get("region") or ""),
        "zone": str(row.get("zone_v2") or ""),
    }


def resolve_uid(ctx: ChatContext, query: str | None, limit: int = MAX_RESOLVE_HITS) -> list[dict]:
    """Treffer für ``query``: uid exakt → Ticker exakt → Namens-Slug exakt →
    Prefix/Contains auf Ticker und Name (max. ``limit``)."""
    df = ctx.scored
    if df is None or df.empty or not query:
        return []
    q = str(query).strip()
    if not q:
        return []
    if "uid" in df.columns:
        hit = df.loc[df["uid"].astype(str) == q]
        if not hit.empty:
            return [_hit(hit.iloc[0])]
    tick = df["ticker"].astype(str).str.upper() if "ticker" in df.columns else pd.Series(dtype=str)
    if not tick.empty:
        hit = df.loc[tick == q.upper()]
        if not hit.empty:
            return [_hit(r) for _, r in hit.head(limit).iterrows()]
    names = df["name"].astype(str) if "name" in df.columns else pd.Series(dtype=str)
    slug = slugify_name(q)
    if slug and not names.empty:
        slugs = names.map(slugify_name)
        hit = df.loc[slugs == slug]
        if not hit.empty:
            return [_hit(r) for _, r in hit.head(limit).iterrows()]
        mask = slugs.str.startswith(slug) | slugs.str.contains(re.escape(slug), na=False)
        if not tick.empty:
            mask = mask | tick.str.startswith(q.upper())
        hit = df.loc[mask]
        if not hit.empty:
            return [_hit(r) for _, r in hit.head(limit).iterrows()]
    return []


def _single(ctx: ChatContext, args: dict, key: str = "uid") -> tuple[pd.Series | None, str | None]:
    """Genau eine Zeile zu ``args[key]`` (Fallback: Seiten-uid)."""
    query = _str_arg(args, key)
    if not query and ctx.use_page_context and ctx.page_uid:
        query = ctx.page_uid
    if not query:
        return None, "Kein Titel angegeben (uid, Ticker oder Name erforderlich)."
    hits = resolve_uid(ctx, query)
    if len(hits) == 1:
        return row_by_uid(ctx.scored, hits[0]["uid"]), None
    if not hits:
        return None, f"„{query}“ ist nicht im Universum (Koyfin-Import). Prüfe die Schreibweise oder nutze resolve_company."
    lines = [f"„{query}“ ist mehrdeutig — bitte uid wählen:"]
    lines.append(_table(hits, ["uid", "ticker", "name", "sector", "region", "zone"]))
    return None, "\n".join(lines)


def _held_info(ctx: ChatContext, uid: str) -> dict:
    """Bestand im aktiven Portfolio, Modellportfolio-Mitgliedschaft, AV-Symbol."""
    out: dict = {}
    state = ctx.state
    if state is not None:
        try:
            weights = state.portfolio_weights()
            if uid in weights:
                out["Bestand (aktives Portfolio)"] = f"{fmt_de(weights[uid] * 100, 1)} %"
            else:
                out["Bestand (aktives Portfolio)"] = "nein"
        except Exception:  # noqa: BLE001
            pass
    try:
        mp = persistence.load_model_portfolio()
        if mp is not None and not mp.empty and "uid" in mp.columns:
            m = mp.loc[mp["uid"].astype(str) == uid]
            if not m.empty:
                w = m.iloc[0].get("weight_effective", m.iloc[0].get("weight"))
                out["Modellportfolio"] = f"ja ({fmt_de(float(w) * 100, 1)} %)" if not _is_na(w) else "ja"
            else:
                out["Modellportfolio"] = "nein"
    except Exception:  # noqa: BLE001
        pass
    sym = _av_symbol_cached(uid)
    out["Alpha-Vantage-Symbol"] = sym or "nicht zugeordnet"
    return out


# --------------------------------------------------------------------------- #
# Gespeicherte Daten
# --------------------------------------------------------------------------- #
def tool_resolve_company(args: dict, ctx: ChatContext) -> str:
    query = _str_arg(args, "query")
    if not query:
        return "Parameter query fehlt."
    hits = resolve_uid(ctx, query)
    if not hits:
        return f"Kein Treffer für „{query}“ im Universum."
    return _table(hits, ["uid", "ticker", "name", "sector", "region", "zone"])


def tool_company_snapshot(args: dict, ctx: ChatContext) -> str:
    row, err = _single(ctx, args)
    if row is None:
        return err or "Unbekannter Titel."
    data = snap.koyfin_snapshot(row, ctx.settings)
    groups = _list_arg(args, "groups")
    if groups:
        wanted = {g.lower() for g in groups}
        data = dict(data)
        data["groups"] = [
            g for g in data["groups"] if any(w in g["title"].lower() for w in wanted)
        ]
    text = snap.render_snapshot_text(data, max_chars=max(2000, ctx.max_tool_chars - 1500))
    extra = _held_info(ctx, data["uid"])
    if extra:
        text += "\n\n**Bezug im Tool:**\n" + "\n".join(f"- {k}: {v}" for k, v in extra.items())
    try:
        stamp = persistence.list_snapshots()
        if stamp:
            ctx.add_source(f"Koyfin-Import (Stand {stamp[0][0].isoformat()})")
        else:
            ctx.add_source("Koyfin-Import (aktueller Stand)")
    except Exception:  # noqa: BLE001
        ctx.add_source("Koyfin-Import (aktueller Stand)")
    return text


def tool_company_history(args: dict, ctx: ChatContext) -> str:
    row, err = _single(ctx, args)
    if row is None:
        return err or "Unbekannter Titel."
    n = _int_arg(args, "n", 8, 1, 24)
    cols = _list_arg(args, "columns") or None
    rows = snap.uid_history(str(row.get("uid")), cols, n=n)
    if not rows:
        return f"Für {row.get('ticker')} ist kein Verlauf archiviert (nur der aktuelle Import)."
    ctx.add_source(f"Koyfin-Archiv ({len(rows)} Snapshots)")
    return f"**Verlauf {row.get('ticker')} — {row.get('name')}**\n\n" + snap.render_history_text(rows)


def tool_company_peers(args: dict, ctx: ChatContext) -> str:
    from app.core.peers import compute_peers

    row, err = _single(ctx, args)
    if row is None:
        return err or "Unbekannter Titel."
    n = _int_arg(args, "n", 6, 1, 12)
    mode = "top_score" if str(args.get("mode") or "").lower().startswith("top") else "similar"
    version = "v2" if str(getattr(ctx.settings, "scoring_version", "v2")) == "v2" else "v1"
    peers = compute_peers(ctx.scored, str(row.get("uid")), n=n, mode=mode, version=version)
    if peers is None or peers.empty:
        return "Keine Peers gefunden."
    cols = [c for c in ("uid", "ticker", "name", "industry", "region", "zone_v2", "composite_score",
                        "z_value", "z_quality", "z_momentum", "z_investment", "distance") if c in peers.columns]
    ctx.add_source("Peer-Vergleich (Koyfin-Universum)")
    return f"**Peers von {row.get('ticker')} ({mode}, {version})**\n\n" + _table(peers[cols].to_dict("records"), cols)


def _apply_thresholds(df: pd.DataFrame, thresholds) -> tuple[pd.DataFrame, list[str]]:
    notes: list[str] = []
    if not isinstance(thresholds, dict):
        return df, notes
    for col, spec in thresholds.items():
        if col not in df.columns:
            notes.append(f"Spalte „{col}“ unbekannt — ignoriert.")
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        if isinstance(spec, dict):
            lo, hi = spec.get("min"), spec.get("max")
        else:
            lo, hi = spec, None
        if lo is not None:
            try:
                df = df.loc[series >= float(lo)]
                series = series.loc[df.index]
            except (TypeError, ValueError):
                notes.append(f"min für „{col}“ ungültig.")
        if hi is not None:
            try:
                df = df.loc[series <= float(hi)]
            except (TypeError, ValueError):
                notes.append(f"max für „{col}“ ungültig.")
    return df, notes


SCREEN_DEFAULT_COLUMNS = (
    "uid", "ticker", "name", "sector", "region", "zone_v2", "composite_score",
    "z_value", "z_quality", "z_momentum", "z_investment", "market_cap",
)


def tool_screen_universe(args: dict, ctx: ChatContext) -> str:
    df = ctx.scored
    if df is None or df.empty:
        return "Kein Universum geladen."
    total = len(df)
    notes: list[str] = []
    for key in ("region", "sector", "industry_group", "industry"):
        val = _str_arg(args, key)
        if val and key in df.columns:
            mask = df[key].astype(str).str.contains(re.escape(val), case=False, na=False)
            df = df.loc[mask]
    zone = _str_arg(args, "zone")
    if zone and "zone_v2" in df.columns:
        df = df.loc[df["zone_v2"].astype(str).str.upper() == zone.upper()]
    if args.get("filter_pass") is not None and "filter_pass" in df.columns:
        want = bool(args.get("filter_pass"))
        df = df.loc[df["filter_pass"].fillna(False).astype(bool) == want]
    df, th_notes = _apply_thresholds(df, args.get("thresholds"))
    notes.extend(th_notes)
    sort_by = _str_arg(args, "sort_by") or "composite_score"
    if sort_by not in df.columns:
        notes.append(f"Sortierspalte „{sort_by}“ unbekannt — composite_score verwendet.")
        sort_by = "composite_score" if "composite_score" in df.columns else None
    ascending = bool(args.get("ascending", False))
    if sort_by:
        df = df.assign(_sort=pd.to_numeric(df[sort_by], errors="coerce")).sort_values(
            "_sort", ascending=ascending, na_position="last"
        ).drop(columns="_sort")
    n = _int_arg(args, "n", 20, 1, 50)
    cols = _list_arg(args, "columns")
    unknown = [c for c in cols if c not in ctx.scored.columns]
    if unknown:
        notes.append(f"Unbekannte Spalten ignoriert: {', '.join(unknown)}.")
    cols = [c for c in cols if c in ctx.scored.columns]
    if not cols:
        cols = [c for c in SCREEN_DEFAULT_COLUMNS if c in ctx.scored.columns]
    else:
        for base in ("uid", "ticker", "name"):
            if base in ctx.scored.columns and base not in cols:
                cols.insert(0, base)
        cols = list(dict.fromkeys(cols))
    if sort_by and sort_by not in cols:
        cols.append(sort_by)
    hits = df.head(n)
    head = f"**Screening: {len(hits)} von {len(df)} Treffern (Universum {total} Titel)**"
    if notes:
        head += "\n\n" + "\n".join(f"- {t}" for t in notes)
    ctx.add_source("Koyfin-Universum (Screening)")
    if hits.empty:
        return head + "\n\nKeine Titel erfüllen die Kriterien."
    return head + "\n\n" + _table(hits[cols].to_dict("records"), cols)


def _cols(ctx: ChatContext) -> dict:
    from app.core.committee_report import resolve_score_columns

    return resolve_score_columns(ctx.settings, ctx.scored)


def _portfolio_uids(ctx: ChatContext) -> set[str]:
    try:
        return set(ctx.state.portfolio_weights().keys()) if ctx.state is not None else set()
    except Exception:  # noqa: BLE001
        return set()


def tool_universe_overview(args: dict, ctx: ChatContext) -> str:
    from app.core.committee_report import compute_universe_overview

    df = ctx.scored
    if df is None or df.empty:
        return "Kein Universum geladen."
    top_n = _int_arg(args, "top_n", 15, 1, 50)
    ov = compute_universe_overview(df, _cols(ctx), top_n=top_n, portfolio_uids=_portfolio_uids(ctx))
    keep = {k: v for k, v in ov.items() if k not in ("all_candidates", "all_stocks", "sell_zone")}
    sell = ov.get("sell_zone") or []
    keep["sell_zone_top"] = sell[:10]
    ctx.add_source("Koyfin-Universum (Übersicht)")
    return "**Universum — Überblick**\n\n" + _render(keep)


def tool_sector_overview(args: dict, ctx: ChatContext) -> str:
    from app.core.sector_momentum import aggregate_sectors

    df = ctx.scored
    if df is None or df.empty:
        return "Kein Universum geladen."
    try:
        history = persistence.load_sector_score_history()
    except Exception:  # noqa: BLE001
        history = None
    score_col = "composite_score" if "composite_score" in df.columns else "total_score"
    agg = aggregate_sectors(df, history, score_col=score_col)
    if not agg:
        return "Keine Sektor-Aggregate berechenbar."
    level = str(args.get("level") or "sector").lower()
    rows = []
    for s in agg:
        base = {k: v for k, v in s.items() if not isinstance(v, (list, dict))}
        rows.append(base)
        if level.startswith("ind"):
            for ind in s.get("industries") or []:
                r = {k: v for k, v in ind.items() if not isinstance(v, (list, dict))}
                r = {"sector": s.get("sector"), **r}
                rows.append(r)
    ctx.add_source("Sektor-Momentum (Koyfin-Universum)")
    return f"**Sektor-Übersicht ({level})**\n\n" + _render(rows, max_rows=80)


def tool_changes_since(args: dict, ctx: ChatContext) -> str:
    from app.core.committee_report import (
        compute_stock_deltas,
        normalize_snapshot_frame,
        pick_comparison_snapshots,
        zone_transition_matrix,
    )
    from app.core.signal_events import load_signal_events

    df = ctx.scored
    if df is None or df.empty:
        return "Kein Universum geladen."
    basis = str(args.get("basis") or "previous").lower()
    snaps = persistence.list_snapshots()
    if not snaps:
        return "Kein Archiv-Snapshot vorhanden — Vergleiche sind erst nach dem zweiten Import möglich."
    pick = pick_comparison_snapshots(snaps, ctx.today)
    key = "month" if basis.startswith("m") else "prev"
    chosen = pick.get(key) or pick.get("prev")
    if chosen is None:
        return "Kein geeigneter Vergleichs-Snapshot gefunden."
    prev_date = chosen if isinstance(chosen, date) else chosen.get("date") if isinstance(chosen, dict) else None
    if prev_date is None:
        return "Kein geeigneter Vergleichs-Snapshot gefunden."
    prev = normalize_snapshot_frame(persistence.load_universe_snapshot(prev_date))
    if prev is None or prev.empty:
        return f"Snapshot {prev_date.isoformat()} konnte nicht geladen werden."
    cols = _cols(ctx)
    deltas = compute_stock_deltas(df, prev, cols)
    uid_q = _str_arg(args, "uid")
    if uid_q:
        row, err = _single(ctx, {"uid": uid_q})
        if row is None:
            return err or "Unbekannter Titel."
        one = deltas.loc[deltas["uid"].astype(str) == str(row.get("uid"))]
        ctx.add_source(f"Koyfin-Archiv (Vergleich mit {prev_date.isoformat()})")
        if one.empty:
            return f"{row.get('ticker')} war im Snapshot {prev_date.isoformat()} nicht enthalten."
        return f"**Veränderung {row.get('ticker')} seit {prev_date.isoformat()}**\n\n" + _render(one.to_dict("records"))
    n = _int_arg(args, "n", 15, 1, 40)
    parts = [f"**Veränderungen seit Snapshot {prev_date.isoformat()}**"]
    parts.append("**Zonenwechsel-Matrix (Zeilen vorher, Spalten heute):**\n" + zone_transition_matrix(deltas).to_string())
    valid = deltas.dropna(subset=["delta_score"]) if "delta_score" in deltas.columns else deltas
    show = [c for c in ("uid", "ticker", "name", "score_prev", "score_now", "delta_score", "zone_prev", "zone_now") if c in valid.columns]
    parts.append("**Größte Verbesserungen:**\n" + _table(valid.sort_values("delta_score", ascending=False).head(n)[show].to_dict("records"), show))
    parts.append("**Größte Verschlechterungen:**\n" + _table(valid.sort_values("delta_score", ascending=True).head(n)[show].to_dict("records"), show))
    zc = valid.loc[valid["zone_prev"].astype(str) != valid["zone_now"].astype(str)] if {"zone_prev", "zone_now"} <= set(valid.columns) else pd.DataFrame()
    if not zc.empty:
        parts.append(f"**Zonenwechsel ({len(zc)}):**\n" + _table(zc.head(n)[show].to_dict("records"), show))
    try:
        ev = load_signal_events(df)
        if ev is not None and not ev.empty:
            new = ev.loc[ev["is_new"].fillna(False).astype(bool)] if "is_new" in ev.columns else ev
            if not new.empty:
                parts.append(f"**Neue SMA-Signale ({len(new)}):**\n" + _render(new.head(n).to_dict("records")))
    except Exception:  # noqa: BLE001
        pass
    ctx.add_source(f"Koyfin-Archiv (Vergleich mit {prev_date.isoformat()})")
    return "\n\n".join(parts)


def tool_portfolio_overview(args: dict, ctx: ChatContext) -> str:
    from app.core.committee_report import compute_portfolio_section

    if ctx.state is None or ctx.scored is None or ctx.scored.empty:
        return "Kein Portfolio/Universum geladen."
    pid = args.get("portfolio_id")
    try:
        pid = int(pid) if pid not in (None, "") else None
    except (TypeError, ValueError):
        pid = None
    diags: list = []
    sec = compute_portfolio_section(
        ctx.state, ctx.scored, _cols(ctx), ctx.settings, pid, ctx.today,
        {"vs_prev": {"available": False}, "vs_month": {"available": False}}, None, diags,
    )
    if not sec.get("available", True):
        return sec.get("note") or "Portfolio nicht verfügbar."
    keep = {k: v for k, v in sec.items() if k not in ("positions", "benchmark_sector_weights", "benchmark_region_weights")}
    positions = sec.get("positions") or []
    keep["positions"] = positions[:60]
    ctx.add_source(f"Portfolio „{sec.get('name')}“ (Tool-Daten)")
    return f"**Portfolio {sec.get('name')}**\n\n" + _render(keep, max_rows=60)


def tool_model_portfolio_overview(args: dict, ctx: ChatContext) -> str:
    from app.core.committee_report import compute_model_portfolio_section

    if ctx.state is None or ctx.scored is None or ctx.scored.empty:
        return "Kein Universum geladen."
    diags: list = []
    sec = compute_model_portfolio_section(ctx.state, ctx.scored, _cols(ctx), ctx.settings, ctx.today, ctx.today, diags)
    if not sec.get("available", True):
        return sec.get("note") or "Kein Modellportfolio gespeichert."
    keep = {k: v for k, v in sec.items() if k not in ("all_trades", "run_diagnostics")}
    ctx.add_source(f"Modellportfolio (Stand {_fmt(sec.get('run_date'))})")
    return "**Modellportfolio**\n\n" + _render(keep, max_rows=60)


def tool_factor_timing_state(args: dict, ctx: ChatContext) -> str:
    from app.core.committee_report import compute_factor_timing_section

    if ctx.scored is None or ctx.scored.empty:
        return "Kein Universum geladen."
    sec = compute_factor_timing_section(ctx.scored, ctx.settings)
    if not sec.get("available", True):
        return sec.get("note") or "Factor Timing nicht verfügbar."
    ctx.add_source("Factor Timing (Tool-Daten)")
    return "**Factor Timing**\n\n" + _render(sec)


def tool_risk_overview(args: dict, ctx: ChatContext) -> str:
    from app.core.committee_report import compute_risk_section

    if ctx.state is None or ctx.scored is None or ctx.scored.empty:
        return "Kein Universum geladen."
    pid = args.get("portfolio_id")
    try:
        pid = int(pid) if pid not in (None, "") else None
    except (TypeError, ValueError):
        pid = None
    sec = compute_risk_section(ctx.state, ctx.scored, ctx.settings, pid, ctx.today)
    if not sec.get("available", True):
        return sec.get("note") or "Risikomodul: kein Kurs-Cache vorhanden (python -m app.tools.risk_report update)."
    ctx.add_source("Risikomodul (Kurs-Cache)")
    return "**Risiko-Überblick**\n\n" + _render(sec, max_rows=30)


def _insight_summary(ins: dict | None, kind: str) -> dict | None:
    if not ins:
        return None
    content = ins.get("content") or {}
    out = {"run_id": ins.get("run_id"), "period": ins.get("period"), "status": ins.get("status"), "created_at": ins.get("created_at")}
    if isinstance(content, dict):
        if kind == "synthesis":
            for key in ("vorschlag", "faktor_zusammenspiel", "quellen_zusammenhaenge", "nicht_eingepreiste_themen", "verbundene_unternehmen"):
                if content.get(key):
                    out[key] = content[key]
        elif kind == "transcript_summary":
            for key in ("zusammenfassung", "guidance", "ton", "vertrauen_management", "kernaussagen", "risiken", "qa_highlights", "quant_abgleich"):
                if content.get(key) is not None:
                    out[key] = content[key]
        else:
            for key in ("zusammenfassung", "themen", "wesentlichkeit", "quant_abgleich"):
                if content.get(key) is not None:
                    out[key] = content[key]
    return out


def tool_analyst_insights(args: dict, ctx: ChatContext) -> str:
    row, err = _single(ctx, args)
    if row is None:
        return err or "Unbekannter Titel."
    uid = str(row.get("uid"))
    parts = [f"**KI-Analyst: {row.get('ticker')} — {row.get('name')}**"]
    sugg = store.list_suggestions(uid=uid, limit=5)
    if sugg:
        parts.append("**Vorschläge (neueste zuerst):**\n" + _table(
            sugg, [c for c in ("created_at", "suggestion_type", "confidence", "quant_consistency", "status", "rationale") if c in sugg[0]]))
    syn = _insight_summary(store.load_latest_synthesis(uid), "synthesis")
    if syn:
        parts.append("**Letzte Synthese:**\n" + _render(syn))
    tr = _insight_summary(store.load_insight(uid, "transcript_summary"), "transcript_summary")
    if tr:
        parts.append("**Earnings-Call-Zusammenfassung:**\n" + _render(tr))
    nd = _insight_summary(store.load_insight(uid, "news_digest"), "news_digest")
    if nd:
        parts.append("**News-Digest:**\n" + _render(nd))
    events = store.list_events(limit=15, uid=uid)
    if events:
        parts.append("**Ereignisse:**\n" + _table(events, [c for c in ("event_date", "kind", "period", "title") if c in events[0]]))
    links = store.load_company_links_for(uid)
    if links:
        parts.append("**Verbundene Unternehmen:**\n" + _table(links, [c for c in ("ticker", "related_ticker", "related_name", "relation", "context") if c in links[0]]))
    trends = store.load_trend_links_for(uid)
    if trends:
        parts.append("**Trend-Profiteur:**\n" + _render(trends))
    if len(parts) == 1:
        return parts[0] + "\n\nNoch keine Analyst-Ergebnisse für diesen Titel gespeichert (Lauf über die Seite KI-Analyst oder `python -m app.tools.analyst run`)."
    ctx.add_source("KI-Analyst (gespeicherte Läufe)")
    return "\n\n".join(parts)


def tool_analyst_run_overview(args: dict, ctx: ChatContext) -> str:
    n = _int_arg(args, "n_runs", 3, 1, 10)
    runs = store.list_runs(limit=n)
    if not runs:
        return "Noch kein Analyst-Lauf gespeichert."
    parts = ["**Letzte Analyst-Läufe**\n" + _table(
        runs, [c for c in ("run_id", "status", "scope", "model", "started_at", "n_companies", "n_events", "n_llm_calls", "n_themes", "n_trends") if c in runs[0]])]
    sugg = store.list_suggestions(limit=20)
    if sugg:
        parts.append("**Offene Vorschläge:**\n" + _table(
            sugg, [c for c in ("ticker", "suggestion_type", "confidence", "quant_zone", "quant_consistency", "created_at") if c in sugg[0]]))
    themes = store.list_themes(limit=10)
    if themes:
        parts.append("**Themen (letzter Lauf mit Themen):**\n" + _render(
            [{k: v for k, v in t.items() if k in ("title", "kind", "direction", "confidence", "description", "uids")} for t in themes]))
    trends = store.list_trends(limit=6)
    if trends:
        rows = []
        for t in trends:
            rows.append({
                "title": t.get("title"), "kind": t.get("kind"), "horizon": t.get("horizon"), "strength": t.get("strength"),
                "confidence": t.get("confidence"),
                "beneficiaries": ", ".join(f"{l.get('ticker')} ({l.get('role')}, {l.get('pricing_quant')})" for l in (t.get("links") or t.get("beneficiaries") or [])[:8]),
            })
        parts.append("**Trends & Profiteure:**\n" + _table(rows, list(rows[0].keys())))
    ctx.add_source("KI-Analyst (gespeicherte Läufe)")
    return "\n\n".join(parts)


METHODOLOGY_TOPICS = {
    "zonen": (
        "**Zonen (Composite v2):** KANDIDAT = Universumsfilter bestanden und Composite im oberen Bereich "
        "(Kaufkandidat); HALTEN = Filter bestanden, mittlerer Bereich (Bestand halten, kein Neukauf); "
        "VERKAUFEN = Filter bestanden, unterer Bereich (Position überprüfen/abbauen); FILTER = Universumsfilter "
        "nicht bestanden (Marktkapitalisierung, Piotroski, Liquidität, Abdeckung, Listing-Dauer, Verschuldung, "
        "Zinsdeckung) — keine Investition unabhängig vom Score."
    ),
    "faktoren": (
        "**Faktoren (Composite v2):** Value (Bewertung: u. a. EV/EBIT, KGV, FCF-Rendite, KBV), Quality "
        "(Profitabilität/Stabilität: u. a. ROIC, Bruttomarge, Accruals, Piotroski), Momentum (12-1-Monats-Kurs-"
        "momentum, EPS-Revisionen, SMA-Signale), Investment (konservatives Wachstum von Bilanz/Aktienanzahl). "
        "Jeder Indikator wird winsorisiert und innerhalb der Neutralisierungsgruppe (Sektor/Region) z-standardisiert; "
        "Z > 0 = besser als Gruppenmittel, Z ≈ ±1 = eine Standardabweichung. Faktor-Z = gewichteter Mittelwert der "
        "Indikator-Z; Composite = gewichtete Summe der Faktor-Z, erneut winsorisiert."
    ),
    "filter": (
        "**Universumsfilter:** Mindest-Marktkapitalisierung, Mindest-Piotroski-F-Score, Mindest-Handelsvolumen (ADV), "
        "Mindest-Datenabdeckung, Mindest-Listing-Dauer, maximale Verschuldung (D/E) und Mindest-Zinsdeckung (ICR). "
        "Nicht bestandene Kriterien stehen je Titel in filter_reasons (Snapshot-Werkzeug)."
    ),
    "signale": (
        "**Technische Signale:** sma_signal (GOLDEN CROSS = SMA-50 über SMA-200 gekreuzt, DEATH CROSS umgekehrt, "
        "Kurs über/unter SMA-200), trend_phase (Trendphasen inkl. „müder“ Bulle/Bär), trend_warning. Diese "
        "Signale sind Overlays, sie verändern den Composite nicht."
    ),
    "piotroski": (
        "**Piotroski F-Score (0–9):** neun binäre Kriterien aus Profitabilität (ROA > 0, OCF > 0, ΔROA > 0, "
        "OCF > Nettogewinn), Verschuldung/Liquidität (ΔLeverage < 0, ΔCurrent Ratio > 0, keine Aktienausgabe) und "
        "Effizienz (ΔBruttomarge > 0, ΔAsset Turnover > 0). Die Rohdaten stehen im Koyfin-Snapshot."
    ),
    "altman": (
        "**Altman Z-Score:** Insolvenzrisiko-Kennzahl aus Working Capital, Gewinnrücklagen, EBIT, Marktwert des "
        "Eigenkapitals und Umsatz je Bilanzsumme; > 2,99 sicher, < 1,81 Risikozone."
    ),
    "modellportfolio": (
        "**Modellportfolio:** Zielportfolio aus KANDIDAT-/HALTEN-Titeln nach Portfoliokonstruktions-Regeln "
        "(Sektor-/Regionenbänder, Positionsgrenzen, Turnover-Limit, Tracking-Error ex ante). Trades = Differenz "
        "zum Quellportfolio; „deferred“ = wegen Turnover-Budget verschoben."
    ),
    "factor_timing": (
        "**Factor Timing:** Regime-Erkennung aus Makro (Zinsstruktur, Inflation, Öl) und Faktor-Momentum im "
        "Universum; taktische Faktorgewichte weichen innerhalb von Bändern von den strategischen ab (Modus monitor "
        "= nur Anzeige, apply = Gewichte wirken im Composite)."
    ),
    "ki_analyst": (
        "**KI-Analyst:** periodischer Lauf, der je Titel Earnings, Transkripte, News, Insider-Transaktionen und "
        "Koyfin-Daten zu einer Synthese verbindet (Faktor-Zusammenspiel, quellenübergreifende Zusammenhänge, nicht "
        "eingepreiste Themen, Vorschlag mit Konfidenz) sowie Themen und Trends mit Profiteuren ermittelt. "
        "Vorschläge sind Entscheidungsunterstützung, keine Handelsanweisung."
    ),
}


def tool_methodology(args: dict, ctx: ChatContext) -> str:
    from app.core.committee_report import compute_parameter_appendix

    topic = (_str_arg(args, "topic") or "alle").lower()
    parts: list[str] = []
    if topic in ("alle", "all", "overview", "übersicht"):
        parts.extend(METHODOLOGY_TOPICS.values())
    else:
        for key, text in METHODOLOGY_TOPICS.items():
            if key in topic or topic in key:
                parts.append(text)
        if not parts:
            parts.extend(METHODOLOGY_TOPICS.values())
    if topic in ("alle", "all", "parameter", "einstellungen", "faktoren", "filter"):
        try:
            app = compute_parameter_appendix(ctx.settings, None)
            rows = []
            for g in app.get("groups", []):
                for r in g.get("rows", []):
                    if topic in ("faktoren",) and "Faktor" not in g["title"] and "Composite" not in g["title"]:
                        continue
                    if topic in ("filter",) and "filter" not in g["title"].lower():
                        continue
                    rows.append({"Gruppe": g["title"], "Parameter": r["label"], "Wert": r["value"]})
            if rows:
                parts.append("**Aktuelle Parameter:**\n" + _table(rows, ["Gruppe", "Parameter", "Wert"], labels=False))
        except Exception as exc:  # noqa: BLE001
            parts.append(f"(Parameter nicht lesbar: {exc})")
    ctx.add_source("Methodik (MODEL_DESCRIPTION / Einstellungen)")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Alpha Vantage live
# --------------------------------------------------------------------------- #
def _av_symbol_cached(uid: str) -> str | None:
    try:
        ws = store.load_watch_state(uid)
        if ws and ws.get("av_symbol"):
            return str(ws["av_symbol"])
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.core import av_store

        m = av_store.load_av_mapping(uid)
        if m and m.get("av_symbol"):
            return str(m["av_symbol"])
    except Exception:  # noqa: BLE001
        pass
    return None


def _resolve_av_symbol(ctx: ChatContext, uid: str) -> str | None:
    sym = _av_symbol_cached(uid)
    if sym:
        return sym
    try:
        from app.core import market_data

        resolved, _unres = market_data.resolve_symbols([uid], ctx.scored, ctx.settings, persist=True)
        ctx.n_av_calls += 1
        hit = resolved.get(uid)
        if hit is not None:
            return str(hit.av_symbol)
    except Exception as exc:  # noqa: BLE001
        log.info("AV-Symbolauflösung für %s fehlgeschlagen: %s", uid, exc)
    return None


NOT_AT_AV = (
    "{ticker} ist nicht bei Alpha Vantage verfügbar (kein Symbol auflösbar) — nur gespeicherte "
    "Koyfin-Daten nutzbar (company_snapshot, company_history)."
)


def _av_target(ctx: ChatContext, args: dict) -> tuple[str | None, str | None, str | None]:
    """(symbol, ticker_label, fehler). ``symbol``-Argument hat Vorrang vor uid."""
    explicit = _str_arg(args, "symbol")
    if explicit:
        return explicit.upper(), explicit.upper(), None
    row, err = _single(ctx, args)
    if row is None:
        return None, None, err
    uid = str(row.get("uid"))
    sym = _resolve_av_symbol(ctx, uid)
    if not sym:
        return None, None, NOT_AT_AV.format(ticker=row.get("ticker"))
    return sym, str(row.get("ticker")), None


def _av_error(exc: Exception) -> str:
    kind = getattr(exc, "kind", None)
    if kind == "rate_limit":
        return "Alpha Vantage: Rate-Limit erreicht — bitte später erneut versuchen."
    if kind == "auth":
        return "Alpha Vantage: API-Key ungültig oder fehlt (ALPHAVANTAGE_API_KEY)."
    return f"Alpha Vantage: {exc}"


def _av_modules():
    from app.core import av_client, av_fundamentals

    return av_client, av_fundamentals


def tool_av_news(args: dict, ctx: ChatContext) -> str:
    _, avf = _av_modules()
    days = _int_arg(args, "days", 7, 1, 30)
    limit = _int_arg(args, "limit", 15, 1, 30)
    topic = _str_arg(args, "topic")
    time_from = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    label = topic
    tickers = None
    if not topic:
        sym, label, err = _av_target(ctx, args)
        if err:
            return err
        tickers = [sym]
    try:
        ctx.n_av_calls += 1
        items = avf.fetch_news_sentiment(
            tickers=tickers, topics=[topic] if topic else None, time_from=time_from,
            limit=limit, requests_per_minute=ctx.rpm,
        )
    except Exception as exc:  # noqa: BLE001
        return _av_error(exc)
    if not items:
        return f"Keine News zu {label} in den letzten {days} Tagen."
    lines = [f"**News zu {label} (Alpha Vantage, letzte {days} Tage, {len(items)} Treffer)**", ""]
    for i, it in enumerate(items[:limit], 1):
        ts = it.get("time_published")
        when = avf.format_news_time(ts) if ts else "–"
        sent = it.get("overall_sentiment_label") or "–"
        score = it.get("overall_sentiment_score")
        rel = ""
        for t in it.get("ticker_sentiment") or []:
            if tickers and str(t.get("ticker", "")).upper() == tickers[0].upper():
                rel = f"; Relevanz {fmt_de(t.get('relevance_score'), 2)}, Ticker-Sentiment {t.get('sentiment_label')}"
        lines.append(f"[{i}] {when} · {it.get('source') or '–'} · **{it.get('title')}** (Sentiment {sent} {fmt_de(score, 2)}{rel})")
        if it.get("summary"):
            lines.append(f"    {str(it['summary'])[:300]}")
        if it.get("url"):
            lines.append(f"    {it['url']}")
            ctx.add_source(f"[{i}] {it.get('source') or 'News'}: {it.get('title')} — {it['url']}")
    return "\n".join(lines)


def tool_av_earnings(args: dict, ctx: ChatContext) -> str:
    _, avf = _av_modules()
    sym, label, err = _av_target(ctx, args)
    if err:
        return err
    n = _int_arg(args, "n", 8, 1, 20)
    try:
        ctx.n_av_calls += 1
        rows = avf.fetch_earnings(sym, requests_per_minute=ctx.rpm)
    except Exception as exc:  # noqa: BLE001
        return _av_error(exc)
    if not rows:
        return f"Alpha Vantage liefert keine Quartals-EPS für {label} ({sym})."
    ctx.add_source(f"Alpha Vantage EARNINGS ({sym})")
    cols = ["fiscal_date_ending", "reported_date", "reported_eps", "estimated_eps", "surprise", "surprise_pct"]
    return f"**Quartals-EPS {label} ({sym})**\n\n" + _table(rows[:n], cols)


def tool_av_transcript(args: dict, ctx: ChatContext) -> str:
    _, avf = _av_modules()
    row, err = _single(ctx, args)
    if row is None:
        return err or "Unbekannter Titel."
    uid = str(row.get("uid"))
    quarter = _str_arg(args, "quarter")
    stored = store.load_insight(uid, "transcript_summary", quarter)
    if stored and stored.get("content"):
        ctx.add_source(f"Earnings-Call-Zusammenfassung {stored.get('period')} (KI-Analyst)")
        return (f"**Gespeicherte Earnings-Call-Zusammenfassung {row.get('ticker')} {stored.get('period')}**\n\n"
                + _render(_insight_summary(stored, "transcript_summary")))
    sym = _resolve_av_symbol(ctx, uid)
    if not sym:
        return NOT_AT_AV.format(ticker=row.get("ticker"))
    if not quarter:
        try:
            ctx.n_av_calls += 1
            latest = avf.latest_reported_quarter(avf.fetch_earnings(sym, requests_per_minute=ctx.rpm))
        except Exception as exc:  # noqa: BLE001
            return _av_error(exc)
        if not latest:
            return f"Kein berichtetes Quartal für {row.get('ticker')} bei Alpha Vantage."
        quarter = latest[0]
    try:
        ctx.n_av_calls += 1
        tr = avf.fetch_transcript(sym, quarter, requests_per_minute=ctx.rpm)
    except Exception as exc:  # noqa: BLE001
        return _av_error(exc)
    if not tr or not tr.get("segments"):
        return f"Kein Transkript für {row.get('ticker')} {quarter} bei Alpha Vantage (typisch für Nicht-US-Titel)."
    ctx.add_source(f"Alpha Vantage EARNINGS_CALL_TRANSCRIPT ({sym} {quarter})")
    budget = max(3000, ctx.max_tool_chars - 600)
    lines = [f"**Earnings-Call-Transkript {row.get('ticker')} {quarter} (gekürzt)**", ""]
    used = 0
    for seg in tr["segments"]:
        chunk = f"**{seg.get('speaker')} ({seg.get('title') or '–'}):** {seg.get('content')}"
        if used + len(chunk) > budget:
            lines.append("… (weitere Segmente ausgelassen)")
            break
        lines.append(chunk)
        used += len(chunk)
    return "\n\n".join(lines)


def tool_av_insider(args: dict, ctx: ChatContext) -> str:
    _, avf = _av_modules()
    sym, label, err = _av_target(ctx, args)
    if err:
        return err
    days = _int_arg(args, "days", 180, 7, 730)
    try:
        ctx.n_av_calls += 1
        rows = avf.fetch_insider_transactions(sym, since=ctx.today - timedelta(days=days), requests_per_minute=ctx.rpm)
    except Exception as exc:  # noqa: BLE001
        return _av_error(exc)
    if not rows:
        return f"Keine Insider-Transaktionen für {label} ({sym}) in den letzten {days} Tagen."
    buys = [r for r in rows if r.get("acquisition_or_disposal") == "A"]
    sells = [r for r in rows if r.get("acquisition_or_disposal") == "D"]
    ctx.add_source(f"Alpha Vantage INSIDER_TRANSACTIONS ({sym})")
    head = (f"**Insider-Transaktionen {label} ({sym}, {days} Tage):** {len(buys)} Käufe, {len(sells)} Verkäufe; "
            f"Volumen Käufe {fmt_de(sum((r.get('shares') or 0) * (r.get('share_price') or 0) for r in buys), 0)}, "
            f"Verkäufe {fmt_de(sum((r.get('shares') or 0) * (r.get('share_price') or 0) for r in sells), 0)}")
    cols = ["transaction_date", "executive", "executive_title", "acquisition_or_disposal", "shares", "share_price"]
    return head + "\n\n" + _table(rows[:25], cols)


def _returns(series: pd.Series, today: date) -> dict:
    s = series.dropna()
    if s.empty:
        return {}
    last = float(s.iloc[-1])
    last_date = s.index[-1]
    out = {"Letzter Kurs": last, "Stand": pd.Timestamp(last_date).date().isoformat()}

    def ret(days: int, label: str) -> None:
        cutoff = pd.Timestamp(last_date) - pd.Timedelta(days=days)
        past = s.loc[s.index <= cutoff]
        if not past.empty:
            out[label] = f"{fmt_de((last / float(past.iloc[-1]) - 1) * 100, 1)} %"

    ret(7, "Rendite 1W")
    ret(30, "Rendite 1M")
    ret(91, "Rendite 3M")
    ret(182, "Rendite 6M")
    ret(365, "Rendite 1J")
    ytd = s.loc[s.index < pd.Timestamp(year=pd.Timestamp(last_date).year, month=1, day=1)]
    if not ytd.empty:
        out["Rendite YTD"] = f"{fmt_de((last / float(ytd.iloc[-1]) - 1) * 100, 1)} %"
    year = s.loc[s.index >= pd.Timestamp(last_date) - pd.Timedelta(days=365)]
    if not year.empty:
        hi, lo = float(year.max()), float(year.min())
        out["52W-Hoch"] = hi
        out["52W-Tief"] = lo
        out["Abstand 52W-Hoch"] = f"{fmt_de((last / hi - 1) * 100, 1)} %"
    for w in (50, 200):
        if len(s) >= w:
            sma = float(s.tail(w).mean())
            out[f"Abstand SMA-{w}"] = f"{fmt_de((last / sma - 1) * 100, 1)} %"
    return out


def tool_av_prices(args: dict, ctx: ChatContext) -> str:
    from app.core import av_store

    av_client, _ = _av_modules()
    sym, label, err = _av_target(ctx, args)
    if err:
        return err
    try:
        cached = av_store.load_prices(sym)
    except Exception:  # noqa: BLE001
        cached = pd.DataFrame()
    fresh = False
    if cached is not None and not cached.empty:
        last = pd.Timestamp(cached.index.max()).date()
        fresh = (ctx.today - last).days <= PRICE_CACHE_MAX_AGE_DAYS
    if not fresh:
        try:
            ctx.n_av_calls += 1
            size = "compact" if (cached is not None and not cached.empty) else "full"
            df = av_client.fetch_daily_adjusted(sym, size, requests_per_minute=ctx.rpm)
            try:
                av_store.save_prices(sym, df)
                cached = av_store.load_prices(sym)
            except Exception:  # noqa: BLE001
                cached = df
        except Exception as exc:  # noqa: BLE001
            if cached is None or cached.empty:
                return _av_error(exc)
    if cached is None or cached.empty:
        return f"Keine Kursdaten für {label} ({sym})."
    col = "adj_close" if "adj_close" in cached.columns else cached.columns[0]
    series = pd.to_numeric(cached[col], errors="coerce")
    series.index = pd.to_datetime(series.index)
    stats = _returns(series.sort_index(), ctx.today)
    ctx.add_source(f"Alpha Vantage TIME_SERIES_DAILY_ADJUSTED ({sym}{'' if fresh else ', aktualisiert'})")
    note = "" if fresh else " (aktualisiert)"
    return f"**Kurse {label} ({sym}){note}**\n\n" + _render(stats)


MACRO_SERIES = {
    "treasury_10y": ("fetch_treasury_yield_10y", "US-Staatsanleihe 10J (%)"),
    "treasury_2y": (None, "US-Staatsanleihe 2J (%)"),
    "cpi": ("fetch_cpi", "US-Verbraucherpreisindex"),
    "wti": ("fetch_wti", "WTI-Rohöl (USD)"),
}


def tool_av_macro(args: dict, ctx: ChatContext) -> str:
    av_client, _ = _av_modules()
    from app.core import factor_timing as ft

    wanted = [s.lower() for s in _list_arg(args, "series")] or list(MACRO_SERIES)
    parts = []
    fetched: dict[str, pd.Series] = {}
    for key in wanted:
        if key not in MACRO_SERIES:
            parts.append(f"- Unbekannte Reihe „{key}“ (erlaubt: {', '.join(MACRO_SERIES)}).")
            continue
        fn_name, label = MACRO_SERIES[key]
        try:
            ctx.n_av_calls += 1
            if key == "treasury_2y":
                s = av_client.fetch_treasury_yield("2year", requests_per_minute=ctx.rpm)
            else:
                s = getattr(av_client, fn_name)(requests_per_minute=ctx.rpm)
        except Exception as exc:  # noqa: BLE001
            parts.append(f"- {label}: {_av_error(exc)}")
            continue
        s = pd.to_numeric(s, errors="coerce").dropna()
        if s.empty:
            parts.append(f"- {label}: keine Daten.")
            continue
        fetched[key] = s
        tail = s.tail(6)
        parts.append(f"- **{label}:** aktuell {fmt_de(float(s.iloc[-1]), 2)} (Stand {pd.Timestamp(s.index[-1]).date().isoformat()}); "
                     f"Verlauf: " + ", ".join(f"{pd.Timestamp(i).date().isoformat()}: {fmt_de(float(v), 2)}" for i, v in tail.items()))
    if "treasury_10y" in fetched and "treasury_2y" in fetched:
        spread = ft.spread_from_yields(fetched["treasury_10y"], fetched["treasury_2y"])
        if spread is not None:
            parts.append(f"- **Zinsstruktur 10J–2J:** {fmt_de(spread, 2)} Prozentpunkte")
    if "cpi" in fetched:
        yoy = ft.cpi_yoy(fetched["cpi"])
        if yoy is not None:
            parts.append(f"- **Inflation (CPI, YoY):** {fmt_de(yoy, 1)} %")
    if fetched:
        ctx.add_source("Alpha Vantage Makro (" + ", ".join(fetched) + ")")
    return "**Makro (Alpha Vantage)**\n\n" + "\n".join(parts)


def tool_av_symbol_search(args: dict, ctx: ChatContext) -> str:
    av_client, _ = _av_modules()
    query = _str_arg(args, "query")
    if not query:
        return "Parameter query fehlt."
    try:
        ctx.n_av_calls += 1
        hits = av_client.fetch_symbol_search(query, requests_per_minute=ctx.rpm)
    except Exception as exc:  # noqa: BLE001
        return _av_error(exc)
    if not hits:
        return f"Keine Alpha-Vantage-Symbole für „{query}“."
    ctx.add_source(f"Alpha Vantage SYMBOL_SEARCH ({query})")
    return _table(hits[:10], ["symbol", "name", "type", "region", "currency", "match_score"], labels=False)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def _p(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


_UID = {"type": "string", "description": "uid, Ticker oder Name des Titels (leer = Titel der aktuellen Seite)"}

TOOLS: list[ChatTool] = [
    ChatTool("resolve_company", "Sucht Titel im Universum nach uid, Ticker oder Namensbestandteil; liefert uid, Ticker, Name, Sektor, Region, Zone.",
             _p({"query": {"type": "string", "description": "Ticker, Name oder uid"}}, ["query"]), tool_resolve_company),
    ChatTool("company_snapshot", "Vollständiger Koyfin-Datensatz eines Titels (alle importierten Kennzahlen in Gruppen: Identität, Bewertung, Qualität, Wachstum, Momentum/Technik, Risiko/Liquidität, Bilanz) plus Scores, Faktor-Z, Zone, Filter, Piotroski, Altman, SMA/Trend, Bestand und Modellportfolio-Mitgliedschaft. Erste Wahl für jede Frage zu einem Titel, besonders bei europäischen Titeln.",
             _p({"uid": _UID, "groups": {"type": "array", "items": {"type": "string"}, "description": "optional: nur diese Gruppen (z. B. Bewertung, Qualität)"}}), tool_company_snapshot),
    ChatTool("company_history", "Verlauf eines Titels über die letzten Koyfin-Importe (Score, Zone, Faktor-Z, Momentum, Revisionen, Bewertung …).",
             _p({"uid": _UID, "columns": {"type": "array", "items": {"type": "string"}, "description": "optional: Spalten-IDs"}, "n": {"type": "integer", "description": "Anzahl Snapshots (max. 24)"}}), tool_company_history),
    ChatTool("company_peers", "Ähnliche Titel (Faktorprofil) oder stärkste Titel derselben Industrie/Region.",
             _p({"uid": _UID, "n": {"type": "integer"}, "mode": {"type": "string", "enum": ["similar", "top_score"]}}), tool_company_peers),
    ChatTool("screen_universe", "Screening des Universums mit Filtern (Region, Sektor, Industriegruppe, Zone, Filterstatus, Schwellen je Spalte) und Sortierung; liefert bis zu 50 Titel mit wählbaren Spalten.",
             _p({"region": {"type": "string"}, "sector": {"type": "string"}, "industry_group": {"type": "string"}, "industry": {"type": "string"},
                 "zone": {"type": "string", "enum": list(ZONES)}, "filter_pass": {"type": "boolean"},
                 "thresholds": {"type": "object", "description": "{spalte: {min, max}} z. B. {\"z_value\": {\"min\": 0.5}}"},
                 "sort_by": {"type": "string", "description": "Spalten-ID (Default composite_score)"}, "ascending": {"type": "boolean"},
                 "n": {"type": "integer", "description": "max. 50"}, "columns": {"type": "array", "items": {"type": "string"}}}), tool_screen_universe),
    ChatTool("universe_overview", "Überblick des gesamten Universums: Zonen-/Klassenverteilung, Score-Statistik, Signale, Top-Kandidaten, Sektoren, Regionen.",
             _p({"top_n": {"type": "integer"}}), tool_universe_overview),
    ChatTool("sector_overview", "Sektor-Momentum: Kennzahlen je Sektor (optional je Industrie) inkl. Score-Veränderung.",
             _p({"level": {"type": "string", "enum": ["sector", "industry"]}}), tool_sector_overview),
    ChatTool("changes_since", "Veränderungen seit dem vorherigen Import oder seit ~1 Monat: Score-Deltas, Zonenwechsel, neue SMA-Signale; optional für einen Titel.",
             _p({"basis": {"type": "string", "enum": ["previous", "month"]}, "uid": {"type": "string"}, "n": {"type": "integer"}}), tool_changes_since),
    ChatTool("portfolio_overview", "Aktives (oder gewähltes) Portfolio: Positionen, Zonen, Flags, Konzentration, Sektor-/Regionen-/Faktor-Exposure, Handlungsbedarf.",
             _p({"portfolio_id": {"type": "integer"}}), tool_portfolio_overview),
    ChatTool("model_portfolio_overview", "Modellportfolio (Zielportfolio): Zusammensetzung, Trades, Exposure, Kennzahlen, Abweichungen.",
             _p({}), tool_model_portfolio_overview),
    ChatTool("factor_timing_state", "Factor-Timing-Regime, Makro-Inputs, strategische vs. taktische Faktorgewichte, Regime-Verlauf.",
             _p({}), tool_factor_timing_state),
    ChatTool("risk_overview", "Risikomodul: Ex-post-Rendite/Drawdown, Tracking Error, Risikobeiträge, Szenarien (nur aus Kurs-Cache).",
             _p({"portfolio_id": {"type": "integer"}}), tool_risk_overview),
    ChatTool("analyst_insights", "Gespeicherte KI-Analyst-Ergebnisse eines Titels: Vorschläge, Synthese (Faktor-Zusammenspiel, Zusammenhänge, nicht eingepreiste Themen), Earnings-Call-Zusammenfassung, News-Digest, Ereignisse, verbundene Unternehmen, Trend-Rollen.",
             _p({"uid": _UID}), tool_analyst_insights),
    ChatTool("analyst_run_overview", "Letzte KI-Analyst-Läufe, offene Vorschläge, Themen im Universum, Trends & Profiteure.",
             _p({"n_runs": {"type": "integer"}}), tool_analyst_run_overview),
    ChatTool("methodology", "Methodik des Multi-Faktor-Modells (Zonen, Faktoren, Filter, Signale, Piotroski, Altman, Modellportfolio, Factor Timing, KI-Analyst) und aktuelle Parameter.",
             _p({"topic": {"type": "string", "description": "z. B. zonen, faktoren, filter, signale, piotroski, altman, modellportfolio, factor_timing, ki_analyst, parameter, alle"}}), tool_methodology),
    # Live
    ChatTool("av_news", "Aktuelle News mit Sentiment von Alpha Vantage zu einem Titel oder Thema (topic z. B. technology, energy_transportation, economy_macro, financial_markets, mergers_and_acquisitions).",
             _p({"uid": _UID, "symbol": {"type": "string", "description": "AV-Symbol direkt"}, "topic": {"type": "string"}, "days": {"type": "integer"}, "limit": {"type": "integer"}}), tool_av_news, live=True),
    ChatTool("av_earnings", "Quartals-EPS (berichtet vs. Schätzung, Überraschung) von Alpha Vantage.",
             _p({"uid": _UID, "symbol": {"type": "string"}, "n": {"type": "integer"}}), tool_av_earnings, live=True),
    ChatTool("av_transcript", "Earnings-Call-Transkript (gespeicherte Zusammenfassung des KI-Analysten, sonst Live-Abruf; meist nur US-Titel).",
             _p({"uid": _UID, "quarter": {"type": "string", "description": "z. B. 2026Q2 (leer = letztes berichtetes Quartal)"}}), tool_av_transcript, live=True),
    ChatTool("av_insider", "Insider-Transaktionen (Käufe/Verkäufe von Führungskräften) von Alpha Vantage.",
             _p({"uid": _UID, "symbol": {"type": "string"}, "days": {"type": "integer"}}), tool_av_insider, live=True),
    ChatTool("av_prices", "Kursentwicklung (Renditen 1W/1M/3M/6M/1J/YTD, 52-Wochen-Spanne, SMA-Abstände) aus Kurs-Cache, bei Bedarf Live-Aktualisierung.",
             _p({"uid": _UID, "symbol": {"type": "string"}}), tool_av_prices, live=True),
    ChatTool("av_macro", "Makro-Reihen von Alpha Vantage: US-Zinsen 10J/2J, Zinsstruktur, CPI/Inflation, WTI.",
             _p({"series": {"type": "array", "items": {"type": "string", "enum": list(MACRO_SERIES)}}}), tool_av_macro, live=True),
    ChatTool("av_symbol_search", "Symbolsuche bei Alpha Vantage (auch Titel außerhalb des Universums, Indizes, ETFs).",
             _p({"query": {"type": "string"}}, ["query"]), tool_av_symbol_search, live=True),
]

_BY_NAME = {t.name: t for t in TOOLS}


def tool_names(include_live: bool = True) -> list[str]:
    return [t.name for t in TOOLS if include_live or not t.live]


def tool_specs(include_live: bool = True) -> list[dict]:
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in TOOLS
        if include_live or not t.live
    ]


def run_tool(name: str, args: dict | None, ctx: ChatContext) -> str:
    """Führt ein Werkzeug aus; raised nie. Kürzt die Ausgabe auf
    ``ctx.max_tool_chars`` und protokolliert den Aufruf in ``ctx.trace``."""
    args = dict(args or {}) if isinstance(args, dict) else {}
    tool = _BY_NAME.get(str(name))
    started = time.monotonic()
    ctx.n_tool_calls += 1
    if tool is None:
        out = f"Unbekanntes Werkzeug „{name}“. Verfügbar: {', '.join(tool_names(ctx.av_enabled))}."
    elif tool.live and not ctx.av_enabled:
        out = "Live-Daten sind nicht verfügbar (ALPHAVANTAGE_API_KEY fehlt) — nutze die gespeicherten Daten."
    else:
        try:
            out = tool.fn(args, ctx)
        except Exception as exc:  # noqa: BLE001
            log.warning("Chat-Werkzeug %s fehlgeschlagen: %s", name, exc, exc_info=True)
            out = f"Werkzeug {name} fehlgeschlagen: {type(exc).__name__}: {exc}"
    out = str(out or "")
    truncated = False
    if len(out) > ctx.max_tool_chars:
        out = out[: ctx.max_tool_chars - len(TRUNCATION_NOTE)] + TRUNCATION_NOTE
        truncated = True
    ctx.trace.append({
        "tool": str(name),
        "args": args,
        "chars": len(out),
        "truncated": truncated,
        "seconds": round(time.monotonic() - started, 2),
        "preview": out[:160],
    })
    return out
