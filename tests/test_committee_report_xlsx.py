"""Tests des Excel-Anhangs (``app.core.committee_report_xlsx``)."""

from __future__ import annotations

import io

import pytest

from tests.test_committee_report import env, report, scored_universe  # noqa: F401

openpyxl = pytest.importorskip("openpyxl")


def test_workbook_has_required_sheets_and_german_headers(report):  # noqa: F811
    from app.core import committee_report_xlsx as x

    data = x.build_committee_xlsx(report)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    names = wb.sheetnames
    for sheet in (
        x.SHEET_OVERVIEW, x.SHEET_UNIVERSE, x.SHEET_CANDIDATES, x.SHEET_SELL, x.SHEET_CHANGES_PREV,
        x.SHEET_CHANGES_MONTH, x.SHEET_ZONE_MATRIX, x.SHEET_SECTORS, x.SHEET_PORTFOLIO,
        x.SHEET_PORTFOLIO_KPI, x.SHEET_PORTFOLIO_EXPOSURE, x.SHEET_MODEL, x.SHEET_TRADES,
        x.SHEET_DATA_QUALITY, x.SHEET_PARAMETERS,
    ):
        assert sheet in names, sheet
    assert x.SHEET_RISK not in names and x.SHEET_AGENTS not in names
    assert all(len(n) <= 31 for n in names)

    uni = wb[x.SHEET_UNIVERSE]
    headers = [c.value for c in uni[1] if c.value]
    assert headers[:4] == ["UID", "Ticker", "Name", "Sektor"]
    assert all("_" not in h for h in headers)
    assert uni.max_row == report["universe"]["n"] + 1
    assert uni.freeze_panes == "A2"


def test_numeric_cells_keep_number_formats(report):  # noqa: F811
    from app.core import committee_report_xlsx as x

    wb = openpyxl.load_workbook(io.BytesIO(x.build_committee_xlsx(report)))
    pf = wb[x.SHEET_PORTFOLIO]
    headers = [c.value for c in pf[2]]
    weight_col = headers.index("Gewicht") + 1
    score_col = headers.index("Composite (v2)") + 1
    cell_w = pf.cell(row=3, column=weight_col)
    cell_s = pf.cell(row=3, column=score_col)
    assert isinstance(cell_w.value, float) and cell_w.number_format == x.FMT_PERCENT
    assert isinstance(cell_s.value, float) and cell_s.number_format == x.FMT_ONE
    trades = wb[x.SHEET_TRADES]
    t_headers = [c.value for c in trades[1]]
    assert "Aktion" in t_headers and "Δ Gewicht" in t_headers


def test_overview_sheet_lists_key_findings(report):  # noqa: F811
    from app.core import committee_report_xlsx as x

    wb = openpyxl.load_workbook(io.BytesIO(x.build_committee_xlsx(report)))
    ws = wb[x.SHEET_OVERVIEW]
    values = [str(c.value) for row in ws.iter_rows() for c in row if c.value is not None]
    assert "Key Findings" in values
    assert any("Testdepot" in v for v in values)
    assert any(f["text"] in values for f in report["key_findings"])
