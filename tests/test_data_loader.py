"""Tests für ``load_koyfin_csv`` — insbesondere das Verwerfen von Gruppen-Überschriften."""

from __future__ import annotations

from app.core.data_loader import load_koyfin_csv


def test_group_headers_are_dropped():
    csv = (
        "Ticker,Name,Last Price,1d Chg %\n"
        "MSCI World,,,\n"
        "MSFT,Microsoft,380,0.01\n"
        "ORCL,Oracle,140,-0.005\n"
        "Unclassified,,,\n"
        "SAP,SAP SE,200,0.002\n"
        "Watch,,,\n"
        "ADBE,Adobe,500,0.015\n"
    )

    df = load_koyfin_csv(csv.encode("utf-8"))

    assert list(df["ticker"]) == ["MSFT", "ORCL", "SAP", "ADBE"]
    for header in ("MSCI World", "Unclassified", "Watch"):
        assert header not in set(df["ticker"])


def test_regular_rows_pass_through():
    csv = (
        "Ticker,Name,Last Price,1d Chg %\n"
        "MSFT,Microsoft,380,0.01\n"
        "ORCL,Oracle,140,-0.005\n"
    )

    df = load_koyfin_csv(csv.encode("utf-8"))

    assert len(df) == 2
    assert set(df["ticker"]) == {"MSFT", "ORCL"}


def test_sma20_absent_yields_nan_column():
    csv = (
        "Ticker,Name,Last Price,1d Chg %\n"
        "MSFT,Microsoft,380,0.01\n"
    )

    df = load_koyfin_csv(csv.encode("utf-8"))

    assert "sma_20" in df.columns
    assert df["sma_20"].isna().all()


def _base_57_row() -> tuple[list[str], list[str]]:
    """57-Spalten-Zeile mit gesetzten Werten für die Positions-Prüfung."""
    from app.core.schema import KOYFIN_COLUMNS

    headers = [c.upper() for c in KOYFIN_COLUMNS]
    values = ["MSFT", "Microsoft", "Tech", "SW", "US"] + ["1"] * (
        len(KOYFIN_COLUMNS) - 8
    ) + ["375", "340", "2026-07-14"]
    # Plausibler Kurs, sonst schlägt der Kurs/SMA-200-Sentinel an.
    values[headers.index("LAST_PRICE")] = "380"
    return headers, values


def test_sma20_detected_mid_table_without_shifting_base_columns():
    headers, values = _base_57_row()
    # SMA (20D) mitten in den Export eingefügt (Position beliebig).
    headers = headers[:30] + ["SMA (20D)"] + headers[30:]
    values = values[:30] + ["390"] + values[30:]
    csv = ",".join(headers) + "\n" + ",".join(values) + "\n"

    df = load_koyfin_csv(csv.encode("utf-8"))

    assert df["sma_20"].iloc[0] == 390
    # Basisspalten dürfen sich nicht verschieben.
    assert df["sma_50"].iloc[0] == 375
    assert df["sma_200"].iloc[0] == 340
    assert df["export_date"].iloc[0] == "2026-07-14"


def test_sma20_detected_as_last_column():
    headers, values = _base_57_row()
    csv = ",".join(headers + ["sma_20"]) + "\n" + ",".join(values + ["390"]) + "\n"

    df = load_koyfin_csv(csv.encode("utf-8"))

    assert df["sma_20"].iloc[0] == 390
    assert df["sma_50"].iloc[0] == 375


def test_sma20_percent_variant_is_ignored():
    headers, values = _base_57_row()
    # Die %-Variante ist eine Distanz, keine SMA-Preisspalte.
    headers = headers[:30] + ["SMA % (20D)"] + headers[30:]
    values = values[:30] + ["0.05"] + values[30:]
    csv = ",".join(headers) + "\n" + ",".join(values) + "\n"

    df = load_koyfin_csv(csv.encode("utf-8"))

    assert df["sma_20"].isna().all()
    # Ohne Extraktion verschöbe die Zusatzspalte alles — die Basisspalten
    # bleiben aber nur korrekt, wenn sie ignoriert-durchgereicht wird; hier
    # zählt allein, dass sma_20 nicht fälschlich belegt wird.


