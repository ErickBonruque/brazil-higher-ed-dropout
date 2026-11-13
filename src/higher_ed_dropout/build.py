"""Turn the raw census CSVs of one year into a typed Parquet partition.

Output layout (Hive style, one file per year)::

    <out>/courses/census_year=2024/part-0.parquet

Each row is one course offer (for distance learning, one course per city of
offer). Institution attributes are joined in, so the dashboard and the
metrics only read one table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import duckdb

from higher_ed_dropout import schema

log = logging.getLogger(__name__)

COURSES_DIR = "courses"


@dataclass(frozen=True)
class BuildReport:
    """Row counts and data-quality checks for one built year."""

    year: int
    rows: int
    institutions: int
    rows_without_institution: int
    graduates_above_enrolled: int
    negative_counts: int
    output: Path
    network_filled: int = 0

    @property
    def ok(self) -> bool:
        """True when no data-quality check failed."""
        return self.graduates_above_enrolled == 0 and self.negative_counts == 0


def partition_path(out_dir: Path, year: int) -> Path:
    """Return the Parquet file for ``year`` under ``out_dir``."""
    return out_dir / COURSES_DIR / f"census_year={year}" / "part-0.parquet"


def _read_csv_sql(path: Path) -> str:
    # all_varchar + TRY_CAST in the select keeps the types independent of
    # DuckDB's type sniffing, which can change with the sample size.
    quoted = str(path).replace("'", "''")
    return (
        f"read_csv('{quoted}', delim='{schema.DELIMITER}', header=true, "
        "all_varchar=true, quote='\"')"
    )


def build_year(raw_dir: Path, out_dir: Path, year: int) -> BuildReport:
    """Build the Parquet partition for one census year.

    Args:
        raw_dir: Folder with one sub-folder per year holding the INEP CSVs.
        out_dir: Root of the Parquet dataset.
        year: Census year to build.

    Returns:
        A :class:`BuildReport` with counts and data-quality checks.

    Raises:
        FileNotFoundError: If the courses file for ``year`` is not there.
        LayoutError: If a required column is missing or the file has another year.
    """
    year_dir = raw_dir / str(year)
    course_file = schema.find_file(year_dir, (schema.COURSE_FILE,), year)
    if course_file is None:
        raise FileNotFoundError(f"no courses file for {year} in {year_dir}")
    inst_file = schema.find_file(year_dir, schema.INSTITUTION_FILES, year)

    course_cols = schema.select_list(
        schema.COURSE_COLUMNS, schema.read_header(course_file), course_file.name
    )
    count_names = set(schema.COUNT_COLUMNS)
    keep = [c.name for c in schema.COURSE_COLUMNS if c.name not in count_names]

    con = duckdb.connect()
    con.execute(
        "CREATE TEMP TABLE c AS SELECT "
        + ", ".join(course_cols)
        + f" FROM {_read_csv_sql(course_file)}"
    )

    years = [r[0] for r in con.execute("SELECT DISTINCT census_year FROM c").fetchall()]
    if years != [year]:
        raise schema.LayoutError(f"{course_file.name}: expected year {year}, found {years}")

    # 2013: TP_REDE is present but empty in 98% of the rows. The network follows
    # from the administrative category (same mapping in every other edition).
    public = ", ".join(map(str, schema.PUBLIC_CATEGORIES))
    private = ", ".join(map(str, schema.PRIVATE_CATEGORIES))
    filled = con.execute(
        f"""
        UPDATE c SET network_code = CASE
            WHEN admin_category_code IN ({public}) THEN 1
            WHEN admin_category_code IN ({private}) THEN 2
        END
        WHERE network_code IS NULL
          AND admin_category_code IN ({public}, {private})
        """
    ).fetchall()[0][0]

    if inst_file is not None:
        inst_cols = schema.select_list(
            schema.INSTITUTION_COLUMNS, schema.read_header(inst_file), inst_file.name
        )
        con.execute(
            "CREATE TEMP TABLE i AS SELECT DISTINCT ON (institution_id) "
            + ", ".join(inst_cols)
            + f" FROM {_read_csv_sql(inst_file)}"
        )
    else:
        log.warning("%s: no institutions file, institution columns will be NULL", year)
        con.execute(
            "CREATE TEMP TABLE i AS SELECT "
            + ", ".join(
                f"CAST(NULL AS {c.sql_type}) AS {c.name}" for c in schema.INSTITUTION_COLUMNS
            )
            + " WHERE false"
        )

    inst_names = [c.name for c in schema.INSTITUTION_COLUMNS if c.name != "institution_id"]
    select = (
        "SELECT "
        + ", ".join(f"c.{n}" for n in keep if n != "census_year")
        + ", "
        + ", ".join(f"i.{n}" for n in inst_names)
        + ", "
        + ", ".join(f"COALESCE(c.{c}, 0) AS {c}" for c in schema.COUNT_COLUMNS)
        + " FROM c LEFT JOIN i USING (institution_id)"
        + " ORDER BY c.institution_id, c.course_id"
    )

    target = partition_path(out_dir, year)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".parquet.tmp")
    tmp_sql = str(tmp).replace("'", "''")
    con.execute(f"COPY ({select}) TO '{tmp_sql}' (FORMAT parquet, COMPRESSION zstd)")
    tmp.replace(target)

    negatives = " OR ".join(f"{c} < 0" for c in schema.COUNT_COLUMNS)
    row = con.execute(
        f"""
        SELECT count(*),
               count(DISTINCT c.institution_id),
               count(*) FILTER (WHERE i.institution_id IS NULL),
               count(*) FILTER (WHERE COALESCE(c.graduates, 0) > COALESCE(c.enrolled, 0)),
               count(*) FILTER (WHERE {negatives})
        FROM c LEFT JOIN i USING (institution_id)
        """
    ).fetchone()
    con.close()
    if row is None:  # pragma: no cover - an aggregate always returns one row
        raise RuntimeError("quality query returned no row")
    report = BuildReport(
        year=year,
        rows=int(row[0]),
        institutions=int(row[1]),
        rows_without_institution=int(row[2]),
        graduates_above_enrolled=int(row[3]),
        negative_counts=int(row[4]),
        output=target,
        network_filled=int(filled),
    )
    log.info("%s: %s rows, %s institutions -> %s", year, report.rows, report.institutions, target)
    return report


def dataset_glob(out_dir: Path) -> str:
    """Return the glob that reads every built year."""
    return str(out_dir / COURSES_DIR / "*" / "*.parquet")
