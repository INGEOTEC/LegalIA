"""The Atlas pair explanations (issue #249).

    uv run --group viz pytest scripts/embeddings/tests -q

The six-instrument toy corpus of `test_instrument_matrix.py`, its fixtures
imported rather than copied, so every weight a pair file has to add up to is
the one derived on paper there. The toy `units.parquet` carries no
`num`/`path`/`piece`; `labelled` adds them after the matrix is built (the
join is on `text_sha1`, which it leaves alone), so the labels have something
to read.
"""

import hashlib
import io
import json
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_instrument_umap_html  # noqa: E402
import export_atlas_pairs  # noqa: E402
import instrument_matrix  # noqa: E402
from test_instrument_matrix import (  # noqa: E402,F401
    COLLECTIONS, cache, index_of, prepared, with_matrix)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)

#: `(clave, eId)` -> `(num, path, piece)` for the toy units that get one.
LABELS = {
    ("a", "art_1"): ("1", ["TÍTULO PRIMERO Disposiciones generales"], 0),
    ("a", "art_2"): ("Primero", ["TRANSITORIOS 29 DE AGOSTO DE 2008"], 0),
    ("b", "art_1"): ("1", ["CAPÍTULO I"], 0),
    ("b", "art_2"): ("Único", ["TRANSITORIOS"], 0),
    ("b", "cap_1"): ("I", [], 0),
    ("c", "art_1"): ("27", [], 0),
    ("900", "art_1"): ("PRIMERO", [], 0),
    ("900", "art_2"): ("SEGUNDO", [], 0),
}


def quiet(*args):
    pass


@pytest.fixture
def labelled(with_matrix, cache):
    """`num`/`path`/`piece` added to the toy `units.parquet`, in place."""
    for release in ("scjn-leyes-vectors", "scjn-lineamientos-vectors"):
        path = cache / release / "units.parquet"
        table = pq.read_table(path)
        keys = list(zip(table["clave"].to_pylist(), table["eId"].to_pylist()))
        num = [LABELS.get(key, ("3", [], 0))[0] for key in keys]
        crumbs = [LABELS.get(key, ("3", [], 0))[1] for key in keys]
        piece = [LABELS.get(key, ("3", [], 0))[2] for key in keys]
        table = table.append_column("num", pa.array(num, type=pa.string()))
        table = table.append_column("path", pa.array(crumbs, type=pa.list_(pa.string())))
        table = table.append_column("piece", pa.array(piece, type=pa.int32()))
        pq.write_table(table, path)
    return with_matrix


def run(work_dir, cache, out_dir=None, **kwargs):
    return export_atlas_pairs.export(work_dir, out_dir=out_dir, cache_dir=cache, now=NOW,
                                     log=quiet, **kwargs)


@pytest.fixture
def exported(labelled, cache):
    manifest = run(labelled, cache)
    out_dir = export_atlas_pairs.output_dir(labelled)
    matrix = np.load(instrument_matrix.output_dir(labelled) / "matrix.npy")
    nearest = pq.read_table(instrument_matrix.output_dir(labelled)
                            / "nearest.parquet").to_pandas()
    files = {path.name: json.loads(path.read_text(encoding="utf-8"))
             for path in sorted((out_dir / "pairs").iterdir())}
    return {"work_dir": labelled, "out_dir": out_dir, "manifest": manifest,
            "matrix": matrix, "nearest": nearest, "files": files,
            "index": index_of(labelled)}


# -- labels ------------------------------------------------------------------ #

def test_unit_label_on_every_unit_type():
    label = export_atlas_pairs.unit_label
    assert label("article", "27", ["TÍTULO PRIMERO", "CAPÍTULO I"], 0) == "Article 27"
    assert label("article_piece", "2", ["TÍTULO PRIMERO"], 3) == "Article 2, part 3"
    assert label("heading", "V", [], 0) == "Heading V"
    assert label("heading", None, [], 0, "**TÍTULO SEGUNDO** **DE LA FIRMA**") \
        == "Heading TÍTULO SEGUNDO DE LA FIRMA"
    assert label("loose", None, ["TRANSITORIOS 29 DE AGOSTO DE 2008"], 0) \
        == "Transitory provisions, 29 DE AGOSTO DE 2008"
    assert label("loose", None, ["CAPÍTULO 41 Pieles"], 2) == "Loose text"
    assert label("preamble") == "Preamble"
    assert label("conclusions") == "Closing"


