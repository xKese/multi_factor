"""KI-Analyst: kontinuierlicher Analyst-Agent (Ollama Cloud + Alpha Vantage).

Dash-freies Paket. Einstiegspunkt ist :func:`run_analyst` in
:mod:`app.core.analyst.runner`; der In-App-Scheduler liegt in
:mod:`app.core.analyst.scheduler`, die CLI in :mod:`app.tools.analyst`.
Die Re-Exports werden lazy aufgelöst, damit Teilmodule (Client, Store)
ohne den Runner importierbar bleiben.
"""

from __future__ import annotations

__all__ = ["AnalystConfigError", "run_analyst"]


def __getattr__(name: str):
    if name in __all__:
        from . import runner

        return getattr(runner, name)
    raise AttributeError(name)
