# `scripts/embeddings/` — a vector for every text of every federal instrument

Fase 2 of issue #218 (the implementation plan for #217's research), widened
to all three SCJN corpora by issue #227's Fase 3: the Slurm build that turns
`md2akn.text_units()`'s output into one vector per distinct text of the
cached `scjn-leyes`, `scjn-reglamentos` and `scjn-lineamientos` releases.
This is orchestration for one research run on one specific cluster
(`headmaster` + `cemieredes`), not a package — it talks to Slurm, to Hugging
Face Hub, and to this machine's own `/home` layout, none of which belongs
inside `packages/`.

**This has been run, end to end, against the real cluster and both real
models** — six model × corpus runs (issue #227's Fase 3; the leyes half was
first run for #218 and rebuilt here under rules 8 and 9). What is *not*
automated is the publish: no GitHub Action creates or uploads a release
here, and a human runs the `PUBLICAR.md` the packaging step generates (issue
#115, Hallazgo C).

## One work directory per collection

`--coleccion {leyes,reglamentos,lineamientos}` picks the corpus (default
`leyes`, so the #218 command keeps working), and each gets its own work
directory: `emb-run-leyes/`, `emb-run-reglamentos/`, `emb-run-lineamientos/`.
Dedup stays inside a collection — measured at 0.9 % of the vectors saved by
deduplicating across them, against a shared file three independently
crawled, packaged and republished corpora would all have to be published
against (issue #227's decision 8).

`units.parquet` carries `coleccion` and `clave`: `clave` is a law's slug and
the `id_ordenamiento` of a reglamento or a lineamiento, and it is what names
every published file, so a reader never branches. `slug` and `codNota` are
null for the id-keyed collections, which have no DOF link at all (#220).

## The work directory

Everything lives under one directory per run, on `/home` (`--work-dir`, or
`$LEGALIA_EMB_WORK`) — never `/tmp`, which is not shared between
`headmaster` and the compute nodes on this cluster (verified: a file written
to `/tmp` on `headmaster` is invisible inside a job).

```
work/
  units.parquet  leaves.parquet  corpus-manifest.json
  shards.json
  runs/<model-slug>/
      shard-0000.parquet  shard-0000.done  shard-0000.json
      shard-0001.parquet.parcial
      shard-0002.failed
      vectors/
          vectors-<clave>-<model-slug>-<K>.parquet
          vectors-shared-<model-slug>-<K>.parquet
      manifest.json
```

## The sequence

```bash
# Once: populate the scjn/dofjson caches this reads from -- no network from here on.
nota2md download all

# 1. The corpus, as two Parquet files + a manifest. Once per collection:
python scripts/embeddings/build_units.py --work-dir emb-run-leyes
python scripts/embeddings/build_units.py --work-dir emb-run-reglamentos \
    --coleccion reglamentos
python scripts/embeddings/build_units.py --work-dir emb-run-lineamientos \
    --coleccion lineamientos

# 2. Dedup by text_sha1, sort by estimated token length, balance into shards.
python scripts/embeddings/plan_shards.py --work-dir emb-run-leyes --num-shards 12

# 3. One model at a time: download it, submit every pending shard, wait, retry, delete it.
python scripts/embeddings/submit_jobs.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-0.6B
python scripts/embeddings/status.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-0.6B

# 4. Once every shard is done: the per-instrument vector files + the manifest.
python scripts/embeddings/merge_shards.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-0.6B

# repeat 3-4 for the second model
python scripts/embeddings/submit_jobs.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-4B
python scripts/embeddings/merge_shards.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-4B

# 5. Once every model has merged: the release assets + PUBLICAR.md (never uploaded here).
python scripts/embeddings/package_vectors.py --work-dir emb-run-leyes \
    --coleccion leyes --out-dir emb-run-leyes/publish

# 6. A human reads PUBLICAR.md and runs it. Its upload step is this, per part --
#    resumable, because GitHub's secondary rate limit cuts a thousand-asset
#    upload in half and `xargs` leaves no record of what landed.
python scripts/embeddings/upload_release_assets.py scjn-leyes-vectors \
    emb-run-leyes/publish/parte-1.txt
```

Steps 2-4 repeat per collection against its own work directory; nothing
below `build_units.py`/`merge_shards.py` knows what a collection is — a
shard is keyed by `text_sha1` alone.

Incremental re-embedding after a reform: replan against what a model already
has, then resubmit — only the new units reach the GPU at all.

```bash
python scripts/embeddings/build_units.py --work-dir emb-run-leyes
python scripts/embeddings/plan_shards.py --work-dir emb-run-leyes \
    --against emb-run-leyes/runs/qwen3-0.6b
python scripts/embeddings/submit_jobs.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-0.6B
```

## The CPU smoke test

No cluster, no Slurm, no GPU — proves the whole pipeline (units -> shards ->
vectors -> merge) end to end. Still downloads the real 0.6B model's weights
(~1.2 GB) the first time it runs, since `encode_shard.py` has no fake mode of
its own:

```bash
python scripts/embeddings/build_units.py --work-dir ~/smoke --slug lft
python scripts/embeddings/plan_shards.py --work-dir ~/smoke --num-shards 1
python scripts/embeddings/encode_shard.py --work-dir ~/smoke \
    --model Qwen/Qwen3-Embedding-0.6B --shard 0 --device cpu --limit 512
python scripts/embeddings/merge_shards.py --work-dir ~/smoke \
    --model Qwen/Qwen3-Embedding-0.6B
```

`scripts/embeddings/tests/` covers the surrounding logic — atomic writes,
shard balancing, the `.done`/`.failed` marker contract, merge-by-hash —
against small fixtures and a monkeypatched pipeline, with no download and no
dependency on `torch`/`transformers` being importable at all.

## Design points worth restating (see issue #218 for the measurements)

- A shard is keyed by `text_sha1` alone: `encode_shard.py` never learns
  which instrument a text came from, so a text four laws share gets embedded
  once. `merge_shards.py` is where the instrument comes back, from
  `units.parquet`'s own `text_sha1 -> clave` map.
- `units.parquet` is byte-reproducible from the same release, cap and
  template — the row order is `(coleccion, clave, document order)`. Rebuilding
  the 315 laws under issue #227's rule 8 with rule 9 off
  (`--no-split-over-cap`) reproduced the pre-#227 file exactly, one byte
  apart: the pyarrow writer version in the Parquet footer.
- Every artifact is written as `<name>.parcial`, `fsync`'d, then renamed —
  the same convention `scjn.cache.SUFIJO_PARCIAL` uses, replicated in
  `_atomic.py` rather than imported (this directory has no other reason to
  depend on `scjn`'s cache layout).
- `padding_side = "left"` on the tokenizer is not optional (last-token
  pooling), and the only transform applied to the model's own output is the
  cast to `float16` — no normalization, no quantization, no truncation.
- A failing shard writes `shard-XXXX.failed` and does not fail the run;
  `status.py` says what to relaunch.

## The UMAP explorer (issue #241)

The same vectors, looked at. Issue #241 adds four scripts that fit UMAP on
**every** vector of the three corpora and turn the result into one
standalone HTML file — an overview of all ~400,000 unit rows, a detail view
of a clicked instrument (and, with `--neighbors k`, of a clicked text's `k`
nearest neighbours), and a view with one point per instrument. Exactly one
instrument is highlighted at a time: **the last click wins**, whichever view
it landed in.

### Where things are

| What | Path |
|---|---|
| The script that generates the page | `scripts/embeddings/build_umap_html.py` |
| The page | `output/umap-vectors-qwen3-0.6b.html` |
| Its Vega-Lite spec, on its own | `output/umap-vectors-qwen3-0.6b.vl.json` |
| Work directory (vectors, projections, neighbours) | `emb-run-umap/` |
| One directory per configuration | `emb-run-umap/nn016`, `nn032`, `nn064`, `nn128` |
| Earlier sweeps, kept on disk | `emb-run-umap/nn004`, `nn008`, `nn015`, `nn050`, `nn200` |

Neither `emb-run-umap/` nor `output/` is committed, so this table is how a
reader finds the file that produced a page they were handed. The page itself
carries the same answer in a footer — see the provenance footer below.

**This is a different cluster from everything above.** The vector build ran
on `cemieredes` (GPUs, its own `/home/mgraffg/.venvs/cluster` venv, one GPU
per `sbatch` job); this runs on `geoint`: partition `compute`, three nodes
`geoint0`/`geoint1`/`geoint2`, ~60 cores and ~245 GB each, **no GPU at all**
(`GRES=(null)`), time limit `infinite`, and a shared `/home` — so
`submit_umap.sh` calls this repository's own synced `.venv`
(`/home/mgraffg/software/LegalIA/.venv/bin/python`), which is the one
interpreter the login node and every job can agree on. `submit.sh` and
`submit_jobs.py` are untouched: they are the GPU cluster's and would ask for
a GPU that does not exist here.

`geoint0` is also the login node and was saturated by another user's work
outside Slurm, so every job is submitted with `--exclude=geoint0`
(`submit_umap.py --exclude`, that value as the default): two configurations
run on `geoint1`/`geoint2` and the rest queue. They are submitted heaviest
first (`128, 64, 32, 16`), so the long poles start immediately; no job waits on
another, because the shared neighbour table is computed once and kept.
`prepare_umap_input.py` and `build_umap_html.py` do run on the login node —
they are minutes and a couple of GB.

The sweep is `project_umap.DEFAULT_N_NEIGHBORS = (16, 32, 64, 128)`, one
constant the launcher and the HTML builder both import. It has moved twice:
15 / 50 / 200 first, then 4 / 8 / 16 / 32 for a finer look at local
structure, then this window when the small neighbourhoods turned out to
shatter the cloud into filaments. **Nothing is ever deleted**: `nn016` and
`nn032` were reused exactly as they were (only 64 and 128 ran), and
`build_umap_html.py --projections all` brings every earlier directory back
into the radio.

```bash
uv sync --group viz                       # umap-learn, pynndescent, altair, pandas, vl-convert-python
uv run --group viz python scripts/embeddings/prepare_umap_input.py
uv run --group viz python scripts/embeddings/submit_umap.py --wait
uv run --group viz python scripts/embeddings/build_umap_html.py
```

Everything derived lives under `emb-run-umap/` and `output/`, both
gitignored: no vector, no projection and no HTML is ever committed.

### `prepare_umap_input.py` — one matrix, read once

Three jobs each re-reading ~600 parquet files would triple a ten-minute step
and could disagree about row order; one `vectors.npy` cannot. It reads
`legalvec.load_vectors` once per instrument (the published per-instrument
file unioned with the collection's shared file), deduplicates **inside** a
collection and never across it (issue #227's own rule: a text two
collections share got a vector in each release, so it gets two rows here),
and writes `vectors.npy` (`float16`, exactly as published),
`vector_ids.parquet` (`row` -> `coleccion`, `text_sha1`),
`instruments.parquet`, `centroid_input.npy` and `input.json`, then
`prepare.done`. A missing asset surfaces as `legalvec.AssetNotCached`, never
as a silently skipped instrument.

An instrument's centroid is the mean of its **unit rows'** vectors, so a law
that repeats the same transitorio is pulled toward that text as many times
as it repeats it. The rows are copied out of each `VectorSet` rather than
kept as views: a view keeps that instrument's whole matrix (its own file plus
the ~11 MB shared one) alive for as long as any single row of it is
referenced, which measured at ~7 GB after 300 instruments and would have
reached ~25 GB.

### `project_umap.py` — one configuration, one node

`umap.UMAP(n_components=2, metric="cosine", n_neighbors=..., min_dist=0.1,
n_jobs=-1, low_memory=False)` on all N rows, with **no `random_state`**: it
forces single-threaded execution, which on a 60-core node turns an hour into
a day. Reproducibility rests on keeping `coordinates.parquet` and
`projection.json`, not on a seed.

Coordinates are min-max scaled to `[0, 1]` per configuration (the raw range
is recorded in `projection.json`), so the HTML's radio switches projections
without rescaling an axis — UMAP's raw units carry no meaning anyway. The
1,523 instrument centroids are `transform`ed by the fitted model and mapped
through the *same* affine map, so an instrument may land slightly outside
`[0, 1]`, which is information rather than an error.

`--knn 15` writes `neighbors.parquet` at the **work-directory root**, not
inside a configuration: the neighbours live in the 1,024-dimension embedding
space, so they are the same for every projection. They come from a separate
`pynndescent.NNDescent(n_neighbors=16)` index with the self column dropped,
rather than from UMAP's private `_knn_indices`. The launcher passes `--knn`
to the cheapest configuration only, and an existing `neighbors.parquet` is
**kept** rather than rewritten (`--force-knn` overrides): a second sweep over
other `n_neighbors` changes no distance in the embedding space, so k stays 15
across every sweep and the second one paid nothing for it.

### `submit_umap.py` — and why its wait is chunked

`submit_umap.py --wait` blocks polling `squeue` until every job has left the
queue, up to `--max-wait-hours 8`, then prints the table and `scancel`s
anything still running at the ceiling. That is the human command. An
automated session cannot use it as such: its per-command timeout is minutes,
and ending a turn to wait for a background process kills the jobs'
supervisor with the session. So the wait is resumable —
`--wait --max-wait-minutes 9` returns exit status 75 ("still running",
naming what remains and for how long) and the caller simply calls it again;
the state lives in `emb-run-umap/jobs.json`, not in a process that has to
stay alive. `--report` prints the same table offline.

There is **no retry and no fallback**. A configuration that dies or hits the
8-hour ceiling is reported with the tail of its own Slurm output, and the
HTML is built from the configurations that finished (issue #241's explicit
choice: a fit that does not fit is a finding, not something to hide behind a
sampled rerun). Re-running the launcher skips whatever already has a
`.done`, so relaunching one transient failure is a one-line command.

### `build_umap_html.py` — one file, three linked views

One mark per **unit row** (a shared text is one vector but several unit
rows, drawn once per row that carries it), all six `unit_type`s by default.
The data is inlined with short keys: `v` (vector row), `c` (collection
letter), `i` (instrument index), `u` (unit-type code), `e` (`eId`), one
`x`/`y` pair per projection rounded to 3 decimals, and `n` — the neighbours
as one comma-separated string, ~100 bytes against a JSON array's ~180.
Altair consolidates identical datasets, so the four layers that read the
full point table inline it once.

- **Overview**: `pick` (a point selection on click) and `collections` (a
  point selection bound to the colour legend, which hides a whole corpus).
- **Detail**: the picked instrument's every unit (honouring `pick` *or*
  `pick_instrument`), the picked point itself, and — only with `--neighbors
  k` — that text's neighbours as an outline mark, so a neighbour that is also
  in the same instrument reads as both; all over a faint
  `--background-points` sample. Empty until something is clicked; a text mark
  names the instrument and the `eId`. The view's title says which of the two
  it is showing, so it never promises neighbours that are switched off.
- **Instruments**: a three-layer view — one mark per instrument at its
  centroid (size by unit count, its own `pick_instrument` selection), plus a
  black ring and the instrument's name around whatever was clicked, in
  *either* view. Ring and label are filtered by the same predicate the detail
  view uses (`pick.i` **or** `pick_instrument.i`), so the two cannot
  disagree. A ring rather than a colour change, because colour already means
  collection.

**What each mark means is on the page**, as the views' own subtitles rather
than a hand-drawn legend layer (a subtitle is a few lines of spec, renders
the same on every renderer, and cannot drift from the data):

| Mark | Meaning |
|---|---|
| ◆ black diamond, detail view | the clicked text |
| ○ red rings, detail view | with `--neighbors k` only: its `k` nearest neighbours by cosine, across all three collections (the count is the flag's value, never a literal) |
| filled shapes, detail view | every unit of the same instrument; shape = unit type |
| grey cloud, detail view | the `--background-points` sample, for orientation only |
| black ring + name, instrument view | the instrument of the clicked text, or the clicked centroid |

Knobs: `--unit-types`, `--projections` (a comma-separated list, or `all` for
every finished directory including the earlier sweeps'), `--neighbors N`
(**0 by default** — see below), `--text-chars N` (N characters of the unit's
own text in the tooltip), `--background-points`, `--overview-sample` (thins
only the overview layer), `--inline-js` (embed vega/vega-lite/vega-embed
instead of loading them from jsdelivr) and `--nearest`.

`--nearest` is **off** by default, and that is a bug fix rather than a
preference. Vega-Lite implements `nearest: true` by inserting a hidden
Voronoi mark that captures the pointer; the tooltip is then evaluated on that
mark, whose datum is a wrapper, so every field it names comes out
`undefined`. The first pass shipped `nearest` on and the whole overview
tooltip read `undefined` — that is the finding. With it off the pointer has
to land on the mark itself, so the overview mark went from `size=3`,
`opacity=0.3` to `size=10`, `opacity=0.25`: clickable, still a cloud. The
flag remains, for anyone who wants the old behaviour back.

#### The last click wins

The overview and the instrument view own two independent selections, and
Vega-Lite cannot define one selection over two concatenated views. Before
this was wired, clicking a unit and then a centroid left **both** live: the
detail view showed the union of two instruments and the bottom view ringed
two centroids, with nothing on the page saying which units belonged to
which.

The fix is that each selection is *cleared* by a click in the other's marks.
A selection's `clear` accepts any Vega event stream, `@<markname>:click`
scopes a stream to one view's marks, and a comma merges streams — so
`dblclick` clearing survives alongside it:

| selection | `clear` |
|---|---|
| `pick` (overview) | `dblclick, @centroids_1_marks:click` |
| `pick_instrument` (instrument view) | `dblclick, @overview_marks:click` |

Those mark names are the compiler's, not ours. `build_umap_html.py` names
the two clickable views (`.properties(name="overview")` and
`name="centroids"`), Vega-Lite compiles a view named `v` into a mark named
`v_marks`, and Altair adds one twist on the way: a **layer child** inside a
concat gets that concat row's index appended, so `centroids` reaches
Vega-Lite as `centroids_1` and ends up as `centroids_1_marks`. A unit view
like the overview keeps its name as it is. Both names are module constants
(`OVERVIEW_MARKS`, `CENTROIDS_MARKS`).

Nothing trusts them. `check_last_click_wins` compiles the spec to Vega with
`vl_convert` (on a copy whose inlined datasets are truncated to five rows —
mark names and signals are structural) and **fails the build** unless both
marks exist and each selection's `*_tuple` signal has a clearing `on` entry
(`update: "null"`) carrying the other view's `markname`. It runs before
anything is written, its result goes into the logged `measured` dict as
`last_click_wins`, and `tests/test_umap_scripts.py` asserts the same thing
on a toy spec — so a Vega-Lite or Altair upgrade that renames a mark fails a
13-second test rather than a 70 MB build. **A person still confirms the
clicks in a browser**: the compiled signals are evidence that the streams
are wired, not that the page feels right.

#### Neighbours are off by default

`--neighbors` defaults to **0**: no red rings, no `n` field in the inlined
data, no neighbour line in the detail subtitle, and a detail title that does
not mention them ("click a unit or an instrument: every unit of that
instrument"). The reader asked for the neighbours to go "en este momento",
so the machinery is switched off rather than removed — `--neighbors 15`
restores the full page with no refit, because `neighbors.parquet` stays on
disk and `project_umap.py --knn` is untouched.

Every generated page ends with a small grey **provenance footer** naming this
script, the repository commit (`unknown` where there is no git), the ISO
date, the work directory, the projections and the exact command line; the
`.vl.json` carries the same record in `usermeta.provenance`, so a page and a
spec can be matched. It is inserted by post-processing Altair's HTML —
Altair's saver has no hook for anything outside the chart, and inserting
after the chart's `<div>` leaves both the CDN and the `--inline-js` template
intact. `build_umap_html.py` also prints the absolute path of both files
when it finishes.

**The interactive behaviour is verified by a person opening the file.** What
the scripts themselves verify is the spec: `chart.to_dict()` validates
against the Vega-Lite schema, the spec is compiled to Vega and both
cross-view `clear` streams are asserted (see "the last click wins" above,
which is also the one check that *stops* the build), the written `.vl.json`
is reloaded with Altair, and `tests/test_umap_scripts.py` asserts the `pick`,
`pick_instrument`, `collections` and `proj` params, the neighbour filter, the
legend binding and the canvas renderer are all in it.

### Tests

```bash
uv run --group viz pytest scripts/embeddings/tests -q
```

Synthetic data, no network, no Slurm, no real UMAP fit: `umap` and
`pynndescent` are stubbed into `sys.modules` with known coordinates and
neighbours, so what is under test is the dedup rule, the centroid mean, the
`[0, 1]` scaling and its affine reuse, the `.done` contract, the launcher's
submission order / wait / report, and the generated spec.

### Measured, on `geoint`, 2026-09-21

`prepare_umap_input.py`, on the login node: **775.5 s** (12.9 min) for
**381,349** vectors at K=1,024 — 107,691 leyes + 264,911 reglamentos +
8,747 lineamientos, exactly the distinct-text counts of the three
`units.parquet` — plus **1,523** instrument centroids (315 + 1,082 + 126).
`vectors.npy` is 781 MB, `vector_ids.parquet` 17 MB, peak RSS ~1.4 GB.

Then three jobs, submitted at once, `--exclude=geoint0`: `nn200` and `nn015`
started immediately on `geoint1`/`geoint2`, `nn050` queued for `Resources`
and started ~5 min later on `geoint2`. Every configuration finished; none
came near the 8-hour limit.

| config | `n_neighbors` | node | load | fit | centroids | kNN | total | peak RSS |
|---|---|---|---|---|---|---|---|---|
| `nn015` | 15 | `geoint2` | 14.5 s | 203.9 s | 22.3 s | 15.3 s | **284.5 s** | 10.1 GB |
| `nn050` | 50 | `geoint2` | 1.5 s | 211.8 s | 25.0 s | — | **258.5 s** | 10.8 GB |
| `nn200` | 200 | `geoint1` | 14.5 s | 469.8 s | 45.6 s | — | **555.7 s** | 15.8 GB |

So the estimates in the issue (20–60 min, 30–90 min, 1–3 h) were an order of
magnitude pessimistic: 62 numba threads on an idle 60-core node fit 381,349
× 1,024 in 3–8 minutes, and `n_neighbors=200` costs ~2.3× the fit of
`n_neighbors=15` rather than ~13×. The k=15 cosine neighbour table
(`pynndescent`, 16 neighbours with the self column dropped) took **15.3 s**
and 42 MB of parquet — cheap enough that computing it in the cheapest job,
once, is not a real economy so much as a way to keep it out of the
projections. Peak RSS never passed 16 GB of the ~245 GB a node has, so the
"fit on everything" decision was never close to the constraint it was
weighed against.

`build_umap_html.py`, on the login node, ~2 min:

```
408804 points, 1523 instruments
output/umap-vectors-qwen3-0.6b.html: 113.6 MB, 408804 points
```

**113.6 MB, not the 50–60 MB the issue estimated** — three projections'
`x`/`y` pairs, the neighbour strings and JSON's own key overhead over
408,804 rows. The knobs are real: `--neighbors 0` drops it to
67.1 MB, and `--projections nn015` or `--overview-sample` cut it
further. The default is left where issue #241 asked for it, and the size is
printed rather than hidden.

### Measured, the second sweep, `geoint`, 2026-09-21

Four jobs, submitted at once, `--exclude=geoint0`: `nn032` and `nn016`
started immediately on `geoint1`/`geoint2`, `nn008` and `nn004` queued
(`Resources`, then `Priority`) and started ~4 min later on the node each
freed. `prepare_umap_input.py` was **not** rerun — the same 381,349 vectors —
and `neighbors.parquet` was kept, so k stays 15 and the kNN column is empty
for every row.

| config | `n_neighbors` | node | load | fit | centroids | kNN | total | peak RSS |
|---|---|---|---|---|---|---|---|---|
| `nn004` | 4 | `geoint2` | 1.4 s | 803.7 s | 21.5 s | kept | **836.7 s** | 10.1 GB |
| `nn008` | 8 | `geoint1` | 1.5 s | 346.1 s | 21.5 s | kept | **382.3 s** | 10.3 GB |
| `nn016` | 16 | `geoint2` | 1.5 s | 191.4 s | 21.8 s | kept | **232.8 s** | 10.1 GB |
| `nn032` | 32 | `geoint1` | 1.5 s | 170.1 s | 23.2 s | kept | **212.7 s** | 10.4 GB |

**In this range the cost runs the other way**: `n_neighbors=4` cost 4.7× the
fit of `n_neighbors=32`, where the first sweep had `n_neighbors=200` cost
2.3× `n_neighbors=15`. A small neighbourhood makes a sparser, more
fragmented graph, and UMAP's own heuristic then runs many more epochs over
it. So the launcher's "heaviest first" rule, which sorts by descending
`n_neighbors`, in fact submitted this sweep *cheapest* first. It is kept as
issue #241 specifies it — with two idle nodes and four jobs of 3–14 minutes
the order cost nothing — but the rule is a heuristic about a monotone cost,
and this table is the measurement that says the cost is not monotone. Peak
RSS was ~10 GB for all four, of ~245 GB.

The page itself, rebuilt from the four:

```
408804 points, 1523 instruments
output/umap-vectors-qwen3-0.6b.html: 124.7 MB, 408804 points
```

124.7 MB against the first pass's 113.6, for the one reason that matters
here: four `x`/`y` pairs per point instead of three.

One correction the run itself forced: reloading the written `.vl.json` with
`alt.Chart.from_dict` **validates every inlined data row against the
schema**, which on a 113 MB spec had not finished after ten minutes. The
reload now happens with `validate=False`, and the schema check runs on a
copy whose datasets are truncated to five rows — the spec's structure is
what a schema can say anything about anyway.

### Measured, the third sweep, `geoint`, 2026-09-22

The window moved up to 16 / 32 / 64 / 128, so only **two** jobs ran:
`submit_umap.py` skipped `nn016` and `nn032` (both already `.done`) and
submitted `nn128` then `nn064`, which started at once on `geoint1`/`geoint2`.
`prepare_umap_input.py` was not rerun — the same 381,349 vectors — and
`neighbors.parquet` was kept again, so k stays 15 across all three sweeps.

| config | `n_neighbors` | node | load | fit | centroids | kNN | total | peak RSS |
|---|---|---|---|---|---|---|---|---|
| `nn016` | 16 | `geoint2` | 1.5 s | 191.4 s | 21.8 s | kept | **232.8 s** | 10.1 GB |
| `nn032` | 32 | `geoint1` | 1.5 s | 170.1 s | 23.2 s | kept | **212.7 s** | 10.4 GB |
| `nn064` | 64 | `geoint2` | 1.5 s | 238.5 s | 27.3 s | kept | **276.9 s** | 11.3 GB |
| `nn128` | 128 | `geoint1` | 1.5 s | 342.2 s | 35.6 s | kept | **389.3 s** | 13.4 GB |

(The first two rows are the second sweep's own measurement, repeated here
because these are the directories this sweep reused rather than refitted.)

**Above 32 the cost is monotone again**, and mildly so: 128 costs 2.0× the
fit of 16, against the 4.7× *penalty* 4 paid over 32 in the second sweep. So
the launcher's "heaviest first" rule submitted this sweep in genuine
heaviest-first order — the first time in three sweeps. Peak RSS grows with
`n_neighbors` as the neighbour graph does (10.1 → 13.4 GB), still an order
of magnitude under the ~245 GB a node has. Centroid `transform` follows the
same curve (21.8 → 35.6 s).

The page, rebuilt from the four with neighbours off:

```
408804 points, 1523 instruments
last click wins: pick_tuple cleared by @centroids_1_marks:click,
                 pick_instrument_tuple cleared by @overview_marks:click
output/umap-vectors-qwen3-0.6b.html: 78.2 MB, 408804 points
```

**78.2 MB**, against the second sweep's 124.7 for the same four projections:
the `n` column — one comma-separated string of 15 vector rows per point,
~100 bytes × 408,804 — was the whole difference, which is the measured
answer to "what do the neighbours cost". The compiled-Vega check ran on the
real spec and logged both clear streams, as above; the footer is present
exactly once.

## The instrument map (issue #242)

The UMAP explorer above is a picture of **texts**. This is a picture of the
1,523 **instruments** that own them, built from one question asked of every
unit of every federal law, reglamento and lineamiento:

> which instrument owns the text closest to this one, among all the texts
> that are not exclusively mine?

Weighing the answers gives a square matrix `A` (1,523 × 1,523, rows and
columns in `instruments.parquet`'s `i` order): every unit row of instrument
`I` hands out a total weight of **1**, `A[I, J] += 1/m` to each of the `m`
instruments owning a winning text. An instrument is then represented by
**where its articles' nearest foreign neighbours live** — a distribution over
the other instruments — rather than by its own text, and two instruments land
together when their articles point at the same places.

### Where things are

| What | Path |
|---|---|
| The matrix (a Slurm job) | `scripts/embeddings/instrument_matrix.py` |
| The page | `scripts/embeddings/build_instrument_umap_html.py` → `output/umap-instruments-qwen3-0.6b.html` (+ `.vl.json`) |
| Everything derived | `emb-run-umap/instrument-matrix/` (`matrix.npy`, `nearest.parquet`, `matrix.json`, `umap.parquet`, `umap.json`, `job.json`, `slurm-*.out`, `.done`) |

Nothing here is committed, and nothing `prepare_umap_input.py` or
`project_umap.py` wrote is touched: this is one subdirectory inside #241's
own work directory, and it only ever *reads* `vectors.npy`,
`vector_ids.parquet` and `instruments.parquet`.

```bash
uv run --group viz python scripts/embeddings/instrument_matrix.py --dry-run
uv run --group viz python scripts/embeddings/instrument_matrix.py --submit
uv run --group viz python scripts/embeddings/instrument_matrix.py --wait --max-wait-minutes 9
uv run --group viz python scripts/embeddings/build_instrument_umap_html.py
```

### The rules that define `A`

Each of these was a decision in issue #242, not a default:

- **All six `unit_type`s count**, not only `article`: the question is about
  everything that makes up a document. There is no `--unit-types` flag —
  narrowing it is a later experiment, not a knob.
- **Ties count, every one of them.** A row's winners are every column within
  `--tolerance` (1e-6) of its best. Identical texts across collections are
  *exact* ties in float32, and breaking them by column index would silently
  prefer `leyes` to everything else.
- **`1/m` to each of the `m` instruments owning a winning text.** A text two
  instruments share is evidence about both, so both are credited — but a unit
  row is one article and weighs one, however many instruments answer for it.
  Row sums therefore equal the instrument's unit-row count exactly, which
  `matrix.json` records as `row_sums_equal_units`. The first pass added +1 to
  each instead; see *What the first run measured, and why the rule changed*
  below.
- **Per unit row, not per distinct text.** A boilerplate transitorio repeated
  `m` times inside a code is `m` articles and counts `m` times — the same
  choice #241's centroids made.
- **Only columns owned *exclusively* by the source are masked.** A text `I`
  shares with `J` stays a candidate: the nearest foreign neighbour of such a
  unit is that very text, at similarity 1, which is the strongest relation
  there is and the last thing to hide.
- **Exact cosine, by blocked matrix products.** Not `neighbors.parquet`
  (k=15 neighbours of an article of a 3,600-article code are all inside that
  code, so the foreign one is never among them) and not an approximate
  index. The published vectors are **not** normalised — their norms run 92 to
  121 — so the matrix is normalised once, in place, before any product.
  `--block-rows` (default 1,024) bounds one product at ~1.6 GB whatever the
  instrument; `ccf`'s 3,654 distinct texts against all 381,349 would be
  5.6 GB in one piece. Blocking changes no count, which a test asserts.
- **Directed, never symmetrised.** A reglamento pointing at its law says
  nothing about the law pointing back. The other direction is not thrown
  away: the page's tooltip carries both the row sum (`units pointing out`)
  and the column sum (`foreign units pointing here`).

The unit → instrument → vector row join is **`build_umap_html.load_frames`
itself**, imported and called with no projection, rather than a second copy:
the two scripts must never disagree about which vector row a unit got.

`nearest.parquet` keeps the evidence, one row per unit row: `similarity`,
`n_winners` (how many vector rows tied), `targets` (the instruments
credited), `m` (how many of them) and `weight` (`1/m`), next to `clave`,
`unit_type` and `eId`. `n_winners` and `m` are different numbers: one winning
row can have several owners, and two tied rows can share one.

### The Slurm plumbing

`--dry-run`/`--submit`/`--wait`/`--report`, in `submit_umap.py`'s own style
and reusing its `queued_jobs`: one job, `--exclude=geoint0`, a two-hour
ceiling, and **no retry** — a job that dies is reported with the tail of its
own Slurm output, never resubmitted automatically. `--wait
--max-wait-minutes N` returns **75** while the job is still there, 0 once
`.done` exists and 1 if the job left the queue without one, so an automated
session can poll in chunks instead of holding a process open. `.done` makes a
plain rerun a no-op; `--force` recomputes — `--submit --force` forwards the
flag into the job (the `.done` check happens there, not at submission time)
and deletes the previous run's `.done` first, so `--wait` cannot mistake the
run being replaced for the one it is waiting on.

### The page

`build_instrument_umap_html.py` runs on the login node — 1,523 × 1,523 is
seconds of work, so there is no job to submit. Rows are L2-normalised (a
zero row raises rather than reaching UMAP as `nan`), then fitted four times
with `n_neighbors` 4 / 8 / 16 / 32, `metric="cosine"`, `min_dist=0.1` and
**`random_state=0`** — the opposite of `project_umap.py`'s decision, for the
opposite reason: a seed costs a single-threaded fit, which at this size is
seconds, and buys a reproducible page. Each projection is min-max scaled into
`[0, 1]` with its raw range recorded, so the radio switches between them
without rescaling an axis.

One layered scatter: colour by collection (legend-bound toggle), size by
`units` on a **log** scale (1 to 3,663 — on a linear scale every lineamiento
would be an invisible dot), a radio for `n_neighbors` defaulting to 16, and a
click that puts a black ring and the instrument's name on the picked point
and red rings on the ten instruments its units point at hardest (the
comma-separated-string trick #241 validated, read back with Vega's `split`).
`nearest` is **off**, for the reason #241 measured. The tooltip carries
`nombre`, `clave`, `coleccion`, `units`, both directions of the matrix and
the five strongest targets with their weights — all three to **one decimal**,
since a weight is a sum of fractions. `units pointing out` now equals `units`
for every instrument, which is the `1/m` rule visible on every tooltip; both
are kept for exactly that reason, and the column sum (`foreign units pointing
here`) is the one that varies. The same provenance footer and
`usermeta.provenance` as #241's page, through the same helpers.

**A person still confirms the interaction in a browser.** What the script
verifies is the spec: `chart.to_dict()` against the Vega-Lite schema, the
written `.vl.json` reloaded, and the tests below.

### Tests

```bash
uv run --group viz pytest scripts/embeddings/tests/test_instrument_matrix.py -q
```

A six-instrument toy corpus over two collections whose vectors are written
by hand, so every weight in the expected matrix is derivable with a pen: a
text shared inside a collection wins at cosine 1, a text identical across
collections wins at cosine 1 from the other side, one row ties across two
foreign instruments and gives ½ to each, one text repeated twice inside an
instrument counts twice, an instrument's own exclusive texts are masked, and
a boilerplate line carried by three leyes at once ("Se deroga.") is won
outright — one winning row, three owners — by a lineamiento whose unit hands
them ⅓ each. The row-sum identity (`A.sum(axis=1)` = each instrument's unit
count, `A.sum()` = the unit rows) is asserted on the toy matrix, as are
`float32` and `weight == 1/m`. Blocking is asserted invisible (`--block-rows
1` against the default), the Slurm plumbing is driven through a fake `squeue`
in its three states (plus `--force` forwarded into the job and the previous
`.done` dropped), and the page is built with a stub reducer.

### What the first run measured, and why the rule changed

The first run of this matrix credited **+1 to each** instrument owning a
winning text, and produced `A.sum() = 1,639,503` for 408,804 unit rows — four
counts per row on average against a median of one. Ties were not the cause
(678 rows had any). It was the *owners* rule: a single winning column can be
owned by hundreds of instruments at once (the worst row: **847**), because
boilerplate — a transitorio, "Se deroga." — is one vector row shared across a
whole collection. `A` was therefore counting article–instrument *incidences*,
not articles, and the "strongest targets" of many instruments were simply
whoever owns the most boilerplate.

Hence the `1/m` rule above: a unit row with one unambiguous foreign
neighbour still adds 1, and a row answered by 847 instruments adds 1/847 to
each instead of 847 counts at once. The winner search, the tolerance, the
masking and everything on the page are unchanged — only the crediting is, so
the two maps differ by the rule alone. The first matrix was overwritten in
place (`--force`); `matrix.json`'s `weighting` says which rule produced what
is on disk.

### Measured, `geoint`, 2026-09-22 (the `1/m` run, job 42196)

The matrix, one job on `geoint1` (62 threads, `--exclude=geoint0`):

| phase | seconds |
|---|---|
| load (the join + `vectors.npy`) | 2.4 |
| normalise (381,349 rows, in place) | 0.8 |
| sweep (381,349 × 381,349 cosines, blocked) | 665.9 |
| write | 0.5 |
| **total** | **670.8 s (11.2 min)** |

Peak RSS **4.27 GB** of the ~245 GB a node has — a fraction of what the issue
budgeted, because a 1,024-row block and one normalised copy of the matrix is
all that is ever live. Inside the 5–30 minute estimate, and nowhere near the
two-hour ceiling; the same arithmetic as the +1 run, to within 3 seconds.

| what | value |
|---|---|
| unit rows answered | 408,804 |
| vector rows | 381,349 (10,309 owned by more than one instrument) |
| instruments | 1,523 |
| `A.sum()` | **408,804.0** — one unit row, one unit of weight |
| every row sums to that instrument's unit count | yes (`row_sums_equal_units`, worst deviation 4.9e-4) |
| non-zero cells | 864,966 of 2,319,529 (37 %) |
| unit rows with a tie | 678 (676 two-way, 2 three-to-five-way) |
| largest `m` | 847 |
| mean similarity of the winner | 0.843 |
| unit rows whose winner is an identical text (cosine 1) | 35,829 (8.8 %) |

The `m` histogram — how many instruments a unit row's answer is split over —
is what the change is about:

| `m` | 1 | 2 | 3–5 | 6–20 | 21–100 | 101+ |
|---|---|---|---|---|---|---|
| unit rows | 377,349 | 11,317 | 8,454 | 5,679 | 4,105 | 1,900 |

92 % of unit rows have a single answer and are credited exactly as before;
the 1,900 rows with `m ≥ 101` are the boilerplate ones that used to add up to
847 counts each, and now add one between them.

The four fits, on the login node, `random_state=0`:

| `n_neighbors` | 4 | 8 | 16 | 32 | total |
|---|---|---|---|---|---|
| seconds | 38.7 | 18.1 | 20.6 | 21.0 | **98.4** |

The page: **1.40 MB** for 1,523 instruments — three orders of magnitude under
the unit-level page, which is what one point per instrument instead of one
per unit row buys — with the footer present exactly once.
