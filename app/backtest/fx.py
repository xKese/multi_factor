"""USD/EUR-Umrechnung (Spec 4.2): ``fx(d)`` ist der Schlusskurs am letzten
Handelstag ≤ d; Kursreihen werden tagesweise umgerechnet, damit Renditen die
EUR-Perspektive abbilden."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd


class FxSeries:
    """USD→EUR-Tagesreihe (Wert = EUR je USD)."""

    def __init__(self, series: pd.Series) -> None:
        s = pd.to_numeric(series, errors="coerce").dropna().sort_index()
        s.index = pd.DatetimeIndex(s.index)
        if s.empty:
            raise ValueError("FX-Reihe ist leer")
        self.series = s

    @classmethod
    def constant(cls, value: float, start: date, end: date) -> "FxSeries":
        idx = pd.bdate_range(start, end)
        return cls(pd.Series(float(value), index=idx))

    def rate(self, d: date | pd.Timestamp) -> float:
        """Letzter verfügbarer Kurs ≤ d (kein Blick nach vorn)."""
        ts = pd.Timestamp(d)
        pos = self.series.index.searchsorted(ts, side="right") - 1
        if pos < 0:
            return float("nan")
        return float(self.series.iloc[pos])

    def aligned(self, calendar: pd.DatetimeIndex) -> pd.Series:
        """FX je Kalendertag, forward-filled (nur aus der Vergangenheit)."""
        return self.series.reindex(self.series.index.union(calendar)).ffill().reindex(calendar)

    def to_eur(self, usd: pd.Series | pd.DataFrame):
        """USD-Reihe/Panel (DatetimeIndex) tagesweise nach EUR."""
        fx = self.aligned(pd.DatetimeIndex(usd.index))
        if isinstance(usd, pd.DataFrame):
            return usd.mul(fx.to_numpy(), axis=0)
        return usd * fx.to_numpy()

    def amount_to_eur(self, usd: float, d: date) -> float:
        r = self.rate(d)
        if np.isnan(r) or usd is None:
            return float("nan")
        return float(usd) * r
