"""`scripts/find_scjn_table_instruments.py` (issue #255, review fix), against
a stub SCJN API -- no network. Not part of `pytest packages/scjn` (this
directory is orchestration, not a package): run directly with

    pytest scripts/tests -q
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "packages" / "scjn"))

import find_scjn_table_instruments as fst  # noqa: E402
from scjn.api import Articulo, Reforma, ScjnApiError  # noqa: E402


class StubApi:
    """`reformas` maps `id_ordenamiento` to the `Reforma` list to hand back
    (newest first, the SCJN's own row order); `articulos` maps
    `(id_ordenamiento, reforma_id)` (as strings) to the `Articulo` list.
    `errors` maps either key shape to an exception to raise instead. Records
    every call so a test can assert exactly what was requested."""

    def __init__(self, reformas=None, articulos=None, errors=None):
        self.reformas = reformas or {}
        self.articulos = articulos or {}
        self.errors = errors or {}
        self.reforma_calls: list[str] = []
        self.articulo_calls: list[tuple[str, str]] = []

    def reformas_of_ordenamiento(self, id_ordenamiento):
        id_ordenamiento = str(id_ordenamiento)
        self.reforma_calls.append(id_ordenamiento)
        if id_ordenamiento in self.errors:
            raise self.errors[id_ordenamiento]
        return self.reformas[id_ordenamiento]

    def articulos_of_reforma(self, id_ordenamiento, id_reforma):
        key = (str(id_ordenamiento), str(id_reforma))
        self.articulo_calls.append(key)
        if key in self.errors:
            raise self.errors[key]
        return self.articulos[key]


def _seed_instrument(base: Path, coleccion: str, key: str, *, id_ordenamiento, n_snapshots=1) -> Path:
    directorio = base / coleccion / key
    directorio.mkdir(parents=True, exist_ok=True)
    (directorio / "estado.json").write_text(
        json.dumps({"id_ordenamiento": id_ordenamiento} if id_ordenamiento is not None else {}),
        encoding="utf-8",
    )
    for i in range(n_snapshots):
        (directorio / f"0{i + 1}-01-2020.md").write_text("---\n---\n\ncontenido\n", encoding="utf-8")
    return directorio


# -- latest-version selection ------------------------------------------------


def test_latest_version_skips_a_newer_reform_without_text(tmp_path):
    _seed_instrument(tmp_path, "leyes", "lft", id_ordenamiento="42")
    stub = StubApi(
        reformas={
            "42": [
                Reforma(reformaId=3, fecha_publicacion="03-03-2024", tieneArticulos=False),
                Reforma(reformaId=2, fecha_publicacion="02-02-2023", tieneArticulos=True),
                Reforma(reformaId=1, fecha_publicacion="01-01-2020", tieneArticulos=True),
            ]
        },
        articulos={("42", "2"): [Articulo(1, 1, "ARTÍCULO 1", "sin tabulador")]},
    )

    rc = fst.main(["--coleccion", "leyes", "--outdir", str(tmp_path)], api=stub)

    assert rc == 0
    assert stub.articulo_calls == [("42", "2")]
    output = json.loads((tmp_path / "leyes-table-instruments.json").read_text(encoding="utf-8"))
    assert output["lft"]["latest_reforma_id"] == 2
    assert output["lft"]["latest_fecha_publicacion"] == "02-02-2023"
    assert output["lft"]["has_table"] is False
    assert output["lft"]["reformas_scjn"] == 3


def test_a_tie_on_fecha_publicacion_is_broken_by_scjn_row_order(tmp_path):
    _seed_instrument(tmp_path, "leyes", "cpeum", id_ordenamiento="7")
    stub = StubApi(
        reformas={
            "7": [
                Reforma(reformaId=20, fecha_publicacion="01-01-2020", tieneArticulos=True),
                Reforma(reformaId=19, fecha_publicacion="01-01-2020", tieneArticulos=True),
            ]
        },
        articulos={("7", "20"): [Articulo(1, 1, "ARTÍCULO 1", "texto")]},
    )

    rst = fst.main(["--coleccion", "leyes", "--outdir", str(tmp_path)], api=stub)

    assert rst == 0
    assert stub.articulo_calls == [("7", "20")]


def test_no_text_bearing_reform_at_all_is_has_table_false_with_no_articulo_call(tmp_path):
    _seed_instrument(tmp_path, "leyes", "x", id_ordenamiento="9")
    stub = StubApi(reformas={"9": [Reforma(reformaId=1, fecha_publicacion="01-01-2020", tieneArticulos=False)]})

    rc = fst.main(["--coleccion", "leyes", "--outdir", str(tmp_path)], api=stub)

    assert rc == 0
    assert stub.articulo_calls == []
    output = json.loads((tmp_path / "leyes-table-instruments.json").read_text(encoding="utf-8"))
    assert output["x"]["has_table"] is False
    assert output["x"]["latest_reforma_id"] is None


# -- tab detection -------------------------------------------------------


def test_has_table_true_when_latest_version_carries_a_tab(tmp_path):
    _seed_instrument(tmp_path, "reglamentos", "100", id_ordenamiento="100")
    stub = StubApi(
        reformas={"100": [Reforma(reformaId=1, fecha_publicacion="01-01-2020", tieneArticulos=True)]},
        articulos={("100", "1"): [Articulo(1, 1, "ARTÍCULO 1", "a\tb\tc")]},
    )

    rc = fst.main(["--coleccion", "reglamentos", "--outdir", str(tmp_path)], api=stub)

    assert rc == 0
    output = json.loads((tmp_path / "reglamentos-table-instruments.json").read_text(encoding="utf-8"))
    assert output["100"]["has_table"] is True


# -- resume ----------------------------------------------------------------


def test_second_run_skips_already_probed_keys(tmp_path):
    _seed_instrument(tmp_path, "leyes", "lft", id_ordenamiento="42")
    first = StubApi(
        reformas={"42": [Reforma(reformaId=1, fecha_publicacion="01-01-2020", tieneArticulos=True)]},
        articulos={("42", "1"): [Articulo(1, 1, "ARTÍCULO 1", "texto")]},
    )
    fst.main(["--coleccion", "leyes", "--outdir", str(tmp_path)], api=first)

    second = StubApi()
    rc = fst.main(["--coleccion", "leyes", "--outdir", str(tmp_path)], api=second)

    assert rc == 0
    assert second.reforma_calls == []
    assert second.articulo_calls == []


# -- error recording ---------------------------------------------------------


def test_api_error_is_recorded_and_the_run_continues_and_exits_nonzero(tmp_path):
    _seed_instrument(tmp_path, "leyes", "a", id_ordenamiento="1")
    _seed_instrument(tmp_path, "leyes", "b", id_ordenamiento="2")
    stub = StubApi(
        reformas={"2": [Reforma(reformaId=1, fecha_publicacion="01-01-2020", tieneArticulos=True)]},
        articulos={("2", "1"): [Articulo(1, 1, "ARTÍCULO 1", "texto")]},
        errors={"1": ScjnApiError("boom")},
    )

    rc = fst.main(["--coleccion", "leyes", "--outdir", str(tmp_path)], api=stub)

    assert rc == 1
    output = json.loads((tmp_path / "leyes-table-instruments.json").read_text(encoding="utf-8"))
    assert output["a"]["error"] == "boom"
    assert output["a"]["has_table"] is None
    assert output["b"]["error"] is None
    assert isinstance(output["b"]["has_table"], bool)


# -- no id_ordenamiento ------------------------------------------------------


def test_instrument_without_id_ordenamiento_is_reported_and_skipped(tmp_path):
    _seed_instrument(tmp_path, "leyes", "sin-id", id_ordenamiento=None)
    stub = StubApi()

    rc = fst.main(["--coleccion", "leyes", "--outdir", str(tmp_path)], api=stub)

    assert rc == 1
    assert stub.reforma_calls == []
    assert stub.articulo_calls == []
    output = json.loads((tmp_path / "leyes-table-instruments.json").read_text(encoding="utf-8"))
    assert output["sin-id"]["error"] == "no_id_ordenamiento"
    assert output["sin-id"]["has_table"] is None
