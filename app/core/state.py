"""Prozess-lokaler In-Memory-Store für Universum, Settings und M&S-Portfolio."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

import pandas as pd

from .config import Settings
from .scoring import compute_scores


@dataclass
class AppState:
    settings: Settings = field(default_factory=Settings)
    raw: pd.DataFrame = field(default_factory=pd.DataFrame)
    scored: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Fallback-Liste, solange kein Portfolio hochgeladen/persistiert wurde.
    # Gepflegt wird das Portfolio über den Koyfin-Watchlist-Upload auf
    # /portfolios (→ set_ms_portfolio + save_ms_portfolio).
    ms_portfolio: list[str] = field(
        default_factory=lambda: [
            "AIR", "ALV", "GOOGL", "AMZN", "AAPL", "SAN", "BRKB", "DHR",
            "AIR.PA", "NVDA", "MSFT",
        ]
    )
    ms_portfolio_names: dict[str, str] = field(default_factory=dict)
    # Importierte Portfoliogewichte (Dezimalanteile, Summe 1,0). Leer, wenn
    # der Upload keine Gewichtsspalte hatte → ``portfolio_weights()`` fällt
    # auf Gleichgewichtung zurück.
    ms_portfolio_weights: dict[str, float] = field(default_factory=dict)
    ms_portfolio_imported_at: object | None = None
    _lock: Lock = field(default_factory=Lock, repr=False)

    def recompute(self) -> None:
        with self._lock:
            if self.raw.empty:
                self.scored = pd.DataFrame()
            else:
                self.scored = compute_scores(self.raw, self.settings)

    def set_raw(self, df: pd.DataFrame) -> None:
        self.raw = df
        self.recompute()

    def set_ms_portfolio(self, df: pd.DataFrame, imported_at=None) -> None:
        """Ersetzt das M&S-Portfolio aus einem Upload-/DB-Frame.

        ``df`` braucht ``ticker`` (+ optional ``name``, ``weight``,
        ``imported_at``). Kein Recompute nötig — ``scored`` ist
        portfoliounabhängig.
        """
        self.ms_portfolio = [str(t) for t in df["ticker"].tolist()]
        names = (
            df["name"].fillna("")
            if "name" in df.columns
            else pd.Series("", index=df.index)
        )
        self.ms_portfolio_names = {
            str(t): str(n) for t, n in zip(df["ticker"], names)
        }
        self.ms_portfolio_weights = {}
        if "weight" in df.columns:
            weights = pd.to_numeric(df["weight"], errors="coerce")
            if weights.notna().any() and float(weights.fillna(0).sum()) > 0:
                self.ms_portfolio_weights = {
                    str(t): float(w)
                    for t, w in zip(df["ticker"], weights)
                    if pd.notna(w) and w > 0
                }
        if imported_at is not None:
            self.ms_portfolio_imported_at = imported_at
        elif "imported_at" in df.columns and len(df):
            self.ms_portfolio_imported_at = df["imported_at"].iloc[0]

    def portfolio_weights(self) -> dict[str, float]:
        """Portfoliogewichte als Dezimalanteile (Summe 1,0).

        Importierte Gewichte haben Vorrang; ohne Gewichtsspalte im Upload
        wird gleichgewichtet (1/N über alle Positionen). Auf Summe 1,0
        renormalisiert, damit Rundungsreste im Import nicht durchschlagen.
        """
        if self.ms_portfolio_weights:
            total = sum(self.ms_portfolio_weights.values())
            if total > 0:
                return {
                    t: w / total for t, w in self.ms_portfolio_weights.items()
                }
        if not self.ms_portfolio:
            return {}
        n = len(self.ms_portfolio)
        return {t: 1.0 / n for t in self.ms_portfolio}

    def load_from_db(self) -> bool:
        from .persistence import load_ms_portfolio, load_settings, load_universe

        stored_settings = load_settings()
        if stored_settings is not None:
            self.settings = stored_settings

        portfolio = load_ms_portfolio()
        if portfolio is not None and not portfolio.empty:
            self.set_ms_portfolio(portfolio)

        df = load_universe()
        if df is None or df.empty:
            return False
        self.set_raw(df)
        return True


STATE = AppState()
