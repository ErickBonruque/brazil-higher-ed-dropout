"""Download the census ZIP files from INEP and extract only the CSVs we use.

TLS note: ``download.inep.gov.br`` sends its certificate without the
intermediate CA. Browsers fetch the missing certificate on their own; Python
does not, so a plain ``requests.get`` fails. Instead of turning verification
off, this module adds that one public intermediate (shipped in ``certs/``) to
the certifi bundle.
"""

from __future__ import annotations

import io
import logging
import shutil
import tempfile
import zipfile
from collections.abc import Iterable
from functools import cache
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import IO

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from higher_ed_dropout import schema

log = logging.getLogger(__name__)

URL_TEMPLATE = (
    "https://download.inep.gov.br/microdados/microdados_censo_da_educacao_superior_{year}.zip"
)
CHUNK_SIZE = 1 << 20
TIMEOUT = (30, 300)  # connect, read (seconds)


class DownloadError(RuntimeError):
    """The file could not be downloaded completely."""


@cache
def ca_bundle() -> str:
    """Return a CA bundle path: certifi roots plus the INEP intermediate."""
    extra = resources.files("higher_ed_dropout").joinpath(
        "certs/rnp-icpedu-gr46-ov-tls-ca-2025.pem"
    )
    # A new file with a random name, readable only by this user: a fixed name
    # in the shared temp folder could be created first by someone else.
    with tempfile.NamedTemporaryFile(
        "w", encoding="ascii", prefix="higher_ed_dropout_ca_", suffix=".pem", delete=False
    ) as fh:
        fh.write(Path(certifi.where()).read_text(encoding="ascii"))
        fh.write("\n")
        fh.write(extra.read_text(encoding="ascii"))
    return fh.name


def make_session(retries: int = 5) -> requests.Session:
    """Session with retry and exponential backoff on network errors and 429/5xx."""
    retry = Retry(
        total=retries,
        backoff_factor=2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = "brazil-higher-ed-dropout/0.1"
    return session


def download(
    url: str,
    dest: Path,
    session: requests.Session,
    *,
    verify: str | bool | None = None,
) -> Path:
    """Stream ``url`` to ``dest``. Skip if ``dest`` already exists.

    The data goes to ``<dest>.part`` first and is renamed only when the size
    matches ``Content-Length``, so an interrupted run never leaves a file that
    looks complete.

    Raises:
        DownloadError: On HTTP errors or when the size does not match.
    """
    if dest.exists():
        log.info("already downloaded: %s", dest)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        with session.get(
            url, stream=True, timeout=TIMEOUT, verify=ca_bundle() if verify is None else verify
        ) as resp:
            resp.raise_for_status()
            expected = int(resp.headers.get("Content-Length", 0)) or None
            written = 0
            with part.open("wb") as fh:
                for chunk in resp.iter_content(CHUNK_SIZE):
                    fh.write(chunk)
                    written += len(chunk)
        if expected is not None and written != expected:
            raise DownloadError(f"{url}: got {written} bytes, expected {expected}")
    except requests.RequestException as exc:
        part.unlink(missing_ok=True)
        raise DownloadError(f"{url}: {exc}") from exc
    except DownloadError:
        part.unlink(missing_ok=True)
        raise
    part.replace(dest)
    log.info("downloaded %s (%d MB)", dest, written >> 20)
    return dest


def _wanted_names(year: int) -> set[str]:
    return {p.format(year=year).upper() for p in (schema.COURSE_FILE, *schema.INSTITUTION_FILES)}


def transcode(src: IO[bytes], target: Path) -> None:
    """Stream ``src`` (Windows-1252) into ``target`` as UTF-8.

    Writes to a temporary name first, so a crash never leaves half a file.
    """
    tmp = target.with_name(target.name + ".tmp")
    text = io.TextIOWrapper(src, encoding=schema.SOURCE_ENCODING, newline="")
    with tmp.open("w", encoding=schema.ENCODING, newline="") as dst:
        shutil.copyfileobj(text, dst, CHUNK_SIZE)
    text.detach()
    tmp.replace(target)


def import_folder(source_dir: Path, out_dir: Path, year: int) -> list[Path]:
    """Copy the CSVs of ``year`` from a folder downloaded by hand, as UTF-8."""
    wanted = _wanted_names(year)
    target_dir = out_dir / str(year)
    target_dir.mkdir(parents=True, exist_ok=True)
    done: list[Path] = []
    for path in sorted(source_dir.iterdir()):
        if path.is_file() and path.name.upper() in wanted:
            target = target_dir / path.name.upper()
            with path.open("rb") as src:
                transcode(src, target)
            done.append(target)
    return done


def extract_microdata(zip_path: Path, out_dir: Path, year: int) -> list[Path]:
    """Extract the courses and institutions CSVs of ``year`` into ``out_dir/<year>/``.

    The files are converted to UTF-8 on the way out and streamed, not loaded
    in memory. Only the file name of each member is used (never its folder),
    so a malformed archive cannot write outside ``out_dir``.
    """
    wanted = _wanted_names(year)
    target_dir = out_dir / str(year)
    target_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = PurePosixPath(info.filename.replace("\\", "/")).name.upper()
            if info.is_dir() or name not in wanted:
                continue
            target = target_dir / name
            with zf.open(info) as src:
                transcode(src, target)
            extracted.append(target)
    if not any(p.name.startswith("MICRODADOS_CADASTRO_CURSOS") for p in extracted):
        raise schema.LayoutError(f"{zip_path.name}: courses file for {year} not found in archive")
    return extracted


def fetch_years(
    years: Iterable[int],
    raw_dir: Path,
    *,
    keep_zip: bool = False,
    session: requests.Session | None = None,
) -> dict[int, list[Path]]:
    """Download and extract several years. Years already extracted are skipped."""
    session = session or make_session()
    result: dict[int, list[Path]] = {}
    for year in years:
        if schema.find_file(raw_dir / str(year), (schema.COURSE_FILE,), year):
            log.info("%s: already extracted", year)
            result[year] = sorted((raw_dir / str(year)).iterdir())
            continue
        zip_path = raw_dir / "_zip" / f"censo_superior_{year}.zip"
        download(URL_TEMPLATE.format(year=year), zip_path, session)
        result[year] = extract_microdata(zip_path, raw_dir, year)
        if not keep_zip:
            zip_path.unlink()
    return result
