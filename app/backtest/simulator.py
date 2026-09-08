"""Simulation der Portfolioentwicklung (Spec 5 und 6).

Je Handelstag: Kurse (EUR) laden, Delistings verkaufen, NAV bewerten; an
Rebalancing-Stichtagen den Snapshot durch die **produktive** Pipeline
schicken und die Trade-Liste zu Schlusskursen mit Kosten ausführen.

Produktive Funktionen werden ausschließlich über ihre Modulattribute
aufgerufen (``scoring.compute_scores``, ``scoring_v2.compute_scores_v2``,
``pc.select_portfolio``, ``pc.compute_weights``, ``pc.apply_te_constraint``,
``pc.build_trade_list``, ``pc.build_model_portfolio`` für ``interim``) —
ein Monkeypatch auf ``app.core`` trifft damit garantiert den Simulator
(Test 12) und es existiert keine Kopie der Modelllogik.
"""

from __future__ import annotations

import hashlib
import logging
import math
import subprocess
import time
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

from app.core import portfolio_construction as pc
from app.core import scoring, scoring_v2
from app.core.config import Settings
from app.core.diagnostics import SEV_ERROR, SEV_WARNING, Diagnostic
from app.core.persistence import settings_hash_v2

from .config import BacktestConfig
from .dataset import BacktestDataset
from .pit_builder import AlphaVantageSnapshotSource, SnapshotSource, benchmark_weights_proxy

log = logging.getLogger(__name__)

MODE_FULL = pc.MODE_FULL
MODE_INTERIM = pc.MODE_INTERIM
RISK_WINDOW = 504


class BacktestAbort(RuntimeError):
    """Abbruch des Laufs (z. B. Cache-Abdeckung < 95 % an einem Stichtag)."""


# ── Kalender (Spec 6) ────────────────────────────────────────────────────


def rebalance_schedule(
    calendar: pd.DatetimeIndex, start: date, end: date, settings: Settings
) -> list[tuple[date, str]]:
    """Letzter Handelstag der Monate aus ``pc_rebalance_months`` (``full``)
    und ``pc_interim_months`` (``interim``) zwischen ``start`` und ``end``.
    Der erste Stichtag ist immer ``full``."""
    days = calendar[(calendar >= pd.Timestamp(start)) & (calendar <= pd.Timestamp(end))]
    if len(days) == 0:
        return []
    full = {int(m) for m in settings.pc_rebalance_months}
    interim = {int(m) for m in settings.pc_interim_months}
    out: list[tuple[date, str]] = []
    series = pd.Series(days, index=days)
    for (_, month), grp in series.groupby([days.year, days.month]):
        last = grp.iloc[-1].date()
        if month in full:
            out.append((last, MODE_FULL))
        elif month in interim:
            out.append((last, MODE_INTERIM))
    out.sort()
    if out:
        out[0] = (out[0][0], MODE_FULL)
    return out


# ── Buchhaltung (Spec 5.1, 5.3, 5.4) ─────────────────────────────────────


@dataclass
class PortfolioState:
    date: date
    cash_eur: float
    positions: dict[str, float]
    nav_eur: float
    weights: dict[str, float]
    last_rebalance_date: date | None
    mode: str


