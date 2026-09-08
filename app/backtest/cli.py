"""CLI der Backtest-Engine (Spec 13).

    python -m app.backtest fetch  --config configs/backtest_us_default.yaml [--force] [--tickers A,B,C]
    python -m app.backtest run    --config configs/backtest_us_default.yaml [--start] [--end]
                                  [--no-sensitivities] [--variant S3]
    python -m app.backtest report --run <run_id | Verzeichnis>
    python -m app.backtest paper  update [--no-fetch] | report
    python -m app.backtest snapshot --date YYYY-MM-DD --out snapshot.csv [--config …]

Exit-Codes wie beim Modellportfolio-CLI: 0 ohne Fehler, 1 bei Warnungen
(z. B. Diagnose-Fehler an Stichtagen, fehlende Faktordaten), 2 bei Fehlern
(Abbruch, Konfigurationsfehler). ``run`` bricht ab, wenn der Cache an einem
Stichtag für mehr als 5 % des Universums fehlt.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

from . import av_client as avc
from .config import BacktestConfig, SENSITIVITY_VARIANTS, VARIANT_BASE, load_config

log = logging.getLogger(__name__)


def _load(args) -> BacktestConfig:
    if getattr(args, "config", None):
        return load_config(args.config)
    return BacktestConfig()


def _client(cfg: BacktestConfig) -> avc.BacktestAVClient:
    cache = avc.BacktestCache(cfg.cache_dir)
    return avc.BacktestAVClient(
        cache,
        requests_per_minute=cfg.av_requests_per_minute,
        ttl_prices=cfg.av_cache_ttl_days_prices,
        ttl_fundamentals=cfg.av_cache_ttl_days_fundamentals,
        ttl_listing=cfg.av_cache_ttl_days_listing,
        ttl_no_data=cfg.av_no_data_ttl_days,
        retry_attempts=cfg.av_retry_attempts,
    )


class _Progress:
    def __init__(self, total: int, label: str) -> None:
        self.total = total
        self.label = label
        self.n = 0
        self.t0 = time.time()

    def step(self, item: str = "") -> None:
        self.n += 1
        if self.n % 25 == 0 or self.n == self.total:
            elapsed = time.time() - self.t0
            rate = self.n / elapsed if elapsed > 0 else 0.0
            eta = (self.total - self.n) / rate if rate > 0 else 0.0
            print(f"  {self.label}: {self.n}/{self.total} ({eta / 60:.0f} min verbleibend) {item}",
                  flush=True)


# ── fetch ────────────────────────────────────────────────────────────────


def _cmd_fetch(args) -> int:
    from .simulator import rebalance_schedule
    from .universe import union_tickers

    cfg = _load(args)
    if not avc.api_key():
        print("ALPHAVANTAGE_API_KEY ist nicht gesetzt.", file=sys.stderr)
        return 2
    client = _client(cfg)
    force = bool(args.force)
    errors: list[str] = []

    print("1/5 Benchmark, FX, Faktordateien …", flush=True)
    bm = client.fetch_prices(cfg.bt_benchmark_ticker, force=force)
    if bm is None or bm.empty:
        print(f"Benchmark {cfg.bt_benchmark_ticker} nicht ladbar.", file=sys.stderr)
        return 2
    if cfg.bt_benchmark_ticker_eur:
        client.fetch_prices(cfg.bt_benchmark_ticker_eur, force=force)
    fx = client.fetch_fx("USD", "EUR", force=force)
    if fx is None or fx.empty:
        print("FX-Reihe USD/EUR nicht ladbar.", file=sys.stderr)
        return 2
    for name, url in (("ff5", cfg.ff_url_5factors), ("mom", cfg.ff_url_momentum)):
        try:
            client.fetch_factor_file(name, url, force=force)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Faktordatei {name}: {exc}")

    end = cfg.bt_end or bm.index[-1].date()
    settings = cfg.settings()
    schedule = rebalance_schedule(bm.index, cfg.bt_start, end, settings)
    print(f"2/5 LISTING_STATUS für {len(schedule)} Stichtage …", flush=True)
    listings: dict[date, object] = {}
    prog = _Progress(len(schedule) + 1, "Listings")
    for d, _mode in schedule:
        try:
            df = client.fetch_listing_status(d, "active", force=force)
            if df is not None:
                listings[d] = df
        except avc.BacktestAVError as exc:
            errors.append(f"LISTING_STATUS {d}: {exc}")
        prog.step(d.isoformat())
    try:
        client.fetch_listing_status(None, "delisted", force=force)
    except avc.BacktestAVError as exc:
        errors.append(f"LISTING_STATUS delisted: {exc}")
    prog.step("delisted")

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = union_tickers(listings, cfg)
    print(f"3/5 OVERVIEW für {len(tickers)} Ticker …", flush=True)
    prog = _Progress(len(tickers), "Overview")
    keep: list[str] = []
    for t in tickers:
        try:
            ov = client.fetch_overview(t, force=force)
        except avc.BacktestAVError as exc:
            errors.append(f"OVERVIEW {t}: {exc}")
            ov = None
        if ov is None or ov.empty:
            keep.append(t)  # fehlende OVERVIEW: Titel bleibt (Spec 3.2)
        else:
            row = ov.iloc[0]
            asset = str(row.get("asset_type") or "").lower()
            country = str(row.get("country") or "").upper()
            if (not asset or asset == "common stock") and (not country or country in ("USA", "US", "UNITED STATES")):
                keep.append(t)
        prog.step(t)

    print(f"4/5 Kurse für {len(keep)} Ticker …", flush=True)
    prog = _Progress(len(keep), "Kurse")
    for t in keep:
        try:
            client.fetch_prices(t, force=force)
        except avc.BacktestAVError as exc:
            errors.append(f"Kurse {t}: {exc}")
        prog.step(t)

    print(f"5/5 Fundamentals (3 Endpunkte) für {len(keep)} Ticker …", flush=True)
    prog = _Progress(len(keep) * 3, "Fundamentals")
    for t in keep:
        for fn in (client.fetch_income_statement, client.fetch_balance_sheet, client.fetch_cash_flow):
            try:
                fn(t, force=force)
            except avc.BacktestAVError as exc:
                errors.append(f"{fn.__name__} {t}: {exc}")
            prog.step(t)

    print(f"Fertig — API-Aufrufe in diesem Lauf: {client.api_calls}, Fehler: {len(errors)}")
    for e in errors[:50]:
        print("  " + e)
    return 1 if errors else 0


# ── run / report ─────────────────────────────────────────────────────────


def _cmd_run(args) -> int:
    from .dataset import BacktestDataset
    from .report import save_run
    from .runner import run_full
    from .simulator import BacktestAbort

    cfg = _load(args)
    if args.start:
        cfg.bt_start = date.fromisoformat(args.start)
    if args.end:
        cfg.bt_end = date.fromisoformat(args.end)
    if args.variant and args.variant not in SENSITIVITY_VARIANTS and args.variant != VARIANT_BASE:
        print(f"Unbekannte Variante {args.variant!r}; bekannt: {', '.join(SENSITIVITY_VARIANTS)}",
              file=sys.stderr)
        return 2
    cache = avc.BacktestCache(cfg.cache_dir)
    try:
        dataset = BacktestDataset.from_cache(cache, cfg)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    t0 = time.time()
    try:
        run = run_full(
            cfg, dataset,
            include_sensitivities=not args.no_sensitivities,
            only_variant=args.variant,
            progress=lambda m: print("  " + m, flush=True),
        )
    except BacktestAbort as exc:
        print(f"Abbruch: {exc}", file=sys.stderr)
        return 2
    path = save_run(run, cfg.report_dir)
    base = run["base"]
    m = run["metrics"]
    print(f"Report geschrieben: {path} ({time.time() - t0:.0f} s)")
    print(
        "Rendite p. a.: " + _pct(m["portfolio"]["ann_return"])
        + " · Benchmark: " + _pct(m["benchmark"]["ann_return"])
        + " · TE: " + _pct(m["active"]["tracking_error"])
        + " · IR: " + _num(m["active"]["information_ratio"])
    )
    n_err = int(base.rebalances["n_errors"].sum()) if not base.rebalances.empty else 0
    if run.get("regression") is None or n_err:
        return 1
    return 0


def _cmd_report(args) -> int:
    from .report import build_markdown, load_run

    run_dir = Path(args.run)
    if not run_dir.exists():
        cfg = _load(args)
        run_dir = Path(cfg.report_dir) / args.run
    if not (run_dir / "run.json").exists():
        print(f"Lauf nicht gefunden: {run_dir}", file=sys.stderr)
        return 2
    run = load_run(run_dir)
    md = build_markdown(run)
    out = run_dir.parent / f"{run['run_id']}.md"
    out.write_text(md, encoding="utf-8")
    print(f"Report geschrieben: {out}")
    return 0


# ── paper ────────────────────────────────────────────────────────────────


def _cmd_paper(args) -> int:
    from . import paper

    cfg = _load(args)
    if args.paper_command == "update":
        try:
            summary = paper.update_paper(config=cfg, fetch=not args.no_fetch)
        except (ValueError, RuntimeError) as exc:
            print(f"Paper-Update fehlgeschlagen: {exc}", file=sys.stderr)
            return 2
        print(f"paper_nav_daily aktualisiert: {summary.get('rows', 0)} Zeilen "
              f"(Snapshots: {summary.get('n_snapshots')}, Start: {summary.get('start', '–')})")
        if summary.get("note"):
            print(summary["note"])
        if summary.get("unresolved"):
            print("Nicht aufgelöste Ticker: " + ", ".join(summary["unresolved"]))
        if summary.get("missing_cache"):
            print("Ohne Kurscache: " + ", ".join(summary["missing_cache"]))
        return 1 if (summary.get("unresolved") or summary.get("missing_cache")) else 0
    md = paper.build_paper_report(rf=cfg.bt_rf)
    out = Path(cfg.report_dir) / f"paper_{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"Report geschrieben: {out}")
    return 0


# ── snapshot ─────────────────────────────────────────────────────────────


def _cmd_snapshot(args) -> int:
    from .dataset import BacktestDataset
    from .pit_builder import AlphaVantageSnapshotSource, write_snapshot_csv

    cfg = _load(args)
    d = date.fromisoformat(args.date)
    cache = avc.BacktestCache(cfg.cache_dir)
    try:
        dataset = BacktestDataset.from_cache(cache, cfg)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    source = AlphaVantageSnapshotSource(dataset, cfg)
    snap = source.build_snapshot(d)
    write_snapshot_csv(snap, args.out)
    stats = source.universe_stats(d)
    print(f"Snapshot {d.isoformat()}: {len(snap)} Titel → {args.out}")
    print("Universum: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    if args.score:
        from app.core.scoring import compute_scores
        from app.core.scoring_v2 import compute_scores_v2

        settings = cfg.settings()
        scored = compute_scores(snap, settings)
        scored, diags = compute_scores_v2(scored, settings, snapshot_date=d)
        n_pass = int(scored["filter_pass"].sum())
        cov = scored["data_coverage_v2"]
        print(f"Filter bestanden: {n_pass} · data_coverage_v2 ≥ 0,6: "
              f"{_pct(float((cov >= 0.6).mean()))} · Zonen: {scored['zone_v2'].value_counts().to_dict()}")
    return 0


# ── main ─────────────────────────────────────────────────────────────────


def _pct(v) -> str:
    try:
        return f"{float(v) * 100:.1f} %".replace(".", ",")
    except (TypeError, ValueError):
        return "–"


def _num(v) -> str:
    try:
        return f"{float(v):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return "–"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="[warn] %(message)s")
    parser = argparse.ArgumentParser(prog="python -m app.backtest", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("fetch", help="Cache füllen (Alpha Vantage)")
    p.add_argument("--config")
    p.add_argument("--force", action="store_true")
    p.add_argument("--tickers", help="Kommagetrennte Ticker statt Listing-Vereinigung")
    p.set_defaults(func=_cmd_fetch)

    p = sub.add_parser("run", help="Backtest ausführen")
    p.add_argument("--config")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--no-sensitivities", action="store_true")
    p.add_argument("--variant", help="Nur diese Variante (plus Basisfall), z. B. S3_equal_weight")
    p.set_defaults(func=_cmd_run)

    p = sub.add_parser("report", help="Report aus gespeichertem Lauf erzeugen")
    p.add_argument("--run", required=True, help="run_id oder Lauf-Verzeichnis")
    p.add_argument("--config")
    p.set_defaults(func=_cmd_report)

    p = sub.add_parser("paper", help="Paper-Portfolio")
    p.add_argument("paper_command", choices=["update", "report"])
    p.add_argument("--config")
    p.add_argument("--no-fetch", action="store_true", help="Kurscache nicht aktualisieren")
    p.set_defaults(func=_cmd_paper)

    p = sub.add_parser("snapshot", help="PIT-Snapshot als CSV (Debug)")
    p.add_argument("--date", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--config")
    p.add_argument("--score", action="store_true", help="zusätzlich durch das Scoring schicken")
    p.set_defaults(func=_cmd_snapshot)

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
