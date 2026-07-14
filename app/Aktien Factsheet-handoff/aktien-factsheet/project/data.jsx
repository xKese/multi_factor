// Sample stock data — A-rated tech names with plausible numbers consistent with
// the Meeder & Seifer multi-factor scoring schema (factor weights:
// Value 25%, Quality 27%, Growth 15%, Momentum 18%, Low Vol 15%).

const TICKERS = {
  ASML: {
    ticker: "ASML",
    name: "ASML Holding N.V.",
    sector: "Information Technology",
    industry: "Semiconductor Equipment",
    region: "Niederlande",
    last_price: 712.40,
    market_cap: 281_500_000_000,
    pe: 31.2, pb: 18.4, ps: 9.1, pfcf: 38.6, ev_ebitda: 24.7, peg: 1.9, div_yield: 0.92,
    roe: 51.3, roa: 18.9, roic: 42.7, gross_margin: 51.8, op_margin: 32.1,
    debt_equity: 0.31, int_coverage: 28.4, current_ratio: 1.62,
    rev_cagr_3y: 18.4, eps_cagr_3y: 21.7, fcf_cagr_3y: 16.3, fwd_eps_growth: 24.1,
    eps_revisions_3m: 4.2,
    ret_1m: 3.8, ret_3m: 12.4, ret_6m: 21.7, ret_12m: 38.5,
    beta: 1.18, volatility_1y: 28.4, range_52w_low: 545.20, range_52w_high: 781.90,
    piotroski: 8, altman_z: 7.2, sma_50: 681.30, sma_200: 612.40,
    sma_signal: "GOLDEN CROSS",
    value_score: 58.4, quality_score: 92.1, growth_score: 86.7, momentum_score: 81.3, lowvol_score: 64.8,
    total_score: 78.6,
    classification: "B+ – Sehr Gut",
    recommendation: "BUY",
    filter_ok: "JA",
    sector_rank: 3,
    sector_total: 84,
    industry_rank: 1,
    industry_total: 12,
    thesis: "Monopolistische Stellung in EUV-Lithografie, hoher Auftragsbestand und steigende Margen tragen einen langfristig überdurchschnittlichen Score.",
  },
  NVDA: {
    ticker: "NVDA",
    name: "NVIDIA Corporation",
    sector: "Information Technology",
    industry: "Semiconductors",
    region: "USA",
    last_price: 142.85,
    market_cap: 3_510_000_000_000,
    pe: 52.4, pb: 41.2, ps: 28.7, pfcf: 61.3, ev_ebitda: 41.8, peg: 1.4, div_yield: 0.03,
    roe: 119.2, roa: 58.4, roic: 87.3, gross_margin: 75.8, op_margin: 62.1,
    debt_equity: 0.18, int_coverage: 87.1, current_ratio: 4.18,
    rev_cagr_3y: 64.8, eps_cagr_3y: 91.4, fcf_cagr_3y: 78.2, fwd_eps_growth: 41.6,
    eps_revisions_3m: 8.7,
    ret_1m: 7.2, ret_3m: 18.6, ret_6m: 34.1, ret_12m: 184.3,
    beta: 1.71, volatility_1y: 51.3, range_52w_low: 78.40, range_52w_high: 152.80,
    piotroski: 9, altman_z: 12.4, sma_50: 134.80, sma_200: 118.40,
    sma_signal: "GOLDEN CROSS",
    value_score: 24.7, quality_score: 98.2, growth_score: 99.1, momentum_score: 96.4, lowvol_score: 18.3,
    total_score: 84.2,
    classification: "A – Exzellent",
    recommendation: "STRONG BUY",
    filter_ok: "JA",
    sector_rank: 1,
    sector_total: 84,
    industry_rank: 1,
    industry_total: 28,
    thesis: "Strukturelle KI-Nachfrage und Margen-Hebel rechtfertigen Premium-Bewertung; Volatilität bleibt erhöht, Quality- und Growth-Profil sind erstklassig.",
  },
  MSFT: {
    ticker: "MSFT",
    name: "Microsoft Corporation",
    sector: "Information Technology",
    industry: "Systems Software",
    region: "USA",
    last_price: 428.60,
    market_cap: 3_186_000_000_000,
    pe: 36.4, pb: 11.8, ps: 13.2, pfcf: 41.7, ev_ebitda: 24.3, peg: 2.3, div_yield: 0.71,
    roe: 38.7, roa: 18.4, roic: 28.6, gross_margin: 69.8, op_margin: 44.7,
    debt_equity: 0.24, int_coverage: 41.2, current_ratio: 1.34,
    rev_cagr_3y: 14.7, eps_cagr_3y: 16.8, fcf_cagr_3y: 13.4, fwd_eps_growth: 13.8,
    eps_revisions_3m: 1.8,
    ret_1m: 1.4, ret_3m: 4.7, ret_6m: 8.3, ret_12m: 18.7,
    beta: 0.92, volatility_1y: 21.6, range_52w_low: 358.40, range_52w_high: 468.30,
    piotroski: 7, altman_z: 6.8, sma_50: 421.80, sma_200: 408.40,
    sma_signal: "Kurs > SMA-200",
    value_score: 41.3, quality_score: 88.4, growth_score: 71.2, momentum_score: 58.7, lowvol_score: 78.4,
    total_score: 71.4,
    classification: "B+ – Sehr Gut",
    recommendation: "BUY",
    filter_ok: "JA",
    sector_rank: 4,
    sector_total: 84,
    industry_rank: 2,
    industry_total: 18,
    thesis: "Cloud- und KI-Integration treibt stabiles zweistelliges Wachstum bei niedrigem Risikoprofil — defensives Quality-Compounding.",
  },
  TSM: {
    ticker: "TSM",
    name: "Taiwan Semiconductor MFG",
    sector: "Information Technology",
    industry: "Semiconductors",
    region: "Taiwan",
    last_price: 184.20,
    market_cap: 956_000_000_000,
    pe: 27.8, pb: 7.4, ps: 11.1, pfcf: 31.4, ev_ebitda: 16.8, peg: 1.1, div_yield: 1.23,
    roe: 28.4, roa: 14.7, roic: 24.6, gross_margin: 53.7, op_margin: 42.1,
    debt_equity: 0.27, int_coverage: 38.4, current_ratio: 2.31,
    rev_cagr_3y: 21.4, eps_cagr_3y: 24.8, fcf_cagr_3y: 18.7, fwd_eps_growth: 28.4,
    eps_revisions_3m: 5.3,
    ret_1m: 4.7, ret_3m: 14.8, ret_6m: 28.4, ret_12m: 91.7,
    beta: 1.24, volatility_1y: 32.7, range_52w_low: 96.40, range_52w_high: 198.40,
    piotroski: 8, altman_z: 8.4, sma_50: 178.40, sma_200: 158.20,
    sma_signal: "GOLDEN CROSS",
    value_score: 67.3, quality_score: 87.4, growth_score: 91.2, momentum_score: 89.7, lowvol_score: 51.4,
    total_score: 81.7,
    classification: "A – Exzellent",
    recommendation: "STRONG BUY",
    filter_ok: "JA",
    sector_rank: 2,
    sector_total: 84,
    industry_rank: 2,
    industry_total: 28,
    thesis: "Dominanter Foundry-Anbieter mit struktureller KI-Nachfrage — günstigere Bewertung als US-Peers bei vergleichbarem Wachstum.",
  },
};