@dataclass
class Ledger:
    """Cash und Positionen (Stück); Ausführung zu Schlusskursen mit Kosten
    je Seite; Käufe auf ganze Aktien abgerundet; Cash nie negativ."""

    cash: float
    cost_rate: float
    positions: dict[str, float] = field(default_factory=dict)
    last_price: dict[str, float] = field(default_factory=dict)
    total_costs: float = 0.0
    # Gewichtsänderungen unter diesem NAV-Anteil werden nicht gehandelt
    # (Rundungsreste ganzer Aktien, Renormierung) — 5 bp des NAV.
    min_trade_share: float = 0.0005

    def update_prices(self, prices: dict[str, float]) -> int:
        """Bekannte Kurse übernehmen; liefert Anzahl gehaltener Titel ohne Kurs."""
        missing = 0
        for uid in self.positions:
            px = prices.get(uid)
            if px is not None and np.isfinite(px) and px > 0:
                self.last_price[uid] = float(px)
            else:
                missing += 1
        return missing

    def position_value(self, uid: str) -> float:
        return self.positions.get(uid, 0.0) * self.last_price.get(uid, 0.0)

    def nav(self) -> float:
        return self.cash + sum(self.position_value(u) for u in self.positions)

    def weights(self) -> dict[str, float]:
        nav = self.nav()
        if nav <= 0:
            return {}
        return {u: self.position_value(u) / nav for u in self.positions if self.positions[u] > 0}

    def sell(self, uid: str, shares: float, price: float, haircut: float = 0.0) -> tuple[float, float]:
        shares = min(shares, self.positions.get(uid, 0.0))
        if shares <= 0 or not np.isfinite(price) or price <= 0:
            return 0.0, 0.0
        gross = shares * price * (1.0 - haircut)
        cost = gross * self.cost_rate
        self.cash += gross - cost
        self.total_costs += cost
        remaining = self.positions[uid] - shares
        if remaining <= 1e-9:
            self.positions.pop(uid, None)
        else:
            self.positions[uid] = remaining
        return gross, cost

    def buy(self, uid: str, shares: float, price: float) -> tuple[float, float]:
        shares = float(math.floor(shares))
        if shares <= 0 or not np.isfinite(price) or price <= 0:
            return 0.0, 0.0
        gross = shares * price
        cost = gross * self.cost_rate
        if gross + cost > self.cash + 1e-9:
            shares = float(math.floor(self.cash / (price * (1.0 + self.cost_rate))))
            if shares <= 0:
                return 0.0, 0.0
            gross = shares * price
            cost = gross * self.cost_rate
        self.cash -= gross + cost
        self.total_costs += cost
        self.positions[uid] = self.positions.get(uid, 0.0) + shares
        self.last_price[uid] = float(price)
        return gross, cost

    def rebalance_to(
        self, target: dict[str, float], prices: dict[str, float], when: date
    ) -> tuple[list[dict], int]:
        """Positionen auf Zielgewichte bringen: erst Verkäufe, dann Käufe
        (skaliert auf verfügbares Cash inkl. Kosten). Liefert Trades und die
        Anzahl nicht ausführbarer Käufe (kein Kurs)."""
        trades: list[dict] = []
        n_failed = 0
        self.update_prices(prices)
        nav = self.nav()
        uids = sorted(set(self.positions) | {u for u, w in target.items() if w > 0})

        def _px(uid: str) -> float:
            px = prices.get(uid)
            return float(px) if px is not None and np.isfinite(px) and px > 0 else float("nan")

        # Verkäufe / Reduktionen.
        for uid in uids:
            w_t = float(target.get(uid, 0.0))
            shares = self.positions.get(uid, 0.0)
            if shares <= 0:
                continue
            px = _px(uid)
            if not np.isfinite(px):
                if w_t <= 0:
                    n_failed += 1
                continue
            target_shares = math.floor(w_t * nav / px) if w_t > 0 else 0
            if target_shares < shares:
                to_sell = shares - target_shares
                if target_shares > 0 and to_sell * px < self.min_trade_share * nav:
                    continue
                gross, cost = self.sell(uid, to_sell, px)
                trades.append(
                    {"date": when, "uid": uid, "action": "VERKAUF" if target_shares == 0 else "REDUZIEREN",
                     "shares": -to_sell, "price_eur": px, "notional_eur": -gross, "cost_eur": cost}
                )
        # Käufe / Aufstockungen, skaliert auf Cash.
        wants: dict[str, float] = {}
        for uid in uids:
            w_t = float(target.get(uid, 0.0))
            if w_t <= 0:
                continue
            px = _px(uid)
            if not np.isfinite(px):
                if self.positions.get(uid, 0.0) <= 0:
                    n_failed += 1
                continue
            cur_val = self.positions.get(uid, 0.0) * px
            need = w_t * nav - cur_val
            if need > 0 and (cur_val <= 0 or need >= self.min_trade_share * nav):
                wants[uid] = need
        total = sum(wants.values())
        available = self.cash / (1.0 + self.cost_rate)
        scale = min(1.0, available / total) if total > 0 else 0.0
        for uid in sorted(wants):
            px = _px(uid)
            shares = math.floor(wants[uid] * scale / px)
            if shares <= 0:
                continue
            new = uid not in self.positions
            gross, cost = self.buy(uid, shares, px)
            if gross > 0:
                trades.append(
                    {"date": when, "uid": uid, "action": "KAUF" if new else "AUFSTOCKEN",
                     "shares": shares, "price_eur": px, "notional_eur": gross, "cost_eur": cost}
                )
        return trades, n_failed


