"""Export the evidence behind every *Closest instruments* weight of the Atlas.

Issue #249. The Atlas (`website/pages/atlas.qmd`, issues #244/#245) says the
Constitution is closest to the *LEY General de Instituciones y Procedimientos
Electorales* with a weight of 116.6, and nothing more. That number is a sum:
every provision (unit row) of the Constitution whose nearest text outside the
Constitution belongs to the LGIPE adds `1/m` to it, `m` being how many
instruments own that winning text (issue #242's rule, `instrument_matrix.py`).
This script writes, for every pair the panel lists under **Closest
instruments**, which provisions those are and what they matched, with the full
text of both sides:

    uv run --group viz python scripts/embeddings/export_atlas_pairs.py \\
        --install website/pages/atlas/pairs
    uv run --group viz python scripts/embeddings/export_atlas_pairs.py \\
        --atlas website/pages/atlas/atlas.json --force

It is a pure read of #242's outputs — `instrument-matrix/matrix.npy`,
`nearest.parquet`, `matrix.json`, plus #241's `vectors.npy`,
`vector_ids.parquet` and `instruments.parquet` and the `legalvec` cache —
and never touches any of them. No Slurm, no network, ~3 minutes.

* **Which pairs.** `export_atlas_data.weighted_targets` over `matrix.npy`,
  imported, so the files are exactly `atlas.json`'s `out` lists: 7,604 pairs,
  file `pairs/<i>-<j>.json`, `i`/`j` being positions in `atlas.json`'s
  `instruments` array (= `instruments.parquet`'s `i`). `--atlas` checks an
  existing `atlas.json` against that, pair by pair and `clave` by `clave`.
* **Which rows.** One per **unit row** of `nearest.parquet` whose `targets`
  hold `j` — a text repeated inside the source counts once per repetition,
  exactly as in the matrix — so `sum(1/m)` over a file's rows is
  `matrix[i, j]`, which is asserted to 1e-3 for every pair.
* **Which target texts won.** `nearest.parquet` does not record the winning
  vector rows, and recording them would mean rerunning #242's Slurm job. So
  they are recomputed here, per pair, by the same exact cosine
  `instrument_matrix.py` used, over `j`'s own rows only: every `j` row within
  `--tolerance` of the recorded best wins. Since `j` owns a global winner, its
  own best *is* the global best — asserted for every row, a `SystemExit`
  naming `i`, `j` and the vector row otherwise.
* **Labels** are English for the type word and the corpus' own spelling for
  everything else (`unit_label`); the breadcrumb (`path`) is exported beside
  the label, never merged into it.

Outputs, under `--out-dir` (default `<work-dir>/atlas-pairs`, gitignored):
`pairs/`, `manifest.json`, `atlas-pairs.tar.gz` (reproducible: sorted members,
fixed mtime/uid/gid, gzip mtime 0), `SHA256SUMS.txt` and `PUBLICAR.md`, with
`.done` last; a rerun with `.done` present exports nothing unless `--force`.
`--install DIR` then replaces `DIR` with a copy of `pairs/` (the site's own,
gitignored `website/pages/atlas/pairs/`).

**Nothing here publishes anything.** The files are ~264 MB, too much for
`master`, and GitHub release assets carry no CORS header, so the page cannot
read them from a release either: a human publishes the tarball as the release
`atlas-pairs` by running `PUBLICAR.md`, and the website's publish workflow
unpacks it into the site (issue #250). Issue #115, Hallazgo C.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import re
import shutil
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_umap_html as html  # noqa: E402
import instrument_matrix  # noqa: E402
from build_instrument_umap_html import TOP_TARGETS  # noqa: E402
from export_atlas_data import weighted_targets  # noqa: E402

RELEASE = "atlas-pairs"
ASSET = "atlas-pairs.tar.gz"
DEFAULT_REPO = "INGEOTEC/LegalIA"
SUBDIR = "atlas-pairs"
PAIRS = "pairs"

#: The breadcrumb separator. A single character a Spanish legal heading never
#: contains, so the page can show the path as one line without escaping.
PATH_SEPARATOR = " › "

#: Between a transitorios block's own name and the article inside it.
TRANSITORY_SEPARATOR = " · "

#: The per-pair sums are asserted at the same tolerance `matrix.json`'s
#: `row_sums_equal_units` uses: `matrix.npy` is float32.
SUM_TOLERANCE = 1e-3

#: Every tarball member gets this mtime, so two exports of the same pairs are
#: byte-identical and `SHA256SUMS.txt` means something across runs.
TAR_MTIME = 0

#: The columns `units.parquet` carries for a label and a text, forwarded
#: through `build_umap_html.load_frames` — the one join — never re-joined here.
LABEL_COLUMNS = ("num", "path", "piece", "text")

#: What the export needs on disk, relative to the work directory.
REQUIRED = (
    f"{instrument_matrix.SUBDIR}/.done",
    f"{instrument_matrix.SUBDIR}/matrix.npy",
    f"{instrument_matrix.SUBDIR}/nearest.parquet",
    "vectors.npy",
    "vector_ids.parquet",
    "instruments.parquet",
)

#: A transitorios block in a unit's `path`: `TRANSITORIOS`, `TRANSITORIOS 29
#: DE AGOSTO DE 2008`, `TRANSITORIOS DE 5 DE ...`, `TRANSITORIOS 10-03-2009`.
_TRANSITORIOS = re.compile(r"^\s*TRANSITORIOS\b\s*(.*)$", re.IGNORECASE)
_BOLD = re.compile(r"\*\*")


def output_dir(work_dir: Path) -> Path:
    return Path(work_dir) / SUBDIR


def _present(value) -> bool:
    """A corpus field that is actually there: not None, not NaN, not ''."""
    if value is None:
        return False
    if isinstance(value, float) and value != value:
        return False
    return str(value).strip() != ""


def _path_list(path) -> list[str]:
    """`units.parquet`'s `path` (a list, a numpy array or null) as strings."""
    if path is None or isinstance(path, float):
        return []
    return [str(element) for element in path]


