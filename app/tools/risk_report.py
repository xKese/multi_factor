"""CLI des Risiko-&-Benchmark-Moduls (Scheduler-tauglich).

Usage:
    python -m app.tools.risk_report update [--asof YYYY-MM-DD] [--map SAP=SAP.DEX:EUR ...]
    python -m app.tools.risk_report report [--asof YYYY-MM-DD] [--only "COVID,Zinsjahr2022"]
                                           [--variante fest|buyhold] [--out DIR]

``update`` lädt/cached nur (Kurse, FX, Makro — Alpha Vantage, Key aus
``ALPHAVANTAGE_API_KEY``); ``report`` rechnet strikt aus dem Cache und
schreibt den Markdown-Report. Exit-Codes: 0 Erfolg, 1 fehlende Daten
(kein Portfolio, kein Cache, Update komplett fehlgeschlagen),
2 Argument-/Konfigurationsfehler.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from app.core import av_store, market_data
from app.core.risk_report import compute_risk_report, write_report
from app.core.state import STATE


def _parse_asof(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def _apply_manual_mappings(entries: list[str]) -> list[str]:
    """``TICKER=AV_SYMBOL[:WÄHRUNG]`` als bestätigte Mappings speichern.

    Als Ticker ist auch eine uid erlaubt (``SAN~sanofi=SAN.PA:EUR``) — nur
    der Symbolteil vor dem ``~`` wird groß geschrieben, der Namens-Slug
    bleibt unverändert (er ist der Mapping-Schlüssel)."""

    from app.core.uid import UID_SEPARATOR

    errors: list[str] = []
    for entry in entries:
        if "=" not in entry:
            errors.append(entry)
            continue
        ticker, _, target = entry.partition("=")
        symbol, _, currency = target.partition(":")
        if not ticker.strip() or not symbol.strip():
            errors.append(entry)
            continue
        key = ticker.strip()
        if UID_SEPARATOR in key:
            base, _, slug = key.partition(UID_SEPARATOR)
            key = f"{base.upper()}{UID_SEPARATOR}{slug}"
        else:
            key = key.upper()
        av_store.save_av_mapping(
            key,
            symbol.strip(),
            currency.strip().upper() or "USD",
            confirmed=True,
        )
        print(f"Mapping gespeichert: {key} → {symbol.strip()}")
    return errors


def _load_state() -> tuple[list[str], dict[str, float]] | None:
    STATE.load_from_db()
    if not STATE.ms_portfolio:
        print(
            "Kein M&S-Portfolio in der Datenbank — bitte zuerst auf "
            "/portfolios eine Watchlist importieren.",
            file=sys.stderr,
        )
        return None
    # Positionen als uids (bei Ticker-Kollisionen eindeutig) — konsistent
    # zu den Keys von portfolio_weights().
    resolved = STATE.resolve_portfolio()
    tickers = resolved["uid"].astype(str).tolist()
    return tickers, STATE.portfolio_weights()


def _universe():
    if STATE.scored is not None and not STATE.scored.empty:
        return STATE.scored
    return STATE.raw


def _cmd_update(args: argparse.Namespace) -> int:
    loaded = _load_state()
    if loaded is None:
        return 1
    tickers, _ = loaded

    summary = market_data.update_cache(
        tickers, _universe(), STATE.settings, asof=args.asof_date
    )
    print(
        f"Aktualisiert: {len(summary['aktualisiert'])} Symbole · "
        f"übersprungen (Cache aktuell): {len(summary['uebersprungen'])} · "
        f"API-Calls: {summary['api_calls']}"
    )
    for ticker in summary["nicht_aufloesbar"]:
        print(
            f"  [warn] Ticker {ticker} nicht auflösbar — im Report unter "
            "Datenqualität; manuell zuordnen mit --map.",
            file=sys.stderr,
        )
    for fehler in summary["fehler"]:
        print(f"  [warn] {fehler}", file=sys.stderr)

    if not summary["aktualisiert"] and not summary["uebersprungen"]:
        print("Update komplett fehlgeschlagen.", file=sys.stderr)
        return 1
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    loaded = _load_state()
    if loaded is None:
        return 1
    tickers, weights = loaded
    settings = STATE.settings

    only = None
    if args.only:
        only = [s.strip() for s in args.only.split(",") if s.strip()]
        unknown = [s for s in only if s not in settings.risk_scenario_windows]
        if unknown:
            print(
                f"Unbekannte Szenarien: {', '.join(unknown)}. Verfügbar: "
                f"{', '.join(settings.risk_scenario_windows)}",
                file=sys.stderr,
            )
            return 2

    asof = args.asof_date or date.today()
    try:
        res = compute_risk_report(
            tickers,
            weights,
            settings,
            STATE.scored,
            asof,
            variant=args.variante,
            only=only,
        )
    except ValueError as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1

    out_dir = args.out or settings.risk_report_dir
    path = write_report(res, out_dir)
    quality = res["quality"]
    print(f"Report geschrieben: {path}")
    if quality.unresolved or quality.missing_cache:
        print(
            f"  [warn] {len(quality.unresolved) + len(quality.missing_cache)} "
            "Titel ohne Kursdaten — siehe Abschnitt Datenqualität.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.risk_report", description=__doc__
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_update = sub.add_parser(
        "update", help="Kurs-/FX-/Makro-Cache aktualisieren (nur laden)"
    )
    p_update.add_argument("--asof", help="Stichtag YYYY-MM-DD (Default: heute)")
    p_update.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="TICKER=AV_SYMBOL[:WÄHRUNG]",
        help="Manuelles Ticker-Mapping speichern (z. B. SAP=SAP.DEX:EUR)",
    )
    p_update.set_defaults(func=_cmd_update)

    p_report = sub.add_parser(
        "report", help="Report zum Stichtag aus dem Cache erzeugen"
    )
    p_report.add_argument("--asof", help="Stichtag YYYY-MM-DD (Default: heute)")
    p_report.add_argument(
        "--only",
        help='Szenarien selektiv rechnen, z. B. --only "COVID,Zinsjahr2022"',
    )
    p_report.add_argument(
        "--variante",
        choices=["fest", "buyhold"],
        default="fest",
        help="Portfoliovariante (Default: fest = täglich rebalanced)",
    )
    p_report.add_argument("--out", help="Zielverzeichnis (Default: Settings)")
    p_report.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="TICKER=AV_SYMBOL[:WÄHRUNG]",
        help="Manuelles Ticker-Mapping speichern (z. B. SAP=SAP.DEX:EUR)",
    )
    p_report.set_defaults(func=_cmd_report)

    args = parser.parse_args(argv)

    # Warnungen der Core-Module (fail-open-Pfade) im Scheduler sichtbar machen.
    logging.basicConfig(
        level=logging.WARNING, format="[%(levelname)s] %(name)s: %(message)s"
    )

    try:
        args.asof_date = _parse_asof(args.asof)
    except ValueError:
        print(
            f"Ungültiges Datum: {args.asof!r} (erwartet YYYY-MM-DD)",
            file=sys.stderr,
        )
        return 2

    bad = _apply_manual_mappings(args.map)
    if bad:
        print(
            f"Ungültige --map-Angaben: {', '.join(bad)} "
            "(erwartet TICKER=AV_SYMBOL[:WÄHRUNG])",
            file=sys.stderr,
        )
        return 2

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
