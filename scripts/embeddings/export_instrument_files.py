"""Export one JSON file per Atlas instrument: its `md2akn` tree, its excerpts,
parse-quality flags and the nearest texts of every searched excerpt.

Issue #289 (the data side of the website's Instruments page, issue #290):

    uv run --group viz python scripts/embeddings/export_instrument_files.py \\
        --work-dir emb-run-atlas-4b \\
        --instruments-as website/pages/atlas/atlas-qwen3-4b.json \\
        --index website/pages/instruments/instruments.json \\
        --install website/pages/instruments/files

`md2akn` is what every vector, matrix and Atlas number rests on, and nobody can
check 2,676 parses by hand. This writes, for each instrument of the work
directory (`instruments.parquet`, positionally the Atlas' own `instruments`), a
file a reviewer's browser can read: the document, the tree `md2akn` read in it,
the excerpts `text_units` cut from it, flags saying where to look first and,
on every searched excerpt, the five closest texts of other instruments
(`nearest_texts.py`, issue #288). Nothing is judged here: a flag is a count.

* **Which text.** The *one snapshot the instrument's vectors were built from*:
  the `archivo` column of the vectors release's `units.parquet`
  (`legalvec.load_units`), read through `snapshot_reader(coleccion)(clave,
  archivo)` (default: the `scjn` cache). It is parsed again with `md2akn`
  (`parse_markdown`, `text_units(split_articles=False)`) and the result is
  **asserted equal** to the released rows — `(unit_type, eId, piece,
  text_sha1, start_char, end_char)`, in order. Issue #279 proved a re-parse
  reproduces them, so a difference is a finding, not noise: the run stops with a
  `SystemExit` naming the instrument and the first differing position, and there
  is no flag to continue.
* **`files/<coleccion>/<clave>.json`**, short keys, the text stored once:
  `meta` (`i` the Atlas position, `c`, `k`, `n`, `archivo`, `fecha_publicacion`,
  `codNota` (leyes only), `md2akn_version`, `model`, `model_slug`, `top`,
  `generated`, `commit`, `numbering` = `{mode: articulo|ordinal|numeral,
  roman_articles}`, `units`, `searched`, `flags`), `markdown` (the raw document
  as read, frontmatter included), `tree` (the root node: `t` akn_type, `e` eId,
  `n` num, `h` heading, `r` refers_to, `s`/`l` start/end, `x` `["chapeau"|"tail"]`,
  `a` annotations `{raw, action, scope, date, source}`, `c` children; null and
  empty keys omitted) and `units` (document order: `u` unit_type, `e` eId, `p`
  piece, `pe` piece_eId, `s`/`l`, `sha` text_sha1, `v` vector row, `x` true when
  the excerpt was **searched** (`instrument_matrix.searched_mask`: no heading, no
  transitorio), `f` flag names, and, only when `x`, `near`: up to `top` entries
  `[j, eId, piece, similarity, also]` — `j` the Atlas index of the first owner of
  the target text (lowest `j`, then its first unit), its excerpt there, the
  similarity to 4 places and `also` the other owners as `[[j, eId, piece], ...]`;
  a text owned by several instruments is **one** entry).
* **Offsets are UTF-16 code units.** `md2akn`'s `start_char`/`end_char` are
  Python code points; a JavaScript string is UTF-16, so every `s`/`l` is
  converted here (an astral character before an offset counts twice) and the
  page slices `markdown` directly.
* **Flags** (`FLAGS`, meanings in `FLAG_MEANINGS`): `loose`, `duplicate_number`,
  `numbering_gap`, `over_window` on excerpts; `no_articles`, `uncovered`,
  `no_transitorios` on the instrument. `duplicate_number` and `numbering_gap`
  look only at the articles outside a transitorios section (a decree's
  `PRIMERO` repeats by design). `meta.flags` holds the counts and `score`, the
  sum of the excerpt-level counts plus one for each instrument-level flag but
  `no_transitorios` — the page's default ranking key.
* **Neighbours** are `nearest-texts.parquet`'s rows resolved to owners: the
  instruments owning the target vector row among their *searched* excerpts,
  the source excluded, which is `targets` (asserted). The first entry's
  similarity is asserted equal to `nearest.parquet`'s.
* **The index** (`--index`): `meta` and one `{c, k, n, archivo, u, x, a, f, s}`
  per instrument in the Atlas' order; `--instruments-as` refuses an Atlas file
  whose `c`/`k`/`n` differ at any position.
* **Release `atlas-instruments`**: `atlas-instruments-<slug>.tar.gz` (`files/` +
  `manifest.json`, reproducible), `manifest-<slug>.json`, `SHA256SUMS-<slug>.txt`;
  every name carries the model's slug. GitHub release assets carry no CORS
  header and are too big for git, so a human publishes the tarball by running
  `PUBLICAR.md` and the website's workflow unpacks it into the site (issue #115,
  Hallazgo C). `--install DIR` copies the files (only) into DIR.

`.done` makes a rerun a no-op; `--force` recomputes.
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import io
import json
import re
import shutil
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "md2akn"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packages" / "scjn"))

import legalvec  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import md2akn  # noqa: E402
from md2akn import coverage, parse_markdown, text_units  # noqa: E402

import build_umap_html as html  # noqa: E402
import instrument_matrix  # noqa: E402
import nearest_texts  # noqa: E402
from export_atlas_data import duplicates_dropped, model_name  # noqa: E402
from export_atlas_pairs import TAR_MTIME, _cd_target, encode  # noqa: E402

RELEASE = "atlas-instruments"
DEFAULT_REPO = "INGEOTEC/LegalIA"
SUBDIR = "instrument-files"
FILES = "files"
INDEX = "instruments.json"

#: The excerpt-level flags first, then the instrument-level ones.
FLAGS = ("loose", "duplicate_number", "numbering_gap", "over_window",
         "no_articles", "uncovered", "no_transitorios")
EXCERPT_FLAGS = FLAGS[:4]
INSTRUMENT_FLAGS = FLAGS[4:]
#: Instrument-level flags that add one to `score` (`no_transitorios` is only
#: information: a law without transitory articles is ordinary).
SCORED_INSTRUMENT_FLAGS = ("no_articles", "uncovered")

FLAG_MEANINGS = {
    "loose": "Text that no rule placed inside an article (an excerpt of type loose).",
    "duplicate_number": "Two articles that ended up with the same identifier "
                        "(outside the transitory provisions): the same number in the "
                        "same place of the outline, so the second could only be kept "
                        "apart with a suffix. The same number in two different "
                        "containers (article 1 of Part 1 and of Part 2) is not one; "
                        "both articles of a collision are flagged.",
    "numbering_gap": "An article whose integer number is not the previous integer "
                     "number plus one within its own container (outside the "
                     "transitory provisions; numbers such as 4 Bis or roman numerals "
                     "are skipped, and a container that starts over at 1 is not a gap).",
    "over_window": "An excerpt longer than the embedding model's window: its vector "
                   "was computed on a truncated text.",
    "no_articles": "The instrument has no article excerpt at all.",
    "uncovered": "Characters of the document that fall in no excerpt, no annotation "
                 "and no frontmatter (md2akn's coverage invariant).",
    "no_transitorios": "The instrument has no transitory section (informational).",
}

#: What the export needs on disk, relative to the work directory.
REQUIRED = (
    "prepare.done",
    f"{instrument_matrix.SUBDIR}/.done",
    f"{instrument_matrix.SUBDIR}/nearest.parquet",
    f"{nearest_texts.SUBDIR}/.done",
    f"{nearest_texts.SUBDIR}/{nearest_texts.TABLE}",
    "vector_ids.parquet",
    "instruments.parquet",
)

#: Columns forwarded from `units.parquet` through `build_umap_html.load_frames`:
#: the one join, never re-joined here.
UNIT_COLUMNS = ("text_sha1", "piece", "piece_eId", "start_char", "end_char", "num", "path",
                "archivo", "fecha_publicacion", "codNota", "envelope")

_INTEGER_NUM = re.compile(r"^\s*(\d+)\s*[º°oO]?\.?\s*$")
_ROMAN_NUM = re.compile(r"^\s*[IVXLCDM]+(?:\s+(?:BIS|TER|QUATER))?\.?\s*$")


def output_dir(work_dir: Path) -> Path:
    return Path(work_dir) / SUBDIR


def asset_names(model: str) -> dict:
    """The three assets of one model, every one named with its slug: there is
    no default model here to leave unsuffixed (a second model adds its own)."""
    slug = legalvec.model_slug(model)
    return {"model_slug": slug, "asset": f"{RELEASE}-{slug}.tar.gz",
            "manifest": f"manifest-{slug}.json", "sums": f"SHA256SUMS-{slug}.txt"}


# -- offsets ------------------------------------------------------------------ #

class Utf16:
    """Code-point offsets of `text` converted to UTF-16 code units: every
    astral character before an offset counts twice."""

    def __init__(self, text: str):
        self.astral = [i for i, ch in enumerate(text) if ord(ch) > 0xFFFF]

    def __call__(self, offset: int) -> int:
        return offset + bisect.bisect_left(self.astral, offset)


# -- the tree ----------------------------------------------------------------- #

def annotation_json(note) -> dict:
    entry = {"raw": note.raw, "action": note.action, "scope": note.scope,
             "date": note.date.isoformat() if note.date is not None else None,
             "source": note.source}
    return {key: value for key, value in entry.items() if value is not None}


def node_json(node, utf16: Utf16) -> dict:
    """One node as `{t, e, n, h, r, s, l, x, a, c}`, empty keys omitted. Built
    iteratively: a long article is only a few levels deep, but nothing here
    should hit the interpreter's recursion limit on a pathological document."""
    def shallow(current) -> dict:
        entry = {"t": current.akn_type, "e": current.eId, "n": current.num,
                 "h": current.heading, "r": current.refers_to,
                 "s": utf16(current.start_char), "l": utf16(current.end_char)}
        marks = [name for name, on in (("chapeau", current.is_chapeau),
                                       ("tail", current.is_tail)) if on]
        if marks:
            entry["x"] = marks
        if current.notes:
            entry["a"] = [annotation_json(note) for note in current.notes]
        return {key: value for key, value in entry.items()
                if value is not None and value != ""}

    root = shallow(node)
    pending = [(node, root)]
    while pending:
        source, target = pending.pop()
        if source.children:
            target["c"] = []
            for child in source.children:
                built = shallow(child)
                target["c"].append(built)
                pending.append((child, built))
    return root