def breadcrumb(path) -> str:
    return PATH_SEPARATOR.join(_path_list(path))


def unit_label(unit_type: str, num=None, path=None, piece=None, text=None) -> str:
    """A human-readable name for one unit, English for the type word and the
    corpus' own spelling for everything else.

    * `article` -> `Article 27`; inside a transitorios block,
      `Transitory provisions, 29 DE AGOSTO DE 2008 · Único`.
    * `article_piece` -> `Article 2, part 3` (the same, with `, part N`, in a
      transitorios block).
    * `heading` -> `Heading V`, or the heading's own text when `num` is empty.
    * `loose` -> `Transitory provisions[, <date>]` in a transitorios block,
      `Loose text` otherwise.
    * `preamble` -> `Preamble`; `conclusions` -> `Closing`.

    `path` only decides whether a unit sits in a transitorios block and names
    the block; the breadcrumb itself is `breadcrumb(path)`, exported beside
    the label. An unknown `unit_type` is a `ValueError`: md2akn has six.
    """
    transitory = None
    for element in reversed(_path_list(path)):
        match = _TRANSITORIOS.match(element)
        if match:
            qualifier = match.group(1).strip()
            transitory = "Transitory provisions" + (f", {qualifier}" if qualifier else "")
            break
    number = str(num).strip() if _present(num) else ""

    if unit_type in ("article", "article_piece"):
        if transitory:
            label = transitory + (f"{TRANSITORY_SEPARATOR}{number}" if number else "")
        else:
            label = f"Article {number}" if number else "Article"
        if unit_type == "article_piece" and _present(piece):
            label += f", part {int(piece)}"
        return label
    if unit_type == "heading":
        if number:
            return f"Heading {number}"
        heading = _BOLD.sub("", str(text)).strip() if _present(text) else ""
        return f"Heading {heading}" if heading else "Heading"
    if unit_type == "loose":
        return transitory or "Loose text"
    if unit_type == "preamble":
        return "Preamble"
    if unit_type == "conclusions":
        return "Closing"
    raise ValueError(f"unknown unit_type {unit_type!r}")


def check_inputs(work_dir: Path) -> None:
    """A `SystemExit` naming the first input that is not on disk."""
    for name in REQUIRED:
        path = Path(work_dir) / name
        if not path.exists():
            raise SystemExit(f"{path} is missing -- this export reads what "
                             "instrument_matrix.py (issue #242) and "
                             "prepare_umap_input.py (issue #241) wrote")