# ── Ergebnis ─────────────────────────────────────────────────────────────


@dataclass
class BacktestResult:
    config: BacktestConfig
    settings_hash: str
    nav_daily: pd.DataFrame
    holdings: pd.DataFrame
    trades: pd.DataFrame
    rebalances: pd.DataFrame
    factor_exposures: pd.DataFrame
    sector_weights: pd.DataFrame
    contributions: pd.Series
    model_diagnostics: pd.DataFrame
    meta: dict = field(default_factory=dict)

    def nav_hash(self) -> str:
        """SHA-256 der gerundeten NAV-Reihe (Reproduzierbarkeit, Test 15)."""
        vals = np.round(self.nav_daily["nav"].to_numpy(dtype=float), 6)
        return hashlib.sha256(vals.tobytes()).hexdigest()


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=False
        )
        return out.stdout.strip() or "unbekannt"
    except Exception:  # noqa: BLE001
        return "unbekannt"


def risk_cache_from_backtest(
    dataset: BacktestDataset, t: date, uids: list[str], window: int = RISK_WINDOW
) -> dict | None:
    """EUR-Renditepanel der letzten ``window`` Handelstage bis einschließlich
    ``t`` — strikt ohne Daten nach ``t`` (Spec 5.2)."""
    ts = pd.Timestamp(t)
    cols = [u for u in uids if u in dataset.adj_close.columns]
    if not cols:
        return None
    px = dataset.adj_close_eur.loc[:ts, cols].tail(window + 1)
    bm = dataset.benchmark_eur().loc[:ts].tail(window + 1)
    if len(px) < 31:
        return None
    returns = px.pct_change(fill_method=None).iloc[1:]
    bm_ret = bm.pct_change(fill_method=None).iloc[1:]
    return {"returns": returns, "bm_returns": bm_ret}


# ── Simulator ────────────────────────────────────────────────────────────