# -- flags -------------------------------------------------------------------- #

def integer_number(num):
    """`27` for `27`, `1º`, `1o.`; None for `4 Bis`, `I`, `Primero`, `2,3`."""
    match = _INTEGER_NUM.match(str(num)) if num is not None else None
    return int(match.group(1)) if match else None


def is_roman(num) -> bool:
    return bool(num is not None and _ROMAN_NUM.match(str(num)))


def excerpt_flags(units, truncated: set, repeated: frozenset | set = frozenset()) -> list[list[str]]:
    """Per excerpt (a `TextUnit`), its excerpt-level flag names, in `FLAGS` order.

    `repeated` is the set of eIds of the nodes `md2akn` marks `repeated` (two
    provisions that ended up with one identifier, review fix-2 of #291): an
    `article` excerpt outside a transitorios section whose eId is in it is
    `duplicate_number`. `numbering_gap` reads the same articles in document
    order, one container at a time (its `path`): an article whose integer
    number is not the previous integer number *of its own container* plus one
    is a gap, so a Part that starts over at 1 is not one (the first
    integer-numbered article of a container never is)."""
    transitory = [instrument_matrix.is_transitorio_path(unit.path) for unit in units]
    numbered = [(position, unit) for position, unit in enumerate(units)
                if unit.unit_type == "article" and not transitory[position]
                and unit.num is not None]
    duplicated = {position for position, unit in numbered if unit.eId in repeated}
    gaps, previous = set(), {}
    for position, unit in numbered:
        value = integer_number(unit.num)
        if value is None:
            continue
        before = previous.get(unit.path)
        if before is not None and value != before + 1:
            gaps.add(position)
        previous[unit.path] = value
    flags = []
    for position, unit in enumerate(units):
        names = []
        if unit.unit_type == "loose":
            names.append("loose")
        if position in duplicated:
            names.append("duplicate_number")
        if position in gaps:
            names.append("numbering_gap")
        if unit.text_sha1 in truncated:
            names.append("over_window")
        flags.append(names)
    return flags


