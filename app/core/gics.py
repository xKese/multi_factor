"""GICS-Hierarchie: Zuordnung der Koyfin-Industrie zur Industriegruppe.

Grundlage der sequenziellen Neutralisierung (Spec „Sequenzielle
Neutralisierung", Abschnitt 1): Die Koyfin-Spalte ``industry`` liegt auf
GICS-Ebene 3 (Industry, 74 Werte in GICS 2023; Ergebnis der Ebenenprüfung
siehe ``MODEL_DESCRIPTION.md``). Die statische Tabelle
``data/reference/gics_map.csv`` (versioniert) ordnet jede Industrie ihrer
Industriegruppe (Ebene 2, 25 Werte) und ihrem Sektor (Ebene 1) zu.

Zuordnung beim Import (:func:`assign_industry_group`): Left-Join über den
normalisierten Industrienamen (Trim, Kleinschreibung, ``&`` → ``and``,
Satzzeichen entfernt, Mehrfach-Leerzeichen zusammengezogen). Nicht
zuordenbare Werte erhalten ``industry_group = sector`` (Fallback) — die
Diagnose listet sie als Warnung; eine Sektorabweichung zwischen Koyfin und
Mapping wird als Info gelistet, der Koyfin-Sektor bleibt maßgeblich.

Für Alpha-Vantage-Snapshots (Backtest) liefert
``data/reference/av_industry_to_gics.csv`` ein manuell gepflegtes Mapping
der SIC-basierten ``OVERVIEW.Industry`` auf die GICS-Industrie; unvollständige
Zuordnungen fallen ebenfalls auf den Sektor.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .diagnostics import SEV_INFO, SEV_WARNING, Diagnostic

_REFERENCE_DIR = Path(__file__).resolve().parents[2] / "data" / "reference"
GICS_MAP_PATH = _REFERENCE_DIR / "gics_map.csv"
AV_INDUSTRY_MAP_PATH = _REFERENCE_DIR / "av_industry_to_gics.csv"

# Herkunft der Industriegruppe je Titel (Spalte ``industry_group_source``):
# ``gics`` = über gics_map zugeordnet, ``av`` = über das Alpha-Vantage-
# Mapping, ``sector`` = Fallback auf den Sektor (nicht zuordenbar).
SOURCE_GICS = "gics"
SOURCE_AV = "av"
SOURCE_SECTOR = "sector"


def normalize_industry_name(value: object) -> str:
    """Vergleichsschlüssel eines Industrienamens.

    Trim, Kleinschreibung, ``&`` → ``and``, Entfernung von Satzzeichen
    (Kommata, Klammern, Bindestriche — Koyfin schreibt z. B. „Hotels
    Restaurants and Leisure" für GICS „Hotels, Restaurants & Leisure"),
    Zusammenziehen doppelter Leerzeichen.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip().lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=4)
def _load_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str).fillna("")


def load_gics_map(path: str | Path | None = None) -> pd.DataFrame:
    """Mapping-Tabelle (Spalten ``industry, industry_group, sector,
    gics_version``) mit zusätzlicher Schlüsselspalte ``key``."""
    frame = _load_csv(str(path or GICS_MAP_PATH)).copy()
    frame["key"] = frame["industry"].map(normalize_industry_name)
    return frame


def load_av_industry_map(path: str | Path | None = None) -> pd.DataFrame:
    """Alpha-Vantage-Mapping (``av_industry, gics_industry``) mit ``key``."""
    frame = _load_csv(str(path or AV_INDUSTRY_MAP_PATH)).copy()
    frame["key"] = frame["av_industry"].map(normalize_industry_name)
    return frame


def industry_group_names(gics_map: pd.DataFrame | None = None) -> frozenset[str]:
    """Alle kanonischen Industriegruppen-Namen des Mappings."""
    frame = gics_map if gics_map is not None else load_gics_map()
    return frozenset(str(v) for v in frame["industry_group"].unique() if v)


def _text_column(df: pd.DataFrame, column: str) -> pd.Series:
    if column in df.columns:
        return df[column].map(lambda v: "" if v is None or pd.isna(v) else str(v))
    return pd.Series("", index=df.index, dtype="object")


