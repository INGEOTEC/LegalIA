"""`scripts/reconvert_scjn_tables.py` (issue #255), against a stub SCJN API
-- no network. Not part of `pytest packages/scjn` (this directory is
orchestration, not a package): run directly with

    pytest scripts/tests -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "packages" / "scjn"))

import reconvert_scjn_tables as rst  # noqa: E402
from scjn.api import Articulo, ScjnApiError  # noqa: E402


class StubApi:
    """A stand-in for `scjn.api.ScjnApi`: `answers` maps
    `(id_ordenamiento, reforma_id)` (as strings) to the `Articulo` list to
    hand back, `errors` to an exception to raise instead. Records every
    call so a test can assert exactly what was, or was not, requested."""

    def __init__(self, answers=None, errors=None):
        self.answers = answers or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, str]] = []

    def articulos_of_reforma(self, id_ordenamiento, id_reforma):
        key = (str(id_ordenamiento), str(id_reforma))
        self.calls.append(key)
        if key in self.errors:
            raise self.errors[key]
        return self.answers[key]


def _header(*, id_ordenamiento="42", reforma_id="7", fecha="01-01-2020") -> str:
    return (
        "---\n"
        "fuente: scjn\n"
        "ordenamiento: LEY DE PRUEBA\n"
        f"fecha_publicacion: {fecha}\n"
        f"id_ordenamiento: {id_ordenamiento}\n"
        f"reforma_id: {reforma_id}\n"
        "---"
    )


def _write_snapshot(directorio: Path, fecha: str, body: str, **header_kwargs) -> Path:
    directorio.mkdir(parents=True, exist_ok=True)
    archivo = directorio / f"{fecha}.md"
    archivo.write_text(_header(fecha=fecha, **header_kwargs) + "\n\n" + body, encoding="utf-8")
    return archivo


# -- words() / split_header_body() ----------------------------------------


def test_words_strips_table_markup_and_collapses_whitespace():
    assert rst.words("**a**\t|b|\n\nc\\ d") == ["a", "b", "c", "d"]


def test_split_header_body_round_trips():
    text = _header() + "\n\ncuerpo\ndel\ntexto\n"
    header, body = rst.split_header_body(text)
    assert header == _header()
    assert body == "cuerpo\ndel\ntexto\n"
    assert header + "\n\n" + body == text


# -- reconvert: rewritten ---------------------------------------------------


def test_rewrites_tabbed_snapshot_and_keeps_header_bytes(tmp_path):
    directorio = tmp_path / "leyes" / "lft"
    archivo = _write_snapshot(directorio, "01-01-2020", "a\tb\tc\n")
    stub = StubApi({("42", "7"): [Articulo(1, 1, "ARTÍCULO 1", "a\tb\tc")]})

    rc = rst.main(["--coleccion", "leyes", "--outdir", str(tmp_path)], api=stub)

    assert rc == 0
    assert stub.calls == [("42", "7")]
    new_text = archivo.read_text(encoding="utf-8")
    header, body = rst.split_header_body(new_text)
    assert header == _header()
    assert "\t" not in body
    assert "|" in body


# -- reconvert: untouched, no API call --------------------------------------


def test_snapshot_without_tab_is_untouched_and_triggers_no_call(tmp_path):
    directorio = tmp_path / "leyes" / "lft"
    archivo = _write_snapshot(directorio, "01-01-2020", "sin tabulador aqui\n")
    original = archivo.read_text(encoding="utf-8")
    stub = StubApi()

    rc = rst.main(["--coleccion", "leyes", "--outdir", str(tmp_path)], api=stub)

    assert rc == 0
    assert stub.calls == []
    assert archivo.read_text(encoding="utf-8") == original


# -- reconvert: mismatch -----------------------------------------------------


def test_word_mismatch_leaves_file_untouched_and_is_reported(tmp_path):
    directorio = tmp_path / "leyes" / "lft"
    archivo = _write_snapshot(directorio, "01-01-2020", "a\tb\tX\n")
    original = archivo.read_text(encoding="utf-8")
    stub = StubApi({("42", "7"): [Articulo(1, 1, "ARTÍCULO 1", "a\tb\tc")]})

    rc = rst.main(
        ["--coleccion", "leyes", "--outdir", str(tmp_path), "--report", str(tmp_path / "r.json")],
        api=stub,
    )

    assert rc == 0
    assert archivo.read_text(encoding="utf-8") == original
    import json

    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["summary"] == {"mismatch": 1}
    assert report["instruments"]["lft"][0]["outcome"] == "mismatch"
    assert "position" in report["instruments"]["lft"][0]["detail"]


# -- reconvert: API error, run continues -------------------------------------


def test_api_error_is_reported_as_failed_and_the_run_continues(tmp_path):
    fallida = _write_snapshot(
        tmp_path / "leyes" / "a", "01-01-2020", "x\ty\n", id_ordenamiento="1", reforma_id="1"
    )
    original_fallida = fallida.read_text(encoding="utf-8")
    exitosa = _write_snapshot(
        tmp_path / "leyes" / "b", "02-02-2020", "x\ty\n", id_ordenamiento="2", reforma_id="2"
    )
    stub = StubApi(
        answers={("2", "2"): [Articulo(1, 1, "ARTÍCULO 1", "x\ty")]},
        errors={("1", "1"): ScjnApiError("boom")},
    )

    rc = rst.main(["--coleccion", "leyes", "--outdir", str(tmp_path)], api=stub)

    assert rc == 1
    assert fallida.read_text(encoding="utf-8") == original_fallida
    assert "\t" not in exitosa.read_text(encoding="utf-8")
    assert set(stub.calls) == {("1", "1"), ("2", "2")}


# -- reconvert: resumable ----------------------------------------------------


def test_second_run_makes_zero_api_calls(tmp_path):
    archivo = _write_snapshot(tmp_path / "leyes" / "lft", "01-01-2020", "a\tb\tc\n")
    first = StubApi({("42", "7"): [Articulo(1, 1, "ARTÍCULO 1", "a\tb\tc")]})
    rst.main(["--coleccion", "leyes", "--outdir", str(tmp_path)], api=first)
    assert "\t" not in archivo.read_text(encoding="utf-8")

    second = StubApi()
    rc = rst.main(["--coleccion", "leyes", "--outdir", str(tmp_path)], api=second)

    assert rc == 0
    assert second.calls == []


# -- dry run ------------------------------------------------------------------


def test_dry_run_makes_zero_api_calls(tmp_path):
    archivo = _write_snapshot(tmp_path / "leyes" / "lft", "01-01-2020", "a\tb\tc\n")
    original = archivo.read_text(encoding="utf-8")
    stub = StubApi()

    rc = rst.main(["--coleccion", "leyes", "--outdir", str(tmp_path), "--dry-run"], api=stub)

    assert rc == 0
    assert stub.calls == []
    assert archivo.read_text(encoding="utf-8") == original


# -- control ------------------------------------------------------------------


def test_control_detects_a_split_that_does_not_round_trip(tmp_path):
    archivo = _write_snapshot(tmp_path / "leyes" / "lft", "01-01-2020", "esto no coincidira\n")
    original = archivo.read_text(encoding="utf-8")
    stub = StubApi({("42", "7"): [Articulo(1, 1, "ARTÍCULO 1", "un texto completamente distinto")]})

    rc = rst.main(["--coleccion", "leyes", "--outdir", str(tmp_path), "--control", "1"], api=stub)

    assert rc == 1
    assert stub.calls == [("42", "7")]
    assert archivo.read_text(encoding="utf-8") == original


def test_control_passes_when_snapshot_round_trips(tmp_path):
    body = "un texto completamente distinto\n"
    _write_snapshot(tmp_path / "leyes" / "lft", "01-01-2020", body)
    stub = StubApi({("42", "7"): [Articulo(1, 1, "ARTÍCULO 1", "un texto completamente distinto")]})

    rc = rst.main(["--coleccion", "leyes", "--outdir", str(tmp_path), "--control", "1"], api=stub)

    assert rc == 0
    assert stub.calls == [("42", "7")]