// Indicator definitions — label, key, format, percentile (out of 100, sector-relative)
const INDICATORS = {
  Value: {
    weight: 25,
    color: "#5b8def",
    items: [
      { label: "P/B", key: "pb", fmt: "num2", lower_better: true },
      { label: "P/E", key: "pe", fmt: "num2", lower_better: true },
      { label: "P/FCF", key: "pfcf", fmt: "num2", lower_better: true },
      { label: "EV/EBITDA", key: "ev_ebitda", fmt: "num2", lower_better: true },
      { label: "P/S", key: "ps", fmt: "num2", lower_better: true },
      { label: "PEG", key: "peg", fmt: "num2", lower_better: true },
      { label: "Dividendenrendite", key: "div_yield", fmt: "pct" },
    ],
  },
  Quality: {
    weight: 27,
    color: "#22a06b",
    items: [
      { label: "ROE", key: "roe", fmt: "pct" },
      { label: "ROIC", key: "roic", fmt: "pct" },
      { label: "ROA", key: "roa", fmt: "pct" },
      { label: "Bruttomarge", key: "gross_margin", fmt: "pct" },
      { label: "Operative Marge", key: "op_margin", fmt: "pct" },
      { label: "Debt/Equity", key: "debt_equity", fmt: "num2", lower_better: true },
      { label: "Zinsdeckung", key: "int_coverage", fmt: "num1" },
      { label: "Current Ratio", key: "current_ratio", fmt: "num2" },
      { label: "Piotroski", key: "piotroski", fmt: "score9" },
      { label: "Altman Z", key: "altman_z", fmt: "num2" },
    ],
  },
  Growth: {
    weight: 15,
    color: "#d97757",
    items: [
      { label: "Umsatz CAGR 3J", key: "rev_cagr_3y", fmt: "pct" },
      { label: "EPS CAGR 3J", key: "eps_cagr_3y", fmt: "pct" },
      { label: "FCF CAGR 3J", key: "fcf_cagr_3y", fmt: "pct" },
      { label: "Forward EPS-Wachstum", key: "fwd_eps_growth", fmt: "pct" },
    ],
  },
  Momentum: {
    weight: 18,
    color: "#8b5cf6",
    items: [
      { label: "Return 1M", key: "ret_1m", fmt: "pct_signed" },
      { label: "Return 3M", key: "ret_3m", fmt: "pct_signed" },
      { label: "Return 6M", key: "ret_6m", fmt: "pct_signed" },
      { label: "Return 12M", key: "ret_12m", fmt: "pct_signed" },
      { label: "EPS-Revisionen 3M", key: "eps_revisions_3m", fmt: "pct_signed" },
    ],
  },
  "Low Volatility": {
    weight: 15,
    color: "#0891b2",
    items: [
      { label: "Beta", key: "beta", fmt: "num2" },
      { label: "Volatilität 1J", key: "volatility_1y", fmt: "pct" },
      { label: "52W Range", key: "range_52w_pct", fmt: "pct" },
    ],
  },
};