def pairs_of(matrix, top: int = TOP_TARGETS) -> list[tuple[int, int, float]]:
    """`(i, j, weight)` for every `j` among `i`'s `top` strongest targets —
    `atlas.json`'s own `out` lists, from the function that writes them."""
    return [(i, j, weight) for i in range(matrix.shape[0])
            for j, weight in weighted_targets(matrix, i, top)]


def check_atlas(atlas_path: Path, pairs, instruments) -> None:
    """An existing `atlas.json` must list exactly these pairs, with these
    weights, over the same instrument table — or the page cannot find a file,
    or finds the wrong one."""
    atlas = json.loads(Path(atlas_path).read_text(encoding="utf-8"))
    entries = atlas["instruments"]
    claves = instruments["clave"].astype(str).tolist()
    if [entry["k"] for entry in entries] != claves:
        raise SystemExit(f"{atlas_path} was built over a different instrument table "
                         "than instruments.parquet: rerun export_atlas_data.py")
    listed = [(i, int(j), float(w)) for i, entry in enumerate(entries) for j, w in entry["out"]]
    if listed != [(i, j, float(w)) for i, j, w in pairs]:
        raise SystemExit(f"{atlas_path}'s `out` lists are not matrix.npy's strongest "
                         "targets: rerun export_atlas_data.py")


def load(work_dir: Path, *, cache_dir=None, log=print) -> dict:
    """Everything the export reads, aligned: `nearest.parquet` row `n` is
    unit row `n` of the shared join, which is asserted rather than assumed."""
    import numpy as np
    import pyarrow.parquet as pq

    work_dir = Path(work_dir)
    out_dir = instrument_matrix.output_dir(work_dir)
    matrix = np.load(out_dir / "matrix.npy")
    summary = json.loads((out_dir / "matrix.json").read_text(encoding="utf-8")) \
        if (out_dir / "matrix.json").exists() else {}
    nearest = pq.read_table(out_dir / "nearest.parquet").to_pandas()
    instruments = pq.read_table(work_dir / "instruments.parquet").to_pandas()

    n = len(instruments)
    if matrix.shape != (n, n):
        raise SystemExit(f"matrix.npy is {matrix.shape}, instruments.parquet has {n} rows")
    if instruments["i"].astype(int).tolist() != list(range(n)):
        raise SystemExit("instruments.parquet is not in `i` order")

    collections = tuple(name for name in html.COLLECTION_CODES
                        if name in set(instruments["coleccion"]))
    units = instrument_matrix.unit_rows(work_dir, collections=collections,
                                        cache_dir=cache_dir, log=log)
    points, _ = html.load_frames(work_dir, [], neighbors=0, collections=collections,
                                 cache_dir=cache_dir, extra_columns=LABEL_COLUMNS, log=log)
    if len(units) != len(nearest) or len(points) != len(units):
        raise SystemExit(f"nearest.parquet has {len(nearest)} rows, the join gives "
                         f"{len(units)}: the legalvec cache is not the one "
                         "instrument_matrix.py swept")
    for column in ("i", "eId", "row", "unit_type"):
        if not (units[column].to_numpy() == nearest[column].to_numpy()).all():
            raise SystemExit(f"nearest.parquet's `{column}` does not line up with the "
                             "join: the legalvec cache is not the one "
                             "instrument_matrix.py swept")
    if not ((points["i"].to_numpy() == units["i"].to_numpy()).all()
            and (points["v"].to_numpy() == units["row"].to_numpy()).all()):
        raise SystemExit("load_frames and unit_rows disagree about the unit rows")

    return {"matrix": matrix, "summary": summary, "nearest": nearest,
            "instruments": instruments, "points": points}


def _normalised(vectors, rows):
    import numpy as np

    block = np.asarray(vectors[rows], dtype=np.float32)
    norms = np.linalg.norm(block, axis=1, keepdims=True)
    return block / np.where(norms > 0, norms, 1.0)


def _instrument_entry(instruments, i: int) -> dict:
    row = instruments.iloc[i]
    return {"i": int(i), "k": str(row["clave"]), "c": str(row["coleccion"]),
            "n": str(row["nombre"])}


