// Variation 3 — Modern Card Mosaic
// Soft cards on a tinted background, generous radii, contemporary fintech.
// Subtle shadows, color-coded factor accents, ample but disciplined whitespace.

const cardStyles = {
  page: {
    width: "794px",
    height: "1123px",
    background: "#f5f4f1",
    color: "#15161a",
    fontFamily: "'Inter', 'Helvetica Neue', sans-serif",
    fontSize: "8.5px",
    lineHeight: 1.45,
    padding: "32px 32px 24px",
    boxSizing: "border-box",
    display: "flex",
    flexDirection: "column",
    gap: "10px",
    fontFeatureSettings: "'tnum' 1, 'lnum' 1, 'cv11' 1",
  },
  topBar: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    fontSize: "8px",
    letterSpacing: "0.1em",
    textTransform: "uppercase",
    color: "#75757b",
  },
  brand: {
    display: "flex", alignItems: "center", gap: "8px",
    fontWeight: 600, color: "#15161a",
  },
  dot: { width: "7px", height: "7px", borderRadius: "50%", background: "#15161a" },
  card: {
    background: "#ffffff",
    borderRadius: "10px",
    padding: "14px 16px",
    boxShadow: "0 1px 0 rgba(20,20,30,0.04), 0 1px 2px rgba(20,20,30,0.04)",
  },
  headerCard: {
    display: "grid",
    gridTemplateColumns: "1fr auto",
    gap: "16px",
    alignItems: "center",
  },
  ticker: {
    fontSize: "36px",
    fontWeight: 700,
    letterSpacing: "-0.025em",
    lineHeight: 1,
  },
  name: { fontSize: "11.5px", color: "#3a3a40", fontWeight: 500, marginTop: "3px" },
  metaPills: { display: "flex", gap: "5px", marginTop: "7px", flexWrap: "wrap" },
  pill: {
    fontSize: "7.5px",
    padding: "2px 8px",
    background: "#f5f4f1",
    color: "#3a3a40",
    borderRadius: "10px",
    fontWeight: 500,
  },
  scoreSide: {
    textAlign: "right",
    minWidth: "138px",
    paddingLeft: "16px",
    borderLeft: "1px solid #ebeae5",
  },
  scoreNum: {
    fontSize: "44px",
    fontWeight: 700,
    lineHeight: 0.95,
    letterSpacing: "-0.03em",
    fontVariantNumeric: "tabular-nums",
  },
  scoreSlash: { color: "#a8a8ad", fontSize: "16px", fontWeight: 500 },
  scoreLabel: {
    fontSize: "7.5px",
    textTransform: "uppercase",
    letterSpacing: "0.14em",
    color: "#75757b",
    marginTop: "2px",
  },
  classPill: (color) => ({
    display: "inline-block",
    fontSize: "8px",
    fontWeight: 700,
    letterSpacing: "0.06em",
    padding: "3px 9px",
    borderRadius: "12px",
    background: color.bg,
    color: color.fg,
    marginTop: "5px",
  }),
  thesis: {
    fontSize: "10.5px",
    lineHeight: 1.5,
    marginTop: "10px",
    paddingTop: "10px",
    borderTop: "1px solid #ebeae5",
    color: "#15161a",
    fontWeight: 400,
  },
  thesisLabel: {
    fontSize: "7.5px",
    textTransform: "uppercase",
    letterSpacing: "0.14em",
    color: "#75757b",
    fontWeight: 600,
    marginRight: "8px",
  },
  quoteRow: {
    display: "grid",
    gridTemplateColumns: "repeat(6, 1fr)",
    gap: "8px",
  },
  quoteCard: {
    background: "#ffffff",
    borderRadius: "8px",
    padding: "10px 11px",
    boxShadow: "0 1px 0 rgba(20,20,30,0.04)",
  },
  qLabel: {
    fontSize: "7px",
    textTransform: "uppercase",
    letterSpacing: "0.12em",
    color: "#75757b",
    marginBottom: "4px",
  },
  qValue: {
    fontSize: "15px",
    fontWeight: 600,
    letterSpacing: "-0.02em",
    fontVariantNumeric: "tabular-nums",
  },
  qSub: { fontSize: "7.5px", color: "#75757b", marginTop: "2px" },
  body: {
    display: "grid",
    gridTemplateColumns: "1fr 1.45fr",
    gap: "10px",
    flex: 1,
  },
  sectionTitle: {
    fontSize: "8px",
    textTransform: "uppercase",
    letterSpacing: "0.16em",
    color: "#75757b",
    fontWeight: 700,
    marginBottom: "10px",
  },
  factorRow: {
    display: "grid",
    gridTemplateColumns: "1fr 38px",
    alignItems: "center",
    gap: "10px",
    marginBottom: "8px",
  },
  factorLabel: { display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "3px" },
  factorName: { fontSize: "9.5px", fontWeight: 600, display: "flex", alignItems: "center", gap: "5px" },
  factorDot: (c) => ({ width: "7px", height: "7px", borderRadius: "50%", background: c }),
  factorWeight: { fontSize: "7.5px", color: "#75757b" },
  bar: { height: "7px", background: "#f0eee8", borderRadius: "4px", overflow: "hidden" },
  barFill: (c, p) => ({ height: "100%", width: `${p}%`, background: c, borderRadius: "4px" }),
  factorScore: {
    textAlign: "right",
    fontSize: "13px",
    fontWeight: 700,
    fontVariantNumeric: "tabular-nums",
  },
  badgeWrap: { display: "flex", gap: "5px", flexWrap: "wrap" },
  badge: (tone) => {
    const map = {
      up: { bg: "#e6f4eb", color: "#0f5b30", border: "#cfeadb" },
      down: { bg: "#fce9e6", color: "#8a1f15", border: "#f3cfc9" },
      warn: { bg: "#fbf3df", color: "#6e530b", border: "#f1e3b8" },
      info: { bg: "#e6edf7", color: "#1a3868", border: "#cfdaee" },
      neutral: { bg: "#f0eee8", color: "#3a3a40", border: "#e3e1da" },
    };
    const c = map[tone] || map.neutral;
    return {
      display: "inline-flex",
      alignItems: "center",
      gap: "5px",
      padding: "3px 8px",
      borderRadius: "12px",
      fontSize: "8px",
      background: c.bg,
      color: c.color,
      border: `1px solid ${c.border}`,
    };
  },
  badgeLabel: { textTransform: "uppercase", letterSpacing: "0.08em", opacity: 0.7, fontWeight: 600 },
  badgeValue: { fontWeight: 700 },
  retGrid: { display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "6px", marginTop: "8px" },
  retCell: {
    background: "#f5f4f1",
    borderRadius: "6px",
    padding: "6px 4px",
    textAlign: "center",
  },
  retVal: (pos) => ({
    fontSize: "12px",
    fontWeight: 700,
    fontVariantNumeric: "tabular-nums",
    color: pos ? "#0f5b30" : "#8a1f15",
    whiteSpace: "nowrap",
  }),
  retLabel: { fontSize: "7px", color: "#75757b", textTransform: "uppercase", letterSpacing: "0.1em", marginTop: "1px" },
  indGroup: { marginBottom: "9px" },
  indHeader: {
    display: "flex", justifyContent: "space-between", alignItems: "center",
    paddingBottom: "4px", marginBottom: "4px",
    borderBottom: "1px solid #ebeae5",
  },
  indTitle: (c) => ({
    fontSize: "9px",
    fontWeight: 700,
    letterSpacing: "0.06em",
    textTransform: "uppercase",
    color: c,
  }),
  indWeight: { fontSize: "7.5px", color: "#75757b", fontWeight: 600 },
  indLine: {
    display: "grid",
    gridTemplateColumns: "1fr 50px 32px",
    alignItems: "center",
    gap: "6px",
    padding: "2px 0",
    fontSize: "8.5px",
  },
  indLabel: { color: "#3a3a40" },
  indVal: { fontVariantNumeric: "tabular-nums", textAlign: "right", fontWeight: 600, fontSize: "9px" },
  pctTrack: { height: "4px", background: "#f0eee8", borderRadius: "2px" },
  pctFill: (p, c) => ({ height: "100%", width: `${p}%`, background: c, borderRadius: "2px" }),
  rangeBar: {
    position: "relative",
    height: "6px",
    background: "#f0eee8",
    borderRadius: "4px",
    margin: "8px 0 4px",
  },
  rangeMarker: (p) => ({
    position: "absolute",
    left: `calc(${p}% - 4px)`,
    top: "-2px",
    width: "8px",
    height: "10px",
    background: "#15161a",
    borderRadius: "2px",
  }),
  rangeLabels: { display: "flex", justifyContent: "space-between", fontSize: "7.5px", color: "#75757b" },
  peerRow: { display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "8px" },
  peerCard: {
    background: "#ffffff",
    borderRadius: "8px",
    padding: "8px 10px",
    boxShadow: "0 1px 0 rgba(20,20,30,0.04)",
  },
  peerHead: { display: "flex", justifyContent: "space-between", alignItems: "baseline" },
  peerTicker: { fontSize: "11px", fontWeight: 700 },
  peerScore: { fontSize: "13px", fontWeight: 700, fontVariantNumeric: "tabular-nums" },
  peerName: { fontSize: "8px", color: "#75757b", marginTop: "2px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
  peerFoot: { display: "flex", justifyContent: "space-between", marginTop: "4px", fontSize: "8px" },
  footer: {
    display: "flex", justifyContent: "space-between",
    fontSize: "7.5px", color: "#a8a8ad",
    letterSpacing: "0.1em", textTransform: "uppercase",
    paddingTop: "4px",
  },
  smaCard: { display: "flex", flexDirection: "column", gap: "3px" },
  smaSignal: (tone) => ({
    fontWeight: 700, fontSize: "11.5px",
    color: tone === "up" ? "#0f5b30" : tone === "down" ? "#8a1f15" : "#15161a",
  }),
};

