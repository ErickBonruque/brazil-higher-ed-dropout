"""Command line: ``hedrop download | import | build | rates | sample``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import duckdb

from higher_ed_dropout import build, download, metrics

FIRST_YEAR, LAST_YEAR = 2010, 2024


def parse_years(text: str) -> list[int]:
    """Parse ``"2019"``, ``"2010-2014"`` or ``"2012,2016-2018"`` into sorted years."""
    years: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        start, sep, end = part.partition("-")
        try:
            lo = int(start)
            hi = int(end) if sep else lo
        except ValueError:
            raise argparse.ArgumentTypeError(f"invalid year range: {part!r}") from None
        if lo > hi:
            raise argparse.ArgumentTypeError(f"invalid year range: {part!r}")
        years.update(range(lo, hi + 1))
    if not years:
        raise argparse.ArgumentTypeError("no year given")
    return sorted(years)


def _available_years(raw_dir: Path) -> list[int]:
    if not raw_dir.is_dir():
        return []
    return sorted(int(p.name) for p in raw_dir.iterdir() if p.is_dir() and p.name.isdigit())


def cmd_download(args: argparse.Namespace) -> int:
    download.fetch_years(args.years, args.raw, keep_zip=args.keep_zip)
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Import CSVs that were downloaded by hand (one sub-folder per year)."""
    years = args.years or _available_years(args.source)
    for year in years:
        files = download.import_folder(args.source / str(year), args.raw, year)
        print(f"{year}: {len(files)} file(s)")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    years = args.years or _available_years(args.raw)
    if not years:
        print(f"no raw data in {args.raw}; run 'hedrop download' first", file=sys.stderr)
        return 1
    status = 0
    print("year  rows      institutions  no_inst  grad>enr  negative  network_filled")
    for year in years:
        r = build.build_year(args.raw, args.out, year)
        print(
            f"{r.year}  {r.rows:<8}  {r.institutions:<12}  {r.rows_without_institution:<7}"
            f"  {r.graduates_above_enrolled:<8}  {r.negative_counts:<8}  {r.network_filled}"
        )
        if not r.ok:
            status = 2
    return status


def cmd_rates(args: argparse.Namespace) -> int:
    con = metrics.connect(args.data)
    by = [b for b in args.by.split(",") if b] if args.by else []
    rel = metrics.rates(con, by, definition=args.definition)
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        rel.write_csv(str(args.csv))
        print(f"wrote {args.csv}")
    else:
        rel.show(max_rows=200)
    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    """Write a small random sample of the dataset (used by the demo dashboard)."""
    src = build.dataset_glob(args.data).replace("'", "''")
    target = args.out / build.COURSES_DIR
    target.mkdir(parents=True, exist_ok=True)
    dst = target.as_posix().replace("'", "''")
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
            SELECT * FROM read_parquet('{src}', hive_partitioning = true)
            USING SAMPLE {float(args.percent)} PERCENT (bernoulli, {int(args.seed)})
        ) TO '{dst}' (FORMAT parquet, COMPRESSION zstd, PARTITION_BY (census_year),
                      OVERWRITE_OR_IGNORE, FILENAME_PATTERN 'part-{{i}}')
        """
    )
    print(f"wrote sample to {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hedrop", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("download", help="download and extract census CSVs from INEP")
    d.add_argument("--years", type=parse_years, default=f"{FIRST_YEAR}-{LAST_YEAR}")
    d.add_argument("--raw", type=Path, default=Path("data/raw"))
    d.add_argument("--keep-zip", action="store_true")
    d.set_defaults(func=cmd_download)

    i = sub.add_parser("import", help="import CSVs downloaded by hand (<dir>/<year>/*.CSV)")
    i.add_argument("source", type=Path)
    i.add_argument("--years", type=parse_years, default=None)
    i.add_argument("--raw", type=Path, default=Path("data/raw"))
    i.set_defaults(func=cmd_import)

    b = sub.add_parser("build", help="build the Parquet dataset from the raw CSVs")
    b.add_argument("--years", type=parse_years, default=None)
    b.add_argument("--raw", type=Path, default=Path("data/raw"))
    b.add_argument("--out", type=Path, default=Path("data/parquet"))
    b.set_defaults(func=cmd_build)

    r = sub.add_parser("rates", help="print or export dropout rates")
    r.add_argument("--data", type=Path, default=Path("data/parquet"))
    r.add_argument("--by", default="census_year", help="comma-separated dimensions")
    r.add_argument(
        "--definition",
        type=metrics.Definition,
        default=metrics.Definition.BONDS,
        choices=list(metrics.Definition),
    )
    r.add_argument("--csv", type=Path, default=None)
    r.set_defaults(func=cmd_rates)

    s = sub.add_parser("sample", help="write a small random sample for the demo")
    s.add_argument("--data", type=Path, default=Path("data/parquet"))
    s.add_argument("--out", type=Path, default=Path("data/sample"))
    s.add_argument("--percent", type=float, default=1.0)
    s.add_argument("--seed", type=int, default=42)
    s.set_defaults(func=cmd_sample)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