def build_pairs(data: dict, vectors, pairs, *, tolerance: float, log=print):
    """Yield `(name, document)` for every pair: the rows, their winning target
    units and the texts both sides carry, as issue #249 lays the file out."""
    import numpy as np

    matrix, nearest = data["matrix"], data["nearest"]
    instruments, points = data["instruments"], data["points"]

    source_i = nearest["i"].to_numpy()
    vector_row = nearest["row"].to_numpy()
    similarity = nearest["similarity"].to_numpy()
    m = nearest["m"].to_numpy()
    unit_type = nearest["unit_type"].astype(str).to_numpy()
    labels = [unit_label(t, num, path, piece, text) for t, num, path, piece, text in
              zip(unit_type, points["num"], points["path"], points["piece"], points["text"])]
    paths = [breadcrumb(path) for path in points["path"]]
    texts = points["text"].to_numpy()

    wanted = {(i, j) for i, j, _ in pairs}
    by_pair: dict[tuple[int, int], list[int]] = {}
    for position, (i, targets) in enumerate(zip(source_i, nearest["targets"])):
        for j in targets:
            key = (int(i), int(j))
            if key in wanted:
                by_pair.setdefault(key, []).append(position)

    # Per target instrument: its distinct vector rows, and which of its unit
    # rows carry each one (a text repeated inside `j` is one row, several units).
    carriers: dict[int, dict[int, list[int]]] = {}
    for position, (i, row) in enumerate(zip(source_i, vector_row)):
        carriers.setdefault(int(i), {}).setdefault(int(row), []).append(position)

    started = time.time()
    for count, (i, j, weight) in enumerate(pairs, 1):
        positions = by_pair.get((i, j), [])
        total = float(sum(1.0 / m[p] for p in positions))
        if abs(total - float(matrix[i, j])) > SUM_TOLERANCE:
            raise SystemExit(f"pair {i}-{j}: its rows add up to {total:.4f}, "
                             f"matrix.npy says {float(matrix[i, j]):.4f}")

        source_rows = sorted({int(vector_row[p]) for p in positions})
        target_rows = sorted(carriers[j])
        scores = _normalised(vectors, source_rows) @ _normalised(vectors, target_rows).T
        recorded = {}
        for p in positions:
            recorded.setdefault(int(vector_row[p]), float(similarity[p]))
        winners: dict[int, list[int]] = {}
        for offset, row in enumerate(source_rows):
            best = float(scores[offset].max())
            if abs(best - recorded[row]) > tolerance:
                raise SystemExit(f"pair {i}-{j}, vector row {row}: {j}'s best text is at "
                                 f"{best:.7f}, nearest.parquet recorded "
                                 f"{recorded[row]:.7f} (tolerance {tolerance})")
            threshold = min(best, recorded[row]) - tolerance
            won = np.flatnonzero(scores[offset] >= threshold)
            order = sorted(won.tolist(), key=lambda c: (-float(scores[offset, c]),
                                                        target_rows[c]))
            winners[row] = [target_rows[c] for c in order]

        rows, used = [], set()
        ordered = sorted(positions, key=lambda p: (-round(float(similarity[p]), 4),
                                                   int(m[p]), labels[p], p))
        for p in ordered:
            row = int(vector_row[p])
            used.add(row)
            targets = []
            for winner in winners[row]:
                used.add(winner)
                targets += [{"label": labels[q], "path": paths[q], "text": winner}
                            for q in carriers[j][winner]]
            rows.append({"label": labels[p], "path": paths[p], "unit_type": unit_type[p],
                         "text": row, "targets": targets,
                         "similarity": round(float(similarity[p]), 4), "m": int(m[p])})
        document = {
            "source": _instrument_entry(instruments, i),
            "target": _instrument_entry(instruments, j),
            "weight": weight,
            "provisions": len(rows),
            "rows": rows,
            "texts": {str(row): _text_of(texts, carriers, row, i, j) for row in sorted(used)},
        }
        if count % 500 == 0 or count == len(pairs):
            log(f"{count}/{len(pairs)} pairs, {time.time() - started:.0f}s elapsed")
        yield f"{i}-{j}.json", document, total


def _text_of(texts, carriers, row: int, i: int, j: int) -> str:
    """The text of one vector row, read off a unit of `i` or `j` carrying it
    (every unit of a vector row has the same `text_sha1`)."""
    positions = carriers[i].get(row) or carriers[j][row]
    value = texts[positions[0]]
    return str(value) if _present(value) else ""


