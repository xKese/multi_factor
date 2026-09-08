"""Alpha-Vantage-Zugriff für den Backtest: Token-Bucket-Limiter, Retry,
Parquet-Cache mit SQLite-Manifest, Feldmapping (Spec 2).

Bewusst getrennt vom produktiven ``app.core.av_client`` (Risikomodul, Cache
in der App-Datenbank): Der Backtest braucht Fundamentals, LISTING_STATUS und
ein Volumen von ~6.000 Aufrufen, das in Parquet-Dateien je Endpunkt und
Ticker besser aufgehoben ist als in der App-DB. Der API-Key kommt
ausschließlich aus ``ALPHAVANTAGE_API_KEY`` und erscheint weder in Logs noch
im Cache noch in Reports.

``requests`` und ``time`` sind Modul-Attribute, damit Tests sie per
monkeypatch ersetzen können.
"""

from __future__ import annotations

import io
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

BASE_URL = "https://www.alphavantage.co/query"
_TIMEOUT = (10, 120)
RETRY_DELAYS: tuple[float, ...] = (2.0, 8.0, 30.0)
_RATE_LIMIT_KEYS = ("Note", "Information")
_ERROR_KEY = "Error Message"

# Endpunkte (Spec 2.1) → Cache-Unterverzeichnis.
EP_LISTING = "listing_status"
EP_PRICES = "prices"
EP_INCOME = "income_statement"
EP_BALANCE = "balance_sheet"
EP_CASHFLOW = "cash_flow"
EP_OVERVIEW = "overview"
EP_FX = "fx"
EP_FACTORS = "factors"

STATUS_OK = "ok"
STATUS_NO_DATA = "no_data"

FUNDAMENTAL_ENDPOINTS: tuple[str, ...] = (EP_INCOME, EP_BALANCE, EP_CASHFLOW)


class BacktestAVError(RuntimeError):
    """``kind``: ``rate_limit`` | ``error`` | ``parse`` | ``no_key``."""

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


def api_key() -> str | None:
    key = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
    return key or None


# ── Rate-Limit (Spec 2.2) ────────────────────────────────────────────────


class SlidingWindowLimiter:
    """Rate-Limiter mit gleitendem 60-Sekunden-Fenster: nie mehr als
    ``requests_per_minute`` Aufrufe in einem beliebigen 60-s-Fenster.

    Strenger als ein klassischer Token-Bucket (der nach einer Pause einen
    doppelten Burst zuließe). Kein Sleep-Raten: Ein Aufruf wartet exakt bis
    der älteste Aufruf im Fenster 60 s alt ist. ``clock``/``sleep`` sind
    injizierbar (Tests).
    """

    WINDOW = 60.0

    def __init__(self, requests_per_minute: int, clock=None, sleep=None) -> None:
        self.capacity = max(1, int(requests_per_minute))
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._stamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            while self._stamps and now - self._stamps[0] >= self.WINDOW:
                self._stamps.popleft()
            if len(self._stamps) >= self.capacity:
                # 1 ms Sicherheitsmarge gegen Gleitkomma-Rundung am Fensterrand.
                wait = self.WINDOW - (now - self._stamps[0]) + 1e-3
                if wait > 0:
                    self._sleep(wait)
                now = self._clock()
                while self._stamps and now - self._stamps[0] >= self.WINDOW:
                    self._stamps.popleft()
            self._stamps.append(now)


# Name aus der Spec (2.2) als Alias.
TokenBucketLimiter = SlidingWindowLimiter


# ── Cache (Spec 2.3) ─────────────────────────────────────────────────────


@dataclass
class ManifestEntry:
    endpoint: str
    ticker: str
    fetched_at: datetime
    rows: int
    status: str


