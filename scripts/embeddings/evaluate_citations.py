"""Row-level evaluation of the Atlas data sets against explicit citations (issue #273).

`evaluate_links.py` asks, per *instrument*, whether the law a reglamento develops
is its closest instrument. This asks the question one *unit row* at a time, with
signal C of `gold_links.py`: a unit row that cites "artículo N de la Ley X" names
the law it is about, and the three data sets (0.6B, 4B, BM25) can be asked where
that law ranks among the 1,302 foreign instruments *for that one row*.

A model's `nearest.parquet` keeps only the winner of each row, so the rank needs
the row's full score vector: each model's work directory is re-scored here with
its own scorer (`scoring.scorer_for`: the exact cosine over `vectors.npy`, or
BM25) under the very mask the matrix used -- the source's *exclusively owned*
texts and the texts no searched row owns are removed
(`instrument_matrix.owners_of_rows` / `own_rows_of`, imported, not copied) -- and
every instrument's score is the best over the vector rows it owns. The cited law's
rank is its **competition rank** (1 + the foreign instruments scoring strictly
more, by the scorer's own tie tolerance). A text the citing row shares word for
word with the cited law is a column the law owns and the mask keeps, so it ranks
the law first: the matrix's own rule, kept, and counted (`law_owns_text`).

Two checks keep this an evaluation of the published data sets and not of a fourth
method: the best foreign score recomputed here equals `nearest.parquet`'s
`similarity` for every citing row (`scoring.agrees`, tolerance 1e-6 -- a
`SystemExit` otherwise, never loosened), and a row is scored only when the
matrix counted it in **every** model (`nearest.parquet`'s `counted`: not an
`identical_shared` or `no_match` row), so the models are compared on the same
rows. How many each rule removes is reported.

    python evaluate_citations.py                  # the three default work directories
    python evaluate_citations.py --report         # print the table, offline

Law level only (not the cited article), single-law rows only (a row citing two
laws has two defensible answers; counted, not scored). A law that has no score at
all (BM25, no shared term) has no rank: reciprocal rank 0, never recalled, and it
counts as `n_foreign + 1` for the median.

**Metrics**: recall@1, recall@5, recall@10, MRR and the median rank, per model and
per collection (reglamentos / lineamientos), and for the ten most cited laws.
**Significance** is #272's rule, fixed beforehand: for every pair of models and
every metric a paired bootstrap over rows (seed 0, 10,000 resamples) gives the
95 % percentile interval of the mean (median) difference, McNemar's exact test is
added for the recall@k ones, and a difference is **real only if the interval
excludes 0 and, for a binary metric, p < 0.05**; otherwise a tie.

Everything is local: `vectors.npy` / `bm25-index/` / `tokens.parquet` and
`nearest.parquet` of the work directories, `gold-links/citations.parquet`. A missing
input is a `SystemExit` naming it; nothing is rebuilt.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import evaluate_links  # noqa: E402
import instrument_matrix  # noqa: E402
import scoring  # noqa: E402
import unique_instruments  # noqa: E402
from _atomic import atomic_write_text  # noqa: E402
from gold_links import CITATIONS, SUBDIR  # noqa: E402

DEFAULT_GOLD_DIR = Path("emb-run-atlas") / SUBDIR
EVALUATION = "evaluation-rows.json"
RECALL_AT = (1, 5, 10)
TOP_LAWS = 10
CONSTITUTION = "constitucion politica de los estados unidos mexicanos"
BLOCK_ROWS = 256

#: `(key, label, kind)`: `binary` (a McNemar test too), `mean`, or `median`
#: (lower is better).
METRICS = (
    ("recall1", "recall@1", "binary"),
    ("recall5", "recall@5", "binary"),
    ("recall10", "recall@10", "binary"),
    ("mrr", "MRR", "mean"),
    ("median_rank", "median rank", "median"),
)


# -- the rows ------------------------------------------------------------------- #

def load_citations(gold_dir: Path, gold: dict):
    """The single-law citing rows of `citations.parquet`, one per `(i,
    text_sha1)`, with the cited law."""
    import pandas as pd

    path = Path(gold_dir) / CITATIONS
    if not path.is_file():
        raise SystemExit(f"{path} does not exist: run gold_links.py first")
    table = pd.read_parquet(path)
    return table[table["single_law"]].drop_duplicates(["i", "text_sha1"]).reset_index(drop=True)


def citing_vector_rows(citations, work_dir: Path):
    """`citations` with the `row` of the vector each citing text got in
    `work_dir` (`vector_ids.parquet`, on `(coleccion, text_sha1)`)."""
    import pandas as pd
    import pyarrow.parquet as pq

    path = Path(work_dir) / "vector_ids.parquet"
    if not path.is_file():
        raise SystemExit(f"{path} does not exist")
    ids = pq.read_table(path).to_pandas()
    joined = citations.merge(ids, on=["coleccion", "text_sha1"], how="left")
    if joined["row"].isna().any():
        missing = joined[joined["row"].isna()].iloc[0]
        raise SystemExit(f"{path}: no vector for citing text {missing['text_sha1']} "
                         f"({missing['coleccion']} {missing['clave']}): the gold and the work "
                         "directory are from different preparations")
    joined["row"] = joined["row"].astype("int32")
    return joined


# -- the rank of the cited law ---------------------------------------------------- #

def instrument_layout(owners):
    """How to reduce a vector-row score to one score per instrument: the owned
    vector rows sorted by instrument (`pair_rows`), where each instrument's run
    starts (`starts`) and which instrument that is (`instruments`)."""
    pairs = sorted((instrument, row) for row, group in enumerate(owners) for instrument in group)
    instruments = np.array([p[0] for p in pairs], dtype=np.int64)
    pair_rows = np.array([p[1] for p in pairs], dtype=np.int64)
    starts = np.flatnonzero(np.r_[True, instruments[1:] != instruments[:-1]])
    return pair_rows, starts, instruments[starts]


def competition_rank(scores, law_score: float, *, tolerance: float, relative: bool):
    """`1 +` the scores strictly above `law_score` (beyond the tie tolerance),
    or `None` for a law with no score (`-inf`, or a lexical score of 0)."""
    if not np.isfinite(law_score) or (relative and law_score <= 0):
        return None
    above = law_score * (1.0 + tolerance) if relative else law_score + tolerance
    return 1 + int((scores > above).sum())


def rank_rows(scorer, units, rows, *, tolerance: float = instrument_matrix.DEFAULT_TOLERANCE,
              block_rows: int = BLOCK_ROWS, log=print):
    """For each citing row (a frame with `i`, `row`, `law`), the cited law's rank
    among the foreign instruments, the best foreign score (what `nearest.parquet`
    records) and whether the law owns a text identical to the row's.

    `units` is `instrument_matrix.unit_rows`' join; it is narrowed to the searched
    rows (`searched_mask`) and masked exactly as `sweep_answers` does: the
    source's exclusively owned vector rows plus the unowned ones.
    """
    searched = units[instrument_matrix.searched_mask(units)].reset_index(drop=True)
    owners = instrument_matrix.owners_of_rows(searched, scorer.n_rows)
    own_rows = instrument_matrix.own_rows_of(searched)
    unowned = np.array([c for c, group in enumerate(owners) if not group], dtype=np.int64)
    pair_rows, starts, instrument_ids = instrument_layout(owners)
    position = {int(i): n for n, i in enumerate(instrument_ids)}

    out = {"rank": [None] * len(rows), "best": [0.0] * len(rows), "law_owns_text": [False] * len(rows)}
    rows = rows.reset_index(drop=True)
    mark = time.time()
    for done, (i, group) in enumerate(rows.groupby("i")):
        exclusive = np.array([c for c in own_rows[i] if len(owners[c]) == 1], dtype=np.int64)
        masked = np.concatenate([exclusive, unowned])
        for start in range(0, len(group), block_rows):
            block = group.iloc[start:start + block_rows]
            scores = scorer.scores(block["row"].to_numpy())
            if masked.size:
                scores[:, masked] = -np.inf
            by_instrument = np.maximum.reduceat(scores[:, pair_rows], starts, axis=1)
            by_instrument[:, position[int(i)]] = -np.inf          # the source is not foreign
            for offset, (index, record) in enumerate(block.iterrows()):
                vector = by_instrument[offset]
                best = float(vector.max())
                if not np.isfinite(best) or (scorer.relative and best <= 0):
                    best = 0.0          # a lexical row with no match: recorded as 0
                out["best"][index] = best
                law_at = position.get(int(record["law"]))
                law_score = float(vector[law_at]) if law_at is not None else float("-inf")
                out["rank"][index] = competition_rank(
                    vector, law_score, tolerance=tolerance, relative=scorer.relative)
                out["law_owns_text"][index] = int(record["law"]) in owners[int(record["row"])]
        if done % 100 == 0:
            log(f"  {done + 1} instruments, {time.time() - mark:.0f}s")
    return out


def verify_against_nearest(rows, ranked, nearest, scorer, tolerance: float) -> float:
    """The largest difference between the best foreign score recomputed here and
    `nearest.parquet`'s `similarity` for the same `(i, row)`. A `SystemExit` the
    first time they disagree: the mask or the row mapping is wrong, and the answer
    is to fix that."""
    recorded = nearest.set_index(["i", "row"])["similarity"]
    worst = 0.0
    for n, (i, row) in enumerate(zip(rows["i"], rows["row"])):
        theirs = float(recorded.loc[(int(i), int(row))])
        mine = float(ranked["best"][n])
        if not scoring.agrees(mine, theirs, tolerance, scorer.relative):
            raise SystemExit(
                f"instrument {int(i)}, vector row {int(row)}: the best foreign score is {mine} "
                f"here and {theirs} in nearest.parquet -- the mask or the row mapping does not "
                "reproduce the matrix")
        worst = max(worst, abs(mine - theirs))
    return worst


def load_nearest(label: str, work_dir: Path):
    import pyarrow.parquet as pq

    path = Path(work_dir) / instrument_matrix.SUBDIR / "nearest.parquet"
    if not path.is_file():
        raise SystemExit(f"{label}: {path} does not exist (this script never rebuilds it: run "
                         "instrument_matrix.py)")
    frame = pq.read_table(path, columns=["i", "row", "similarity", "counted",
                                         "drop_reason"]).to_pandas()
    return frame.drop_duplicates(["i", "row"])


def score_model(label: str, work_dir: Path, citations, *, cache_dir=None, collections=None,
                log=print) -> dict:
    """One model's ranks for the citing rows, with the matrix's own flags."""
    work_dir = Path(work_dir)
    rows = citing_vector_rows(citations, work_dir)
    nearest = load_nearest(label, work_dir)
    log(f"{label}: loading the scorer and the unit join")
    units = instrument_matrix.unit_rows(work_dir, collections=collections, cache_dir=cache_dir,
                                        log=lambda *a: None)
    scorer = scoring.scorer_for(work_dir)
    log(f"{label}: ranking {len(rows)} citing rows ({scorer.method})")
    ranked = rank_rows(scorer, units, rows, log=log)
    tolerance = instrument_matrix.DEFAULT_TOLERANCE
    worst = verify_against_nearest(rows, ranked, nearest, scorer, tolerance)
    flags = rows[["i", "row"]].merge(nearest, on=["i", "row"], how="left")
    if flags["counted"].isna().any():
        raise SystemExit(f"{label}: a citing row is not a searched row of nearest.parquet")
    rows = rows.assign(rank=ranked["rank"], law_owns_text=ranked["law_owns_text"],
                       counted=flags["counted"].to_numpy(),
                       drop_reason=flags["drop_reason"].to_numpy())
    return {"rows": rows, "max_difference_from_nearest": worst, "method": scorer.method,
            "relative": scorer.relative}


# -- metrics ---------------------------------------------------------------------- #

def rank_array(ranks, cap: int) -> np.ndarray:
    """`ranks` as floats, a missing rank (no score) as `cap`."""
    return np.array([cap if r is None or r != r else r for r in ranks], dtype=float)


def metric_values(ranks, cap: int) -> dict[str, np.ndarray]:
    """Per row: the three recalls (0/1), the reciprocal rank (0 for no rank) and
    the rank itself (`cap` for no rank)."""
    missing = np.array([r is None or r != r for r in ranks])
    plain = rank_array(ranks, cap)
    values = {f"recall{k}": ((plain <= k) & ~missing).astype(float) for k in RECALL_AT}
    values["mrr"] = np.where(missing, 0.0, 1.0 / plain)
    values["median_rank"] = plain
    return values


def summarise(values: dict[str, np.ndarray]) -> dict:
    n = len(values["mrr"])
    out = {}
    for key, _, kind in METRICS:
        if kind == "binary":
            out[key] = {"count": int(values[key].sum()), "mean": float(values[key].mean()) if n else None}
        elif kind == "mean":
            out[key] = {"mean": float(values[key].mean()) if n else None}
        else:
            out[key] = {"median": float(np.median(values[key])) if n else None}
    return out


def median_compare(first: np.ndarray, second: np.ndarray, indices: np.ndarray, *,
                   names=("first", "second")) -> dict:
    """The verdict on the median rank, where lower is better: the bootstrap
    interval of `median(first) - median(second)`; real only if it excludes 0."""
    differences = np.median(first[indices], axis=1) - np.median(second[indices], axis=1)
    low, high = np.percentile(differences, [(100 - evaluate_links.BOOTSTRAP_LEVEL) / 2,
                                            100 - (100 - evaluate_links.BOOTSTRAP_LEVEL) / 2])
    difference = float(np.median(first) - np.median(second))
    real = low > 0 or high < 0
    return {"difference": difference, "interval": [float(low), float(high)], "p": None,
            "verdict": "tie" if not real else f"{names[0] if difference < 0 else names[1]} better"}


def compare_models(per_model: dict[str, dict[str, np.ndarray]], *, seed: int,
                   resamples: int) -> list[dict]:
    labels = list(per_model)
    n = len(per_model[labels[0]]["mrr"])
    if n == 0:
        return []
    indices = np.random.default_rng(seed).integers(0, n, size=(resamples, n))
    pairs = []
    for first, second in itertools.combinations(labels, 2):
        for key, _, kind in METRICS:
            a, b = per_model[first][key], per_model[second][key]
            if kind == "median":
                result = median_compare(a, b, indices, names=(first, second))
            else:
                result = evaluate_links.compare(a, b, kind == "binary", indices,
                                                names=(first, second))
            pairs.append({"first": first, "second": second, "metric": key, **result})
    return pairs


def common_rows(scored: dict[str, dict]):
    """The citing rows every model's matrix counted, in the first model's order,
    and what each rule removed per model."""
    labels = list(scored)
    base = scored[labels[0]]["rows"][["i", "text_sha1", "coleccion", "law"]].copy()
    keep = np.ones(len(base), dtype=bool)
    removed = {}
    for label in labels:
        rows = scored[label]["rows"]
        counted = rows["counted"].to_numpy(dtype=bool)
        reasons = rows["drop_reason"].fillna("counted").to_numpy()
        removed[label] = {str(reason): int((reasons == reason).sum())
                          for reason in sorted(set(reasons)) if reason != "counted"}
        keep &= counted
    return base, keep, removed


def evaluate(gold: dict, gold_dir: Path, work_dirs: dict[str, Path], *, cache_dir=None,
             collections=None, seed: int = 0, resamples: int = 10000, log=print) -> dict:
    for label, work_dir in work_dirs.items():
        evaluate_links.check_instruments(label, work_dir, gold)
    all_citations = _all_citations(gold_dir)
    citations = load_citations(gold_dir, gold)
    scored = {label: score_model(label, path, citations, cache_dir=cache_dir,
                                 collections=collections, log=log)
              for label, path in work_dirs.items()}
    return summarise_scored(gold, citations, all_citations, scored, work_dirs, seed=seed,
                            resamples=resamples)


def _all_citations(gold_dir: Path):
    import pandas as pd

    return pd.read_parquet(Path(gold_dir) / CITATIONS)


def summarise_scored(gold: dict, citations, all_citations, scored: dict[str, dict],
                     work_dirs: dict[str, Path], *, seed: int, resamples: int) -> dict:
    """Everything `evaluation-rows.json` holds, from the per-model ranks."""
    labels = list(scored)
    names = gold.get("law_names", {})
    base, keep, removed = common_rows(scored)
    n_foreign = len(gold["instruments"]) - 1
    cap = n_foreign + 1

    ranks = {label: scored[label]["rows"]["rank"].tolist() for label in labels}
    values = {label: metric_values([r for r, k in zip(ranks[label], keep) if k], cap)
              for label in labels}
    kept = base[keep].reset_index(drop=True)
    kept_ranks = {label: [r for r, k in zip(ranks[label], keep) if k] for label in labels}

    def split(mask):
        return {label: summarise(metric_values([r for r, m in zip(kept_ranks[label], mask) if m],
                                               cap)) for label in labels}

    per_collection = {}
    for coleccion in sorted(set(kept["coleccion"])):
        mask = (kept["coleccion"] == coleccion).to_numpy()
        per_collection[coleccion] = {"rows": int(mask.sum()), "models": split(mask)}

    # One law -- the Constitution, cited by every kind of regulation -- is more than a
    # quarter of the rows, so the metrics are also given without it.
    constitution = {int(law) for law, nombre in names.items()
                    if unique_instruments.name_key(nombre).startswith(CONSTITUTION)}
    others = (~kept["law"].isin(constitution)).to_numpy()
    without_constitution = {"rows": int(others.sum()), "models": split(others)}

    law_counts = kept["law"].value_counts()
    top_laws = []
    for law in law_counts.index[:TOP_LAWS]:
        mask = (kept["law"] == law).to_numpy()
        top_laws.append({"law": int(law), "nombre": names.get(str(int(law)), str(int(law))),
                         "rows": int(mask.sum()),
                         "models": {label: summarise(metric_values(
                             [r for r, m in zip(kept_ranks[label], mask) if m], cap))
                             for label in labels}})

    return {
        "thresholds": {
            "rule": ("a difference is real only if the paired-bootstrap interval of the mean "
                     "(median) difference over rows excludes 0 and, for the recall@k metrics, "
                     "McNemar's exact test has p < p_threshold; otherwise a tie"),
            "bootstrap_level": evaluate_links.BOOTSTRAP_LEVEL, "resamples": resamples,
            "seed": seed, "p_threshold": evaluate_links.P_THRESHOLD,
            "tie_tolerance": instrument_matrix.DEFAULT_TOLERANCE,
            "rank": "competition rank among the foreign instruments; no score = no rank",
            "median_rank_of_no_score": cap, "foreign_instruments": n_foreign,
        },
        "citations": {
            "table_rows": int(len(all_citations)),
            "citing_rows": int(all_citations[["i", "text_sha1"]].drop_duplicates().shape[0]),
            "single_law_rows": int(len(citations)),
            "multi_law_rows_not_scored": int(
                all_citations[["i", "text_sha1"]].drop_duplicates().shape[0] - len(citations)),
            "removed_by_the_matrix_rules": removed,
            "scored_rows": int(keep.sum()),
            "law_owns_text": {label: int(scored[label]["rows"]["law_owns_text"][keep].sum())
                              for label in labels},
        },
        "models": {label: {"work_dir": str(work_dirs[label]), "method": scored[label]["method"],
                           "max_difference_from_nearest": scored[label]["max_difference_from_nearest"]}
                   for label in labels},
        "metrics": {label: summarise(values[label]) for label in labels},
        "pairs": compare_models(values, seed=seed, resamples=resamples),
        "per_collection": per_collection,
        "without_constitution": without_constitution,
        "top_laws": top_laws,
    }


# -- output ------------------------------------------------------------------------ #

def _cell(entry: dict, kind: str) -> str:
    if kind == "binary":
        return f"{entry['count']} ({entry['mean']:.3f})"
    if kind == "mean":
        return f"{entry['mean']:.3f}"
    return f"{entry['median']:g}"


def metric_table(models: dict, labels) -> list[str]:
    lines = ["| metric | " + " | ".join(labels) + " |", "|---|" + "---|" * len(labels)]
    for key, label, kind in METRICS:
        lines.append(f"| {label} | " + " | ".join(_cell(models[m][key], kind) for m in labels) + " |")
    return lines


def render_markdown(evaluation: dict) -> str:
    labels = list(evaluation["metrics"])
    cites = evaluation["citations"]
    rule = evaluation["thresholds"]
    lines = ["# Atlas data sets against explicit law citations (row level)", "",
             f"{cites['citing_rows']} citing rows, {cites['single_law_rows']} citing exactly one "
             f"law ({cites['multi_law_rows_not_scored']} citing several: not scored); removed by "
             f"the matrix rules (per model): {cites['removed_by_the_matrix_rules']}; "
             f"**{cites['scored_rows']} scored** (counted by every model). The cited law owns a "
             f"word-for-word copy of the row's text (rank 1 by the matrix's rule) in "
             f"{cites['law_owns_text']}. Foreign instruments: {rule['foreign_instruments']}.", "",
             f"Significance (fixed beforehand): paired bootstrap over rows, seed {rule['seed']}, "
             f"{rule['resamples']} resamples, {rule['bootstrap_level']:.0f} % percentile interval "
             f"must exclude 0 and, for recall@k, McNemar exact p < {rule['p_threshold']}; "
             "otherwise a tie.", "", "## All scored rows", ""]
    lines += metric_table(evaluation["metrics"], labels)
    lines += ["", "| pair | metric | difference | 95 % interval | McNemar p | verdict |",
              "|---|---|---|---|---|---|"]
    for pair in evaluation["pairs"]:
        low, high = pair["interval"]
        p = "" if pair["p"] is None else f"{pair['p']:.4f}"
        lines.append(f"| {pair['first']} - {pair['second']} | {pair['metric']} | "
                     f"{pair['difference']:+.4f} | [{low:+.4f}, {high:+.4f}] | {p} | "
                     f"{pair['verdict']} |")
    for coleccion, entry in evaluation["per_collection"].items():
        lines += ["", f"## {coleccion}: {entry['rows']} rows", ""]
        lines += metric_table(entry["models"], labels)
    entry = evaluation["without_constitution"]
    lines += ["", f"## Without the Constitution: {entry['rows']} rows", ""]
    lines += metric_table(entry["models"], labels)
    lines += ["", f"## The {len(evaluation['top_laws'])} most cited laws (recall@5)", "",
              "| law | rows | " + " | ".join(labels) + " |", "|---|---|" + "---|" * len(labels)]
    for entry in evaluation["top_laws"]:
        cells = [_cell(entry["models"][m]["recall5"], "binary") for m in labels]
        lines.append(f"| {entry['nombre']} | {entry['rows']} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold-dir", type=Path, default=DEFAULT_GOLD_DIR,
                        help="gold_links.py's output (default: emb-run-atlas/gold-links)")
    parser.add_argument("--work-dir", action="append", default=None, metavar="LABEL=PATH",
                        help="a model to score, repeatable (default: 0.6B=emb-run-atlas "
                             "4B=emb-run-atlas-4b BM25=emb-run-atlas-bm25)")
    parser.add_argument("--cache-dir", type=Path, default=None,
                        help="legalvec's cache (default: legalvec.cache.CACHE_DIR)")
    parser.add_argument("--output", type=Path, default=None,
                        help="evaluation-rows.json (default: in --gold-dir)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--resamples", type=int, default=10000)
    parser.add_argument("--report", action="store_true",
                        help="print the table from the written evaluation-rows.json and exit")
    args = parser.parse_args(argv)

    output = args.output or args.gold_dir / EVALUATION
    if args.report:
        if not output.is_file():
            raise SystemExit(f"{output} does not exist: run evaluate_citations.py first")
        print(render_markdown(json.loads(output.read_text(encoding="utf-8"))))
        return 0

    gold = evaluate_links.load_gold(args.gold_dir / "gold.json")
    evaluation = evaluate(gold, args.gold_dir, evaluate_links.parse_work_dirs(args.work_dir),
                          cache_dir=args.cache_dir, seed=args.seed, resamples=args.resamples)
    atomic_write_text(output, json.dumps(evaluation, ensure_ascii=False, indent=1) + "\n")
    markdown = render_markdown(evaluation)
    atomic_write_text(output.with_suffix(".md"), markdown)
    print(markdown)
    print(f"wrote {output} and {output.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
