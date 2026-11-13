"""Dropout rate over the Parquet dataset, with DuckDB.

Two definitions are available. Both use INEP's "desvinculado" (student left
the course without finishing, ``dropped_out``) as the numerator. They differ in
the denominator:

``bonds`` (default)
    Every student bond the census reports for the year::

        enrolled + on_leave + dropped_out + transferred + deceased

    In the INEP data dictionary an enrollment ("matrícula") is a bond with the
    status "studying" or "graduated", so graduates are already inside
    ``enrolled``. The build checks this: ``graduates <= enrolled`` holds in
    every row of every edition from 2010 to 2024.

``legacy``
    ``enrolled + graduates + dropped_out``. This is the formula of the first
    version of this project (the one in the conference poster). It counts
    graduates twice and leaves out students on leave, transferred or
    deceased. The two errors partly cancel: for 2024 it gives 23.5% against
    22.6% with ``bonds``. It stays here so the published numbers can be
    reproduced.

The rate is *pooled*: sum of numerators over sum of denominators for the
group, not the mean of per-course rates (small courses would dominate that).
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path

import duckdb

from higher_ed_dropout.build import dataset_glob


class Definition(StrEnum):
    """Which denominator to use for the dropout rate."""

    BONDS = "bonds"
    LEGACY = "legacy"


DENOMINATORS: dict[Definition, str] = {
    Definition.BONDS: "enrolled + on_leave + dropped_out + transferred + deceased",
    Definition.LEGACY: "enrolled + graduates + dropped_out",
}

# Columns of the ``courses`` view that callers may group or filter by.
DIMENSIONS: frozenset[str] = frozenset(
    {
        "census_year",
        "region",
        "state",
        "network",
        "modality",
        "admin_category_code",
        "degree_code",
        "level_code",
        "field_name",
        "institution_id",
        "institution_name",
        "course_name",
    }
)

_VIEW_SQL = """
CREATE OR REPLACE VIEW courses AS
SELECT *,
       CASE network_code WHEN 1 THEN 'public' WHEN 2 THEN 'private' ELSE 'other' END
           AS network,
       CASE modality_code WHEN 1 THEN 'in_person' WHEN 2 THEN 'distance' ELSE 'other' END
           AS modality
FROM read_parquet('{glob}', hive_partitioning = true)
"""


def connect(data_dir: Path) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB connection with a ``courses`` view on ``data_dir``."""
    glob = dataset_glob(data_dir).replace("'", "''")
    con = duckdb.connect()
    con.execute(_VIEW_SQL.format(glob=glob))
    return con


def _check_dimensions(names: Sequence[str]) -> None:
    unknown = sorted(set(names) - DIMENSIONS)
    if unknown:
        raise ValueError(f"unknown dimension(s): {', '.join(unknown)}")


def rates(
    con: duckdb.DuckDBPyConnection,
    by: Sequence[str] = (),
    *,
    definition: Definition = Definition.BONDS,
    filters: dict[str, Sequence[str | int]] | None = None,
) -> duckdb.DuckDBPyRelation:
    """Pooled dropout rate, grouped by ``by``.

    Args:
        con: Connection from :func:`connect`.
        by: Dimensions to group by (see :data:`DIMENSIONS`). Empty means one row.
        definition: Which denominator to use.
        filters: ``{dimension: allowed values}``; rows must match every entry.

    Returns:
        A relation with the ``by`` columns, ``courses``, ``dropped_out``,
        ``bonds`` and ``dropout_rate`` (0 to 1, NULL when there is no bond).

    Raises:
        ValueError: If a dimension name is not in :data:`DIMENSIONS`.
    """
    filters = filters or {}
    _check_dimensions([*by, *filters])

    where: list[str] = []
    params: list[object] = []
    for column, values in filters.items():
        if not values:
            continue
        where.append(f"{column} IN ({', '.join('?' for _ in values)})")
        params.extend(values)

    keys = ", ".join(by)
    denominator = DENOMINATORS[Definition(definition)]
    sql = (
        f"SELECT {keys + ', ' if keys else ''}"
        "count(*) AS courses, "
        "sum(dropped_out) AS dropped_out, "
        f"sum({denominator}) AS bonds, "
        f"sum(dropped_out) / nullif(sum({denominator}), 0) AS dropout_rate "
        "FROM courses"
        + (f" WHERE {' AND '.join(where)}" if where else "")
        + (f" GROUP BY {keys} ORDER BY {keys}" if keys else "")
    )
    return con.sql(sql, params=params)
