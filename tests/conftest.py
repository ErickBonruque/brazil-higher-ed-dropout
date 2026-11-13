"""Synthetic census files that mimic the real INEP layout changes."""

from __future__ import annotations

from pathlib import Path

import pytest

# 2023-style layout: TP_CATEGORIA_ADMINISTRATIVA before TP_REDE, old IES file name.
COURSES_OLD = (
    "NU_ANO_CENSO;NO_REGIAO;SG_UF;TP_CATEGORIA_ADMINISTRATIVA;TP_REDE;CO_IES;NO_CURSO;CO_CURSO;"
    "TP_MODALIDADE_ENSINO;QT_ING;QT_MAT;QT_CONC;QT_SIT_TRANCADA;QT_SIT_DESVINCULADO;"
    "QT_SIT_TRANSFERIDO;QT_SIT_FALECIDO;EXTRA_COLUMN\n"
    "2019;Sul;PR;1;1;10;Matemática;100;1;20;80;10;5;10;5;0;x\n"
    "2019;Sul;PR;1;1;10;História;101;2;30;100;20;0;40;0;0;x\n"
    "2019;Sudeste;SP;4;2;20;Direito – noturno;200;1;50;200;30;10;20;10;0;x\n"
)
IES_OLD = (
    "NU_ANO_CENSO;CO_IES;NO_IES;SG_IES;TP_ORGANIZACAO_ACADEMICA\n"
    "2019;10;Universidade Pública do Paraná;UPP;1\n"
    "2019;20;Faculdade São João;FSJ;3\n"
)

# 2024-style layout: columns moved, new optional column missing (QT_SIT_FALECIDO),
# renamed IES file, a blank count cell.
COURSES_NEW = (
    "NU_ANO_CENSO;CO_IES;TP_REDE;NO_CURSO;CO_CURSO;TP_MODALIDADE_ENSINO;QT_MAT;QT_CONC;"
    "QT_SIT_DESVINCULADO;QT_SIT_TRANCADA;QT_SIT_TRANSFERIDO;NO_REGIAO;SG_UF\n"
    "2024;10;1;Matemática;100;1;90;15;10;;0;Sul;PR\n"
    "2024;30;2;Pedagogia;300;2;100;10;50;0;0;Nordeste;BA\n"
)
IES_NEW = (
    "NU_ANO_CENSO;CO_IES;NO_IES;SG_IES;TP_ORGANIZACAO_ACADEMICA\n"
    "2024;10;Universidade Pública do Paraná;UPP;1\n"
    "2024;30;Centro Universitário Ação;CUA;2\n"
)


def write_cp1252(path: Path, text: str) -> Path:
    """Write ``text`` the way INEP does (Windows-1252)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("cp1252"))
    return path


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    """Folder in the INEP encoding, one sub-folder per year."""
    root = tmp_path / "source"
    write_cp1252(root / "2019" / "MICRODADOS_CADASTRO_CURSOS_2019.CSV", COURSES_OLD)
    write_cp1252(root / "2019" / "MICRODADOS_CADASTRO_IES_2019.CSV", IES_OLD)
    write_cp1252(root / "2024" / "microdados_cadastro_cursos_2024.csv", COURSES_NEW)
    write_cp1252(root / "2024" / "MICRODADOS_ED_SUP_IES_2024.CSV", IES_NEW)
    return root
