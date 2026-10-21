"""Beobachtungsliste des KI-Analysten: Scope → Titel, Priorisierung,
Alpha-Vantage-Symbole.

Scopes (``Settings.analyst_scope``):

- ``portfolio`` — aktives Portfolio ∪ Modellportfolio-Ziel ∪ Zone KANDIDAT
- ``universe`` — alle Titel des Universums
- ``custom`` — ``Settings.analyst_custom_tickers`` (uids oder Ticker)

Die Priorisierung ist ein Round-Robin: Bestandstitel zuerst, dann die am
längsten nicht geprüften, dann nach Composite-Score — so deckt der Agent
bei begrenztem Budget über mehrere Läufe alle Titel ab.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from .. import persistence
from ..diagnostics import SEV_WARNING, Diagnostic
from ..uid import row_by_uid

SCOPES = ("portfolio", "universe", "custom")

_TIER = {"portfolio": 0, "modellportfolio": 1, "kandidat": 2, "custom": 2, "universum": 3}


@dataclass
class WatchItem:
    uid: str
    ticker: str
    name: str
    av_symbol: str | None = None
    reasons: set[str] = field(default_factory=set)
    zone: str | None = None
    composite_score: float | None = None
    held: bool = False
    last_checked_at: datetime | None = None

    @property
    def tier(self) -> int:
        return min((_TIER.get(r, 9) for r in self.reasons), default=9)


def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _txt(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    return s or None


def _item_from_row(row, reason: str) -> WatchItem:
    uid = str(row.get("uid") or row.get("ticker"))
    return WatchItem(
        uid=uid,
        ticker=str(row.get("ticker") or uid),
        name=str(row.get("name") or ""),
        reasons={reason},
        zone=_txt(row.get("zone_v2")),
        composite_score=_num(row.get("composite_score")),
    )


def held_uids(state) -> set[str]:
    """uids des aktiven Portfolios (Status ``ok``); fail-open leer."""
    if state is None:
        return set()
    try:
        pf = state.resolve_portfolio()
    except Exception:  # noqa: BLE001 — kein Portfolio geladen
        return set()
    if pf is None or pf.empty or "uid" not in pf.columns:
        return set()
    if "status" in pf.columns:
        pf = pf[pf["status"] == "ok"]
    return {str(u) for u in pf["uid"].dropna()}


def build_watchlist(
    settings, scored: pd.DataFrame, state=None, scope: str | None = None,
    tickers: list[str] | None = None,
) -> tuple[list[WatchItem], list[Diagnostic]]:
    """Scope → Liste von :class:`WatchItem` (je uid ein Eintrag, Gründe
    gemergt) plus Diagnosen (unbekannte Custom-Ticker)."""
    scope = (scope or getattr(settings, "analyst_scope", "portfolio") or "portfolio").lower()
    if scope not in SCOPES:
        raise ValueError(f"Unbekannter Scope: {scope}")
    diags: list[Diagnostic] = []
    items: dict[str, WatchItem] = {}

    def _add(row, reason: str) -> None:
        item = _item_from_row(row, reason)
        if item.uid in items:
            items[item.uid].reasons.add(reason)
        else:
            items[item.uid] = item

    if scored is None or scored.empty:
        return [], diags
    held = held_uids(state)

    if scope == "universe":
        for _, row in scored.iterrows():
            _add(row, "universum")
    elif scope == "custom":
        entries = tickers if tickers is not None else list(
            getattr(settings, "analyst_custom_tickers", []) or []
        )
        for entry in entries:
            key = str(entry).strip()
            if not key:
                continue
            # Streng: exakte uid, sonst eindeutiger Ticker (ohne
            # ``row_by_uid``-Fallback auf die erste Kollisionszeile).
            row = None
            if "uid" in scored.columns:
                hits = scored[scored["uid"].astype(str) == key]
                row = hits.iloc[0] if len(hits) == 1 else None
            if row is None and "ticker" in scored.columns:
                hits = scored[scored["ticker"].astype(str).str.upper() == key.upper()]
                row = hits.iloc[0] if len(hits) == 1 else None
            if row is None:
                diags.append(
                    Diagnostic(
                        SEV_WARNING,
                        "custom_ticker_unknown",
                        f"Eigener Ticker „{key}“ nicht (eindeutig) im Universum — übersprungen.",
                    )
                )
                continue
            _add(row, "custom")
    else:  # portfolio
        for uid in held:
            row = row_by_uid(scored, uid)
            if row is not None:
                _add(row, "portfolio")
        try:
            mp = persistence.load_model_portfolio()
        except Exception:  # noqa: BLE001
            mp = None
        if mp is not None and not mp.empty and "uid" in mp.columns:
            for uid in mp["uid"].dropna():
                row = row_by_uid(scored, str(uid))
                if row is not None:
                    _add(row, "modellportfolio")
        if "zone_v2" in scored.columns:
            for _, row in scored[scored["zone_v2"] == "KANDIDAT"].iterrows():
                _add(row, "kandidat")

    for item in items.values():
        item.held = item.uid in held
    return list(items.values()), diags


def prioritize(items: list[WatchItem], max_n: int | None = None) -> list[WatchItem]:
    """Tier → ``last_checked_at`` aufsteigend (``None`` zuerst) → Score absteigend."""
    far_past = datetime.min

    def key(it: WatchItem):
        return (
            it.tier,
            it.last_checked_at or far_past,
            -(it.composite_score if it.composite_score is not None else -1e9),
            it.uid,
        )

    ordered = sorted(items, key=key)
    if max_n is not None and max_n >= 0:
        ordered = ordered[: int(max_n)]
    return ordered


def apply_watch_states(items: list[WatchItem], states: dict[str, dict]) -> None:
    """Überträgt ``last_checked_at``/``av_symbol`` aus dem Cursor."""
    for it in items:
        st = states.get(it.uid)
        if not st:
            continue
        it.last_checked_at = st.get("last_checked_at")
        if st.get("av_symbol") and not it.av_symbol:
            it.av_symbol = st["av_symbol"]


def resolve_av_symbols(
    items: list[WatchItem], scored: pd.DataFrame, settings, market_data=None
) -> list[Diagnostic]:
    """Setzt ``av_symbol`` über ``market_data.resolve_symbols`` (gespeichertes
    Mapping → Heuristik → Symbol-Suche); nicht auflösbare Titel erhalten
    eine Warnung und laufen im Quant-only-Pfad weiter."""
    if market_data is None:
        from .. import market_data as md

        market_data = md
    pending = [it for it in items if not it.av_symbol]
    if not pending:
        return []
    resolved, unresolved = market_data.resolve_symbols(
        [it.uid for it in pending], scored, settings, persist=True
    )
    for it in pending:
        hit = resolved.get(it.uid)
        if hit is not None:
            it.av_symbol = hit.av_symbol
    diags = []
    for uid in unresolved:
        ticker = next((it.ticker for it in pending if it.uid == uid), uid)
        diags.append(
            Diagnostic(
                SEV_WARNING,
                "av_symbol_unresolved",
                f"{ticker}: kein Alpha-Vantage-Symbol auflösbar — nur Quant-Daten "
                "(manuell zuordnen: python -m app.tools.risk_report update --map …).",
                uid,
            )
        )
    return diags
