"""Prepare the BM25 work directory the Atlas' lexical baseline reads.

Issue #267. The Atlas compares the 1,303 unique federal instruments through a
text-to-text score (`scoring.py`). Two work directories hold the dense one
(`emb-run-atlas/`, `emb-run-atlas-4b/`); this prepares the third, whose score is
**BM25** — a classical lexical baseline whose vocabulary and inverse document
frequencies come from the corpus being compared itself, nothing external:

    uv run --group viz python scripts/embeddings/prepare_bm25_input.py \\
        --work-dir emb-run-atlas-bm25 --from emb-run-atlas
    uv run --group viz python scripts/embeddings/prepare_bm25_input.py \\
        --work-dir emb-run-atlas-bm25 --from emb-run-atlas --force

It reads no vector. `--from` is an existing dense work directory prepared with
`prepare_umap_input.py --unique-names`; its `vector_ids.parquet`,
`instruments.parquet` and `unique-instruments.json` are **copied byte for byte**,
so a vector `row` and an instrument `i` mean the same text and the same
instrument in every work directory (which is what makes `export_atlas_data.py
--instruments-as` hold by construction). The texts come from the `legalvec`
cache's `units.parquet`, through `build_umap_html.load_frames` — the one join
every script uses.

* **The index is the candidate set.** One document per vector row carried by at
  least one *searched* unit (`instrument_matrix.searched_mask`: headings and
  transitorios are out), so the vocabulary and the IDF are those of the texts
  actually compared, and a heading or a transitorio shapes nothing.
* **Tokens** are lowercase words of two or more characters and nothing else:
  `bm25s.tokenize(lower=True, stopwords=None, stemmer=None)` with its default
  Unicode-aware pattern `(?u)\\b\\w\\w+\\b`. Accents are kept; the Markdown `**`
  markers fall out of the pattern, so the `text` column is tokenised as is. The
  IDF makes `de`/`la`/`el` nearly weightless anyway.
* **Index parameters** are bm25s' defaults — `method="lucene"`, `k1=1.5`,
  `b=0.75` — recorded in `input.json` so a later sweep changes them knowingly.

Outputs, under `--work-dir` (`emb-run-atlas-bm25/`, gitignored):

* `vector_ids.parquet`, `instruments.parquet`, `unique-instruments.json` —
  copies of `--from`'s.
* `bm25-index/` — bm25s' saved index (`retriever.save`), documents in
  ascending vector-row order.
* `tokens.parquet` — `row` (the vector row of each document, in index order)
  and `token_ids` (its token ids), so the query side is rebuilt without
  tokenising again.
* `input.json` — `method: "bm25"`, `model: "bm25"`, `model_slug: "bm25"`,
  `k: null`, the library version and parameters, the tokeniser, vocabulary size,
  documents indexed, documents with no term, plus the `unique_names`, `n`,
  `instruments`, `collections` and `legalvec_version` of the source.
* `prepare.done` — written last; re-running without `--force` is a no-op.

**No `vectors.npy` and no `centroid_input.npy`**: nothing BM25 produces is a
vector per text.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scoring  # noqa: E402

#: The three tables copied verbatim from the dense work directory.
COPIED = ("vector_ids.parquet", "instruments.parquet", "unique-instruments.json")

DONE_MARKER = "prepare.done"

#: bm25s' own defaults (Lucene), spelled out so `input.json` records them.
K1 = 1.5
B = 0.75
METHOD = "lucene"

TOKENISER = {
    "lower": True,
    "stopwords": None,
    "stemmer": None,
    "token_pattern": r"(?u)\b\w\w+\b",
}


def check_source(source: Path) -> dict:
    """The source's `input.json`, after refusing a directory that is not a
    finished, unique-names dense preparation."""
    if not (source / DONE_MARKER).exists():
        raise SystemExit(f"{source / DONE_MARKER} is missing: --from must be a finished "
                         "prepare_umap_input.py work directory")
    record = json.loads((source / "input.json").read_text(encoding="utf-8"))
    if not record.get("unique_names"):
        raise SystemExit(f"{source} was not prepared with `prepare_umap_input.py "
                         "--unique-names`: the Atlas draws unique instruments only")
    for name in COPIED:
        if not (source / name).exists():
            raise SystemExit(f"{source / name} is missing")
    return record


def candidate_texts(work_dir: Path, *, collections, cache_dir=None, log=print):
    """`(rows, texts)`: the vector row of every searched document and its text,
    rows ascending.

    The text is read off the first searched unit carrying the row (every unit of
    a vector row has the same `text_sha1`). Headings and transitorios are left
    out through `instrument_matrix.searched_mask`, so a text only they carry has
    no document at all.
    """
    import numpy as np
    import pandas as pd

    import build_umap_html as html
    import instrument_matrix

    points, _ = html.load_frames(work_dir, [], neighbors=0, collections=collections,
                                 cache_dir=cache_dir, extra_columns=("path", "text"), log=log)
    names = dict(enumerate(html.UNIT_TYPES))
    units = pd.DataFrame({
        "unit_type": points["u"].map(names),
        "transitorio": [instrument_matrix.is_transitorio_path(path) for path in points["path"]],
    })
    searched = instrument_matrix.searched_mask(units)
    rows = points["v"].to_numpy()[searched]
    texts = points["text"].to_numpy()[searched]
    first = pd.Series(texts).groupby(rows).first()
    ordered_rows = first.index.to_numpy(dtype=np.int64)
    log(f"{int(searched.sum())} searched unit rows carry {len(ordered_rows)} distinct texts "
        f"({len(points) - int(searched.sum())} headings and transitorios left out)")
    return ordered_rows, [("" if text is None or text != text else str(text))
                          for text in first.to_numpy()]


def prepare(work_dir: Path, source: Path, *, cache_dir=None, force: bool = False,
            log=print) -> dict:
    """Copy the row tables, tokenise and index the candidate texts, save the
    index and the token ids, write `input.json`, then `prepare.done`."""
    import bm25s
    import pyarrow as pa
    import pyarrow.parquet as pq

    import build_umap_html as html
    from _atomic import atomic_write_table, atomic_write_text

    work_dir, source = Path(work_dir), Path(source)
    marker = work_dir / DONE_MARKER
    if marker.exists() and not force:
        log(f"{marker} exists -- nothing to do (pass --force to redo)")
        return json.loads((work_dir / "input.json").read_text(encoding="utf-8"))

    origin = check_source(source)
    started = time.time()
    work_dir.mkdir(parents=True, exist_ok=True)
    if marker.exists():
        marker.unlink()
    for name in COPIED:
        shutil.copyfile(source / name, work_dir / name)

    collections = tuple(origin["collections"]) or tuple(html.COLLECTION_CODES)
    rows, texts = candidate_texts(work_dir, collections=collections, cache_dir=cache_dir,
                                  log=log)
    if not len(rows):
        raise SystemExit("no searched text to index")

    mark = time.time()
    tokenized = bm25s.tokenize(texts, lower=TOKENISER["lower"], stopwords=None, stemmer=None,
                               return_ids=True, show_progress=False)
    seconds_tokenise = round(time.time() - mark, 1)
    without_terms = sum(1 for ids in tokenized.ids if not ids)
    # Before indexing: `BM25.index` adds an empty "" token to this very dict.
    vocabulary = len(tokenized.vocab)
    log(f"{len(texts)} documents tokenised in {seconds_tokenise}s: "
        f"{vocabulary} terms, {without_terms} documents without any")

    mark = time.time()
    retriever = bm25s.BM25(method=METHOD, k1=K1, b=B)
    retriever.index(tokenized, show_progress=False)
    seconds_index = round(time.time() - mark, 1)

    # A fresh directory renamed into place, so a half-written index can never
    # look finished and a stale file from an earlier run can never survive.
    staging = work_dir / (scoring.INDEX_DIR + ".partial")
    if staging.exists():
        shutil.rmtree(staging)
    retriever.save(staging, show_progress=False)
    final = work_dir / scoring.INDEX_DIR
    if final.exists():
        shutil.rmtree(final)
    staging.rename(final)
    atomic_write_table(work_dir / scoring.TOKENS, pa.table({
        "row": pa.array(rows, type=pa.int32()),
        "token_ids": pa.array([list(ids) for ids in tokenized.ids],
                              type=pa.list_(pa.int32())),
    }))

    n = pq.ParquetFile(work_dir / "vector_ids.parquet").metadata.num_rows
    summary = {
        "method": scoring.BM25,
        "model": "bm25",
        "model_slug": "bm25",
        "k": None,
        "n": int(n),
        "instruments": origin["instruments"],
        "unique_names": True,
        "collections": origin["collections"],
        "legalvec_version": origin.get("legalvec_version"),
        "source_work_dir": str(source),
        "bm25": {
            "library": "bm25s",
            "library_version": bm25s.__version__,
            "method": METHOD,
            "k1": K1,
            "b": B,
            "query": "binary over distinct terms",
        },
        "tokeniser": TOKENISER,
        "vocabulary": vocabulary,
        "documents": int(len(rows)),
        "documents_without_terms": int(without_terms),
        "seconds_tokenise": seconds_tokenise,
        "seconds_index": seconds_index,
        "seconds": round(time.time() - started, 1),
    }
    atomic_write_text(work_dir / "input.json",
                      json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    atomic_write_text(marker, "")
    log(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", type=Path, default=Path("emb-run-atlas-bm25"))
    parser.add_argument("--from", dest="source", type=Path, required=True,
                        help="a finished dense work directory prepared with --unique-names "
                             "(e.g. emb-run-atlas)")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="legalvec cache to read (default: $LEGALVEC_CACHE_DIR)")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    prepare(args.work_dir, args.source, cache_dir=args.cache_dir, force=args.force)


if __name__ == "__main__":
    main()