class Simulator:
    def __init__(
        self,
        config: BacktestConfig,
        dataset: BacktestDataset,
        source: SnapshotSource | None = None,
        settings: Settings | None = None,
        progress=None,
    ) -> None:
        self.config = config
        self.dataset = dataset
        self.settings = settings or config.settings()
        # Der Benchmark-Proxy wird in ``full`` explizit übergeben; für den
        # ``interim``-Pfad (produktiver Orchestrator) darf keine DB-gestützte
        # statische Quelle greifen.
        self.settings.pc_benchmark_source = "universe"
        self.source = source or AlphaVantageSnapshotSource(dataset, config)
        self.progress = progress or (lambda msg: None)
        self._model_diags: list[dict] = []

    # ── Modellaufruf an einem Stichtag ───────────────────────────────────

    def score_snapshot(self, t: date) -> tuple[pd.DataFrame, list[Diagnostic]]:
        snapshot = self.source.build_snapshot(t)
        stats = self.source.universe_stats(t)
        base = stats.get("after_suffix") or stats.get("listed_raw") or 0
        missing = stats.get("missing_cache", 0)
        if base and missing / base > self.config.bt_missing_cache_max_share:
            raise BacktestAbort(
                f"Stichtag {t.isoformat()}: Kurscache fehlt für {missing} von {base} "
                f"Titeln ({missing / base * 100:.1f} %) — Abbruch (fetch ausführen)."
            )
        scored = scoring.compute_scores(snapshot, self.settings)
        scored, diags = scoring_v2.compute_scores_v2(
            scored, self.settings, overrides=None, snapshot_date=t
        )
        return scored, list(diags)

    def _target_from_trades(self, trades: pd.DataFrame, current: dict[str, float]) -> dict[str, float]:
        """Ausführbare Zielgewichte aus der Trade-Liste: verschobene Trades
        behalten das aktuelle Gewicht, HALTEN bleibt unverändert."""
        if trades is None or trades.empty:
            return dict(current)
        out: dict[str, float] = {}
        has_budget = "weight_effective_after_budget" in trades.columns
        for _, r in trades.iterrows():
            uid = str(r["uid"])
            action = r["action"]
            if action == pc.ACTION_DEFERRED or action == pc.ACTION_HOLD:
                w = float(r["weight_current"])
            elif has_budget and pd.notna(r.get("weight_effective_after_budget")):
                w = float(r["weight_effective_after_budget"])
            else:
                w = float(r["weight_target"])
            if w > 0:
                out[uid] = w
        total = sum(out.values())
        if total > 0:
            out = {u: w / total for u, w in out.items()}
        return out

    def build_target(
        self, t: date, mode: str, current: dict[str, float], scored: pd.DataFrame
    ) -> dict:
        """Produktive Portfoliokonstruktion; liefert Zielgewichte, Trade-Liste,
        Diagnosen und Kennzahlen des Stichtags."""
        settings = self.settings
        diags: list[Diagnostic] = []
        te: float | None = None
        te_coverage: float | None = None
        uni = scored.copy()
        uni.index = pd.Index(uni["uid"].astype(str), name="_uid")
        uni = uni[~uni.index.duplicated(keep="first")]

        if mode == MODE_INTERIM and current:
            res = pc.build_model_portfolio(
                scored, settings, current, mode=MODE_INTERIM, snapshot_date=t,
                overrides=None, risk_cache=None,
            )
            diags.extend(res["diagnostics"])
            trades = res["trades"].trades
            turnover = res["trades"].turnover_oneway
            n_deferred = res["trades"].n_deferred
            portfolio = res["portfolio"].set_index("uid") if not res["portfolio"].empty else pd.DataFrame()
            target = self._target_from_trades(trades, current)
            return {
                "target": target, "trades": trades, "portfolio": portfolio, "diagnostics": diags,
                "te": None, "te_coverage": None, "turnover": turnover, "n_deferred": n_deferred,
                "n_candidates": int((uni["zone_v2"] == scoring_v2.ZONE_CANDIDATE).sum()),
            }

        benchmark = benchmark_weights_proxy(scored, self.config)
        selection = pc.select_portfolio(
            uni, current, benchmark, settings, overrides=None, snapshot_date=t
        )
        diags.extend(selection.diagnostics)
        portfolio = selection.portfolio
        weight_input = portfolio
        if self.config.bt_equal_weight and not portfolio.empty:
            # Sensitivität S3: Score-Tilt und Vola neutralisieren → 1/N mit
            # Cap/Floor aus der produktiven Funktion.
            weight_input = portfolio.copy()
            weight_input["composite_z"] = np.nan
            weight_input["volatility_1y"] = 1.0
        weights = pc.compute_weights(weight_input, settings, diagnostics=diags)
        if not self.config.bt_skip_te_constraint and len(weights):
            cache = risk_cache_from_backtest(self.dataset, t, list(weights.index))
            weights, te, details = pc.apply_te_constraint(weights, settings, cache)
            diags.extend(details["diagnostics"])
            te_coverage = details.get("coverage")
        target_frame = portfolio.loc[[u for u in weights.index if u in portfolio.index]].copy()
        target_frame["weight_effective"] = weights.reindex(target_frame.index)
        target_frame["weight_model"] = target_frame["weight_effective"]
        target_frame["reason"] = [f"zone_{z}" for z in target_frame.get("zone_v2", "")]
        exit_reasons = {
            str(r["uid"]): str(r["reason"]) for _, r in selection.exits.iterrows()
        }
        trade_list = pc.build_trade_list(
            target_frame, current, settings, mode, universe=uni, exit_reasons=exit_reasons
        )
        diags.extend(trade_list.diagnostics)
        target = self._target_from_trades(trade_list.trades, current)
        if not trade_list.trades.empty and not target:
            target = {u: float(w) for u, w in weights.items() if w > 0}
        return {
            "target": target, "trades": trade_list.trades, "portfolio": target_frame,
            "diagnostics": diags, "te": te, "te_coverage": te_coverage,
            "turnover": trade_list.turnover_oneway, "n_deferred": trade_list.n_deferred,
            "n_candidates": int((uni["zone_v2"] == scoring_v2.ZONE_CANDIDATE).sum()),
        }

    # ── Hauptschleife ────────────────────────────────────────────────────

    def run(self) -> BacktestResult:
        cfg = self.config
        ds = self.dataset
        started = time.time()
        end = cfg.bt_end or ds.calendar[-1].date()
        days = ds.trading_days(cfg.bt_start, end)
        if len(days) == 0:
            raise BacktestAbort("Kein Handelstag im Simulationszeitraum")
        schedule = dict(rebalance_schedule(ds.calendar, cfg.bt_start, end, self.settings))
        if not schedule:
            raise BacktestAbort("Kein Rebalancing-Stichtag im Zeitraum")

        adj = ds.adj_close_eur
        col_idx = {c: i for i, c in enumerate(adj.columns)}
        arr = adj.to_numpy(dtype=float)
        row_pos = {ts: i for i, ts in enumerate(adj.index)}
        bm_eur = ds.benchmark_eur()
        bm0 = float(bm_eur.loc[:pd.Timestamp(days[0])].dropna().iloc[-1])

        ledger = Ledger(cash=float(cfg.bt_initial_capital), cost_rate=cfg.cost_rate())
        daily_rows: list[dict] = []
        trade_rows: list[dict] = []
        holding_rows: list[dict] = []
        rebal_rows: list[dict] = []
        expo_rows: list[dict] = []
        sector_rows: list[dict] = []
        contrib: dict[str, float] = {}
        missing_since = 0
        delist_since = 0
        prev_weights: dict[str, float] = {}
        prev_bm = bm0
        last_rebalance: date | None = None
        mode = MODE_FULL
        daily_yield = cfg.bt_cash_yield / 252.0

        for ts in days:
            t = ts.date()
            row = arr[row_pos[ts]]
            prices = {u: row[col_idx[u]] for u in ledger.positions if u in col_idx}
            missing_today = ledger.update_prices(prices)
            missing_since += missing_today
            bm_now = float(bm_eur.loc[ts]) if pd.notna(bm_eur.loc[ts]) else prev_bm
            bm_ret = bm_now / prev_bm - 1.0 if prev_bm else 0.0

            # Beitrag zur aktiven Rendite (Vortagesgewichte).
            if prev_weights:
                for uid, w in prev_weights.items():
                    p_prev = prev_prices.get(uid)
                    p_now = ledger.last_price.get(uid)
                    if p_prev and p_now and np.isfinite(p_prev) and np.isfinite(p_now) and p_prev > 0:
                        contrib[uid] = contrib.get(uid, 0.0) + w * ((p_now / p_prev - 1.0) - bm_ret)

            # Delistings (Spec 3.4): Titel ohne Kurs, dessen Reihe endet.
            for uid in list(ledger.positions):
                px = prices.get(uid)
                if px is not None and np.isfinite(px):
                    continue
                last_day = ds.last_price_date(uid)
                delisted_at = ds.delisting_date(uid)
                gap = int(((adj.index > (last_day if last_day is not None else ts)) & (adj.index <= ts)).sum())
                if (delisted_at is not None and delisted_at <= ts) or gap > cfg.bt_missing_price_max_days:
                    price = ledger.last_price.get(uid, float("nan"))
                    shares = ledger.positions[uid]
                    gross, cost = ledger.sell(uid, shares, price, haircut=cfg.bt_delisting_haircut)
                    delist_since += 1
                    trade_rows.append(
                        {"date": t, "uid": uid, "action": "DELISTING", "shares": -shares,
                         "price_eur": price * (1.0 - cfg.bt_delisting_haircut), "notional_eur": -gross,
                         "cost_eur": cost, "reason": "delisting"}
                    )

            if daily_yield:
                ledger.cash *= 1.0 + daily_yield

            if t in schedule:
                mode = schedule[t]
                self.progress(f"{t.isoformat()} {mode}")
                current = ledger.weights()
                scored, model_diags = self.score_snapshot(t)
                result = self.build_target(t, mode, current, scored)
                diags = model_diags + result["diagnostics"]
                target = result["target"]
                px_all = {u: row[col_idx[u]] for u in target if u in col_idx}
                px_all.update(prices)
                trades, n_failed = ledger.rebalance_to(target, px_all, t)
                for tr in trades:
                    tr["reason"] = mode
                trade_rows.extend(trades)
                last_rebalance = t
                nav_after = ledger.nav()

                # Diagnosen des Stichtags protokollieren.
                codes = {d.code for d in diags}
                for d in diags:
                    self._model_diags.append(
                        {"date": t, "severity": d.severity, "code": d.code, "uid": d.uid,
                         "message": d.message}
                    )
                uni_stats = self.source.universe_stats(t)
                unknown_share = (
                    float((scored["sector"] == "Unknown").mean()) if len(scored) else 0.0
                )
                rebal_rows.append(
                    {
                        "date": t, "mode": mode, "n_universe": int(len(scored)),
                        "n_eligible": int(scored["filter_pass"].sum()) if "filter_pass" in scored else 0,
                        "n_candidates": result["n_candidates"],
                        "n_portfolio": int(len(target)),
                        "n_positions": int(len(ledger.positions)),
                        "te_ex_ante": result["te"], "te_coverage": result["te_coverage"],
                        "te_unmet": "te_constraint_unmet" in codes,
                        "te_skipped": any(c.startswith("te_skipped") for c in codes),
                        "fill_zone_used": "fill_zone_used" in codes,
                        "below_min": "portfolio_below_min" in codes,
                        "turnover_oneway": result["turnover"],
                        "n_trades": int(len(trades)), "n_deferred": int(result["n_deferred"]),
                        "n_buy_failed": int(n_failed),
                        "n_missing_prices": int(missing_since), "n_delistings": int(delist_since),
                        "unknown_sector_share": unknown_share,
                        "n_errors": sum(1 for d in diags if d.severity == SEV_ERROR),
                        "n_warnings": sum(1 for d in diags if d.severity == SEV_WARNING),
                        "cash_share": ledger.cash / nav_after if nav_after else np.nan,
                        **{f"uni_{k}": v for k, v in uni_stats.items()},
                    }
                )
                missing_since = 0
                delist_since = 0

                # Bestände und Exposures nach Ausführung.
                weights_now = ledger.weights()
                uni = scored.set_index(scored["uid"].astype(str))
                for uid, shares in sorted(ledger.positions.items()):
                    price = ledger.last_price.get(uid, np.nan)
                    holding_rows.append(
                        {
                            "date": t, "uid": uid, "shares": shares, "price_eur": price,
                            "value_eur": shares * price, "weight": weights_now.get(uid, 0.0),
                            "composite_z": uni["composite_z"].get(uid, np.nan) if "composite_z" in uni else np.nan,
                            "zone_v2": uni["zone_v2"].get(uid, "") if "zone_v2" in uni else "",
                            "sector": uni["sector"].get(uid, "") if "sector" in uni else "",
                        }
                    )
                for factor in scoring_v2.V2_FACTOR_NAMES:
                    col = f"z_{factor}"
                    if col not in uni.columns:
                        continue
                    z = pd.to_numeric(uni[col], errors="coerce")
                    w = pd.Series(weights_now, dtype=float)
                    w = w[w.index.isin(z.index)]
                    pf_val = float((z.reindex(w.index) * w).sum() / w.sum()) if len(w) and w.sum() > 0 else np.nan
                    expo_rows.append(
                        {"date": t, "factor": factor, "portfolio": pf_val,
                         "universe": float(z.mean()) if z.notna().any() else np.nan}
                    )
                bm_sector = benchmark_weights_proxy(scored, cfg).sector or {}
                pf_sector: dict[str, float] = {}
                for uid, w in weights_now.items():
                    sec = str(uni["sector"].get(uid, "Unknown")) if "sector" in uni else "Unknown"
                    pf_sector[sec] = pf_sector.get(sec, 0.0) + w
                for sec in sorted(set(pf_sector) | set(bm_sector)):
                    sector_rows.append(
                        {"date": t, "sector": sec, "portfolio": pf_sector.get(sec, 0.0),
                         "benchmark": float(bm_sector.get(sec, 0.0))}
                    )

            nav = ledger.nav()
            daily_rows.append(
                {
                    "date": t, "nav": nav, "cash": ledger.cash,
                    "n_positions": int(len(ledger.positions)),
                    "benchmark_nav": cfg.bt_initial_capital * bm_now / bm0,
                    "missing_prices": int(missing_today),
                    "mode": mode if t in schedule else "",
                    "rebalance": t in schedule,
                }
            )
            prev_weights = ledger.weights()
            prev_prices = dict(ledger.last_price)
            prev_bm = bm_now

        nav_daily = pd.DataFrame(daily_rows)
        nav_daily["date"] = pd.to_datetime(nav_daily["date"])
        nav_daily = nav_daily.set_index("date")
        trades = pd.DataFrame(
            trade_rows, columns=["date", "uid", "action", "shares", "price_eur", "notional_eur",
                                 "cost_eur", "reason"]
        )
        meta = {
            "config_name": cfg.name,
            "config_hash": cfg.config_hash(),
            "settings_hash": settings_hash_v2(self.settings),
            "git_commit": _git_commit(),
            "cache_asof": (ds.cache_asof.isoformat() if isinstance(ds.cache_asof, datetime) else str(ds.cache_asof or "")),
            "start": days[0].date().isoformat(),
            "end": days[-1].date().isoformat(),
            "n_rebalances": len(rebal_rows),
            "n_trading_days": int(len(days)),
            "total_costs_eur": ledger.total_costs,
            "elapsed_s": round(time.time() - started, 1),
            "run_at": datetime.now().isoformat(timespec="seconds"),
        }
        return BacktestResult(
            config=cfg,
            settings_hash=meta["settings_hash"],
            nav_daily=nav_daily,
            holdings=pd.DataFrame(holding_rows),
            trades=trades,
            rebalances=pd.DataFrame(rebal_rows),
            factor_exposures=pd.DataFrame(expo_rows, columns=["date", "factor", "portfolio", "universe"]),
            sector_weights=pd.DataFrame(sector_rows, columns=["date", "sector", "portfolio", "benchmark"]),
            contributions=pd.Series(contrib, dtype=float).sort_values(ascending=False),
            model_diagnostics=pd.DataFrame(
                self._model_diags, columns=["date", "severity", "code", "uid", "message"]
            ),
            meta=meta,
        )


