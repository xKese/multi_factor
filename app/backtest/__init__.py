"""Backtest-Engine (Stufe 1: Alpha Vantage, US-Universum) und Paper-Portfolio.

Leitprinzip: kein Nachbau des Modells. Die Engine erzeugt je Stichtag einen
Koyfin-kompatiblen Punkt-in-Zeit-Snapshot (``pit_builder``) und schickt ihn
unverändert durch die produktive Pipeline aus ``app/core`` (Scoring v1 für
Piotroski, Composite v2, Universumsfilter, Selektion, Gewichtung,
TE-Kontrolle, Trade-Liste). Alle Daten kommen aus einem lokalen Cache
(``av_client``); während der Simulation findet kein Netzwerkzugriff statt.

Module:

- ``config``            BacktestConfig + YAML-Loader + Sensitivitätsvarianten
- ``av_client``         Alpha-Vantage-Zugriff, Token-Bucket-Limiter, Parquet-Cache
- ``fx``                USD/EUR-Umrechnung
- ``dataset``           In-Memory-Datensatz aus dem Cache (Kurse, Fundamentals, …)
- ``universe``          historisches Universum aus LISTING_STATUS
- ``pit_builder``       Koyfin-kompatibler Snapshot je Stichtag (SnapshotSource)
- ``simulator``         Portfolioentwicklung, Trades, Kosten, Delistings
- ``metrics``           Kennzahlen
- ``factor_regression`` Fama-French-5 + Momentum-Exposures
- ``report``            Markdown-Report + CSV-Exporte (Vorbehaltsblock zuerst)
- ``paper``             Live-Paper-Portfolio des produktiven Modells
- ``cli``               ``python -m app.backtest …``
"""
