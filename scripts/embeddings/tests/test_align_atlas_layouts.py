"""The alignment of the Atlas layouts (issue #310): synthetic data, no network.

    uv run --group viz pytest scripts/embeddings/tests/test_align_atlas_layouts.py -q

Every `UserWarning` from POT is an error for the whole module, so `numItermax` is
proved sufficient on a 2,670-point problem and not merely hoped for.
"""

import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import align_atlas_layouts as aligner  # noqa: E402

pytestmark = pytest.mark.filterwarnings("error::UserWarning")


def rotation(angle: float, mirrored: bool = False):
    matrix = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    return matrix @ np.diag([1.0, -1.0]) if mirrored else matrix


def cloud(n=200, seed=0):
    rng = np.random.default_rng(seed)
    return np.concatenate([rng.normal([0, 0], [1.0, 0.4], (n // 2, 2)),
                           rng.normal([3, 1], [0.3, 0.9], (n - n // 2, 2))])


# -- procrustes --------------------------------------------------------------- #

@pytest.mark.parametrize("mirrored", [False, True])
def test_procrustes_recovers_a_known_rotation_or_reflection_scale_and_translation(mirrored):
    target = cloud()
    source = (target - np.array([0.5, -0.2])) / 1.7 @ rotation(0.9, mirrored).T
    # source @ R_true = (target - shift) / 1.7 up to the orthogonal map
    n = len(target)
    scale, matrix, translation = aligner.procrustes(target, source, np.eye(n) / n)
    assert scale == pytest.approx(1.7, abs=1e-6)
    assert np.allclose(matrix @ matrix.T, np.eye(2), atol=1e-9)
    assert (np.linalg.det(matrix) < 0) == mirrored
    assert np.allclose(aligner.apply(source, (scale, matrix, translation)), target, atol=1e-6)


def test_procrustes_with_a_permutation_plan_recovers_the_transform_of_the_permuted_cloud():
    target = cloud(60, seed=1)
    permutation = np.random.default_rng(2).permutation(len(target))
    source = 0.4 * target[permutation] @ rotation(-1.1, True) + np.array([2.0, 3.0])
    plan = np.zeros((len(target), len(target)))
    plan[np.arange(len(target)), permutation] = 1.0 / len(target)      # source i <-> target[perm i]
    transform = aligner.procrustes(target, source, plan)
    assert transform[0] == pytest.approx(1 / 0.4, abs=1e-6)
    assert np.allclose(aligner.apply(source, transform), target[permutation], atol=1e-6)


# -- align -------------------------------------------------------------------- #

def test_align_undoes_a_rotated_mirrored_scaled_copy_exactly():
    target = cloud(150, seed=3)
    source = 2.5 * target @ rotation(2.0, True) + np.array([-4.0, 1.0])
    aligned, record = aligner.align(target, source)
    assert np.allclose(aligned, target, atol=1e-6)
    assert record["cost_after"] == pytest.approx(0.0, abs=1e-9)
    assert record["displacement_aligned"] == pytest.approx(0.0, abs=1e-6)
    assert record["diagonal_mass"] == pytest.approx(1.0)
    assert record["reflection"] is True
    assert record["cost_before"] > 0.1
    assert record["displacement_raw"] > 1


def test_align_is_not_dragged_by_the_instruments_that_moved():
    rng = np.random.default_rng(4)
    target = cloud(300, seed=5)
    source = 1.3 * target @ rotation(0.7) + np.array([1.0, -1.0])
    moved = rng.choice(len(source), 30, replace=False)
    source = source.copy()
    source[moved] = rng.uniform(-8, 8, (30, 2))                  # a tenth moved far away
    kept = np.setdiff1d(np.arange(len(source)), moved)
    aligned, record = aligner.align(target, source)
    plain = aligner.apply(source, aligner.procrustes(target, source, np.eye(len(target)) / len(target)))
    free = np.linalg.norm(aligned[kept] - target[kept], axis=1).mean()
    fixed = np.linalg.norm(plain[kept] - target[kept], axis=1).mean()
    assert free < fixed
    objective = record["objective"]
    assert all(later <= earlier + aligner.OBJECTIVE_TOLERANCE
               for earlier, later in zip(objective, objective[1:]))
    assert record["cost_after"] <= record["cost_before"]


def test_align_refuses_layouts_of_different_shapes():
    with pytest.raises(ValueError):
        aligner.align(np.zeros((5, 2)), np.zeros((4, 2)))


def test_pot_does_not_stop_early_on_2670_points():
    """The `numItermax` of POT's default warns on this size; ours must not (the module's
    warnings filter would raise)."""
    rng = np.random.default_rng(6)
    target, moved = rng.random((2670, 2)), rng.random((2670, 2))
    plan, cost = aligner.transport_plan(target, moved)
    assert plan.sum() == pytest.approx(1.0) and cost > 0


# -- common_frame ------------------------------------------------------------- #

def test_common_frame_maps_the_union_into_the_unit_square_touching_every_end():
    first, second = cloud(50, seed=7), cloud(50, seed=8) * 2 + 1
    frame = aligner.common_frame([first, second])
    framed = np.concatenate([aligner.apply_frame(layout, frame) for layout in (first, second)])
    assert framed.min(axis=0) == pytest.approx([0, 0]) and framed.max(axis=0) == pytest.approx([1, 1])
    assert framed.min() >= 0 and framed.max() <= 1


def test_common_frame_is_the_identity_on_layouts_already_in_the_unit_square():
    rng = np.random.default_rng(9)
    layout = rng.random((40, 2))
    layout[0], layout[1] = [0, 0], [1, 1]
    frame = aligner.common_frame([layout])
    assert frame == {"x": [0.0, 1.0], "y": [0.0, 1.0]}
    assert np.allclose(aligner.apply_frame(layout, frame), layout)


# -- files -------------------------------------------------------------------- #

def toy_file(path, *, model, seed, keys=("a", "b", "c", "d", "e", "f", "g", "h"),
             layouts=("8", "16"), extra_meta=None):
    base = cloud(len(keys), seed=0)
    rng = np.random.default_rng(seed)
    projections = {}
    for layout in layouts:
        turn = rotation(rng.uniform(0, 6), bool(rng.integers(2)))
        moved = 1.5 * (base + rng.normal(0, 0.05, base.shape)) @ turn
        low, high = moved.min(axis=0), moved.max(axis=0)
        projections[layout] = np.round((moved - low) / (high - low), 4).tolist()
    meta = {"title": "t", "model": model, "umap": {"random_state": 0}, "weighting": "1/m",
            **(extra_meta or {})}
    path.write_text(json.dumps({
        "meta": meta,
        "instruments": [{"c": "leyes", "k": key, "n": key.upper(), "p": 3, "pc": 2}
                        for key in keys],
        "projections": projections}, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8")
    return path


@pytest.fixture
def files(tmp_path):
    return [toy_file(tmp_path / "small.json", model="small", seed=1),
            toy_file(tmp_path / "large.json", model="large", seed=2),
            toy_file(tmp_path / "lexical.json", model="bm25", seed=3,
                     extra_meta={"bm25": {"k1": 1.5}})]


def quiet(*args):
    pass


def run(files, **kwargs):
    return aligner.align_files(files[1], 16, files, log=quiet, **kwargs)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_align_files_rewrites_every_file_with_meta_alignment_at_the_same_position(files):
    before = [read(path) for path in files]
    result = run(files)
    assert result["changed"] == files
    after = [read(path) for path in files]
    for old, new in zip(before, after):
        assert new["instruments"] == old["instruments"]
        position = list(new["meta"]).index("umap") + 1
        assert list(new["meta"])[position] == "alignment"
        assert [key for key in new["meta"] if key != "alignment"] == list(old["meta"])
        alignment = new["meta"]["alignment"]
        assert alignment["method"] == "wasserstein-procrustes"
        assert alignment["reference"] == {"file": "large.json", "model": "large",
                                          "n_neighbors": 16}
        assert list(alignment["layouts"]) == list(new["projections"])
        points = np.array([p for layout in new["projections"].values() for p in layout])
        assert points.min() >= 0 and points.max() <= 1
        assert all(len(str(v).split(".")[-1]) <= 4 for row in points.tolist() for v in row)
    reference = after[1]["meta"]["alignment"]["layouts"]["16"]
    assert reference["scale"] == pytest.approx(1.0) and reference["reflection"] is False
    assert reference["displacement_aligned"] == 0
    for new in after:
        for record in new["meta"]["alignment"]["layouts"].values():
            assert record["displacement_aligned"] <= record["displacement_raw"] + 1e-9
            assert record["cost_after"] <= record["cost_before"] + 1e-9


def test_align_files_is_idempotent_and_check_says_so(files):
    assert run(files, check=True)["changed"] == files           # unaligned: would change
    run(files)
    first = [path.read_bytes() for path in files]
    assert run(files, check=True)["changed"] == []              # aligned: nothing to do
    assert run(files)["changed"] == []
    assert [path.read_bytes() for path in files] == first
    assert aligner.main([str(p) for p in files] + ["--reference", str(files[1]), "--check"]) == 0


def test_check_exits_1_before_and_writes_nothing(files, capsys):
    snapshot = [path.read_bytes() for path in files]
    assert aligner.main([str(p) for p in files] + ["--reference", str(files[1]), "--check"]) == 1
    assert [path.read_bytes() for path in files] == snapshot


def test_a_file_with_a_swapped_instrument_is_refused_and_nothing_is_written(files):
    data = read(files[0])
    data["instruments"][2]["k"], data["instruments"][3]["k"] = \
        data["instruments"][3]["k"], data["instruments"][2]["k"]
    files[0].write_text(json.dumps(data), encoding="utf-8")
    snapshot = [path.read_bytes() for path in files]
    with pytest.raises(SystemExit, match="instrument 2 differs"):
        run(files)
    assert [path.read_bytes() for path in files] == snapshot


def test_a_file_missing_a_layout_is_refused(files):
    data = read(files[2])
    del data["projections"]["8"]
    files[2].write_text(json.dumps(data), encoding="utf-8")
    snapshot = [path.read_bytes() for path in files]
    with pytest.raises(SystemExit, match=r"layouts \['16'\]"):
        run(files)
    assert [path.read_bytes() for path in files] == snapshot


def test_a_file_aligned_onto_another_reference_is_refused_unless_forced(files):
    run(files)
    data = read(files[0])
    data["meta"]["alignment"]["reference"]["file"] = "elsewhere.json"
    files[0].write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SystemExit, match="--force"):
        run(files)
    assert run(files, force=True)["changed"]


def test_the_reference_must_be_among_the_files(files, tmp_path):
    other = toy_file(tmp_path / "other.json", model="x", seed=9)
    with pytest.raises(SystemExit, match="not among the files"):
        aligner.align_files(other, 16, files, log=quiet)


def test_a_reference_without_the_layout_is_refused(files):
    with pytest.raises(SystemExit, match="no layout 32"):
        aligner.align_files(files[1], 32, files, log=quiet)


def test_the_committed_files_are_aligned():
    """The three files the page loads carry the alignment and lie in the unit square."""
    pages = Path(__file__).resolve().parents[3] / "website" / "pages" / "atlas"
    for name in ("atlas.json", "atlas-qwen3-4b.json", "atlas-bm25.json"):
        data = json.loads((pages / name).read_text(encoding="utf-8"))
        assert data["meta"]["alignment"]["reference"] == {
            "file": "atlas-qwen3-4b.json", "model": "Qwen/Qwen3-Embedding-4B", "n_neighbors": 16}
        points = np.array([p for layout in data["projections"].values() for p in layout])
        assert points.min() >= 0 and points.max() <= 1
        assert list(data["meta"])[list(data["meta"]).index("umap") + 1] == "alignment"
    four = json.loads((pages / "atlas-qwen3-4b.json").read_text(encoding="utf-8"))
    sixteen = four["meta"]["alignment"]["layouts"]["16"]
    assert sixteen["scale"] == pytest.approx(1.0, abs=1e-6) and sixteen["reflection"] is False
    assert sixteen["displacement_aligned"] == 0
    for name in ("atlas.json", "atlas-qwen3-4b.json", "atlas-bm25.json"):
        data = json.loads((pages / name).read_text(encoding="utf-8"))
        for record in data["meta"]["alignment"]["layouts"].values():
            assert record["displacement_aligned"] <= record["displacement_raw"]
            assert record["cost_after"] <= record["cost_before"]
