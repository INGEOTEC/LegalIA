"""A local, `legalvec`-shaped cache holding vectors that are not published yet (issue #294).

Issue #291 re-embedded the texts `md2akn` 0.5.1 changed, and issue #293 verified
the merged result and wrote the `PUBLICAR.md` a human will run. Until that
happens `legalvec.load_units`/`load_vectors` would still serve the published,
stale files, so every script that takes `--cache-dir` (`prepare_umap_input.py`,
`export_instrument_files.py`, ...) is pointed at this directory instead:

    python scripts/embeddings/stage_vectors_cache.py \\
        --work-dir leyes=emb-run-leyes-051 --work-dir reglamentos=emb-run-reglamentos-051 \\
        --work-dir lineamientos=emb-run-lineamientos-051 --work-dir tratados=emb-run-tratados-051 \\
        --out emb-run-vectors-051-cache

The layout is `legalvec`'s own (`<out>/<release tag>/units.parquet`,
`vectors-<clave>-<model>-<K>.parquet`, ...), so nothing downstream knows it is a
staging area. Each collection's directory is built **from its merged work
directory alone**, which is complete: `merge_shards.py` writes every
per-instrument and shared file of both models, changed or not, and #293 proved
that the unchanged ones are bit-identical to the published files. Laying the
changed files over a copy of the published cache (the cache a machine has may
be incomplete anyway) would give the same bytes and could silently leave a stale
file behind; building from the merge cannot.

Files are hard-linked (the work directory and the cache share a filesystem; a
copy is made when they do not), so staging costs no disk. **Every file is
verified against the `SHA256SUMS.txt` that `package_vectors.py` wrote for the
merge**: a vector file, `units.parquet`, `leaves.parquet`,
`corpus-manifest.json` and `vectors-manifest.json` must hash to the listed digest, every listed file must be
staged and nothing else may be (`vectors-*` files of a model that is not in the
sums are refused rather than staged). A failed check is a `SystemExit` naming the
file. A staged directory is replaced as a whole and `staged.done` is written last,
so a half-built one is never read as complete. Read-only on the work directories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

#: The release tag (= cache subdirectory) of each collection, as `legalvec` names them.
TAGS = {
    "leyes": "scjn-leyes-vectors",
    "reglamentos": "scjn-reglamentos-vectors",
    "lineamientos": "scjn-lineamientos-vectors",
    "tratados": "scjn-tratados-vectors",
}

DONE_MARKER = "staged.done"
SUMS = "SHA256SUMS.txt"
#: What `package_vectors.py` lists next to the vector files: three files of the
#: work directory itself, and the manifest it wrote under `publish/`.
METADATA = ("units.parquet", "leaves.parquet", "corpus-manifest.json")
PUBLISHED_METADATA = ("vectors-manifest.json",)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_sums(path: Path) -> dict[str, str]:
    """`{file name: sha256}` of a `SHA256SUMS.txt`."""
    sums = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, name = line.split(None, 1)
            sums[name.strip()] = digest
    return sums


def sources(work_dir: Path) -> dict[str, Path]:
    """`{asset name: file}` of everything a merged work directory publishes."""
    files = {name: work_dir / name for name in METADATA if (work_dir / name).exists()}
    files.update({name: work_dir / "publish" / name for name in PUBLISHED_METADATA
                  if (work_dir / "publish" / name).exists()})
    for directory in sorted((work_dir / "runs").glob("*/vectors")):
        for path in sorted(directory.glob("vectors-*.parquet")):
            if path.name in files:
                raise SystemExit(f"{path.name} is in two models' vector directories of {work_dir}")
            files[path.name] = path
    return files


def link_or_copy(source: Path, target: Path) -> str:
    try:
        os.link(source, target)
        return "linked"
    except OSError:
        shutil.copy2(source, target)
        return "copied"


def stage_collection(work_dir: Path, out_dir: Path, *, log=print) -> dict:
    """Stage one merged work directory as `out_dir`, verified against its sums."""
    work_dir, out_dir = Path(work_dir), Path(out_dir)
    sums_path = work_dir / "publish" / SUMS
    if not sums_path.exists():
        raise SystemExit(f"{sums_path} does not exist: run package_vectors.py on {work_dir} first")
    sums = read_sums(sums_path)
    found = sources(work_dir)

    missing = sorted(set(sums) - set(found))
    extra = sorted(set(found) - set(sums))
    if missing or extra:
        raise SystemExit(
            f"{work_dir}: the files and {sums_path.name} disagree -- "
            f"{len(missing)} listed but absent (e.g. {missing[:3]}), "
            f"{len(extra)} present but not listed (e.g. {extra[:3]})"
        )

    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    how = {"linked": 0, "copied": 0}
    for name in sorted(found):
        actual = sha256(found[name])
        if actual != sums[name]:
            raise SystemExit(f"{found[name]}: sha256 {actual} is not the {sums[name]} that {sums_path} lists")
        how[link_or_copy(found[name], out_dir / name)] += 1
    # The sums file itself is part of a published release; keep it so the staged
    # directory still carries what `legalvec status` and a human look for.
    shutil.copy2(sums_path, out_dir / SUMS)
    (out_dir / DONE_MARKER).write_text("", encoding="utf-8")
    log(f"  {out_dir.name}: {len(found)} files verified ({how['linked']} linked, {how['copied']} copied)")
    return {"files": len(found), **how}


def parse_work_dirs(items: list[str]) -> dict[str, Path]:
    pairs = {}
    for item in items:
        collection, sep, path = item.partition("=")
        if not sep or collection not in TAGS:
            raise SystemExit(f"--work-dir expects <collection>=<directory>, collection one of {sorted(TAGS)}: {item!r}")
        pairs[collection] = Path(path)
    return pairs


def stage(work_dirs: dict[str, Path], out: Path, *, log=print) -> dict:
    """Stage every collection of `work_dirs` into `out/<tag>/`; returns a report."""
    report = {}
    for collection, work_dir in work_dirs.items():
        report[collection] = stage_collection(work_dir, Path(out) / TAGS[collection], log=log)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", action="append", required=True, metavar="COLLECTION=DIR",
                        help="a merged work directory, once per collection (repeatable)")
    parser.add_argument("--out", type=Path, required=True, help="the staging cache directory")
    args = parser.parse_args(argv)
    report = stage(parse_work_dirs(args.work_dir), args.out)
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
