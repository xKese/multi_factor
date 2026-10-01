"""Konstruktionsprofile: benannte Versionen der Portfoliokonstruktions-
Kriterien (``pc_*``-Settings) und ihre Zuordnung zu hochgeladenen
Portfolios.

Motivation: Verschiedene Portfolios haben verschiedene Risikoprofile
(z. B. konservativ mit engerem TE-Band und niedrigerem Gewichts-Cap,
dynamisch mit weniger Titeln und höherem Cap). Die globalen Einstellungen
bleiben der Standard; ein Profil überschreibt nur die Felder aus
``PC_PROFILE_FIELDS`` — Universumsfilter, Scoring und Benchmark-Stammdaten
bleiben global, weil sie beim Import für das gesamte Universum gelten.

Die Auflösung (:func:`resolve_construction_settings`) ist die gemeinsame
Quelle für Modellportfolio-Seite und CLI: Bestandsportfolio → zugeordnetes
Profil → wirksame Settings. Ohne Zuordnung (oder bei DB-Fehlern) gelten die
globalen Einstellungen unverändert.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import fields as dataclass_fields

from .config import PC_PROFILE_FIELDS, Settings

log = logging.getLogger(__name__)

# Anzeigename des Standards (keine Zuordnung → globale Einstellungen).
DEFAULT_PROFILE_LABEL = "Standard (globale Einstellungen)"

_FIELD_TYPES: dict[str, str] = {
    f.name: str(f.type) for f in dataclass_fields(Settings)
}


def profile_payload(settings: Settings) -> dict:
    """Die profilierbaren Felder eines Settings-Objekts als JSON-taugliches
    Dict (Listen kopiert, keine Referenzen auf das Original)."""
    out: dict = {}
    for name in PC_PROFILE_FIELDS:
        value = getattr(settings, name)
        out[name] = list(value) if isinstance(value, (list, tuple, set)) else value
    return out


def coerce_profile_value(name: str, value):
    """Wert eines Profilfelds auf den Typ des Settings-Felds bringen
    (JSON liefert z. B. ``int`` statt ``float`` oder Listen von Strings)."""
    ftype = _FIELD_TYPES.get(name, "")
    if ftype == "int":
        return int(value)
    if ftype == "float":
        return float(value)
    if ftype == "str":
        return str(value)
    if ftype == "list[int]":
        return [int(v) for v in value]
    return value


def apply_profile(settings: Settings, data: dict | None) -> Settings:
    """Kopie von ``settings`` mit den Profilwerten aus ``data``.

    Unbekannte Keys werden ignoriert (Profil aus einer älteren
    App-Version), fehlende Keys behalten den globalen Wert (Feld neu seit
    dem Speichern des Profils). Werte, die sich nicht auf den Feldtyp
    bringen lassen, werden mit Warnung übersprungen — kein stiller
    Abbruch, aber auch kein Absturz durch ein defektes Profil.
    """
    out = copy.deepcopy(settings)
    if not data:
        return out
    for name in PC_PROFILE_FIELDS:
        if name not in data:
            continue
        try:
            setattr(out, name, coerce_profile_value(name, data[name]))
        except (TypeError, ValueError) as exc:
            log.warning(
                "Profilwert %s=%r nicht übernommen (%s) — globaler Wert bleibt.",
                name, data[name], exc,
            )
    return out


def profile_differences(settings: Settings, data: dict | None) -> dict[str, tuple]:
    """Felder, in denen ``data`` von den globalen Settings abweicht:
    ``{feld: (global, profil)}`` — für Anzeige und Diagnosen."""
    if not data:
        return {}
    applied = apply_profile(settings, data)
    diff: dict[str, tuple] = {}
    for name in PC_PROFILE_FIELDS:
        base = getattr(settings, name)
        new = getattr(applied, name)
        if base != new:
            diff[name] = (base, new)
    return diff


def resolve_construction_settings(
    base: Settings, portfolio_id: int | None
) -> tuple[Settings, dict | None]:
    """Wirksame Konstruktions-Settings für das Bestandsportfolio
    ``portfolio_id``: das zugeordnete Profil auf ``base`` angewendet.

    Rückgabe ``(settings, profile)`` — ``profile`` ist der Katalogeintrag
    (``id, name, data, …``) oder ``None``, wenn keine Zuordnung existiert.
    Fail-open: DB-Fehler ergeben ``(base, None)``; ``base`` selbst wird nie
    verändert.
    """
    if portfolio_id is None:
        return base, None
    try:
        from .persistence import get_pc_profile, get_pc_profile_assignment

        profile_id = get_pc_profile_assignment(int(portfolio_id))
        profile = get_pc_profile(profile_id) if profile_id is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Konstruktionsprofil für Portfolio %s nicht ermittelbar: %s",
            portfolio_id, exc,
        )
        return base, None
    if profile is None:
        return base, None
    return apply_profile(base, profile.get("data")), profile