def run_backtest(
    config: BacktestConfig,
    dataset: BacktestDataset,
    source: SnapshotSource | None = None,
    settings: Settings | None = None,
    progress=None,
) -> BacktestResult:
    return Simulator(config, dataset, source=source, settings=settings, progress=progress).run()


# ── Replay gespeicherter Zielgewichte (Paper-Portfolio, Spec 11) ─────────


def replay_targets(
    prices_eur: pd.DataFrame,
    benchmark_eur: pd.Series,
    targets: dict[date, dict[str, float]],
    initial_capital: float,
    cost_rate: float,
    end: date | None = None,
) -> pd.DataFrame:
    """Tägliche Bewertung eines Portfolios, das an den Stichtagen ``targets``
    auf die gegebenen Gewichte gedreht wird (dieselbe Buchhaltung wie der
    Backtest: Kosten, ganze Aktien, Kursfortschreibung).

    Liefert ``date, nav, benchmark_nav, cash, n_positions, missing_prices``.
    """
    if not targets:
        return pd.DataFrame(columns=["nav", "benchmark_nav", "cash", "n_positions", "missing_prices"])
    dates = sorted(targets)
    start = pd.Timestamp(dates[0])
    cal = prices_eur.index[(prices_eur.index >= start)]
    if end is not None:
        cal = cal[cal <= pd.Timestamp(end)]
    ledger = Ledger(cash=float(initial_capital), cost_rate=cost_rate)
    schedule = {pd.Timestamp(d): w for d, w in targets.items()}
    bm = benchmark_eur.reindex(cal).ffill()
    bm0 = float(bm.dropna().iloc[0]) if bm.notna().any() else 1.0
    rows: list[dict] = []
    pending: dict[str, float] | None = None
    for ts in cal:
        row = prices_eur.loc[ts]
        prices = {u: float(row[u]) for u in row.index if pd.notna(row[u])}
        missing = ledger.update_prices(prices)
        # Ein Stichtag ohne Handelstag im Kalender wird am nächsten Tag ausgeführt.
        due = [d for d in schedule if d <= ts]
        if due:
            latest = max(due)
            pending = schedule.pop(latest)
            for d in due:
                schedule.pop(d, None)
        if pending is not None:
            ledger.rebalance_to(pending, prices, ts.date())
            pending = None
        rows.append(
            {
                "date": ts.date(), "nav": ledger.nav(), "cash": ledger.cash,
                "n_positions": int(len(ledger.positions)),
                "benchmark_nav": float(initial_capital) * (float(bm.loc[ts]) / bm0 if pd.notna(bm.loc[ts]) else np.nan),
                "missing_prices": int(missing),
            }
        )
    out = pd.DataFrame(rows)
    out["date"] = pd.to_datetime(out["date"])
    return out.set_index("date")
