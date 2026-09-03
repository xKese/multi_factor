"""Diagnoseliste für Composite v2 und Portfoliokonstruktion.

Leitprinzip der Spec: Keine stillen Fallbacks. Jede Stelle, an der eine
Regel nicht angewendet werden kann (fehlende Daten, unerfüllbare
Restriktion), erzeugt einen Eintrag mit Schweregrad, der in UI und Report
sichtbar ist.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

SEV_ERROR = "Fehler"
SEV_WARNING = "Warnung"
SEV_INFO = "Info"

# Sortierreihenfolge: Fehler zuerst.
SEVERITY_ORDER: dict[str, int] = {SEV_ERROR: 0, SEV_WARNING: 1, SEV_INFO: 2}


@dataclass
class Diagnostic:
    """Ein Diagnoseeintrag.

    ``code`` ist ein stabiler, maschinenlesbarer Kurzname (z. B.
    ``"piotroski_na"``), ``message`` der deutsche Klartext für UI/Report,
    ``uid`` der betroffene Titel (``None`` = Universums-/Portfolioebene).
    """

    severity: str
    code: str
    message: str
    uid: str | None = None


def sort_diagnostics(diags: list[Diagnostic]) -> list[Diagnostic]:
    """Sortiert nach Schweregrad (Fehler → Warnung → Info), dann Code/uid."""
    return sorted(
        diags,
        key=lambda d: (SEVERITY_ORDER.get(d.severity, 9), d.code, d.uid or ""),
    )


def count_by_severity(diags: list[Diagnostic]) -> dict[str, int]:
    counts = {SEV_ERROR: 0, SEV_WARNING: 0, SEV_INFO: 0}
    for d in diags:
        counts[d.severity] = counts.get(d.severity, 0) + 1
    return counts


def diags_to_json(diags: list[Diagnostic]) -> str:
    return json.dumps([asdict(d) for d in diags], ensure_ascii=False)


def diags_from_json(payload: str | None) -> list[Diagnostic]:
    if not payload:
        return []
    try:
        raw = json.loads(payload)
    except (TypeError, ValueError):
        return []
    out: list[Diagnostic] = []
    for item in raw:
        if isinstance(item, dict) and "severity" in item and "message" in item:
            out.append(
                Diagnostic(
                    severity=str(item.get("severity")),
                    code=str(item.get("code", "")),
                    message=str(item.get("message")),
                    uid=item.get("uid"),
                )
            )
    return out