class BacktestCache:
    """Parquet je Endpunkt und Schlüssel unter ``<root>/<endpoint>/<key>.parquet``
    plus Manifest ``cache_manifest`` (SQLite, ``<root>/manifest.sqlite``)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._db_path = self.root / "manifest.sqlite"
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache_manifest ("
                "endpoint TEXT NOT NULL, ticker TEXT NOT NULL, "
                "fetched_at TEXT NOT NULL, rows INTEGER NOT NULL, "
                "status TEXT NOT NULL, PRIMARY KEY (endpoint, ticker))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    @staticmethod
    def _safe(key: str) -> str:
        return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(key))

    def path(self, endpoint: str, key: str) -> Path:
        return self.root / endpoint / f"{self._safe(key)}.parquet"

    def entry(self, endpoint: str, key: str) -> ManifestEntry | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT endpoint, ticker, fetched_at, rows, status FROM "
                "cache_manifest WHERE endpoint = ? AND ticker = ?",
                (endpoint, str(key)),
            ).fetchone()
        if row is None:
            return None
        return ManifestEntry(row[0], row[1], datetime.fromisoformat(row[2]), int(row[3]), row[4])

    def entries(self, endpoint: str | None = None) -> pd.DataFrame:
        with self._lock, self._connect() as conn:
            sql = "SELECT endpoint, ticker, fetched_at, rows, status FROM cache_manifest"
            params: tuple = ()
            if endpoint:
                sql += " WHERE endpoint = ?"
                params = (endpoint,)
            df = pd.read_sql_query(sql, conn, params=params)
        if not df.empty:
            df["fetched_at"] = pd.to_datetime(df["fetched_at"])
        return df

    def is_fresh(self, endpoint: str, key: str, ttl_days: int, now: datetime | None = None) -> bool:
        entry = self.entry(endpoint, key)
        if entry is None:
            return False
        now = now or datetime.now()
        return entry.fetched_at + timedelta(days=int(ttl_days)) > now

    def write(
        self,
        endpoint: str,
        key: str,
        df: pd.DataFrame | None,
        status: str = STATUS_OK,
        fetched_at: datetime | None = None,
    ) -> None:
        fetched_at = fetched_at or datetime.now()
        path = self.path(endpoint, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = 0
        if df is not None and status == STATUS_OK:
            out = df.copy()
            if isinstance(out.index, pd.DatetimeIndex) or out.index.name:
                out = out.reset_index()
            out.to_parquet(path, index=False)
            rows = int(len(df))
        elif path.exists():
            path.unlink()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO cache_manifest (endpoint, ticker, fetched_at, rows, status) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (endpoint, ticker) DO UPDATE SET "
                "fetched_at = excluded.fetched_at, rows = excluded.rows, "
                "status = excluded.status",
                (endpoint, str(key), fetched_at.isoformat(), rows, status),
            )

    def read(self, endpoint: str, key: str) -> pd.DataFrame | None:
        """Gecachter Frame oder ``None`` (fehlt / ``no_data``)."""
        path = self.path(endpoint, key)
        entry = self.entry(endpoint, key)
        if entry is None or entry.status != STATUS_OK or not path.exists():
            return None
        df = pd.read_parquet(path)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
        return df

    def latest_fetch(self) -> datetime | None:
        df = self.entries()
        if df.empty:
            return None
        return df["fetched_at"].max().to_pydatetime()


# ── Feldmapping (Spec 2.4) ───────────────────────────────────────────────

INCOME_MAP: dict[str, tuple[str, ...]] = {
    "revenue": ("totalRevenue",),
    "cogs": ("costOfRevenue",),
    "ebit": ("ebit", "operatingIncome"),
    "ebitda": ("ebitda",),
    "net_income": ("netIncome",),
    "interest_expense": ("interestExpense",),
    "depreciation": ("depreciationAndAmortization",),
}
BALANCE_MAP: dict[str, tuple[str, ...]] = {
    "total_assets": ("totalAssets",),
    "total_liab": ("totalLiabilities",),
    "equity": ("totalShareholderEquity",),
    "total_debt": ("shortLongTermDebtTotal",),
    "long_term_debt": ("longTermDebt",),
    "short_term_debt": ("shortTermDebt",),
    "cash": ("cashAndCashEquivalentsAtCarryingValue", "cashAndShortTermInvestments"),
    "current_assets": ("totalCurrentAssets",),
    "current_liab": ("totalCurrentLiabilities",),
    "retained_earnings": ("retainedEarnings",),
    "shares_out": ("commonStockSharesOutstanding",),
}
CASHFLOW_MAP: dict[str, tuple[str, ...]] = {
    "ocf": ("operatingCashflow",),
    "capex": ("capitalExpenditures",),
}

FUNDAMENTAL_COLUMNS: tuple[str, ...] = (
    "revenue", "cogs", "ebit", "ebitda", "net_income", "interest_expense",
    "total_assets", "total_liab", "equity", "total_debt", "cash",
    "current_assets", "current_liab", "retained_earnings", "shares_out",
    "ocf", "capex", "fcf",
)


def _num(value) -> float:
    """``"None"``/``None``/leer → NaN, sonst float."""
    if value is None:
        return np.nan
    if isinstance(value, str):
        v = value.strip()
        if v in ("", "None", "none", "null", "-", "NaN"):
            return np.nan
        try:
            return float(v)
        except ValueError:
            return np.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return np.nan


def _pick(report: dict, keys: tuple[str, ...]) -> float:
    for key in keys:
        val = _num(report.get(key))
        if not np.isnan(val):
            return val
    return np.nan


def _map_reports(reports: list[dict], mapping: dict[str, tuple[str, ...]]) -> pd.DataFrame:
    rows = []
    for rep in reports or []:
        fiscal = rep.get("fiscalDateEnding")
        if not fiscal:
            continue
        row = {"fiscal_date": pd.Timestamp(fiscal)}
        for name, keys in mapping.items():
            row[name] = _pick(rep, keys)
        rows.append(row)
    if not rows:
        return pd.DataFrame(columns=["fiscal_date", *mapping])
    df = pd.DataFrame(rows).drop_duplicates("fiscal_date", keep="first")
    return df.sort_values("fiscal_date").reset_index(drop=True)


def map_income_statement(payload: dict) -> pd.DataFrame:
    df = _map_reports(payload.get("annualReports", []), INCOME_MAP)
    if not df.empty:
        # Fallback ebitda = ebit + D&A (Spec 2.4).
        fallback = df["ebit"] + df["depreciation"]
        df["ebitda"] = df["ebitda"].where(df["ebitda"].notna(), fallback)
    return df.drop(columns=["depreciation"], errors="ignore")


def map_balance_sheet(payload: dict) -> pd.DataFrame:
    df = _map_reports(payload.get("annualReports", []), BALANCE_MAP)
    if not df.empty:
        fallback = df["long_term_debt"].fillna(0.0) + df["short_term_debt"].fillna(0.0)
        both_missing = df["long_term_debt"].isna() & df["short_term_debt"].isna()
        fallback = fallback.where(~both_missing)
        df["total_debt"] = df["total_debt"].where(df["total_debt"].notna(), fallback)
    return df.drop(columns=["long_term_debt", "short_term_debt"], errors="ignore")


def map_cash_flow(payload: dict) -> pd.DataFrame:
    df = _map_reports(payload.get("annualReports", []), CASHFLOW_MAP)
    if not df.empty:
        df["fcf"] = df["ocf"] - df["capex"].abs()
    return df


def merge_fundamentals(
    income: pd.DataFrame | None,
    balance: pd.DataFrame | None,
    cashflow: pd.DataFrame | None,
) -> pd.DataFrame:
    """Outer-Join der drei Abschlüsse auf ``fiscal_date`` mit allen
    ``FUNDAMENTAL_COLUMNS`` (fehlende Spalten NaN)."""
    frames = [f for f in (income, balance, cashflow) if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=["fiscal_date", *FUNDAMENTAL_COLUMNS])
    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on="fiscal_date", how="outer")
    for col in FUNDAMENTAL_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    out["fiscal_date"] = pd.to_datetime(out["fiscal_date"])
    return out.sort_values("fiscal_date").reset_index(drop=True)[
        ["fiscal_date", *FUNDAMENTAL_COLUMNS]
    ]


def map_overview(payload: dict) -> pd.DataFrame:
    """OVERVIEW → eine Zeile: name, sector, industry, country, asset_type,
    exchange, currency."""
    if not payload or "Symbol" not in payload:
        return pd.DataFrame()

    def _s(key: str) -> str | None:
        v = payload.get(key)
        if v is None or str(v).strip() in ("", "None", "-"):
            return None
        return str(v).strip()

    return pd.DataFrame(
        [
            {
                "symbol": _s("Symbol"),
                "name": _s("Name"),
                "sector": _s("Sector"),
                "industry": _s("Industry"),
                "country": _s("Country"),
                "asset_type": _s("AssetType"),
                "exchange": _s("Exchange"),
                "currency": _s("Currency"),
            }
        ]
    )


def parse_prices(payload: dict, context: str = "") -> pd.DataFrame:
    """TIME_SERIES_DAILY_ADJUSTED → DataFrame (DatetimeIndex ``date``,
    Spalten close, adj_close, volume, dividend, split)."""
    block = payload.get("Time Series (Daily)")
    if not isinstance(block, dict) or not block:
        raise BacktestAVError(
            f"{context}: 'Time Series (Daily)' fehlt (Keys: {sorted(payload)})",
            kind="parse",
        )
    first = next(iter(block.values()))
    if "5. adjusted close" not in first:
        raise BacktestAVError(
            f"{context}: '5. adjusted close' fehlt (Felder: {sorted(first)})",
            kind="parse",
        )
    rows = {
        pd.Timestamp(day): {
            "close": _num(r.get("4. close")),
            "adj_close": _num(r.get("5. adjusted close")),
            "volume": _num(r.get("6. volume")),
            "dividend": _num(r.get("7. dividend amount")),
            "split": _num(r.get("8. split coefficient")),
        }
        for day, r in block.items()
    }
    df = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    df.index.name = "date"
    return df


def parse_fx(payload: dict, context: str = "") -> pd.DataFrame:
    block = payload.get("Time Series FX (Daily)")
    if not isinstance(block, dict) or not block:
        raise BacktestAVError(
            f"{context}: 'Time Series FX (Daily)' fehlt (Keys: {sorted(payload)})",
            kind="parse",
        )
    rows = {pd.Timestamp(day): {"close": _num(r.get("4. close"))} for day, r in block.items()}
    df = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    df.index.name = "date"
    return df


def parse_listing_status(csv_text: str) -> pd.DataFrame:
    """LISTING_STATUS (CSV) → symbol, name, exchange, asset_type, ipo_date,
    delisting_date, status."""
    if not csv_text or not csv_text.strip():
        return pd.DataFrame(
            columns=["symbol", "name", "exchange", "asset_type", "ipo_date",
                     "delisting_date", "status"]
        )
    df = pd.read_csv(io.StringIO(csv_text), dtype=str)
    rename = {
        "symbol": "symbol", "name": "name", "exchange": "exchange",
        "assetType": "asset_type", "ipoDate": "ipo_date",
        "delistingDate": "delisting_date", "status": "status",
    }
    df = df.rename(columns=rename)
    for col in rename.values():
        if col not in df.columns:
            df[col] = None
    df["ipo_date"] = pd.to_datetime(df["ipo_date"], errors="coerce")
    df["delisting_date"] = pd.to_datetime(
        df["delisting_date"].replace({"null": None}), errors="coerce"
    )
    return df[list(rename.values())].dropna(subset=["symbol"]).reset_index(drop=True)


# ── Client ───────────────────────────────────────────────────────────────


class BacktestAVClient:
    """Idempotente ``fetch_*``-Methoden: Cache-Treffer innerhalb der TTL werden
    nicht erneut geladen; Antworten ohne erwartete Schlüssel werden als
    ``no_data`` gecacht (Spec 2.2/2.3)."""

    def __init__(
        self,
        cache: BacktestCache,
        requests_per_minute: int = 75,
        ttl_prices: int = 7,
        ttl_fundamentals: int = 90,
        ttl_listing: int = 365,
        ttl_no_data: int = 30,
        retry_attempts: int = 3,
        limiter: TokenBucketLimiter | None = None,
    ) -> None:
        self.cache = cache
        self.limiter = limiter or TokenBucketLimiter(requests_per_minute)
        self.ttl_prices = ttl_prices
        self.ttl_fundamentals = ttl_fundamentals
        self.ttl_listing = ttl_listing
        self.ttl_no_data = ttl_no_data
        self.retry_attempts = max(1, int(retry_attempts))
        self.api_calls = 0

    # ── HTTP ─────────────────────────────────────────────────────────────

    def _request(self, params: dict, as_text: bool = False):
        key = api_key()
        if not key:
            raise BacktestAVError("ALPHAVANTAGE_API_KEY ist nicht gesetzt", kind="no_key")
        query = {**params, "apikey": key}
        if not as_text:
            query["datatype"] = "json"
        # Log-Kontext ohne Key: nur Funktion und Symbol.
        context = f"{params.get('function')} {params.get('symbol', params.get('date', ''))}".strip()
        last = "unbekannter Fehler"
        for attempt in range(self.retry_attempts):
            if attempt:
                delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
                time.sleep(delay)
            self.limiter.acquire()
            self.api_calls += 1
            try:
                resp = requests.get(BASE_URL, params=query, timeout=_TIMEOUT)
            except requests.RequestException as exc:
                last = f"Netzwerkfehler: {type(exc).__name__}"
                log.info("AV %s: %s (Versuch %d)", context, last, attempt + 1)
                continue
            if resp.status_code != 200:
                last = f"HTTP {resp.status_code}"
                log.info("AV %s: %s (Versuch %d)", context, last, attempt + 1)
                continue
            if as_text:
                text = resp.text
                stripped = text.lstrip()
                if stripped.startswith("{"):
                    # Rate-Limit-/Fehlerhinweis kommt auch beim CSV-Endpunkt als JSON.
                    try:
                        data = resp.json()
                    except ValueError:
                        data = {}
                    if any(k in data for k in _RATE_LIMIT_KEYS):
                        last = "Rate-Limit"
                        log.info("AV %s: Rate-Limit (Versuch %d)", context, attempt + 1)
                        continue
                    if _ERROR_KEY in data:
                        raise BacktestAVError(f"API-Fehler {context}: {data[_ERROR_KEY]}")
                return text
            try:
                data = resp.json()
            except ValueError:
                last = "Antwort ist kein JSON"
                log.info("AV %s: %s (Versuch %d)", context, last, attempt + 1)
                continue
            if not isinstance(data, dict):
                last = "Antwort ist kein JSON-Objekt"
                continue
            if _ERROR_KEY in data:
                raise BacktestAVError(f"API-Fehler {context}: {data[_ERROR_KEY]}")
            if any(k in data for k in _RATE_LIMIT_KEYS) and len(data) == 1:
                last = "Rate-Limit"
                log.info("AV %s: Rate-Limit (Versuch %d)", context, attempt + 1)
                continue
            return data
        raise BacktestAVError(
            f"AV {context}: nach {self.retry_attempts} Versuchen aufgegeben — {last}",
            kind="rate_limit" if last == "Rate-Limit" else "error",
        )

    # ── generisches Cache-Fetch ──────────────────────────────────────────

    def _cached(self, endpoint: str, key: str, ttl: int, loader, force: bool = False):
        """Liefert (DataFrame|None, aus_cache: bool)."""
        entry = self.cache.entry(endpoint, key)
        if entry is not None and not force:
            ttl_eff = self.ttl_no_data if entry.status == STATUS_NO_DATA else ttl
            if self.cache.is_fresh(endpoint, key, ttl_eff):
                return self.cache.read(endpoint, key), True
        try:
            df = loader()
        except BacktestAVError as exc:
            if exc.kind == "parse":
                log.info("AV %s/%s: keine Daten (%s)", endpoint, key, exc.kind)
                self.cache.write(endpoint, key, None, status=STATUS_NO_DATA)
                return None, False
            raise
        if df is None or df.empty:
            self.cache.write(endpoint, key, None, status=STATUS_NO_DATA)
            return None, False
        self.cache.write(endpoint, key, df, status=STATUS_OK)
        return df, False

    # ── Endpunkte ────────────────────────────────────────────────────────

    def fetch_prices(self, ticker: str, force: bool = False) -> pd.DataFrame | None:
        def _load():
            data = self._request(
                {"function": "TIME_SERIES_DAILY_ADJUSTED", "symbol": ticker,
                 "outputsize": "full"}
            )
            return parse_prices(data, f"TIME_SERIES_DAILY_ADJUSTED {ticker}")

        df, _ = self._cached(EP_PRICES, ticker, self.ttl_prices, _load, force)
        return df

    def fetch_fx(self, from_symbol: str = "USD", to_symbol: str = "EUR",
                 force: bool = False) -> pd.DataFrame | None:
        key = f"{from_symbol}{to_symbol}"

        def _load():
            data = self._request(
                {"function": "FX_DAILY", "from_symbol": from_symbol,
                 "to_symbol": to_symbol, "outputsize": "full"}
            )
            return parse_fx(data, f"FX_DAILY {key}")

        df, _ = self._cached(EP_FX, key, self.ttl_prices, _load, force)
        return df

    def _fetch_statement(self, endpoint: str, function: str, ticker: str, mapper,
                         force: bool) -> pd.DataFrame | None:
        def _load():
            data = self._request({"function": function, "symbol": ticker})
            if "annualReports" not in data:
                raise BacktestAVError(f"{function} {ticker}: annualReports fehlt", kind="parse")
            return mapper(data)

        df, _ = self._cached(endpoint, ticker, self.ttl_fundamentals, _load, force)
        return df

    def fetch_income_statement(self, ticker: str, force: bool = False):
        return self._fetch_statement(EP_INCOME, "INCOME_STATEMENT", ticker,
                                     map_income_statement, force)

    def fetch_balance_sheet(self, ticker: str, force: bool = False):
        return self._fetch_statement(EP_BALANCE, "BALANCE_SHEET", ticker,
                                     map_balance_sheet, force)

    def fetch_cash_flow(self, ticker: str, force: bool = False):
        return self._fetch_statement(EP_CASHFLOW, "CASH_FLOW", ticker,
                                     map_cash_flow, force)

    def fetch_overview(self, ticker: str, force: bool = False) -> pd.DataFrame | None:
        def _load():
            data = self._request({"function": "OVERVIEW", "symbol": ticker})
            df = map_overview(data)
            if df.empty:
                raise BacktestAVError(f"OVERVIEW {ticker}: Symbol fehlt", kind="parse")
            return df

        df, _ = self._cached(EP_OVERVIEW, ticker, self.ttl_fundamentals, _load, force)
        return df

    def fetch_listing_status(self, at: date | None, state: str = "active",
                             force: bool = False) -> pd.DataFrame | None:
        key = f"{state}_{at.isoformat()}" if at is not None else state

        def _load():
            params = {"function": "LISTING_STATUS", "state": state}
            if at is not None:
                params["date"] = at.isoformat()
            text = self._request(params, as_text=True)
            df = parse_listing_status(text)
            if df.empty:
                raise BacktestAVError(f"LISTING_STATUS {key}: leer", kind="parse")
            return df

        ttl = self.ttl_listing if at is not None else self.ttl_prices
        df, _ = self._cached(EP_LISTING, key, ttl, _load, force)
        return df

    def fetch_factor_file(self, name: str, url: str, force: bool = False) -> pd.DataFrame | None:
        """Kenneth-French-CSV (ZIP oder CSV) → Frame (Index ``date``)."""
        from .factor_regression import parse_french_csv

        def _load():
            resp = requests.get(url, timeout=_TIMEOUT)
            if resp.status_code != 200:
                raise BacktestAVError(f"Faktordatei {name}: HTTP {resp.status_code}")
            return parse_french_csv(resp.content)

        df, _ = self._cached(EP_FACTORS, name, self.ttl_listing, _load, force)
        return df
