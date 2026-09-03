"""Prozess-lokaler In-Memory-Store für Universum, Settings und M&S-Portfolio."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

import pandas as pd

from .config import Settings
from .scoring import compute_scores


def _load_overrides_safe() -> pd.DataFrame | None:
    """Override-Register fail-open laden (DB-Fehler blockieren keinen Import)."""
    try:
        from .persistence import load_overrides

        return load_overrides()
    except Exception:  # noqa: BLE001
        return None


def _load_tactical_safe() -> dict[str, float] | None:
    """Jüngste taktische Faktor-Timing-Gewichte, fail-open (nur relevant im
    Modus ``factor_timing_mode = "active"``, Spec 9)."""
    try:
        from .persistence import load_factor_timing_history

        history = load_factor_timing_history(limit=1)
        return history[0]["weights"] if history else None
    except Exception:  # noqa: BLE001
        return None


@dataclass
class AppState:
    settings: Settings = field(default_factory=Settings)
    raw: pd.DataFrame = field(default_factory=pd.DataFrame)
    scored: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Diagnoseliste des letzten v2-Laufs (Spec 0.2) — für UI und Reports.
    v2_diagnostics: list = field(default_factory=list)
    # Fallback-Liste, solange kein Portfolio hochgeladen/persistiert wurde.
    # Gepflegt wird das Portfolio über den Koyfin-Watchlist-Upload auf
    # /portfolios (→ set_ms_portfolio + save_ms_portfolio).
    ms_portfolio: list[str] = field(
        default_factory=lambda: [
            "AIR", "ALV", "GOOGL", "AMZN", "AAPL", "SAN", "BRKB", "DHR",
            "AIR.PA", "NVDA", "MSFT",
        ]
    )
    ms_portfolio_names: dict[str, str] = field(default_factory=dict)
    # Importierte Portfoliogewichte (Dezimalanteile, Summe 1,0). Leer, wenn
    # der Upload keine Gewichtsspalte hatte → ``portfolio_weights()`` fällt
    # auf Gleichgewichtung zurück.
    ms_portfolio_weights: dict[str, float] = field(default_factory=dict)
    # Roh-Einträge des Uploads (ticker, name, weight) — Basis für die
    # lazy uid-Auflösung in resolve_portfolio().
    ms_portfolio_entries: pd.DataFrame = field(default_factory=pd.DataFrame)
    ms_portfolio_imported_at: object | None = None
    _lock: Lock = field(default_factory=Lock, repr=False)

    def recompute(self) -> None:
        with self._lock:
            if self.raw.empty:
                self.scored = pd.DataFrame()
                self.v2_diagnostics = []
                return
            scored = compute_scores(self.raw, self.settings)
            # Composite v2 wird bei jedem Import zusätzlich berechnet
            # (Parallelbetrieb, Spec 0.1) und landet über ``save_universe``
            # mit im PIT-Archiv. Eine ungültige Gewichtssumme ist ein
            # Import-Fehler und propagiert (Spec 2.4).
            from .scoring_v2 import compute_scores_v2, map_tactical_to_v2
            from .signal_events import snapshot_date_from_universe

            tactical = None
            if self.settings.factor_timing_mode == "active":
                tactical = map_tactical_to_v2(
                    _load_tactical_safe(), self.settings
                )
            scored, diags = compute_scores_v2(
                scored,
                self.settings,
                overrides=_load_overrides_safe(),
                snapshot_date=snapshot_date_from_universe(self.raw, None),
                tactical_weights=tactical,
            )
            self.scored = scored
            self.v2_diagnostics = diags

    def set_raw(self, df: pd.DataFrame) -> None:
        # uid nachrüsten für Universen aus Bestands-DBs (vor Einführung der
        # Spalte gespeichert); frische Importe bringen sie bereits mit.
        if not df.empty and "uid" not in df.columns:
            from .uid import assign_uids

            df = assign_uids(df)
        self.raw = df
        self.recompute()

    def set_ms_portfolio(self, df: pd.DataFrame, imported_at=None) -> None:
        """Ersetzt das M&S-Portfolio aus einem Upload-/DB-Frame.

        ``df`` braucht ``ticker`` (+ optional ``name``, ``weight``,
        ``imported_at``). Die Roh-Einträge werden zusätzlich als
        ``ms_portfolio_entries`` gehalten — die Auflösung gegen das Universum
        (uid) passiert lazy in :meth:`resolve_portfolio`, weil das Portfolio
        beim App-Start vor dem Universum geladen wird. Kein Recompute nötig —
        ``scored`` ist portfoliounabhängig.
        """
        self.ms_portfolio = [str(t) for t in df["ticker"].tolist()]
        names = (
            df["name"].fillna("")
            if "name" in df.columns
            else pd.Series("", index=df.index)
        )
        self.ms_portfolio_names = {
            str(t): str(n) for t, n in zip(df["ticker"], names)
        }
        entries = pd.DataFrame(
            {
                "ticker": [str(t) for t in df["ticker"]],
                "name": [str(n) for n in names],
            }
        )
        self.ms_portfolio_weights = {}
        if "weight" in df.columns:
            weights = pd.to_numeric(df["weight"], errors="coerce")
            if weights.notna().any() and float(weights.fillna(0).sum()) > 0:
                self.ms_portfolio_weights = {
                    str(t): float(w)
                    for t, w in zip(df["ticker"], weights)
                    if pd.notna(w) and w > 0
                }
                entries["weight"] = weights.to_numpy()
        self.ms_portfolio_entries = entries
        if imported_at is not None:
            self.ms_portfolio_imported_at = imported_at
        elif "imported_at" in df.columns and len(df):
            self.ms_portfolio_imported_at = df["imported_at"].iloc[0]

    def resolve_portfolio(self) -> pd.DataFrame:
        """Watchlist-Einträge gegen das Universum auflösen (uid je Position).

        Rückgabe: DataFrame mit ``ticker, name, weight, uid, status``:

        - ``ok``: genau eine Universums-Zeile passt (eindeutiger Ticker, oder
          bei Ticker-Kollision per Namensabgleich aufgelöst) — ``uid`` gesetzt.
        - ``missing``: Ticker nicht im Universum — ``uid`` = Ticker (für
          Anzeige/Risiko-Modul unverändert nutzbar).
        - ``ambiguous``: Ticker mehrfach im Universum und per Name nicht
          auflösbar — ``uid`` = Ticker; solche Positionen dürfen NICHT per
          ``isin`` gematcht werden (sonst Doppelzählung beider Kandidaten).
        """
        from .uid import slugify_name

        entries = self.ms_portfolio_entries
        if entries is None or entries.empty:
            entries = pd.DataFrame(
                {
                    "ticker": self.ms_portfolio,
                    "name": [
                        self.ms_portfolio_names.get(t, "") for t in self.ms_portfolio
                    ],
                }
            )
        out = entries.copy()
        if "weight" not in out.columns:
            out["weight"] = pd.NA

        scored = self.scored
        uids: list[str] = []
        statuses: list[str] = []
        for _, e in out.iterrows():
            ticker = str(e["ticker"]).strip()
            name = str(e.get("name") or "").strip()
            if scored is None or scored.empty or "ticker" not in scored.columns:
                uids.append(ticker)
                statuses.append("missing")
                continue
            hits = scored[scored["ticker"].astype(str).str.upper() == ticker.upper()]
            if hits.empty:
                uids.append(ticker)
                statuses.append("missing")
                continue
            if len(hits) == 1:
                uids.append(str(hits.iloc[0].get("uid") or ticker))
                statuses.append("ok")
                continue
            # Ticker-Kollision: per Namens-Slug auflösen (exakt, sonst Prefix
            # in beide Richtungen — "Santander" matcht "Banco Santander").
            slug = slugify_name(name)
            resolved = None
            if slug:
                cand_slugs = hits["name"].map(slugify_name)
                exact = hits[cand_slugs == slug]
                if len(exact) == 1:
                    resolved = exact.iloc[0]
                else:
                    prefix = hits[
                        cand_slugs.str.startswith(slug)
                        | pd.Series(
                            [slug.startswith(c) and bool(c) for c in cand_slugs],
                            index=hits.index,
                        )
                    ]
                    if len(prefix) == 1:
                        resolved = prefix.iloc[0]
            if resolved is not None:
                uids.append(str(resolved.get("uid") or ticker))
                statuses.append("ok")
            else:
                uids.append(ticker)
                statuses.append("ambiguous")
        out["uid"] = uids
        out["status"] = statuses
        return out

    def portfolio_weights(self) -> dict[str, float]:
        """Portfoliogewichte als Dezimalanteile (Summe 1,0), keyed by uid.

        Importierte Gewichte haben Vorrang; ohne Gewichtsspalte im Upload
        wird gleichgewichtet (1/N über alle Positionen). Auf Summe 1,0
        renormalisiert, damit Rundungsreste im Import nicht durchschlagen.
        Für eindeutige Ticker ist die uid identisch zum Ticker (bisheriges
        Verhalten); nicht auflösbare Einträge behalten ihren Ticker.
        """
        resolved = self.resolve_portfolio()
        if resolved.empty:
            return {}
        weights = pd.to_numeric(resolved["weight"], errors="coerce")
        if weights.notna().any() and float(weights.fillna(0).sum()) > 0:
            pairs = [
                (str(u), float(w))
                for u, w in zip(resolved["uid"], weights)
                if pd.notna(w) and w > 0
            ]
            total = sum(w for _, w in pairs)
            if total > 0:
                return {u: w / total for u, w in pairs}
        n = len(resolved)
        return {str(u): 1.0 / n for u in resolved["uid"]}

    def load_from_db(self) -> bool:
        from .persistence import load_ms_portfolio, load_settings, load_universe

        stored_settings = load_settings()
        if stored_settings is not None:
            self.settings = stored_settings

        portfolio = load_ms_portfolio()
        if portfolio is not None and not portfolio.empty:
            self.set_ms_portfolio(portfolio)

        df = load_universe()
        if df is None or df.empty:
            return False
        self.set_raw(df)
        return True


STATE = AppState()