def test_fwd_rev_growth_detected_without_shifting_base_columns():
    headers, values = _base_57_row()
    # Erwartetes Umsatzwachstum mitten im Export (Position beliebig).
    headers = headers[:25] + ["Est. Revenue CAGR (3Y)"] + headers[25:]
    values = values[:25] + ["0.12"] + values[25:]
    csv = ",".join(headers) + "\n" + ",".join(values) + "\n"

    df = load_koyfin_csv(csv.encode("utf-8"))

    assert df["fwd_rev_growth"].iloc[0] == 0.12
    assert df["sma_50"].iloc[0] == 375
    assert df["sma_200"].iloc[0] == 340
    assert df["export_date"].iloc[0] == "2026-07-14"


def test_fwd_rev_growth_absent_yields_nan_column():
    headers, values = _base_57_row()
    csv = ",".join(headers) + "\n" + ",".join(values) + "\n"

    df = load_koyfin_csv(csv.encode("utf-8"))

    assert "fwd_rev_growth" in df.columns
    assert df["fwd_rev_growth"].isna().all()


def test_eps_revision_header_not_mistaken_for_fwd_rev_growth():
    headers, values = _base_57_row()
    # Realer Koyfin-Header für EPS-Revisions enthält "Est." und "Revision" —
    # darf NICHT als Forward-Umsatzwachstum extrahiert werden (das würde die
    # positionale Zuordnung aller Folgespalten zerstören).
    headers[26] = "EPS Est. Revision % (3M)"
    csv = ",".join(headers) + "\n" + ",".join(values) + "\n"

    df = load_koyfin_csv(csv.encode("utf-8"))

    assert df["fwd_rev_growth"].isna().all()
    assert df["sma_50"].iloc[0] == 375
    assert df["export_date"].iloc[0] == "2026-07-14"


# Echte Header-Zeile eines Koyfin-Exports (57 Spalten: 56 Basisspalten OHNE
# "Export Date" + angehängtem "Est Rev CAGR (1Y)"). Regressionsbasis für den
# Spalten-Verschiebungs-Bug: "EPS Est Avg Rev % (FY1E - 3M)" enthält
# "rev"+"est" (Koyfin kürzt "Revision" zu "Rev") und wurde fälschlich als
# Forward-Umsatzwachstum extrahiert — alle Folgespalten verrutschten.
_REAL_HEADERS = (
    "Ticker,Name,Sector,Industry,Region,Market Cap,Last Price,P/E (LTM),"
    "P/B (LTM),P/S (LTM),P/FCF (LTM),EV/EBITDA (LTM),PEG (NTM),"
    "Div Yield (TTM),Return On Equity % (LTM),Return on Assets (ROA) % (LTM),"
    "ROIC (LTM),Gross Profit Margin % (LTM),EBIT Margin % (LTM),"
    "Total Debt / Equity (LTM),EBIT / Interest Expense (LTM),"
    "Current Ratio (LTM),Total Revenues/CAGR (3Y TTM),"
    "Basic EPS - CO/CAGR (3Y TTM),FCF/CAGR (3Y TTM),EPS - Est YoY % (FY1E),"
    "EPS Est Avg Rev % (FY1E - 3M),Total Return (1M),Total Return (3M),"
    "Total Return (6M),Total Return (1Y),Beta (1Y),Volatility (1Y),"
    "52W High,52W Low,Altman Z-Score (LTM),Net Income - (IS) (LTM),"
    "Net Income - (IS) (-1FY),CFO (LTM),CFO (-1FY),Total Assets (LTM),"
    "Total Assets (-1FY),Total Debt (LTM),Total Debt (-1FY),"
    "Total Current Assets (LTM),Total Current Liabilities (LTM),"
    "Total Current Assets (-1FY),Total Current Liabilities (-1FY),"
    "Shrs Out,Shrs Out (-1FY),Total Revenues (LTM),"
    "Cost of Goods Sold/Total (LTM),Total Revenues (-1FY),"
    "Cost of Goods Sold/Total (-1FY),SMA (50D),SMA (200D),Est Rev CAGR (1Y)"
)


