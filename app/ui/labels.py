"""Zentrales Mapping technische Spalten-IDs → menschenlesbare Labels."""

from __future__ import annotations


INDICATOR_LABELS: dict[str, str] = {
    # Basis
    "ticker": "Ticker",
    "name": "Name",
    "sector": "Sektor",
    "industry": "Industrie",
    "region": "Region",
    "market_cap": "Market Cap",
    "last_price": "Letzter Kurs",
    "export_date": "Stand",
    # Value
    "pe": "P/E",
    "pb": "P/B",
    "ps": "P/S",
    "pfcf": "P/FCF",
    "ev_ebitda": "EV/EBITDA",
    "peg": "PEG",
    "div_yield": "Dividendenrendite",
    # Quality
    "roe": "ROE",
    "roa": "ROA",
    "roic": "ROIC",
    "gross_margin": "Bruttomarge",
    "op_margin": "Operative Marge",
    "debt_equity": "Debt / Equity",
    "int_coverage": "Interest Coverage",
    "current_ratio": "Current Ratio",
    "piotroski": "Piotroski F-Score",
    "altman_z": "Altman Z-Score",
    "ocf_ni": "OCF / Net Income",
    # Growth
    "rev_cagr_3y": "Umsatz-CAGR 3J",
    "eps_cagr_3y": "EPS-CAGR 3J",
    "fcf_cagr_3y": "FCF-CAGR 3J",
    "fwd_eps_growth": "Forward EPS Growth",
    "eps_revisions_3m": "EPS-Revisions 3M",
    # Momentum
    "ret_1m": "Return 1M",
    "ret_3m": "Return 3M",
    "ret_6m": "Return 6M",
    "ret_12m": "Return 12M",
    # Risk / Low Vol
    "beta": "Beta",
    "volatility_1y": "Volatilität 1J",
    "range_52w": "52W-Range",
    "high_52w": "52W-Hoch",
    "low_52w": "52W-Tief",
    # Scores
    "value_score": "Value-Score",
    "quality_score": "Quality-Score",
    "growth_score": "Growth-Score",
    "momentum_score": "Momentum-Score",
    "lowvol_score": "Low-Vol-Score",
    "total_score": "Gesamt-Score",
    "classification": "Klassifikation",
    "filter_ok": "Filter",
    "recommendation": "Empfehlung",
    "sma_signal": "SMA-Signal",
    "sma_200_distance": "Abstand SMA-200 (%)",
    "sma_50_distance": "Abstand SMA-50 (%)",
    "sma_gap": "SMA-50 vs SMA-200 (%)",
    "direction": "Richtung",
    "sma_20": "SMA-20",
    "sma_50": "SMA-50",
    "sma_200": "SMA-200",
    "sma_20_distance": "Abstand SMA-20 (%)",
    "mom_12_1": "Momentum 12-1 (%)",
    "dist_52w_high": "Abstand 52W-Hoch (%)",
    "trend_phase": "Trend-Phase",
    # Factor-IDs (in Einstellungen)
    "value": "Value",
    "quality": "Quality",
    "growth": "Growth",
    "momentum": "Momentum",
    "lowvol": "Low Volatility",
}


FACTOR_GROUP_LABELS: dict[str, str] = {
    "value": "Value-Indikatoren",
    "quality": "Quality-Indikatoren",
    "growth": "Growth-Indikatoren",
    "momentum": "Momentum-Indikatoren",
    "lowvol": "Low-Vol-Indikatoren",
}


def label_for(key: str) -> str:
    """Liefert den menschenlesbaren Titel für eine Spalten-ID.

    Unbekannte Keys werden mit Titel-Case-Ersatz (``debt_equity`` →
    ``"Debt Equity"``) zurückgegeben, damit nie rohe Snake-Case-IDs in
    der UI landen.
    """
    if key in INDICATOR_LABELS:
        return INDICATOR_LABELS[key]
    return key.replace("_", " ").title()
