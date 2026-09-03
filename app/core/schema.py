"""Koyfin CSV-Spaltenschema (1:1 zum Daten_Import-Sheet)."""

from __future__ import annotations

KOYFIN_COLUMNS: list[str] = [
    "ticker",
    "name",
    "sector",
    "industry",
    "region",
    "market_cap",
    "last_price",
    "pe",
    "pb",
    "ps",
    "pfcf",
    "ev_ebitda",
    "peg",
    "div_yield",
    "roe",
    "roa",
    "roic",
    "gross_margin",
    "op_margin",
    "debt_equity",
    "int_coverage",
    "current_ratio",
    "rev_cagr_3y",
    "eps_cagr_3y",
    "fcf_cagr_3y",
    "fwd_eps_growth",
    "eps_revisions_3m",
    "ret_1m",
    "ret_3m",
    "ret_6m",
    "ret_12m",
    "beta",
    "volatility_1y",
    "high_52w",
    "low_52w",
    "altman_z",
    "net_income",
    "net_income_prev",
    "ocf",
    "ocf_prev",
    "total_assets",
    "total_assets_prev",
    "total_debt",
    "total_debt_prev",
    "current_assets",
    "current_liab",
    "current_assets_prev",
    "current_liab_prev",
    "shares_out",
    "shares_out_prev",
    "revenue",
    "cogs",
    "revenue_prev",
    "cogs_prev",
    "sma_50",
    "sma_200",
    "export_date",
]

# Optionale Spalten, die der Loader per Header-Erkennung extrahiert (Position
# im Export beliebig). NICHT in KOYFIN_COLUMNS aufnehmen — das Mapping der 57
# Basisspalten ist positional und würde sich sonst verschieben.
# ``fwd_rev_growth``: erwartetes Umsatzwachstum (Koyfin "Est. Revenue CAGR" /
# "Revenue Est. Growth NTM") als zweiter Forward-Growth-Indikator.
# Composite v2 (Spec 1.2): ``ev_ebit`` (dritter Value-Indikator),
# ``net_debt_ebitda`` (Leverage in Quality), ``fcf_yield`` (FCF/EV),
# ``adv_3m`` (Tagesumsatz 3M in Mio EUR, Liquiditätsfilter),
# ``ipo_date`` (Erstnotiz ISO, IPO-Filter — einzige nicht-numerische).
OPTIONAL_COLUMNS: tuple[str, ...] = (
    "sma_20",
    "fwd_rev_growth",
    "ev_ebit",
    "net_debt_ebitda",
    "fcf_yield",
    "adv_3m",
    "ipo_date",
)

# Optionale Spalten, die NICHT numerisch koerziert werden dürfen.
OPTIONAL_TEXT_COLUMNS: frozenset[str] = frozenset({"ipo_date"})

PERCENT_COLUMNS: set[str] = {
    "div_yield",
    "roe",
    "roa",
    "roic",
    "gross_margin",
    "op_margin",
    "rev_cagr_3y",
    "eps_cagr_3y",
    "fcf_cagr_3y",
    "fwd_eps_growth",
    "fwd_rev_growth",
    "eps_revisions_3m",
    "ret_1m",
    "ret_3m",
    "ret_6m",
    "ret_12m",
    "volatility_1y",
}
