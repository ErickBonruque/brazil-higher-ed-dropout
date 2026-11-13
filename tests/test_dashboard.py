"""Smoke test: the dashboard runs on the sample data without errors."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_runs_on_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEDROP_DATA", str(ROOT / "data" / "sample"))
    app = AppTest.from_file(str(ROOT / "app" / "dashboard.py"), default_timeout=60).run()
    assert not app.exception
    assert len(app.metric) == 3
    assert app.metric[2].value.endswith("%")

    app.sidebar.multiselect[1].select("public").run()
    assert not app.exception