def assign_industry_group(
    df: pd.DataFrame, gics_map: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Ergänzt ``industry_group`` (und ``industry_group_source``) je Titel.

    Left-Join ``industry`` → ``industry_group`` über die normalisierten
    Namen. Nicht zuordenbare Werte erhalten den Koyfin-Sektor als
    Industriegruppe (Quelle ``sector``). Liegt ``industry_group`` bereits
    mit Werten vor (z. B. aus einem Export mit Gruppen-Spalte oder aus dem
    Alpha-Vantage-Mapping), werden vorhandene Werte auf die kanonische
    Schreibweise des Mappings gehoben und nur leere Einträge zugeordnet.
    Idempotent; verändert das übergebene DataFrame nicht.
    """
    out = df.copy()
    gmap = gics_map if gics_map is not None else load_gics_map()
    lookup = dict(zip(gmap["key"], gmap["industry_group"]))
    group_by_key = {normalize_industry_name(g): g for g in industry_group_names(gmap)}

    industry = _text_column(out, "industry")
    sector = _text_column(out, "sector")
    mapped = industry.map(normalize_industry_name).map(lookup)

    existing = _text_column(out, "industry_group")
    existing_source = _text_column(out, "industry_group_source")
    # Vorhandene Gruppenwerte kanonisieren (z. B. „Software and Services"
    # → „Software & Services"); unbekannte Werte bleiben unverändert.
    canonical = existing.map(lambda v: group_by_key.get(normalize_industry_name(v), v))
    has_existing = canonical != ""

    group = mapped.where(mapped.notna(), None)
    group = pd.Series(group, index=out.index, dtype="object")
    source = pd.Series(SOURCE_GICS, index=out.index, dtype="object")
    unmapped = group.isna() | (group == "")
    group = group.mask(unmapped, sector)
    source = source.mask(unmapped, SOURCE_SECTOR)

    # Bereits vorhandene Werte haben Vorrang. Herkunft übernehmen; fehlt sie
    # (Export mit Gruppen-Spalte ohne Herkunft), gilt ein kanonischer
    # Gruppenname als ``gics``, jeder andere Wert als Sektor-Fallback —
    # so bildet ein zurückgefallener Titel nie eine eigene Pseudo-Gruppe.
    inferred = canonical.map(
        lambda v: SOURCE_GICS if normalize_industry_name(v) in group_by_key else SOURCE_SECTOR
    )
    keep_source = existing_source.where(existing_source != "", inferred)
    group = group.mask(has_existing, canonical)
    source = source.mask(has_existing, keep_source)

    out["industry_group"] = group.astype("object")
    out["industry_group_source"] = source.astype("object")
    return out


def assign_industry_group_av(
    industry: pd.Series,
    sector: pd.Series,
    av_map: pd.DataFrame | None = None,
    gics_map: pd.DataFrame | None = None,
) -> tuple[pd.Series, pd.Series]:
    """Industriegruppe aus der Alpha-Vantage-Industrie (SIC-Beschreibung).

    Zweistufig: ``av_industry`` → GICS-Industrie (``av_industry_to_gics``)
    → Industriegruppe (``gics_map``). Ohne Zuordnung: Sektor (Quelle
    ``sector``). Liefert (industry_group, industry_group_source).
    """
    amap = av_map if av_map is not None else load_av_industry_map()
    gmap = gics_map if gics_map is not None else load_gics_map()
    av_lookup = dict(zip(amap["key"], amap["gics_industry"]))
    g_lookup = dict(zip(gmap["key"], gmap["industry_group"]))
    keys = industry.map(normalize_industry_name)
    gics_industry = keys.map(av_lookup)
    group = gics_industry.map(lambda v: g_lookup.get(normalize_industry_name(v)) if isinstance(v, str) else None)
    group = pd.Series(group, index=industry.index, dtype="object")
    sector_text = sector.map(lambda v: "" if v is None or pd.isna(v) else str(v))
    unmapped = group.isna()
    source = pd.Series(SOURCE_AV, index=industry.index, dtype="object")
    group = group.mask(unmapped, sector_text)
    source = source.mask(unmapped, SOURCE_SECTOR)
    return group.astype("object"), source.astype("object")


def industry_group_diagnostics(
    df: pd.DataFrame, gics_map: pd.DataFrame | None = None
) -> list[Diagnostic]:
    """Diagnosen der Gruppenzuordnung (Spec 1.4 / 6).

    - Warnung je Industrie ohne Eintrag im Mapping (Fallback auf Sektor),
      mit Anzahl betroffener Titel.
    - Info je Industrie, deren Koyfin-Sektor vom Mapping-Sektor abweicht
      (der Koyfin-Wert bleibt maßgeblich).
    """
    diags: list[Diagnostic] = []
    if "industry_group" not in df.columns:
        return diags
    gmap = gics_map if gics_map is not None else load_gics_map()
    sector_lookup = dict(zip(gmap["key"], gmap["sector"]))

    industry = _text_column(df, "industry")
    sector = _text_column(df, "sector")
    source = _text_column(df, "industry_group_source")
    if not source.any():
        group_names = industry_group_names(gmap)
        source = df["industry_group"].map(
            lambda v: SOURCE_GICS if isinstance(v, str) and v in group_names else SOURCE_SECTOR
        )

    fallback = source == SOURCE_SECTOR
    if fallback.any():
        counts = industry[fallback].replace("", "(leer)").value_counts()
        for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            diags.append(
                Diagnostic(
                    SEV_WARNING,
                    "industry_not_in_gics_map",
                    f"Industrie nicht im GICS-Mapping: {name} ({int(n)} Titel) — "
                    "Industriegruppe = Sektor",
                )
            )

    mapped_sector = industry.map(normalize_industry_name).map(sector_lookup)
    deviates = (source == SOURCE_GICS) & mapped_sector.notna() & (mapped_sector != sector) & (sector != "")
    if deviates.any():
        pairs = pd.DataFrame(
            {"industry": industry[deviates], "koyfin": sector[deviates], "map": mapped_sector[deviates]}
        )
        for (name, koy, mapped), n in sorted(
            pairs.value_counts().items(), key=lambda kv: (-kv[1], kv[0])
        ):
            diags.append(
                Diagnostic(
                    SEV_INFO,
                    "industry_sector_deviation",
                    f"Sektorabweichung {name}: Koyfin \u201e{koy}\u201c \u2260 "
                    f"GICS-Mapping \u201e{mapped}\u201c ({int(n)} Titel) \u2014 "
                    "Koyfin-Sektor beibehalten",
                )
            )
    return diags


def industry_group_size_stats(df: pd.DataFrame, small_threshold: int = 20) -> dict | None:
    """Verteilung der Gruppengrößen (Min, Median, Max, Anzahl < Schwelle)."""
    if "industry_group" not in df.columns:
        return None
    groups = _text_column(df, "industry_group")
    sizes = groups[groups != ""].value_counts()
    if sizes.empty:
        return None
    return {
        "n_groups": int(len(sizes)),
        "min": int(sizes.min()),
        "median": float(sizes.median()),
        "max": int(sizes.max()),
        "n_small": int((sizes < small_threshold).sum()),
        "small": sorted(str(k) for k, v in sizes.items() if v < small_threshold),
    }
