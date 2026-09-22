"""The Atlas export (issue #244).

    uv run --group viz pytest scripts/embeddings/tests -q

The six-instrument toy corpus of `test_instrument_matrix.py`, its fixtures
imported rather than copied, so the matrix under the export is the one whose
every weight is derived on paper there. A stub reducer stands in for UMAP.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_instrument_umap_html  # noqa: E402
import export_atlas_data  # noqa: E402
import instrument_matrix  # noqa: E402
from test_instrument_matrix import (  # noqa: E402,F401
    cache, index_of, prepared, stub_umap, with_matrix)

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def exported(with_matrix, stub_umap, tmp_path):
    output = tmp_path / "site" / "atlas" / "atlas.json"
    measured = export_atlas_data.export(with_matrix, output, now=NOW, log=lambda *a: None)
    return {"work_dir": with_matrix, "output": output, "measured": measured,
            "data": json.loads(output.read_text(encoding="utf-8")),
            "matrix": np.load(instrument_matrix.output_dir(with_matrix) / "matrix.npy")}


def every_key_and_value(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from every_key_and_value(value)
    elif isinstance(node, list):
        for value in node:
            yield from every_key_and_value(value)
    else:
        yield node


def test_the_schema_is_meta_instruments_projections(exported):
    data = exported["data"]
    assert list(data) == ["meta", "instruments", "projections"]
    assert list(data["meta"]) == [
        "title", "generated", "commit", "model", "instruments", "provisions",
        "distinct_texts", "collections", "n_neighbors", "default_n_neighbors", "umap",
        "weighting", "top", "sources"]
    meta = data["meta"]
    assert meta["title"] == ("An Atlas of Mexican Federal Law: Laws, Regulations "
                             "and Guidelines")
    assert meta["generated"] == "2026-09-22T12:00:00+00:00"
    assert meta["commit"]
    assert meta["model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert meta["instruments"] == 6
    assert meta["distinct_texts"] == 9
    assert meta["n_neighbors"] == [4, 8, 16, 32]
    assert meta["default_n_neighbors"] == 16
    assert meta["umap"] == {"min_dist": 0.1, "metric": "cosine", "random_state": 0,
                            "umap_version": "stub"}
    assert meta["weighting"] == "1/m"
    assert meta["top"] == 5
    for entry in data["instruments"]:
        assert list(entry) == ["c", "k", "n", "p", "in", "out", "inc"]


def test_collections_and_provisions_come_from_the_table_and_the_matrix(exported):
    meta, matrix = exported["data"]["meta"], exported["matrix"]
    assert meta["collections"] == {"leyes": 3, "lineamientos": 3}
    assert meta["provisions"] == 13
    assert meta["provisions"] == pytest.approx(float(matrix.sum()), abs=1e-3)
    assert sum(entry["p"] for entry in exported["data"]["instruments"]) == meta["provisions"]
    assert meta["sources"] == ["scjn-leyes", "scjn-lineamientos",
                               "scjn-leyes-vectors", "scjn-lineamientos-vectors"]


def test_every_instrument_carries_its_provisions_and_both_directions(exported):
    data, matrix = exported["data"], exported["matrix"]
    table = pq.read_table(exported["work_dir"] / "instruments.parquet").to_pandas()
    assert len(data["instruments"]) == len(table) == matrix.shape[0]
    strongest = build_instrument_umap_html.strongest_targets
    for i, entry in enumerate(data["instruments"]):
        assert entry["k"] == table["clave"].iloc[i]
        assert entry["c"] == table["coleccion"].iloc[i]
        assert entry["n"] == table["nombre"].iloc[i]
        assert entry["p"] == int(table["units"].iloc[i])
        # The `1/m` rule: a row sums to the instrument's provisions.
        assert float(matrix[i].sum()) == pytest.approx(entry["p"], abs=1e-3)
        assert entry["in"] == round(float(matrix[:, i].sum()), 1)
        assert [j for j, _ in entry["out"]] == strongest(matrix, i)
        assert [w for _, w in entry["out"]] == [round(float(matrix[i, j]), 1)
                                              for j in strongest(matrix, i)]
        assert [j for j, _ in entry["inc"]] == strongest(matrix.T, i)
        assert [w for _, w in entry["inc"]] == [round(float(matrix[j, i]), 1)
                                              for j in strongest(matrix.T, i)]
        for pairs in (entry["out"], entry["inc"]):
            assert len(pairs) <= 5
            assert all(w > 0 for _, w in pairs)
            assert [w for _, w in pairs] == sorted((w for _, w in pairs), reverse=True)


def test_the_hand_computed_weights_survive_the_export(exported):
    """`b` points at `a` with 2 + 1/2 + 1/3 (`test_instrument_matrix`'s own
    derivation), and `a` receives 2.833 + 0.833 + 1 + 0.333 = 5."""
    data = exported["data"]
    index = index_of(exported["work_dir"])
    b = data["instruments"][index["b"]]
    assert b["out"][0] == [index["a"], 2.8]
    assert len(b["out"]) == 4
    a = data["instruments"][index["a"]]
    assert a["in"] == 5.0
    assert a["inc"][0] == [index["b"], 2.8]


def test_projections_are_rounded_unit_square_pairs_keyed_by_n_neighbors(
        with_matrix, stub_umap, tmp_path):
    output = tmp_path / "atlas.json"
    export_atlas_data.main(["--work-dir", str(with_matrix), "--output", str(output),
                            "--decimals", "2", "--n-neighbors", "8,16"])
    data = json.loads(output.read_text(encoding="utf-8"))
    assert list(data["projections"]) == ["8", "16"]
    assert data["meta"]["n_neighbors"] == [8, 16]
    table = pq.read_table(
        instrument_matrix.output_dir(with_matrix) / "umap.parquet").to_pandas()
    for key, pairs in data["projections"].items():
        assert len(pairs) == 6
        for i, (x, y) in enumerate(pairs):
            assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
            assert x == round(x, 2) and y == round(y, 2)
            assert x == pytest.approx(float(table[f"x{key}"].iloc[i]), abs=0.005)
            assert y == pytest.approx(float(table[f"y{key}"].iloc[i]), abs=0.005)


def test_the_file_is_compact_utf8_ending_in_a_newline(exported):
    text = exported["output"].read_text(encoding="utf-8")
    assert text.endswith("}\n") and text.count("\n") == 1
    assert ", " not in text.split('"instruments":[', 1)[1][:40]
    assert exported["measured"]["bytes"] == exported["output"].stat().st_size


def test_the_export_never_says_units_or_tooltip(exported):
    for item in every_key_and_value(exported["data"]):
        if isinstance(item, str):
            assert item not in ("units", "tooltip")
    text = exported["output"].read_text(encoding="utf-8")
    assert '"units"' not in text and "tooltip" not in text


def test_the_export_refits_nothing(with_matrix, stub_umap, tmp_path, monkeypatch):
    build_instrument_umap_html.project(with_matrix, log=lambda *a: None)
    out_dir = instrument_matrix.output_dir(with_matrix)
    before = {name: (out_dir / name).read_bytes()
              for name in ("matrix.npy", "umap.parquet", "umap.json")}
    monkeypatch.setattr(build_instrument_umap_html, "fit_one",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("refitted")))
    export_atlas_data.export(with_matrix, tmp_path / "atlas.json", log=lambda *a: None)
    assert {name: (out_dir / name).read_bytes() for name in before} == before


def test_a_projection_that_was_never_fitted_is_a_system_exit(with_matrix, stub_umap,
                                                            tmp_path):
    build_instrument_umap_html.project(with_matrix, n_neighbors=(4, 8), log=lambda *a: None)
    with pytest.raises(SystemExit, match="n_neighbors=\\[16\\]"):
        export_atlas_data.export(with_matrix, tmp_path / "atlas.json",
                                 n_neighbors=(4, 16), log=lambda *a: None)


@pytest.mark.parametrize("missing", ["matrix.npy", ".done"])
def test_a_missing_matrix_is_a_system_exit_naming_instrument_matrix(
        with_matrix, stub_umap, tmp_path, missing):
    (instrument_matrix.output_dir(with_matrix) / missing).unlink()
    with pytest.raises(SystemExit, match="instrument_matrix.py"):
        export_atlas_data.export(with_matrix, tmp_path / "atlas.json", log=lambda *a: None)
    assert not (tmp_path / "atlas.json").exists()


def test_an_instrument_that_points_nowhere_is_a_system_exit(with_matrix, stub_umap,
                                                           tmp_path):
    build_instrument_umap_html.project(with_matrix, log=lambda *a: None)
    path = instrument_matrix.output_dir(with_matrix) / "matrix.npy"
    matrix = np.load(path)
    matrix[1] = 0
    np.save(path, matrix)
    with pytest.raises(SystemExit, match="points nowhere"):
        export_atlas_data.export(with_matrix, tmp_path / "atlas.json", log=lambda *a: None)


def test_a_matrix_of_the_wrong_shape_is_a_system_exit(with_matrix, stub_umap, tmp_path):
    build_instrument_umap_html.project(with_matrix, log=lambda *a: None)
    path = instrument_matrix.output_dir(with_matrix) / "matrix.npy"
    np.save(path, np.load(path)[:5, :5])
    with pytest.raises(SystemExit, match="instrument_matrix.py"):
        export_atlas_data.export(with_matrix, tmp_path / "atlas.json", log=lambda *a: None)