function classChipColor(c) {
  if (c.startsWith("A")) return { bg: "#0f5b30", fg: "#ffffff" };
  if (c.startsWith("B+")) return { bg: "#1a3868", fg: "#ffffff" };
  if (c.startsWith("B")) return { bg: "#3a3a40", fg: "#ffffff" };
  return { bg: "#75757b", fg: "#ffffff" };
}

function CardBadge({ label, value, tone = "neutral" }) {
  return (
    <span style={cardStyles.badge(tone)}>
      <span style={cardStyles.badgeLabel}>{label}</span>
      <span style={cardStyles.badgeValue}>{value}</span>
    </span>
  );
}

function CardFactsheet({ ticker, dense, showPeers }) {
  const t = TICKERS[ticker];
  const peers = PEERS[ticker] || [];
  const cls = classChipColor(t.classification);
  const rangePct = ((t.last_price - t.range_52w_low) / (t.range_52w_high - t.range_52w_low)) * 100;
  const smaTone = t.sma_signal.includes("GOLDEN") ? "up" : t.sma_signal.includes("DEATH") ? "down" : t.sma_signal.includes(">") ? "info" : "neutral";

  const factors = [
    { label: "Value", score: t.value_score, weight: 25, color: "#5b8def" },
    { label: "Quality", score: t.quality_score, weight: 27, color: "#22a06b" },
    { label: "Growth", score: t.growth_score, weight: 15, color: "#d97757" },
    { label: "Momentum", score: t.momentum_score, weight: 18, color: "#8b5cf6" },
    { label: "Low Vol", score: t.lowvol_score, weight: 15, color: "#0891b2" },
  ];

  return (
    <div style={{ ...cardStyles.page, padding: dense ? "24px 24px 18px" : "32px 32px 24px", gap: dense ? "8px" : "10px" }}>
      <div style={cardStyles.topBar}>
        <span style={cardStyles.brand}>
          <span style={cardStyles.dot} />
          Meeder &amp; Seifer · Multi-Faktor
        </span>
        <span>Aktien-Factsheet · 04.05.2026</span>
      </div>

      <div style={{ ...cardStyles.card, ...cardStyles.headerCard }}>
        <div>
          <div style={cardStyles.ticker}>{t.ticker}</div>
          <div style={cardStyles.name}>{t.name}</div>
          <div style={cardStyles.metaPills}>
            <span style={cardStyles.pill}>{t.sector}</span>
            <span style={cardStyles.pill}>{t.industry}</span>
            <span style={cardStyles.pill}>{t.region}</span>
          </div>
        </div>
        <div style={cardStyles.scoreSide}>
          <div style={cardStyles.scoreNum}>
            {fmtDE(t.total_score, 1)}<span style={cardStyles.scoreSlash}> / 100</span>
          </div>
          <div style={cardStyles.scoreLabel}>Gesamt-Score</div>
          <div style={cardStyles.classPill(cls)}>{t.classification}</div>
          <div style={{ fontSize: "7.5px", color: "#75757b", marginTop: "5px" }}>
            Sektor #{t.sector_rank}/{t.sector_total} · Ind. #{t.industry_rank}/{t.industry_total}
          </div>
        </div>
      </div>

      <div style={cardStyles.card}>
        <div>
          <span style={cardStyles.thesisLabel}>These</span>
          <span style={{ fontSize: "10.5px", lineHeight: 1.5 }}>{t.thesis}</span>
        </div>
      </div>

      <div style={cardStyles.quoteRow}>
        <div style={cardStyles.quoteCard}>
          <div style={cardStyles.qLabel}>Kurs USD</div>
          <div style={cardStyles.qValue}>{fmtPrice(t.last_price)}</div>
          <div style={{ ...cardStyles.qSub, color: t.ret_1m >= 0 ? "#0f5b30" : "#8a1f15", fontWeight: 600 }}>
            {fmtPct(t.ret_1m, true, 1)} · 1M
          </div>
        </div>
        <div style={cardStyles.quoteCard}>
          <div style={cardStyles.qLabel}>Marktkap.</div>
          <div style={cardStyles.qValue}>{fmtMcap(t.market_cap)}</div>
          <div style={cardStyles.qSub}>USD</div>
        </div>
        <div style={{ ...cardStyles.quoteCard, gridColumn: "span 2" }}>
          <div style={cardStyles.qLabel}>52-Wochen-Range</div>
          <div style={cardStyles.rangeBar}>
            <div style={cardStyles.rangeMarker(rangePct)} />
          </div>
          <div style={cardStyles.rangeLabels}>
            <span>{fmtPrice(t.range_52w_low)}</span>
            <span style={{ color: "#15161a", fontWeight: 600 }}>{fmtPrice(t.last_price)}</span>
            <span>{fmtPrice(t.range_52w_high)}</span>
          </div>
        </div>
        <div style={cardStyles.quoteCard}>
          <div style={cardStyles.qLabel}>Beta · Vol</div>
          <div style={cardStyles.qValue}>{fmtDE(t.beta, 2)}</div>
          <div style={cardStyles.qSub}>{fmtPct(t.volatility_1y, false, 1)} 1J</div>
        </div>
        <div style={cardStyles.quoteCard}>
          <div style={cardStyles.qLabel}>Empfehlung</div>
          <div style={{ ...cardStyles.qValue, color: t.recommendation.includes("BUY") ? "#0f5b30" : t.recommendation === "SELL" ? "#8a1f15" : "#3a3a40" }}>{t.recommendation}</div>
          <div style={cardStyles.qSub}>Filter {t.filter_ok}</div>
        </div>
      </div>

      <div style={cardStyles.body}>
        <div style={cardStyles.card}>
          <div style={cardStyles.sectionTitle}>Faktor-Profil</div>
          {factors.map(f => (
            <div key={f.label} style={cardStyles.factorRow}>
              <div>
                <div style={cardStyles.factorLabel}>
                  <span style={cardStyles.factorName}>
                    <span style={cardStyles.factorDot(f.color)} />{f.label}
                  </span>
                  <span style={cardStyles.factorWeight}>Gewicht {f.weight} %</span>
                </div>
                <div style={cardStyles.bar}>
                  <div style={cardStyles.barFill(f.color, f.score)} />
                </div>
              </div>
              <div style={cardStyles.factorScore}>{fmtDE(f.score, 0)}</div>
            </div>
          ))}

          <div style={{ ...cardStyles.sectionTitle, marginTop: "12px" }}>Signal · Filter</div>
          <div style={cardStyles.badgeWrap}>
            <CardBadge label="Piotroski" value={`${t.piotroski}/9`} tone={t.piotroski >= 5 ? "up" : "down"} />
            <CardBadge label="Altman Z" value={fmtDE(t.altman_z, 2)} tone={t.altman_z >= 1.8 ? "up" : "down"} />
            <CardBadge label="Filter" value={t.filter_ok === "JA" ? "bestanden" : "nicht bestanden"} tone={t.filter_ok === "JA" ? "up" : "down"} />
          </div>

          <div style={{ marginTop: "10px" }}>
            <div style={cardStyles.qLabel}>SMA-Signal</div>
            <div style={cardStyles.smaSignal(smaTone)}>{t.sma_signal}</div>
            <div style={{ fontSize: "8px", color: "#75757b", marginTop: "1px" }}>
              SMA-50 {fmtPrice(t.sma_50)} · SMA-200 {fmtPrice(t.sma_200)}
            </div>
          </div>

          <div style={{ marginTop: "10px" }}>
            <div style={cardStyles.qLabel}>Rückblicksfenster</div>
            <div style={cardStyles.retGrid}>
              {[["1M", "ret_1m"], ["3M", "ret_3m"], ["6M", "ret_6m"], ["12M", "ret_12m"]].map(([l, k]) => (
                <div key={k} style={cardStyles.retCell}>
                  <div style={cardStyles.retVal(t[k] >= 0)}>{fmtPct(t[k], true, 1)}</div>
                  <div style={cardStyles.retLabel}>{l}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div style={cardStyles.card}>
          <div style={cardStyles.sectionTitle}>Kennzahlen</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px 14px" }}>
            {Object.entries(INDICATORS).map(([name, group]) => (
              <div key={name} style={cardStyles.indGroup}>
                <div style={cardStyles.indHeader}>
                  <span style={cardStyles.indTitle(group.color)}>{name}</span>
                  <span style={cardStyles.indWeight}>{group.weight}%</span>
                </div>
                {group.items.map(item => {
                  const v = t[item.key];
                  const pct = pctFor(t.ticker, item.key);
                  return (
                    <div key={item.key} style={cardStyles.indLine}>
                      <span style={cardStyles.indLabel}>{item.label}</span>
                      <span style={cardStyles.indVal}>{fmtIndicator(v, item.fmt)}</span>
                      <div style={cardStyles.pctTrack}>
                        <div style={cardStyles.pctFill(pct, group.color)} />
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
          <div style={{ ...cardStyles.sectionTitle, marginBottom: "6px" }}>Comparables</div>
          <div style={cardStyles.peerRow}>
            {peers.slice(0, 4).map(p => (
              <div key={p.ticker} style={cardStyles.peerCard}>
                <div style={cardStyles.peerHead}>
                  <span style={cardStyles.peerTicker}>{p.ticker}</span>
                  <span style={cardStyles.peerScore}>{fmtDE(p.score, 1)}</span>
                </div>
                <div style={cardStyles.peerName}>{p.name}</div>
                <div style={cardStyles.peerFoot}>
                  <span style={{ color: "#75757b" }}>{p.classification}</span>
                  <span style={{ color: p.ret_12m >= 0 ? "#0f5b30" : "#8a1f15", fontWeight: 700 }}>
                    {fmtPct(p.ret_12m, true, 1)}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={cardStyles.footer}>
        <span>Quelle Koyfin · M&amp;S Multi-Faktor-Modell</span>
        <span>Vertraulich</span>
      </div>
    </div>
  );
}

window.CardFactsheet = CardFactsheet;
