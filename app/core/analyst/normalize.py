"""Tolerante Normalisierung von LLM-Werten (Enums, Zahlen, Schlüssel).

Ollama Cloud erzwingt das JSON-Schema nicht zuverlässig; Modelle liefern
Enum-Werte in anderer Schreibweise („Einstieg prüfen“, „bestätigt“),
Zahlen mit Dezimalkomma oder Prozent („0,7“, „70 %“) und gelegentlich
abweichende Schlüssel. Diese Helfer bilden solche Varianten auf die
erwarteten Werte ab, statt still Defaults einzusetzen.
"""

from __future__ import annotations

import re
import unicodedata

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def canon(value) -> str:
    """Kanonische Form: Umlaute → ae/oe/ue/ss, Akzente entfernt, Kleinschreibung,
    Nicht-Alphanumerisches → ``_`` (zusammengefasst, ohne Rand)."""
    if value is None:
        return ""
    text = str(value).translate(_UMLAUTS)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "_", text.lower())
    return text.strip("_")


def norm_enum(value, allowed, aliases: dict[str, str] | None = None) -> str | None:
    """Wert auf einen der ``allowed`` Werte abbilden oder ``None``.

    Reihenfolge: exakte kanonische Gleichheit → Alias-Tabelle → eindeutiger
    Präfix/Teilstring (in beide Richtungen, ab 4 Zeichen).
    """
    key = canon(value)
    if not key:
        return None
    table = {canon(a): a for a in allowed}
    if key in table:
        return table[key]
    if aliases:
        for alias, target in aliases.items():
            if canon(alias) == key and target in allowed:
                return target
    if len(key) >= 4:
        hits = [a for c, a in table.items() if c.startswith(key) or key.startswith(c) or key in c or c in key]
        if len(hits) == 1:
            return hits[0]
        # Mehrere Treffer: den mit dem längsten gemeinsamen Präfix nehmen, wenn eindeutig.
        if hits:
            scored = sorted(((len(_common_prefix(canon(a), key)), a) for a in hits), reverse=True)
            if len(scored) == 1 or scored[0][0] > scored[1][0]:
                return scored[0][1]
    return None


def _common_prefix(a: str, b: str) -> str:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return a[:n]


def norm_float(value, lo: float | None = None, hi: float | None = None) -> float | None:
    """Zahl aus float/int/str (Dezimalkomma, Prozent) oder ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
    else:
        text = str(value).strip().replace("\u00a0", "")
        if not text:
            return None
        percent = text.endswith("%")
        text = text.rstrip("%").strip()
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        elif "," in text and "." in text:
            text = text.replace(".", "").replace(",", ".")
        m = re.search(r"-?\d+(?:\.\d+)?", text)
        if not m:
            return None
        f = float(m.group(0))
        if percent:
            f = f / 100.0
    if f != f:  # NaN
        return None
    # Anteil als Prozentzahl geliefert („70“ statt 0,7) → skalieren; knapp
    # über 1 (z. B. 1,7) ist eher ein Ausreißer und wird nur geklemmt.
    if hi is not None and hi <= 1.0 and 5.0 <= f <= 100.0:
        f = f / 100.0
    if lo is not None:
        f = max(lo, f)
    if hi is not None:
        f = min(hi, f)
    return f


def pick(d: dict, *keys, default=None):
    """Ersten vorhandenen Schlüssel (kanonisch verglichen) zurückgeben."""
    if not isinstance(d, dict):
        return default
    wanted = [canon(k) for k in keys]
    for k, v in d.items():
        if canon(k) in wanted and v is not None:
            return v
    return default
