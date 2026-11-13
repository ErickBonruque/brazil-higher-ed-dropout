from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from higher_ed_dropout import cli


@pytest.mark.parametrize(
    ("text", "years"),
    [
        ("2019", [2019]),
        ("2010-2012", [2010, 2011, 2012]),
        ("2016, 2012-2013,2016", [2012, 2013, 2016]),
    ],
)
def test_parse_years(text: str, years: list[int]) -> None:
    assert cli.parse_years(text) == years


@pytest.mark.parametrize("text", ["", "abc", "2015-2010", "2010-x"])
def test_parse_years_rejects(text: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        cli.parse_years(text)


def test_end_to_end(source_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw, out, sample = tmp_path / "raw", tmp_path / "parquet", tmp_path / "sample"
    assert cli.main(["import", str(source_dir), "--raw", str(raw)]) == 0
    assert cli.main(["build", "--raw", str(raw), "--out", str(out)]) == 0
    assert "2024  2" in capsys.readouterr().out

    csv = tmp_path / "rates.csv"
    assert cli.main(["rates", "--data", str(out), "--by", "census_year", "--csv", str(csv)]) == 0
    lines = csv.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("census_year,courses,dropped_out,bonds,dropout_rate")
    assert len(lines) == 3

    assert cli.main(["sample", "--data", str(out), "--out", str(sample), "--percent", "100"]) == 0
    assert sorted(p.name for p in (sample / "courses").iterdir()) == [
        "census_year=2019",
        "census_year=2024",
    ]


def test_build_without_data(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["build", "--raw", str(tmp_path / "none")]) == 1
    assert "hedrop download" in capsys.readouterr().err