def instrument_flags(tree, units, uncovered_chars: int) -> dict:
    """The instrument-level flags that apply, with their counts (`uncovered` is
    the number of uncovered characters, the others 1)."""
    found = {}
    if not any(unit.unit_type == "article" for unit in units):
        found["no_articles"] = 1
    if uncovered_chars > 0:
        found["uncovered"] = int(uncovered_chars)
    if not any(node.refers_to == md2akn.REFERS_TO_TRANSITORIOS for node in tree.walk()):
        found["no_transitorios"] = 1
    return found


def flag_counts(per_excerpt: list[list[str]], instrument_level: dict) -> dict:
    """`meta.flags`: counts per flag that applies (zero ones omitted) and the
    ranking `score`."""
    counts = {}
    for names in per_excerpt:
        for name in names:
            counts[name] = counts.get(name, 0) + 1
    counts.update(instrument_level)
    score = sum(counts.get(name, 0) for name in EXCERPT_FLAGS) \
        + sum(1 for name in SCORED_INSTRUMENT_FLAGS if counts.get(name, 0) > 0)
    return {**{name: counts[name] for name in FLAGS if name in counts}, "score": score}


# -- reading ------------------------------------------------------------------- #

def default_snapshot_reader(coleccion: str, scjn_cache_dir=None):
    """`reader(clave, archivo) -> str`: the Markdown of one snapshot out of the
    `scjn` cache (disk only: an `AssetNotCached` is raised, not downloaded)."""
    import scjn

    if coleccion == "leyes":
        def read(clave, archivo):
            return scjn.markdown_de_snapshot(clave, archivo, cache_dir=scjn_cache_dir)
        return read
    corpus = {"reglamentos": scjn.download_scjn_reglamentos_corpus,
              "lineamientos": scjn.download_scjn_lineamientos_corpus,
              "tratados": scjn.download_scjn_tratados_corpus}[coleccion]

    def read(clave, archivo):
        for snapshot in corpus(str(clave), cache_dir=scjn_cache_dir)["snapshots"]:
            if snapshot["archivo"] == archivo:
                return snapshot["markdown"]
        raise SystemExit(f"{coleccion}/{clave}: the cached tarball has no snapshot {archivo!r}")
    return read