// Peers for each ticker
const PEERS = {
  ASML: [
    { ticker: "AMAT", name: "Applied Materials", score: 74.2, ret_12m: 24.6, classification: "B+" },
    { ticker: "LRCX", name: "Lam Research", score: 71.8, ret_12m: 18.4, classification: "B+" },
    { ticker: "KLAC", name: "KLA Corp", score: 73.1, ret_12m: 28.9, classification: "B+" },
    { ticker: "TEL", name: "Tokyo Electron", score: 68.4, ret_12m: 14.2, classification: "B" },
  ],
  NVDA: [
    { ticker: "AMD", name: "Advanced Micro Devices", score: 64.7, ret_12m: 12.8, classification: "B" },
    { ticker: "AVGO", name: "Broadcom", score: 78.3, ret_12m: 67.4, classification: "B+" },
    { ticker: "TSM", name: "TSMC", score: 81.7, ret_12m: 91.7, classification: "A" },
    { ticker: "MRVL", name: "Marvell Technology", score: 58.4, ret_12m: 21.3, classification: "C" },
  ],
  MSFT: [
    { ticker: "ORCL", name: "Oracle", score: 68.7, ret_12m: 32.4, classification: "B" },
    { ticker: "ADBE", name: "Adobe", score: 64.2, ret_12m: -8.7, classification: "B" },
    { ticker: "CRM", name: "Salesforce", score: 61.8, ret_12m: 14.6, classification: "B" },
    { ticker: "NOW", name: "ServiceNow", score: 72.4, ret_12m: 38.7, classification: "B+" },
  ],
  TSM: [
    { ticker: "NVDA", name: "NVIDIA", score: 84.2, ret_12m: 184.3, classification: "A" },
    { ticker: "ASML", name: "ASML Holding", score: 78.6, ret_12m: 38.5, classification: "B+" },
    { ticker: "AVGO", name: "Broadcom", score: 78.3, ret_12m: 67.4, classification: "B+" },
    { ticker: "AMAT", name: "Applied Materials", score: 74.2, ret_12m: 24.6, classification: "B+" },
  ],
};

