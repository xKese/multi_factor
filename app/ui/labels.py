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
    "fwd_rev_growth": "Forward Umsatzwachstum",
    "rev_growth_1y": "Umsatzwachstum 1J",
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
    "recommendation_overlay": "Empfehlung inkl. Momentum",
    "data_coverage": "Daten-Abdeckung",
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
    "investment": "Investment",
    # Composite v2
    "composite_score": "Composite-Score (v2)",
    "composite_raw": "Composite (roh)",
    "composite_z": "Composite-Z",
    "composite_pct": "Composite-Perzentil",
    "classification_v2": "Klasse (v2)",
    "zone_v2": "Zone",
    "data_coverage_v2": "Abdeckung (v2)",
    "z_value": "Z Value",
    "z_quality": "Z Quality",
    "z_momentum": "Z Momentum",
    "z_investment": "Z Investment",
    "cov_value": "Abdeckung Value",
    "cov_quality": "Abdeckung Quality",
    "cov_momentum": "Abdeckung Momentum",
    "cov_investment": "Abdeckung Investment",
    "filter_pass": "Universum-Filter",
    "filter_reasons": "Filter-Gründe",
    "trend_warning": "Trend-Warnung",
    # v2-Indikatoren (abgeleitet bzw. optionale Koyfin-Spalten)
    "gp_ta": "Gross Profit / Assets",
    "accruals": "Accruals",
    "asset_growth": "Asset Growth",
    "share_issuance": "Aktienausgabe (Netto)",
    "mom_12_1_adj": "Momentum 12-1 (vol-adj.)",
    "fcf_yield": "FCF-Rendite",
    "fcf_yield_v2": "FCF-Rendite (v2)",
    "fcf_yield_calc": "FCF-Rendite (aus P/FCF)",
    "fcf_yield_source": "FCF-Quelle",
    "ev_ebit": "EV/EBIT",
    "net_debt_ebitda": "Net Debt / EBITDA",
    "debt_ebit": "Debt / EBIT",
    "adv_3m": "Ø Handelsvolumen 3M (Mio.)",
    "ipo_date": "IPO-Datum",
    # Portfoliokonstruktion (Modellportfolio)
    "weight_current": "Gewicht aktuell",
    "weight_model": "Gewicht Modell",
    "weight_effective": "Gewicht effektiv",
    "weight_target": "Gewicht Ziel",
    "delta_w": "Δ Gewicht",
    "cte": "cTE-Beitrag",
    "action": "Aktion",
    "reason": "Begründung",
    "uid": "UID",
    "override": "Override",
    "override_id": "Override-ID",
}


FACTOR_GROUP_LABELS: dict[str, str] = {
    "value": "Value-Indikatoren",
    "quality": "Quality-Indikatoren",
    "growth": "Growth-Indikatoren",
    "momentum": "Momentum-Indikatoren",
    "lowvol": "Low-Vol-Indikatoren",
    "investment": "Investment-Indikatoren",
}

# Präfixe für dynamisch erzeugte v2-Spalten (je Indikator): das Label wird
# aus dem Basis-Indikator abgeleitet, damit auch künftige Indikatoren nie
# als rohe Snake-Case-IDs in der UI landen.
_PREFIX_LABELS: tuple[tuple[str, str], ...] = (
    ("z_", "Z {}"),
    ("cov_", "Abdeckung {}"),
    ("neut_level_", "Neutralisierung {}"),
)


def label_for(key: str) -> str:
    """Liefert den menschenlesbaren Titel für eine Spalten-ID.

    Unbekannte Keys werden mit Titel-Case-Ersatz (``debt_equity`` →
    ``"Debt Equity"``) zurückgegeben, damit nie rohe Snake-Case-IDs in
    der UI landen.
    """
    if key in INDICATOR_LABELS:
        return INDICATOR_LABELS[key]
    for prefix, template in _PREFIX_LABELS:
        if key.startswith(prefix):
            base = key.removeprefix(prefix)
            return template.format(INDICATOR_LABELS.get(base, base.replace("_", " ").title()))
    return key.replace("_", " ").title()