def truncated_texts(coleccion: str, model: str, cache_dir=None) -> set:
    """The `text_sha1` of every text the model truncated, from the release's
    `vectors-manifest.json` (`models.<slug>.texts_truncated`)."""
    from legalvec import cache

    path = cache.release_dir(coleccion, cache_dir) / "vectors-manifest.json"
    if not path.exists():
        raise SystemExit(f"{path} is missing: download the {coleccion} vectors release")
    models = json.loads(path.read_text(encoding="utf-8"))["models"]
    entry = models.get(legalvec.model_slug(model))
    if entry is None:
        raise SystemExit(f"{path} has no model {legalvec.model_slug(model)!r}")
    return {item["text_sha1"] for item in entry.get("texts_truncated") or []}


def check_inputs(work_dir: Path) -> None:
    for name in REQUIRED:
        if not (Path(work_dir) / name).exists():
            raise SystemExit(f"{Path(work_dir) / name} is missing -- this export reads what "
                             "instrument_matrix.py (issue #242), nearest_texts.py "
                             "(issue #288) and prepare_umap_input.py (issue #241) wrote")
    duplicates_dropped(work_dir)


def load(work_dir: Path, *, cache_dir=None, top: int, log=print) -> dict:
    """Everything the export reads, aligned. `units` is the shared join over all
    the instruments (`build_umap_html.load_frames`), `searched` its narrowing
    through `instrument_matrix.searched_mask`, which is asserted to line up with
    `nearest.parquet` positionally, as `export_atlas_pairs.load` does."""
    import numpy as np
    import pandas as pd
    import pyarrow.parquet as pq

    work_dir = Path(work_dir)
    instruments = pq.read_table(work_dir / "instruments.parquet").to_pandas()
    n = len(instruments)
    if instruments["i"].astype(int).tolist() != list(range(n)):
        raise SystemExit("instruments.parquet is not in `i` order")
    present = set(instruments["coleccion"])
    unknown = sorted(present - set(html.COLLECTION_CODES))
    if unknown:
        raise SystemExit(f"instruments.parquet holds collection(s) {unknown} that "
                         "build_umap_html.COLLECTION_CODES does not know")
    collections = tuple(name for name in html.COLLECTION_CODES if name in present)

    points, _ = html.load_frames(work_dir, [], neighbors=0, collections=collections,
                                 cache_dir=cache_dir, extra_columns=UNIT_COLUMNS, log=log)
    names = dict(enumerate(html.UNIT_TYPES))
    codes = {code: name for name, code in html.COLLECTION_CODES.items()}
    units = pd.DataFrame({
        "i": points["i"].astype("int64"),
        "coleccion": points["c"].map(codes),
        "unit_type": points["u"].map(names),
        "eId": points["e"],
        "row": points["v"].astype("int64"),
        "transitorio": [instrument_matrix.is_transitorio_path(path) for path in points["path"]],
        "envelope": instrument_matrix.envelope_flags(points)[0],
    })
    searched = instrument_matrix.searched_mask(units)

    nearest = pq.read_table(work_dir / instrument_matrix.SUBDIR / "nearest.parquet").to_pandas()
    narrowed = units[searched].reset_index(drop=True)
    if len(narrowed) != len(nearest):
        raise SystemExit(f"nearest.parquet has {len(nearest)} rows, the join gives "
                         f"{len(narrowed)}: the legalvec cache is not the one "
                         "instrument_matrix.py swept")
    for column in ("i", "eId", "row", "unit_type"):
        if not (narrowed[column].to_numpy() == nearest[column].to_numpy()).all():
            raise SystemExit(f"nearest.parquet's `{column}` does not line up with the "
                             "join: the legalvec cache is not the one "
                             "instrument_matrix.py swept")
    counts = units.groupby("i").size().reindex(range(n), fill_value=0)
    if counts.to_numpy().tolist() != instruments["units"].astype(int).tolist():
        raise SystemExit("the join's unit rows per instrument differ from instruments.parquet's "
                         "`units`: the legalvec cache is not the one the work directory was "
                         "prepared from")

    near_table = pq.read_table(work_dir / nearest_texts.SUBDIR / nearest_texts.TABLE).to_pandas()
    return {"instruments": instruments, "points": points, "units": units,
            "searched": searched, "nearest": nearest, "near_table": near_table,
            "n": n, "top": top}


