"""Source layout of the INEP microdata and the canonical output schema.

The census files keep the same column *names* between editions, but the column
*order* changes (2024 added 23 columns in the middle of the file) and the
institutions file was renamed in 2022. Everything here selects columns by name,
so a new edition only breaks the build when a required column disappears.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# INEP says "latin-1", but some editions contain Windows-1252 characters in the
# 0x80-0x9F range (en dash, bullet), which strict Latin-1 decoders reject.
# cp1252 decodes every edition from 2010 to 2024. The extract step writes UTF-8.
SOURCE_ENCODING = "cp1252"
ENCODING = "utf-8"
DELIMITER = ";"

COURSE_FILE = "MICRODADOS_CADASTRO_CURSOS_{year}.CSV"
# The institutions file was renamed in the 2022 edition.
INSTITUTION_FILES = (
    "MICRODADOS_ED_SUP_IES_{year}.CSV",
    "MICRODADOS_CADASTRO_IES_{year}.CSV",
)


@dataclass(frozen=True)
class Column:
    """One source column and how it appears in the output."""

    source: str
    name: str
    sql_type: str
    required: bool = False


COURSE_COLUMNS: tuple[Column, ...] = (
    Column("NU_ANO_CENSO", "census_year", "INTEGER", required=True),
    Column("CO_IES", "institution_id", "BIGINT", required=True),
    Column("CO_CURSO", "course_id", "BIGINT"),
    Column("NO_CURSO", "course_name", "VARCHAR"),
    Column("NO_REGIAO", "region", "VARCHAR"),
    Column("SG_UF", "state", "VARCHAR"),
    Column("TP_REDE", "network_code", "TINYINT", required=True),
    Column("TP_CATEGORIA_ADMINISTRATIVA", "admin_category_code", "TINYINT"),
    Column("TP_MODALIDADE_ENSINO", "modality_code", "TINYINT", required=True),
    Column("TP_GRAU_ACADEMICO", "degree_code", "TINYINT"),
    Column("TP_NIVEL_ACADEMICO", "level_code", "TINYINT"),
    Column("NO_CINE_AREA_GERAL", "field_name", "VARCHAR"),
    Column("QT_ING", "entrants", "BIGINT"),
    Column("QT_MAT", "enrolled", "BIGINT", required=True),
    Column("QT_CONC", "graduates", "BIGINT", required=True),
    Column("QT_SIT_TRANCADA", "on_leave", "BIGINT"),
    Column("QT_SIT_DESVINCULADO", "dropped_out", "BIGINT", required=True),
    Column("QT_SIT_TRANSFERIDO", "transferred", "BIGINT"),
    Column("QT_SIT_FALECIDO", "deceased", "BIGINT"),
)

INSTITUTION_COLUMNS: tuple[Column, ...] = (
    Column("CO_IES", "institution_id", "BIGINT", required=True),
    Column("NO_IES", "institution_name", "VARCHAR"),
    Column("SG_IES", "institution_acronym", "VARCHAR"),
    Column("TP_ORGANIZACAO_ACADEMICA", "academic_org_code", "TINYINT"),
)

# Count columns, in output names. Blank cells in the source mean zero.
COUNT_COLUMNS: tuple[str, ...] = (
    "entrants",
    "enrolled",
    "graduates",
    "on_leave",
    "dropped_out",
    "transferred",
    "deceased",
)

# TP_CATEGORIA_ADMINISTRATIVA -> TP_REDE, from the INEP data dictionary:
# 1-3 federal/state/municipal, 7 "special" (public by law); 4-6, 8, 9 private.
# Checked against every edition where both columns are filled.
PUBLIC_CATEGORIES = (1, 2, 3, 7)
PRIVATE_CATEGORIES = (4, 5, 6, 8, 9)

MODALITY_LABELS = {1: "in_person", 2: "distance"}
NETWORK_LABELS = {1: "public", 2: "private"}


class LayoutError(ValueError):
    """The source files do not match the expected layout."""


def read_header(path: Path) -> list[str]:
    """Return the upper-case column names from the first line of a census CSV."""
    with path.open(encoding=ENCODING, newline="") as fh:
        first = fh.readline()
    return [c.strip().strip('"').upper() for c in first.rstrip("\r\n").split(DELIMITER)]


def find_file(directory: Path, patterns: tuple[str, ...], year: int) -> Path | None:
    """Find the first file in ``directory`` that matches one of ``patterns``.

    The match ignores case, because older editions mix ``.csv`` and ``.CSV``.
    """
    if not directory.is_dir():
        return None
    by_upper = {p.name.upper(): p for p in directory.iterdir() if p.is_file()}
    for pattern in patterns:
        hit = by_upper.get(pattern.format(year=year).upper())
        if hit is not None:
            return hit
    return None


def select_list(columns: tuple[Column, ...], header: list[str], file_label: str) -> list[str]:
    """Build the SQL select expressions for ``columns``, given the file header.

    Required columns that are missing raise :class:`LayoutError`. Optional
    columns that are missing become typed NULLs, so every year has the same
    output schema.
    """
    available = set(header)
    missing = [c.source for c in columns if c.required and c.source not in available]
    if missing:
        raise LayoutError(f"{file_label}: missing required column(s) {', '.join(missing)}")

    exprs = []
    for col in columns:
        if col.source in available:
            exprs.append(f'TRY_CAST("{col.source}" AS {col.sql_type}) AS {col.name}')
        else:
            exprs.append(f"CAST(NULL AS {col.sql_type}) AS {col.name}")
    return exprs
