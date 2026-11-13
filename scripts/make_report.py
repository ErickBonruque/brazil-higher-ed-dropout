"""Write the result tables and README charts from the full Parquet dataset.

Usage: uv run python scripts/make_report.py [data/parquet]
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import duckdb
import matplotlib.pyplot as plt

from higher_ed_dropout import metrics

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
IMG = ROOT / "docs" / "img"

# Categorical slots 1 and 2 (blue, orange); validated for color-vision deficiency.
COLORS = ("#2a78d6", "#eb6834")
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"

TABLES = {
    "rates_by_year.csv": ["census_year"],
    "rates_by_year_modality.csv": ["census_year", "modality"],
    "rates_by_year_network.csv": ["census_year", "network"],
}


def write_tables(con: duckdb.DuckDBPyConnection) -> None:
    RESULTS.mkdir(exist_ok=True)
    for name, by in TABLES.items():
        rel = metrics.rates(con, by).project(
            ", ".join(by) + ", courses, dropped_out, bonds, round(dropout_rate, 4) AS dropout_rate"
        )
        rel.write_csv(str(RESULTS / name))
    legacy = metrics.rates(con, ["census_year"], definition=metrics.Definition.LEGACY).project(
        "census_year, round(dropout_rate, 4) AS dropout_rate_legacy"
    )
    legacy.write_csv(str(RESULTS / "rates_by_year_legacy.csv"))


def line_chart(
    con: duckdb.DuckDBPyConnection,
    dimension: str,
    labels: dict[str, str],
    title: str,
    out: Path,
) -> None:
    rows = metrics.rates(con, ["census_year", dimension]).fetchall()
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for color, (key, label) in zip(COLORS, labels.items(), strict=True):
        pts = [(r[0], 100 * r[-1]) for r in rows if r[1] == key]
        xs, ys = zip(*pts, strict=True)
        ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=4, label=label)
        ax.annotate(
            f"{label} {ys[-1]:.1f}%",
            (xs[-1], ys[-1]),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            color=INK,
            fontsize=9,
        )
    ax.set_title(title, loc="left", color=INK, fontsize=12)
    ax.set_ylabel("Dropout rate (%)", color=MUTED)
    ax.set_ylim(0, None)
    ax.set_xlim(2009.5, 2027)
    ax.set_xticks(range(2010, 2025, 2))
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, length=0)
    ax.legend(frameon=False, loc="upper left", labelcolor=INK)
    fig.text(
        0.01,
        0.01,
        "Source: INEP, Higher Education Census microdata. Rate = dropped out / all student bonds.",
        color=MUTED,
        fontsize=7,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    IMG.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=SURFACE)
    plt.close(fig)


def main() -> None:
    data = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "parquet"
    con = metrics.connect(data)
    write_tables(con)
    line_chart(
        con,
        "modality",
        {"in_person": "In person", "distance": "Distance"},
        "Dropout rate by mode of study, Brazil 2010-2024",
        IMG / "dropout_by_modality.png",
    )
    line_chart(
        con,
        "network",
        {"public": "Public", "private": "Private"},
        "Dropout rate by type of institution, Brazil 2010-2024",
        IMG / "dropout_by_network.png",
    )
    print(f"wrote {RESULTS} and {IMG}")


if __name__ == "__main__":
    main()