def owner_map(units, searched) -> dict:
    """`vector row -> {instrument: position of its first searched unit}`, the
    owners `targets` is made of."""
    owners: dict[int, dict[int, int]] = {}
    rows = units["row"].to_numpy()
    instruments = units["i"].to_numpy()
    for position in range(len(units)):
        if searched[position]:
            owners.setdefault(int(rows[position]), {}).setdefault(
                int(instruments[position]), position)
    return owners


def near_lookup(data: dict) -> dict:
    """`(i, source vector row) -> [(target row, similarity, targets), ...]`
    ranked, from `nearest-texts.parquet`, plus the agreement of its first entry
    with `nearest.parquet` (every searched unit, asserted)."""
    table = data["near_table"].sort_values(["i", "row", "rank"], kind="stable")
    lookup: dict[tuple[int, int], list] = {}
    for i, row, target, similarity, targets in zip(
            table["i"].to_numpy(), table["row"].to_numpy(), table["target_row"].to_numpy(),
            table["similarity"].to_numpy(), table["targets"].to_numpy()):
        lookup.setdefault((int(i), int(row)), []).append(
            (int(target), float(similarity), [int(t) for t in targets]))
    nearest = data["nearest"]
    for position, (i, row, similarity, winners) in enumerate(zip(
            nearest["i"].to_numpy(), nearest["row"].to_numpy(),
            nearest["similarity"].to_numpy(), nearest["n_winners"].to_numpy())):
        entries = lookup.get((int(i), int(row)))
        if winners == 0:
            continue
        if not entries or abs(entries[0][1] - float(similarity)) > instrument_matrix.DEFAULT_TOLERANCE:
            raise SystemExit(f"instrument {int(i)}, vector row {int(row)}: the first nearest text "
                             f"{entries[0][1] if entries else None!r} is not nearest.parquet's "
                             f"{float(similarity)!r}")
    return lookup


def near_entries(source: int, entries, owners: dict, top: int, rounding: int = 4) -> list:
    """The `near` list of one searched excerpt: one entry per target text (at
    most `top`), each `[j, eId, piece, similarity, also]`, from the rows
    `nearest_texts.py` wrote. The owners of a text, `source` excluded, are
    ordered by Atlas index; the first is `j`."""
    result = []
    for target, similarity, targets in entries[:top]:
        found = sorted((j, position) for j, position in owners[target].items() if j != source)
        if sorted(j for j, _ in found) != targets:
            raise SystemExit(f"instrument {source}, text {target}: its owners {found} are not "
                             f"nearest-texts.parquet's targets {targets}")
        result.append((found, round(similarity, rounding)))
    return result


# -- one instrument ------------------------------------------------------------- #

def compare_with_released(label: str, parsed, released) -> None:
    """The re-parse must reproduce the released excerpts, in order."""
    mine = [(u.unit_type, u.eId, int(u.piece), u.text_sha1, int(u.start_char), int(u.end_char))
            for u in parsed]
    if len(mine) != len(released):
        position = min(len(mine), len(released))
        raise SystemExit(f"{label}: md2akn {md2akn.__version__} gives {len(mine)} excerpts, the "
                         f"released units.parquet has {len(released)} (they differ from "
                         f"position {position} on)")
    for position, (a, b) in enumerate(zip(mine, released)):
        if a != b:
            raise SystemExit(f"{label}: excerpt {position} differs: re-parse {a}, released {b}")


