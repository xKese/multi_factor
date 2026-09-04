"""UI-Helfer: Plotly-Theme, Layout-Bausteine, Formatter und Labels."""

from app.ui.command_palette import command_palette_layout
from app.ui.formatters import (
    PERCENT_FIELDS,
    fmt_de,
    fmt_indicator,
    fmt_int,
    fmt_market_cap,
    fmt_percent,
    fmt_signed_percent,
    parse_de,
)
from app.ui.labels import FACTOR_GROUP_LABELS, INDICATOR_LABELS, label_for
from app.ui.theme import (
    MS_DARK,
    MS_LIGHT,
    diagnostics_panel,
    factor_breakdown,
    kpi_band,
    ms_badge,
    panel,
    quote_header,
    register_plotly_templates,
    section_header,
    zone_badge,
)

__all__ = [
    "FACTOR_GROUP_LABELS",
    "INDICATOR_LABELS",
    "MS_DARK",
    "MS_LIGHT",
    "PERCENT_FIELDS",
    "command_palette_layout",
    "diagnostics_panel",
    "factor_breakdown",
    "fmt_de",
    "fmt_indicator",
    "fmt_int",
    "fmt_market_cap",
    "fmt_percent",
    "fmt_signed_percent",
    "kpi_band",
    "label_for",
    "ms_badge",
    "panel",
    "parse_de",
    "quote_header",
    "register_plotly_templates",
    "section_header",
    "zone_badge",
]
