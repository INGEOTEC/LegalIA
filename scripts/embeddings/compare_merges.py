"""Did an incremental re-embedding change only what it should? (issue #293)

`merge_shards.py` writes one vector file per instrument (the texts only that
instrument owns) plus one shared file (the texts several instruments own). After
an incremental re-embedding (issue #291: `plan_shards.py --against`, only the new
`text_sha1` encoded) the new merge must differ from the published one in exactly
the files whose content *has to* change, and in nothing else. This script proves
it, for one collection and one model, from the two work directories and nothing
else:

1. **The expected change is derived from the two `units.parquet` files**, not
   trusted from a list: an instrument's own file holds the texts it owns alone,
   so it changes exactly when that set changes (a new text, a dropped one, a text
   that became shared or stopped being shared); the shared file changes exactly
   when the set of shared texts does.
2. **The files that differ are exactly those** -- no file differs that should not
   (an unchanged text re-embedded, a different order of vectors is *not* a
   difference of content but is counted separately), none that should differ is
   equal, and a file gained or lost is one the units say must appear or go.
3. **Unchanged texts keep their vector bit for bit**: every `text_sha1` present
   in both merges has the same vector, wherever it sits (an instrument's file or
   the shared one).
4. **Every text of the new units has a vector**, and no file holds a text outside
   its own expected set.
5. **No vector is non-finite** (NaN or +-inf), in either merge.
6. With `--changed <compare_units.py --json output>` the differing instruments
   must also all be in that list (issue #291's list of changed instruments).

    python scripts/embeddings/compare_merges.py \\
        --published emb-run-whole-leyes --rebuilt emb-run-leyes-051 \\
        --model Qwen/Qwen3-Embedding-0.6B --changed /tmp/cmp/leyes.json

Read-only: neither work directory is written to. A failed check is a
`SystemExit` naming the files; `compare()` returns the same findings as data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

SHARED = "shared"


def model_slug(model: str) -> str:
    """`Qwen/Qwen3-Embedding-0.6B` -> `qwen3-0.6b` (`legalvec.model_slug`'s rule,
    restated: these scripts are not an importer of the packages)."""
    return model.rsplit("/", 1)[-1].lower().replace("-embedding", "")


def _owners(units_parquet: Path) -> dict[str, set[str]]:
    table = pq.read_table(units_parquet, columns=["clave", "text_sha1"])
    owners: dict[str, set[str]] = {}
    for clave, sha in zip(table.column("clave").to_pylist(), table.column("text_sha1").to_pylist()):
        owners.setdefault(sha, set()).add(clave)
    return owners


def expected_contents(units_parquet: Path) -> dict[str, frozenset[str]]:
    """`{clave | "shared": the text_sha1 that file must hold}` -- an instrument
    owns the texts no other instrument has, the shared file holds the rest.
    `merge_shards.py` writes a file for *every* instrument of the units (an
    empty one when all its texts are shared) and always the shared one, so every
    clave is a key, with an empty set when it owns nothing."""
    owners = _owners(units_parquet)
    contents: dict[str, set[str]] = {SHARED: set()}
    for owner_set in owners.values():
        for clave in owner_set:
            contents.setdefault(clave, set())
    for sha, owner_set in owners.items():
        key = next(iter(owner_set)) if len(owner_set) == 1 else SHARED
        contents[key].add(sha)
    return {key: frozenset(value) for key, value in contents.items()}


def _clave_of(name: str, slug: str) -> str:
    """`vectors-<clave>-<slug>-<K>.parquet` -> `<clave>` (`shared` for the shared file)."""
    return name[len("vectors-"):name.rindex(f"-{slug}-")]


def read_files(vectors_dir: Path, slug: str) -> dict[str, dict]:
    """`{clave: {"name", "hashes": [...], "digests": {sha: digest}, "finite": bool,
    "rows": n}}` for every vector file of `vectors_dir` -- a digest per text rather
    than the vector itself, so a collection's worth of 2,560-float rows is never
    held at once."""
    files = {}
    for path in sorted(vectors_dir.glob(f"vectors-*-{slug}-*.parquet")):
        table = pq.read_table(path)
        hashes = table.column("text_sha1").to_pylist()
        digests, finite = {}, True
        if hashes:
            flat = table.column("vector").combine_chunks().flatten().to_numpy(zero_copy_only=False)
            matrix = flat.reshape(len(hashes), -1)
            finite = bool(np.isfinite(matrix.astype(np.float32)).all())
            for sha, row in zip(hashes, matrix):
                digests[sha] = hashlib.sha1(np.ascontiguousarray(row).tobytes()).hexdigest()
        files[_clave_of(path.name, slug)] = {
            "name": path.name, "hashes": hashes, "digests": digests,
            "finite": finite, "rows": len(hashes),
        }
    return files


def compare(published: Path, rebuilt: Path, model: str, *, changed: set[str] | None = None) -> dict:
    """The five checks of the module docstring as data. `problems` is empty
    when every one holds; every other key is a measurement."""
    slug = model_slug(model)
    old_units, new_units = published / "units.parquet", rebuilt / "units.parquet"
    old_expected, new_expected = expected_contents(old_units), expected_contents(new_units)
    old = read_files(published / "runs" / slug / "vectors", slug)
    new = read_files(rebuilt / "runs" / slug / "vectors", slug)
    problems: list[str] = []

    # What the units say must differ.
    must_differ = {
        key for key in set(old_expected) | set(new_expected)
        if old_expected.get(key) != new_expected.get(key)
    }
    must_gain = {key for key in new_expected if key not in old_expected}
    must_lose = {key for key in old_expected if key not in new_expected}

    # What the files do.
    gained = sorted(set(new) - set(old))
    lost = sorted(set(old) - set(new))
    differing, reordered, equal = [], [], 0
    for key in sorted(set(old) & set(new)):
        if old[key]["digests"] != new[key]["digests"]:
            differing.append(key)
        elif old[key]["hashes"] != new[key]["hashes"]:
            reordered.append(key)
        else:
            equal += 1

    for key in sorted(set(gained) ^ must_gain):
        problems.append(f"file for {key!r}: gained={key in gained}, but the units say it must be gained={key in must_gain}")
    for key in sorted(set(lost) ^ must_lose):
        problems.append(f"file for {key!r}: lost={key in lost}, but the units say it must be lost={key in must_lose}")
    changed_files = set(differing) | set(gained) | set(lost)
    expected_changed = (must_differ & (set(old) & set(new))) | must_gain | must_lose
    for key in sorted(changed_files - expected_changed):
        problems.append(f"{key!r}: the file differs from the published one but its set of texts did not change")
    for key in sorted(expected_changed - changed_files):
        problems.append(f"{key!r}: its set of texts changed but the file is the published one")
    if changed is not None:
        for key in sorted(k for k in changed_files if k != SHARED and k not in changed):
            problems.append(f"{key!r}: differs but is not in the list of changed instruments")

    # Unchanged texts keep their vector, wherever they sit.
    old_digest = {sha: d for f in old.values() for sha, d in f["digests"].items()}
    new_digest = {sha: d for f in new.values() for sha, d in f["digests"].items()}
    moved = [sha for sha in old_digest.keys() & new_digest.keys() if old_digest[sha] != new_digest[sha]]
    if moved:
        problems.append(f"{len(moved)} text(s) present in both merges have a different vector, e.g. {moved[0]}")

    # Completeness: each file holds exactly its expected texts.
    for key, expected in new_expected.items():
        if key not in new:
            problems.append(f"{key!r}: no vector file in the new merge")
            continue
        held = set(new[key]["hashes"])
        if held != expected:
            problems.append(
                f"{key!r}: file holds {len(held - expected)} unexpected and misses {len(expected - held)} text(s)"
            )
    for key in sorted(set(new) - set(new_expected)):
        problems.append(f"{key!r}: a stale vector file, no such instrument in the new units")

    # Finite.
    non_finite = sorted(
        [("published", key) for key, f in old.items() if not f["finite"]]
        + [("rebuilt", key) for key, f in new.items() if not f["finite"]]
    )
    for side, key in non_finite:
        problems.append(f"{side} {key!r}: a vector is not finite")

    return {
        "model": model,
        "files_published": len(old), "files_rebuilt": len(new),
        "equal": equal, "reordered": reordered, "differing": differing,
        # Same name on the live release, different content: what an upload that
        # skips an asset of the same size cannot see (`package_vectors.py`).
        "changed_file_names": [new[key]["name"] for key in differing],
        "gained": gained, "lost": lost,
        "texts_published": len(old_digest), "texts_rebuilt": len(new_digest),
        "texts_new": len(new_digest.keys() - old_digest.keys()),
        "texts_dropped": len(old_digest.keys() - new_digest.keys()),
        "texts_with_a_different_vector": len(moved),
        "non_finite_files": non_finite,
        "problems": problems,
    }


def markdown(result: dict, collection: str) -> str:
    """One row per model, the shape the README section uses."""
    differing = [k for k in result["differing"] if k != SHARED]
    shared = SHARED in result["differing"]
    return (
        f"| {collection} | `{model_slug(result['model'])}` | {result['files_published']:,} → {result['files_rebuilt']:,} "
        f"| {result['equal'] + len(result['reordered']):,} | {len(differing)}{' + shared' if shared else ''} "
        f"| {len(result['gained'])} / {len(result['lost'])} | {result['texts_new']:,} / {result['texts_dropped']:,} "
        f"| {result['texts_with_a_different_vector']} | {len(result['non_finite_files'])} |"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--published", type=Path, required=True)
    parser.add_argument("--rebuilt", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--changed", type=Path,
                        help="`compare_units.py --json` output: the instruments allowed to differ")
    parser.add_argument("--json", type=Path, help="write the findings as JSON")
    parser.add_argument("--changed-files-out", type=Path,
                        help="write the names of the files that keep their name but changed "
                             "content, one per line, for `package_vectors.py --changed-files`")
    args = parser.parse_args(argv)
    changed = None
    if args.changed:
        changed = {entry["clave"] for entry in json.loads(args.changed.read_text())["changed"]}
    result = compare(args.published, args.rebuilt, args.model, changed=changed)
    if args.json:
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if args.changed_files_out:
        args.changed_files_out.write_text(
            "".join(name + "\n" for name in result["changed_file_names"]), encoding="utf-8")
    print(json.dumps({k: (v if not isinstance(v, list) or len(v) < 12 else f"{len(v)} files")
                      for k, v in result.items()}, ensure_ascii=False, indent=1))
    if result["problems"]:
        raise SystemExit(
            f"{len(result['problems'])} problem(s):\n  " + "\n  ".join(result["problems"][:40])
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