def instrument_document(i: int, data: dict, markdown: str, owners: dict, lookup: dict,
                        truncated: set, *, positions, meta: dict, log=print):
    """Build one instrument's document. Returns `(document, entry)`, `entry`
    being its line of the index."""
    units_frame, points = data["units"], data["points"]
    label = f"{meta['c']}/{meta['k']}"
    released = [(units_frame["unit_type"].iat[p], str(points["e"].iat[p]),
                 0 if _missing(points["piece"].iat[p]) else int(points["piece"].iat[p]),
                 str(points["text_sha1"].iat[p]),
                 int(points["start_char"].iat[p]), int(points["end_char"].iat[p]))
                for p in positions]

    tree = parse_markdown(markdown)
    parsed = text_units(tree, split_articles=False)
    compare_with_released(label, parsed, released)

    mode = tree.modo or "articulo"
    utf16 = Utf16(markdown)
    flags = excerpt_flags(parsed, truncated, {n.eId for n in tree.walk() if n.repeated})
    instrument_level = instrument_flags(tree, parsed, coverage(tree, parsed).uncovered_chars)
    counts = flag_counts(flags, instrument_level)

    searched = data["searched"]
    unit_entries = []
    for offset, (unit, names) in enumerate(zip(parsed, flags)):
        position = positions[offset]
        entry = {"u": unit.unit_type, "e": unit.eId}
        if unit.piece:
            entry["p"] = int(unit.piece)
        if unit.piece_eId:
            entry["pe"] = unit.piece_eId
        entry.update({"s": utf16(unit.start_char), "l": utf16(unit.end_char),
                      "sha": unit.text_sha1, "v": int(units_frame["row"].iat[position])})
        if searched[position]:
            entry["x"] = True
        if names:
            entry["f"] = names
        if searched[position]:
            ranked = near_entries(i, lookup.get((i, entry["v"]), []), owners, meta["top"])
            near = []
            for found, similarity in ranked:
                (j, first), rest = found[0], found[1:]
                target = [j, units_frame["eId"].iat[first], _piece(points["piece"].iat[first])]
                also = [[k, units_frame["eId"].iat[p], _piece(points["piece"].iat[p])]
                        for k, p in rest]
                near.append(target + [similarity, also])
            entry["near"] = near
        unit_entries.append(entry)

    articles = sum(1 for unit in parsed if unit.unit_type == "article")
    roman = sum(1 for node in tree.walk() if node.akn_type == "article" and is_roman(node.num))
    n_searched = sum(1 for p in positions if searched[p])
    document = {
        "meta": {**meta, "numbering": {"mode": mode, "roman_articles": roman},
                 "units": len(parsed), "searched": n_searched, "flags": counts},
        "markdown": markdown,
        "tree": node_json(tree, utf16),
        "units": unit_entries,
    }
    entry = {"c": meta["c"], "k": meta["k"], "n": meta["n"], "archivo": meta["archivo"],
             "u": len(parsed), "x": n_searched, "a": articles,
             "f": {name: counts[name] for name in FLAGS if name in counts}, "s": counts["score"]}
    return document, entry


def _missing(value) -> bool:
    return value is None or (isinstance(value, float) and value != value)


def _piece(value) -> int:
    return 0 if _missing(value) else int(value)


# -- the whole export ------------------------------------------------------------ #

def check_same_instruments(entries: list, reference_path: Path) -> None:
    """Refuse `entries` unless their `c`/`k`/`n` are the Atlas file's, position
    by position (`export_atlas_data.check_same_instruments`' rule: the page
    opens an instrument by its Atlas index, so `i` must mean the same thing)."""
    reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))["instruments"]
    for position, (mine, theirs) in enumerate(zip(entries, reference)):
        for key in ("c", "k", "n"):
            if mine[key] != theirs[key]:
                raise SystemExit(
                    f"instrument {position} differs from {reference_path}: `{key}` is "
                    f"{mine[key]!r} here, {theirs[key]!r} there -- the two exports do not "
                    "list the same instruments in the same positions")
    if len(entries) != len(reference):
        raise SystemExit(
            f"{len(entries)} instruments here, {len(reference)} in {reference_path}: they "
            f"differ from position {min(len(entries), len(reference))} on")


def tarball(out_dir: Path, manifest_name: str) -> bytes:
    """`files/` (recursively) and the manifest as one reproducible `.tar.gz`:
    members sorted, mtime/uid/gid fixed, gzip header without a timestamp. The
    manifest is a member named `manifest.json` whatever file it is on disk."""
    members = [(out_dir / manifest_name, "manifest.json"), (out_dir / FILES, FILES)]
    for path in sorted((out_dir / FILES).rglob("*"), key=lambda p: p.relative_to(out_dir).as_posix()):
        members.append((path, path.relative_to(out_dir).as_posix()))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for path, arcname in members:
            info = archive.gettarinfo(str(path), arcname=arcname)
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


def publish_instructions(out_dir: Path, repo: str, manifest: dict) -> str:
    """`PUBLICAR.md`: the exact `gh` lines for a human to run."""
    asset, manifest_name, sums = manifest["asset"], manifest["manifest"], manifest["sums"]
    assets = f"{asset} {manifest_name} {sums}"
    lines = [
        f"# Publish `{RELEASE}` — generated commands, run by hand",
        "",
        "Issue #115, Hallazgo C: no GitHub Action publishes data derived from the",
        f"SCJN, and neither does this script. Read `{manifest_name}` first: it names the",
        "commit, the md2akn version and the nearest-texts run these files were built from.",
        "",
        f"Built from `{manifest['model']}` (`{manifest['model_slug']}`): "
        f"{manifest['files']:,} instrument files, {manifest['units']:,} excerpts "
        f"({manifest['searched']:,} searched), {manifest['bytes'] / 1e6:.1f} MB of JSON "
        "before compression.",
        "",
        f"Release body: `.github/{RELEASE}.md` in the repository. The file *is* the body.",
        "",
        f"**Publish the `{RELEASE}` release before the pull request that adds the Instruments",
        "page is merged to `master`.** The website's publish workflow downloads",
        f"`{asset}` and fails when it is missing.",
        "",
        "If the release exists (add or replace these three assets, same tag):",
        "",
        "```bash",
        "REPO=$(git rev-parse --show-toplevel)",
        f'cd "{_cd_target(out_dir)}"',
        f"sha256sum -c {sums}",
        f"gh release upload {RELEASE} --repo {repo} {assets} --clobber",
        f"gh release edit {RELEASE} --repo {repo} --notes-file $REPO/.github/{RELEASE}.md",
        "```",
        "",
        "Only if the release does not exist yet:",
        "",
        "```bash",
        "REPO=$(git rev-parse --show-toplevel)",
        f'cd "{_cd_target(out_dir)}"',
        f"sha256sum -c {sums}",
        f'gh release create {RELEASE} --repo {repo} --title "LegalIA — Atlas instrument files" '
        f"--notes-file $REPO/.github/{RELEASE}.md {assets}",
        "```",
        "",
        "Read all of this before running it. Nothing here publishes itself "
        "(issue #115, Hallazgo C).",
        "",
    ]
    return "\n".join(lines)


