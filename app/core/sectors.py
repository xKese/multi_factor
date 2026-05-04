"""ETF-Zuordnung fuer die Sektor-Momentum-Matrix (TAA Conviction)."""

from __future__ import annotations


SECTOR_ETFS: dict[str, str] = {
    "IXP": "Communication",
    "RXI": "Consumer Discretionary",
    "KXI": "Consumer Staples",
    "IXC": "Energy",
    "IXG": "Financial",
    "IXJ": "Health Care",
    "EXI": "Industrials",
    "MXI": "Materials",
    "REET": "Real Estate",
    "IXN": "Technology",
    "JXI": "Utilities",
}

INDUSTRY_ETFS: dict[str, str] = {
    "MOO": "Agribusiness",
    "JETS": "Airlines",
    "IBB": "Biotechnology",
    "PBW": "Clean Energy",
    "GDX": "Gold Miners",
    "ITB": "Home Construction",
    "KIE": "Insurance",
    "FDN": "Internet",
    "XME": "Metals & Mining",
    "AMLP": "MLPs (Infrastruktur)",
    "XOP": "Oil and Gas Exploration",
    "OIH": "Oil Services",
    "VNQ": "Real Estate",
    "KRE": "Regional Banking",
    "XRT": "Retail",
    "SMH": "Semiconductor",
    "TAN": "Solar",
    "IGV": "Tech-Software",
    "PHO": "Water Resources",
}

GROUP_SECTOR = "sector"
GROUP_INDUSTRY = "industry"


def group_for(ticker: str) -> str | None:
    if ticker in SECTOR_ETFS:
        return GROUP_SECTOR
    if ticker in INDUSTRY_ETFS:
        return GROUP_INDUSTRY
    return None


def display_name(ticker: str) -> str | None:
    return SECTOR_ETFS.get(ticker) or INDUSTRY_ETFS.get(ticker)
