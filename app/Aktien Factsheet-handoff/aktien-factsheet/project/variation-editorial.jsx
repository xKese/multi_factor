// Variation 1 — Editorial fintech
// FT-style refined typography (serif display + grotesk body),
// generous whitespace, restrained palette, single accent for score tier.

const editorialStyles = {
  page: {
    width: "794px",
    height: "1123px",
    background: "#fafaf7",
    color: "#1a1a1a",
    fontFamily: "'Inter', 'Helvetica Neue', sans-serif",
    fontSize: "9px",
    lineHeight: 1.45,
    padding: "44px 48px 36px",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: "16px",
    fontFeatureSettings: "'tnum' 1, 'lnum' 1",
  },
  topBar: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    fontSize: "8px",
    letterSpacing: "0.14em",
    textTransform: "uppercase",
    color: "#737067",
    paddingBottom: "8px",
    borderBottom: "1px solid #1a1a1a",
  },
  brand: { fontWeight: 600, color: "#1a1a1a" },
  header: {
    display: "grid",
    gridTemplateColumns: "1fr auto",
    gap: "24px",
    alignItems: "end",
  },
  ticker: {
    fontFamily: "'Source Serif Pro', 'Source Serif 4', Georgia, serif",
    fontSize: "44px",
    lineHeight: 1,
    fontWeight: 600,
    letterSpacing: "-0.02em",
  },
  name: {
    fontFamily: "'Source Serif Pro', 'Source Serif 4', Georgia, serif",
    fontSize: "16px",
    fontWeight: 400,
    color: "#3d3a32",
    marginTop: "6px",
    fontStyle: "italic",
  },
  metaRow: {
    display: "flex",
    gap: "14px",
    fontSize: "9px",
    color: "#737067",
    marginTop: "10px",
    flexWrap: "wrap",
  },
  metaItem: { display: "flex", gap: "5px" },
  metaLabel: { color: "#a39e92" },
  scoreCard: {
    minWidth: "120px",
    textAlign: "right",
  },
  scoreNum: {
    fontFamily: "'Source Serif Pro', 'Source Serif 4', Georgia, serif",
    fontSize: "56px",
    lineHeight: 0.95,
    fontWeight: 600,
    letterSpacing: "-0.03em",
  },
  scoreOutOf: { color: "#a39e92", fontSize: "11px", fontWeight: 400 },
  scoreLabel: {
    fontSize: "9px",
    textTransform: "uppercase",
    letterSpacing: "0.16em",
    color: "#737067",
    marginTop: "4px",
  },
  classBadge: (color) => ({
    display: "inline-block",
    fontSize: "8.5px",
    fontWeight: 600,
    letterSpacing: "0.1em",
    textTransform: "uppercase",
    padding: "3px 8px",
    background: color,
    color: "#fff",
    marginTop: "6px",
  }),
  thesisRow: {
    display: "grid",
    gridTemplateColumns: "auto 1fr",
    gap: "16px",
    alignItems: "start",
    paddingTop: "12px",
    borderTop: "1px solid #e5e2d9",
  },
  thesisLabel: {
    fontSize: "8px",
    letterSpacing: "0.16em",
    textTransform: "uppercase",
    color: "#a39e92",
    paddingTop: "3px",
  },
  thesis: {
    fontFamily: "'Source Serif Pro', 'Source Serif 4', Georgia, serif",
    fontSize: "12px",
    fontStyle: "italic",
    lineHeight: 1.5,
    color: "#1a1a1a",
    fontWeight: 400,
  },
  quoteGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(6, 1fr)",
    gap: "0",
    border: "1px solid #1a1a1a",
  },
  quoteCell: {
    padding: "10px 12px",
    borderRight: "1px solid #e5e2d9",
  },
  quoteLabel: {
    fontSize: "7.5px",
    textTransform: "uppercase",
    letterSpacing: "0.12em",
    color: "#737067",
    marginBottom: "3px",
  },
  quoteValue: {
    fontFamily: "'Source Serif Pro', 'Source Serif 4', Georgia, serif",
    fontSize: "16px",
    fontWeight: 600,
    letterSpacing: "-0.01em",
  },
  quoteSub: { fontSize: "8px", color: "#737067", marginTop: "1px" },
  sectionTitle: {
    fontSize: "8px",
    textTransform: "uppercase",
    letterSpacing: "0.16em",
    color: "#737067",
    fontWeight: 600,
    marginBottom: "8px",
    paddingBottom: "5px",
    borderBottom: "1px solid #1a1a1a",
  },
  bodyGrid: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "20px",
    flex: 1,
  },
  factorBars: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  factorRow: {
    display: "grid",
    gridTemplateColumns: "70px 1fr 36px",
    gap: "8px",
    alignItems: "center",
  },
  factorLabel: {
    fontSize: "9.5px",
    fontWeight: 500,
  },
  factorBarOuter: {
    height: "10px",
    background: "#efece4",
    position: "relative",
  },
  factorBarInner: (color, pct) => ({
    height: "100%",
    width: `${pct}%`,
    background: color,
  }),
  factorWeight: {
    fontSize: "8px",
    color: "#a39e92",
    marginLeft: "6px",
  },
  factorScore: {
    textAlign: "right",
    fontSize: "10.5px",
    fontFamily: "'Source Serif Pro', 'Source Serif 4', Georgia, serif",
    fontWeight: 600,
  },
  indicatorTable: {
    width: "100%",
    fontSize: "8.5px",
    borderCollapse: "collapse",
  },
  indicatorHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "baseline",
    marginBottom: "5px",
  },
  indicatorTitle: {
    fontSize: "9.5px",
    fontWeight: 600,
    letterSpacing: "0.04em",
    textTransform: "uppercase",
  },
  indicatorWeight: {
    fontSize: "8px",
    color: "#a39e92",
  },
  indicatorBlock: {
    marginBottom: "10px",
  },
  indicatorLine: {
    display: "grid",
    gridTemplateColumns: "1fr auto 36px",
    gap: "6px",
    alignItems: "center",
    padding: "2.5px 0",
    borderBottom: "1px dotted #e5e2d9",
  },
  indicatorLabel: { color: "#3d3a32" },
  indicatorValue: { fontWeight: 500, textAlign: "right" },
  pctTrack: {
    height: "6px",
    background: "#efece4",
    position: "relative",
  },
  pctFill: (pct, color) => ({
    height: "100%",
    width: `${pct}%`,
    background: color,
  }),
  badgesRow: {
    display: "flex",
    gap: "6px",
    flexWrap: "wrap",
  },
  badge: (tone) => {
    const tones = {
      up: { bg: "#e8f1ec", color: "#1b5e3a", border: "#1b5e3a" },
      down: { bg: "#f7e9e7", color: "#9c2a20", border: "#9c2a20" },
      warn: { bg: "#faf3e0", color: "#8a6914", border: "#b08a1f" },
      info: { bg: "#eaf0f7", color: "#1a3d72", border: "#1a3d72" },
      neutral: { bg: "#f0eee8", color: "#3d3a32", border: "#737067" },
    };
    const c = tones[tone] || tones.neutral;
    return {
      display: "inline-flex",
      alignItems: "center",
      gap: "5px",
      padding: "3px 8px",
      fontSize: "8px",
      letterSpacing: "0.04em",
      background: c.bg,
      color: c.color,
      border: `0.5px solid ${c.border}`,
    };
  },
  badgeLabel: { textTransform: "uppercase", letterSpacing: "0.1em", opacity: 0.7 },
  badgeValue: { fontWeight: 600 },
  rueckblickWrap: {
    display: "grid",
    gridTemplateColumns: "auto 1fr",
    gap: "12px",
    alignItems: "center",
  },
  retCol: { display: "flex", flexDirection: "column", gap: "2px", alignItems: "center" },
  retLabel: { fontSize: "7.5px", color: "#737067", textTransform: "uppercase", letterSpacing: "0.1em" },
  retValue: (positive) => ({
    fontFamily: "'Source Serif Pro', Georgia, serif",
    fontSize: "13px",
    fontWeight: 600,
    color: positive ? "#1b5e3a" : "#9c2a20",
    whiteSpace: "nowrap",
  }),
  retGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(4, 1fr)",
    gap: "12px",
  },
  peerRow: {
    display: "grid",
    gridTemplateColumns: "repeat(4, 1fr)",
    gap: "8px",
  },
  peerCard: {
    border: "1px solid #1a1a1a",
    padding: "8px 10px",
  },
  peerHead: { display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: "2px" },
  peerTicker: { fontWeight: 700, fontSize: "11px", letterSpacing: "0.02em" },
  peerScore: {
    fontFamily: "'Source Serif Pro', Georgia, serif",
    fontSize: "13px",
    fontWeight: 600,
  },
  peerName: { fontSize: "8px", color: "#737067", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  peerMeta: { fontSize: "8px", marginTop: "3px", display: "flex", justifyContent: "space-between" },
  footer: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    fontSize: "7.5px",
    color: "#a39e92",
    letterSpacing: "0.1em",
    textTransform: "uppercase",
    paddingTop: "6px",
    borderTop: "1px solid #e5e2d9",
  },
  smaWrap: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    fontSize: "9px",
  },
  smaSignal: (tone) => ({
    fontWeight: 700,
    color: tone === "up" ? "#1b5e3a" : tone === "down" ? "#9c2a20" : "#1a1a1a",
    fontSize: "11px",
    fontFamily: "'Source Serif Pro', Georgia, serif",
  }),
  inlineLabel: { fontSize: "8px", color: "#737067", textTransform: "uppercase", letterSpacing: "0.1em" },
  rangeBar: {
    position: "relative",
    height: "6px",
    background: "#efece4",
    margin: "8px 0 4px",
  },
  rangeFill: (pct) => ({
    position: "absolute",
    left: 0,
    width: `${pct}%`,
    height: "100%",
    background: "#1a1a1a",
  }),
  rangeMarker: (pct) => ({
    position: "absolute",
    left: `calc(${pct}% - 3px)`,
    top: "-2px",
    width: "6px",
    height: "10px",
    background: "#d97757",
  }),
  rangeLabels: { display: "flex", justifyContent: "space-between", fontSize: "7.5px", color: "#737067" },
};

