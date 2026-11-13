"""Streamlit dashboard over the Parquet dataset.

Run:  streamlit run app/dashboard.py            (uses data/parquet, or the sample)
      HEDROP_DATA=path/to/parquet streamlit run app/dashboard.py
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pandas as pd
import plotly.express as px
import streamlit as st

from higher_ed_dropout import metrics

ROOT = Path(__file__).resolve().parents[1]
COLORS = {"in_person": "#2a78d6", "distance": "#eb6834", "public": "#2a78d6", "private": "#eb6834"}
LABELS = {
    "in_person": "In person",
    "distance": "Distance",
    "public": "Public",
    "private": "Private",
    "other": "Other",
}


def data_dir() -> Path:
    env = os.environ.get("HEDROP_DATA")
    if env:
        return Path(env)
    full = ROOT / "data" / "parquet"
    return full if full.exists() else ROOT / "data" / "sample"


@st.cache_resource
def connection(path: str) -> duckdb.DuckDBPyConnection:
    return metrics.connect(Path(path))


def options(con: duckdb.DuckDBPyConnection, column: str) -> list[str]:
    sql = f"SELECT DISTINCT {column} FROM courses WHERE {column} IS NOT NULL ORDER BY 1"
    return [r[0] for r in con.execute(sql).fetchall()]


def frame(rel: duckdb.DuckDBPyRelation) -> pd.DataFrame:
    df = rel.df()
    df["dropout_rate"] = (100 * df["dropout_rate"]).round(1)
    return df


def main() -> None:
    st.set_page_config(page_title="Higher education dropout in Brazil", layout="wide")
    path = data_dir()
    con = connection(str(path))
    is_sample = path.name == "sample"

    st.title("Higher education dropout in Brazil")
    st.caption(
        "Source: INEP, Higher Education Census microdata. "
        "Rate = students who dropped out / all student bonds of the year."
        + (" **Demo mode: 2% random sample of courses.**" if is_sample else "")
    )

    years = [int(y) for y in options(con, "census_year")]
    with st.sidebar:
        st.header("Filters")
        year = st.select_slider("Year", options=years, value=years[-1])
        states = st.multiselect("State", options(con, "state"))
        networks = st.multiselect(
            "Type of institution", ["public", "private"], format_func=LABELS.get
        )
        modalities = st.multiselect(
            "Mode of study", ["in_person", "distance"], format_func=LABELS.get
        )
        definition = st.radio(
            "Denominator",
            list(metrics.Definition),
            format_func=lambda d: "All student bonds" if d == "bonds" else "Legacy (poster)",
        )

    base = {"state": states, "network": networks, "modality": modalities}
    this_year = {**base, "census_year": [year]}

    total = metrics.rates(con, definition=definition, filters=this_year).fetchall()[0]
    c1, c2, c3 = st.columns(3)
    c1.metric("Student bonds", f"{int(total[2] or 0):,}")
    c2.metric("Dropped out", f"{int(total[1] or 0):,}")
    c3.metric("Dropout rate", f"{100 * total[3]:.1f}%" if total[3] is not None else "-")

    left, right = st.columns(2)
    with left:
        st.subheader("Over time, by mode of study")
        df = frame(
            metrics.rates(con, ["census_year", "modality"], definition=definition, filters=base)
        )
        df = df[df["modality"] != "other"]
        fig = px.line(
            df,
            x="census_year",
            y="dropout_rate",
            color="modality",
            markers=True,
            color_discrete_map=COLORS,
            labels={"dropout_rate": "Dropout rate (%)", "census_year": ""},
        )
        fig.for_each_trace(lambda t: t.update(name=LABELS.get(t.name, t.name)))
        st.plotly_chart(fig, width="stretch")
    with right:
        st.subheader("Over time, by type of institution")
        df = frame(
            metrics.rates(con, ["census_year", "network"], definition=definition, filters=base)
        )
        df = df[df["network"] != "other"]
        fig = px.line(
            df,
            x="census_year",
            y="dropout_rate",
            color="network",
            markers=True,
            color_discrete_map=COLORS,
            labels={"dropout_rate": "Dropout rate (%)", "census_year": ""},
        )
        fig.for_each_trace(lambda t: t.update(name=LABELS.get(t.name, t.name)))
        st.plotly_chart(fig, width="stretch")

    st.subheader(f"Fields of study, {year}")
    fields = frame(metrics.rates(con, ["field_name"], definition=definition, filters=this_year))
    fields = fields[fields["bonds"] >= 1000].sort_values("dropout_rate", ascending=False)
    st.dataframe(
        fields.rename(columns={"field_name": "Field", "dropout_rate": "Dropout rate (%)"}),
        hide_index=True,
        width="stretch",
    )


main()