// Format helpers (German locale)
const fmtDE = (n, dec = 1) => {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  return Number(n).toLocaleString("de-DE", { minimumFractionDigits: dec, maximumFractionDigits: dec });
};
const fmtPct = (n, signed = false, dec = 1) => {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  const s = fmtDE(n, dec) + " %";
  return signed && n > 0 ? "+" + s : s;
};
const fmtPrice = (n) => {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  return Number(n).toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
const fmtMcap = (n) => {
  if (n === null || n === undefined) return "–";
  if (n >= 1e12) return fmtDE(n / 1e12, 2) + " Bio.";
  if (n >= 1e9) return fmtDE(n / 1e9, 1) + " Mrd.";
  if (n >= 1e6) return fmtDE(n / 1e6, 0) + " Mio.";
  return fmtDE(n, 0);
};
const fmtIndicator = (val, fmt) => {
  if (val === null || val === undefined || Number.isNaN(val)) return "–";
  if (fmt === "pct") return fmtPct(val, false, 1);
  if (fmt === "pct_signed") return fmtPct(val, true, 1);
  if (fmt === "num1") return fmtDE(val, 1);
  if (fmt === "num2") return fmtDE(val, 2);
  if (fmt === "score9") return Math.round(val) + " / 9";
  return String(val);
};

// Add range_52w_pct computed
Object.values(TICKERS).forEach(t => {
  t.range_52w_pct = ((t.last_price - t.range_52w_low) / (t.range_52w_high - t.range_52w_low)) * 100;
});

// Mock percentile per indicator (sector-relative). Coarse but plausible.
// Higher = better after invert applied.
function pctFor(ticker, key) {
  // Use a deterministic mock based on factor scores so percentiles roughly align
  const t = TICKERS[ticker];
  // Map indicator → factor → use factor score with small per-indicator perturbation
  const factorMap = {
    pb: "value_score", pe: "value_score", pfcf: "value_score", ev_ebitda: "value_score",
    ps: "value_score", peg: "value_score", div_yield: "value_score",
    roe: "quality_score", roic: "quality_score", roa: "quality_score",
    gross_margin: "quality_score", op_margin: "quality_score", debt_equity: "quality_score",
    int_coverage: "quality_score", current_ratio: "quality_score",
    piotroski: "quality_score", altman_z: "quality_score",
    rev_cagr_3y: "growth_score", eps_cagr_3y: "growth_score", fcf_cagr_3y: "growth_score",
    fwd_eps_growth: "growth_score",
    ret_1m: "momentum_score", ret_3m: "momentum_score", ret_6m: "momentum_score",
    ret_12m: "momentum_score", eps_revisions_3m: "momentum_score",
    beta: "lowvol_score", volatility_1y: "lowvol_score", range_52w_pct: "lowvol_score",
  };
  const base = t[factorMap[key]] || 50;
  // hash key+ticker to a stable +/- 12 perturbation
  let h = 0;
  for (const c of (ticker + key)) h = (h * 31 + c.charCodeAt(0)) | 0;
  const delta = ((h % 25) - 12);
  return Math.max(2, Math.min(99, Math.round(base + delta)));
}

window.TICKERS = TICKERS;
window.INDICATORS = INDICATORS;
window.PEERS = PEERS;
window.fmtDE = fmtDE;
window.fmtPct = fmtPct;
window.fmtPrice = fmtPrice;
window.fmtMcap = fmtMcap;
window.fmtIndicator = fmtIndicator;
window.pctFor = pctFor;
