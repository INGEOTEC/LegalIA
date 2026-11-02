"""`scripts/verify_scjn_outline.py` (issue #303) on a toy corpus written to
`tmp_path` with the real writer -- no network. Not part of `pytest packages/scjn`
(this directory is orchestration, not a package): run with

    pytest scripts/tests -q
"""

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "packages" / "scjn"))

import verify_scjn_outline as v  # noqa: E402
from scjn.api import Articulo, articulos_a_markdown, guarda_crudo  # noqa: E402
from scjn.outline import marker_line  # noqa: E402

HEADER = "---\nfuente: scjn\nordenamiento: LEY X\n---\n\n"

BLOCKS = [
    Articulo(0, 1, "ENCABEZADO", "LEY DE PRUEBA"),
    Articulo(0, 2, "TÍTULO PRIMERO", "TÍTULO PRIMERO"),
    Articulo(1, 3, "ARTÍCULO 1", "Artículo 1.- Texto del primero."),
    Articulo(0, 4, "N. DE E.", "[N. DE E. VÉASE ANEXO 1]"),  # an editorial aside: no text, no marker
    Articulo(2, 5, "ARTÍCULO 2", "Artículo 2.- Texto del segundo."),
]


def write(root: Path, collection: str, key: str, snapshot: str, body: str, blocks=None) -> Path:
    path = root / collection / key / f"{snapshot}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + body, encoding="utf-8")
    if blocks is not None:
        guarda_crudo(root / "raw-api" / collection / key / f"{snapshot}.json.gz", blocks)
    return path


def run(root: Path, collection: str = "leyes") -> dict:
    return v.verify_collection(root, root / "raw-api", collection)


def test_an_untouched_snapshot_is_identical_and_its_editorial_gap_is_explained(tmp_path):
    write(tmp_path, "leyes", "lx", "01-01-2020", articulos_a_markdown(BLOCKS, outline=True), BLOCKS)
    result = run(tmp_path)
    s = result["summary"]
    assert s["drift"] == {"identical": 1, "differs": 0, "no-raw": 0}
    assert s["markers"] == 4
    # orden 4 is a block the API served that wrote no text: explained, not an omission.
    assert (s["gaps_explained_snapshots"], s["gaps_explained_blocks"]) == (1, 1)
    assert s["gaps_unexplained_snapshots"] == 0
    assert (s["round_trip_violations"], s["invariance_violations"]) == (0, 0)
    assert s["unmarked_with_text"] == 0 and s["extra_markers"] == 0
    assert s["kinds"] == {"header": 1, "container": 1, "article": 2}
    assert s["types"] == {"title": 1}


def test_a_body_that_differs_from_the_raw_answer_is_drift_not_a_violation(tmp_path):
    body = articulos_a_markdown(BLOCKS, outline=True).replace("Texto del primero", "Texto viejo")
    write(tmp_path, "leyes", "lx", "01-01-2020", body, BLOCKS)
    result = run(tmp_path)
    assert result["summary"]["drift"] == {"identical": 0, "differs": 1, "no-raw": 0}
    assert result["findings"]["differs"] == ["lx/01-01-2020.md"]
    assert result["summary"]["round_trip_violations"] == 0


def test_a_snapshot_with_no_raw_answer_is_reported_and_its_gap_cannot_be_explained(tmp_path):
    blocks = [b for b in BLOCKS if b.orden != 3]  # orden 3 has no block, and no marker
    write(tmp_path, "leyes", "lx", "01-01-2020", articulos_a_markdown(blocks, outline=True))
    s = run(tmp_path)["summary"]
    assert s["drift"] == {"identical": 0, "differs": 0, "no-raw": 1}
    assert s["gaps_explained_snapshots"] == 0
    assert (s["gaps_unexplained_snapshots"], s["gaps_unexplained_blocks"]) == (1, 2)
    assert s["gaps_unexplained_outside_ledger"] == ["lx"]


def test_a_gap_the_raw_answer_does_not_serve_is_unexplained(tmp_path):
    served = [b for b in BLOCKS if b.orden != 3]  # the API never served orden 3
    write(tmp_path, "leyes", "lx", "01-01-2020", articulos_a_markdown(served, outline=True), served)
    s = run(tmp_path)["summary"]
    assert s["drift"]["identical"] == 1
    # orden 3 is unserved (unexplained); orden 4 is served and wrote nothing (explained).
    assert (s["gaps_unexplained_snapshots"], s["gaps_unexplained_blocks"]) == (1, 1)
    assert (s["gaps_explained_snapshots"], s["gaps_explained_blocks"]) == (1, 1)


def test_a_raw_block_with_text_and_no_marker_is_counted(tmp_path):
    body = articulos_a_markdown(BLOCKS, outline=True)
    body = body.replace(marker_line(5, 2, "ARTÍCULO 2") + "\n\n", "")  # the marker is lost, the text stays
    write(tmp_path, "leyes", "lx", "01-01-2020", body, BLOCKS)
    findings = run(tmp_path)["findings"]
    assert findings["unmarked_with_text"] == {"lx/01-01-2020.md": [5]}


def test_a_marker_that_is_not_what_the_writer_writes_is_a_round_trip_violation(tmp_path):
    body = articulos_a_markdown(BLOCKS, outline=True).replace(
        'kind=article num="1" label="ARTÍCULO 1"', 'kind=provision num="1" label="ARTÍCULO 1"'
    )
    write(tmp_path, "leyes", "lx", "01-01-2020", body, BLOCKS)
    s = run(tmp_path)["summary"]
    assert s["round_trip_violations"] >= 1
    lines = run(tmp_path)["findings"]["round_trip"]
    assert any("orden 3" in line and "lx/01-01-2020.md" in line for line in lines)


