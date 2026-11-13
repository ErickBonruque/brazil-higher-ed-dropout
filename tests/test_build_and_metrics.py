from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from higher_ed_dropout import build, download, metrics, schema
from tests.conftest import write_cp1252


@pytest.fixture
def raw_dir(source_dir: Path, tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    for year in (2019, 2024):
        download.import_folder(source_dir / str(year), raw, year)
    return raw


@pytest.fixture
def data_dir(raw_dir: Path, tmp_path: Path) -> Path:
    out = tmp_path / "parquet"
    for year in (2019, 2024):
        build.build_year(raw_dir, out, year)
    return out


def test_import_converts_to_utf8_and_normalizes_names(raw_dir: Path) -> None:
    new = raw_dir / "2024" / "MICRODADOS_CADASTRO_CURSOS_2024.CSV"
    assert new.exists()
    assert "Matemática" in new.read_text(encoding="utf-8")
    old = raw_dir / "2019" / "MICRODADOS_CADASTRO_CURSOS_2019.CSV"
    assert "Direito – noturno" in old.read_text(encoding="utf-8")  # cp1252 0x96


def test_build_report_and_partition(raw_dir: Path, tmp_path: Path) -> None:
    report = build.build_year(raw_dir, tmp_path / "out", 2019)
    assert report.rows == 3
    assert report.institutions == 2
    assert report.rows_without_institution == 0
    assert report.ok
    assert report.output == build.partition_path(tmp_path / "out", 2019)


def test_same_schema_for_every_layout(data_dir: Path) -> None:
    con = metrics.connect(data_dir)
    cols = {
        year: [
            r[0]
            for r in con.execute(
                f"DESCRIBE SELECT * FROM courses WHERE census_year={year}"
            ).fetchall()
        ]
        for year in (2019, 2024)
    }
    assert cols[2019] == cols[2024]
    row = con.execute(
        "SELECT institution_name, on_leave, deceased, course_name FROM courses "
        "WHERE census_year = 2024 AND course_id = 100"
    ).fetchone()
    # joined from the renamed IES file; blank cell and missing column become 0
    assert row == ("Universidade Pública do Paraná", 0, 0, "Matemática")


def test_rebuild_replaces_partition(raw_dir: Path, data_dir: Path) -> None:
    build.build_year(raw_dir, data_dir, 2019)
    con = metrics.connect(data_dir)
    assert con.execute("SELECT count(*) FROM courses").fetchone() == (5,)


def test_missing_required_column_is_a_layout_error(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    (raw / "2020").mkdir(parents=True)
    (raw / "2020" / "MICRODADOS_CADASTRO_CURSOS_2020.CSV").write_text(
        "NU_ANO_CENSO;CO_IES;TP_REDE;TP_MODALIDADE_ENSINO;QT_MAT;QT_CONC\n2020;1;1;1;10;1\n",
        encoding="utf-8",
    )
    with pytest.raises(schema.LayoutError, match="QT_SIT_DESVINCULADO"):
        build.build_year(raw, tmp_path / "out", 2020)


def test_wrong_year_inside_file_is_a_layout_error(raw_dir: Path, tmp_path: Path) -> None:
    target = raw_dir / "2021" / "MICRODADOS_CADASTRO_CURSOS_2021.CSV"
    target.parent.mkdir()
    target.write_bytes((raw_dir / "2019" / "MICRODADOS_CADASTRO_CURSOS_2019.CSV").read_bytes())
    with pytest.raises(schema.LayoutError, match="expected year 2021"):
        build.build_year(raw_dir, tmp_path / "out", 2021)


def test_missing_courses_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build.build_year(tmp_path, tmp_path / "out", 2019)


def test_quality_checks_flag_bad_rows(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    write_cp1252(
        raw / "2019" / "MICRODADOS_CADASTRO_CURSOS_2019.CSV",
        "NU_ANO_CENSO;CO_IES;TP_REDE;TP_MODALIDADE_ENSINO;QT_MAT;QT_CONC;QT_SIT_DESVINCULADO\n"
        "2019;1;1;1;10;20;0\n2019;1;1;1;10;0;-1\n",
    )
    report = build.build_year(raw, tmp_path / "out", 2019)
    assert report.graduates_above_enrolled == 1
    assert report.negative_counts == 1
    assert report.rows_without_institution == 2
    assert not report.ok


def test_empty_network_is_filled_from_admin_category(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    write_cp1252(
        raw / "2013" / "MICRODADOS_CADASTRO_CURSOS_2013.CSV",
        "NU_ANO_CENSO;CO_IES;TP_CATEGORIA_ADMINISTRATIVA;TP_REDE;TP_MODALIDADE_ENSINO;"
        "QT_MAT;QT_CONC;QT_SIT_DESVINCULADO\n"
        "2013;1;1;;1;10;0;1\n2013;2;7;;1;10;0;1\n2013;3;4;;1;10;0;1\n"
        "2013;4;5;2;1;10;0;1\n2013;5;;;1;10;0;1\n",
    )
    out = tmp_path / "out"
    report = build.build_year(raw, out, 2013)
    assert report.network_filled == 3  # row 4 keeps its code; row 5 stays unknown
    con = metrics.connect(out)
    got = dict(con.execute("SELECT institution_id, network FROM courses").fetchall())
    assert got == {1: "public", 2: "public", 3: "private", 4: "private", 5: "other"}


# ---- metrics -------------------------------------------------------------
# 2019 totals: dropped 70; bonds = enrolled 380 + leave 15 + dropped 70 + transf 15 = 480
#              legacy = enrolled 380 + graduates 60 + dropped 70 = 510


@pytest.mark.parametrize(
    ("definition", "expected"),
    [(metrics.Definition.BONDS, 70 / 480), (metrics.Definition.LEGACY, 70 / 510)],
)
def test_pooled_rate(data_dir: Path, definition: metrics.Definition, expected: float) -> None:
    con = metrics.connect(data_dir)
    rel = metrics.rates(con, definition=definition, filters={"census_year": [2019]})
    courses, dropped, _bonds, rate = rel.fetchall()[0]
    assert (courses, dropped) == (3, 70)
    assert rate == pytest.approx(expected)


def test_grouped_rates(data_dir: Path) -> None:
    con = metrics.connect(data_dir)
    rows = metrics.rates(con, ["census_year", "network"]).fetchall()
    by_key = {(r[0], r[1]): r[-1] for r in rows}
    assert by_key[(2019, "public")] == pytest.approx(50 / (180 + 5 + 50 + 5))
    assert by_key[(2019, "private")] == pytest.approx(20 / (200 + 10 + 20 + 10))
    assert by_key[(2024, "private")] == pytest.approx(50 / 150)


def test_modality_labels(data_dir: Path) -> None:
    con = metrics.connect(data_dir)
    labels = {r[0] for r in metrics.rates(con, ["modality"]).fetchall()}
    assert labels == {"in_person", "distance"}


def test_empty_filter_values_are_ignored(data_dir: Path) -> None:
    con = metrics.connect(data_dir)
    all_rows = metrics.rates(con).fetchall()[0]
    assert metrics.rates(con, filters={"state": []}).fetchall()[0] == all_rows


def test_zero_bonds_gives_null_rate(data_dir: Path) -> None:
    con = metrics.connect(data_dir)
    rel = metrics.rates(con, filters={"state": ["XX"]})
    assert rel.fetchall() == [(0, None, None, None)]


@pytest.mark.parametrize("bad", ["state; DROP TABLE x", "unknown"])
def test_unknown_dimension_is_rejected(data_dir: Path, bad: str) -> None:
    con = metrics.connect(data_dir)
    with pytest.raises(ValueError, match="unknown dimension"):
        metrics.rates(con, [bad])
    with pytest.raises(ValueError, match="unknown dimension"):
        metrics.rates(con, filters={bad: ["PR"]})


def test_filter_values_are_parameters(data_dir: Path) -> None:
    con = metrics.connect(data_dir)
    rel = metrics.rates(con, filters={"state": ["PR' OR '1'='1"]})
    assert rel.fetchall() == [(0, None, None, None)]
    assert isinstance(con, duckdb.DuckDBPyConnection)