def encode(document: dict) -> bytes:
    return (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n") \
        .encode("utf-8")


def tarball(out_dir: Path) -> bytes:
    """`pairs/` and `manifest.json` as one reproducible `.tar.gz`: members
    sorted, mtime/uid/gid fixed, gzip header without a timestamp or name."""
    members = [out_dir / "manifest.json", out_dir / PAIRS]
    members += sorted((out_dir / PAIRS).iterdir(), key=lambda p: p.name)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for path in members:
            info = archive.gettarinfo(str(path), arcname=str(path.relative_to(out_dir)))
            info.mtime = TAR_MTIME
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if path.is_dir() else 0o644
            if path.is_dir():
                archive.addfile(info)
            else:
                with open(path, "rb") as handle:
                    archive.addfile(info, handle)
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0) as handle:
        handle.write(buffer.getvalue())
    return compressed.getvalue()


def _cd_target(out_dir: Path) -> str:
    """`package_vectors._cd_destino`'s rule: anchored on `$REPO` inside the
    repository, the absolute path outside it."""
    root = Path(__file__).resolve().parents[2]
    resolved = Path(out_dir).resolve()
    try:
        return f"$REPO/{resolved.relative_to(root)}"
    except ValueError:
        return str(resolved)


def publish_instructions(out_dir: Path, repo: str, manifest: dict) -> str:
    """`PUBLICAR.md`: the exact `gh` lines for a human to run, generated the
    way `package_vectors.genera_publicar` generates a vector release's."""
    assets = f"{ASSET} manifest.json SHA256SUMS.txt"
    lines = [
        f"# Publish `{RELEASE}` — generated commands, run by hand",
        "",
        "Issue #115, Hallazgo C: no GitHub Action publishes data derived from the",
        "SCJN, and neither does this script. Read `manifest.json` first: it names the",
        "commit, the matrix and the tolerance these explanations were built from.",
        "",
        f"{manifest['pairs']:,} pairs, {manifest['provisions']:,} provisions, "
        f"{manifest['bytes'] / 1e6:.1f} MB of JSON before compression.",
        "",
        f"Release body: `.github/{RELEASE}.md` in the repository. The file *is* the body.",
        "",
        "The first publication:",
        "",
        "```bash",
        "REPO=$(git rev-parse --show-toplevel)",
        f'cd "{_cd_target(out_dir)}"',
        "sha256sum -c SHA256SUMS.txt",
        f'gh release create {RELEASE} --repo {repo} --title "LegalIA — Atlas pair explanations" '
        f"--notes-file $REPO/.github/{RELEASE}.md {assets}",
        "```",
        "",
        "Every later regeneration replaces the assets of the same tag, which is the",
        "one name the website's publish workflow downloads:",
        "",
        "```bash",
        "REPO=$(git rev-parse --show-toplevel)",
        f'cd "{_cd_target(out_dir)}"',
        "sha256sum -c SHA256SUMS.txt",
        f"gh release upload {RELEASE} --repo {repo} {assets} --clobber",
        f"gh release edit {RELEASE} --repo {repo} --notes-file $REPO/.github/{RELEASE}.md",
        "```",
        "",
        "Read all of this before running it. Nothing here publishes itself "
        "(issue #115, Hallazgo C).",
        "",
    ]
    return "\n".join(lines)