def test_a_marker_that_disagrees_with_the_raw_block_is_a_mismatch_and_drift_not_a_violation(tmp_path):
    # A snapshot older than the raw answer kept beside it: `lft` 04-12-2023 on 2026-11-01.
    body = articulos_a_markdown(BLOCKS, outline=True).replace("orden=3 numero=1", "orden=3 numero=7")
    write(tmp_path, "leyes", "lx", "01-01-2020", body, BLOCKS)
    s = run(tmp_path)["summary"]
    assert s["block_mismatches"] == 1 and s["drift"]["differs"] == 1
    assert s["round_trip_violations"] == 0


def test_the_exit_status_is_non_zero_only_for_a_contract_violation(tmp_path, capsys):
    good = tmp_path / "good"
    write(good, "leyes", "lx", "01-01-2020", articulos_a_markdown(BLOCKS, outline=True).replace(
        "Texto del primero", "Texto viejo"), BLOCKS)  # drift only
    assert v.main(["--coleccion", "leyes", "--outdir", str(good)]) == 0
    bad = tmp_path / "bad"
    write(bad, "leyes", "lx", "01-01-2020", articulos_a_markdown(BLOCKS, outline=True).replace(
        "kind=article", "kind=other"), BLOCKS)
    assert v.main(["--coleccion", "leyes", "--outdir", str(bad)]) == 1
    assert "VIOLATION round_trip" in capsys.readouterr().out


def test_compare_flags_a_change_in_the_drift_classes(tmp_path):
    write(tmp_path, "leyes", "lx", "01-01-2020", articulos_a_markdown(BLOCKS, outline=True), BLOCKS)
    before = [run(tmp_path)]
    same = [run(tmp_path)]
    assert v.compare(before, same) is True
    write(tmp_path, "leyes", "ly", "01-01-2020", "algo\n", BLOCKS)
    assert v.compare(before, [run(tmp_path)]) is False


def test_the_guard_audit_counts_each_shape_in_a_raw_answer(tmp_path):
    rows = lambda *ordenes: [
        {"numero": 0, "orden": o, "referencia": f"R{o}", "contenido": "x"} for o in ordenes
    ]
    assert v.audit_answer(rows(1, 2, 3))["refused"] is False
    gap = v.audit_answer(rows(1, 2, 5))
    assert gap["gaps"] and gap["refused"] and gap["defects"] == ["missing orden [3, 4]"]
    repeated = v.audit_answer(rows(1, 2, 2, 3))
    assert repeated["repeats"] == 1 and not repeated["refused"]
    shared = v.audit_answer(rows(1, 2, 3) + [{"numero": 0, "orden": 3, "referencia": "OTRO", "contenido": "y"}])
    assert shared["shared_orden"] == 1 and shared["repeats"] == 0 and not shared["refused"]
    unnumbered = v.audit_answer([{"numero": 0, "orden": None, "referencia": "A", "contenido": "x"}])
    assert unnumbered["no_orden"] == 1 and not unnumbered["refused"]


def test_audit_guard_reads_every_raw_file_of_a_collection(tmp_path):
    raw = tmp_path / "raw-api" / "leyes" / "lx"
    raw.mkdir(parents=True)
    for name, ordenes in (("01-01-2020", (1, 2, 3)), ("01-01-2021", (1, 3))):
        filas = [{"numero": 0, "orden": o, "referencia": "R", "contenido": "x"} for o in ordenes]
        with gzip.open(raw / f"{name}.json.gz", "wt", encoding="utf-8") as f:
            json.dump(filas, f)
    audit = v.audit_guard(tmp_path, tmp_path / "raw-api", "leyes")
    assert audit["counts"]["answers"] == 2
    assert audit["counts"]["with_gaps"] == 1 and audit["counts"]["refused_today"] == 1
    assert audit["examples"]["gaps"] == ["lx/01-01-2021: missing orden [2]"]


def test_the_vocabulary_has_one_example_per_branch_and_parses_back():
    vocabulary = v.build_vocabulary(None)
    kinds = {e["attributes"]["kind"] for e in vocabulary["examples"]}
    assert kinds == set(vocabulary["kinds"])
    types = {e["attributes"].get("type") for e in vocabulary["examples"]} - {None}
    assert types == set(vocabulary["container_types"])
    # The examples include a label with a quote, a backslash and `--`, and a marker with no orden.
    labels = {e["referencia"] for e in vocabulary["examples"]}
    assert 'ANEXO "A" \\ B -- C' in labels
    assert any(e["orden"] is None for e in vocabulary["examples"])
    for e in vocabulary["examples"]:
        assert e["line"].count("-->") == 1 and "\n" not in e["line"]


def test_write_vocabulary_is_stable_and_marker_distribution_counts_the_markers(tmp_path, capsys):
    write(tmp_path, "leyes", "lx", "01-01-2020", articulos_a_markdown(BLOCKS, outline=True))
    out = tmp_path / "vocabulary.json"
    argv = ["--coleccion", "leyes", "--outdir", str(tmp_path), "--write-vocabulary", str(out)]
    assert v.main(argv) == 0
    first = out.read_bytes()
    assert v.main(argv) == 0
    assert out.read_bytes() == first
    written = json.loads(first)
    assert written["distribution"]["leyes"] == {
        "snapshots": 1, "markers": 4,
        "kind": {"article": 2, "container": 1, "header": 1}, "type": {"title": 1},
    }
