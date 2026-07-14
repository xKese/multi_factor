// Variation 2 — Swiss Grid
// Strict 12-column grid, neutral palette, monospace for numbers,
// dense but precise. Single accent (deep red-orange) for tier markers.

const swissStyles = {
  page: {
    width: "794px",
    height: "1123px",
    background: "#ffffff",
    color: "#0d0d0d",
    fontFamily: "'Inter', 'Helvetica Neue', sans-serif",
    fontSize: "8.5px",
    lineHeight: 1.4,
    padding: "36px 36px 28px",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: "12px",
    fontFeatureSettings: "'tnum' 1, 'lnum' 1",
  },
  mono: { fontFamily: "'JetBrains Mono', 'Menlo', monospace" },
  rule: { height: "2px", background: "#0d0d0d", margin: 0 },
  thinRule: { height: "1px", background: "#0d0d0d", margin: 0 },
  topBar: {
    display: "grid",
    gridTemplateColumns: "repeat(12, 1fr)",
    gap: "8px",
    fontSize: "7.5px",
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    paddingBottom: "6px",
  },
  topCell: { gridColumn: "span 4" },
  topCellEnd: { gridColumn: "span 4", textAlign: "right" },
  header: {
    display: "grid",
    gridTemplateColumns: "repeat(12, 1fr)",
    gap: "8px",
    alignItems: "end",
    paddingTop: "6px",
  },
  hdrLeft: { gridColumn: "span 7" },
  hdrRight: { gridColumn: "span 5", textAlign: "right" },
  ticker: {
    fontSize: "52px",
    fontWeight: 700,
    letterSpacing: "-0.04em",
    lineHeight: 0.9,
    fontFamily: "'JetBrains Mono', 'Menlo', monospace",
  },
  name: { fontSize: "13px", fontWeight: 500, marginTop: "4px", color: "#0d0d0d" },
  metaRow: {
    display: "flex",
    gap: "14px",
    fontSize: "8px",
    color: "#5a5a5a",
    marginTop: "5px",
    textTransform: "uppercase",
    letterSpacing: "0.06em",
  },
  scoreNum: {
    fontSize: "64px",
    fontWeight: 700,
    lineHeight: 0.85,
    letterSpacing: "-0.04em",
    fontFamily: "'JetBrains Mono', 'Menlo', monospace",
  },
  scoreSlash: { fontSize: "20px", color: "#9a9a9a", fontWeight: 400 },
  scoreLabel: {
    fontSize: "7.5px",
    textTransform: "uppercase",
    letterSpacing: "0.18em",
    color: "#5a5a5a",
    marginTop: "4px",
  },
  classChip: (color) => ({
    display: "inline-block",
    fontSize: "8px",
    fontWeight: 700,
    letterSpacing: "0.12em",
    padding: "3px 7px",
    background: color,
    color: "#ffffff",
    marginTop: "5px",
  }),
  thesisRow: {
    display: "grid",
    gridTemplateColumns: "60px 1fr",
    gap: "12px",
    paddingTop: "8px",
    borderTop: "1px solid #0d0d0d",
  },
  thesisLabel: {
    fontSize: "7.5px",
    textTransform: "uppercase",
    letterSpacing: "0.16em",
    color: "#5a5a5a",
  },
  thesis: { fontSize: "10px", lineHeight: 1.5, color: "#0d0d0d" },
  quoteGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(12, 1fr)",
    border: "1px solid #0d0d0d",
  },
  quoteCell: (span = 2, last = false) => ({
    gridColumn: `span ${span}`,
    padding: "8px 9px",
    borderRight: last ? "none" : "1px solid #0d0d0d",
  }),
  qLabel: {
    fontSize: "7px",
    textTransform: "uppercase",
    letterSpacing: "0.14em",
    color: "#5a5a5a",
    marginBottom: "4px",
  },
  qValue: {
    fontFamily: "'JetBrains Mono', 'Menlo', monospace",
    fontSize: "15px",
    fontWeight: 600,
    letterSpacing: "-0.02em",
  },
  qSub: { fontSize: "7.5px", color: "#5a5a5a", marginTop: "2px" },
  sectionLabel: {
    fontSize: "7.5px",
    textTransform: "uppercase",
    letterSpacing: "0.18em",
    color: "#0d0d0d",
    fontWeight: 700,
    paddingBottom: "4px",
    marginBottom: "6px",
    borderBottom: "1px solid #0d0d0d",
  },
  body: {
    display: "grid",
    gridTemplateColumns: "repeat(12, 1fr)",
    gap: "10px",
    flex: 1,
  },
  factorTable: {
    width: "100%",
    borderCollapse: "collapse",
    fontFamily: "'JetBrains Mono', 'Menlo', monospace",
    fontSize: "9px",
  },
  ftRow: { borderBottom: "1px solid #e0e0e0" },
  ftLabel: { padding: "5px 0", fontFamily: "'Inter', sans-serif", fontWeight: 500 },
  ftWeight: { padding: "5px 6px", color: "#5a5a5a", fontSize: "8px" },
  ftScore: { padding: "5px 0 5px 6px", fontWeight: 700, textAlign: "right", fontSize: "11px" },
  bar: {
    width: "100%",
    height: "8px",
    background: "#f0f0f0",
    position: "relative",
  },
  barFill: (pct) => ({
    height: "100%",
    width: `${pct}%`,
    background: "#0d0d0d",
  }),
  barAccent: (pct) => ({
    height: "100%",
    width: `${pct}%`,
    background: "#d44a26",
  }),
  indGrid: { display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: "10px 14px" },
  indGroup: { },
  indHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "baseline",
    paddingBottom: "3px",
    borderBottom: "1px solid #0d0d0d",
    marginBottom: "4px",
  },
  indTitle: { fontSize: "8.5px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase" },
  indWeight: { fontSize: "7.5px", color: "#5a5a5a", fontFamily: "'JetBrains Mono', monospace" },
  indLine: {
    display: "grid",
    gridTemplateColumns: "1fr 50px 30px",
    alignItems: "center",
    padding: "1.5px 0",
    fontSize: "8.5px",
    gap: "4px",
  },
  indVal: { fontFamily: "'JetBrains Mono', monospace", textAlign: "right", fontWeight: 500 },
  pctTrack: { height: "4px", background: "#f0f0f0" },
  pctFill: (pct) => ({ height: "100%", width: `${pct}%`, background: "#0d0d0d" }),
  badgesRow: { display: "flex", gap: "5px", flexWrap: "wrap" },
  badge: (tone) => {
    const map = {
      up: { bg: "#0d0d0d", color: "#fff" },
      down: { bg: "#d44a26", color: "#fff" },
      warn: { bg: "#f0f0f0", color: "#0d0d0d", border: "#0d0d0d" },
      neutral: { bg: "#fff", color: "#0d0d0d", border: "#0d0d0d" },
    };
    const c = map[tone] || map.neutral;
    return {
      display: "inline-flex",
      gap: "5px",
      padding: "3px 7px",
      fontSize: "8px",
      letterSpacing: "0.06em",
      background: c.bg,
      color: c.color,
      border: `1px solid ${c.border || c.bg}`,
      fontFamily: "'JetBrains Mono', monospace",
    };
  },
  badgeLabel: { textTransform: "uppercase", letterSpacing: "0.12em", opacity: 0.75 },
  badgeValue: { fontWeight: 700 },
  retRow: { display: "grid", gridTemplateColumns: "repeat(4, 1fr)", border: "1px solid #0d0d0d" },
  retCell: (last = false) => ({
    padding: "6px 8px",
    borderRight: last ? "none" : "1px solid #0d0d0d",
    textAlign: "center",
  }),
  retVal: (pos) => ({
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: "13px",
    fontWeight: 700,
    color: pos ? "#0d0d0d" : "#d44a26",
    whiteSpace: "nowrap",
  }),
  retLabel: { fontSize: "7px", color: "#5a5a5a", textTransform: "uppercase", letterSpacing: "0.14em", marginTop: "2px" },
  rangeBar: {
    position: "relative",
    height: "8px",
    background: "#f0f0f0",
    margin: "6px 0 4px",
  },
  rangeFill: { position: "absolute", left: 0, height: "100%", background: "#0d0d0d" },
  rangeMarker: (pct) => ({
    position: "absolute",
    left: `calc(${pct}% - 1.5px)`,
    top: "-3px",
    width: "3px",
    height: "14px",
    background: "#d44a26",
  }),
  rangeLabels: {
    display: "flex", justifyContent: "space-between",
    fontSize: "7.5px", color: "#5a5a5a",
    fontFamily: "'JetBrains Mono', monospace",
  },
  peerGrid: { display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "0", border: "1px solid #0d0d0d" },
  peerCell: (last = false) => ({
    padding: "8px 10px",
    borderRight: last ? "none" : "1px solid #0d0d0d",
  }),
  peerHead: { display: "flex", justifyContent: "space-between", alignItems: "baseline" },
  peerTicker: { fontSize: "11px", fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" },
  peerScore: { fontSize: "13px", fontWeight: 700, fontFamily: "'JetBrains Mono', monospace" },
  peerName: { fontSize: "7.5px", color: "#5a5a5a", marginTop: "2px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  peerFoot: { display: "flex", justifyContent: "space-between", marginTop: "4px", fontSize: "7.5px" },
  footer: {
    display: "grid",
    gridTemplateColumns: "repeat(12, 1fr)",
    gap: "8px",
    fontSize: "7px",
    letterSpacing: "0.12em",
    textTransform: "uppercase",
    color: "#5a5a5a",
    paddingTop: "6px",
    borderTop: "1px solid #0d0d0d",
  },
};

function classColorSwiss(c) {
  if (c.startsWith("A")) return "#0d0d0d";
  if (c.startsWith("B+")) return "#0d0d0d";
  if (c.startsWith("B")) return "#5a5a5a";
  return "#9a9a9a";
}

function SwissBadge({ label, value, tone = "neutral" }) {
  return (
    <span style={swissStyles.badge(tone)}>
      <span style={swissStyles.badgeLabel}>{label}</span>
      <span style={swissStyles.badgeValue}>{value}</span>
    </span>
  );
}

function SwissFactsheet({ ticker, dense, showPeers }) {
  const t = TICKERS[ticker];
  const peers = PEERS[ticker] || [];
  const accent = classColorSwiss(t.classification);
  const rangePct = ((t.last_price - t.range_52w_low) / (t.range_52w_high - t.range_52w_low)) * 100;
  const smaTone = t.sma_signal.includes("GOLDEN") ? "up" : t.sma_signal.includes("DEATH") ? "down" : "neutral";

  const factors = [
    { label: "Value", score: t.value_score, weight: 25 },
    { label: "Quality", score: t.quality_score, weight: 27 },
    { label: "Growth", score: t.growth_score, weight: 15 },
    { label: "Momentum", score: t.momentum_score, weight: 18 },
    { label: "Low Vol", score: t.lowvol_score, weight: 15 },
  ];

  return (
    <div style={{ ...swissStyles.page, padding: dense ? "28px 28px 22px" : "36px 36px 28px", gap: dense ? "8px" : "12px" }}>
      <div style={swissStyles.topBar}>
        <span style={swissStyles.topCell}>M&amp;S · Multi-Faktor</span>
        <span style={{ gridColumn: "span 4", textAlign: "center" }}>Aktien-Factsheet</span>
        <span style={swissStyles.topCellEnd}>04.05.2026</span>
      </div>
      <div style={swissStyles.rule} />

      <div style={swissStyles.header}>
        <div style={swissStyles.hdrLeft}>
          <div style={swissStyles.ticker}>{t.ticker}</div>
          <div style={swissStyles.name}>{t.name}</div>
          <div style={swissStyles.metaRow}>
            <span>{t.sector}</span><span>·</span>
            <span>{t.industry}</span><span>·</span>
            <span>{t.region}</span>
          </div>
        </div>
        <div style={swissStyles.hdrRight}>
          <div style={swissStyles.scoreNum}>
            {fmtDE(t.total_score, 1)}
            <span style={swissStyles.scoreSlash}> /100</span>
          </div>
          <div style={swissStyles.scoreLabel}>Gesamt-Score</div>
          <div style={swissStyles.classChip(accent)}>{t.classification}</div>
        </div>
      </div>

      <div style={swissStyles.thesisRow}>
        <div style={swissStyles.thesisLabel}>These</div>
        <div style={swissStyles.thesis}>{t.thesis}</div>
      </div>

      <div style={swissStyles.quoteGrid}>
        <div style={swissStyles.quoteCell(2)}>
          <div style={swissStyles.qLabel}>Kurs USD</div>
          <div style={swissStyles.qValue}>{fmtPrice(t.last_price)}</div>
          <div style={swissStyles.qSub}>{fmtPct(t.ret_1m, true, 1)} · 1M</div>
        </div>
        <div style={swissStyles.quoteCell(2)}>
          <div style={swissStyles.qLabel}>Marktkap.</div>
          <div style={swissStyles.qValue}>{fmtMcap(t.market_cap)}</div>
          <div style={swissStyles.qSub}>USD</div>
        </div>
        <div style={swissStyles.quoteCell(3)}>
          <div style={swissStyles.qLabel}>52-Wochen-Range</div>
          <div style={swissStyles.rangeBar}>
            <div style={swissStyles.rangeMarker(rangePct)} />
          </div>
          <div style={swissStyles.rangeLabels}>
            <span>{fmtPrice(t.range_52w_low)}</span>
            <span>{fmtPrice(t.range_52w_high)}</span>
          </div>
        </div>
        <div style={swissStyles.quoteCell(2)}>
          <div style={swissStyles.qLabel}>Beta · Vol 1J</div>
          <div style={swissStyles.qValue}>{fmtDE(t.beta, 2)}</div>
          <div style={swissStyles.qSub}>{fmtPct(t.volatility_1y, false, 1)}</div>
        </div>
        <div style={swissStyles.quoteCell(2)}>
          <div style={swissStyles.qLabel}>Empfehlung</div>
          <div style={{ ...swissStyles.qValue, color: t.recommendation.includes("BUY") ? "#0d0d0d" : "#d44a26" }}>{t.recommendation}</div>
          <div style={swissStyles.qSub}>Filter {t.filter_ok}</div>
        </div>
        <div style={swissStyles.quoteCell(1, true)}>
          <div style={swissStyles.qLabel}>Rang</div>
          <div style={swissStyles.qValue}>{t.sector_rank}</div>
          <div style={swissStyles.qSub}>/ {t.sector_total}</div>
        </div>
      </div>

      <div style={swissStyles.body}>
        <div style={{ gridColumn: "span 5" }}>
          <div style={swissStyles.sectionLabel}>Faktor-Profil</div>
          <table style={swissStyles.factorTable}>
            <tbody>
              {factors.map(f => (
                <tr key={f.label} style={swissStyles.ftRow}>
                  <td style={swissStyles.ftLabel}>{f.label}</td>
                  <td style={{ width: "55%", padding: "5px 6px" }}>
                    <div style={swissStyles.bar}>
                      <div style={swissStyles.barFill(f.score)} />
                    </div>
                  </td>
                  <td style={swissStyles.ftWeight}>{f.weight}%</td>
                  <td style={swissStyles.ftScore}>{fmtDE(f.score, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div style={{ ...swissStyles.sectionLabel, marginTop: "12px" }}>Signal · Filter</div>
          <div style={swissStyles.badgesRow}>
            <SwissBadge label="Piotroski" value={`${t.piotroski}/9`} tone={t.piotroski >= 5 ? "up" : "down"} />
            <SwissBadge label="Altman Z" value={fmtDE(t.altman_z, 2)} tone={t.altman_z >= 1.8 ? "up" : "down"} />
            <SwissBadge label="SMA" value={t.sma_signal.replace("✓ ", "").replace("⚠ ", "")} tone={smaTone} />
            <SwissBadge label="Filter" value={t.filter_ok} tone={t.filter_ok === "JA" ? "up" : "down"} />
          </div>

          <div style={{ marginTop: "10px" }}>
            <div style={swissStyles.qLabel}>Rückblicksfenster</div>
            <div style={swissStyles.retRow}>
              {[["1M", "ret_1m"], ["3M", "ret_3m"], ["6M", "ret_6m"], ["12M", "ret_12m"]].map(([l, k], i, a) => (
                <div key={k} style={swissStyles.retCell(i === a.length - 1)}>
                  <div style={swissStyles.retVal(t[k] >= 0)}>{fmtPct(t[k], true, 1)}</div>
                  <div style={swissStyles.retLabel}>{l}</div>
                </div>
              ))}
            </div>
            <div style={{ fontSize: "7.5px", color: "#5a5a5a", marginTop: "4px", fontFamily: "'JetBrains Mono', monospace" }}>
              SMA-50 {fmtPrice(t.sma_50)} · SMA-200 {fmtPrice(t.sma_200)}
            </div>
          </div>
        </div>

        <div style={{ gridColumn: "span 7" }}>
          <div style={swissStyles.sectionLabel}>Kennzahlen</div>
          <div style={swissStyles.indGrid}>
            {Object.entries(INDICATORS).map(([name, group]) => (
              <div key={name} style={swissStyles.indGroup}>
                <div style={swissStyles.indHeader}>
                  <span style={swissStyles.indTitle}>{name}</span>
                  <span style={swissStyles.indWeight}>{group.weight}%</span>
                </div>
                {group.items.map(item => {
                  const v = t[item.key];
                  const pct = pctFor(t.ticker, item.key);
                  return (
                    <div key={item.key} style={swissStyles.indLine}>
                      <span>{item.label}</span>
                      <span style={swissStyles.indVal}>{fmtIndicator(v, item.fmt)}</span>
                      <div style={swissStyles.pctTrack}>
                        <div style={swissStyles.pctFill(pct)} />
                      </div>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </div>

      {showPeers && (
        <div style={{ marginTop: "auto" }}>
          <div style={swissStyles.sectionLabel}>Comparables</div>
          <div style={swissStyles.peerGrid}>
            {peers.slice(0, 4).map((p, i, a) => (
              <div key={p.ticker} style={swissStyles.peerCell(i === a.length - 1)}>
                <div style={swissStyles.peerHead}>
                  <span style={swissStyles.peerTicker}>{p.ticker}</span>
                  <span style={swissStyles.peerScore}>{fmtDE(p.score, 1)}</span>
                </div>
                <div style={swissStyles.peerName}>{p.name}</div>
                <div style={swissStyles.peerFoot}>
                  <span style={{ color: "#5a5a5a" }}>{p.classification}</span>
                  <span style={{ color: p.ret_12m >= 0 ? "#0d0d0d" : "#d44a26", fontFamily: "'JetBrains Mono', monospace", fontWeight: 600 }}>
                    {fmtPct(p.ret_12m, true, 1)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={swissStyles.footer}>
        <span style={{ gridColumn: "span 4" }}>Quelle Koyfin</span>
        <span style={{ gridColumn: "span 4", textAlign: "center" }}>M&amp;S Multi-Faktor-Modell</span>
        <span style={{ gridColumn: "span 4", textAlign: "right" }}>Vertraulich</span>
      </div>
    </div>
  );
}

window.SwissFactsheet = SwissFactsheet;