def export(work_dir: Path, *, out_dir: Path | None = None, cache_dir=None,
           top: int = TOP_TARGETS, tolerance: float = instrument_matrix.DEFAULT_TOLERANCE,
           atlas_path: Path | None = None, repo: str = DEFAULT_REPO, now=None,
           log=print) -> dict:
    """Write every pair file, the manifest, the tarball, its sums and
    `PUBLICAR.md`, then `.done`. Returns the manifest."""
    from datetime import datetime, timezone

    import numpy as np

    from _atomic import atomic_write_bytes, atomic_write_text
    from package_vectors import sha256

    work_dir = Path(work_dir)
    out_dir = Path(out_dir) if out_dir is not None else output_dir(work_dir)
    check_inputs(work_dir)
    started = time.time()

    done = out_dir / ".done"
    if done.exists():
        done.unlink()
    data = load(work_dir, cache_dir=cache_dir, log=log)
    pairs = pairs_of(data["matrix"], top)
    if atlas_path is not None:
        check_atlas(atlas_path, pairs, data["instruments"])
        log(f"{atlas_path}: the same {len(pairs)} pairs")
    vectors = np.load(work_dir / "vectors.npy", mmap_mode="r")
    log(f"{len(pairs)} pairs over {len(data['nearest'])} unit rows, "
        f"{vectors.shape[0]} vectors")

    # A fresh directory, renamed into place once complete: a stale pair file
    # from an earlier run can never survive, and a half-written `pairs/` can
    # never look finished.
    staging = out_dir / (PAIRS + ".parcial")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    provisions, weight, total_bytes = 0, 0.0, 0
    largest = {"file": None, "rows": 0, "bytes": 0}
    for name, document, pair_weight in build_pairs(data, vectors, pairs,
                                                   tolerance=tolerance, log=log):
        payload = encode(document)
        atomic_write_bytes(staging / name, payload)
        provisions += document["provisions"]
        weight += pair_weight
        total_bytes += len(payload)
        if len(payload) > largest["bytes"]:
            largest = {"file": f"{PAIRS}/{name}", "rows": document["provisions"],
                       "bytes": len(payload)}
    final = out_dir / PAIRS
    if final.exists():
        shutil.rmtree(final)
    staging.rename(final)

    manifest = {
        "generated": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "commit": html.repository_commit(),
        "work_dir": str(work_dir),
        "matrix": data["summary"],
        "top": top,
        "tolerance": tolerance,
        "pairs": len(pairs),
        "provisions": provisions,
        "weight": round(weight, 1),
        "bytes": total_bytes,
        "largest": largest,
        "release": RELEASE,
        "asset": ASSET,
    }
    atomic_write_text(out_dir / "manifest.json",
                      json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    atomic_write_bytes(out_dir / ASSET, tarball(out_dir))
    sums = sorted(f"{sha256(out_dir / name)}  {name}" for name in (ASSET, "manifest.json"))
    atomic_write_text(out_dir / "SHA256SUMS.txt", "\n".join(sums) + "\n")
    atomic_write_text(out_dir / "PUBLICAR.md", publish_instructions(out_dir, repo, manifest))
    atomic_write_text(done, "")

    log(json.dumps({key: manifest[key] for key in ("pairs", "provisions", "weight",
                                                  "bytes", "largest")},
                   ensure_ascii=False))
    log(f"{out_dir / ASSET}: {(out_dir / ASSET).stat().st_size / 1e6:.1f} MB, "
        f"{time.time() - started:.0f}s. Nothing was published: read "
        f"{out_dir / 'PUBLICAR.md'} (issue #115, Hallazgo C).")
    return manifest


def install(out_dir: Path, target: Path, log=print) -> int:
    """Replace `target` with a copy of the finished `pairs/`, deleting what
    was there first so a pair from an earlier run cannot survive."""
    source = Path(out_dir) / PAIRS
    if not (Path(out_dir) / ".done").exists():
        raise SystemExit(f"{Path(out_dir) / '.done'} is missing: nothing finished to install")
    target = Path(target)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    count = sum(1 for _ in target.iterdir())
    log(f"{target}: {count} pair files installed")
    return count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("emb-run-umap"))
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="the legalvec cache (default: legalvec's own)")
    parser.add_argument("--top", type=int, default=TOP_TARGETS,
                        help="closest instruments per instrument, as in atlas.json")
    parser.add_argument("--tolerance", type=float,
                        default=instrument_matrix.DEFAULT_TOLERANCE,
                        help="similarity slack that still counts as a tie")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="default: <work-dir>/atlas-pairs")
    parser.add_argument("--atlas", type=Path, default=None,
                        help="an atlas.json to check the pairs against, e.g. "
                             "website/pages/atlas/atlas.json")
    parser.add_argument("--install", type=Path, default=None, metavar="DIR",
                        help="replace DIR with a copy of pairs/, e.g. "
                             "website/pages/atlas/pairs")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--force", action="store_true",
                        help="export again even if .done is already there")
    args = parser.parse_args(argv)

    out_dir = args.out_dir or output_dir(args.work_dir)
    done = out_dir / ".done"
    if done.exists() and not args.force:
        print(f"{done} is there -- nothing to export (pass --force to redo it)")
    else:
        export(args.work_dir, out_dir=out_dir, cache_dir=args.cache_dir, top=args.top,
               tolerance=args.tolerance, atlas_path=args.atlas, repo=args.repo)
    if args.install is not None:
        install(out_dir, args.install)
    return 0


if __name__ == "__main__":
    sys.exit(main())