function classColor(classification) {
  if (classification.startsWith("A")) return "#1b5e3a";
  if (classification.startsWith("B+")) return "#1a3d72";
  if (classification.startsWith("B")) return "#3d3a32";
  return "#737067";
}

function EditorialBadge({ label, value, tone = "neutral" }) {
  return (
    <span style={editorialStyles.badge(tone)}>
      <span style={editorialStyles.badgeLabel}>{label}</span>
      <span style={editorialStyles.badgeValue}>{value}</span>
    </span>
  );
}

function EditorialFactorBars({ t }) {
  const factors = [
    { label: "Value", score: t.value_score, color: "#5b8def", weight: 25 },
    { label: "Quality", score: t.quality_score, color: "#22a06b", weight: 27 },
    { label: "Growth", score: t.growth_score, color: "#d97757", weight: 15 },
    { label: "Momentum", score: t.momentum_score, color: "#8b5cf6", weight: 18 },
    { label: "Low Vol", score: t.lowvol_score, color: "#0891b2", weight: 15 },
  ];
  return (
    <div style={editorialStyles.factorBars}>
      {factors.map(f => (
        <div key={f.label} style={editorialStyles.factorRow}>
          <div>
            <div style={editorialStyles.factorLabel}>{f.label}</div>
            <div style={editorialStyles.factorWeight}>Gewicht {f.weight} %</div>
          </div>
          <div style={editorialStyles.factorBarOuter}>
            <div style={editorialStyles.factorBarInner(f.color, f.score)} />
          </div>
          <div style={editorialStyles.factorScore}>{fmtDE(f.score, 0)}</div>
        </div>
      ))}
    </div>
  );
}

