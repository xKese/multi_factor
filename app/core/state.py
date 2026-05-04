"""Prozess-lokaler In-Memory-Store für Universum, Settings und Portfolios."""

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
    ms_portfolio: list[str] = field(
        default_factory=lambda: [
            "AIR", "ALV", "GOOGL", "AMZN", "AAPL", "SAN", "BRKB", "DHR",
            "AIR.PA", "NVDA", "MSFT",
        ]
    )
    my_portfolio: list[str] = field(
        default_factory=lambda: ["ASML", "ATI", "AER", "ALV", "GOOGL", "AMZN", "AAPL"]
    )
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

    def load_from_db(self) -> bool:
        from .persistence import load_settings, load_universe

        stored_settings = load_settings()
        if stored_settings is not None:
            self.settings = stored_settings

        df = load_universe()
        if df is None or df.empty:
            return False
        self.set_raw(df)
        return True


STATE = AppState()