def export(work_dir: Path, *, out_dir: Path | None = None, cache_dir=None, top: int = nearest_texts.TOP_TEXTS,
           index_path: Path | None = None, instruments_as: Path | None = None,
           snapshot_reader=None, scjn_cache_dir=None, repo: str = DEFAULT_REPO, now=None,
           log=print) -> dict:
    """Write every instrument file, the index, the manifest, the tarball, its
    sums and `PUBLICAR.md`, then `.done`. Returns the manifest."""
    from datetime import datetime, timezone

    from _atomic import atomic_write_bytes, atomic_write_text
    from package_vectors import sha256

    work_dir = Path(work_dir)
    out_dir = Path(out_dir) if out_dir is not None else output_dir(work_dir)
    check_inputs(work_dir)
    model = model_name(work_dir)
    names = asset_names(model)
    started = time.time()
    generated = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    commit = html.repository_commit()
    reader_for = snapshot_reader or (lambda coleccion: default_snapshot_reader(
        coleccion, scjn_cache_dir))

    done = out_dir / ".done"
    if done.exists():
        done.unlink()
    data = load(work_dir, cache_dir=cache_dir, top=top, log=log)
    near_summary = json.loads((work_dir / nearest_texts.SUBDIR / nearest_texts.SUMMARY)
                              .read_text(encoding="utf-8"))
    if near_summary["top"] < top:
        raise SystemExit(f"nearest-texts holds the top {near_summary['top']}, --top asks for {top}")
    owners = owner_map(data["units"], data["searched"])
    lookup = near_lookup(data)
    truncated = {coleccion: truncated_texts(coleccion, model, cache_dir)
                 for coleccion in sorted(set(data["instruments"]["coleccion"]))}
    readers = {}

    instruments = data["instruments"]
    positions_of = {int(i): group.tolist() for i, group in
                    data["units"].reset_index(drop=True).groupby("i").groups.items()}

    staging = out_dir / (FILES + ".parcial")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    entries, total_bytes, units_total, searched_total = [], 0, 0, 0
    largest = {"file": None, "bytes": 0, "units": 0}
    flag_totals = {name: {"instruments": 0, "count": 0} for name in FLAGS}
    collections: dict[str, int] = {}
    for i in range(data["n"]):
        row = instruments.iloc[i]
        coleccion, clave = str(row["coleccion"]), str(row["clave"])
        positions = positions_of[i]
        first = positions[0]
        archivos = {str(data["points"]["archivo"].iat[p]) for p in positions}
        if len(archivos) != 1:
            raise SystemExit(f"{coleccion}/{clave}: its released units come from {len(archivos)} "
                             f"snapshots {sorted(archivos)}, expected one")
        archivo = archivos.pop()
        codnota = data["points"]["codNota"].iat[first]
        meta = {"i": i, "c": coleccion, "k": clave, "n": str(row["nombre"]), "archivo": archivo,
                "fecha_publicacion": str(data["points"]["fecha_publicacion"].iat[first]),
                "codNota": None if _missing(codnota) else int(codnota),
                "md2akn_version": md2akn.__version__, "model": model,
                "model_slug": names["model_slug"], "top": top,
                "generated": generated, "commit": commit}
        reader = readers.setdefault(coleccion, reader_for(coleccion))
        markdown = reader(clave, archivo)
        document, entry = instrument_document(
            i, data, markdown, owners, lookup, truncated[coleccion],
            positions=positions, meta=meta, log=log)
        payload = encode(document)
        path = staging / coleccion / f"{clave}.json"
        atomic_write_bytes(path, payload)
        total_bytes += len(payload)
        units_total += entry["u"]
        searched_total += entry["x"]
        collections[coleccion] = collections.get(coleccion, 0) + 1
        for name, count in entry["f"].items():
            if name != "score":
                flag_totals[name]["instruments"] += 1
                flag_totals[name]["count"] += count
        if len(payload) > largest["bytes"]:
            largest = {"file": f"{FILES}/{coleccion}/{clave}.json", "bytes": len(payload),
                       "units": entry["u"]}
        entries.append(entry)
        if (i + 1) % 200 == 0 or i + 1 == data["n"]:
            log(f"{i + 1}/{data['n']} instruments, {time.time() - started:.0f}s elapsed")
    if instruments_as is not None:
        check_same_instruments(entries, instruments_as)
        log(f"{instruments_as}: the same {len(entries)} instruments in the same positions")

    final = out_dir / FILES
    if final.exists():
        shutil.rmtree(final)
    staging.rename(final)

    flag_totals = {name: counts for name, counts in flag_totals.items() if counts["instruments"]}
    index = {
        "meta": {"generated": generated, "commit": commit, "model": model,
                 "model_slug": names["model_slug"], "md2akn_version": md2akn.__version__,
                 "top": top, "instruments": len(entries), "collections": collections,
                 "units": units_total, "searched": searched_total, "flags": flag_totals,
                 "flag_meanings": FLAG_MEANINGS, "release": RELEASE, "files": f"{FILES}/"},
        "instruments": entries,
    }
    index_bytes = encode(index)
    atomic_write_bytes(out_dir / INDEX, index_bytes)
    if index_path is not None:
        atomic_write_bytes(Path(index_path), index_bytes)

    manifest = {
        "generated": generated, "commit": commit, "work_dir": str(work_dir),
        "model": model, "model_slug": names["model_slug"],
        "md2akn_version": md2akn.__version__, "top": top, "instruments": len(entries),
        "files": len(entries), "units": units_total, "searched": searched_total,
        "bytes": total_bytes, "largest": largest, "flags": flag_totals,
        "release": RELEASE, "asset": names["asset"], "manifest": names["manifest"],
        "sums": names["sums"], "nearest_texts": near_summary,
    }
    atomic_write_text(out_dir / names["manifest"],
                      json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    atomic_write_bytes(out_dir / names["asset"], tarball(out_dir, names["manifest"]))
    sums = sorted(f"{sha256(out_dir / name)}  {name}"
                  for name in (names["asset"], names["manifest"]))
    atomic_write_text(out_dir / names["sums"], "\n".join(sums) + "\n")
    atomic_write_text(out_dir / "PUBLICAR.md", publish_instructions(out_dir, repo, manifest))
    atomic_write_text(done, "")

    log(json.dumps({key: manifest[key] for key in ("files", "units", "searched", "bytes",
                                                  "largest", "flags")}, ensure_ascii=False))
    log(f"{out_dir / names['asset']}: {(out_dir / names['asset']).stat().st_size / 1e6:.1f} MB, "
        f"{time.time() - started:.0f}s. Nothing was published: read "
        f"{out_dir / 'PUBLICAR.md'} (issue #115, Hallazgo C).")
    return manifest


def install(out_dir: Path, target: Path, log=print) -> int:
    """Replace `target` with a copy of the finished `files/` (the files only,
    no manifest), deleting what was there first."""
    source = Path(out_dir) / FILES
    if not (Path(out_dir) / ".done").exists():
        raise SystemExit(f"{Path(out_dir) / '.done'} is missing: nothing finished to install")
    target = Path(target)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    count = sum(1 for path in target.rglob("*.json"))
    log(f"{target}: {count} instrument files installed")
    return count


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("emb-run-umap"))
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="the legalvec cache (default: legalvec's own)")
    parser.add_argument("--scjn-cache-dir", type=Path, default=None,
                        help="the scjn cache (default: scjn's own)")
    parser.add_argument("--top", type=int, default=nearest_texts.TOP_TEXTS,
                        help="nearest texts kept per searched excerpt")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="default: <work-dir>/instrument-files")
    parser.add_argument("--index", type=Path, default=None,
                        help="where to write the index, e.g. "
                             "website/pages/instruments/instruments.json")
    parser.add_argument("--instruments-as", type=Path, default=None,
                        help="an Atlas file whose c/k/n the index must agree with, e.g. "
                             "website/pages/atlas/atlas-qwen3-4b.json")
    parser.add_argument("--install", type=Path, default=None, metavar="DIR",
                        help="replace DIR with a copy of files/, e.g. "
                             "website/pages/instruments/files")
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
               index_path=args.index, instruments_as=args.instruments_as,
               scjn_cache_dir=args.scjn_cache_dir, repo=args.repo)
    if args.install is not None:
        install(out_dir, args.install)
    return 0


if __name__ == "__main__":
    sys.exit(main())
