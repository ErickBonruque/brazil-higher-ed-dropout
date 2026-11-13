from __future__ import annotations

import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests

from higher_ed_dropout import download, schema
from tests.conftest import COURSES_OLD, IES_OLD


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, length: int | None = None) -> None:
        self.body = body
        self.status = status
        self.headers = {"Content-Length": str(len(body) if length is None else length)}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise requests.HTTPError(f"{self.status}")

    def iter_content(self, size: int) -> Iterator[bytes]:
        for i in range(0, len(self.body), 3):
            yield self.body[i : i + 3]


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


def _session(resp: FakeResponse) -> Any:
    return FakeSession(resp)


def test_download_writes_file_and_verifies_tls(tmp_path: Path) -> None:
    session = _session(FakeResponse(b"hello world"))
    dest = download.download("https://x/y.zip", tmp_path / "y.zip", session)
    assert dest.read_bytes() == b"hello world"
    assert not (tmp_path / "y.zip.part").exists()
    assert session.calls[0]["verify"] == download.ca_bundle()


def test_ca_bundle_contains_certifi_and_intermediate() -> None:
    import certifi

    bundle = Path(download.ca_bundle()).read_text(encoding="ascii")
    pem = (
        Path(download.__file__).parent / "certs" / "rnp-icpedu-gr46-ov-tls-ca-2025.pem"
    ).read_text(encoding="ascii")
    assert pem.strip() in bundle
    assert Path(certifi.where()).read_text(encoding="ascii").strip() in bundle


def test_download_skips_existing(tmp_path: Path) -> None:
    dest = tmp_path / "y.zip"
    dest.write_bytes(b"old")
    session = _session(FakeResponse(b"new"))
    download.download("https://x/y.zip", dest, session)
    assert dest.read_bytes() == b"old"
    assert session.calls == []


def test_truncated_download_leaves_nothing(tmp_path: Path) -> None:
    session = _session(FakeResponse(b"short", length=100))
    with pytest.raises(download.DownloadError, match="expected 100"):
        download.download("https://x/y.zip", tmp_path / "y.zip", session)
    assert list(tmp_path.iterdir()) == []


def test_http_error_leaves_nothing(tmp_path: Path) -> None:
    session = _session(FakeResponse(b"", status=404))
    with pytest.raises(download.DownloadError, match="404"):
        download.download("https://x/y.zip", tmp_path / "y.zip", session)
    assert list(tmp_path.iterdir()) == []


def _make_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "microdados_2019/dados/MICRODADOS_CADASTRO_CURSOS_2019.CSV",
            COURSES_OLD.encode("cp1252"),
        )
        zf.writestr(
            "microdados_2019/dados/microdados_cadastro_ies_2019.csv", IES_OLD.encode("cp1252")
        )
        zf.writestr("microdados_2019/leia-me/dicionario.pdf", b"%PDF")
        zf.writestr("../../escape/MICRODADOS_CADASTRO_IES_2018.CSV", b"x")
    return path


def test_extract_only_wanted_files_as_utf8(tmp_path: Path) -> None:
    zip_path = _make_zip(tmp_path / "c.zip")
    out = tmp_path / "raw"
    files = download.extract_microdata(zip_path, out, 2019)
    assert sorted(p.name for p in files) == [
        "MICRODADOS_CADASTRO_CURSOS_2019.CSV",
        "MICRODADOS_CADASTRO_IES_2019.CSV",
    ]
    assert all(p.parent == out / "2019" for p in files)
    assert "Direito – noturno" in files[0].read_text(encoding="utf-8")
    assert not (tmp_path / "escape").exists()


def test_extract_without_courses_file_fails(tmp_path: Path) -> None:
    zip_path = tmp_path / "c.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("leia-me.pdf", b"%PDF")
    with pytest.raises(schema.LayoutError):
        download.extract_microdata(zip_path, tmp_path / "raw", 2019)


def test_fetch_years_skips_extracted_and_removes_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "raw"
    (raw / "2018").mkdir(parents=True)
    (raw / "2018" / "MICRODADOS_CADASTRO_CURSOS_2018.CSV").write_text("x", encoding="utf-8")
    zip_bytes = _make_zip(tmp_path / "src.zip").read_bytes()
    session = _session(FakeResponse(zip_bytes))

    result = download.fetch_years([2018, 2019], raw, session=session)

    assert [c["url"] for c in session.calls] == [download.URL_TEMPLATE.format(year=2019)]
    assert len(result[2019]) == 2
    assert not (raw / "_zip" / "censo_superior_2019.zip").exists()


def test_make_session_retries() -> None:
    session = download.make_session(retries=3)
    adapter = session.get_adapter("https://download.inep.gov.br")
    assert adapter.max_retries.total == 3  # type: ignore[attr-defined]