def test_unit_label_names_a_transitory_article_by_its_block():
    label = export_atlas_pairs.unit_label
    assert label("article", "Único", ["TRANSITORIOS 29 DE AGOSTO DE 2008"], 0) \
        == "Transitory provisions, 29 DE AGOSTO DE 2008 · Único"
    # A block with no date, nested under the instrument's own name.
    assert label("article", "TERCERO", ["LEY General de Acceso", "TRANSITORIOS"], 0) \
        == "Transitory provisions · TERCERO"
    assert label("article_piece", "Segundo", ["TRANSITORIOS 10-03-2009"], 2) \
        == "Transitory provisions, 10-03-2009 · Segundo, part 2"
    # The breadcrumb is exported beside the label, never inside it.
    assert export_atlas_pairs.breadcrumb(["TÍTULO PRIMERO", "CAPÍTULO I"]) \
        == "TÍTULO PRIMERO › CAPÍTULO I"
    assert export_atlas_pairs.breadcrumb(None) == ""
    with pytest.raises(ValueError):
        label("paragraph", "1")


def test_labels_survive_a_units_table_without_num_or_path(with_matrix, cache):
    """The toy table as `test_instrument_matrix.py` writes it: no `num`,
    `path` or `piece` at all. The export still runs, with bare type words."""
    run(with_matrix, cache)
    out_dir = export_atlas_pairs.output_dir(with_matrix)
    labels = {row["label"] for path in (out_dir / "pairs").iterdir()
              for row in json.loads(path.read_text(encoding="utf-8"))["rows"]}
    assert labels == {"Article", "Heading transitorio compartido"}


# -- which pairs, which rows ------------------------------------------------- #

def test_one_file_per_strongest_target_and_none_for_a_zero_weight(exported):
    matrix = exported["matrix"]
    expected = {f"{i}-{j}.json"
                for i in range(matrix.shape[0])
                for j in build_instrument_umap_html.strongest_targets(matrix, i, 5)}
    assert set(exported["files"]) == expected
    assert all(matrix[int(name.split("-")[0]), int(name.split("-")[1][:-5])] > 0
               for name in exported["files"])
    # `b` points at four instruments, `900` at two.
    index = exported["index"]
    assert sum(name.startswith(f"{index['b']}-") for name in exported["files"]) == 4
    assert exported["manifest"]["pairs"] == len(expected) == 17


def test_each_file_is_the_matrix_cell_it_explains(exported):
    instruments = pq.read_table(exported["work_dir"] / "instruments.parquet").to_pandas()
    matrix, nearest = exported["matrix"], exported["nearest"]
    for name, document in exported["files"].items():
        i, j = (int(part) for part in name[:-5].split("-"))
        assert list(document) == ["source", "target", "weight", "provisions", "rows", "texts"]
        for side, index in (("source", i), ("target", j)):
            row = instruments.iloc[index]
            assert document[side] == {"i": index, "k": row["clave"], "c": row["coleccion"],
                                      "n": row["nombre"]}
        assert document["weight"] == round(float(matrix[i, j]), 1)
        assert sum(1 / row["m"] for row in document["rows"]) \
            == pytest.approx(float(matrix[i, j]), abs=1e-6)
        assert document["provisions"] == len(document["rows"])
        # Exactly nearest.parquet's rows of `i` whose targets hold `j`.
        expected = nearest[(nearest["i"] == i)
                           & nearest["targets"].apply(lambda targets: j in targets)]
        assert sorted((row["text"], row["m"]) for row in document["rows"]) \
            == sorted(zip(expected["row"].astype(int), expected["m"].astype(int)))
        for row in document["rows"]:
            assert list(row) == ["label", "path", "unit_type", "text", "targets",
                                 "similarity", "m"]
            assert list(row["targets"][0]) == ["label", "path", "text"]
            assert str(row["text"]) in document["texts"]
            assert all(str(target["text"]) in document["texts"] for target in row["targets"])
        similarities = [row["similarity"] for row in document["rows"]]
        assert similarities == sorted(similarities, reverse=True)


def test_every_winning_target_text_is_one_of_js_at_the_recorded_similarity(exported,
                                                                          cache):
    """Recomputed here with numpy, independently of the exporter: each
    `targets[].text` is a vector row a unit of `j` carries, and its cosine to
    the source row is the row's `similarity`."""
    work_dir = exported["work_dir"]
    vectors = np.load(work_dir / "vectors.npy").astype(np.float64)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    units = instrument_matrix.unit_rows(work_dir, collections=COLLECTIONS,
                                        cache_dir=cache, log=quiet)
    rows_of = units.groupby("i")["row"].apply(set).to_dict()
    for name, document in exported["files"].items():
        j = document["target"]["i"]
        for row in document["rows"]:
            assert row["targets"]
            for target in row["targets"]:
                assert target["text"] in rows_of[j]
                cosine = float(vectors[row["text"]] @ vectors[target["text"]])
                assert cosine == pytest.approx(row["similarity"], abs=1e-4)


