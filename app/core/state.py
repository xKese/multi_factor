"""Prozess-lokaler In-Memory-Store für Universum, Settings und Portfolios."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

import pandas as pd

from .config import Settings
from .data_loader import load_from_excel
from .scoring import compute_scores


EXCEL_PATH = Path(__file__).resolve().parent.parent.parent / "M&S_Multi-Faktor-Model.xlsx"


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

    def load_excel(self) -> None:
        if EXCEL_PATH.exists():
            self.raw = load_from_excel(EXCEL_PATH)
            self.recompute()

    def set_raw(self, df: pd.DataFrame) -> None:
        self.raw = df
        self.recompute()


STATE = AppState()
STATE.load_excel()