def _real_header_row() -> tuple[list[str], list[str]]:
    headers = _REAL_HEADERS.split(",")
    assert len(headers) == 57
    values = ["MSFT", "Microsoft", "Tech", "SW", "US", "2500000", "380"]
    # Indizes 7..25: Multiples/Margen/CAGRs — Marker 7.0..25.0.
    values += [str(float(i)) for i in range(7, 26)]
    values += [
        "0.02",   # 26 EPS Est Avg Rev % (3M) = eps_revisions_3m
        "0.03",   # 27 Total Return (1M)
        "0.08",   # 28 Total Return (3M)
        "0.15",   # 29 Total Return (6M)
        "0.30",   # 30 Total Return (1Y)
        "0.9",    # 31 Beta
        "28.4",   # 32 Volatility (Prozentwert)
        "400",    # 33 52W High
        "300",    # 34 52W Low
        "3.5",    # 35 Altman
    ]
    # Indizes 36..53: Piotroski-Rohdaten — Marker 36.0..53.0.
    values += [str(float(i)) for i in range(36, 54)]
    values += ["375", "340", "0.12"]  # SMA-50, SMA-200, Est Rev CAGR (1Y)
    assert len(values) == 57
    return headers, values


def test_real_koyfin_headers_no_column_shift():
    """Regression: Der EPS-Revisions-Header darf nicht als Forward-Umsatz
    extrahiert werden — sonst verrutschen alle Folgespalten (SMA-200 bekam
    CAGR-Werte → Distanzen in Millionen %)."""
    headers, values = _real_header_row()
    csv = ",".join(headers) + "\n" + ",".join(values) + "\n"

    df = load_koyfin_csv(csv.encode("utf-8"))

    row = df.iloc[0]
    assert row["eps_revisions_3m"] == 0.02
    assert row["ret_1m"] == 0.03
    assert row["ret_12m"] == 0.30
    assert row["beta"] == 0.9
    assert row["volatility_1y"] == 0.284
    assert row["sma_50"] == 375
    assert row["sma_200"] == 340
    # Die echte Est-Rev-Spalte am Ende wird als fwd_rev_growth erkannt.
    assert row["fwd_rev_growth"] == 0.12
    # Export ohne "Export Date" → Spalte NaN (Snapshot-Datum via Dateiname).
    assert df["export_date"].isna().all()


def test_real_koyfin_headers_with_export_date_and_mid_table_est_rev():
    """Variante: 58 Spalten mit Export Date am Ende und Est-Rev-Spalte
    mitten im Export."""
    headers, values = _real_header_row()
    headers, values = headers[:-1], values[:-1]  # Est Rev CAGR entfernen
    headers = headers[:26] + ["Est Rev CAGR (1Y)"] + headers[26:] + ["Export Date"]
    values = values[:26] + ["0.12"] + values[26:] + ["2026-08-07"]
    csv = ",".join(headers) + "\n" + ",".join(values) + "\n"

    df = load_koyfin_csv(csv.encode("utf-8"))

    row = df.iloc[0]
    assert row["fwd_rev_growth"] == 0.12
    assert row["eps_revisions_3m"] == 0.02
    assert row["sma_200"] == 340
    assert row["export_date"] == "2026-08-07"


def test_eps_est_avg_rev_header_not_matched():
    from app.core.data_loader import _match_fwd_rev_growth

    def norm(s: str) -> str:
        import re

        return re.sub(r"[^a-z0-9]", "", s.lower())

    assert not _match_fwd_rev_growth(
        "EPS Est Avg Rev % (FY1E - 3M)", norm("EPS Est Avg Rev % (FY1E - 3M)")
    )
    assert not _match_fwd_rev_growth(
        "Total Revenues/CAGR (3Y TTM)", norm("Total Revenues/CAGR (3Y TTM)")
    )
    assert _match_fwd_rev_growth("Est Rev CAGR (1Y)", norm("Est Rev CAGR (1Y)"))
    assert _match_fwd_rev_growth(
        "Revenue Est. Growth (NTM)", norm("Revenue Est. Growth (NTM)")
    )