def test_a_tie_lists_the_winner_in_each_instrument(exported):
    """`b`/t2 ties between t1 in `a` and t1 in `900`: each of the two files
    lists that one row with `m == 2`, pointing at its own copy of t1."""
    index, files = exported["index"], exported["files"]
    for target, label in (("a", "Article 1"), ("900", "Article SEGUNDO")):
        document = files[f"{index['b']}-{index[target]}.json"]
        tied = [row for row in document["rows"] if row["m"] == 2]
        assert len(tied) == 1
        assert [t["label"] for t in tied[0]["targets"]] == [label]
        assert document["texts"][str(tied[0]["targets"][0]["text"])] == "texto t1"


def test_a_text_repeated_inside_the_source_is_one_row_per_unit_and_one_text(exported):
    """`b` carries the shared transitorio twice (an article and a heading):
    two rows of `b -> a`, two labels, one entry in `texts`."""
    index = exported["index"]
    document = exported["files"][f"{index['b']}-{index['a']}.json"]
    shared = [row for row in document["rows"]
              if document["texts"][str(row["text"])] == "transitorio compartido"]
    assert len(shared) == 2
    assert len({row["text"] for row in shared}) == 1
    assert {row["label"] for row in shared} == {"Transitory provisions · Único",
                                               "Heading I"}
    assert list(document["texts"].values()).count("transitorio compartido") == 1
    # Both point at `a`'s own transitorio, labelled by its block and dated.
    for row in shared:
        assert [t["label"] for t in row["targets"]] \
            == ["Transitory provisions, 29 DE AGOSTO DE 2008 · Primero"]
        assert [t["path"] for t in row["targets"]] == ["TRANSITORIOS 29 DE AGOSTO DE 2008"]
        assert row["similarity"] == 1.0 and row["m"] == 1
    # 2 + 1/2 + 1/3, the cell `test_instrument_matrix.py` derives by hand.
    assert document["weight"] == 2.8
    assert document["provisions"] == 4
    # Similarity first, then the heavier weight (smaller `m`): the two m == 1
    # copies at 1.0, the boilerplate at 1.0 with m == 3, then the tie.
    assert [(row["similarity"], row["m"]) for row in document["rows"]][:3] \
        == [(1.0, 1), (1.0, 1), (1.0, 3)]


def test_a_boilerplate_winner_carried_by_three_instruments(exported):
    index = exported["index"]
    document = exported["files"][f"{index['902']}-{index['a']}.json"]
    assert document["provisions"] == 1
    (row,) = document["rows"]
    assert row["m"] == 3
    assert document["texts"][str(row["text"])] == "Se deroga."
    assert document["weight"] == 0.3


# -- the package ------------------------------------------------------------- #

