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
    COLLECTIONS, cache, index_of, prepared, transitorio_work, with_matrix)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)

#: `(clave, eId)` -> `(num, path, piece)` for the toy units that get one.
LABELS = {
    ("a", "art_1"): ("1", ["TÍTULO PRIMERO Disposiciones generales"], 0),
    ("a", "art_2"): ("Primero", ["TÍTULO SEGUNDO"], 0),
    ("b", "art_1"): ("1", ["CAPÍTULO I"], 0),
    ("b", "art_2"): ("Único", ["CAPÍTULO II"], 0),
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
    assert labels == {"Article"}


# -- which pairs, which rows ------------------------------------------------- #

def test_one_file_per_strongest_target_and_none_for_a_zero_weight(exported):
    matrix = exported["matrix"]
    expected = {f"{i}-{j}.json"
                for i in range(matrix.shape[0])
                for j in build_instrument_umap_html.strongest_targets(matrix, i, 5)}
    assert set(exported["files"]) == expected
    assert all(matrix[int(name.split("-")[0]), int(name.split("-")[1][:-5])] > 0
               for name in exported["files"])
    # `b` points at two instruments, `902` at two, `901` at one.
    index = exported["index"]
    assert sum(name.startswith(f"{index['b']}-") for name in exported["files"]) == 2
    assert sum(name.startswith(f"{index['901']}-") for name in exported["files"]) == 1
    assert exported["manifest"]["pairs"] == len(expected) == 11


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
        # Exactly nearest.parquet's counted rows of `i` whose targets hold `j`.
        expected = nearest[(nearest["i"] == i) & nearest["counted"]
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


def test_a_heading_is_never_a_row_and_a_repeated_text_is_one_row_per_searched_unit(exported):
    """`b` carries the shared text in an article and in a heading: the
    heading is not searched, so `b -> a` lists one row for it, one entry in
    `texts`, next to the `t2` tie."""
    index = exported["index"]
    document = exported["files"][f"{index['b']}-{index['a']}.json"]
    shared = [row for row in document["rows"]
              if document["texts"][str(row["text"])] == "transitorio compartido"]
    assert len(shared) == 1
    assert shared[0]["label"] == "Article Único"
    assert list(document["texts"].values()).count("transitorio compartido") == 1
    # It points at `a`'s own copy, labelled by its number, with its breadcrumb.
    # (A transitorio is not searched any more, so no pair file carries one; the
    # "Transitory provisions" label is covered by the `unit_label` tests.)
    assert [t["label"] for t in shared[0]["targets"]] == ["Article Primero"]
    assert [t["path"] for t in shared[0]["targets"]] == ["TÍTULO SEGUNDO"]
    assert shared[0]["similarity"] == 1.0 and shared[0]["m"] == 1
    # 1 + 1/2, the cell `test_instrument_matrix.py` derives by hand.
    assert document["weight"] == 1.5
    assert document["provisions"] == 2
    # Similarity first: the identical text, then the tie.
    assert [(row["similarity"], row["m"]) for row in document["rows"]] \
        == [(1.0, 1), (0.9939, 2)]
    for document in exported["files"].values():
        assert all(row["unit_type"] != "heading" for row in document["rows"])
        assert not any(row["similarity"] == 1.0 and row["m"] > 1 for row in document["rows"])
        # Nor is a heading a target: no target label names one.
        assert not any(target["label"].startswith("Heading")
                       for row in document["rows"] for target in row["targets"])


def test_the_dropped_boilerplate_has_no_row_and_a_non_identical_tie_does(exported):
    """`902`'s "Se deroga." wins at cosine 1 with `m == 3`: not counted, so in
    no file. Its other unit ties over `a` and `b` at 1/2, and is the only row
    of both files."""
    index = exported["index"]
    for target in ("a", "b"):
        document = exported["files"][f"{index['902']}-{index[target]}.json"]
        assert document["provisions"] == 1
        (row,) = document["rows"]
        assert row["m"] == 2
        assert document["texts"][str(row["text"])] == "texto t6"
        assert document["weight"] == 0.5
    assert f"{index['902']}-{index['c']}.json" not in exported["files"]
    for document in exported["files"].values():
        assert "Se deroga." not in [document["texts"][str(row["text"])]
                                    for row in document["rows"]]


# -- the package ------------------------------------------------------------- #

def test_the_manifest_sums_and_publish_plan(exported):
    out_dir, manifest = exported["out_dir"], exported["manifest"]
    on_disk = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk == manifest
    assert list(manifest) == ["generated", "commit", "work_dir", "matrix", "top",
                              "tolerance", "method", "tolerance_relative", "pairs",
                              "provisions", "weight", "bytes",
                              "largest", "release", "model", "model_slug", "asset", "manifest",
                               "sums"]
    assert manifest["generated"] == "2026-09-23T12:00:00+00:00"
    assert manifest["top"] == 5
    assert manifest["tolerance"] == instrument_matrix.DEFAULT_TOLERANCE
    assert manifest["matrix"]["unit_rows"] == 16
    assert manifest["matrix"]["counted_rows"] == 9
    assert manifest["provisions"] == sum(d["provisions"] for d in exported["files"].values())
    # Every counted unit row of the toy corpus credits some instrument among
    # the closest five (no instrument has more than two targets), so the pairs
    # explain the whole matrix.
    assert manifest["weight"] == pytest.approx(9.0, abs=0.05)
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
    # Issue #259: the existing release is replaced in place, and that block
    # comes first, under the warning to do it before merging to `master`.
    assert "gh release edit atlas-pairs --repo INGEOTEC/LegalIA --notes-file" in plan
    assert plan.index("gh release upload") < plan.index("gh release create")
    assert plan.index("before the pull request") < plan.index("gh release upload")
    assert "merged to `master`" in plan
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


def test_a_work_dir_prepared_without_unique_names_is_refused(labelled, cache):
    record = labelled / "input.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data["unique_names"] = False
    record.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SystemExit, match="--unique-names"):
        run(labelled, cache)
    assert not (export_atlas_pairs.output_dir(labelled) / ".done").exists()


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


# -- one asset set per model (issue #261) ------------------------------------- #

FOUR_B = "Qwen/Qwen3-Embedding-4B"


def as_model(work_dir, model):
    record = work_dir / "input.json"
    data = json.loads(record.read_text(encoding="utf-8"))
    data["model"] = model
    record.write_text(json.dumps(data), encoding="utf-8")


def test_the_default_model_keeps_the_published_asset_names():
    names = export_atlas_pairs.asset_names("Qwen/Qwen3-Embedding-0.6B")
    assert names == {"model_slug": "qwen3-0.6b", "asset": "atlas-pairs.tar.gz",
                     "manifest": "manifest.json", "sums": "SHA256SUMS.txt"}


def test_any_other_model_adds_its_slug_to_every_name():
    assert export_atlas_pairs.asset_names(FOUR_B) == {
        "model_slug": "qwen3-4b", "asset": "atlas-pairs-qwen3-4b.tar.gz",
        "manifest": "manifest-qwen3-4b.json", "sums": "SHA256SUMS-qwen3-4b.txt"}


def test_a_default_model_manifest_records_the_model_and_the_unsuffixed_names(exported):
    manifest = exported["manifest"]
    assert manifest["model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert manifest["model_slug"] == "qwen3-0.6b"
    assert (manifest["asset"], manifest["manifest"], manifest["sums"]) \
        == ("atlas-pairs.tar.gz", "manifest.json", "SHA256SUMS.txt")
    assert json.loads((exported["out_dir"] / "manifest.json").read_text()) == manifest


@pytest.fixture
def exported_4b(labelled, cache):
    as_model(labelled, FOUR_B)
    manifest = run(labelled, cache)
    return {"work_dir": labelled, "out_dir": export_atlas_pairs.output_dir(labelled),
            "manifest": manifest}


def test_a_4b_export_writes_the_suffixed_assets_and_only_those(exported_4b):
    out_dir, manifest = exported_4b["out_dir"], exported_4b["manifest"]
    assert manifest["model"] == FOUR_B and manifest["model_slug"] == "qwen3-4b"
    assert manifest["release"] == "atlas-pairs"
    assert manifest["asset"] == "atlas-pairs-qwen3-4b.tar.gz"
    assert manifest["manifest"] == "manifest-qwen3-4b.json"
    assert manifest["sums"] == "SHA256SUMS-qwen3-4b.txt"
    names = {path.name for path in out_dir.iterdir()}
    assert {"atlas-pairs-qwen3-4b.tar.gz", "manifest-qwen3-4b.json",
            "SHA256SUMS-qwen3-4b.txt", "PUBLICAR.md", "pairs", ".done"} <= names
    assert not names & {"atlas-pairs.tar.gz", "manifest.json", "SHA256SUMS.txt"}
    assert json.loads((out_dir / "manifest-qwen3-4b.json").read_text()) == manifest

    sums = (out_dir / "SHA256SUMS-qwen3-4b.txt").read_text().splitlines()
    assert sorted(line.split("  ")[1] for line in sums) \
        == ["atlas-pairs-qwen3-4b.tar.gz", "manifest-qwen3-4b.json"]
    for line in sums:
        digest, name = line.split("  ")
        assert hashlib.sha256((out_dir / name).read_bytes()).hexdigest() == digest


def test_a_4b_tarball_keeps_the_layout_the_workflow_unpacks(exported_4b):
    out_dir = exported_4b["out_dir"]
    with tarfile.open(out_dir / "atlas-pairs-qwen3-4b.tar.gz", mode="r:gz") as archive:
        names = archive.getnames()
        assert names[0] == "manifest.json"
        assert "manifest-qwen3-4b.json" not in names
        assert "pairs" in names
        assert json.load(archive.extractfile("manifest.json")) \
            == json.loads((out_dir / "manifest-qwen3-4b.json").read_text())


def test_a_4b_publicar_adds_the_three_assets_to_the_existing_release(exported_4b):
    plan = (exported_4b["out_dir"] / "PUBLICAR.md").read_text(encoding="utf-8")
    assets = "atlas-pairs-qwen3-4b.tar.gz manifest-qwen3-4b.json SHA256SUMS-qwen3-4b.txt"
    assert f"gh release upload atlas-pairs --repo INGEOTEC/LegalIA {assets} --clobber" in plan
    assert "sha256sum -c SHA256SUMS-qwen3-4b.txt" in plan
    assert "**added**" in plan
    assert "Qwen/Qwen3-Embedding-4B" in plan
    assert "atlas-qwen3-4b.json" in plan and "before the pull request" in plan
    assert plan.index("before the pull request") < plan.index("gh release upload")
    assert plan.rstrip().endswith("(issue #115, Hallazgo C).")
    # The default model's assets are never named in an upload line.
    upload = next(line for line in plan.splitlines() if line.startswith("gh release upload"))
    assert " atlas-pairs.tar.gz" not in upload and " manifest.json" not in upload


def test_the_default_publicar_still_replaces_the_release_in_place(exported):
    plan = (exported["out_dir"] / "PUBLICAR.md").read_text(encoding="utf-8")
    assert "**added**" not in plan
    assert "Replace the `atlas-pairs` release before the pull request" in plan
    assert "`atlas.json` is merged" in plan


def test_install_into_an_arbitrary_directory_for_a_4b_export(exported_4b, cache, tmp_path):
    target = tmp_path / "site" / "pairs-qwen3-4b"
    argv = ["--work-dir", str(exported_4b["work_dir"]), "--cache-dir", str(cache),
            "--install", str(target)]
    assert export_atlas_pairs.main(argv) == 0
    installed = sorted(p.name for p in target.iterdir())
    assert installed == sorted(p.name for p in (exported_4b["out_dir"] / "pairs").iterdir())
    assert installed and all(name.endswith(".json") for name in installed)


# -- the lexical baseline's asset set (issue #267) ---------------------------- #

def test_the_bm25_model_adds_its_own_slug_to_every_name():
    import legalvec

    assert legalvec.model_slug("bm25") == "bm25"
    assert export_atlas_pairs.asset_names("bm25") == {
        "model_slug": "bm25", "asset": "atlas-pairs-bm25.tar.gz",
        "manifest": "manifest-bm25.json", "sums": "SHA256SUMS-bm25.txt"}


@pytest.fixture
def exported_bm25(labelled, cache, tmp_path):
    import prepare_bm25_input

    work_dir = tmp_path / "bm25"
    prepare_bm25_input.prepare(work_dir, labelled, cache_dir=cache, log=quiet)
    instrument_matrix.build_matrix(work_dir, collections=COLLECTIONS, cache_dir=cache,
                                   log=quiet)
    manifest = run(work_dir, cache)
    out_dir = export_atlas_pairs.output_dir(work_dir)
    matrix = np.load(instrument_matrix.output_dir(work_dir) / "matrix.npy")
    nearest = pq.read_table(instrument_matrix.output_dir(work_dir)
                            / "nearest.parquet").to_pandas()
    files = {path.name: json.loads(path.read_text(encoding="utf-8"))
             for path in sorted((out_dir / "pairs").iterdir())}
    return {"work_dir": work_dir, "out_dir": out_dir, "manifest": manifest, "matrix": matrix,
            "nearest": nearest, "files": files, "index": index_of(work_dir)}


def test_a_bm25_export_needs_no_vectors_and_writes_only_the_bm25_assets(exported_bm25):
    out_dir, manifest = exported_bm25["out_dir"], exported_bm25["manifest"]
    assert not (exported_bm25["work_dir"] / "vectors.npy").exists()
    assert manifest["model"] == "bm25" and manifest["model_slug"] == "bm25"
    assert manifest["method"] == "bm25" and manifest["tolerance_relative"] is True
    assert manifest["release"] == "atlas-pairs"
    assert (manifest["asset"], manifest["manifest"], manifest["sums"]) \
        == ("atlas-pairs-bm25.tar.gz", "manifest-bm25.json", "SHA256SUMS-bm25.txt")
    names = {path.name for path in out_dir.iterdir()}
    assert {"atlas-pairs-bm25.tar.gz", "manifest-bm25.json", "SHA256SUMS-bm25.txt",
            "PUBLICAR.md", "pairs", ".done"} <= names
    assert not names & {"atlas-pairs.tar.gz", "manifest.json", "SHA256SUMS.txt"}
    assert json.loads((out_dir / "manifest-bm25.json").read_text()) == manifest
    sums = (out_dir / "SHA256SUMS-bm25.txt").read_text().splitlines()
    for line in sums:
        digest, name = line.split("  ")
        assert hashlib.sha256((out_dir / name).read_bytes()).hexdigest() == digest
    with tarfile.open(out_dir / "atlas-pairs-bm25.tar.gz", mode="r:gz") as archive:
        assert archive.getnames()[0] == "manifest.json"


def test_every_bm25_pair_adds_up_to_its_cell(exported_bm25):
    """The toy BM25 matrix has `b -> a` = 1.2 (a whole `ts` plus a fifth of `t2`)."""
    matrix, index = exported_bm25["matrix"], exported_bm25["index"]
    files = exported_bm25["files"]
    assert files
    for name, document in files.items():
        i, j = (int(part) for part in name[:-len(".json")].split("-"))
        total = sum(1.0 / row["m"] for row in document["rows"])
        assert total == pytest.approx(float(matrix[i, j]), abs=1e-3)
        assert document["provisions"] == len(document["rows"])
    document = files[f"{index['b']}-{index['a']}.json"]
    assert document["weight"] == pytest.approx(1.2, abs=0.05)
    assert sorted(row["m"] for row in document["rows"]) == [1, 5]


def test_a_bm25_pair_names_the_tied_target_texts_with_the_raw_score(exported_bm25):
    index = exported_bm25["index"]
    document = exported_bm25["files"][f"{index['b']}-{index['a']}.json"]
    tied = next(row for row in document["rows"] if row["m"] == 5)
    assert tied["similarity"] == pytest.approx(0.1532, abs=1e-4)
    # `a` owns exactly one of the six tied texts: its `t1`.
    assert len(tied["targets"]) == 1
    assert document["texts"][str(tied["targets"][0]["text"])] == "texto t1"
    identical = next(row for row in document["rows"] if row["m"] == 1)
    assert document["texts"][str(identical["targets"][0]["text"])] == "transitorio compartido"


def test_a_bm25_publicar_adds_the_three_assets_and_says_it_is_not_an_embedding(exported_bm25):
    plan = (exported_bm25["out_dir"] / "PUBLICAR.md").read_text(encoding="utf-8")
    assets = "atlas-pairs-bm25.tar.gz manifest-bm25.json SHA256SUMS-bm25.txt"
    assert f"gh release upload atlas-pairs --repo INGEOTEC/LegalIA {assets} --clobber" in plan
    assert "sha256sum -c SHA256SUMS-bm25.txt" in plan
    assert "**added**" in plan and "atlas-bm25.json" in plan
    assert "BM25" in plan and "not an embedding" in plan
    assert plan.index("before the pull request") < plan.index("gh release upload")


def test_a_bm25_recomputation_that_disagrees_with_the_matrix_is_refused(exported_bm25, cache):
    out = instrument_matrix.output_dir(exported_bm25["work_dir"]) / "nearest.parquet"
    table = pq.read_table(out)
    column = table.schema.get_field_index("similarity")
    values = table.column("similarity").to_pylist()
    values = [value * 1.5 for value in values]
    table = table.set_column(column, "similarity", pa.array(values, type=pa.float32()))
    pq.write_table(table, out)
    with pytest.raises(SystemExit, match="nearest.parquet recorded"):
        run(exported_bm25["work_dir"], cache)


def test_a_bm25_export_without_its_index_is_a_system_exit(exported_bm25, cache):
    (exported_bm25["work_dir"] / "tokens.parquet").unlink()
    with pytest.raises(SystemExit, match="tokens.parquet"):
        run(exported_bm25["work_dir"], cache)


# -- transitorios are not searched (issue #264) ------------------------------- #

def test_load_narrows_the_join_like_the_matrix_when_transitorios_exist(transitorio_work):
    """`load` asserts `nearest.parquet` lines up with the join narrowed by
    `instrument_matrix.searched_mask`; over a corpus with transitorios (and a
    heading inside one) that only holds if both use the same predicate."""
    work_dir, cache = transitorio_work
    instrument_matrix.build_matrix(work_dir, collections=("leyes",), cache_dir=cache,
                                   log=quiet)
    data = export_atlas_pairs.load(work_dir, cache_dir=cache, log=quiet)
    assert len(data["nearest"]) == len(data["points"]) == 8
    assert not data["nearest"]["unit_type"].eq("heading").any()
    assert data["summary"]["transitorio_rows_excluded"] == 8
    assert export_atlas_pairs.instrument_matrix.searched_mask is instrument_matrix.searched_mask
