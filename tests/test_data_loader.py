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