function EditorialIndicators({ t, dense }) {
  const groups = Object.entries(INDICATORS);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "12px" }}>
      {groups.map(([name, group]) => (
        <div key={name} style={editorialStyles.indicatorBlock}>
          <div style={editorialStyles.indicatorHeader}>
            <span style={editorialStyles.indicatorTitle}>{name}</span>
            <span style={editorialStyles.indicatorWeight}>{group.weight} %</span>
          </div>
          {group.items.map(item => {
            const v = t[item.key];
            const pct = pctFor(t.ticker, item.key);
            return (
              <div key={item.key} style={editorialStyles.indicatorLine}>
                <span style={editorialStyles.indicatorLabel}>{item.label}</span>
                <span style={editorialStyles.indicatorValue}>{fmtIndicator(v, item.fmt)}</span>
                <div style={editorialStyles.pctTrack}>
                  <div style={editorialStyles.pctFill(pct, group.color)} />
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function EditorialFactsheet({ ticker, dense, showPeers }) {
  const t = TICKERS[ticker];
  const peers = PEERS[ticker] || [];
  const accent = classColor(t.classification);
  const rangePct = ((t.last_price - t.range_52w_low) / (t.range_52w_high - t.range_52w_low)) * 100;
  const smaTone = t.sma_signal.includes("GOLDEN") ? "up" :
                  t.sma_signal.includes("DEATH") ? "down" :
                  t.sma_signal.includes(">") ? "up" : "neutral";

  return (
    <div style={{ ...editorialStyles.page, padding: dense ? "32px 38px 28px" : "44px 48px 36px", gap: dense ? "11px" : "16px" }}>
      <div style={editorialStyles.topBar}>
        <span style={editorialStyles.brand}>Meeder &amp; Seifer · Multi-Faktor-Modell</span>
        <span>Stand 04. Mai 2026 · Editorial</span>
      </div>

      <div style={editorialStyles.header}>
        <div>
          <div style={editorialStyles.ticker}>{t.ticker}</div>
          <div style={editorialStyles.name}>{t.name}</div>
          <div style={editorialStyles.metaRow}>
            <span style={editorialStyles.metaItem}><span style={editorialStyles.metaLabel}>Sektor</span> {t.sector}</span>
            <span style={editorialStyles.metaItem}><span style={editorialStyles.metaLabel}>Industrie</span> {t.industry}</span>
            <span style={editorialStyles.metaItem}><span style={editorialStyles.metaLabel}>Region</span> {t.region}</span>
          </div>
        </div>
        <div style={editorialStyles.scoreCard}>
          <div style={editorialStyles.scoreNum}>
            {fmtDE(t.total_score, 1)}
            <span style={editorialStyles.scoreOutOf}> / 100</span>
          </div>
          <div style={editorialStyles.scoreLabel}>Gesamt-Score</div>
          <div style={editorialStyles.classBadge(accent)}>{t.classification}</div>
        </div>
      </div>

      <div style={editorialStyles.thesisRow}>
        <div style={editorialStyles.thesisLabel}>These</div>
        <div style={editorialStyles.thesis}>{t.thesis}</div>
      </div>

      <div style={editorialStyles.quoteGrid}>
        <div style={editorialStyles.quoteCell}>
          <div style={editorialStyles.quoteLabel}>Kurs</div>
          <div style={editorialStyles.quoteValue}>{fmtPrice(t.last_price)}</div>
          <div style={editorialStyles.quoteSub}>USD</div>
        </div>
        <div style={editorialStyles.quoteCell}>
          <div style={editorialStyles.quoteLabel}>Marktkap.</div>
          <div style={editorialStyles.quoteValue}>{fmtMcap(t.market_cap)}</div>
          <div style={editorialStyles.quoteSub}>USD</div>
        </div>
        <div style={editorialStyles.quoteCell}>
          <div style={editorialStyles.quoteLabel}>52W Range</div>
          <div style={editorialStyles.rangeBar}>
            <div style={editorialStyles.rangeMarker(rangePct)} />
          </div>
          <div style={editorialStyles.rangeLabels}>
            <span>{fmtPrice(t.range_52w_low)}</span>
            <span>{fmtPrice(t.range_52w_high)}</span>
          </div>
        </div>
        <div style={editorialStyles.quoteCell}>
          <div style={editorialStyles.quoteLabel}>Beta</div>
          <div style={editorialStyles.quoteValue}>{fmtDE(t.beta, 2)}</div>
          <div style={editorialStyles.quoteSub}>Vol. 1J {fmtPct(t.volatility_1y, false, 1)}</div>
        </div>
        <div style={editorialStyles.quoteCell}>
          <div style={editorialStyles.quoteLabel}>Empfehlung</div>
          <div style={{ ...editorialStyles.quoteValue, color: t.recommendation.includes("BUY") ? "#1b5e3a" : t.recommendation === "SELL" ? "#9c2a20" : "#3d3a32" }}>{t.recommendation}</div>
          <div style={editorialStyles.quoteSub}>Filter {t.filter_ok}</div>
        </div>
        <div style={{ ...editorialStyles.quoteCell, borderRight: "none" }}>
          <div style={editorialStyles.quoteLabel}>Sektor-Rang</div>
          <div style={editorialStyles.quoteValue}>{t.sector_rank} <span style={{ color: "#a39e92", fontSize: "10px" }}>/ {t.sector_total}</span></div>
          <div style={editorialStyles.quoteSub}>Industrie {t.industry_rank}/{t.industry_total}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.4fr", gap: "20px" }}>
        <div>
          <div style={editorialStyles.sectionTitle}>Faktor-Profil</div>
          <EditorialFactorBars t={t} />
          <div style={{ ...editorialStyles.sectionTitle, marginTop: "14px" }}>Filter &amp; Signale</div>
          <div style={editorialStyles.badgesRow}>
            <EditorialBadge label="Piotroski" value={`${t.piotroski} / 9`} tone={t.piotroski >= 5 ? "up" : "down"} />
            <EditorialBadge label="Altman Z" value={fmtDE(t.altman_z, 2)} tone={t.altman_z >= 1.8 ? "up" : "down"} />
            <EditorialBadge label="Filter" value={t.filter_ok === "JA" ? "bestanden" : "nicht bestanden"} tone={t.filter_ok === "JA" ? "up" : "down"} />
          </div>
          <div style={{ marginTop: "10px" }}>
            <div style={editorialStyles.inlineLabel}>SMA-Signal</div>
            <div style={editorialStyles.smaSignal(smaTone)}>{t.sma_signal}</div>
            <div style={{ fontSize: "8px", color: "#737067", marginTop: "2px" }}>
              SMA-50 {fmtPrice(t.sma_50)} · SMA-200 {fmtPrice(t.sma_200)}
            </div>
          </div>
          <div style={{ marginTop: "10px" }}>
            <div style={editorialStyles.inlineLabel}>Rückblicksfenster</div>
            <div style={{ ...editorialStyles.retGrid, marginTop: "4px" }}>
              {[["1M", "ret_1m"], ["3M", "ret_3m"], ["6M", "ret_6m"], ["12M", "ret_12m"]].map(([l, k]) => (
                <div key={k} style={editorialStyles.retCol}>
                  <div style={editorialStyles.retValue(t[k] >= 0)}>{fmtPct(t[k], true, 1)}</div>
                  <div style={editorialStyles.retLabel}>{l}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div>
          <div style={editorialStyles.sectionTitle}>Kennzahlen</div>
          <EditorialIndicators t={t} dense={dense} />
        </div>
      </div>

      {showPeers && (
        <div style={{ marginTop: "auto" }}>
          <div style={editorialStyles.sectionTitle}>Comparables</div>
          <div style={editorialStyles.peerRow}>
            {peers.slice(0, 4).map(p => (
              <div key={p.ticker} style={editorialStyles.peerCard}>
                <div style={editorialStyles.peerHead}>
                  <span style={editorialStyles.peerTicker}>{p.ticker}</span>
                  <span style={editorialStyles.peerScore}>{fmtDE(p.score, 1)}</span>
                </div>
                <div style={editorialStyles.peerName}>{p.name}</div>
                <div style={editorialStyles.peerMeta}>
                  <span style={{ color: "#737067" }}>{p.classification}</span>
                  <span style={{ color: p.ret_12m >= 0 ? "#1b5e3a" : "#9c2a20", fontWeight: 600 }}>
                    {fmtPct(p.ret_12m, true, 1)} 12M
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={editorialStyles.footer}>
        <span>Quelle Koyfin · M&amp;S Multi-Faktor-Modell</span>
        <span>Vertraulich · Nur für interne Verwendung</span>
      </div>
    </div>
  );
}

window.EditorialFactsheet = EditorialFactsheet;
