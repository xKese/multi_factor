"""Globale Test-Voreinstellungen.

Der In-App-Scheduler des KI-Analysten startet in ``create_app()`` einen
Daemon-Thread — in Tests unterdrückt (``KI_ANALYST_SCHEDULER=0``), damit
Seiten-Tests keine Hintergrundläufe gegen echte Keys auslösen können.
"""

from __future__ import annotations

import os

os.environ.setdefault("KI_ANALYST_SCHEDULER", "0")