def test_shifted_columns_rejected_by_plausibility_check():
    """Sentinel: Landet eine Wachstumsrate in der SMA-200-Spalte (verrutschte
    Zuordnung), wird der Import laut abgelehnt statt still persistiert."""
    import pytest

    headers, values = _real_header_row()
    values[55] = "0.08"  # SMA (200D) enthält plötzlich eine Wachstumsrate
    csv = ",".join(headers) + "\n" + ",".join(values) + "\n"

    with pytest.raises(ValueError, match="Spaltenzuordnung unplausibel"):
        load_koyfin_csv(csv.encode("utf-8"))


def test_volatility_scaled_to_decimal():
    # Koyfin liefert Volatilität als Prozentwert (z. B. 28,4 = 28,4 %).
    # Der Loader muss durch 100 teilen, damit alle Prozent-Felder konsistent
    # als Dezimalanteil im DataFrame liegen.
    columns = (
        "ticker,name,sector,industry,region,market_cap,last_price,pe,pb,ps,"
        "pfcf,ev_ebitda,peg,div_yield,roe,roa,roic,gross_margin,op_margin,"
        "debt_equity,int_coverage,current_ratio,rev_cagr_3y,eps_cagr_3y,"
        "fcf_cagr_3y,fwd_eps_growth,eps_revisions_3m,ret_1m,ret_3m,ret_6m,"
        "ret_12m,beta,volatility_1y"
    )
    csv = (
        f"{columns}\n"
        "MSFT,Microsoft,Tech,SW,US,2500000,380,32,11,11,35,22,2.0,0.008,"
        "0.38,0.16,0.25,0.68,0.42,0.5,35,1.8,0.16,0.17,0.15,0.13,0.02,"
        "0.03,0.08,0.15,0.35,0.9,28.4\n"
    )

    df = load_koyfin_csv(csv.encode("utf-8"))

    # 28,4 (Prozent) → 0,284 (Dezimalanteil)
    assert df["volatility_1y"].iloc[0] == 0.284


def test_v2_optional_columns_detected_without_shifting_base_columns():
    """Die fünf v2-Zusatzspalten (Spec 1.2) werden per Header erkannt und
    verschieben die positionalen Basisspalten nicht; ``ipo_date`` bleibt
    Text."""
    headers, values = _base_57_row()
    extra_headers = [
        "EV / EBIT (LTM)",
        "Net Debt / EBITDA (LTM)",
        "FCF Yield (EV)",
        "ADV (3M)",
        "IPO Date",
    ]
    extra_values = ["18.5", "1.4", "0.04", "12.3", "2001-05-15"]
    headers = headers[:20] + extra_headers + headers[20:]
    values = values[:20] + extra_values + values[20:]
    csv = ",".join(headers) + "\n" + ",".join(values) + "\n"

    df = load_koyfin_csv(csv.encode("utf-8"))

    assert df["ev_ebit"].iloc[0] == 18.5
    assert df["net_debt_ebitda"].iloc[0] == 1.4
    assert df["fcf_yield"].iloc[0] == 0.04
    assert df["adv_3m"].iloc[0] == 12.3
    assert df["ipo_date"].iloc[0] == "2001-05-15"
    # Basisspalten unverschoben.
    assert df["sma_50"].iloc[0] == 375
    assert df["sma_200"].iloc[0] == 340
    assert df["export_date"].iloc[0] == "2026-07-14"


def test_v2_optional_columns_absent_yield_nan():
    csv = "Ticker,Name,Last Price\nMSFT,Microsoft,380\n"
    df = load_koyfin_csv(csv.encode("utf-8"))
    for col in ("ev_ebit", "net_debt_ebitda", "fcf_yield", "adv_3m", "ipo_date"):
        assert col in df.columns
        assert df[col].isna().all()