def test_the_manifest_sums_and_publish_plan(exported):
    out_dir, manifest = exported["out_dir"], exported["manifest"]
    on_disk = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk == manifest
    assert list(manifest) == ["generated", "commit", "work_dir", "matrix", "top",
                              "tolerance", "pairs", "provisions", "weight", "bytes",
                              "largest", "release", "asset"]
    assert manifest["generated"] == "2026-09-23T12:00:00+00:00"
    assert manifest["top"] == 5
    assert manifest["tolerance"] == instrument_matrix.DEFAULT_TOLERANCE
    assert manifest["matrix"]["unit_rows"] == 13
    assert manifest["provisions"] == sum(d["provisions"] for d in exported["files"].values())
    # Every unit row of the toy corpus credits some instrument among the
    # closest five (no instrument has more than four targets), so the pairs
    # explain the whole matrix.
    assert manifest["weight"] == pytest.approx(13.0, abs=0.05)
    sizes = {name: (out_dir / "pairs" / name).stat().st_size for name in exported["files"]}
    assert manifest["bytes"] == sum(sizes.values())
    assert manifest["largest"]["bytes"] == max(sizes.values())
    assert manifest["largest"]["file"].startswith("pairs/")
    assert manifest["release"] == "atlas-pairs"
    assert manifest["asset"] == "atlas-pairs.tar.gz"

    for line in (out_dir / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ")
        assert hashlib.sha256((out_dir / name).read_bytes()).hexdigest() == digest
    assert sorted(line.split("  ")[1] for line in
                  (out_dir / "SHA256SUMS.txt").read_text().splitlines()) \
        == ["atlas-pairs.tar.gz", "manifest.json"]

    plan = (out_dir / "PUBLICAR.md").read_text(encoding="utf-8")
    assert "gh release create atlas-pairs --repo INGEOTEC/LegalIA" in plan
    assert '--title "LegalIA — Atlas pair explanations"' in plan
    assert "gh release upload atlas-pairs --repo INGEOTEC/LegalIA atlas-pairs.tar.gz" in plan
    assert "--clobber" in plan
    assert "--notes-file $REPO/.github/atlas-pairs.md" in plan
    assert plan.rstrip().endswith("(issue #115, Hallazgo C).")
    assert (out_dir / ".done").exists()


def test_the_tarball_holds_everything_and_is_reproducible(exported, cache):
    out_dir = exported["out_dir"]
    first = (out_dir / "atlas-pairs.tar.gz").read_bytes()
    with tarfile.open(fileobj=io.BytesIO(first), mode="r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        assert names[0] == "manifest.json"
        assert set(names) == {"manifest.json", "pairs"} | {
            f"pairs/{name}" for name in exported["files"]}
        assert all(member.mtime == 0 and member.uid == 0 and member.gid == 0
                   for member in members)
        name = next(iter(exported["files"]))
        assert json.load(archive.extractfile(f"pairs/{name}")) == exported["files"][name]
    run(exported["work_dir"], cache)
    assert (out_dir / "atlas-pairs.tar.gz").read_bytes() == first


def test_the_committed_release_body_exists():
    body = Path(__file__).resolve().parents[3] / ".github" / "atlas-pairs.md"
    assert body.exists()
    text = body.read_text(encoding="utf-8")
    assert "atlas-pairs.tar.gz" in text and "#115" in text


# -- reruns, install, refusals ------------------------------------------------ #

def test_done_makes_a_rerun_a_no_op_and_force_rewrites(exported, cache, monkeypatch):
    work_dir, out_dir = exported["work_dir"], exported["out_dir"]

    def explode(*args, **kwargs):
        raise AssertionError("export must not run again without --force")

    monkeypatch.setattr(export_atlas_pairs, "export", explode)
    argv = ["--work-dir", str(work_dir), "--cache-dir", str(cache)]
    assert export_atlas_pairs.main(argv) == 0

    seen = {}
    monkeypatch.setattr(export_atlas_pairs, "export",
                        lambda *args, **kwargs: seen.update(kwargs))
    assert export_atlas_pairs.main(argv + ["--force", "--top", "3"]) == 0
    assert seen["top"] == 3
    assert seen["out_dir"] == out_dir


def test_force_replaces_pairs_it_no_longer_has(exported, cache):
    stale = exported["out_dir"] / "pairs" / "9999-0.json"
    stale.write_text("{}")
    run(exported["work_dir"], cache)
    assert not stale.exists()
    assert not (exported["out_dir"] / "pairs.parcial").exists()


def test_install_replaces_the_directory(exported, cache, tmp_path):
    target = tmp_path / "site" / "pairs"
    target.mkdir(parents=True)
    (target / "0-99.json").write_text("{}")
    argv = ["--work-dir", str(exported["work_dir"]), "--cache-dir", str(cache),
            "--install", str(target)]
    assert export_atlas_pairs.main(argv) == 0
    assert sorted(p.name for p in target.iterdir()) == sorted(exported["files"])
    for name in exported["files"]:
        assert (target / name).read_bytes() \
            == (exported["out_dir"] / "pairs" / name).read_bytes()


def test_the_atlas_check_passes_on_its_own_export_and_fails_on_another(exported, cache,
                                                                         tmp_path):
    """`--atlas` compares against an `atlas.json` whose `out` lists are the
    same `weighted_targets` the exporter reads."""
    import export_atlas_data

    matrix = exported["matrix"]
    instruments = pq.read_table(exported["work_dir"] / "instruments.parquet").to_pandas()
    atlas = {"instruments": [
        {"k": clave, "out": export_atlas_data.weighted_targets(matrix, i, 5)}
        for i, clave in enumerate(instruments["clave"])]}
    path = tmp_path / "atlas.json"
    path.write_text(json.dumps(atlas))
    pairs = export_atlas_pairs.pairs_of(matrix)
    export_atlas_pairs.check_atlas(path, pairs, instruments)

    atlas["instruments"][0]["out"] = atlas["instruments"][0]["out"][:1]
    path.write_text(json.dumps(atlas))
    with pytest.raises(SystemExit):
        export_atlas_pairs.check_atlas(path, pairs, instruments)
    atlas["instruments"][0]["k"] = "zzz"
    path.write_text(json.dumps(atlas))
    with pytest.raises(SystemExit, match="different instrument table"):
        export_atlas_pairs.check_atlas(path, pairs, instruments)


def test_a_missing_matrix_is_refused_by_name(prepared, cache):
    with pytest.raises(SystemExit, match=r"instrument-matrix/\.done"):
        run(prepared, cache)


def test_a_recorded_similarity_the_vectors_disagree_with_is_refused(labelled, cache):
    path = instrument_matrix.output_dir(labelled) / "nearest.parquet"
    table = pq.read_table(path)
    similarity = table["similarity"].to_numpy().copy()
    similarity[0] -= 0.01
    table = table.set_column(table.schema.get_field_index("similarity"), "similarity",
                             pa.array(similarity, type=pa.float32()))
    pq.write_table(table, path)
    with pytest.raises(SystemExit, match="nearest.parquet recorded"):
        run(labelled, cache)
