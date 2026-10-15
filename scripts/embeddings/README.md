# `scripts/embeddings/` — a vector for every text of every federal instrument

Fase 2 of issue #218 (the implementation plan for #217's research), widened
to all three SCJN corpora by issue #227's Fase 3 and to a fourth, the
treaties, by issue #280: the Slurm build that turns
`md2akn.text_units()`'s output into one vector per distinct text of the
cached `scjn-leyes`, `scjn-reglamentos`, `scjn-lineamientos` and
`scjn-tratados` releases.
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

`--coleccion {leyes,reglamentos,lineamientos,tratados}` picks the corpus
(default `leyes`, so the #218 command keeps working), and each gets its own
work directory: `emb-run-leyes/`, `emb-run-reglamentos/`,
`emb-run-lineamientos/`, `emb-run-tratados/`.
Dedup stays inside a collection — measured at 0.9 % of the vectors saved by
deduplicating across them, against a shared file three independently
crawled, packaged and republished corpora would all have to be published
against (issue #227's decision 8).

`units.parquet` carries `coleccion` and `clave`: `clave` is a law's slug and
the `id_ordenamiento` of a reglamento, a lineamiento or a treaty, and it is what names
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
#    --no-split-articles (issue #256) turns rules 3/9 off for articles: one
#    article is one unit whatever its length, truncated at the model's own
#    window rather than split. Omit it to keep the default, cap-2000 shape
#    that reproduces a pre-#256 vector set byte for byte.
python scripts/embeddings/build_units.py --work-dir emb-run-leyes --no-split-articles
python scripts/embeddings/build_units.py --work-dir emb-run-reglamentos \
    --coleccion reglamentos --no-split-articles
python scripts/embeddings/build_units.py --work-dir emb-run-lineamientos \
    --coleccion lineamientos --no-split-articles
python scripts/embeddings/build_units.py --work-dir emb-run-tratados \
    --coleccion tratados --no-split-articles

# 2. Dedup by text_sha1, sort by estimated token length, balance into shards.
python scripts/embeddings/plan_shards.py --work-dir emb-run-leyes --num-shards 12

# 3. One model at a time: download it, submit every pending shard, wait, retry, delete it.
#    --max-batch-tokens (issue #256) replaces the fixed --batch-size as what
#    governs memory; --batch-size stays as the upper bound on a batch's row
#    count. See "encode_shard.py: window truncation and token-budget
#    batching" below, and "submit_jobs.py: why its wait is chunked too" for
#    --max-wait-minutes.
python scripts/embeddings/submit_jobs.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-0.6B --max-batch-tokens 20000
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

**Migrating an already-published collection to whole articles** (issue
#256's own run): build into a *new* work directory,
`emb-run-whole-<coleccion>/`, rather than the one that already holds the
published (split-article) vectors — `package_vectors.py`'s `PUBLICAR.md`
still targets the same release tags, replacing them in place, but nothing
here overwrites the old work directory while the new one is being built.

### `encode_shard.py`: window truncation and token-budget batching (issue #256)

An article is never split any more when `--no-split-articles` built the
corpus, so a handful of texts are longer than a model's own context window
(measured against the pre-#256 `units.parquet` files: 4 exceed the 0.6B's
32,768 tokens, 2 the 4B's 40,960). Those are **truncated at the model's own
`max_position_embeddings`**, read from its config rather than hard-coded —
never split, windowed or mean-pooled (the user's own decision) — and every
truncated text is recorded, by `text_sha1` and its real (pre-truncation)
token count, in that shard's own `.done` marker and carried into
`merge_shards.py`'s `manifest.json` as `texts_truncated` /
`max_tokens_embedded`.

The fixed `--batch-size` a shard's texts used to be embedded under (default
32, sorted by character length) is replaced by batches built in **token**
order under a padded-token budget, `--max-batch-tokens`: a batch's cost is
its own longest text's token count times its row count, since every row in
a batch pads out to the batch's longest sequence. `--batch-size` stays as
the upper bound on a batch's row count. A text whose own token count already
exceeds `--max-batch-tokens` is embedded alone, in a batch of one, rather
than blocking every shorter text from ever batching with it.

### `submit_jobs.py`: why its wait is chunked too (issue #256)

Same reason, same shape as `submit_umap.py`'s own chunked wait (below):
`submit_jobs.py --model ... ` used to block on `squeue` with no ceiling,
which an automated session driving this cannot hold. `--max-wait-minutes N`
returns exit status 75 ("still running") while shard jobs remain queued or
running, and the caller simply calls the same command again — the state
(the attempt's own job ids) lives in `runs/<model-slug>/jobs.json`, not in a
process that has to stay alive. `0` (the default) blocks until every
pending shard finishes, the pre-#256 behaviour. `--report` prints the
pending-shard count offline, no Slurm at all. Unlike `submit_umap.py`
there is no `--max-wait-hours` ceiling or `scancel` here: a shard job that
does not finish is handled by `submit_jobs.py`'s own `--max-attempts` retry
loop, resumed the same chunked way on the next call.

**`submit_jobs.py --mem` (default `32G`) is not optional in practice**, even
though `submit.sh` itself carries no `#SBATCH --mem`. Found running #256's
own GPU phase: with another user's job holding two of `cemieredes`' three
A100s (and part of the node's ~1 TB of memory), a shard job submitted with
no `--mem` sat `PENDING (Resources)` indefinitely even once a GPU freed up
— on this cluster, "no `--mem` given" resolves to reserving the *entire*
node's memory for the job (`DefMemPerNode=UNLIMITED` at the partition
level apparently means "no accounting", not "use what's free"), so the job
was waiting for the other one to vacate the whole node, not just a GPU.
`--mem=32G` (comfortably more than one shard needs) fixed it immediately.

Incremental re-embedding after a reform (or after a rule change, issue #280):
replan against what a model already has, then resubmit — only the new units
reach the GPU at all.

```bash
python scripts/embeddings/build_units.py --work-dir emb-run-leyes
python scripts/embeddings/plan_shards.py --work-dir emb-run-leyes \
    --against emb-run-leyes/runs/qwen3-0.6b
python scripts/embeddings/submit_jobs.py --work-dir emb-run-leyes \
    --model Qwen/Qwen3-Embedding-0.6B
```

The replan numbers its new shards **after** the highest `shard-NNNN.{done,
parquet,failed}` index already under `--against` or under the work
directory's own `runs/*/` (`first_shard_index` in `shards.json` records where
it started). Before issue #280 it numbered from 0, `submit_jobs.pending_shards`
found `shard-0000.done` already there and called the new shard finished, and
`merge_shards.py` would have published the old vectors without a word. One
plan serves every model whose run is complete: `excluded_already_embedded`
must agree between the models' runs (`already_embedded()` over each) before
submitting — issue #280 checked 100,852 against both.

For a rule change that touches one instrument, **copy the published work
directory first** (`cp -a emb-run-whole-reglamentos emb-run-whole-reglamentos-r10`,
drop `shards.json`, `runs/*/manifest.json` and `publish/`), rebuild
`units.parquet` into the copy, and replan with `--against` — never touch the
directory that holds the published vectors (#256's rule). The new shard's
vectors are merged with the old shards' (a hash no longer in `units.parquet`
is simply never selected), so every per-instrument file comes out again and
is compared against the published one with `pyarrow.Table.equals`.

## The shared cluster venv had drifted (found running issue #256)

`/home/mgraffg/.venvs/cluster` (`submit.sh`'s hardcoded interpreter, shared
with `../Chimalli-overleaf`) had `torch==2.14.0+cu130` — a build needing a
newer NVIDIA driver than `cemieredes` actually has (525.116.04, CUDA 12.0).
`torch.cuda.is_available()` silently returned `False` with no error, so
`encode_shard.py`'s `device_map="auto"` fell back to CPU without complaint
(`"Device set to use cpu"` in the Slurm log is the only sign). Fixed by
reinstalling a driver-compatible build:

```bash
uv pip install --python /home/mgraffg/.venvs/cluster/bin/python \
    "torch==2.4.1" torchvision==0.19.1 --index-url https://download.pytorch.org/whl/cu121
uv pip install --python /home/mgraffg/.venvs/cluster/bin/python accelerate
```

(`accelerate` was also missing — newer `transformers` needs it for
`device_map="auto"`, which #227's original run predates.) Check
`torch.cuda.is_available()` returns `True` under `srun --gres=gpu:1` before
trusting a "successful" shard: a CPU fallback still writes a `.done` marker,
just very slowly, and would otherwise go unnoticed until someone asks why
a 20-text shard took hours.

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
  cast to `float16` — no normalization, no quantization.
- A failing shard writes `shard-XXXX.failed` and does not fail the run;
  `status.py` says what to relaunch.
- Since issue #256, a text over the model's own context window is
  truncated at `max_position_embeddings` (read from the model's config) and
  recorded, never split/windowed/mean-pooled; batches are built under a
  padded-token budget (`--max-batch-tokens`) instead of a fixed row count,
  because `--no-split-articles` corpus texts vary far more in length than
  the pre-#256, cap-2000 shape did.

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

(`submit_jobs.py` gained the same chunked wait for the same reason in issue
#256 — see "submit_jobs.py: why its wait is chunked too" above.)

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

**The similarity control (issues #262, #268).** Beside *Neighbourhood size*, the
toolbar has a second `.atlas-segmented` radiogroup, *Similarity* (titled
*Embedding model* until issue #268, when BM25 joined it), with `0.6B` (the
default), `4B` and `BM25`, and a caption saying the method is what measured how
similar two texts are (the 0.6B is the default), so both the relations the panel
lists and the layout change (the neighbourhood size changes the layout only). Both controls are
built by one `radiogroup()` helper in `atlas.js` — same markup, `aria-checked`,
roving `tabindex` and arrow keys — and sit side by side in `.atlas-controls`
at desktop width and stacked at 390 px.

* **The mount names the models.** `data-src`/`data-pairs` stay the default
  model's paths; one more attribute, `data-models`, is a JSON list of
  `{label, src, pairs}` (`0.6B` → `atlas/atlas.json` + `atlas/pairs/`, `4B` →
  `atlas/atlas-qwen3-4b.json` + `atlas/pairs-qwen3-4b/`, `BM25` →
  `atlas/atlas-bm25.json` + `atlas/pairs-bm25/`), relative like the
  rest, whose first entry must agree with `data-src`/`data-pairs` (a test checks
  it). A mount without it — or with a malformed one — builds the page with no
  model control at all, which is how the toy site's older tests run.
* **Load on switch.** The page opens on the 0.6B and fetches nothing else. The
  first switch to the 4B fetches its file once (a failed fetch is not
  remembered: choosing it again retries, and the status line under the control
  says "The 4B map could not be loaded. Choose it again to retry."). The file is
  refused — the status line says "The 4B map cannot be used: it lists different
  instruments." or "…has no such layout.", and the radio stays on the model on
  screen — unless its `instruments` have the same `k` at every position and its
  `projections` has the current neighbourhood size.
* **What a switch does.** `in`/`out`/`inc`/`p` are swapped into the instruments
  the page already holds, every point animates to the new model's layout at the
  *current* neighbourhood size (the same 700 ms transition as a size change,
  one `moveTo()` serves both), the selection, its ring, its numbered lines and
  the panel are redrawn for the new relations, the explanation dialog's pair
  directory becomes the model's `pairs`, and the "Map data generated on" date
  shows the model's `meta.generated`. The explanations cache is keyed
  `<model>:<i>-<j>`, and a dialog request still on its way when the model
  changes is dropped the way a closed dialog's is. No URL parameter and no
  `localStorage`: neither control persists.
* **A score is not a similarity (issue #268).** The explanation dialog's column
  reads **Score** with one decimal when the data set on screen says
  `meta.method` is `"bm25"` — a raw BM25 score is unbounded, three decimals on a
  number in the hundreds is noise — and **Similarity** with three decimals
  otherwise (a file with no `method`, like the two embedding files committed
  before #267, is an embedding). It is the file's `meta.method` that decides,
  never the option's label, and `data-column` follows for the phone layout; the
  rest of a switch (fetch once, the refusal rules, the status messages naming
  the option, the dialog directory) is #262's, unchanged with three entries.
* **Tests.** The harness tests count requests with Playwright's
  `page.on("request")` (only `atlas.json` on load, the 4B file once however many
  switches), read Chapingo's list for each model from the two JSON files, follow
  the layouts by the correlation of circle positions with `projections[k]`,
  route the 4B file through edited copies (swapped `k`, a missing layout, a
  500) for the refusal and retry paths, and open a real pair under each model
  when both `pairs/` and `pairs-qwen3-4b/` are installed. The toy site gets a
  second model — the same instruments, a reversed relation for one, a mirrored
  layout, its own pair directory — for the dialog's per-model directory, the
  dropped stale answer and a differing table; the rendered-page tests check the
  control after Quarto's CSS and the phone-width stacking.

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

## The instrument map (issues #242, #259)

The UMAP explorer above is a picture of **texts**. This is a picture of the
2,676 unique **instruments** that own them (1,303 until issue #283 put the 1,373
treaties on the map), built from one question asked of every unit of every
federal law, reglamento, lineamiento and treaty:

> which instrument owns the text closest to this one, among all the texts
> that are not exclusively mine?

Weighing the answers gives a square matrix `A` (2,676 × 2,676, rows and
columns in `instruments.parquet`'s `i` order): every *counted* unit row of
instrument `I` hands out a total weight of **1**, `A[I, J] += 1/m` to each of
the `m` instruments owning a winning text. Headings and transitorios
are not compared at all (issue #264), and a word-for-word match shared by
several instruments is searched but not counted (see the rules below). An instrument is then represented by
**where its articles' nearest foreign neighbours live** — a distribution over
the other instruments — rather than by its own text, and two instruments land
together when their articles point at the same places.

The vectors are the whole-article ones (`split_articles: false`, `md2akn`
0.4.0, issue #256) that `legalvec` reads back from the three
`scjn-*-vectors` releases.

Since issue #282 the chain (`prepare_umap_input.COLLECTIONS`,
`build_umap_html.COLLECTION_CODES`, `legalvec.cache.RELEASE_TAGS` — a test pins
that the three agree, in order) and the page's script (`atlas.js`, whose legend
is built from `meta.collections`) carry a fourth collection, `tratados`, stacked
last and coloured `#eda100` on the page. Treaties are not grouped by name
(`unique_instruments.GROUPED_COLLECTIONS` is unchanged: none of the 1,373 names
repeats), and `export_atlas_pairs.py` refuses an instrument whose collection the
registry does not know instead of dropping it. Issue #283 rebuilt the three data
sets with them ("Measured, with the treaties, 2026-10-12", below); the sections
dated before that describe the three-corpus map and are kept as history.

### Where things are

| What | Path |
|---|---|
| The input, read once (issues #241, #259) | `scripts/embeddings/prepare_umap_input.py --unique-names` → `emb-run-atlas/` (`vectors.npy`, `vector_ids.parquet`, `instruments.parquet`, `centroid_input.npy`, `input.json`, `unique-instruments.json`, `prepare.done`) |
| Which instruments are unique | `scripts/embeddings/unique_instruments.py` |
| The matrix (a Slurm job) | `scripts/embeddings/instrument_matrix.py` |
| The research page | `scripts/embeddings/build_instrument_umap_html.py` → `output/umap-instruments-qwen3-0.6b.html` (+ `.vl.json`) |
| The website's data (issue #244) | `scripts/embeddings/export_atlas_data.py` → `website/pages/atlas/atlas.json` (committed) |
| The pair explanations (issue #249) | `scripts/embeddings/export_atlas_pairs.py` → `emb-run-atlas/atlas-pairs/` (`pairs/`, `manifest.json`, `atlas-pairs.tar.gz`, `SHA256SUMS.txt`, `PUBLICAR.md`, `.done`), installed into `website/pages/atlas/pairs/` (gitignored); published by hand as the release `atlas-pairs`, body `.github/atlas-pairs.md` |
| The 4B data set (issue #261) | the same chain over `emb-run-atlas-4b/` → `website/pages/atlas/atlas-qwen3-4b.json` (committed), pair files in `website/pages/atlas/pairs-qwen3-4b/` (gitignored), assets `atlas-pairs-qwen3-4b.tar.gz` / `manifest-qwen3-4b.json` / `SHA256SUMS-qwen3-4b.txt` |
| The BM25 data set (issue #267) | `scripts/embeddings/prepare_bm25_input.py` → `emb-run-atlas-bm25/` (no vectors; `bm25-index/`, `tokens.parquet`), then the same chain → `website/pages/atlas/atlas-bm25.json` (committed), pair files in `website/pages/atlas/pairs-bm25/` (gitignored), assets `atlas-pairs-bm25.tar.gz` / `manifest-bm25.json` / `SHA256SUMS-bm25.txt`; the scorer is `scripts/embeddings/scoring.py` |
| The strong-link gold and the three-way evaluation (issue #272) | `scripts/embeddings/gold_links.py` → `emb-run-atlas/gold-links/` (`gold.json`, `gold.md`), then `scripts/embeddings/evaluate_links.py` → `emb-run-atlas/gold-links/` (`evaluation.json`, `evaluation.md`); gitignored, nothing uploaded |
| The row-level evaluation against citations (issue #273) | `scripts/embeddings/gold_links.py` also writes `emb-run-atlas/gold-links/citations.parquet`, then `scripts/embeddings/evaluate_citations.py` → `emb-run-atlas/gold-links/` (`evaluation-rows.json`, `evaluation-rows.md`); gitignored, nothing uploaded |
| The topics of the instrument map (issue #285; the 4B since review fix-1) | `scripts/embeddings/cluster_instruments.py --work-dir emb-run-atlas-4b` → `emb-run-atlas-4b/topics/` (`labels.parquet`, `comparison.json`, `comparison.md`, `topics.json`, `.done`); gitignored, nothing uploaded. The 0.6B's own `emb-run-atlas/topics/` stays where #285 wrote it |
| The Topics page (issue #286; a draft, not published) | `scripts/embeddings/export_topics_page.py` → `website/pages/topics/topics.json` and `_topics.md` (both committed), included by `website/pages/topics.qmd` (`draft: true`) |
| Everything derived | `emb-run-atlas/instrument-matrix/` (`matrix.npy`, `nearest.parquet`, `matrix.json`, `umap.parquet`, `umap.json`, `job.json`, `slurm-*.out`, `.done`) |

Nothing here is committed except `atlas.json`. Every command takes
`--work-dir emb-run-atlas` explicitly: the scripts' own default stays
`emb-run-umap/`, the unit-level explorer's work directory, which this chain
never touches.

```bash
uv run --group viz python scripts/embeddings/prepare_umap_input.py \
    --work-dir emb-run-atlas --unique-names
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas --dry-run
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas --submit
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas \
    --wait --max-wait-minutes 9
uv run --group viz python scripts/embeddings/build_instrument_umap_html.py --work-dir emb-run-atlas
uv run --group viz python scripts/embeddings/export_atlas_data.py --work-dir emb-run-atlas
uv run --group viz python scripts/embeddings/export_atlas_pairs.py --work-dir emb-run-atlas \
    --atlas website/pages/atlas/atlas.json --install website/pages/atlas/pairs
```

Before `prepare_umap_input.py`, check that the three cached
`~/.cache/legalvec/scjn-*-vectors/corpus-manifest.json` say `"split_articles":
false`, and refresh with `uv run legalvec download --collection all` if not.
The unique-name selection also reads the SCJN corpus for the two id-keyed
collections' snapshot dates; a missing tarball raises `scjn.AssetNotCached`,
whose message names the `scjn download --coleccion ...` command that fixes it.

### A second model: the 4B (issue #261)

The same chain, over the **Qwen/Qwen3-Embedding-4B** vectors (K = 2560,
`legalvec.model_slug` → `qwen3-4b`) that the three `scjn-*-vectors` releases
carry beside the 0.6B, so the page can offer the comparison between instruments
under either model. A second work directory, never `emb-run-atlas/` with a flag:
the 0.6B outputs stay untouched and reproducible, and `--work-dir` is already
the switch. Both models' vectors must be in the `legalvec` cache
(`uv run legalvec status`).

```bash
uv run --group viz python scripts/embeddings/prepare_umap_input.py \
    --work-dir emb-run-atlas-4b --unique-names --model Qwen/Qwen3-Embedding-4B
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas-4b --submit
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas-4b \
    --wait --max-wait-minutes 9
uv run --group viz python scripts/embeddings/build_instrument_umap_html.py \
    --work-dir emb-run-atlas-4b --output output/umap-instruments-qwen3-4b.html
uv run --group viz python scripts/embeddings/export_atlas_data.py --work-dir emb-run-atlas-4b \
    --output website/pages/atlas/atlas-qwen3-4b.json \
    --instruments-as website/pages/atlas/atlas.json
uv run --group viz python scripts/embeddings/export_atlas_pairs.py --work-dir emb-run-atlas-4b \
    --atlas website/pages/atlas/atlas-qwen3-4b.json --install website/pages/atlas/pairs-qwen3-4b
```

* **`--instruments-as`** makes `export_atlas_data.py` refuse, with a `SystemExit`
  naming the first differing position, an export whose `c`/`k`/`n`/`p` differ from
  an existing `atlas.json` — or whose instrument count does. The units and the
  unique-name selection do not depend on the model, so the two files must list the
  same instruments in the same positions (the page swaps them keeping its
  selection); `instruments.parquet` and `vector_ids.parquet` of the two work
  directories are byte-comparable (`pyarrow`'s `Table.equals`), and were checked.
  Only `c`/`k`/`n`/`p` must agree: the relations (`in`, `out`, `inc`) and the
  layouts are what a model changes.
* **Asset names carry the model.** `export_atlas_pairs.py` reads the model from
  `input.json`. The default model (the 0.6B) keeps `atlas-pairs.tar.gz`,
  `manifest.json` and `SHA256SUMS.txt`, so nothing already published moves; any
  other model writes `atlas-pairs-<slug>.tar.gz`, `manifest-<slug>.json` and
  `SHA256SUMS-<slug>.txt` (a second `SHA256SUMS.txt` uploaded with `--clobber`
  would overwrite the first), to be **added** to the same `atlas-pairs` release.
  Inside the tarball the layout is `pairs/` + `manifest.json` either way, so the
  publish workflow unpacks every model the same way; the manifest gains `model`,
  `model_slug`, `asset`, `manifest` and `sums`.
* **`meta` has no new key.** `meta.model` already names the model; the page names
  each file on its mount.
* **Never regenerated here:** `atlas.json`, `emb-run-atlas/` and the published
  `atlas-pairs.tar.gz`. `PUBLICAR.md` (in `emb-run-atlas-4b/atlas-pairs/`) is the
  only thing to run, by hand, before the pull request is merged: the workflow
  fetches both asset sets and fails when either is missing.

### A lexical baseline: BM25 (issue #267)

A third data set over **exactly the same comparison**: same unit rows, same
exclusions (headings and transitorios), same masking of a source's exclusively
owned texts, same ties, same `1/m` weighting, same identical-shared rule, same
UMAP of the matrix rows, same exports, same `atlas-pairs` release. Only *how two
texts are scored* changes: a classical bag-of-words ranking whose vocabulary and
inverse document frequencies come from the compared texts themselves, nothing
external. It is a baseline to read the embeddings against, not a claim about
which is better (that evaluation is issue #217's).

```bash
uv run --group viz python scripts/embeddings/prepare_bm25_input.py \
    --work-dir emb-run-atlas-bm25 --from emb-run-atlas
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas-bm25 --submit
uv run --group viz python scripts/embeddings/instrument_matrix.py --work-dir emb-run-atlas-bm25 \
    --wait --max-wait-minutes 9
uv run --group viz python scripts/embeddings/build_instrument_umap_html.py \
    --work-dir emb-run-atlas-bm25 --output output/umap-instruments-bm25.html
uv run --group viz python scripts/embeddings/export_atlas_data.py --work-dir emb-run-atlas-bm25 \
    --output website/pages/atlas/atlas-bm25.json --instruments-as website/pages/atlas/atlas.json
uv run --group viz python scripts/embeddings/export_atlas_pairs.py --work-dir emb-run-atlas-bm25 \
    --atlas website/pages/atlas/atlas-bm25.json --install website/pages/atlas/pairs-bm25
```

`bm25s` and `scipy` are in the root `viz` group (not `dev`), and the Slurm job
runs this repository's own `.venv`, so `uv sync --group viz` must have run
before `--submit`.

* **A pluggable scorer.** `scoring.py` is the one place a text-to-text score is
  computed. `scorer_for(work_dir)` reads `input.json`'s `method` (`dense` when
  the key is absent, so `emb-run-atlas/` and `emb-run-atlas-4b/` keep working
  untouched) and returns a `DenseScorer` (the cosine code that used to be inline,
  moved) or a `Bm25Scorer`; both answer `scores(rows)` (a `float32` block, row
  `r` the score of every text against source `rows[r]`) and
  `scores_against(rows, candidates)` (the pair export's per-pair recomputation).
  `instrument_matrix.py` and `export_atlas_pairs.py` call it and nothing else; the
  dense output is **byte-for-byte** the one it was (checked on the real 0.6B
  directory in a scratch copy: `matrix.npy`, every column of `nearest.parquet`
  and all 6,288 pair files equal; the toy-corpus suite is unchanged).
* **The tokeniser** is `bm25s.tokenize(lower=True, stopwords=None, stemmer=None)`
  with its default Unicode-aware pattern `(?u)\b\w\w+\b`: lowercase words of
  two or more characters and nothing else. Accents are kept, the Markdown `**`
  markers fall out of the pattern, and a one-character word or a single digit is
  not a token. No stopwords (the IDF makes `de`/`la`/`el` nearly weightless) and
  no stemming (`PyStemmer` is deliberately not installed).
* **The index is the candidate set.** One document per vector row carried by at
  least one *searched* unit (`instrument_matrix.searched_mask`), so a heading or a
  transitorio shapes neither the vocabulary nor the IDF: 110,185 documents, of
  145,788 vector rows. `bm25s`' defaults — `method="lucene"`, `k1=1.5`, `b=0.75`
  — are recorded in `input.json` and `matrix.json` so a later sweep changes them
  knowingly. The **query side is binary over distinct terms** (Lucene's and
  `bm25s`' own convention): a term repeated in a text counts once. A test pins
  the sparse product against `bm25s.BM25.get_scores` row by row.
* **`prepare_bm25_input.py`** copies `vector_ids.parquet`, `instruments.parquet`
  and `unique-instruments.json` verbatim from `--from` (so a vector `row` and an
  instrument `i` mean the same thing in all three work directories and
  `--instruments-as` holds by construction), reads the texts through
  `build_umap_html.load_frames`, indexes them, and writes `bm25-index/`
  (`bm25s`' own files), `tokens.parquet` (each document's vector `row` and token
  ids, so the query side is rebuilt without tokenising again) and `input.json`
  with `method: "bm25"`, `model: "bm25"`, `model_slug: "bm25"`, `k: null`. It
  writes **no `vectors.npy` and no `centroid_input.npy`**: nothing BM25 produces
  is a vector per text. It refuses a source without `prepare.done` or without
  `unique_names: true`.
* **Ties are relative, identity is `text_sha1`.** BM25 scores are unbounded, so
  an absolute 1e-6 means nothing on them: a tie is `score >= best * (1 - 1e-6)`
  (`scoring.tie_mask`), and a word-for-word match is a winner carrying the
  source's own `text_sha1` (in any collection) — what a cosine of 1 means for the
  embeddings. Identical texts tie *exactly* under either scorer: the same token
  multiset gives identical columns of the sparse score matrix, and the product
  accumulates a row's terms in one order for every document. The dense path keeps
  its absolute tolerance and its `>= 1 - tolerance` identity.
* **A row can have no foreign match.** A lexical score of 0 against everything
  would otherwise tie every column, so a source whose best foreign score is not
  positive (no term, or every term lies only in texts the source owns alone) has
  no winner: `nearest.parquet` records it with `m` 0, `weight` 0 and
  `drop_reason == "no_match"`, it is not counted, and `matrix.json` reports
  `no_match_rows`. The 112,077 searched rows had none; the dense path still
  raises when a row finds no target.
* **`similarity` keeps its name and holds the raw BM25 score** (one number per
  row, whatever the scale; the pair files round it to four decimals). There is no
  normalisation: a shorter document can out-score the query's own self-score, so
  a self-score ratio does not stay in [0, 1].
* **`meta` and the footer say which method.** `atlas-bm25.json`'s `meta.method`
  is `"bm25"` and `meta.model` `"bm25"`, with the index parameters, tokeniser,
  vocabulary size and documents indexed; `matrix.json` and the pair manifest
  carry `method` and `tolerance_relative`; the research page's footer names the
  method. `export_atlas_data.py` refuses a `matrix.json` computed by another
  method than `input.json` records. The two committed embedding files predate
  `meta.method` and are not regenerated: a reader treats a missing `method` as
  `"dense"`.
* **Asset names** follow the per-model rule with no special case:
  `legalvec.model_slug("bm25")` is `"bm25"`, so the three added assets are
  `atlas-pairs-bm25.tar.gz` / `manifest-bm25.json` / `SHA256SUMS-bm25.txt`
  (`website.yml` fetches and verifies them, and fails when they are missing).
  `emb-run-atlas-bm25/atlas-pairs/PUBLICAR.md` is the only thing to run, by
  hand, before the pull request is merged.

* **The defaults were tuned against, and kept (issue #267's review fix).** The
  index parameters (`lucene`, `k1` 1.5, `b` 0.75, binary queries) were bm25s'
  own and had never been measured. `tune_bm25.py` searches 300 configurations
  for the one whose per-row winners agree most with the 4B's -- the
  "Measured, BM25 tuning" block below has the table and the decision: the best
  configuration lifts the closest-instrument agreement with the 4B by 11 of
  1,303 instruments, short of the 26 (2 percentage points) that would have
  justified regenerating the data set, so nothing was.

```bash
# The grid on a 10 % sample, two Slurm jobs (geoint), then the top 3 + the defaults in full.
uv run --group viz python scripts/embeddings/tune_bm25.py --dry-run          # the grid, nothing computed
uv run --group viz python scripts/embeddings/tune_bm25.py --submit
uv run --group viz python scripts/embeddings/tune_bm25.py --wait --max-wait-minutes 9   # repeat while it exits 75
uv run --group viz python scripts/embeddings/tune_bm25.py --stage confirm --submit
uv run --group viz python scripts/embeddings/tune_bm25.py --stage confirm --wait --max-wait-minutes 9
uv run --group viz python scripts/embeddings/tune_bm25.py --report            # offline
```

Everything lands under `emb-run-atlas-bm25/tune/` (gitignored): `sample.parquet`,
`grid/<configuration>.json`, `full/<configuration>/` (a normal work directory
each), `results.parquet` and `results.json`. To adopt a winner, pass its values
to `prepare_bm25_input.py` (`--method --k1 --b --weighting`) or change that
script's `METHOD`/`K1`/`B`/`QUERY` constants, re-prepare and rerun the chain;
`Bm25Scorer` reads them back from `input.json`.

### Unique instruments (issue #259)

The SCJN does not reform a reglamento or a lineamiento into a new version: it
reissues it under a new `idOrdenamiento` and keeps the old one. So the
corpora hold several instruments with the same name, and drawing each would
count the same regulation several times. The Atlas compares **unique** laws,
regulations and guidelines, and `unique_instruments.py` is the rule:

- **Only `reglamentos` and `lineamientos` are grouped.** Laws are keyed by
  `abrev`, a curated key, and pass through untouched (`lamn` and `lamni` both
  read "LEY DE AMNISTÍA" and both stay). Names are never compared across
  collections.
- **A name is folded** — NFKD, combining marks dropped, whitespace collapsed,
  casefolded — and nothing looser: no fuzzy matching, and punctuation is not
  folded. That is what pairs `REGLAMENTO  DEL CONCURSO PROGOL…` (a double
  space) with its reissue.
- **The newest original publication date wins**: the date of the instrument's
  own *oldest* snapshot, read from the SCJN corpus and parsed (`fecha_publicacion`
  is `DD-MM-YYYY`, never compared as a string); a tie goes to the larger
  integer `id_ordenamiento`. It agrees with the SCJN's own `vigencia ==
  "VIGENTE"` in all 104 groups that have exactly one VIGENTE member, and has no
  ties today. The newest *latest*-snapshot date is not usable: an abrogated
  instrument's last snapshot can carry the date of the decree that replaced it,
  so it ties often.
- **Only instruments that have vectors compete**, so a text-less instrument
  can never win a group and erase one the Atlas can draw.

| collection | same-name groups | dropped | kept |
|---|---|---|---|
| leyes | (not grouped) | 0 | 315 |
| reglamentos | 138 | 219 | 863 |
| lineamientos | 1 | 1 | 125 |
| tratados | (not grouped: no name repeats) | 0 | 1,373 |
| **total** | | 220 | **2,676** (1,303 before issue #283) |

Four kept pairs of regulations still differ only by punctuation (a final
period, or a comma) — `159474`/`178861`, `52327`/`107302`, `69462`/`108392` and
`81471`/`108459` — since the rule folds accents, whitespace and case only.

`prepare_umap_input.py --unique-names` applies it **before the vector matrix is
stacked**, so a dropped instrument has no row in `instruments.parquet`, no unit
row in the join, and no place in `matrix.npy`. The vector rows are restricted
to texts a *kept* instrument carries too: `legalvec.load_vectors` always unions
in the collection's whole shared file, which also holds texts only a dropped
instrument owns, and those never become a row (so they can never be a
candidate). `unique-instruments.json` records, per collection, the
before/kept/dropped counts and every dropped instrument with its name, first
publication date and the `clave` that replaced it; `input.json` records
`"unique_names": true`. `instrument_matrix.py` asserts that the joined unit rows
equal the sum of `instruments.parquet`'s `units`. Without the flag nothing
changes, which keeps the unit-level explorer's inputs reproducible.

`export_atlas_data.py` and `export_atlas_pairs.py` refuse (`SystemExit`) a work
directory whose `input.json` does not record `unique_names: true`, or whose
`instruments.parquet` still lists an instrument `unique-instruments.json` says
was dropped, so the website cannot be fed a map that counts a reissued
regulation several times.

### The rules that define `A`

Each of these was a decision in issue #242, not a default:

- **Headings and transitorios are out of the comparison entirely**
  (`searched_mask`, which `instrument_matrix.py` and `export_atlas_pairs.py`
  both use, so they cannot disagree about which rows were searched). A unit with
  `unit_type == "heading"` (`EXCLUDED_UNIT_TYPES`), or inside a transitorios
  section, is neither a source row nor a candidate and owns no vector row. The
  transitorios section is Akoma Ntoso's own label — any element of the unit's
  `path` equal to `TRANSITORIOS` or starting with `TRANSITORIOS `
  (`is_transitorio_path`; `md2akn.units._container_label` emits that label
  *only* for a section marked `refersTo="#transitorios"`, so `units.parquet`
  needs no `refers_to` column). `md2akn` marks every heading, and the
  reform-date ones (`**D.O.F. 14 DE ENERO DE 1985.**`) match another
  instrument's identical heading; transitorios repeat the standard wording of a
  decree ("El presente Decreto entrará en vigor al día siguiente…") across
  dozens of instruments. Neither says anything about how two instruments
  relate. A text owned by a heading or a transitorio *and* by an article stays a
  candidate, owned only by the article's instrument; a text only headings and
  transitorios own is never a winner. Headings are counted first, so a heading
  inside a transitorios section is one excluded row, and `searched_rows +
  heading_rows_excluded + transitorio_rows_excluded == unit_rows`. Every other
  `unit_type` counts, and there is no `--unit-types` flag. This replaced issue
  #259's narrower rule (a transitorio whose best match was a near-copy was
  searched, but not counted), which is gone — the 2026-09-29/30 measurements
  below were made under it.
- **Ties count, every one of them.** A row's winners are every column within
  `--tolerance` (1e-6) of its best. Identical texts across collections are
  *exact* ties in float32, and breaking them by column index would silently
  prefer `leyes` to everything else.
- **`1/m` to each of the `m` instruments owning a winning text.** A text two
  instruments share is evidence about both, so both are credited — but a unit
  row is one article and weighs one, however many instruments answer for it.
  Without it a single boilerplate winner ("Se deroga.") owned by hundreds of instruments would credit hundreds of cells
  at once, and `A` would count article–instrument incidences rather than
  articles.
- **A word-for-word match shared by several instruments is not counted.** A
  row whose best similarity is `>= 1 - tolerance` (1e-6) *and* whose `m > 1`
  is boilerplate owned by many instruments — it cannot tell one pair apart
  from any other and says nothing distinctive about its source. It adds
  nothing to `A`, and it is **not** re-credited to its next-nearest text.
  `nearest.parquet` keeps the row, with `counted` false. An identical winner
  owned by exactly one other instrument (`m == 1`) still counts a whole 1, as
  does a non-identical winner with `m > 1` (a tie within the tolerance).
- **The identity.** Every row of `A` sums to that instrument's **counted**
  unit rows (searched rows minus the ones the rule above drops), and
  `A.sum()` equals the counted rows; `matrix.json` records this as
  `row_sums_equal_counted_rows`, next to `unit_rows`, `heading_rows_excluded`,
  `transitorio_rows_excluded`, `excluded_transitorios`, `searched_rows`,
  `identical_shared_dropped` and `counted_rows`. An
  instrument's `p` stays its total excerpts and its `pc` (circle size, issue #271) is the row sum, its counted excerpts.
- **Per unit row, not per distinct text.** A boilerplate article repeated
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
  instrument. Blocking changes no count, which a test asserts.
- **Directed, never symmetrised.** A reglamento pointing at its law says
  nothing about the law pointing back. The other direction is not thrown
  away: the research page's tooltip carries both the row sum (`units pointing
  out`) and the column sum (`foreign units pointing here`).

The unit → instrument → vector row join is **`build_umap_html.load_frames`
itself**, imported and called with no projection, rather than a second copy:
the two scripts must never disagree about which vector row a unit got.

`nearest.parquet` keeps the evidence, one row per *searched* unit row (every
row but the headings and the transitorios): `similarity`, `n_winners` (how many vector rows tied),
`targets` (the instruments answering), `m` (how many of them), `weight`
(`1/m`) and `counted` (whether the row entered `A`), next to `clave`,
`unit_type` and `eId`, and a `drop_reason` (null or `identical_shared`).
`n_winners` and `m` are different numbers: one winning row can have several
owners, and two tied rows can share one.

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
run being replaced for the one it is waiting on. The job runs this checkout's
own `.venv` (shared `/home`), so it executes whatever branch is checked out.

### The research page

`build_instrument_umap_html.py` runs on the login node — 1,303 × 1,303 is
seconds of work, so there is no job to submit. Rows are L2-normalised (a
zero row raises rather than reaching UMAP as `nan`), then fitted four times
with `n_neighbors` 4 / 8 / 16 / 32, `metric="cosine"`, `min_dist=0.1` and
**`random_state=0`** — the opposite of `project_umap.py`'s decision, for the
opposite reason: a seed costs a single-threaded fit, which at this size is
seconds, and buys a reproducible page. Each projection is min-max scaled into
`[0, 1]` with its raw range recorded, so the radio switches between them
without rescaling an axis.

One layered scatter: colour by collection (legend-bound toggle), size by
`units` on a **log** scale, a radio for `n_neighbors` defaulting to 16, and a
click that puts a black ring and the instrument's name on the picked point
and red rings on the five instruments its units point at hardest (the
comma-separated-string trick #241 validated, read back with Vega's `split`).
`nearest` is **off**, for the reason #241 measured. The tooltip carries
`nombre`, `clave`, `coleccion`, `units`, both directions of the matrix and
the five strongest targets — all weights to **one decimal**, since a weight
is a sum of fractions. `units pointing out` is the instrument's counted unit rows, at most its
`units`, and the column sum (`foreign units pointing here`) is the one that
varies. The same provenance
footer and `usermeta.provenance` as #241's page, through the same helpers.

**The five targets are five tooltip rows, and they are the five red rings.**
Each is its own field (`top1`..`top5`, titled `target 1`..`target 5`, after
`foreign units pointing here`), written **weight first**, two spaces, then
the name — vega-tooltip's default style clips a value cell at **300px × 7em**,
so one row per target is what fits without custom tooltip CSS. An instrument
with fewer than five non-zero targets fills the remaining rows with an em
dash `—`, not a null: vega-tooltip prints a null as the word `null` and skips
only `undefined`. Both the rows and the `t` string the red-ring filter reads
come from one helper, `strongest_targets` (weight > 0 only, heaviest first,
ties by ascending `i`, at most `TOP_TARGETS = 5`), so the names and the rings
cannot disagree and an instrument that receives no weight is never ringed.

**A person still confirms the interaction in a browser.** What the script
verifies is the spec: `chart.to_dict()` against the Vega-Lite schema, the
written `.vl.json` reloaded, and the tests below.

### The atlas export

The website's Atlas cannot read parquet or npy, so `export_atlas_data.py`
(issue #244) writes one compact JSON into the website's own tree,
**`website/pages/atlas/atlas.json`** — the one derived file committed for the
page, like `website/pages/data/scjn-leyes-summary.json`:

```bash
uv run --group viz python scripts/embeddings/export_atlas_data.py --work-dir emb-run-atlas
```

It is a pure read of `instruments.parquet`, `matrix.npy`, `matrix.json`,
`umap.parquet` and `umap.json`: `project()` is called only so a missing
matrix fails with a `SystemExit` naming `instrument_matrix.py`, and is a
no-op because `umap.parquet` exists (there is no `--force`). Seconds, no
Slurm, no network. Flags: `--work-dir`, `--output`, `--n-neighbors`
(default `4,8,16,32`, each already fitted), `--top` (5), `--decimals` (4,
coordinates). Run by a human and committed, never by a workflow (issue #115,
Hallazgo C).

One object, keys in this order:

- `meta` — `title`, `generated` (ISO UTC), `commit` (provenance for the file,
  never displayed by the page), `model`, `instruments` (1,303), `provisions`
  (`matrix.json`'s `unit_rows`, 161,989), `heading_rows_excluded` (28,568),
  `transitorio_rows_excluded`, `identical_shared_dropped`, `counted_rows`
  (in that order; the counts of the current files are in the newest
  "Measured" section below),
  `distinct_texts` (`vector_rows`, 145,788), `collections` (`{"leyes": 315, "reglamentos": 863,
  "lineamientos": 125}`, counted from the table), `unique_names` (`true`),
  `duplicates_dropped` (`{"reglamentos": 219, "lineamientos": 1}`, from
  `unique-instruments.json`), `n_neighbors`, `default_n_neighbors` (16), `umap`
  (`min_dist`, `metric`, `random_state`, `umap_version` of the first fit),
  `weighting` (`"1/m"`), `top` (5), `sources` (the three corpus releases, then
  the three `scjn-*-vectors`). `provisions` keeps its old name as a JSON key
  though it counts excerpts (issue #271): keys are code, and the pair files'
  `provisions` lives in a release.
- `instruments` — a list whose index is `i`, each `{"c": coleccion, "k":
  clave, "n": nombre, "p": total excerpts, "pc": counted excerpts, "in":
  incoming weight, "out": [[j, w], …], "inc": [[j, w], …]}`. `out` is `strongest_targets(A, i, top)` and `inc`
  the same helper over `A.T` (who points *here* hardest): weight > 0 only,
  heaviest first, ties by ascending id, weights to one decimal — the ranking
  the research page uses, imported, so the two cannot disagree. `pc`
  (issue #271) is `out`'s total: the matrix row sum, the instrument's counted
  excerpts, an integer `>= 1` (the exporter refuses a row sum more than 1e-3
  from an integer) whose sum over the instruments is `meta.counted_rows`. It
  is a per-model fact — BM25 differs from the two embedding models in 197
  instruments — so, unlike `p`, it is **not** among the keys `--instruments-as`
  compares (`c`, `k`, `n`, `p`). The page shows `pc` only; `p` stays for
  integrity and identity checks.
- `projections` — `{"4": [[x, y], …], "8": …, "16": …, "32": …}`, one pair
  per instrument in `i` order, rounded to `--decimals`, in `[0, 1]`.

**Excerpts, not units, not provisions** (issue #271), in everything the export
carries and the page says: an *excerpt* is a block of text cut from an
instrument whatever its legal role (an article, a transitory article, a
heading, a preamble) — the word of the LegalIA paper, after Colombo et al.
2025 — where *provision* would suggest a normative rule a heading is not, and
*legal provision* already means a DOF note in this project. So: *excerpt* is
the reader's word, *unit* the Python side's (`md2akn.text_units`), *legal
provision* a DOF note. The article of a transitorios block is a *transitory
article* on the page. The Python side keeps `units`; the words `units` and `tooltip` appear nowhere in the
file. The exporter refuses (`SystemExit`) a work directory not prepared with
`--unique-names`, a matrix whose shape does not match the table, a requested
`n_neighbors` missing from `umap.parquet`, an instrument with an empty `out`,
excerpts that do not add up to `matrix.json`'s `unit_rows`, counted excerpts that do not add up to `counted_rows`, and a `matrix.json` that
lacks the counted-row totals or a `matrix.npy` that does not sum to `counted_rows`.

Measured on 2026-09-29: **422.7 kB** (110.1 kB gzipped), under a second; the
Universidad Autónoma Chapingo (`luach`) points at the UAM 10.0, Narro 7.0, Ley
Agraria 1.0, INAH 1.0 and the IPN 1.0 — all five — and `cpeum` has 1,328
excerpts, 837 of them counted. One instrument (the reglamento `124138`, nothing
points at it) has an empty `inc`; every `out` is non-empty.

### The pair explanations (issue #249)

`atlas.json` says the Constitution is closest to the *ESTATUTO de Gobierno del
Distrito Federal* with a weight of 41.0 and nothing more. `export_atlas_pairs.py` writes the evidence
behind every such number, one JSON per pair `(i, j)` the panel lists under
*Closest instruments* — `j` among `strongest_targets(A, i, 5)`, taken from
`export_atlas_data.weighted_targets` itself, so the files are exactly
`atlas.json`'s `out` lists — with the full text of both sides:

```bash
uv run --group viz python scripts/embeddings/export_atlas_pairs.py --work-dir emb-run-atlas \
    --atlas website/pages/atlas/atlas.json --install website/pages/atlas/pairs
```

It needs `emb-run-atlas/instrument-matrix/.done` (plus `matrix.npy`,
`nearest.parquet`, `vectors.npy`, `vector_ids.parquet`,
`instruments.parquet`, each refused by name when missing), a work directory
prepared with `--unique-names`, and the `legalvec` cache, and is a pure read of
all of them. `.done` makes a rerun export nothing unless `--force`; `--install
DIR` (run even then) replaces `DIR` with a copy of `pairs/`, so a pair from an
earlier run cannot survive there. `--atlas` checks an existing `atlas.json`
pair by pair, weight by weight and `clave` by `clave`. Other flags:
`--work-dir`, `--cache-dir`, `--top` (5), `--tolerance` (1e-6), `--out-dir`
(`<work-dir>/atlas-pairs`), `--repo`.

`pairs/<i>-<j>.json`, `i`/`j` being positions in `atlas.json`'s
`instruments`, holds, keys in this order: `source` and `target` (`i`, `k`
clave, `c` collection, `n` name — `k` lets the page notice a tarball built
against another instrument table), `weight` (`round(A[i, j], 1)`, the panel's
number), `provisions` (rows listed, the key kept as published), `rows` and `texts`. A row is one **unit
row** of `nearest.parquet` whose `targets` hold `j` — a text repeated inside
the source is one row per repetition, as in the matrix, so `Σ 1/m` over the
rows is `A[i, j]` (asserted to 1e-3 per pair) — with `label`, `path` (the
breadcrumb, `" › "`-joined, never merged into the label), `unit_type`, `text`
(a vector row, a key into `texts`), `targets` (every unit of `j` carrying a
winning text, each `label`/`path`/`text`), `similarity` (4 decimals) and `m`
(an integer; the page turns it into `1` or `1/m`). Rows go similarity first,
then the heavier weight (smaller `m`), then label. `texts` holds each text
once per file, as `md2akn` emits it.

- **The winners are recomputed, not read.** `nearest.parquet` records which
  instruments won, not which of their vector rows, and recording that would
  mean rerunning #242's Slurm job. So the exporter multiplies each pair's
  source rows by all of `j`'s rows (normalised, from `vectors.npy`), keeps
  every `j` row within the tolerance of the recorded best, and asserts `j`'s
  own best equals `nearest.parquet`'s `similarity` to 1e-6 for every row —
  it must, since `j` owns a global winner and none of its rows is masked.
- **Labels** (`unit_label`) are English for the type word and the corpus'
  spelling for the rest: `Article 27`, `Article 2, part 3`, `Heading V`,
  `Preamble`, `Closing`, `Loose text`, and, for anything inside a
  transitorios block (an `article` whose `path` ends in `TRANSITORIOS …`,
  which is where the corpus puts them — not `loose`), `Transitory provisions,
  29 DE AGOSTO DE 2008 · Único`.
- **Release + per-pair files, not a commit and not one file.** The files are
  far too big for `master`; GitHub release assets send no
  `Access-Control-Allow-Origin` header, so a browser cannot read them from a
  release; GitHub Pages gzips JSON on the fly (`atlas.json`, 427 kB, is served
  as 115 kB), so one plain `.json` per pair costs a median click ~3 kB. The
  tarball is published by a human as the release `atlas-pairs`
  (`PUBLICAR.md`, generated, never run by the script; issue #115, Hallazgo
  C), and the website's publish workflow unpacks it into the site (#250).
  It is reproducible: sorted members, mtime/uid/gid 0, gzip mtime 0.
- **`PUBLICAR.md` replaces the release in place.** The `atlas-pairs` tag
  already exists after its first publication, so the generated file leads with
  `gh release upload ... --clobber` and `gh release edit --notes-file
  .github/atlas-pairs.md` (the create command is kept only for a repository
  that has no such release yet), under the warning to do it **before** the
  pull request that carries the regenerated `atlas.json` is merged to
  `master`: `website.yml` pairs the committed `atlas.json` with whatever the
  release holds when it runs, and the page's `k` check would answer every click
  with the "different version of the map" message.

Measured on 2026-09-29, on the login node:

| | value |
|---|---|
| wall clock | 51 s |
| pairs | 6,372 |
| unit rows the pairs explain | 75,756 of 120,141 counted (weight 73,645.7) |
| unit rows per pair | median 4, p90 27, p99 112, max 705 (`pairs/0-6.json`, *CÓDIGO Civil Federal* → *CÓDIGO Nacional de Procedimientos Civiles y Familiares*) |
| JSON | 165.2 MB raw, 39.8 MB gzipped file by file; per file median 8.8 kB / 2.8 kB, p95 99.3 / 21.7 kB, max 1.19 MB / 214 kB |
| `atlas-pairs.tar.gz` | 35.0 MB |
| largest file | `pairs/1122-1059.json` (1.19 MB, 84 rows): the *REGLAMENTO INTERIOR DE LA SECRETARIA DE MEDIO AMBIENTE Y RECURSOS NATURALES* → the same title with a final period, one of the four punctuation-only pairs above |
| Constitution → *ESTATUTO de Gobierno del Distrito Federal* (`pairs/8-10.json`) | 41 unit rows, 41 distinct texts, weight 41.0, every row a whole 1 (`m == 1`): 38 transitory rows (each below 0.99, the highest 0.906) and three articles (44, 61 and 101) |
| Constitution → *CÓDIGO Penal Federal* (`pairs/8-9.json`) | 55 unit rows, weight 34.4, third of the Constitution's five; 53 of them transitory |

No pair file holds a heading row, a row with similarity 1 and `m > 1`, or a
transitory row at 0.99 or more.
Every file's `weight` equals its `atlas.json` `out` weight, every text key
resolves, every file is sorted, and `Σ 1/m` is within 0.05 of `weight` except
for float noise on exact halves.

### The Atlas page

The file above is what the website's **Atlas** reads
(`website/pages/atlas.qmd`, navbar *Atlas*, issue #245): a hand-written D3 v7
application in `website/pages/atlas/atlas.js` and `atlas.css`, the public
successor of the research page. It draws circle **area**
proportional to counted excerpts, `pc` (a square-root radius, 2.5 px floor) in the site's
palette (laws `#2a78d6`, regulations `#008300`, guidelines `#e87ba4`), lists
all five closest instruments and the five that point here in a detail panel
instead of a tooltip, joins the selection to its five targets with numbered
lines, and has a search box (accents and case folded), a labelled
*Neighbourhood size* control over the four layouts, and a collection legend.
Its browser tests run the qmd's own markup in headless Chromium:

```bash
uv run --group viz playwright install chromium   # once
uv run --group viz pytest scripts/embeddings/tests/test_atlas_page.py -q
```

They serve `website/pages` over `http.server` (Chromium will not `fetch` from
`file://`) with a harness page whose body is the qmd's `<!-- atlas:app -->`
block verbatim, and leave a screenshot with Chapingo selected at
`output/atlas-chapingo.png` (gitignored). Without Playwright or its Chromium
they skip with the reason; the static checks on the qmd, `_quarto.yml` and the
two files always run. The `test_rendered_*` tests (issue #247) render
`pages/atlas.qmd` with Quarto into `website/_site/` (gitignored) and drive the
page Quarto actually wrote, site CSS included — the harness alone cannot see
Quarto's `aside` rule collapse the map to 0 px — leaving
`output/atlas-rendered.png` and `output/atlas-rendered-chapingo.png`; they
skip only when no Quarto is found on `PATH` or under
`~/.local/opt/quarto-*/bin/quarto`. Every number the tests compare with the
page (the subtitle's counts, Chapingo's five targets, the Constitution's
counted excerpts, the pair dialog's rows) is read off `atlas.json` and the installed
pair file, so the prose and the data cannot drift.

**The explanation dialog (issue #250).** Each weight under *Closest
instruments* is a `button.atlas-why` that opens a native `<dialog>` over the
pair file #249 exports: a heading naming both instruments, one sentence
("15 excerpts of … have their closest text outside it in …; they add up to
15.0 of its 137 counted excerpts."), and a table —
number, the source excerpt
(label, breadcrumb, full text), the closest text in the target (the same, plus
"also N other excerpts … carry this text" when several do), similarity to
three decimals, and the weight as `1` or `1/m` with "this text is shared by
*m* instruments". Rows keep the file's order and arrive 50 at a time ("Show 14
more (14 left)" for the last batch of the pair above); the mount's `data-page-size` changes that number, and
exists for the tests. Text is Markdown rendered bold-only, paragraphs on blank
lines, never shortened, never parsed as HTML. The file is fetched on the first
click only (a `Map` per page load), from the mount's `data-pairs`
(`atlas/pairs/`), and checked by both instruments' `k`: a 404 or network error
says "The explanation for this pair is not available.", a mismatch "The
explanation was built for a different version of the map." Escape, the close
button and a backdrop click close it; the selection, lines and zoom are
untouched and focus returns to the weight. *Points here* has no file behind
it (only the five closest are exported) and stays plain numbers.

To see it locally the pairs have to be under `website/pages/atlas/pairs/`
(gitignored): either `uv run --group viz python
scripts/embeddings/export_atlas_pairs.py --work-dir emb-run-atlas --install
website/pages/atlas/pairs`, or the published release by hand:

```bash
gh release download atlas-pairs --repo INGEOTEC/LegalIA \
    --pattern atlas-pairs.tar.gz --pattern SHA256SUMS.txt --dir /tmp/atlas-pairs
(cd /tmp/atlas-pairs && sha256sum -c --ignore-missing SHA256SUMS.txt)
mkdir -p /tmp/atlas-pairs/unpacked && tar -xzf /tmp/atlas-pairs/atlas-pairs.tar.gz -C /tmp/atlas-pairs/unpacked
rm -rf website/pages/atlas/pairs && mv /tmp/atlas-pairs/unpacked/pairs website/pages/atlas/pairs
```

The site does the same: `.github/workflows/website.yml`'s *Fetch the Atlas
pair explanations* step runs those lines (plus `manifest.json` into the same
directory) before `quarto publish`, and fails when the release or the asset
is missing, because a site without the files would answer every click with
"not available". It only copies what a human published (issue #115, Hallazgo
C) — so **replace `atlas-pairs` before this reaches `master`**, or the next
website run pairs the new `atlas.json` with the old pair files.
`_quarto.yml`'s `pages/atlas/**` resource glob already ships the directory.

The dialog's tests run over a toy site — the six-instrument corpus exported
by both scripts into a temporary `website/pages/` look-alike with the real
`atlas.js`/`atlas.css` — and check the rows against the pair file, the weights
against the panel, `1/m`, paging, the three ways to close, focus, no request
before the click and one per pair, both messages, bold-only rendering, and a
390 px phone. With the real pairs installed, one more test opens the
Constitution's heaviest pair (the ESTATUTO, 41 rows, 41.0, every row a whole 1)
and saves `output/atlas-explain-cpeum.png`; without them it skips. A
rendered-site test checks Quarto's `h2` rule does not reach the dialog.

### Topics of the instrument map (issue #285)

```bash
uv run --group viz python scripts/embeddings/cluster_instruments.py --work-dir emb-run-atlas-4b
uv run --group viz python scripts/embeddings/cluster_instruments.py --work-dir emb-run-atlas-4b \
    --choose louvain:1.5 --force
```

Nothing in the Atlas groups the instruments or says what a region of the map is
about. `cluster_instruments.py` groups them from the same information the map is
drawn from, the **L2-normalised rows of `A`** (not the excerpt centroids, not the
page's 2-D coordinates; a zero row is reported and assigned noise), and labels
each cluster. It needs a prepared work directory with a computed matrix, refuses one
without it naming the file, and runs in about five minutes on one CPU: no Slurm, no
GPU, no network. **The gold is not read from the work directory** (review fix-1):
`--gold` names it, defaulting to `emb-run-atlas/gold-links/gold.json` as in
`evaluate_links.py`, because it is built from names and text and not from a model, so
one file serves every work directory and `emb-run-atlas-4b/` has no `gold-links/` of
its own. A gold whose `provenance.instruments` is not the work directory's instrument
count is refused, and `meta.gold` records its path, commit and count. Issue #285
measured the 0.6B (`emb-run-atlas`); the page runs the **4B** (`emb-run-atlas-4b`,
`Qwen/Qwen3-Embedding-4B`), the data set the row-level evaluation found better, with
the same rule, gates, sweeps and labels, and only the matrix changed. The BM25 work
directory is accepted through `--work-dir` but not run.

Four algorithms, each swept over its one tuning parameter (22 configurations: the
issue says 21, but its five, six, six and five values add up to 22):

| algorithm | what it is | swept |
|---|---|---|
| `hdbscan_umap` | UMAP to 5 dimensions (`n_neighbors` 15, `min_dist` 0, cosine, `random_state` 0, fitted once), then `sklearn.cluster.HDBSCAN` on it; density-based, no *k*, an explicit noise label (the BERTopic / Top2Vec pipeline) | `min_cluster_size` 5, 10, 15, 20, 30 |
| `agglomerative` | Ward linkage on the normalised rows (Euclidean distance on unit vectors is monotone in cosine); a dendrogram cut at *k*, no noise | `n_clusters` 20, 30, 40, 50, 60, 80 |
| `kmeans` | `KMeans(n_init=10, random_state=0)` on the normalised rows, spherical *k*-means in effect | `n_clusters` as above |
| `louvain` | `networkx.community.louvain_communities(seed=0)` over the undirected graph of `A + Aᵀ` (the raw matrix, zero diagonal): `A` *is* a weighted directed graph, so it uses the Atlas' own structure with no reduction | `resolution` 0.5, 1.0, 1.5, 2.0, 3.0 |

LDA and NMF are not run (the lexical view already exists as the BM25 data set),
nor Leiden (it would need `igraph`); `networkx` is named in the root `viz` group so
the Louvain import is deliberate. A configuration that raises is recorded as
failed and the sweep goes on. Labels are renumbered by cluster size, so a label
means the partition and not an algorithm's own numbering.

Five measures per configuration: the number of clusters (noise excluded), the
noise fraction, the largest cluster's share of the instruments, the cosine
silhouette over the normalised rows (noise excluded), and the **strong-link
co-membership**, the fraction of the gold's 275 links (an instrument's `targets`
in `gold.json`, signals A or B, issue #272 and #283's review fix) whose two ends
carry the same non-noise label.

**The rule, fixed beforehand and never tuned after the result.** Among the
configurations that finished with 20 to 80 clusters, at most 15 % noise and a
largest cluster of at most 25 % of the instruments, take the one with the highest
co-membership; break a tie by silhouette, then by fewer clusters, then by the
algorithm's name. Co-membership alone is maximised by one giant cluster, so the
gates keep the result a topic map and not a partition into "treaties" and
"everything else". When no configuration passes, the script writes the comparison
and exits non-zero naming the gate each one failed (a finding for a human, not
something to relax); `--choose algorithm:parameter` overrides the rule, and
`meta.selection` records that it did and what the rule would have picked.

**Labels** are BERTopic's class-based TF-IDF over unigrams and bigrams of the
*searched* unit rows (headings and transitorios left out through
`instrument_matrix.searched_mask`, every row counted, so an article written twice
counts twice) of each cluster's instruments, tokenised like
`prepare_bm25_input.py` (lowercase words of two or more characters). A term's
weight in a cluster is its count there relative to the cluster's own total (the L1
normalisation BERTopic applies) times `log(1 + A / f_t)`, `A` the mean number of
terms per cluster and `f_t` the term's count over all clusters; the noise cluster
is a class like the others. A term must occur in at least two instruments.
Without any stopword list, `de`, `la`, `el`, `en`, `los` and `que` were among the
six leading terms of 28 of the first run's 32 clusters, which names no topic, so
`STOPWORDS` holds 58 Spanish function words (no domain word: `artículo`, `ley`,
`estado`, `parte` stay), applied to every cluster alike and recorded in
`meta.labels`. A cluster's three **representatives** are the instruments nearest
(cosine) to its mean normalised row, and its members are ordered by that same
distance.

`topics.json` holds `meta` (algorithm, parameter, selection, seeds, measures,
counts, gates, label settings, library versions, `model`/`method` from
`input.json`, the commit) and one entry per cluster: `id`, `size`, `terms`,
`representatives` and `members` as `[i, distance]`, `collections`. Clusters come
largest first and the noise cluster, if any, is `id: -1` last. Two runs are
byte-identical apart from `meta.generated` (checked on the real run). No
hierarchy is exported, no topic is named by a language model, and `atlas.js`,
`atlas.qmd` and `atlas*.json` are untouched.

### The Topics page (issue #286)

```bash
uv run --group viz python scripts/embeddings/export_topics_page.py --work-dir emb-run-atlas-4b
```

**Status: a draft that the site does not render, link or ship** (review fix-2 of
#286; decision of the repository owner on 2026-10-13, to be reviewed later). The
procedure, the exporter, the two generated files and the page's source stay committed
for that review. `topics.qmd` has `draft: true`; Quarto 1.9.38 renders a draft as an
empty stub file, so `_quarto.yml`'s `project.render` also excludes
`!pages/topics.qmd` and `_site/pages/topics.html` does not exist; there is no navbar
entry, no `pages/topics/**` resource, no BERTopic key in `references.qmd`'s `nocite`
(the `references.bib` entry stays, and the draft still cites it) and no `topics.html`
in `fix_citation_links.py`'s `PAGES`. Tests assert all of it, so the page cannot be
republished by accident. **Re-enabling it** takes dropping `draft: true` and the
`!pages/topics.qmd` render exclusion, restoring the *Topics* navbar entry right after
*Atlas*, the `pages/topics/**` resource, `@Grootendorst2022BERTopic` in `nocite` (and
its sentence) and `pages/topics.html` in `PAGES`, and turning the tests of the wiring
back. The text below describes the page as it was designed to be published.

`website/pages/topics.qmd` presents
every topic of #285's chosen configuration and its members. It is static Markdown,
with no code cells and no JavaScript, so it needs no freeze entry and no browser
harness. The exporter is a pure read of
`emb-run-atlas-4b/topics/topics.json` and `instruments.parquet` (seconds, no refit; a
work directory with no `topics/.done` is refused) and writes the two committed
files, generated and never edited by hand. Regenerating them is this script, run by
a human, never a workflow (issue #115, Hallazgo C); `meta.commit` is provenance and
the page never shows it. The committed files come from `emb-run-atlas-4b/` (review
fix-1 of #285 and #286; they first came from the 0.6B's `emb-run-atlas/`), so
`meta.model` is `Qwen/Qwen3-Embedding-4B`, and a test pins that and the instruments'
`c`/`k`/`n` to `atlas-qwen3-4b.json`.

* **`topics.json`** has `meta` (title, generated, commit, model, method, the
  algorithm, its parameter, the selection, the gates, the measures, the counts),
  `topics` and `instruments`, the Atlas' `[c, k, n]` triples in `i` order, which a
  test pins to `atlas.json` at every `i`. A topic is `id` (the page's number),
  `cluster` (the number #285 gave it), `size`, `terms` (10), `reps` and `members`
  as lists of `i` in order of distance to the topic's mean, and `by` (counts per
  collection).
* **Numbering.** The page numbers the topics from 1 by size, largest first (a tie
  keeps #285's order), so the 32 topics of the measured run are 1 to 32, where
  #285's own tables number them from 0; the instruments in no topic, if any, come
  last as `id: -1`, headed *Instruments in no topic* (the measured run has none).
* **`_topics.md`** opens with an HTML comment saying it is generated, then a summary
  table (topic, three leading terms, size, laws / regulations / guidelines / treaties,
  each topic an anchor to its section) and one section per topic with the heading
  *Topic n: term · term · term* and an explicit `{#topic-n}` id, its ten terms as
  inline code, a sentence with its size and composition in words, its three
  representatives with their collection, and a collapsed `callout-note` with every
  member. Inside it the members are grouped by collection (laws, regulations,
  guidelines, treaties, the Atlas' order, each group a bold line with its count),
  ordered by distance to the topic's mean inside a group, so the representatives
  lead their own group. Plain lists, not tables: a long treaty name wraps in a list
  and overflows a table on a phone. A member is its name and its `abrev` (a law) or
  SCJN id in inline code.
* **A name is written literally.** Names are escaped for Markdown, whitespace runs
  (one name carries a line break) collapse to a space, and a run of two hyphens or
  three dots, which pandoc's smart typography would turn into a dash and an ellipsis,
  and the straight quotes of 17 names are kept as the corpus spells them; the rendered
  page is tested for it.
* **The prose** follows the Atlas' conventions (flowing English, no lead phrase and
  colon, *excerpts*, no *unit*/*provision*, no script name, no commit), and its
  numbers are literals copied from "Measured, topics, the 4B, 2026-10-13" and pinned by
  `tests/test_topics_page.py` against the committed `topics.json`, as is the worked
  example (topic 21, the lottery regulations). The class-based TF-IDF is cited as
  Grootendorst (2022) through `references.qmd`'s `nocite`, and
  `website/scripts/fix_citation_links.py` retargets the link, `topics.html` being
  added to its list of pages.
* **Out of scope**: any change to `atlas.js`, `atlas.css`, `atlas.qmd` or
  `atlas*.json`, a link from a topic into the map (the Atlas has no URL parameter),
  a search box or any interaction, a hierarchy, a figure, the 4B and BM25
  configurations, and a Home teaser card (`index.ipynb` is frozen).

`tests/test_topics_page.py` runs the exporter over a toy work directory (seven
instruments over three collections, a name full of Markdown syntax, clusters whose
#285 ids are not in size order), checks the two committed files against each other
and against `atlas.json`, the qmd's prose and literals against `topics.json`, and,
when a local Quarto is found the way `test_atlas_page.py` finds it, renders the page
into the gitignored `website/_site/` and checks the include, the 2,676 member lines,
the navbar and the citation.

### Nearest texts (issue #288)

`nearest_texts.py` is the matrix's sweep again, keeping the **five closest
foreign texts** of every searched excerpt instead of only the best. The
Instruments page shows them on every excerpt an instrument has and lets the
reader navigate to them, which `nearest.parquet` (the winner and its exact
ties) cannot supply. Everything it needs is read from a finished matrix: it
refuses a work directory without `prepare.done`, `instrument-matrix/.done` and
`instrument-matrix/nearest.parquet`. Only the 4B work directory
(`emb-run-atlas-4b`) has been run; the code goes through `scoring.scorer_for`
and so also works over a BM25 work directory (a lexical score of 0 is never a
neighbour), but nothing was measured for the other two.

```bash
uv run python scripts/embeddings/nearest_texts.py --work-dir emb-run-atlas-4b --dry-run
uv run python scripts/embeddings/nearest_texts.py --work-dir emb-run-atlas-4b --submit
uv run python scripts/embeddings/nearest_texts.py --work-dir emb-run-atlas-4b --wait --max-wait-minutes 9   # 75 while running
uv run python scripts/embeddings/nearest_texts.py --work-dir emb-run-atlas-4b --report                       # offline
uv run python scripts/embeddings/nearest_texts.py --work-dir emb-run-atlas-4b                                # compute here
```

The rules are the matrix's, imported from `instrument_matrix.py` and not
restated in code: the sources are the searched unit rows (`searched_mask`: no
heading, nothing in a transitorios section); the candidates are every vector
row except the source instrument's *exclusively* owned rows and the rows
nobody owns among the searched rows; a text the source shares with another
instrument stays a candidate (at similarity 1 when it is the same text); the
score is the work directory's own, exact, in blocks of `--block-rows`. What is
new is the selection. Per block, `np.argpartition` keeps the best `--top`
columns (`TOP_TEXTS` = 5, importable, "to be consistent with the Atlas"), and
a row where more than K columns reach the K-th score is redone from every
column at or above it, so **a tie at the cut is broken by the lower vector
row**; the kept columns are sorted by `(-similarity, target_row)`. A tie at
rank 1 is not collapsed: two exact ties are ranks 1 and 2. A masked column, or
a zero BM25 score, is never written, so a source can have fewer than K rows.

Outputs, under `<work-dir>/nearest-texts/` (its own directory: the matrix's
`.done` already means "the matrix is there"), written atomically with `.done`
last:

- `nearest-texts.parquet` — one row per (source, rank), sorted by
  `(i, row, rank)`: `i`, `coleccion`, `clave`, `row` (the source's vector row),
  `rank` (1..K), `target_row`, `similarity` (`float32`) and `targets` (the
  instruments owning `target_row`, minus `i`, sorted). The sources are keyed by
  `(i, row)`, an instrument and a vector row, **not** by unit row: a text that
  repeats inside one instrument has one answer, and the exporter fans it back
  out to its excerpts by `eId`. `summary.json` therefore says both numbers,
  `sources` (searched unit rows, equal to `nearest.parquet`'s row count) and
  `source_texts` (distinct `(i, row)`, what this table is keyed by).
- `summary.json` — `top`, `sources`, `source_texts`, `rows_written`,
  `sources_short_of_top` (distinct sources with fewer than K rows, one with
  none at all included), `sources_without_rows`, `agrees_with_nearest`,
  `tolerance`, the scorer's own `describe()`, `block_rows`, `threads`, `node`,
  `slurm_job_id`, `seconds` per phase, `seconds_total` and `peak_rss_gb`.
- `job.json` and `slurm-<jobid>.out` when it ran through Slurm.

**The agreement check.** Rank 1 is supposed to *be* the Atlas' winner, so before
`.done` the script proves it: for every `(i, row)` of `nearest.parquet` with
`n_winners > 0` the rank-1 similarity equals the matrix's within `--tolerance`
(1e-6) and the rank-1 `targets` are among the matrix's; a source the matrix
found no winner for has no row here, and no source here is missing from
`nearest.parquet`. Anything else is a `SystemExit` naming the first
`(i, row)`; the tolerance is not loosened to make a run pass.

**Blocking and the last bit.** Which texts are kept, in which order and owned
by whom does not depend on `--block-rows`; the `float32` similarity can differ
in its last bit between block sizes (a different matrix product sums in a
different order), so the test compares that column at the matrix's tolerance,
not byte for byte.

The Slurm plumbing is not a copy: `instrument_matrix.sbatch_command`/`submit`/
`wait`/`load_job`/`slurm_tail`/`output_dir` take the script, the sub-directory,
the job name, extra arguments and the files that must exist as keyword
arguments, defaulting to the matrix's own (its tests and outputs are
unchanged).

`tests/test_nearest_texts.py` imports the toy corpora of
`test_instrument_matrix.py` and checks the table against a brute force written
in the test (every foreign candidate scored with plain `numpy`, sorted by
`(-similarity, row)`), plus the hand-derivable facts: a `top` larger than the
candidates, the exclusive and unowned rows never a target, a shared text a
candidate at similarity 1, a tie as ranks 1 and 2, the transitorio corpus, a
doctored `nearest.parquet`, `.done`/`--force`, the Slurm states and a BM25
work directory.

### The instrument files (issue #289)

`export_instrument_files.py` is the data side of the website's Instruments page
(issue #290): one JSON file per Atlas instrument — the document, the tree
`md2akn` read in it, the excerpts it was cut into, parse-quality flags and, on
every searched excerpt, the five nearest texts of `nearest_texts.py` resolved to
their owners. It is a pure read of the work directory (`instruments.parquet`,
`nearest.parquet`, `nearest-texts/`), the `legalvec` cache and the `scjn` cache,
with no Slurm and no network (the 4B run: ~9 minutes).

```bash
uv run --group viz python scripts/embeddings/export_instrument_files.py --work-dir emb-run-atlas-4b \
    --instruments-as website/pages/atlas/atlas-qwen3-4b.json \
    --index website/pages/instruments/instruments.json \
    --install website/pages/instruments/files
```

- **Which text, and the check that stops the run.** Each instrument is shown at
  the *one snapshot its vectors were built from*: the `archivo` column of the
  vectors release's `units.parquet`, read through `snapshot_reader(coleccion)
  (clave, archivo)` (default: the `scjn` cache, disk only). The Markdown is
  parsed again (`md2akn.parse_markdown`, `text_units(split_articles=False)`) and
  the result must equal the released rows — `(unit_type, eId, piece, text_sha1,
  start_char, end_char)` in order — or the run is a `SystemExit` naming the
  instrument and the first differing position. There is no `--allow-mismatch`:
  issue #279 proved the re-parse reproduces the units, so a difference is a
  finding to report, not noise to skip.
- **`files/<coleccion>/<clave>.json`**, short keys, the text stored once. `meta`:
  the Atlas position `i`, `c`, `k`, `n`, `archivo`, `fecha_publicacion`,
  `codNota` (leyes only), `md2akn_version`, `model`, `model_slug`, `top`,
  `generated`, `commit`, `numbering` (`mode` `articulo`/`ordinal`/`numeral` from
  `modo_sin_articulos`, `roman_articles` the article nodes numbered with a roman
  numeral), `units`, `searched`, `flags`. `markdown`: the raw document, frontmatter
  included. `tree`: the root node, each `{t, e, n, h, r, s, l, x, a, c}` (akn
  type, `eId`, number, heading, `refers_to`, start, end, `["chapeau"|"tail"]`,
  annotations `{raw, action, scope, date, source}`, children), empty keys omitted.
  `units`: in document order `{u, e, p, pe, s, l, sha, v, x, f, near}` — `v` is
  the vector row, `x` is true when the excerpt was searched (the matrix's own
  `searched_mask`), `f` its flag names and, only when `x`, `near`: up to five
  `[j, eId, piece, similarity, also]`. `j` is the Atlas index of the *first
  owner* of the target text (lowest `j`, then its first searched excerpt), `eId`
  and `piece` that excerpt, `similarity` rounded to four places and `also` the
  remaining owners `[[j, eId, piece], ...]`; a text owned by several instruments
  is one entry. The owners must be exactly `nearest-texts.parquet`'s `targets`,
  and the first entry's similarity `nearest.parquet`'s, both asserted.
- **Offsets are UTF-16 code units.** `md2akn`'s `start_char`/`end_char` are Python
  code points and a JavaScript string is UTF-16, so every `s`/`l` is converted
  once here (an astral character before an offset counts twice, `Utf16`) and the
  page slices `markdown` directly. A test pins it with an emoji and a
  supplementary-plane letter before an article, slicing the way JavaScript does.
- **Flags** are counts the exporter computes, never judgements; the page shows
  `FLAG_MEANINGS` next to each. Excerpt level: `loose` (an excerpt of type
  `loose`), `duplicate_number` (an `article` number — as written, trimmed — that
  appears more than once; every article carrying it is flagged),
  `numbering_gap` (an article whose integer number — `27`, `1º`, `1o.` — is not
  the previous integer number plus one; `4 Bis`, roman numerals and ordinal words
  are skipped, so a repeated number is a gap too), `over_window` (the excerpt's
  `text_sha1` is in the release's `texts_truncated` for the model). The two
  numbering flags look only at articles outside a transitorios section: a
  decree's `PRIMERO` repeats by design. Instrument level: `no_articles` (no
  `article` excerpt), `uncovered` (`coverage().uncovered_chars > 0`, the flag
  carries the character count) and `no_transitorios` (no section with
  `refers_to == "#transitorios"`, informational). `meta.flags` holds the counts
  and `score`, the sum of the excerpt-level counts plus one for `no_articles` and
  one for `uncovered` — the page's default ranking key.
- **The index** (`--index`, committed as `website/pages/instruments/instruments.json`):
  `meta` (`generated`, `commit`, model, `md2akn_version`, `top`, `instruments`,
  `collections`, `units`, `searched`, `flags` as `{flag: {instruments, count}}`,
  `flag_meanings`, `release`, `files`) and `instruments`, one `{c, k, n, archivo,
  u, x, a, f, s}` per instrument in the Atlas' order. `--instruments-as` refuses
  an Atlas file whose `c`/`k`/`n` differ at any position (a `SystemExit` naming
  the first), so the page can open an instrument by its Atlas index.
- **Packaging and the hand-off.** `atlas-instruments-qwen3-4b.tar.gz` (`files/` +
  `manifest.json`, members sorted, mtime 0, byte-reproducible),
  `manifest-qwen3-4b.json` (counts, bytes, `largest`, flag totals, `commit`,
  the nearest-texts `summary.json`) and `SHA256SUMS-qwen3-4b.txt`; every name
  carries the model's slug, with no unsuffixed default, because a second model
  adds its own three assets to the same release `atlas-instruments`
  (`.github/atlas-instruments.md` is its body). Nothing is published: a human
  runs `PUBLICAR.md` (issue #115, Hallazgo C) **before** the pull request that adds
  the page merges, because `website.yml`'s *Fetch the instrument files* step
  downloads the tarball, checks it against the sums, unpacks it into the
  gitignored `website/pages/instruments/files/` and fails when it is missing
  (the manifest lands beside it as `manifest-qwen3-4b.json`, also ignored).
  `--install DIR` copies the files, and only the files, into `DIR`.
  `.done` makes a rerun a no-op, `--force` recomputes.

`tests/test_export_instrument_files.py` builds a toy corpus of real Markdown
(five instruments in three collections, with a loose block, a repeated and a
skipped article number, a prose-only law, a law with no transitorios, an emoji
before an article, a text two laws share and a truncated one), parses it with
`md2akn` the way `build_units.py` does, and runs the matrix and `nearest_texts.py`
over it. It checks every key, the offsets by slicing as JavaScript would, the
`SystemExit` on a doctored `units.parquet`, `near` on searched excerpts only,
each flag (`uncovered` through a stubbed `coverage()`, since the invariant holds
on every real document), the index and `--instruments-as`, the reproducible
tarball and its sums, `PUBLICAR.md`, `--install`, `.done`/`--force`, and the
static wiring (the workflow step before publishing, the `.gitignore` line, the
release body and the committed index against `atlas-qwen3-4b.json`).

### The Instruments page (issue #290)

`website/pages/instruments.qmd` is a
hand-written, library-free page over #289's outputs: `website/pages/instruments/instruments.js`
and `instruments.css`, mounted by the qmd's `<!-- instruments:app -->` block, which reads
only that block's `data-*` attributes (the index, the folder of files, the repository to
report to, and a `data-models` list with the one entry the 4B). `_quarto.yml`'s
`project.resources` ships `pages/instruments/**`; the page has no code cell and no freeze
entry.

- **No navbar entry (review fix-1 of #290).** By decision of the repository owner on
  2026-10-13, while the page is used to find parse errors, it is reached through the
  paragraph that ends the Atlas page, not from the navbar. It still renders, is in the search
  index and ships its folder, and the workflow step is unchanged. Re-enabling the entry takes
  restoring `Instruments` right after `Atlas` in `_quarto.yml`'s navbar and the six-name pins
  in the three test files.
- **What it shows.** A landing with a search box and the corpus table (every instrument,
  its excerpts, compared excerpts, articles, one column per flag and the score; sortable,
  filterable by collection, 50 rows at a time). Opening an instrument shows its header, a
  summary of the parse (numbering mode and each flag as a button that jumps to the first
  flagged excerpt and then cycles), an outline of its containers and articles, and the
  document as `md2akn` tree: one nested block per node with a badge
  `akn_type · eId · num` (the Mexican term is its hover text), the node's text rendered with
  paragraphs and bold only, every excerpt wrapped with its `unit_type` and flags, a per-node
  *Raw* button, and on every compared excerpt a *Nearest texts* disclosure. The *Raw* mode of
  the whole page shows the document once, each excerpt as a span
  (`span.instruments-range[data-unit-type]`) and any character in no excerpt, annotation or
  frontmatter as `span.instruments-uncovered`. The document is rendered in chunks of a few
  hundred nodes per animation frame (outline first), so a 3,589-article code opens at once;
  `data-rendered="true"` marks the end. The whole interface says *excerpt*, *compared excerpt*,
  *nearest texts* and *parse*.
- **Addresses.** `instruments.html?c=<collection>&k=<key>` opens an instrument and
  `#<eId>` (or `#<eId>.<piece>`) an excerpt; the page pushes history on every navigation and
  restores it on `popstate`, so the back button returns to the previous instrument or
  excerpt. An unknown instrument is a status line over the landing. A nearest text is an
  ordinary query-relative link that the page also intercepts, so it opens in a new tab as
  well; its label (*Article 27*, *Preamble*, *Loose text*, *part 2*) is derived from the
  eId alone, with the eId as hover text, because the target's file is not loaded yet.
- **Failure is a message, never a broken page.** A file that does not exist says *This
  instrument's file is not available.*, a file whose `meta.k` or collection differs from the
  index's says it was built from *a different version of the data*, a failed index sets
  `data-ready="error"`, and in every case the page stays usable and a second click retries.
- **Reporting a parse.** Every badge and the header carry a link to
  `<repo>/issues/new` with the title `md2akn: <key> <archivo> <eId>` and a body naming the
  collection, key, name, snapshot, `md2akn` version, eId, the page address with its hash and
  an empty *What is wrong:* line. It is the only way the page reaches GitHub.
- **Colours.** The collections keep the Atlas' categorical colours; a node type has a fixed,
  quiet tint (containers greys, article ink, paragraph/point/subpoint stepped blues) and one
  warning amber is used for parse flags only, all documented in `instruments.css`.
- **The numbering column of the corpus table was left out**: the committed index does not
  carry it (it is in each file's `meta.numbering`, which the instrument view shows), and
  adding it would have changed #289's data.

```bash
uv run --group viz pytest scripts/embeddings/tests/test_instruments_page.py -q
```

`tests/test_instruments_page.py` has three layers. The static checks (front matter and
literals against the committed index, the mount block verbatim, the prose, the navbar and
resource, `fix_citation_links.py` untouched) always run. The browser tests drive headless
Chromium over a *toy site*: the five-instrument corpus of `test_export_instrument_files.py`
exported by the real exporter into a temporary `pages/` directory with the real script and
stylesheet and the qmd's own app block, one test per behaviour (landing, sorting, filter,
*Show more*, search, deep link, back button, nearest texts and their failure messages, flags,
outline, *Raw*, report links, the 375 px layout), writing `output/instruments-alfa.png`. The
rendered tests render the qmd with Quarto into the gitignored `website/_site/` and drive it;
one opens the real Civil Code when `website/pages/instruments/files/` was installed
(`export_instrument_files.py --install website/pages/instruments/files`).

### Tests

```bash
uv run --group viz pytest scripts/embeddings/tests -q
```

`tests/test_instrument_matrix.py`: a six-instrument toy corpus over two
collections whose vectors are written by hand, so every weight in the
expected matrix is derivable with a pen: a text shared inside a collection
wins at cosine 1, a text identical across collections wins at cosine 1 from
the other side, one row ties across two foreign instruments and gives ½ to
each, an instrument's own exclusive texts are masked, and a boilerplate line
carried by three leyes at once ("Se deroga.") wins at cosine 1 with `m = 3`
and is **dropped** rather than credited. Three headings stand in the corpus:
one carries a text nobody else has and is nearly another instrument's
article (it must never win), one carries an article's text (that text is
owned by the article's instrument alone), one repeats a transitorio (it is
not a source row). An identical text owned by exactly one other instrument
still adds 1, and a non-identical tie over two instruments still gives ½ each.
A second toy corpus (with a `path` column) pins the transitorio rule (issue
#264): a transitorio has no row in `nearest.parquet` whatever its similarity
(0.995, 0.98, cosine 1 with `m == 2`), an article at 0.995 still counts, a
transitorio text is not a candidate (a row that used to point at one goes to its
next-nearest text), a text a transitorio shares with an article is owned by the
article's instrument alone, a text only transitorios own never wins, a heading
inside a transitorios section is counted once as a heading, the near-copy
rule's flag, parameter and keys are gone, `export_atlas_pairs.load` accepts the
directory, and the path predicate is read on `TRANSITORIOS`, `TRANSITORIOS 18 DE MARZO DE 1980`, a nested path and a
label that only contains the word.
The identity (`A.sum(axis=1)` = each instrument's counted rows, `A.sum()` =
`counted_rows`) is asserted on the toy matrix, as are `float32` and `weight ==
1/m`. Blocking is asserted invisible (`--block-rows 1` against the default), the
Slurm plumbing is driven through a fake `squeue` in its three states (plus
`--force` forwarded into the job and the previous `.done` dropped), and the
page is built with a stub reducer.
`tests/test_cluster_instruments.py` (issue #285): an eight-instrument toy work
directory (two collections, a matrix and a gold written by hand, two obvious
groups, about water and about health) and two-block random matrices for the
algorithms. Each algorithm recovers the two groups; the measures are the ones the
text defines (co-membership derived on paper, silhouette undefined for one
cluster); the gates are inclusive and each decides who can win; the tie-breaks
run in order; `--choose` overrides the rule and `meta` records it; a failed
configuration is recorded and the sweep goes on; a zero row is noise in every
algorithm; c-TF-IDF ranks the group's distinctive word first and a heading never
reaches a label; every instrument is in exactly one cluster; a second run is
identical apart from `meta.generated`; `.done` returns and `--force`
recomputes and drops the old marker first; an interrupted run leaves no `.done`
and no `.parcial`; no configuration passing the gates writes the comparison and
exits non-zero; a missing matrix or gold is refused by name. UMAP is a stub.
`tests/test_unique_instruments.py` covers the selection on toy records — the
newest original date, a three-member group, accent/double-space folding, a
date tie resolved by the larger id, `DD-MM-YYYY` parsing, leyes and cross-
collection names never merged, and the snapshot-date lookup against a stub
reader. `tests/test_umap_scripts.py` runs `prepare_umap_input.py
--unique-names` over a toy corpus with a duplicated lineamiento: the dropped
instrument's exclusive and shared-file-only vectors get no row, and
`unique-instruments.json`/`input.json` say what happened (the default keeps
everything and writes no report). `tests/test_export_atlas_data.py` imports
the same toy corpus and checks the atlas export against it: the schema and
key order, `p`/`in`/`out`/`inc` against the matrix and `strongest_targets`,
rounded unit-square projections, no refit, and each refusal above (including
a work directory prepared without `--unique-names`).
`tests/test_export_atlas_pairs.py` does the same for the pair explanations:
one file per `strongest_targets` pair, each file's rows exactly
`nearest.parquet`'s and adding up to its cell, every winning target text
re-checked with numpy, the tie, the three-owner boilerplate and the repeated
text of the toy corpus, `unit_label` on every type, the manifest, the sums,
the reproducible tarball, a `PUBLICAR.md` that replaces the existing release
first, the rerun/`--force`/`--install` rules and the refusals.
The lexical baseline (issue #267) adds `tests/test_scoring.py` (the dense
scorer against the normalised product by hand; the BM25 scorer against a
three-document corpus whose Lucene scores are worked out in the file's comments,
against `bm25s.BM25.get_scores` row by row, the exact tie of equal token
multisets, a document with no term, `tie_mask` under both rules, `scorer_for`
choosing dense when `input.json` has no `method`) and
`tests/test_tune_bm25.py` (the grid, the metrics over hand-built tables, weights recomputed
from token counts against every bm25s method's own index, a sample that still masks a
source's unsampled texts, `--dry-run` computing nothing) and
`tests/test_prepare_bm25_input.py` (the copied tables are byte-equal, no vectors,
headings and transitorios are not documents, `input.json`, the refusals,
`.done`/`--force`). `test_instrument_matrix.py` keeps its dense suite unchanged —
the byte-for-byte guard of the refactor — and adds a BM25 run over the same toy
corpus whose matrix is derived on paper (a six-way relative tie giving ⅕ to each
of five instruments, an identical-shared drop decided by `text_sha1`, a row with
no foreign match), `test_export_atlas_pairs.py` a BM25 export (`-bm25` assets
only, `asset_names("bm25")`, the per-pair recomputation) and
`test_export_atlas_data.py` the file's `meta.method` and `--instruments-as`.
`test_atlas_page.py` pins the workflow step and the gitignore line for the BM25
set and that `atlas-bm25.json` lists the same instruments as the default.

### Measured, 2026-09-29

Prepared on the login node in 156.6 s (315 + 863 + 125 instruments, 145,788
distinct texts). The matrix, one job (42240) on `geoint1` (62 threads,
`--exclude=geoint0`):

| phase | seconds |
|---|---|
| load (the join + `vectors.npy`) | 1.5 |
| normalise (145,788 rows, in place) | 0.3 |
| sweep (133,421 searched rows against 145,788 texts, blocked) | 117.3 |
| write | 0.2 |
| **total** | **119.7 s** |

Peak RSS **2.02 GB** of the ~245 GB a node has — a 1,024-row block and one
normalised copy of the matrix is all that is ever live.

| what | value |
|---|---|
| unit rows | 161,989 |
| heading rows excluded | 28,568 |
| searched rows | 133,421 (21,344 of them transitorios) |
| identical winners shared by several instruments, not counted | 9,783 |
| transitorios at similarity 0.99 or more, not counted | 3,497 (3,357 `article`, 140 `loose`; 3,217 with `m == 1`) |
| **counted rows** | **120,141** (9,786 of them transitorios) |
| vector rows | 145,788 (3,370 owned by more than one instrument once headings are set aside) |
| instruments | 1,303 |
| `A.sum()` | **120,141.0** — one counted unit row, one unit of weight |
| every row sums to that instrument's counted rows | yes (`row_sums_equal_counted_rows`) |
| non-zero cells | 38,073 of 1,697,809 (2.2 %) |
| searched rows with a tie | 1,947 (all two-way) |
| largest `m` | 157 |
| mean similarity of the winner | 0.806 (0.785 over the counted rows) |
| searched rows whose winner is an identical text (cosine 1) | 14,455 (10.8 %): 4,672 with `m == 1`, which count unless they are transitorios, and 9,783 shared, which do not |

The `m` histogram over the searched rows — how many instruments a row's
answer is split over:

| `m` | 1 | 2 | 3–5 | 6–20 | 21–100 | 101+ |
|---|---|---|---|---|---|---|
| unit rows | 119,840 | 3,848 | 4,095 | 2,472 | 2,510 | 656 |

90 % of searched rows have a single answer and are credited a whole 1; a
row answered by many instruments adds one between them unless its winner is a
word-for-word text or a near-copy transitorio, in which case it adds nothing.

The four fits, on the login node, `random_state=0`:

| `n_neighbors` | 4 | 8 | 16 | 32 | total |
|---|---|---|---|---|---|
| seconds | 14.3 | 5.7 | 6.5 | 7.4 | **33.9** |

The research page: **1.2 MB** for 1,303 instruments, with the footer present
exactly once.

### Measured, the 4B, 2026-09-30 (issue #261)

`emb-run-atlas-4b/`, prepared on the login node in **369.8 s** (K = 2560; 315 +
863 + 125 instruments, 145,788 distinct texts, `instruments.parquet` and
`vector_ids.parquet` equal to `emb-run-atlas/`'s). The matrix, one job (42303)
on `geoint1` (62 threads, `--exclude=geoint0`):

| phase | 0.6B (seconds) | 4B (seconds) |
|---|---|---|
| load (the join + `vectors.npy`) | 1.5 | 8.9 |
| normalise | 0.3 | 0.8 |
| sweep | 117.3 | 183.3 |
| write | 0.2 | 0.2 |
| **total** | **119.7** | **193.5** |
| peak RSS | 2.02 GB | **3.17 GB** |

| what | 0.6B | 4B |
|---|---|---|
| unit rows | 161,989 | 161,989 |
| heading rows excluded | 28,568 | 28,568 |
| identical winners shared by several instruments, not counted | 9,783 | 9,783 |
| transitorios at similarity 0.99 or more, not counted | 3,497 | 3,611 |
| **counted rows** | **120,141** | **120,027** |
| non-zero cells | 38,073 | 35,316 |
| searched rows with a tie | 1,947 | 1,923 |
| largest `m` | 157 | 157 |
| mean similarity of the winner (searched rows) | 0.806 | 0.795 |
| searched rows whose winner is an identical text | 14,455 | 14,455 |
| `row_sums_equal_counted_rows` | yes | yes |

The four fits took 13.9, 5.2, 6.3 and 6.9 s (`n_neighbors` 4/8/16/32, 32.3 s).
The research page, `output/umap-instruments-qwen3-4b.html`, is 1.2 MB;
`website/pages/atlas/atlas-qwen3-4b.json` is **422,998 bytes** (`atlas.json`:
422,737).

Pair explanations: **6,352 pairs** (the 0.6B has 6,372: fewer instruments have
five distinct targets), 78,384 excerpts, 176.4 MB of JSON, largest file
`pairs/1122-1059.json` (86 rows, 1.19 MB), the tarball **37.5 MB**, 73 s.

How much the model changes the picture: over the 1,303 instruments, the five
closest instruments (`out`) of the two models share 3.4 of 5 on average (4
instruments share none), and the closest one is the same for 1,069.

The Constitution (`cpeum`) and the Chapingo law (`luach`), `out` under each
model — weights first, then the instrument:

| | 0.6B | 4B |
|---|---|---|
| `cpeum` | 41.0 ESTATUTO de Gobierno del Distrito Federal · 39.9 LEY General de Instituciones y Procedimientos Electorales · 34.4 CÓDIGO Penal Federal · 27.4 LEY Orgánica del Congreso General de los Estados Unidos Mexicanos · 24.7 LEY Orgánica del Poder Judicial de la Federación | 42.0 ESTATUTO de Gobierno del Distrito Federal · 42.0 LEY General de Instituciones y Procedimientos Electorales · 36.0 LEY Orgánica del Congreso General de los Estados Unidos Mexicanos · 26.2 CÓDIGO Penal Federal · 24.7 LEY Orgánica del Poder Judicial de la Federación |
| `luach` | 10.0 LEY Orgánica de la Universidad Autónoma Metropolitana · 7.0 LEY Orgánica de la Universidad Autónoma Agraria Antonio Narro · 1.0 LEY Agraria · 1.0 LEY Orgánica del Instituto Nacional de Antropología e Historia · 1.0 LEY Orgánica del Instituto Politécnico Nacional | 9.0 LEY Orgánica de la Universidad Autónoma Metropolitana · 7.0 LEY Orgánica de la Universidad Autónoma Agraria Antonio Narro · 1.0 CONSTITUCIÓN Política de los Estados Unidos Mexicanos · 1.0 LEY General de Educación · 1.0 LEY de Organizaciones Ganaderas |

(The names are the corpus' own spelling; the next issue's tests read the lists
from the JSON, not from this table. The
Constitution's total `in` weight — how hard everything else points at it — rises
from 680.9 to 799.6 under the 4B.)

The `PUBLICAR.md` this run wrote is `emb-run-atlas-4b/atlas-pairs/PUBLICAR.md`;
nothing was uploaded.

### Measured, both models, 2026-09-30 (issue #265)

The rebuild under issue #264's rule — headings *and* transitorios are neither
source rows nor candidates — over the two work directories issue #261 left
(`emb-run-atlas/`, `emb-run-atlas-4b/`), **not** re-prepared: the units, the
unique-name selection and the vectors did not change, only the matrix rule.
The matrices are two jobs (42438 on `geoint1`, 42439 on `geoint2`, 62 threads,
`--exclude=geoint0`), submitted together with `--force`:

| phase | 0.6B (seconds) | 4B (seconds) |
|---|---|---|
| load (the join + `vectors.npy`) | 6.4 | 11.2 |
| normalise | 0.3 | 0.8 |
| sweep (112,077 searched rows against 145,788 texts, blocked) | 102.8 | 162.6 |
| write | 0.2 | 0.2 |
| **total** | **110.0** | **175.2** |
| peak RSS | 1.98 GB | **3.19 GB** |

| what | 0.6B | 4B |
|---|---|---|
| unit rows | 161,989 | 161,989 |
| heading rows excluded | 28,568 | 28,568 |
| **transitorio rows excluded** | **21,344** | **21,344** |
| searched rows | 112,077 | 112,077 |
| identical winners shared by several instruments, not counted | 1,715 | 1,715 |
| **counted rows** | **110,362** | **110,362** |
| vector rows | 145,788 (1,158 owned by more than one instrument, once headings and transitorios are set aside) | same |
| instruments | 1,303 | 1,303 |
| `A.sum()` | **110,362.0** | **110,362.0** |
| every row sums to that instrument's counted rows | yes | yes |
| non-zero cells | 32,539 (was 38,073) | 30,213 (was 35,316) |
| searched rows with a tie | 864 (all two-way) | 842 (all two-way) |
| largest `m` | 14 (was 157) | 14 |
| mean similarity of the winner | 0.787 (0.783 over the counted rows) | 0.775 (0.771) |
| searched rows whose winner is an identical text (cosine 1) | 3,802: 2,087 with `m == 1`, which count, and 1,715 shared, which do not | the same |

The exclusion is independent of the model (the same 28,568 + 21,344 rows), and
so are the 1,715 shared identical winners: an identical text is an exact tie
under either. Counted rows fell by 9,779 for the 0.6B (120,141) and 9,665 for
the 4B (120,027), and the two models now count the same 110,362. The `m`
histogram over the searched rows:

| `m` | 1 | 2 | 3–5 | 6–20 | 21–100 | 101+ |
|---|---|---|---|---|---|---|
| 0.6B unit rows | 108,533 | 1,692 | 1,611 | 241 | 0 | 0 |
| 4B unit rows | 108,626 | 1,654 | 1,542 | 255 | 0 | 0 |

97 % of searched rows have a single answer. The boilerplate that used to fill
the last buckets (up to 157 owners) was the transitorios.

The four fits took 17.6, 7.1, 7.3 and 8.3 s (0.6B, 40.3 s with the write) and
16.3, 6.4, 7.4 and 8.5 s (4B, 38.6 s); the research pages are 1.2 MB each.
`website/pages/atlas/atlas.json` is **421,007 bytes** and `atlas-qwen3-4b.json`
**421,192**, both with `meta.transitorio_rows_excluded` 21,344 and no
`transitorio_near_identical_dropped`; `--instruments-as` accepted the 4B export
(the same 1,303 instruments in the same positions).

Pair explanations, 0.6B / 4B:

| | 0.6B | 4B |
|---|---|---|
| pairs | 6,288 | 6,265 |
| excerpts the pairs explain | 71,348 (weight 70,006.9) | 73,822 (weight 72,531.1) |
| excerpts per pair | median 4, p90 25, max 702 | median 4, p90 27, max 747 |
| JSON | 159.0 MB | 169.0 MB |
| tarball | 33.6 MB | 35.9 MB |
| largest file by size | `pairs/1122-1059.json` (1.19 MB, 84 rows) | `pairs/1122-1059.json` (1.19 MB, 86 rows) |
| export + install | 31 s + tarball 48 s | 49 s + tarball 65 s |

How much the rule moved the picture, against the committed files of the
previous build (`git show master:website/pages/atlas/atlas.json` and
`atlas-qwen3-4b.json`; same instruments, same positions): the five closest
instruments (`out`) share 4.37 of 5 on average for the 0.6B (4.41 for the 4B),
one instrument shares none in either model, and the closest one is unchanged
for **1,239** of the 1,303 (0.6B) and **1,240** (4B). The two models now agree
on the closest instrument for 1,077 (they did on 1,069) and share 3.30 of 5 on
average (3.4). The instruments' total incoming weight fell most where the
transitorios had pointed: the Constitution's `in` went from 680.9 to 246.0
(0.6B) and from 799.6 to 384.0 (4B).

The Constitution (`cpeum`) and the Chapingo law (`luach`), `out` under each
model — weights first, then the instrument:

| | 0.6B | 4B |
|---|---|---|
| `cpeum` | 15.0 LEY General de Instituciones y Procedimientos Electorales · 9.0 LEY Orgánica del Poder Judicial de la Federación · 8.0 LEY Orgánica del Congreso General de los Estados Unidos Mexicanos · 4.5 REGLAMENTO DEL SENADO DE LA REPUBLICA · 4.0 ESTATUTO de Gobierno del Distrito Federal | 16.0 LEY General de Instituciones y Procedimientos Electorales · 9.0 LEY Orgánica del Congreso General de los Estados Unidos Mexicanos · 9.0 LEY Orgánica del Poder Judicial de la Federación · 6.0 ESTATUTO de Gobierno del Distrito Federal · 3.0 CÓDIGO Civil Federal |
| `luach` | 10.0 LEY Orgánica de la Universidad Autónoma Metropolitana · 7.0 LEY Orgánica de la Universidad Autónoma Agraria Antonio Narro · 1.0 LEY Orgánica del Instituto Politécnico Nacional | 9.0 LEY Orgánica de la Universidad Autónoma Metropolitana · 6.0 LEY Orgánica de la Universidad Autónoma Agraria Antonio Narro · 1.0 LEY General de Educación · 1.0 LEY Monetaria de los Estados Unidos Mexicanos · 1.0 LEY Orgánica de la Universidad Nacional Autónoma de México |

The Constitution has 1,328 excerpts and 137 count (136 articles and the
preamble); the pair behind its heaviest weight, `cpeum` → `lgipe`, lists 15
excerpts, each a whole 1, from article 96 at similarity 0.861 (to article
494) down to article 125 at 0.606 (to article 11). The Chapingo law has 28
excerpts and 18 count, all answered by a single instrument each (`m == 1`):
10 to the UAM, 7 to Antonio Narro, 1 to the IPN. The page's worked examples
(`atlas.qmd`) were rewritten from these two files and the installed pair
files; the Constitution's old example, 38 transitory articles matched to the
statute's transitory articles, no longer exists under the rule.

#### Counted excerpts on the page (issue #271, measured 2026-10-05)

The three committed files were regenerated from the three local work
directories with the commands above (a pure read: no refit, pair files and
`atlas-pairs` release untouched, so no publish step) and gained `pc`:
`sum(pc)` is 110,362 (0.6B), 110,362 (4B) and 110,000 (BM25), `cpeum` is 137 in
all three, the smallest `pc` is 1, the median 48 and the largest 3,063 against
a largest `p` of 3,589. `c`/`k`/`n`/`p`, `in`/`out`/`inc` and the projections
are byte-identical to before; only `pc`, `meta.generated`, `meta.commit` and —
for the two embedding files, committed before #267 — `meta.method` changed. A
circle's area is now proportional to `pc`, so the Constitution (137 of 1,328)
is a small circle and more than half of the instruments sit on the 2.5 px
floor; switching to BM25 resizes the 197 circles whose `pc` differs.

The hand-offs this run wrote are `emb-run-atlas/atlas-pairs/PUBLICAR.md` and
`emb-run-atlas-4b/atlas-pairs/PUBLICAR.md`; nothing was uploaded.

### Measured, BM25, 2026-10-01 (issue #267)

`emb-run-atlas-bm25/`, prepared from `emb-run-atlas/` on the login node in
**16.3 s** (tokenising 110,185 texts 7.2 s, building the index 5.4 s; 1.6 GB
peak; `bm25-index/` 59 MB, `tokens.parquet` 21 MB): 63,694 terms, **0 documents
without a term**. The matrix, one job (42489) on `geoint1` (62 threads,
`--exclude=geoint0`), beside the 0.6B's (issue #265):

| phase | 0.6B (seconds) | BM25 (seconds) |
|---|---|---|
| load (the join + the scorer) | 6.4 | 5.3 |
| normalise | 0.3 | — |
| sweep (112,077 searched rows against 145,788 texts, blocked) | 102.8 | 419.9 |
| write | 0.2 | 0.2 |
| **total** | **110.0** | **425.7** |
| peak RSS | 1.98 GB | 2.4 GB |

| what | 0.6B | BM25 |
|---|---|---|
| unit rows | 161,989 | 161,989 |
| heading rows excluded | 28,568 | 28,568 |
| transitorio rows excluded | 21,344 | 21,344 |
| searched rows | 112,077 | 112,077 |
| identical winners shared by several instruments, not counted | 1,715 | 2,077 |
| rows with no foreign match (`no_match`) | — | 0 |
| **counted rows** | **110,362** | **110,000** |
| non-zero cells | 32,539 | 33,199 |
| searched rows with a tie | 864 | 4,487 (2,790 two-way, 1,241 three to five, 456 six or more) |
| largest `m` | 14 | 16 |
| `m == 1` | 108,533 | 106,933 |
| `row_sums_equal_counted_rows` | yes | yes |

BM25 ties far more often than the embeddings (4,487 rows against 864): a short
text made of a few common words has many equally scoring neighbours, where a
dense vector almost never does. The four UMAP fits took 14.2, 5.4, 6.2 and 7.0 s
(32.8 s in all); `output/umap-instruments-bm25.html` is 1.2 MB and its footer
says `method bm25`. `website/pages/atlas/atlas-bm25.json` is **421,725 bytes**
(`atlas.json`: 421,007), accepted by `--instruments-as` (the same 1,303
instruments in the same positions).

Pair explanations: **6,289 pairs** (the 0.6B has 6,288), 72,931 excerpts,
173.5 MB of JSON, largest file `pairs/1122-1059.json` (92 rows, 1.27 MB), the
tarball `atlas-pairs-bm25.tar.gz` **36.3 MB**, 29 s; every file's `sum(1/m)`
equals `matrix[i, j]` to 1e-3 (the export asserts it).

How much the score changes the picture, over the 1,303 instruments: the closest
instrument is the same as the 0.6B's for **906** and as the 4B's for 933; the
five closest share 2.64 of 5 with the 0.6B on average, and 32 instruments share
none. The Constitution's total incoming weight (`in`) is 346.0 (0.6B: 246.0).

The Constitution (`cpeum`) and the Chapingo law (`luach`), `out` under each
score — weights first, then the instrument:

| | 0.6B | BM25 |
|---|---|---|
| `cpeum` | 15.0 LEY General de Instituciones y Procedimientos Electorales · 9.0 LEY Orgánica del Poder Judicial de la Federación · 8.0 LEY Orgánica del Congreso General de los Estados Unidos Mexicanos · 4.5 REGLAMENTO DEL SENADO DE LA REPUBLICA · 4.0 ESTATUTO de Gobierno del Distrito Federal | 17.0 ESTATUTO de Gobierno del Distrito Federal · 12.0 LEY General de Instituciones y Procedimientos Electorales · 8.0 LEY Orgánica del Poder Judicial de la Federación · 6.0 LEY Orgánica del Congreso General de los Estados Unidos Mexicanos · 4.0 LEY Federal de Responsabilidades de los Servidores Públicos |
| `luach` | 10.0 LEY Orgánica de la Universidad Autónoma Metropolitana · 7.0 LEY Orgánica de la Universidad Autónoma Agraria Antonio Narro · 1.0 LEY Orgánica del Instituto Politécnico Nacional | 12.0 LEY Orgánica de la Universidad Autónoma Metropolitana · 2.0 LEY Orgánica de la Universidad Autónoma Agraria Antonio Narro · 2.0 REGLAMENTO INTERIOR DEL COLEGIO DE POSTGRADUADOS · 1.0 LEY Orgánica del Instituto Politécnico Nacional · 1.0 REGLAMENTO DE FUNCIONAMIENTO DE LOS CONCURSOS PROTOUCH INICIAL DE PRONOSTICOS PARA LA ASISTENCIA PUBLICA |

The hand-off this run wrote is `emb-run-atlas-bm25/atlas-pairs/PUBLICAR.md`;
nothing was uploaded.

### Measured, `scjn-tratados`, 2026-10-11 (issue #280)

The fourth collection, `md2akn` 0.5.0 (rule 10, issue #279), `--no-split-articles`,
both models, `emb-run-tratados/` (gitignored), `--num-shards 6`:

| | |
|---|---|
| Instruments with text | 1,373 (82 more have no text and no asset) |
| Units | 41,335 — article 31,493 · loose 3,196 · preamble 2,781 · heading 2,604 · conclusions 1,261 |
| Distinct texts | 39,668 (701 shared by several treaties) |
| `instruments_by_numbering` | `articulo` 1,324 · `numeral` 45 · `ordinal` 4 (rule 8: `numeral` was 279 before rule 10 read the roman openers) |
| Over-cap units | 1, unsplittable (the longest article is 407,348 characters) |
| Shards | 6 per model, 0 failed |
| Truncated texts | 10 in each model, 41,360 to 115,193 tokens; `max_tokens_embedded` 32,768 (0.6B) and 40,960 (4B) |
| GPU time per shard | 54–63 s (0.6B), 150–164 s (4B); three A100 shards at a time, ~7 min (0.6B) and ~15 min (4B) wall with the queue |
| Size | 65 MB (0.6B) + 151 MB (4B) of per-instrument and shared vectors, 12 MB of `units.parquet` |
| Assets | 2,753 (2,748 vector files + 5 metadata) — 995 + 1,000 + 753 vector files as three parts, the shape of `scjn-reglamentos-vectors` |

No vector has a non-finite value. Nothing was published: the generated
`emb-run-tratados/publish/PUBLICAR.md` is for a human (issue #115, Hallazgo C).

### Measured, reglamentos under rule 10, 2026-10-11 (issue #280)

The published directory `emb-run-whole-reglamentos/` was copied to
`emb-run-whole-reglamentos-r10/` and rebuilt with `md2akn` 0.5.0: 110,670 units
and 100,852 distinct texts again, 30 of them new (the roman articles of
`id_ordenamiento` 182464) — `plan_shards.py --against` excluded 100,852 for
both models and numbered the single new shard 30. Compared with the published
merge by `pyarrow.Table.equals`, across 1,083 files per model, **only
`vectors-182464-qwen3-0.6b-1024.parquet` and `vectors-182464-qwen3-4b-2560.parquet`
differ**; every other per-instrument file and both shared files are equal.
`package_vectors.py` reports no stale asset (every instrument keeps its file
name) and the same 995 + 1,000 + 171 partition.

### Measured, with the treaties, 2026-10-12 (issue #283)

The three data sets rebuilt **in place** (`emb-run-atlas/`, `-4b/`, `-bm25/`) with
`tratados` as the fourth, last collection, at commit `84a0cb6d`. Inputs: the four
`legalvec` releases at `legalvec` 0.2.0 — `scjn-tratados-vectors` downloaded
whole (2,753 assets, 222.8 MB, SHA256SUMS checked, 0 `.partial`), and
`scjn-reglamentos-vectors` refreshed for instrument `182464` and the metadata
(`legalvec download --collection reglamentos --key 182464 --refresh`, 9 assets);
its `corpus-manifest.json` now says `md2akn_version` `0.5.0`, `split_articles`
`false` (the other two cached releases stay at 0.4.0, proved byte-equal under
rule 10 in issue #280). Instruments: 315 laws + 863 regulations + 125 guidelines
+ **1,373 treaties = 2,676**, `unique-instruments.json` has `tratados` with
`before` 1,373, `dropped` 0.

| | 0.6B | 4B | BM25 |
|---|---|---|---|
| prepare (login node) | 241.8 s | 531.1 s | 22.8 s (143,301 documents) |
| matrix job (`geoint`) | 182.2 s (job 43088, 2.36 GB) | 309.0 s (43092, 4.02 GB) | 719.0 s (43089, 3.14 GB) |
| unit rows | 203,324 | 203,324 | 203,324 |
| heading rows excluded | 31,172 | 31,172 | 31,172 |
| transitorio rows excluded | 26,182 | 26,182 | 26,182 |
| searched rows | 145,970 | 145,970 | 145,970 |
| identical winners shared by several instruments, not counted | 1,947 | 1,947 | 2,308 |
| **counted rows** (`A.sum()`) | **144,023** | **144,023** | **143,661** |
| vector rows | 185,456 | 185,456 | 185,456 |
| row sums equal counted rows | yes | yes | yes |
| non-zero cells | 46,819 | 44,045 | 47,429 |
| searched rows with a tie | 889 | 853 | 5,348 |
| largest `m` | 14 | 14 | 16 |
| four UMAP fits | 116.6 s | 116.2 s | 118.1 s |

The 0.6B and the 4B again count the same rows. `atlas.json`,
`atlas-qwen3-4b.json` and `atlas-bm25.json` (about 0.9 MB each) list the same
2,676 instruments in the same positions (`--instruments-as` accepted both
others), `meta.collections` is `leyes` 315, `reglamentos` 863, `lineamientos`
125, `tratados` 1,373, `meta.sources` names eight releases, and
`meta.duplicates_dropped` lists only `reglamentos` and `lineamientos`. The pair
sets, installed into the gitignored `website/pages/atlas/pairs*/`: 12,002
(215.0 MB of JSON, 45.7 MB compressed, 95,141 excerpt rows), 11,983 (227.6 MB,
48.5 MB, 98,001) and 12,074 (235.8 MB, 49.5 MB, 97,457) files.

How the treaties changed the picture, 0.6B (4B in parentheses, BM25 after): the
closest instrument is unchanged for **1,275 (1,277; 1,209)** of the 1,303 earlier
instruments; **13 (15; 14)** of them now have a treaty as their closest
instrument, and **1,284 (1,304; 1,326)** of the 1,373 treaties have a treaty as
theirs. The three data sets agree on the closest instrument for 1,971 (0.6B and
4B), 1,557 (0.6B and BM25) and 1,610 (4B and BM25) of the 2,676. The 1,373
treaties sit apart on the map: the mean `(x, y)` of the 0.6B's 16-neighbour layout
is (0.31, 0.64) for treaties against (0.43, 0.34), (0.47, 0.29) and (0.57, 0.33)
for laws, regulations and guidelines.

Chapingo under the 0.6B: 28 excerpts, 18 counted, 10 to the UAM, 6 to Antonio
Narro, 1 to the IPN and 1 to the *CONVENIO INTERNACIONAL PARA EL ESTABLECIMIENTO
DE LA UNIVERSIDAD PARA LA PAZ…*; 21 excerpts of 8 other instruments point at it
(7 Antonio Narro, 7 UAM). The Constitution: 137 counted, closest the LGIPE at
14.0 (14 excerpts, article 96 at 0.86 the closest, article 58 at 0.67 the
weakest), then the LOPJF 9.0, the LOCGEUM 8.0 and, new, the *CONVENCION AMERICANA
SOBRE DERECHOS HUMANOS* (5.0). The page's two worked examples follow these
numbers.

### Measured, strong links, with the treaties, 2026-10-12 (issue #283)

`gold_links.py`, `evaluate_links.py` and `evaluate_citations.py` rerun on the
rebuilt directories, outputs in `emb-run-atlas/gold-links/` (the 2026-10-05
outputs were kept aside and are the "before" below). **The gold did not change**:
the sources are still the 863 reglamentos and 125 lineamientos, treaties are
extra candidates only, and every gold number repeats — A 132 (131 + 1), B 160
(153 + 7), A union B 212 (204 + 8), 227 links, 55 unresolved names, signal C
3,647 hits in 2,537 rows of 832 instruments citing 179 laws, 2,010 citing exactly
one; `_gold-links.md` is byte-identical. Only the candidates grew, from 1,302
foreign instruments to 2,675.

| gold | metric | 0.6B | 4B | BM25 |
|---|---|---|---|---|
| A (132) | closest is a gold law | 99 (0.750) | 96 (0.727) | 102 (0.773) |
| A (132) | a gold law among the five closest | 122 (0.924) | 122 (0.924) | 125 (0.947) |
| A (132) | mean reciprocal rank | 0.829 | 0.819 | 0.849 |
| A (132) | weight share on gold laws | 0.367 | 0.392 | 0.363 |
| A union B (212) | closest is a gold law | 125 (0.590) | 122 (0.575) | 126 (0.594) |
| A union B (212) | a gold law among the five closest | 179 (0.844) | 177 (0.835) | 179 (0.844) |
| A union B (212) | mean reciprocal rank | 0.697 | 0.690 | 0.700 |
| A union B (212) | weight share on gold laws | 0.278 | 0.296 | 0.274 |

"Closest" ties with a non-gold instrument: 2 / 0 / 1 (A) and 4 / 1 / 3 (A union
B). Verdicts (difference of the first minus the second; intervals and McNemar
p-values in `evaluation.md`):

| pair | closest | top-5 | MRR | weight share |
|---|---|---|---|---|
| 0.6B vs 4B, A | tie (+0.023) | tie (0.000) | tie (+0.010) | **4B better** (-0.025) |
| 0.6B vs BM25, A | tie (-0.023) | tie (-0.023) | tie (-0.019) | tie |
| 4B vs BM25, A | tie (-0.045) | tie (-0.023) | **BM25 better** (-0.029) | **4B better** (+0.029) |
| 0.6B vs 4B, A union B | tie (+0.014) | tie (+0.009) | tie (+0.007) | **4B better** (-0.018) |
| 0.6B vs BM25, A union B | tie (-0.005) | tie (0.000) | tie (-0.003) | tie |
| 4B vs BM25, A union B | tie (-0.019) | tie (-0.009) | tie (-0.011) | **4B better** (+0.022) |

**Reading.** The conclusion of 2026-10-05 stands: on the closest and the five
closest instruments the three data sets are indistinguishable, the 4B puts more
weight on the gold law, and on A alone BM25's reciprocal rank beats the 4B's.
Adding 1,373 candidates moved each count by one to three instruments (0.6B A:
100 → 99 closest, 123 → 122 top-5) and not one verdict changed except that the
4B-versus-BM25 *closest* p-value on A is 0.07 (it was 0.18).

### Measured, row-level citations, with the treaties, 2026-10-12 (issue #283)

`evaluate_citations.py` over the same 2,003 scored rows (7 removed as
word-for-word shared by several instruments in each model, 0 where the cited law
owns a copy), the cited law now ranked among **2,675** foreign instruments:

| metric | 0.6B | 4B | BM25 |
|---|---|---|---|
| recall@1 | 190 (0.095) | 202 (0.101) | 177 (0.088) |
| recall@5 | 500 (0.250) | 536 (0.268) | 482 (0.241) |
| recall@10 | 692 (0.345) | 743 (0.371) | 684 (0.341) |
| MRR | 0.178 | 0.190 | 0.171 |
| median rank | 30 | 22 | 30 |
| recall@5, without the Constitution (1,444 rows) | 0.332 | 0.355 | 0.321 |
| MRR, without the Constitution | 0.235 | 0.247 | 0.228 |
| median rank, without the Constitution | 14 | 11 | 13 |

| pair | cited law first | among the five | among the ten | MRR | median rank |
|---|---|---|---|---|---|
| 0.6B − 4B | tie (p = 0.28) | 4B better (p = 0.003) | 4B better (p < 0.001) | 4B better ([−0.019, −0.005]) | 4B better ([+5, +10]) |
| 0.6B − BM25 | tie (p = 0.35) | tie (p = 0.34) | tie (p = 0.71) | tie ([−0.004, +0.017]) | tie ([−7, +4]) |
| 4B − BM25 | tie (p = 0.052) | 4B better (p = 0.002) | 4B better (p = 0.002) | 4B better ([+0.009, +0.029]) | 4B better ([−15, −3]) |

By collection (recall@5 for 0.6B / 4B / BM25): lineamientos (172 rows) 0.302 /
0.308 / 0.238, reglamentos (1,831 rows) 0.245 / 0.264 / 0.241. Among the ten most
cited laws the extremes are the *Código Fiscal de la Federación* (57 rows, BM25
0.684 against 0.404 for the 4B and 0.316 for the 0.6B) and the *Ley Orgánica del
Poder Judicial de la Federación* (41 rows, 0.171 BM25 against 0.439 for the 4B
and 0.488 for the 0.6B). **Reading:** unchanged — the 4B is better than both
others at ranking a cited law among the candidates, the 0.6B and BM25 are tied,
the absolute level is low and falls with the Constitution included. Every
recall moved by at most 0.01 and the median rank by one or two places (29 → 30,
21 → 22) with 1,373 more candidates, which is how little a treaty competes
with the law an excerpt cites.

### Measured, strong links between any two instruments, 2026-10-12 (issue #283, review fix-1)

**What changed and why.** Until here a strong link had a fixed shape: the source
was a reglamento or a lineamiento and the target one of the 315 laws, treaties
only ever being extra candidates. That was a restriction of the first gold, not
a property of the data: a law can be *reglamentaria* of an article of the
Constitution, a treaty can implement an earlier treaty, a law can approve a
convention. `gold_links.py` now takes **every one of the 2,676 instruments as a
source and every one as a target**, whatever collection each belongs to, under
one rule, **an instrument is never linked to itself** (`summary.self_links` is
0, and `test_the_real_gold_never_links_an_instrument_to_itself` pins it on the
real data). The three scripts changed together (`gold_links.py`,
`evaluate_links.py`, `evaluate_citations.py`); matrices, `atlas*.json`, pair
files, vectors and the `atlas-pairs` release are untouched. Everything ran
offline (the gold takes ~32 s), no Slurm. The
2026-10-05 and the first 2026-10-12 sections above stay as they were and are the
"before".

**Rules, each an incident of the first run on the real data.**

* *Names.* The folded names of all 2,676 instruments form one alternation,
  longest first, compiled as a radix-compressed trie (a character-level trie hit
  `RecursionError`). A folded name that several instruments share and that is
  not the source's own would be *ambiguous* and give no link; none is
  (`summary.ambiguous` is 0).
* *Own name.* A source never links to an instrument whose folded name is one of
  its own: the two `LEY de Amnistía`, and the three regulations the corpus holds
  once as a law and once as a reglamento (`REGLAMENTO DE LA CAMARA DE DIPUTADOS`,
  `REGLAMENTO DEL SENADO DE LA REPUBLICA`, `REGLAMENTO PARA EL GOBIERNO INTERIOR
  DEL CONGRESO GENERAL…`) are evidently the same instrument filed under two
  collections, not a link between two instruments. These are the **three links of the old gold that are gone**:
  each of the three reglamentos used to link to its twin in `leyes` by signal A.
  Every other one of the 227 old links is still there (checked row by row
  against `emb-run-atlas/gold-links/links.md` of 2026-10-05).
* *Prefix siblings.* A name that matches at position 0 without being the whole
  source name is a sibling or an extension (`REGLAMENTO DE LA LEY DE NAVEGACION Y
  COMERCIO MARITIMOS` begins with, and contains, `REGLAMENTO DE LA LEY DE
  NAVEGACION`), so it is banned and the search repeats; without this rule a
  treaty named after an earlier one linked to it by an accident of prefix and,
  once, a reglamento lost its true law.
* *Treaty aliases.* A treaty is cited by a short name, so a treaty name of **five
  words or more** also answers to itself without its trailing parenthetical
  qualifier (`(MULTILATERAL, 1974)`) and cut at its first comma. Shorter names
  get no alias: generic short forms produced false treaty-to-treaty links on the
  real data.
* *Self-mentions in B and C.* An "objeto" sentence or a citation naming the
  source's own name is not a link; citations of an instrument to itself are
  counted apart (`citations.self_citations`, 258) and are not in
  `citations.parquet`.
* *Unresolved* is now **62** (it was 55): the name says `ley`, `codigo`… and
  resolves to nothing among the 2,676 instruments; counted and reported, never
  gold and never a negative, as before.

**The gold.** Signal A 161 instruments (10 laws, 130 reglamentos, 2 lineamientos,
19 tratados), B 170 (3, 158, 7, 2), A union B **251** (13, 208, 9, 21), A and B
80 (all reglamentos), B only 90, 19 instruments whose sentence names several
targets; **275 links** (A 161, B 194, B only 114), `B_links_without_evidence` 0.
Before: 132 / 160 / 212 / 227. Links by source -> target collection (links,
distinct sources):

| source -> target | links | sources |
|---|---|---|
| reglamentos -> leyes | 215 | 201 |
| tratados -> tratados | 23 | 21 |
| leyes -> leyes | 12 | 11 |
| lineamientos -> leyes | 9 | 8 |
| reglamentos -> reglamentos | 6 | 6 |
| reglamentos -> tratados | 5 | 3 |
| lineamientos -> reglamentos | 3 | 3 |
| leyes -> tratados | 2 | 2 |

Eight of the sixteen possible source -> target pairs occur; none ends in
`lineamientos`, and a treaty is the source only of links to treaties.
`_gold-links.md` now has the header `| Instrument
| Links to | Signal |` and 275 rows.

**Instrument level** (`evaluation.md`; the metric labels say *gold instrument*
now):

| gold | metric | 0.6B | 4B | BM25 |
|---|---|---|---|---|
| A (161) | closest is a gold instrument | 102 (0.634) | 98 (0.609) | 101 (0.627) |
| A (161) | a gold instrument among the five closest | 130 (0.807) | 132 (0.820) | 132 (0.820) |
| A (161) | mean reciprocal rank | 0.713 | 0.707 | 0.714 |
| A (161) | weight share on gold instruments | 0.307 | 0.322 | 0.293 |
| A union B (251) | closest is a gold instrument | 130 (0.518) | 127 (0.506) | 129 (0.514) |
| A union B (251) | a gold instrument among the five closest | 195 (0.777) | 194 (0.773) | 193 (0.769) |
| A union B (251) | mean reciprocal rank | 0.629 | 0.625 | 0.625 |
| A union B (251) | weight share on gold instruments | 0.246 | 0.259 | 0.237 |

"Closest" ties with a non-gold instrument: 5 / 0 / 1 (A) and 7 / 2 / 3 (A union
B). **Every pair is a tie on the closest, the five closest and the MRR**, on both
gold standards; the only verdicts are the weight share (4B better than the 0.6B,
-0.015 on A and -0.013 on A union B, and better than BM25, +0.029 and +0.022; the
0.6B and BM25 tied). The first gold's one other verdict, BM25's MRR above the
4B's on A alone, **does not survive** (tie, [-0.035, +0.020]).

By collection of the source (A union B; closest / top-5, count of sources, for
0.6B / 4B / BM25), counts and means only, no pairwise test (a source count of 9
or 13 cannot carry one): reglamentos (208) 122 / 119 / 122 and 175 / 174 / 174;
tratados (21) 5 / 3 / 3 and 8 / 8 / 6; leyes (13) 0 / 2 / 1 and 4 / 6 / 6;
lineamientos (9) 3 / 3 / 3 and 8 / 6 / 7. The links that start in a law or a
treaty are the hard ones and the three methods disagree among themselves on
them, which is the sampling noise of a handful of sources.

**Row level (signal C)** (`evaluation-rows.md`): 7,066 citations (258 more of an
instrument to itself set aside) in 5,218 distinct rows of 1,925 instruments,
citing 316 instruments, 6,042 (row, instrument) pairs; 4,497 rows cite exactly
one instrument, 721 cite several (not scored); 29 removed as word-for-word shared
by several instruments (the same in every model), **4,468 scored**, 0 where the
cited instrument owns a copy, ranked among 2,675 foreign instruments. Citation
pairs by source -> target (pairs of a row and an instrument): leyes -> leyes
1,822, reglamentos -> leyes 2,892, tratados -> leyes 863, lineamientos -> leyes
209, reglamentos -> reglamentos 157, lineamientos -> reglamentos 37, tratados ->
tratados 37, tratados -> reglamentos 11, leyes -> reglamentos 8,
reglamentos -> tratados 4, leyes -> tratados 2.

| metric | 0.6B | 4B | BM25 |
|---|---|---|---|
| recall@1 | 414 (0.093) | 483 (0.108) | 448 (0.100) |
| recall@5 | 1,108 (0.248) | 1,244 (0.278) | 1,138 (0.255) |
| recall@10 | 1,494 (0.334) | 1,621 (0.363) | 1,524 (0.341) |
| MRR | 0.174 | 0.195 | 0.180 |
| median rank | 39 | 30 | 40 |
| recall@5, without the Constitution (2,534 rows) | 0.387 | 0.423 | 0.409 |
| MRR, without the Constitution | 0.271 | 0.294 | 0.289 |
| median rank, without the Constitution | 10 | 8 | 9 |

| pair | cited instrument first | among the five | among the ten | MRR | median rank |
|---|---|---|---|---|---|
| 0.6B − 4B | 4B better (p < 0.001) | 4B better (p < 0.001) | 4B better (p < 0.001) | 4B better ([−0.027, −0.016]) | 4B better ([+6, +11]) |
| 0.6B − BM25 | tie (p = 0.09) | tie (p = 0.27) | tie (p = 0.29) | tie ([−0.013, +0.001]) | tie ([−5, +3]) |
| 4B − BM25 | tie (p = 0.08) | 4B better (p < 0.001) | 4B better (p < 0.001) | 4B better ([+0.008, +0.022]) | 4B better ([−14, −6]) |

Recall@5 (0.6B / 4B / BM25) by the collection of the citing row: leyes (1,528)
0.346 / 0.407 / 0.379, reglamentos (1,901) 0.262 / 0.283 / 0.258, lineamientos
(185) 0.319 / 0.346 / 0.281, **tratados (854) 0.027 / 0.023 / 0.019** (median
rank 1,421.5 / 1,613 / 2,119.5): a treaty that cites a Mexican law does it in
passing, inside an agreement about something else, and no method finds the law
(the explanation is a hypothesis, not tested). Among the ten most cited
instruments (recall@5) BM25 is highest on the *Código Fiscal de la Federación*
(172 rows: 0.797 against 0.442 for the 4B and 0.384 for the 0.6B) and lowest on
the Constitution (1,934 rows: 0.052 against 0.088 and 0.066); the 4B is
ahead on the *Ley General de Sociedades Mercantiles* (102 rows: 0.529 against
0.373 and 0.412).

**Reading.** The conclusions of 2026-10-05 and of the first 2026-10-12 section
stand, with one change: now the 4B is better than the 0.6B on *all five*
row-level measures (the 0.6B-versus-4B "cited law first" was a tie) and than BM25
on four, with a larger row set (4,468 against 2,003); on the closest instrument and the
five closest the three data sets remain statistically indistinguishable, and
the 4B alone puts more weight on the gold instrument. The Ley Orgánica del Poder
Judicial de la Federación is no longer among the ten most cited instruments.
Nothing here changes which data set the page opens on.

### Measured, BM25 tuning, 2026-10-01 (issue #267, review fix)

Question: does a better BM25 configuration move the lexical baseline toward the
Qwen3-Embedding-4B's result? **Objective** (decided before running, and the only
thing optimised): *row-level winner agreement* with the 4B -- a searched row is
a **hit** when the set of instruments owning BM25's tied winners intersects the
set the 4B's `nearest.parquet` records for that row; `hit_rate` is hits over the
rows the 4B counted (a row with no BM25 match is a miss). Reported beside it:
`jaccard` (mean Jaccard of the two owner sets), `closest` (instruments, of 1,303,
whose closest instrument is the 4B's), `top5` (mean size of the intersection of
the two top-five lists), `tie_rows`, `id_shared` (`identical_shared_dropped`),
`no_match`, `mean_m`. **Space**: `k1` {0.5, 0.9, 1.2, 1.5, 2.0, 3.0} x `b` {0,
0.25, 0.5, 0.75, 1.0} x method {lucene, robertson, atire, bm25l, bm25+} x query
{binary, tf-saturated} = 300; not the tokeniser, tie tolerance, identity rule,
mask or `1/m`. **Cost**: the grid on a stratified (by collection) 10 % sample --
11,208 of the 112,077 searched unit rows, seed 0 -- in two `geoint` jobs
(42494, 42495; 28 processes each, ~6 minutes in all, median 61 s per
configuration, weights recomputed from `tokens.parquet` with no re-tokenising and
no index rebuild); then the top 3 by sample `hit_rate` plus the defaults as the
control, **full sweeps**, one job each (42496-42499, ~7 minutes each).

Checks the numbers rest on: recomputed weights equal bm25s' saved index for all
five methods (a test); a sweep over a sample gives the rows the full run gives
(identical targets for all 11,208 sampled rows of the defaults); the control's
full `matrix.npy` is byte-equal to the committed data set's (`instrument-matrix/`
of 2026-10-01), so the 933 below is the same 933 as before. A first draft of the
sample sweep masked only the *sampled* rows of a source's own texts, so its own
unsampled texts won against it (40 % `no_match`); the mask now covers all of the
source's searched rows (`own_rows`), and a test pins it.

Sample grid, best ten of 300 (the defaults are 86th, 0.4635):

| method | k1 | b | query | hit_rate | jaccard | tie_rows | no_match |
|---|---|---|---|---|---|---|---|
| robertson | 0.9 | 1 | tf-saturated | 0.4773 | 0.4602 | 589 | 5 |
| robertson | 1.2 | 1 | tf-saturated | 0.4762 | 0.4590 | 607 | 5 |
| lucene | 0.9 | 1 | tf-saturated | 0.4760 | 0.4614 | 549 | 0 |
| atire | 0.9 | 1 | tf-saturated | 0.4760 | 0.4614 | 549 | 0 |
| bm25+ | 0.9 | 1 | tf-saturated | 0.4760 | 0.4614 | 549 | 0 |
| lucene | 1.2 | 1 | tf-saturated | 0.4758 | 0.4610 | 564 | 0 |
| atire | 1.2 | 1 | tf-saturated | 0.4758 | 0.4610 | 565 | 0 |
| bm25+ | 1.2 | 1 | tf-saturated | 0.4758 | 0.4610 | 565 | 0 |
| robertson | 2 | 0.75 | tf-saturated | 0.4757 | 0.4620 | 481 | 5 |
| bm25l | 2 | 0.75 | tf-saturated | 0.4752 | 0.4633 | 461 | 0 |

What the grid says: the best configuration of every method is within 0.002 of
the others (robertson 0.4773, lucene/atire/bm25+ 0.4760, bm25l 0.4752); `b` is
what matters (best `hit_rate` 0.248 at `b` 0, 0.397 at 0.25, 0.452 at 0.5, 0.476
at 0.75, 0.477 at 1), `k1` hardly (0.473 to 0.477 across all six values); the
best binary configuration (robertson, `k1` 0.9, `b` 1) reaches 0.4713, the
saturated query adds about 0.006. `lucene`, `atire` and `bm25+` coincide where
they should (the constant `bm25+` adds to a query term is dropped, and does not
change a ranking).

Full sweeps (112,077 rows, 109,000+ counted), the four configurations measured
against the 4B:

| method | k1 | b | query | hit_rate | jaccard | closest | top5 | tie_rows | id_shared | no_match |
|---|---|---|---|---|---|---|---|---|---|---|
| robertson | 1.2 | 1 | tf-saturated | 0.4760 | 0.4575 | **944** | 2.764 | 6,314 | 2,769 | 39 |
| robertson | 0.9 | 1 | tf-saturated | 0.4748 | 0.4565 | 939 | 2.759 | 6,117 | 2,769 | 39 |
| lucene | 0.9 | 1 | tf-saturated | 0.4730 | 0.4576 | 933 | 2.754 | 5,646 | 2,764 | 0 |
| lucene | 1.5 | 0.75 | binary (**defaults**) | 0.4593 | 0.4470 | **933** | 2.708 | 4,487 | 2,077 | 0 |

**Decision: the defaults stay.** The row-level `hit_rate` does rise, by 1.7
points (0.4593 to 0.4760), but the quantity the Atlas shows -- the closest
instrument of each of the 1,303 -- agrees with the 4B for 944 instruments at
best against the defaults' 933: **+11, 0.8 percentage points**, under the
materiality bar fixed beforehand (+2 points, 26 instruments). Two of the three
winners do not move it at all or by 6. The mean top-five overlap with the 4B
moves from 2.708 to 2.764 of 5. The better row-level scores also tie more (6,314
rows against 4,487) and drop more shared word-for-word rows (2,769 against
2,077): part of the hit rate is bought by ties, where a row names several
instruments and one of them agrees. So `atlas-bm25.json`, the pair files, the
`atlas.qmd` prose and the `PUBLICAR.md` hand-off (`emb-run-atlas-bm25/atlas-pairs/`)
are exactly what #267/#268 produced, and nothing was regenerated or uploaded.


### Comparing the three data sets against strong links (issue #272)

*Review fix-1 of issue #283 (2026-10-12) generalised this: a source is any of the
2,676 instruments and a target any other one, never itself. The text below is
the design of issue #272 as it was written, and the dated section "Measured,
strong links between any two instruments" gives the current rules and numbers.*

Until here the only comparison between the 0.6B, the 4B and BM25 was
*agreement* (the closest instrument coincides for 1,077 of 1,303 between the two
embeddings, 933 and 906 for BM25), and `tune_bm25.py` even optimised BM25 toward
the 4B. Agreement measures consensus, not quality. This adds a gold standard
external to all three: a federal *reglamento* (and a few *lineamientos*) exists
to develop one specific law, and says so itself. A **strong link** is an
instrument (the *source*, a reglamento or lineamiento) -> a law (the *target*,
one of the 315 `leyes`), found by two offline signals over the 1,303 unique
instruments of `emb-run-atlas/instruments.parquet`:

* **A -- the name.** `REGLAMENTO DE LA LEY FEDERAL DEL TRABAJO` contains the
  name of a law. Names are folded (NFKD, accents, case, whitespace; punctuation
  kept), the 315 folded law names form one alternation, longest first, and a
  match nested inside a longer matched law name is dropped. A folded name two
  laws share (`LEY de Amnistía`: `lamn`, `lamni`) is *ambiguous* and gives no
  link (none of the 1,303 names hit it). A name that says `ley` or `codigo` and
  resolves to no law is `unresolved`: the law is not among the 315 (abrogated:
  *Ley Federal de Turismo*, *Ley Forestal*, ...). Unresolved names are counted
  and reported, never gold and never a negative -- no model can be right or
  wrong about a law that is not in the corpus. A law with a long name
  (`LEY de Amparo, Reglamentaria de los artículos 103 y 107 ...`) is found only
  by its full name; a reglamento that spells it shorter in its title is a miss
  of signal A (it can still be found by B).
* **B -- the "objeto" sentence.** "El presente Reglamento tiene por objeto
  reglamentar la Ley ...". Over the instrument's `article` and `loose` unit
  rows that are not in a transitorios section, the sentences (split on `.`
  followed by whitespace) in which an instrument word (`presente`/`este`/...
  `reglamento`/`ordenamiento`/`lineamientos`/...) is followed, within 120
  characters, by `tiene(n) por objeto` or `objeto reglamentar|regular|
  establecer|desarrollar`. The gold laws are the ones named **in that same
  sentence**, not anywhere in the unit. The evidence kept is the unit's `eId`
  and the sentence. **Tightened after reading `gold.md`:** the first version
  (the phrase anywhere in a non-heading, non-transitorio unit) gave 263
  instruments and its B-only links were full of false positives: a preamble's
  "con fundamento en los artículos ... de la Constitución" clause, the objeto of
  a *chapter* of a larger reglamento, a commission's own objeto ("la Comisión
  tiene por objeto"), and definitions articles whose one run-on sentence held the
  phrase far from any subject. It is why the subject is now required, why only
  `article` and `loose` units are read, and why the Constitution is never a
  signal-B target (about thirty regulations name it as the *basis* of an organ's
  powers; it stays a valid target of A). `gold.json` is never edited by hand.

Direction is source -> target only: `A` is directed by design and a law points
at many things, so the reverse is not a strong link. The gold of an instrument is
A union B; `laws_A` keeps A alone, which is the high-precision control. B agrees
with A where both speak: of the 80 instruments with both signals, B names the
law A names in all 80.

**Four metrics at the instrument level**, per gold instrument `i` with gold laws
`G` and row `A[i]` (`evaluate_links.py`): *closest* (a gold law is rank 1),
*top-5* (a gold law has rank <= 5), *reciprocal rank* of the best-ranked gold
law (0 when every gold law has weight 0), and *weight share*,
`sum(A[i, G]) / A[i].sum()`. Rank is the **competition rank** (1 + the instruments
with a strictly greater weight, tolerance 1e-6): equal weights are frequent in
BM25's rows and `argmax` would break them toward the lowest column, which is
always a law, so the optimistic rank is stated and used for every model, and the
table reports how many "closest" were a tie with a non-gold instrument. An
instrument whose row sums to 0 in any model is skipped in all (none was).

**Significance, fixed before the numbers were read.** For every pair of models and
every metric, a paired bootstrap over the gold instruments (seed 0, 10,000
resamples) gives the 95 % percentile interval of the mean difference; for
closest and top-5, McNemar's exact test (binomial on the discordant pairs,
`scipy.stats.binomtest`) is added. A difference is **real only if the interval
excludes 0 and, for a binary metric, McNemar's p < 0.05**; otherwise it is a tie.

```bash
uv run --group viz python scripts/embeddings/gold_links.py --work-dir emb-run-atlas
#   ~6 s: the unit texts come from legalvec's cache; writes emb-run-atlas/gold-links/gold.json, gold.md
uv run --group viz python scripts/embeddings/gold_links.py --report            # offline
uv run --group viz python scripts/embeddings/evaluate_links.py
#   the three default work directories (0.6B, 4B, BM25); writes evaluation.json, evaluation.md
uv run --group viz python scripts/embeddings/evaluate_links.py --report        # offline
```

`evaluate_links.py` refuses a work directory whose `instruments.parquet` is not
the gold's (`coleccion`, `clave` at every `i`) or whose `matrix.npy` is missing
or of the wrong shape, naming the path; it never rebuilds either. Nothing here
uses Slurm or the network, and `emb-run-atlas/gold-links/` is covered by the
`/emb-run*/` ignore rule. The gold is model-independent, so it lives in the
0.6B's directory by convention. Tests: `tests/test_gold_links.py`,
`tests/test_evaluate_links.py`.

`gold_links.py` also writes `links.md`, every one of the gold's 227 instrument -> law
links with the signal that found it (`A`, `B` or `A and B`), sorted by instrument
then law; `website/pages/atlas/_gold-links.md` is a copy the Atlas page includes in
a collapsed callout, regenerated by a human like `atlas.json`. The page reports one
test per measure, the half of the rule above that binds it: McNemar's exact test for
a yes-or-no measure and the bootstrap interval for the others. Every verdict in
`evaluation.md` and `evaluation-rows.md` is the same under that reading (the two
cases where the interval excluded zero but McNemar did not are ties here too).

### Measured, strong links, 2026-10-05 (issue #272)

Gold (`gold_links.py`, commit `7546baf2`): of the 988 reglamentos (863) and
lineamientos (125) of the Atlas, **132 instruments by name (A)** (131 reglamentos,
1 lineamiento), **160 by the objeto sentence (B)** (153 + 7), **212 in A union B**
(204 + 8), 80 in both, 80 by B only; 227 instrument -> law links (132 by A, 175
by B, 95 of them B-only), 12 instruments whose B names more than one law, 9 with
two gold laws and 3 with three. **55 unresolved names** (50 say `DE LA LEY`, 3 more name an abrogated law
otherwise, 2 name the *Código Federal de Instituciones y Procedimientos
Electorales*; 3 of the 55 are lineamientos), 0 ambiguous. Every B link carries an
`eId` and a sentence. The issue counted 53 unresolved names while planning, over
names saying `DE LA LEY ...`; the rule here (`ley` or `codigo` in the name, no
resolved law) is wider and gives 55. A loose first version of B had 263 instruments; reading ten B-only links
by eye is what tightened it (above).

Metrics, count (share) of the instruments scored, none skipped:

| gold | metric | 0.6B | 4B | BM25 |
|---|---|---|---|---|
| A (132) | closest is a gold law | 100 (0.758) | 96 (0.727) | 101 (0.765) |
| A (132) | a gold law among the five closest | 123 (0.932) | 122 (0.924) | 126 (0.955) |
| A (132) | mean reciprocal rank | 0.836 | 0.819 | 0.845 |
| A (132) | weight share on gold laws | 0.373 | 0.400 | 0.367 |
| A union B (212) | closest is a gold law | 126 (0.594) | 122 (0.575) | 126 (0.594) |
| A union B (212) | a gold law among the five closest | 180 (0.849) | 177 (0.835) | 181 (0.854) |
| A union B (212) | mean reciprocal rank | 0.701 | 0.690 | 0.700 |
| A union B (212) | weight share on gold laws | 0.283 | 0.303 | 0.279 |

"Closest" ties with a non-gold instrument: 2 / 0 / 0 (A) and 3 / 1 / 3 (A union
B) for 0.6B / 4B / BM25, so the competition rank gave BM25 and the 0.6B at most
three instruments each.

Verdicts under the rule above (difference of the first minus the second; the
full intervals and McNemar p-values are in `evaluation.md`):

| pair | closest | top-5 | MRR | weight share |
|---|---|---|---|---|
| 0.6B vs 4B, A | tie (+0.030) | tie (+0.008) | tie (+0.016) | **4B better** (-0.027) |
| 0.6B vs BM25, A | tie | tie | tie | tie |
| 4B vs BM25, A | tie | tie | **BM25 better** (-0.026) | **4B better** (+0.033) |
| 0.6B vs 4B, A union B | tie | tie | tie | **4B better** (-0.021) |
| 0.6B vs BM25, A union B | tie | tie | tie | tie |
| 4B vs BM25, A union B | tie | tie | tie | **4B better** (+0.024) |

**Reading.** On what the page shows -- the closest instrument and the five
closest -- **the three are indistinguishable**: all differences are a handful of
instruments (at most 5 of 132 on A, 4 of 212 on A union B), no McNemar test comes
near 0.05 (smallest 0.125), and the control (A alone) and the wider gold (A union
B) say the same thing. Three differences are real: the **4B puts a larger share
of each row's weight on the gold law** (0.400 against 0.373 and 0.367 on A; 0.303
against 0.283 and 0.279 on A union B), against both the 0.6B and BM25 -- the link
is more concentrated in the 4B, even though it is not more often first -- and,
on A alone, BM25's reciprocal rank is better than the 4B's (+0.026, interval
[0.002, 0.054], a small effect that does not survive on the wider gold). The 4B
is also the model BM25 was tuned toward, and is the one with the *fewest* gold
laws first: agreement with it was never evidence of quality. A lexical baseline
with no learned model is as good as either embedding at finding the law a
reglamento develops, which is what the page's BM25 option already suggested.

### Row level: explicit citations (issue #273)

#272 asks, per instrument, whether the law a reglamento develops is its closest
instrument. A unit row that cites "artículo 123 de la Ley Federal del Trabajo"
names the law it is about, so the same three data sets can be asked where that law
ranks among the 1,302 foreign instruments *for that one row*. `nearest.parquet`
keeps only a row's winner, which is why the answer needs the row's whole score
vector, and therefore a re-scoring pass.

**Signal C** (`gold_links.py`, `citations.parquet`). Over every unit row of a
reglamento or lineamiento that is neither a heading nor in a transitorios section,
on folded text (an ordinal's full stop, `1o.`, removed): `articulo(s) N[ bis|ter|...]`,
at most 80 characters with no `.` or `;` between, then `de la`, `del` or `de el` and
a law name (the same longest-first alternation as signal A). A gap that says
`reglamento` is a citation of the regulation behind the law, not of the law
("artículo 3 del Reglamento de la Ley Federal del Trabajo"), and is skipped;
a name two laws share resolves to nothing. One row of the table per `(source i,
text_sha1, cited law)`, with the first article and matched span as evidence,
`hits` and `single_law` (the text cites exactly one distinct law). Law level
only: the cited *article* is not resolved (renumbering, `Bis` articles and ranges
are a project of their own).

**Scoring** (`evaluate_citations.py` -- a script of its own rather than
`evaluate_links.py --level row`, which would have had to carry a scorer, a mask and
a row join the instrument level never needs; it imports that script's bootstrap,
McNemar test and instrument check). For each model's work directory: its own
scorer (`scoring.scorer_for`: the exact cosine or BM25), the unit-row join, and
exactly the mask the matrix used -- the citing instrument's *exclusively owned*
texts and the texts no searched row owns removed (`instrument_matrix.owners_of_rows`
and `own_rows_of`, imported). Each instrument's score is the best over the vector
rows it owns, and the cited law's **competition rank** is 1 + the foreign
instruments scoring strictly more (tolerance 1e-6, relative for BM25). A text the
row shares word for word with the cited law is a column the mask keeps, so it ranks
the law first -- the matrix's own rule, kept, and counted (`law_owns_text`; zero in
this run: no citing text is also a text of the law it cites). A law with no score
at all has no rank (reciprocal rank 0, never recalled, `n_foreign + 1` for the
median). Two checks keep it an evaluation of the published data sets rather than of
a fourth method: the best foreign score recomputed here equals `nearest.parquet`'s
`similarity` for **every** citing row (a `SystemExit` otherwise; the largest
difference was 4.8e-7 for the 0.6B, 3.6e-7 for the 4B and 0 for BM25), and a row is
scored only if the matrix *counted* it in every model, so the models are compared on
the same rows (the `identical_shared` and `no_match` rules remove the rest, and the
report says how many).

**Metrics**: recall@1, recall@5, recall@10, MRR and the median rank, per model; per
collection; without the Constitution; and for the ten most cited laws. Significance
is #272's rule, fixed beforehand: paired bootstrap over rows (seed 0, 10,000
resamples), 95 % percentile interval of the mean (median) difference, McNemar's
exact test for recall@k, real only if the interval excludes 0 and p < 0.05,
otherwise a tie.

```bash
uv run --group viz python scripts/embeddings/gold_links.py --work-dir emb-run-atlas
#   writes citations.parquet next to gold.json (about 25 s)
uv run --group viz python scripts/embeddings/evaluate_citations.py
#   re-scores the three work directories: about two minutes, no Slurm, no network
uv run --group viz python scripts/embeddings/evaluate_citations.py --report     # offline
```

It refuses a work directory that is not the gold's instruments, a missing
`nearest.parquet` (naming the path), a missing scorer input, a citing text with no
vector row, and any disagreement with `nearest.parquet`; it rebuilds none of them.
Tests: `tests/test_evaluate_citations.py` (an independent oracle over the toy corpus
for every `(source, text, cited instrument)`, the mask, a shared text, both scorers)
and the signal-C cases of `tests/test_gold_links.py`.

### Measured, row-level citations, 2026-10-05 (issue #273)

Signal C (commit `82ec9f4e`): **3,647 citations**, **2,537 distinct citing unit rows**
(`(source, text)`) from **832 instruments** citing **179 laws**; 3,104 rows of
`citations.parquet`; **2,010 cite exactly one law** (527 cite several: counted, not
scored). The planning figures with a looser regex were 3,575 / 2,566 / 822 / 172 /
2,107; the difference is `del` before a `Código`, the ordinal full stop and the
`reglamento` guard. Seven citing rows are `identical_shared` rows (the same seven
for the 0.6B, the 4B and BM25) and are removed, leaving **2,003 scored rows**
(1,831 in reglamentos, 172 in lineamientos); 1,444 without the Constitution, which
alone is 559 of them (28 %) because every kind of regulation cites it.

| metric | 0.6B | 4B | BM25 |
|---|---|---|---|
| recall@1 | 191 (0.095) | 207 (0.103) | 182 (0.091) |
| recall@5 | 507 (0.253) | 546 (0.273) | 483 (0.241) |
| recall@10 | 701 (0.350) | 761 (0.380) | 694 (0.346) |
| MRR | 0.180 | 0.194 | 0.173 |
| median rank (of 1,302) | 29 | 21 | 29 |
| *without the Constitution (1,444)* recall@5 | 486 (0.337) | 522 (0.361) | 467 (0.323) |
| *without the Constitution* MRR | 0.238 | 0.252 | 0.231 |
| *without the Constitution* median rank | 13 | 11 | 13 |

Verdicts (first minus second; intervals and p-values in `evaluation-rows.md`):

| pair | recall@1 | recall@5 | recall@10 | MRR | median rank |
|---|---|---|---|---|---|
| 0.6B vs 4B | tie | **4B better** (p 0.001) | **4B better** (p < 0.0001) | **4B better** | **4B better** (29 vs 21) |
| 0.6B vs BM25 | tie | tie | tie | tie | tie |
| 4B vs BM25 | tie (p 0.054) | **4B better** (p 0.0003) | **4B better** (p 0.0004) | **4B better** | **4B better** (21 vs 29) |

**Reading.** Unlike the instrument level, where the three are tied on what the
page shows, **the 4B is better at this question**: the cited law is in its five
closest instruments for 27.3 % of the rows against 25.3 % (0.6B) and 24.1 %
(BM25), and its median rank is 21 against 29. The two 4B differences are small
(two to three points of recall) but not noise; the 0.6B and BM25 are
indistinguishable, and nobody separates on recall@1, which is under 11 % for all
three. The absolute level is low: a row that cites an article of a law is
usually not about that law's *other* texts, and the Constitution alone (559
rows, recall@5 of 3 to 4 % in every model) drags it down, which is why the
without-Constitution rows are also reported; the order is the same without it.
The split by law is where the lexical baseline is not beaten: of the ten most cited
laws BM25's recall@5 is far higher for the *Código Fiscal de la Federación*
(0.684 against 0.439 for the 4B and 0.351 for the 0.6B) and lower for the *Ley
Orgánica del Poder Judicial de la Federación* (0.195 against 0.439 and 0.488), and
the three are within a few rows on the others. Per collection, the order holds in
reglamentos (1,831 rows); in lineamientos (172) the 4B leads and BM25 is last
(recall@10 0.523 / 0.500 / 0.390), without a test. Law level only: this says
nothing about whether the right *article* ranks first.

### Measured, topics, 2026-10-12 (issue #285)

*This is the 0.6B run and the record of what #285 measured; the Topics page does not
use it. The page uses the 4B run, "Measured, topics, the 4B, 2026-10-13" below.*

`cluster_instruments.py --work-dir emb-run-atlas`, commit `84165c97`, the 0.6B
matrix (2,676 × 2,676, 0 zero rows) and the 275 strong links of the review fix of
issue #283; 5 minutes 40 seconds on one machine with no Slurm (the labels read
145,970 searched unit rows), the whole run repeated with a byte-identical `topics.json` and
`comparison.json` apart from `meta.generated`. Every configuration finished (22
of 22). Per configuration, in `comparison.md`:

| algorithm | parameter | clusters | noise | largest | silhouette | co-membership | gates | chosen |
|---|---|---:|---:|---:|---:|---:|---|---|
| hdbscan_umap | min_cluster_size 5 | 146 | 19.4% | 2.7% | 0.144 | 0.429 | fail: 146 clusters, outside 20-80; noise 19.4% over 15% |  |
| hdbscan_umap | min_cluster_size 10 | 74 | 24.0% | 3.2% | 0.100 | 0.455 | fail: noise 24.0% over 15% |  |
| hdbscan_umap | min_cluster_size 15 | 47 | 32.1% | 3.3% | 0.086 | 0.389 | fail: noise 32.1% over 15% |  |
| hdbscan_umap | min_cluster_size 20 | 36 | 34.0% | 8.3% | 0.073 | 0.411 | fail: noise 34.0% over 15% |  |
| hdbscan_umap | min_cluster_size 30 | 24 | 38.5% | 7.8% | 0.058 | 0.255 | fail: noise 38.5% over 15% |  |
| agglomerative | n_clusters 20 | 20 | 0.0% | 69.4% | 0.018 | 0.676 | fail: largest cluster 69.4% over 25% |  |
| agglomerative | n_clusters 30 | 30 | 0.0% | 60.8% | 0.026 | 0.644 | fail: largest cluster 60.8% over 25% |  |
| agglomerative | n_clusters 40 | 40 | 0.0% | 54.8% | 0.034 | 0.615 | fail: largest cluster 54.8% over 25% |  |
| agglomerative | n_clusters 50 | 50 | 0.0% | 53.0% | 0.043 | 0.615 | fail: largest cluster 53.0% over 25% |  |
| agglomerative | n_clusters 60 | 60 | 0.0% | 50.2% | 0.049 | 0.582 | fail: largest cluster 50.2% over 25% |  |
| agglomerative | n_clusters 80 | 80 | 0.0% | 44.5% | 0.063 | 0.535 | fail: largest cluster 44.5% over 25% |  |
| kmeans | n_clusters 20 | 20 | 0.0% | 51.4% | 0.025 | 0.640 | fail: largest cluster 51.4% over 25% |  |
| kmeans | n_clusters 30 | 30 | 0.0% | 50.8% | 0.038 | 0.625 | fail: largest cluster 50.8% over 25% |  |
| kmeans | n_clusters 40 | 40 | 0.0% | 46.7% | 0.043 | 0.462 | fail: largest cluster 46.7% over 25% |  |
| kmeans | n_clusters 50 | 50 | 0.0% | 40.5% | 0.052 | 0.509 | fail: largest cluster 40.5% over 25% |  |
| kmeans | n_clusters 60 | 60 | 0.0% | 36.3% | 0.059 | 0.487 | fail: largest cluster 36.3% over 25% |  |
| kmeans | n_clusters 80 | 80 | 0.0% | 32.7% | 0.066 | 0.476 | fail: largest cluster 32.7% over 25% |  |
| louvain | resolution 0.5 | 15 | 0.0% | 23.6% | 0.018 | 0.862 | fail: 15 clusters, outside 20-80 |  |
| louvain | resolution 1.0 | 32 | 0.0% | 12.6% | 0.028 | 0.855 | pass | **yes** |
| louvain | resolution 1.5 | 41 | 0.0% | 13.1% | 0.035 | 0.807 | pass |  |
| louvain | resolution 2.0 | 53 | 0.0% | 12.3% | 0.043 | 0.807 | pass |  |
| louvain | resolution 3.0 | 67 | 0.0% | 11.4% | 0.047 | 0.800 | pass |  |

Only the four Louvain configurations from `resolution` 1.0 up pass the gates;
`resolution` 0.5 gives 15 communities, under 20. Every Ward and *k*-means
configuration fails the largest-cluster gate (a cluster of 33 to 69 % of the
instruments: the 1,373 treaties and the regulations that read alike do not
separate in the normalised rows), and every HDBSCAN configuration fails the noise
gate (19 to 39 % of the instruments in no dense region of the 5-dimensional map).
The rule picks **`louvain` `resolution` 1.0**: 32 topics, no noise, the largest
with 336 instruments (12.6 %), silhouette 0.028 and co-membership 0.855 (235 of
the 275 strong links stay together). The silhouette is near zero for every
algorithm, which says the rows of `A` have no well-separated clusters in the
cosine sense; the graph communities are what agree with the strong links, by a
wide margin (0.80 to 0.86 against at most 0.68 for any other algorithm).

The 32 topics of the chosen configuration, by size (`topics.json`; the terms are
the first five of ten, the representative the nearest to the cluster's mean):

| id | size | collections | top 5 terms | first representative |
|---:|---:|---|---|---|
| 0 | 336 | leyes 1, tratados 335 | partes, cooperación, convenio, unidos mexicanos, estados unidos | CONVENIO DE COOPERACION EN EL CAMPO DE LA EDUCACION Y LA CULTURA (R... (`24170`) |
| 1 | 301 | leyes 11, reglamentos 6, tratados 284 | estados, presente, miembros, artículo, convención | CONVENIO CONSTITUTIVO DEL ORGANISMO MULTILATERAL DE GARANTIA DE INV... (`60292`) |
| 2 | 203 | leyes 62, lineamientos 15, reglamentos 116, tratados 10 | secretaría, nacional, artículo, federal, unidades | REGLAMENTO INTERIOR DE LA SECRETARIA DE GOBERNACION (`179040`) |
| 3 | 185 | leyes 28, lineamientos 6, reglamentos 40, tratados 111 | artículo, articulo, trabajo, caso, convenio | LEY Federal de los Trabajadores al Servicio del Estado, Reglamentar... (`lftse`) |
| 4 | 162 | leyes 17, lineamientos 69, reglamentos 76 | información, tribunal, artículo, transparencia, datos | REGLAMENTO INTERIOR DEL TRIBUNAL DE DISCIPLINA JUDICIAL (`189406`) |
| 5 | 152 | leyes 1, tratados 151 | requerida, parte, requirente, parte requerida, tratado | TRATADO DE EXTRADICION ENTRE LOS ESTADOS UNIDOS MEXICANOS Y LA REPU... (`24158`) |
| 6 | 146 | leyes 15, lineamientos 2, reglamentos 128, tratados 1 | personal, militar, escuela, artículo, plantel | REGLAMENTO DE LA ESCUELA MILITAR DE IDIOMAS (`174497`) |
| 7 | 140 | leyes 12, reglamentos 15, tratados 113 | ex, kg, parte, 01, demás | ACUERDO DE COMPLEMENTACION ECONOMICA No. 66 CELEBRADO ENTRE LOS EST... (`75190`) |
| 8 | 84 | leyes 18, reglamentos 37, tratados 29 | secretaría, ambiental, artículo, ley, agua | REGLAMENTO DE LA LEY GENERAL DEL EQUILIBRIO ECOLOGICO Y LA PROTECCI... (`16259`) |
| 9 | 84 | tratados 84 | parte contratante, contratante, parte, partes contratantes, otra parte | CONVENIO SOBRE TRANSPORTE AEREO (REPUBLICA DE POLONIA, 1990) (`11266`) |
| 10 | 81 | leyes 5, lineamientos 3, reglamentos 15, tratados 58 | carga, buque, buques, transporte, convenio | CONVENIO INTERNACIONAL SOBRE NORMAS DE FORMACION, TITULACION Y GUAR... (`11345`) |
| 11 | 80 | leyes 21, lineamientos 1, reglamentos 51, tratados 7 | salud, articulo, artículo, secretaría, ley | REGLAMENTO DE LA LEY GENERAL DE SALUD EN MATERIA DE PRESTACION DE S... (`293`) |
| 12 | 77 | leyes 4, lineamientos 1, reglamentos 71, tratados 1 | instituto, artículo, trabajadores, servicios, ley | LEY del Instituto del Fondo Nacional de la Vivienda para los Trabaj... (`lifnvt`) |
| 13 | 74 | leyes 9, lineamientos 2, reglamentos 63 | 2023, electoral, partidos, electorales, instituto | LEY General de Partidos Políticos (`lgpp`) |
| 14 | 74 | leyes 7, reglamentos 17, tratados 50 | servicio, cmr, banda, administraciones, frecuencias | ACTAS FINALES DE LA CONFERENCIA ADMINISTRATIVA MUNDIAL DE RADIOCOMU... (`23943`) |
| 15 | 67 | leyes 34, reglamentos 33 | comisión, ley, artículo, sociedades, sociedad | LEY de Uniones de Crédito (`luc`) |
| 16 | 65 | leyes 24, lineamientos 8, reglamentos 25, tratados 8 | artículo, fiscal, ley, impuesto, refiere | REGLAMENTO DE LA LEY DE PRESUPUESTO, CONTABILIDAD Y GASTO PUBLICO F... (`173`) |
| 17 | 65 | tratados 65 | estado, estado contratante, contratante, otro estado, otro | CONVENIO PARA EVITAR LA DOBLE IMPOSICION E IMPEDIR LA EVASION FISCA... (`24136`) |
| 18 | 51 | leyes 5, reglamentos 46 | sorteo, pronósticos, premios, sorteos, premio | REGLAMENTO DEL SORTEO GANA GATO DE PRONOSTICOS PARA LA ASISTENCIA P... (`52019`) |
| 19 | 42 | tratados 42 | parte contratante, contratante, parte, inversionista, acuerdo | ACUERDO PARA LA PROMOCION Y PROTECCION RECIPROCA DE LAS INVERSIONES... (`55905`) |
| 20 | 41 | leyes 8, lineamientos 10, reglamentos 22, tratados 1 | artículo, centro, adolescentes, persona, caso | LEY Nacional del Sistema Integral de Justicia Penal para Adolescentes (`lnsijpa`) |
| 21 | 39 | leyes 10, reglamentos 26, tratados 3 | debe, artículo, secretaría, energía, ley | REGLAMENTO DE LA LEY REGLAMENTARIA DEL ARTICULO 27 CONSTITUCIONAL E... (`70136`) |
| 22 | 29 | leyes 7, reglamentos 19, tratados 3 | agencia, persona, vuelo, aviación, artículo | REGLAMENTO DE AUTOTRANSPORTE FEDERAL Y SERVICIOS AUXILIARES (`1322`) |
| 23 | 20 | leyes 5, lineamientos 5, reglamentos 10 | contrato, artículo, contratación, servicios, licitación | REGLAMENTO DEL INSTITUTO FEDERAL ELECTORAL EN MATERIA DE OBRAS PUBL... (`119002`) |
| 24 | 15 | leyes 2, lineamientos 1, tratados 12 | estado, tratado, estado receptor, consular, receptor | CONVENCION CONSULAR (REPUBLICA POPULAR CHINA, 1986) (`23941`) |
| 25 | 14 | leyes 4, reglamentos 10 | cámara, mesa, comisiones, presidente, diputados | LEY Orgánica del Congreso General de los Estados Unidos Mexicanos (`locg`) |
| 26 | 13 | leyes 4, reglamentos 8, tratados 1 | migratoria, territorio nacional, artículo, persona extranjera, territorio | REGLAMENTO PARA LA EXPEDICION DE CERTIFICADOS DE NACIONALIDAD MEXICANA (`59985`) |
| 27 | 12 | reglamentos 12 | pasantes, prácticas, examen profesional, naval, profesional | REGLAMENTO PARA EL EXAMEN PROFESIONAL DE LOS GUARDIAMARINAS EGRESAD... (`14706`) |
| 28 | 11 | leyes 1, reglamentos 8, tratados 2 | turísticos, turismo, secretaría, turística, turístico | REGLAMENTO DE LA LEY FEDERAL DE TURISMO (`29`) |
| 29 | 7 | reglamentos 7 | conamed, médica, queja, reembolso, comité | REGLAMENTO PARA EL TRAMITE DE SOLICITUDES DE REEMBOLSO POR LA PREST... (`44035`) |
| 30 | 4 | lineamientos 2, reglamentos 2 | bienes, instituto, artículo, deberá, caso | LINEAMIENTOS PARA EL CONTROL DE INVENTARIOS Y ADMINISTRACION DE LOS... (`181245`) |
| 31 | 2 | tratados 2 | hachemita jordania, hachemita, reino hachemita, jordania, dos mil | CONVENIO MARCO DE COOPERACION TECNICA (REINO HACHEMITA DE JORDANIA,... (`108582`) |

Reading the table: six topics are the treaties alone or nearly so (0, 5, 9, 17,
19, 24: bilateral cooperation agreements, extradition, air transport, double
taxation, investment protection, consular conventions), 1 and 7 are the
multilateral and the trade-tariff ones, and the regulations and laws split by
subject (interior regulations of a secretariat, military education, environment,
health, housing and social security, electoral law, finance and credit, taxation,
lotteries, public procurement, the Congress, migration, tourism). The two
smallest clusters (30, 31) are a handful of instruments each. The terms are the
Spanish words of the corpus, left as it spells them (`articulo` and `artículo`
are two terms: the corpus writes both).

### Measured, topics, the 4B, 2026-10-13 (review fix-1: #285, #286)

`cluster_instruments.py --work-dir emb-run-atlas-4b`, commit `b8a2fd9f`, the 4B
matrix (`Qwen/Qwen3-Embedding-4B`, 2,676 × 2,676, 0 zero rows) and the same 275
strong links, read from `emb-run-atlas/gold-links/gold.json` (`--gold`, its default;
its `provenance.commit` is `a6324cd9`); 4 minutes 48 seconds on one machine with no
Slurm. Run twice, `topics.json` and `comparison.json` are byte-identical apart from
`meta.generated`. Every configuration finished (22 of 22). Nothing but the matrix
changed from the 0.6B run above: same sweeps, gates, rule, c-TF-IDF parameters and
stopword list. Per configuration, in `comparison.md`:

| algorithm | parameter | clusters | noise | largest | silhouette | co-membership | gates | chosen |
|---|---|---:|---:|---:|---:|---:|---|---|
| hdbscan_umap | min_cluster_size 5 | 159 | 19.2% | 1.9% | 0.150 | 0.378 | fail: 159 clusters, outside 20-80; noise 19.2% over 15% |  |
| hdbscan_umap | min_cluster_size 10 | 74 | 22.7% | 3.7% | 0.105 | 0.473 | fail: noise 22.7% over 15% |  |
| hdbscan_umap | min_cluster_size 15 | 55 | 28.9% | 3.6% | 0.093 | 0.465 | fail: noise 28.9% over 15% |  |
| hdbscan_umap | min_cluster_size 20 | 39 | 31.4% | 3.4% | 0.076 | 0.447 | fail: noise 31.4% over 15% |  |
| hdbscan_umap | min_cluster_size 30 | 4 | 0.0% | 94.9% | 0.009 | 0.996 | fail: 4 clusters, outside 20-80; largest cluster 94.9% over 25% |  |
| agglomerative | n_clusters 20 | 20 | 0.0% | 76.8% | 0.019 | 0.735 | fail: largest cluster 76.8% over 25% |  |
| agglomerative | n_clusters 30 | 30 | 0.0% | 69.8% | 0.024 | 0.691 | fail: largest cluster 69.8% over 25% |  |
| agglomerative | n_clusters 40 | 40 | 0.0% | 59.2% | 0.035 | 0.622 | fail: largest cluster 59.2% over 25% |  |
| agglomerative | n_clusters 50 | 50 | 0.0% | 57.1% | 0.045 | 0.622 | fail: largest cluster 57.1% over 25% |  |
| agglomerative | n_clusters 60 | 60 | 0.0% | 50.3% | 0.051 | 0.564 | fail: largest cluster 50.3% over 25% |  |
| agglomerative | n_clusters 80 | 80 | 0.0% | 42.8% | 0.064 | 0.527 | fail: largest cluster 42.8% over 25% |  |
| kmeans | n_clusters 20 | 20 | 0.0% | 60.5% | 0.028 | 0.589 | fail: largest cluster 60.5% over 25% |  |
| kmeans | n_clusters 30 | 30 | 0.0% | 50.0% | 0.040 | 0.549 | fail: largest cluster 50.0% over 25% |  |
| kmeans | n_clusters 40 | 40 | 0.0% | 49.7% | 0.045 | 0.582 | fail: largest cluster 49.7% over 25% |  |
| kmeans | n_clusters 50 | 50 | 0.0% | 40.5% | 0.052 | 0.491 | fail: largest cluster 40.5% over 25% |  |
| kmeans | n_clusters 60 | 60 | 0.0% | 39.5% | 0.059 | 0.429 | fail: largest cluster 39.5% over 25% |  |
| kmeans | n_clusters 80 | 80 | 0.0% | 33.9% | 0.075 | 0.400 | fail: largest cluster 33.9% over 25% |  |
| louvain | resolution 0.5 | 17 | 0.0% | 24.2% | 0.017 | 0.851 | fail: 17 clusters, outside 20-80 |  |
| louvain | resolution 1.0 | 32 | 0.0% | 13.6% | 0.030 | 0.811 | pass | **yes** |
| louvain | resolution 1.5 | 44 | 0.0% | 10.7% | 0.040 | 0.782 | pass |  |
| louvain | resolution 2.0 | 52 | 0.0% | 10.9% | 0.044 | 0.793 | pass |  |
| louvain | resolution 3.0 | 68 | 0.0% | 11.2% | 0.052 | 0.789 | pass |  |

Four configurations pass the gates, all of them Louvain (`resolution` 1.0 to 3.0);
`resolution` 0.5 gives 17 communities, under 20. Ward and *k*-means leave a topic of
33.9 to 76.8 % of the instruments in every configuration. HDBSCAN leaves 19.2 to
31.4 % of the instruments in no dense region with a `min_cluster_size` of 5 to 20,
and with 30 it merges everything into four clusters, one of 94.9 % of the instruments
(co-membership 0.996, which is what the gates are for). The rule picks **`louvain`
`resolution` 1.0**, as for the 0.6B: 32 topics, no noise, the largest with 365
instruments (13.6 %), silhouette 0.030 and co-membership 0.811 (223 of the 275 strong
links stay together; the 0.6B's 0.855 was 235). The partition is a different one: the
lottery regulations, the extradition treaties and the transparency regulations are
again topics of their own, but the sizes and several members differ.

The 32 topics of the chosen configuration, by size (`topics.json`; the terms are the
first five of ten, the representative the nearest to the cluster's mean). The page
numbers them from 1, so page topic *n* is cluster *n* − 1 of this table:

| id | size | collections | top 5 terms | first representative |
|---:|---:|---|---|---|
| 0 | 365 | leyes 1, reglamentos 1, tratados 363 | partes, cooperación, unidos mexicanos, estados unidos, unidos | CONVENIO DE COOPERACION EDUCATIVA Y CULTURAL (REPUBLICA VENEZUELA, ... (`24162`) |
| 1 | 298 | leyes 13, reglamentos 4, tratados 281 | estados, presente, miembros, artículo, parte | CONVENCION DE LA ORGANIZACION MUNDIAL DE LA METEOROLOGIA (MULTILATE... (`23773`) |
| 2 | 198 | leyes 44, lineamientos 19, reglamentos 129, tratados 6 | secretaría, artículo, federal, nacional, información | REGLAMENTO INTERIOR DE LA SECRETARIA DE GOBERNACION (`179040`) |
| 3 | 143 | leyes 10, lineamientos 9, reglamentos 63, tratados 61 | trabajo, trabajadores, instituto, artículo, trabajador | LEY del Instituto del Fondo Nacional de la Vivienda para los Trabaj... (`lifnvt`) |
| 4 | 142 | leyes 4, reglamentos 5, tratados 133 | ex, parte, kg, 01, partes | ACUERDO DE COMPLEMENTACION ECONOMICA No. 66 CELEBRADO ENTRE LOS EST... (`75190`) |
| 5 | 141 | leyes 25, lineamientos 49, reglamentos 67 | información, tribunal, artículo, transparencia, ley | REGLAMENTO DE TRANSPARENCIA Y ACCESO A LA INFORMACION DEL TRIBUNAL ... (`131773`) |
| 6 | 121 | leyes 33, lineamientos 3, reglamentos 34, tratados 51 | articulo, artículo, caso, ley, art | CÓDIGO Civil Federal (`ccf`) |
| 7 | 120 | leyes 2, reglamentos 3, tratados 115 | parte, parte requerida, tratado, extradición, requerida | TRATADO DE EXTRADICION ENTRE LOS ESTADOS UNIDOS MEXICANOS Y LA REPU... (`18576`) |
| 8 | 96 | leyes 12, lineamientos 1, reglamentos 83 | personal, escuela, educación, plantel, artículo | REGLAMENTO DE LA ESCUELA MILITAR DE GRADUADOS DE SANIDAD (`21471`) |
| 9 | 91 | tratados 91 | parte contratante, contratante, parte, partes contratantes, otra parte | CONVENIO SOBRE TRANSPORTE AEREO (GOBIERNO FEDERAL DE AUSTRIA, 1995) (`24104`) |
| 10 | 81 | leyes 26, lineamientos 20, reglamentos 35 | artículo, ley, fiscal, refiere, impuesto | LEY de Ingresos de la Federación para el Ejercicio Fiscal de 2026 (`lif-2026`) |
| 11 | 81 | leyes 10, lineamientos 1, reglamentos 62, tratados 8 | salud, secretaría, artículo, productos, animal | REGLAMENTO DE LA LEY GENERAL DE SALUD EN MATERIA DE PRESTACION DE S... (`293`) |
| 12 | 79 | leyes 5, lineamientos 3, reglamentos 14, tratados 57 | carga, buque, buques, transporte, convenio | CONVENIO INTERNACIONAL PARA PREVENIR LA CONTAMINACION POR LOS BUQUE... (`23845`) |
| 13 | 77 | leyes 17, reglamentos 52, tratados 8 | artículo, secretaría, comisión, ley, energía | REGLAMENTO PARA LA COMERCIALIZACION DE SERVICIOS DE TELECOMUNICACIO... (`45651`) |
| 14 | 77 | leyes 7, lineamientos 7, reglamentos 58, tratados 5 | 2023, electoral, partidos, electorales, consejo | LEY General de Partidos Políticos (`lgpp`) |
| 15 | 76 | tratados 76 | estado, estado contratante, contratante, otro estado, otro | CONVENIO PARA EVITAR LA DOBLE IMPOSICION Y PREVENIR LA EVASION FISC... (`77473`) |
| 16 | 70 | leyes 34, reglamentos 36 | comisión, ley, artículo, sociedades, sociedad | LEY para Regular las Instituciones de Tecnología Financiera (`lritf`) |
| 17 | 57 | leyes 13, lineamientos 1, reglamentos 43 | militar, personal, articulo, servicio, guardia | REGLAMENTO PARA EL SERVICIO INTERIOR DE LAS UNIDADES, DEPENDENCIAS ... (`46901`) |
| 18 | 56 | leyes 11, lineamientos 3, reglamentos 13, tratados 29 | artículo, unión, internacional, presente, ley | ACTA DE GINEBRA DEL ARREGLO DE LA HAYA RELATIVO AL REGISTRO INTERNA... (`143097`) |
| 19 | 55 | leyes 1, reglamentos 2, tratados 52 | cmr, servicio, banda, administraciones, radiocomunicaciones | ACTAS FINALES DE LA CONFERENCIA ADMINISTRATIVA MUNDIAL DE RADIOCOMU... (`23943`) |
| 20 | 43 | leyes 2, reglamentos 41 | sorteo, pronósticos, premios, sorteos, números | REGLAMENTO DEL SORTEO GANA GATO DE PRONOSTICOS PARA LA ASISTENCIA P... (`52019`) |
| 21 | 40 | leyes 15, reglamentos 24, tratados 1 | ambiental, secretaría, agua, artículo, ley | REGLAMENTO DE LA LEY GENERAL DEL EQUILIBRIO ECOLOGICO Y LA PROTECCI... (`16259`) |
| 22 | 29 | leyes 9, lineamientos 3, reglamentos 16, tratados 1 | artículo, adolescentes, centro, niñas, niñas niños | REGLAMENTO GENERAL PARA LOS CENTROS DE ASISTENCIA SOCIAL Y ESTABLEC... (`158840`) |
| 23 | 29 | leyes 2, lineamientos 1, tratados 26 | tratado, estado, estado receptor, consular, receptor | CONVENCION SOBRE AGENTES CONSULARES (MULTILATERAL, 1928) (`16462`) |
| 24 | 22 | leyes 5, lineamientos 4, reglamentos 10, tratados 3 | contrato, artículo, contratación, caso, servicios | REGLAMENTO DEL INSTITUTO FEDERAL ELECTORAL EN MATERIA DE OBRAS PUBL... (`119002`) |
| 25 | 22 | leyes 5, reglamentos 14, tratados 3 | agencia, persona, vuelo, aviación, aviación civil | REGLAMENTO DE LOS SERVICIOS AUXILIARES AL AUTOTRANSPORTE FEDERAL DE... (`165917`) |
| 26 | 18 | lineamientos 1, reglamentos 17 | normatividad, normatividad aplicable, pensionissste, aplicable, servicios | REGLAMENTO ORGANICO DEL FONDO DE LA VIVIENDA DEL INSTITUTO DE SEGUR... (`188947`) |
| 27 | 17 | leyes 5, reglamentos 11, tratados 1 | comisión nacional, nacional, comisión, discapacidad, derechos humanos | REGLAMENTO PARA LA ENTREGA DE LAS MEDALLAS QUE OTORGA LA CAMARA DE ... (`145664`) |
| 28 | 12 | reglamentos 12 | pasantes, prácticas, examen profesional, naval, profesional | REGLAMENTO PARA EL EXAMEN PROFESIONAL DE LOS PRIMEROS MAESTRES PASA... (`14710`) |
| 29 | 11 | leyes 4, reglamentos 7 | cámara, mesa, comisiones, presidente, diputados | LEY Orgánica del Congreso General de los Estados Unidos Mexicanos (`locg`) |
| 30 | 7 | reglamentos 7 | conamed, médica, queja, reembolso, comité | REGLAMENTO PARA EL TRAMITE DE SOLICITUDES DE REEMBOLSO POR LA PREST... (`44035`) |
| 31 | 2 | tratados 2 | reino países, bajos, dos mil, países bajos, reino | ACUERDO ENTRE LOS ESTADOS UNIDOS MEXICANOS Y EL REINO DE LOS PAISES... (`75722`) |

### Measured, nearest texts, the 4B, 2026-10-13 (issue #288)

`nearest_texts.py --work-dir emb-run-atlas-4b`, over the matrix of the 2,676
instruments (185,456 vector rows, 145,970 searched unit rows), top 5, block of
1,024 rows, tolerance 1e-6. **Computed locally, not on the cluster**: the job
was submitted (`sbatch`, job 43111, `--exclude=geoint0`) and sat pending on
`Resources` for the whole time it was watched, because `geoint1` and `geoint2`
were held by another user's interactive jobs (11 h and 2 h old) and `geoint0`
is excluded by default; it was cancelled and the plain `--work-dir` call was
run on the login node (`geoint0`, 60 threads, a load average of about 18 from
other users' work), which is why `summary.json` has no `slurm_job_id`.

| | |
|---|---|
| sources | 145,970 searched unit rows = 145,693 distinct `(instrument, vector row)` texts |
| rows written | 728,465 (exactly 5 for every source text; `sources_short_of_top` 0, `sources_without_rows` 0) |
| `agrees_with_nearest` | **true** (rank-1 similarity within 1e-6 and rank-1 `targets` inside the matrix's, for every source) |
| rank-1 similarity | median 0.764, mean 0.783, minimum 0.339; 4,809 sources at cosine 1 (a word-for-word text of another instrument) |
| exact ties at rank 1 and 2 | 853 sources (the rank-1 text has the lower vector row) |
| rank-1..5 texts with several owners | 17,061 of 728,465 rows, at most 14 owners |
| seconds | load 6.0, normalise 2.2, sweep 720.6, check 0.8, write 0.8; 729.8 in total |
| peak RSS | 4.8 GB |
| `nearest-texts.parquet` | 8.4 MB |

The sweep took about 2.4 times the matrix's 297 s on 62 cluster threads; the
node was shared, so the extra cost of the top-K selection is not separable
from it.

### Measured, the instrument files, the 4B, 2026-10-13 (issue #289)

`export_instrument_files.py --work-dir emb-run-atlas-4b`, md2akn 0.5.0, over the
2,676 instruments at the snapshot their vectors came from. **The re-parse
reproduced the released `units.parquet` for every one of them** (203,324
excerpts, 145,970 searched, `start_char`/`end_char` included): the check never
fired. 533 s on the login node (no Slurm: the export is one process).

| | |
|---|---|
| files | 2,676 (315 leyes, 863 reglamentos, 125 lineamientos, 1,373 tratados), 320.1 MB of JSON (311 MB on disk) |
| largest file | `files/tratados/137355.json`, 4.9 MB, 1,288 excerpts |
| tarball | `atlas-instruments-qwen3-4b.tar.gz` 67.8 MB; `instruments.json` 0.57 MB |

The first corpus-wide `md2akn` quality table. Each cell is *flagged excerpts
(instruments with the flag)*; the instrument-level flags count instruments.
These are counts for a person to look at, not defects: a loose block or a gap
can be legitimate.

| collection | instruments | excerpts | `loose` | `duplicate_number` | `numbering_gap` | `over_window` | `no_articles` | `no_transitorios` |
|---|---|---|---|---|---|---|---|---|
| leyes | 315 | 68,581 | 2,712 (135) | 539 (41) | 181 (34) | 1 (1) | 0 | 6 |
| reglamentos | 863 | 87,965 | 936 (195) | 891 (49) | 181 (67) | 1 (1) | 0 | 64 |
| lineamientos | 125 | 5,443 | 426 (54) | 589 (30) | 31 (15) | 0 | 3 | 11 |
| tratados | 1,373 | 41,335 | 3,196 (113) | 3,152 (91) | 984 (89) | 10 (9) | 53 | 1,032 |
| all | 2,676 | 203,324 | 7,270 (497) | 5,171 (211) | 1,377 (205) | 12 (11) | 56 | 1,113 |

`uncovered` fired for **no instrument**: `coverage()` holds on all 2,676
documents. The highest scores (the page's default order) are the treaty
`137355` (1,779: 568 loose, 626 duplicated and 584 out-of-sequence article
numbers, and one text over the window), the law `ligie-2022` (1,384, all loose
blocks: the tariff), the treaty `114738` (541), the treaty `11345` (501, loose)
and `ccom` (299). The 56 documents with no article at all are the 53 treaties
and 3 lineamientos already counted in the rule-10 and rule-8 measurements.

### Measured, `md2akn` 0.5.1 against the published units, 2026-10-14 (issue #291)

**Do the vectors have to be recomputed? Yes — for all four collections, but only 3,307 texts.**
`md2akn` 0.5.1 (rules 11-13, see `packages/md2akn/README.md`) and `scjn` 0.5.1
(seven treaties re-downloaded with `**Artículo 1.1**:` leads) change the
`units.parquet` of every collection. `compare_units.py` (a read-only script, tested in
`tests/test_compare_units.py`) compares each published work directory with a copy that
`build_units.py --no-split-articles` rebuilt, instrument by instrument:

```bash
python scripts/embeddings/compare_units.py --published emb-run-whole-leyes \
    --rebuilt emb-run-leyes-051 --list-changed
```

An instrument is *changed* when its `(unit_type, eId, piece, text_sha1)` sequence is not the
published one. Most are **only relabelled**: a text that was a container's `loose` excerpt is
now its `intro` (rule 12), with the same `text_sha1`, so it keeps its published vector and
nothing is encoded for it. What has to be encoded is the *distinct new* `text_sha1`:

| collection | instruments | changed | with a new text | only relabelled | unchanged | rows before → after | distinct new texts (dropped) |
|---|---:|---:|---:|---:|---:|---|---:|
| leyes | 315 | 49 | 5 | 44 | 266 | 68,581 → 68,373 | 1,067 (1,277) |
| reglamentos | 1,082 | 144 | 8 | 136 | 938 | 110,670 → 110,697 | 46 (19) |
| lineamientos | 126 | 28 | 4 | 24 | 98 | 5,514 → 5,520 | 23 (17) |
| tratados | 1,373 | 107 | 35 | 72 | 1,266 | 41,335 → 42,063 | 2,171 (1,479) |

Total distinct new texts: **3,307**. The published work directories
(`emb-run-whole-leyes`, `emb-run-whole-reglamentos-r10`, `emb-run-whole-lineamientos`,
`emb-run-tratados`) were not written to (file list, sizes and modification times identical
before and after) and each one's `units.parquet` equals the released one
(`pyarrow.Table.equals` against `legalvec download`'s cached file).

Rows by `unit_type`, before → after:

| unit_type | leyes | reglamentos | lineamientos | tratados |
|---|---:|---:|---:|---:|
| article | 53,586 → 53,586 | 87,129 → 87,129 | 3,768 → 3,768 | 31,493 → 31,493 |
| heading | 11,879 → 11,879 | 19,858 → 19,858 | 951 → 951 | 2,604 → 2,604 |
| preamble | 318 → 318 | 1,922 → 1,922 | 310 → 310 | 2,781 → 2,781 |
| conclusions | 86 → 86 | 436 → 436 | 59 → 59 | 1,261 → 1,261 |
| loose | 2,712 → 1,071 | 1,325 → 606 | 426 → 137 | 3,196 → 44 |
| intro | 0 → 575 | 0 → 730 | 0 → 288 | 0 → 3,098 |
| table | 0 → 858 | 0 → 16 | 0 → 7 | 0 → 782 |

`article` rows are the same in every collection: no rule changes how many articles there are,
only what a decimal article is *called* (`art_1.1`, not `art_1`, `art_1_2`, ...) and, in the
seven re-downloaded treaties, what its text says. `loose` shrinks to what is left under the
transitorios and before the first container; `intro` and `table` are new. Table rows that fall
before the first container stay in the `preamble` (55 `preamble` units: 9 in reglamentos, 14 in
lineamientos and 32 in treaties, holding 70 + 47 + 362 rows), and 2 rows of treaty `23935` stay in
its `conclusions`, as rule 12 says ("text before any container keeps its treatment"); no row of any
collection is a `loose`, `intro` or `heading` unit (swept, `test_units_release_sweep.py`).

#### The 49 laws that change

Five have a new text; the other 44 are only relabelled (`loose` → `intro`, 0 new texts):
`cff` (4 new texts), `lfd` (96), `lfisan` (49), `lieps` (43) and `ligie-2022` (875: rows 1,527 → 1,184,
its tariff is now `table` units; the laws hold 858 `table` units in all). The relabelled ones:
`ccf`, `ccom`, `cmpp`, `cpf`, `egdf`, `ladua`, `lagra`, `lamp`, `lapp`, `ldpam`, `lfafe`, `lfda`, `lfgr`, `lfi`, `lfmzaah`, `lfpa`, `lfpc`, `lfped`, `lfrcf`, `lfrm`, `lfrsp`, `lft`, `lgcs`, `lgipe`, `lgra`, `lgs`, `lie`, `lisr`, `lmeum`, `lmin`, `lned`, `lnrd`, `loefam`, `lopgjdf`, `louaaan`, `lperc`, `lpf`, `lrart27-mn`, `lrcdn`, `lrfiyii-art105`, `lritf`, `lrsf`, `ltfccg`, `lvgc`.

#### The seven re-downloaded treaties

Only three of the seven change: the units come from each treaty's *latest* snapshot, and the
broken `**Artículo N.**M` shape was in an older one of `84349`, `84350`, `90408` and `107392`
(re-downloaded and re-packaged all the same, #255's procedure).

| treaty | rows before → after | new texts |
|---|---|---:|
| `128585` (CPTPP, 2018) | 32 → 32 | 1 |
| `137355` (the T-MEC protocol, 2018) | 1,288 → 1,552 | 1,285 |
| `76` (NAFTA, 1992) | 14 → 14 | 8 |

`137355` (the T-MEC): 626 articles outside the transitorios, **592 repeated numbers before and
59 after**. The 59 left are **a second shape this issue does not fix**: the annex articles
`Artículo 2.A.1:`, `Artículo 12.A.2:` ... (chapter, annex letter, article) are still written by `scjn`
as `**Artículo 2.**A.1:` (61 lines in `137355` and 3 in the reglamento `145559`, none elsewhere), so
the number stops at `2` and the annex's articles repeat `art_2`, `art_2_2`, ... — a letter between
the numbers is outside rule 11's `\d+(?:\.\d+)+`. Every other decimal article has its own eId
(`art_1.1`, `art_1.2`, ...). Reported here, not fixed: it is a finding for a follow-up issue.

The 107 changed treaties are listed by `compare_units.py --list-changed` (35 with a new text; the
largest are `137355` 1,285 · `84855` 300 · `11202` 91 · `23940` 74 · `89694` 73 · `11345` 44 ·
`75190` 38).

#### Re-embedding on `cemieredes`

`plan_shards.py --against <published>/runs/qwen3-0.6b`, one plan for both models: before submitting,
`already_embedded()` over the published `qwen3-0.6b` and `qwen3-4b` runs agreed in every collection
(59,178 · 100,882 · 5,049 · 39,668 texts; reglamentos holds 30 more than its units because of the
pre-rule-10 texts of `182464`). New shards are numbered after the highest existing one
(`first_shard_index`). Always through Slurm (`--mem 32G`, `--max-batch-tokens 20000`), one model at a
time, weights kept between collections and deleted at the end; no shard waited for the queue (another
user's job held one A100 throughout).

| collection | shards | texts | `excluded_already_embedded` | failed | GPU seconds, 0.6B / 4B | truncated texts (published → now) | max tokens embedded |
|---|---:|---:|---:|---:|---|---|---|
| leyes | 2 | 1,067 | 59,178 | 0 | 37 / 73 | 1 → 1 (both models) | 32,768 / 40,960 |
| reglamentos | 1 | 46 | 100,882 | 0 | 15 / 14 | 3 → 3 (0.6B), 1 → 1 (4B) | 32,768 / 40,960 |
| lineamientos | 1 | 23 | 5,049 | 0 | 25 / 90 | 0 → 0 | 32,768 / 40,960 |
| tratados | 3 | 2,171 | 39,668 | 0 | 60 / 104 | 10 → 11 (both models) | 32,768 / 40,960 |

Both models merged (`merge_shards.py`) with `missing_vectors` 0 and 0 failed shards, into
`emb-run-<collection>-051/` (gitignored, each with `corpus-manifest.json` saying `md2akn_version`
`0.5.1` and `split_articles` false). The one truncated text new to the treaties is one more article
over each model's own window, as in the earlier measurements. Verifying that only the changed
instruments' vector files differ from the published ones, the non-finite check and the
`PUBLICAR.md` for the four vectors releases are issue #293's; nothing was published.

### Measured, the re-embedded vectors against the published ones, 2026-10-14 (issue #293)

**Do the re-embedded merges differ from the published ones only where they must? Yes, in
every collection and for both models.** `compare_merges.py` (read-only, tested in
`tests/test_compare_merges.py` against toy merges) answers it from the two work directories alone:

```bash
python scripts/embeddings/compare_merges.py --published emb-run-whole-leyes \
    --rebuilt emb-run-leyes-051 --model Qwen/Qwen3-Embedding-0.6B \
    --changed /tmp/cmp/leyes.json --json /tmp/mcmp/leyes-qwen3-0.6b.json \
    --changed-files-out /tmp/mcmp/leyes-qwen3-0.6b.names
```

What it proves, and stops (`SystemExit` naming the files) when any check fails:

1. **The expected change comes from the two `units.parquet`, not from a list.** An instrument's own
   file holds the texts it owns alone, so it must change exactly when that set changes (a new text, a
   dropped one, a text that became shared or stopped being shared); the shared file likewise. A file
   that differs without its set changing, or whose set changed but is the published file, is a problem.
2. **No file is gained or lost** unless the units say an instrument appeared or went.
3. **A text present in both merges has the same vector, bit for bit**, wherever it sits (a changed
   file may hold it again; the same `text_sha1` never gets a new vector).
4. **Each file holds exactly its expected texts**: none missing, none unexpected, no stale file.
5. **No vector is non-finite** (NaN or ±inf), in either merge.
6. With `--changed` (`compare_units.py --json`, issue #291's list) every differing instrument is in
   that list. A different *order* of rows inside an unchanged file is counted apart (`reordered`),
   not as a difference of content; there was none.

| collection | model | files published → rebuilt | equal | differing | gained / lost | texts new / dropped | texts with another vector | non-finite files |
|---|---|---|---:|---:|---|---|---:|---:|
| leyes | `qwen3-0.6b` | 316 → 316 | 311 | 5 | 0 / 0 | 1,067 / 1,277 | 0 | 0 |
| leyes | `qwen3-4b` | 316 → 316 | 311 | 5 | 0 / 0 | 1,067 / 1,277 | 0 | 0 |
| reglamentos | `qwen3-0.6b` | 1,083 → 1,083 | 1,075 | 8 | 0 / 0 | 46 / 19 | 0 | 0 |
| reglamentos | `qwen3-4b` | 1,083 → 1,083 | 1,075 | 8 | 0 / 0 | 46 / 19 | 0 | 0 |
| lineamientos | `qwen3-0.6b` | 127 → 127 | 123 | 4 | 0 / 0 | 23 / 17 | 0 | 0 |
| lineamientos | `qwen3-4b` | 127 → 127 | 123 | 4 | 0 / 0 | 23 / 17 | 0 | 0 |
| tratados | `qwen3-0.6b` | 1,374 → 1,374 | 1,338 | 35 + shared | 0 / 0 | 2,171 / 1,479 | 0 | 0 |
| tratados | `qwen3-4b` | 1,374 → 1,374 | 1,338 | 35 + shared | 0 / 0 | 2,171 / 1,479 | 0 | 0 |

Files count one per instrument plus the shared file (an instrument whose texts are all shared has
an empty file, as in the published merge). The differing instruments are exactly #291's
"instruments with a new text" (5 laws, 8 reglamentos, 4 lineamientos, 35 treaties), in both models;
the 44 + 136 + 24 + 72 instruments that were only relabelled (`loose` → `intro`, same `text_sha1`)
have a file identical to the published one. Only the treaties' shared file differs (a text newly
shared between treaties); in the three other collections the shared file is unchanged. The new
vectors of a text that is still present are the published ones: **0 texts have a different vector**,
as expected of an incremental re-embedding that encodes only what is new. Distinct texts after the
rebuild, from each `vectors-manifest.json`: leyes 58,968 · reglamentos 100,879 · lineamientos 5,055 ·
tratados 40,360, with `missing_vectors` 0.

#### `PUBLICAR.md`, one per collection, nothing run

`package_vectors.py --coleccion <c> --work-dir emb-run-<c>-051` (with `--changed-files` taking each
model's `.names` file) wrote `emb-run-<c>-051/publish/PUBLICAR.md`, `parte-N.txt`,
`SHA256SUMS.txt` and `vectors-manifest.json`; the live series was read read-only with `gh release
view`. Assets after the change: leyes 637 (632 vector files), reglamentos 2,171 (2,166),
lineamientos 259 (254), tratados 2,753 (2,748), each with the five metadata files; **no stale asset
on any of the four live releases** (the lists of `gh release delete-asset` are empty) and the
partition into numbered parts is unchanged.

Two things the first version of `package_vectors.py` got wrong for this case, found while checking
the generated files and fixed with tests:

* It always wrote `gh release create`, which fails on a release that is already live. For a part
  whose tag is on GitHub it now writes `gh release edit <tag> --notes-file ...` plus `gh release
  upload <tag> --clobber` for the five metadata files (they always change: `units.parquet`,
  `leaves.parquet`, the manifests, the sums).
* `upload_release_assets.py` skips an asset already on the release **at the same size**, and a
  re-embedded vector file often keeps its size (same number of rows, same dtype). Left alone, the
  published, now-stale bytes would have stayed. `--changed-files` (what `compare_merges.py
  --changed-files-out` writes) adds an explicit `gh release upload --clobber` block for exactly the
  files that keep their name and change content: 5, 8, 4 and 36 per model (the treaties' shared
  file included).

The four release bodies (`.github/scjn-*-vectors.md`) were updated to the rebuilt corpus
(`md2akn` 0.5.1, unit and distinct-text counts, vector counts, shared counts and sizes, in the
lineamientos body also replacing numbers from before issue #256). **Nothing was uploaded**: a human
runs the four `PUBLICAR.md` (issue #115, Hallazgo C), and since the releases are replaced in place the
four must be run **before** the pull request merges, or `legalvec` (and the Atlas chain that reads
the vectors, #294) would pair new units with old vectors. `legalvec download --collection all` was
not re-run end to end on this machine: GitHub answered 500 to part of the asset requests.

### Measured, the Atlas chain and the Instruments page under `md2akn` 0.5.1, 2026-10-14 (issue #294)

Everything downstream of the excerpts was rebuilt from the vectors #291 re-embedded and #293 verified,
**without publishing anything**: the same 2,676 instruments, from `md2akn` 0.5.1's units.

**Staging.** `stage_vectors_cache.py` builds `emb-run-vectors-051-cache/` (gitignored by `/emb-run*/`), a
cache in `legalvec`'s own layout (`<tag>/units.parquet`, `vectors-<clave>-<model>-<K>.parquet`, ...), from the
four `emb-run-<collection>-051/` merges, and verifies every file against the `SHA256SUMS.txt` that
`package_vectors.py` wrote for it (5,814 files, hard-linked, 6 s). It is built from the merges alone and
not from "the published cache plus the changed files": a merge is complete (#293 proved the unchanged
files bit-identical), and the machine's published cache was incomplete anyway (GitHub answered 500 to part
of `legalvec download`). Every script of the chain was pointed at it with `--cache-dir` and
`LEGALVEC_CACHE_DIR`. Two defects surfaced on the way and are fixed with tests: `package_vectors.py`
listed its own `SHA256SUMS.txt` in the sums on a second run, and `instrument_matrix.py --submit` never
forwarded `--cache-dir` into the job, so a Slurm job would have read the *published* units against the
*staged* vectors.

**Slurm on `cemieredes`.** `submit_umap.sh` (`--exclusive -c 60`, the repository's `.venv`) is written for
`geoint`; on a node that also runs GPU jobs `--exclusive` waits for the whole machine, and this checkout has
no `.venv`. `instrument_matrix.py`/`nearest_texts.py` gained `--shared-node` (with `--cpus`, default 32, and
`--mem`, default 64G): it submits through `submit_cpu.sh`, which asks for no exclusivity and runs
`/home/mgraffg/.venvs/cluster/bin/python` (the interpreter `submit.sh` already runs the GPU jobs with;
`$LEGALIA_PYTHON` overrides it) and drops the `geoint0` exclusion. Without the flag the sbatch line is byte for
byte what it was. A BM25 job needs `bm25s`, which that venv lacks: a copy of `bm25s` and `scipy` under
`~/.cache/legalia-pylibs` was put on `PYTHONPATH` (`sbatch` exports the environment), a local choice, not part
of the repository. No job waited for the queue (another user's job held the node's other cores; every job
started at once).

| step | 0.6B | 4B | BM25 |
|---|---:|---:|---:|
| `prepare_umap_input.py` / `prepare_bm25_input.py` | 411 s | 15 min | 42 s |
| matrix job (`instrument_matrix.py`) | 180 s | 340 s | 770 s |
| `build_instrument_umap_html.py` (4 UMAP fits, login node) | 3 min 43 s | not timed | not timed |
| `export_atlas_pairs.py` (login node, 8 cores) | 726 s | 816 s | 971 s |

`nearest_texts.py` took 442 s (job 1592) and its proof passed (`agrees_with_nearest: true`);
`export_instrument_files.py` took 1,242 s (it ran beside two other exports on the login node's 8 cores).

**The data sets.** Same instruments, in the same positions, in all three files (`--instruments-as`):

| | published (0.5.0) | now (0.5.1) |
|---|---:|---:|
| instruments | 2,676 | 2,676 |
| excerpts (`meta.provisions`) | 203,324 | 203,874 |
| headings excluded | 31,172 | 31,172 |
| transitory rows excluded | 26,182 | 26,299 |
| word-for-word shared rows not counted (0.6B and 4B / BM25) | 1,947 / 2,308 | 1,947 / 2,291 |
| counted rows (0.6B and 4B / BM25) | 144,023 / 143,661 | 144,456 / 144,111 |
| distinct texts | 185,456 | 185,968 |

The closest instrument is unchanged for **2,659 of the 2,676** under the 0.6B and under the 4B, and for 2,666
under BM25; `p` changed for 48 instruments (the ones whose rows changed) and `pc` for 43 (71 under BM25). The
three `atlas-pairs` sets have 12,007, 11,986 and 12,077 files (12,002, 11,983 and 12,074 before). The 4B's
nearest texts: 146,403 searched unit rows, 146,089 distinct source texts, 730,445 rows (145,970, 145,693 and
728,465 before).

**The instrument files** (`atlas-instruments`, 2,676 files, 319 MB of JSON, 67.6 MB as the tarball): the
re-parse equals the staged units for **all 2,676**, offsets included, with no tolerance. The parse flags before
and after (instruments / excerpts):

| flag | 0.5.0 | 0.5.1 |
|---|---:|---:|
| `loose` | 497 / 7,270 | 287 / 1,676 |
| `duplicate_number` | 211 / 5,171 | 209 / 4,598 |
| `numbering_gap` | 205 / 1,377 | 203 / 844 |
| `over_window` | 11 / 12 | 11 / 12 |
| `no_articles` | 56 | 56 |
| `no_transitorios` | 1,113 | 1,113 |

`loose` falls by four fifths because a container's opening text is an `intro` and a table is a `table`; the two
numbering flags fall with the decimal articles, which no longer read as their first digit. The treaty `137355`
(the T-MEC protocol) has `art_1.1`, `art_1.2`, ... and 63 `duplicate_number` flags, all of them the annex articles
`Artículo 2.A.1` that #291 reported and did not fix (every decimal article has its own number); `ligie-2022`
(1,184 excerpts, 1,527 before) has `sec_I__intro` as an `intro`, 259 `intro` and 769 `table` excerpts (its tariff
was loose paragraphs) and 13 `loose` ones.

**The page.** `intro` and `table` are nodes like any other (badge, outline entry, excerpt marking, nearest
texts); *Parsed* mode draws a table as an HTML table, one per excerpt (a long table is cut at row boundaries into
pieces, each addressable as `#<eId>.<piece>`; a cell's `\|` is a pipe and `**` bold is kept), *Raw* is unchanged;
the prose names the two kinds of excerpt (*introduction*, *table*). `export_atlas_pairs.py` labels them
`Introduction` and `Table`: it had refused the new unit types with a `ValueError` the first time it ran.

**The evaluations were re-run** (`gold_links.py`, `evaluate_links.py`, `evaluate_citations.py`, 7 min, offline).
The gold needed one fix: signal B read only `article` and `loose` units, and the text a chapter opens with had
become `intro`, so the gold lost 6 links to a relabelling (B 167, 269 links). `SOURCE_UNIT_TYPES` now reads
`intro` too, and the gold is exactly what it was: A 161, B 170, 251 instruments, 275 links, 62 unresolved names,
`_gold-links.md` byte-identical. The numbers that moved, all by the last digit: A's mean reciprocal rank
0.713 / 0.707 / 0.714 → 0.712 / 0.706 / 0.714; at the row level the cited instrument among the five closest for
BM25 0.255 → 0.254, among the ten for the 0.6B 0.334 → 0.335, BM25's mean reciprocal rank 0.180 → 0.179, the
0.6B's median rank 39 → 38.5 and, without the Constitution, 0.387 → 0.386 and BM25's 0.289 → 0.288; two
p-values (0.6B − BM25 and 4B − BM25 on "cited instrument first", 0.09 → 0.10 and 0.08 → 0.07) and one interval
(A and B, 4B − BM25, mean reciprocal rank, `[−0.025, +0.024]` → `[−0.024, +0.024]`). The page's tables carry the
new values and say 14 October 2026. **No conclusion changed**: the three data sets are tied on the closest and
the five closest instruments, the 4B is better row by row (on all five measures against the 0.6B and on four
against BM25), the 0.6B and BM25 are tied.

**Tests.** `test_atlas_page.py` literals follow the data (counts in the Data box, the treaty sentence
`1,285 of the 1,373`, the evaluation tables); the two worked examples (Chapingo and the Constitution) needed no
change, their weights are the same. The browser tests ran for real here (headless Chromium, no Quarto, so the
`rendered_*` ones skip): Playwright's browser needs `libnspr4`/`libnss3`, which this machine does not have and
which were unpacked from the Debian packages into `~/.cache/chromium-libs` (`LD_LIBRARY_PATH`), outside the
repository. **Two Atlas tests were flaky on any data** and are fixed: `test_a_4b_file_that_does_not_fit_...` and
`test_a_bm25_file_that_does_not_fit_...` compare circle positions after a refused model switch and failed now
and then, at the commit before this issue too (`c8d2c792`, two runs in three). The cause was the test helper,
not the page: with 2,676 circles drawn by a software renderer a frame can take over a second, so a transition
stalls and `settled_xs`' two equal reads land on the same frame (a series of samples showed a 1.7 s plateau
before the last 1.6 px). `settled_xs` now starts from d3's own signal, no live `__transition` on the mount
(layout tween) or the svg (zoom), and then confirms with the two equal reads; no assertion was loosened. The
whole file passes (88, nine Quarto skips), three runs of the four tests in a row.

**What a human has to run, in this order, before the pull request merges** (nothing was uploaded): see the
issue's comment and `CLAUDE.md`.


### Measured, repeated numbers, 2026-10-15 (review fix-1 of issue #291)

*Review fix-1 of #291, found after the fact:* browsing the Instruments page showed that 209 of the Atlas'
2,676 instruments carried `duplicate_number` (an article number repeated in the instrument; tratados 89,
reglamentos 49, leyes 41, lineamientos 30; 4,598 flagged excerpts), the worst being `tratados/114738` (the
IMDG code, 482). The repository owner's rule is that no instrument should repeat a number, and the owner
suspected the Markdown writer. Nothing was hidden to meet it: the flag's definition
(`export_instrument_files.excerpt_flags`: article units outside the transitorios sharing a `num`,
whatever their container) is unchanged, no `md2akn` rule merges or renumbers, and no instrument is special-cased.

**Where each repeat comes from.** `scripts/embeddings/measure_repeated_numbers.py` reparses, for each of the 209,
the document the Atlas showed and switches one rule off at a time (`uv run --group viz python
scripts/embeddings/measure_repeated_numbers.py --old-files ... --old-index ... --json ...`); a rule's share is the
flagged excerpts that come back when it is off. Totals over the 209 (4,598 excerpts):

| cause (where it originates) | excerpts | fix |
|---|---:|---|
| the writer, `scjn.api` | 0 | none needed for the numbers; its lead regex is nevertheless widened (see below) |
| the number grammar and the transitorios markers of `md2akn` (rule 14) | 948 | whole article number: `18.-A.-`, `Bis` after an ordinal, lowercase and ordinal letters, more Latin spellings, spaced and `DISPOSICIONES TRANSITORIAS` markers, `(SIC)` |
| a table of contents read as articles (rule 15) | 1,374 | a `toc` node and unit, never an article, outside the rule-8 gate |
| restarted whole numerals read as articles in numeral mode (rule 18) | 449 | the restart is a list item of the article it follows |
| recitals under `CONSIDERANDO` and D.O.F.-headed decrees (rule 17) | 150 | recitals are not articles |
| `PARTE`, `ANEXO`, `APÉNDICE` containers (rule 16) | 0 | containers, so their chapters and articles sit under them; it does not change the container-blind flag |
| what remains | 1,605 | the SCJN's own text, below |

Per collection, before and after: leyes 41 instruments (539 excerpts) to 3 (6), reglamentos 49 (891) to 23 (459),
lineamientos 30 (589) to 1 (2), tratados 89 (2,579) to 46 (1,961). The totals after are 73 instruments and 2,428
excerpts: the 68 of the 209 that still repeat (1,605) plus 5 instruments that were not flagged before
(`tratados/96728`, `108587`, `76945`, `108558` and `reglamentos/992`: 823 excerpts). Those five are not new errors:
the old parse read, for example, the fire-test exposure code (PEF 2010, `96728`) as five units (one article over every
model's window), and the new parse segments it into 1,164, exposing the restart of its `1.1` numbering in each annex.

**The writer.** `scjn.api._LEAD_ARTICULO` bolded only part of some article numbers (`**Artículo 18.-**A.-`), which
`md2akn` ignores because it matches a block with the `**` removed; it now takes the whole number (the grammar is a
copy of `md2akn`'s, since `scjn` imports nothing else of this monorepo) and `TestWholeNumberLeads` in
`packages/scjn/tests/test_api.py` pins it from real fragments. Its share of the repeats is 0, as the ablation
shows, so the re-download was a consistency step and not the cure. The 32 + 1 leyes, 13 reglamentos and 24
tratados whose bold or header changed were re-downloaded and repackaged (`PUBLICAR.md` generated, not run); a snapshot
is adopted from the fresh answer only when the two differ in bold or in the header, and otherwise the old file
is kept (150 snapshots), because the fresh answer turns tab-indented text into `|  | text |` rows (#253) and the
repository owner did not ask for that. The five text-less reglamentos' `estado.json` were rebuilt from the
published index so the repackaged index and manifest stay complete.

**`114738`.** `duplicate_number` is 0. The document is two amendments ("annex 1" with parts 1 and A, "annex 2" with
part 3 and the two appendices A and B, plus the annexes between them), so parts 1 to 7 are `part` nodes holding
their own chapters (`1.1`, `1.2`, ...), the text of part 3 sits under part 3, `APÉNDICES A` and `B` are `attachment` nodes at the
end, and the table of contents are `toc` nodes, not articles. Two chapter headings of part 3 are still odd (`3.1 – Generalidades`
holding a dangerous-goods row), a limit of the parse that no repeated number reveals.

**What remains, for the repository owner.** The 73 instruments below still repeat a number under the unchanged
definition. In each, the repeated number is in the SCJN's own API answer (the rows are listed by their `referencia`
for the newest reform), and none is hidden. The causes are of four kinds: numbering that restarts in an annex or an
appendix (the instrument's own text numbers its annexes from 1), in each Part or in a later chapter, title or document
(a law printed together with its protocol, or a congress's decisions), two API rows carrying the same number
(`ARTÍCULO 6` and `ARTÍCULO 6 (SIC)`, a number reused for an `ENCABEZADO` row, the same reform printed twice), and a
few rows with two openers inside one row. Questions: should an annex or part restart count as a repetition (the flag
is container-blind by definition), and should a `(SIC)` row repeat its number at all?

| collection | key | instrument | repeated excerpts | what repeats | evidence in the API answer |
|---|---|---|---:|---|---|
| leyes | `cjm` | CÓDIGO de Justicia Militar | 2 | two SCJN rows (or two documents) carry the same number | no. 916: row 1105 “ARTÍCULO  915”; row 1106 “ARTÍCULO  916” |
| leyes | `cmpp` | CÓDIGO Militar de Procedimientos Penales | 2 | two SCJN rows (or two documents) carry the same number | no. 396: two openers inside one row (row 471) |
| leyes | `lieps` | LEY del Impuesto Especial sobre Producción y | 2 | two SCJN rows (or two documents) carry the same number | no. 23: row 50 “ARTÍCULO  23”; row 51 “ARTÍCULO  23-BIS” |
| lineamientos | `109459` | LINEAMIENTOS QUE RIGEN LA OPERACION DEL CENT | 2 | numbering restarts in a later chapter, title or document | no. TERCERO: parsed from one row |
| reglamentos | `975` | REGLAMENTO GENERAL PARA EL SERVICIO DE SANID | 136 | numbering restarts in each Part | no. 1°: row 3 “ARTÍCULO  1”; row 86 “ARTÍCULO  1” |
| reglamentos | `103948` | REGLAMENTO DE PROCEDIMIENTOS SANCIONADORES E | 96 | numbering restarts inside an annex or appendix | no. 1: row 6 “ARTÍCULO 1”; row 70 “D.O.F. 10 DE NOVIEMBRE D” |
| reglamentos | `965` | REGLAMENTO GENERAL DE REGIONES Y ZONAS MILIT | 64 | numbering restarts in each Part | no. 1°: row 4 “ARTÍCULO  1”; row 61 “ARTÍCULO  1” |
| reglamentos | `67602` | REGLAMENTO PARA LA DESTRUCCION DE FORMATOS D | 56 | numbering restarts in a later chapter, title or document | no. 1: row 10 “ÍNDICE”; row 13 “ARTÍCULO  1” |
| reglamentos | `952` | REGLAMENTO GENERAL DE INFANTERIA | 41 | numbering restarts in a later chapter, title or document | no. 2°: row 9 “ARTÍCULO 2”; row 42 “ARTÍCULO 2” |
| reglamentos | `61683` | REGLAMENTO DE LA CORTE SUPREMA DE JUSTICIA D | 10 | numbering restarts in a later chapter, title or document | no. 1: row 16 “ARTÍCULO 1”; row 30 “ARTÍCULO 1”; row 34 “ARTÍCULO 1” |
| reglamentos | `116367` | REGLAMENTO DE ELECCIONES DEL INSTITUTO NACIO | 8 | two SCJN rows (or two documents) carry the same number | no. 4: row 6 “ARTÍCULO 4”; row 631 “D.O.F. 17 DE OCTUBRE DE ” |
| reglamentos | `992` | REGLAMENTO PARA PREVENIR Y CONTROLAR LA CONT | 6 | numbering restarts inside an annex or appendix | no. 1: row 3 “ARTÍCULO  1”; row 44 “D.O.F. 19 DE SEPTIEMBRE ” |
| reglamentos | `14971` | REGLAMENTO DE LA COMISION INTERINSTITUCIONAL | 6 | two SCJN rows (or two documents) carry the same number | no. 1: row 0 “ENCABEZADO”; row 2 “ARTÍCULO  1” |
| reglamentos | `182893` | REGLAMENTO INTERIOR DEL CONSEJO NACIONAL DEL | 6 | numbering restarts in a later chapter, title or document | no. 1: row 1 “ARTÍCULO 1”; row 7 “ARTÍCULO 1” |
| reglamentos | `1000` | REGLAMENTO DE ADMISION A LAS ESCUELAS NAVALE | 4 | numbering restarts inside an annex or appendix | no. 41: row 48 “ARTÍCULO  41”; row 57 “ANEXO NÚMERO CUATRO” |
| reglamentos | `179970` | REGLAMENTO DEL CENTRO PUBLICO DE MECANISMOS  | 4 | numbering restarts in a later chapter, title or document | no. 1: row 0 “ENCABEZADO”; row 4 “ARTÍCULO 1” |
| reglamentos | `567` | REGLAMENTO DE LOS SERVICIOS DE GUARDERIAS PA | 2 | two SCJN rows (or two documents) carry the same number | no. 6o: row 8 “ARTÍCULO  6”; row 9 “ARTÍCULO  6 (SIC)” |
| reglamentos | `878` | REGLAMENTO NACIONAL DE CARRERAS DE CABALLOS  | 2 | numbering restarts in a later chapter, title or document | no. 152: row 158 “ARTÍCULO  152 (SIC)”; row 169 “ARTÍCULO  152” |
| reglamentos | `925` | REGLAMENTO DE CEREMONIAL MILITAR | 2 | two SCJN rows (or two documents) carry the same number | no. UNICO: row 251 “D.O. 23 DE JULIO DE 1949”; row 252 “D.O. 19 DE DICIEMBRE DE ”; row 253 “D.O.F. 14 DE SEPTIEMBRE ” |
| reglamentos | `10911` | REGLAMENTO PARA LA IMPOSICION DE MULTAS POR  | 2 | two SCJN rows (or two documents) carry the same number | no. 16: row 21 “ARTÍCULO  15”; row 22 “ARTÍCULO  16” |
| reglamentos | `1152` | REGLAMENTO INTERIOR DEL BANCO DE MEXICO | 2 | two SCJN rows (or two documents) carry the same number | no. 14: row 19 “ARTÍCULO  14”; row 20 “ARTÍCULO  14 BIS” |
| reglamentos | `15305` | REGLAMENTO QUE ESTABLECE LOS LINEAMIENTOS PA | 2 | two SCJN rows (or two documents) carry the same number | no. 18: two openers inside one row (row 30) |
| reglamentos | `31949` | REGLAMENTO DEL SORTEO MELATE-REVANCHA DE PRO | 2 | numbering restarts in a later chapter, title or document | no. 47o: row 59 “ARTÍCULO  47”; row 61 “ARTÍCULO  47 (SIC)” |
| reglamentos | `62098` | REGLAMENTO DEL INSTITUTO FEDERAL ELECTORAL P | 2 | two SCJN rows (or two documents) carry the same number | no. 20: row 38 “ARTÍCULO  20”; row 39 “ARTÍCULO  20 (SIC)” |
| reglamentos | `95146` | REGLAMENTO DE VIGILANCIA DEL DESEMPEÑO JURIS | 2 | two SCJN rows (or two documents) carry the same number | no. 68: row 88 “ARTÍCULO 68”; row 89 “ARTÍCULO 68 (SIC)” |
| reglamentos | `180418` | REGLAMENTO DE SESIONES DE LA SALA SUPERIOR | 2 | numbering restarts in a later chapter, title or document | no. 1: row 0 “ENCABEZADO”; row 6 “ARTÍCULO 1” |
| reglamentos | `178268` | REGLAMENTO GENERAL PARA LAS ESCUELAS DE LOS  | 2 | numbering restarts in a later chapter, title or document | no. 1: row 0 “ENCABEZADO”; row 5 “ARTÍCULO 1” |
| tratados | `96728` | CODIGO INTERNACIONAL PARA LA APLICACION DE P | 787 | numbering restarts inside an annex or appendix | no. 1.1: parsed from one row |
| tratados | `96707` | CODIGO INTERNACIONAL PARA LA CONSTRUCCION Y  | 153 | numbering restarts inside an annex or appendix | no. 1.1: parsed from one row |
| tratados | `11293` | CONVENIO INTERNACIONAL PARA LA SEGURIDAD DE  | 130 | numbering restarts inside an annex or appendix | no. 11.1: parsed from one row |
| tratados | `50540` | DECISIONES DEL XXIII CONGRESO DE BUCAREST 20 | 129 | numbering restarts in a later chapter, title or document | no. 1: row 13 “ARTÍCULO  1”; row 106 “ARTÍCULO  1”; row 140 “ARTÍCULO  1” |
| tratados | `43712` | DECISIONES DEL XXII CONGRESO DE BEIJING 1999 | 118 | numbering restarts in a later chapter, title or document | no. I: row 8 “ARTÍCULO  I”; row 210 “ARTÍCULO  I” |
| tratados | `125636` | CONVENCION CONJUNTA SOBRE SEGURIDAD EN LA GE | 54 | two SCJN rows (or two documents) carry the same number | no. 18: row 7 “PREÁMBULO”; row 29 “ARTÍCULO 18” |
| tratados | `24074` | ACUERDO SOBRE EL ESTABLECIMIENTO DE LA COMIS | 48 | numbering restarts inside an annex or appendix | no. III: row 10 “ACUERDO”; row 22 “ARTÍCULO III”; row 90 “ARTÍCULO III” |
| tratados | `89657` | CONVENIO DE GINEBRA RELATIVO AL TRATO DE LOS | 37 | numbering restarts inside an annex or appendix | no. 1: row 8 “ARTÍCULO  1”; row 183 “ANEXO II”; row 184 “ANEXO III” |
| tratados | `67952` | ACUERDO RELATIVO A GIROS POSTALES Y LOS BONO | 36 | numbering restarts in a later chapter, title or document | no. 1: row 4 “ARTÍCULO  1”; row 111 “ARTÍCULO  1” |
| tratados | `11248` | CONVENCION SOBRE ASISTENCIA EN CASO DE ACCID | 34 | two SCJN rows (or two documents) carry the same number | no. 1: row 7 “ARTÍCULO  1”; row 27 “ARTÍCULO  1” |
| tratados | `89658` | CONVENIO DE GINEBRA RELATIVO A LA PROTECCION | 34 | numbering restarts inside an annex or appendix | no. 1: row 8 “ARTÍCULO  1”; row 191 “ANEXO I”; row 192 “ANEXO II” |
| tratados | `26628` | CONVENIO-MARCO RELATIVO A LA EJECUCION DE LA | 31 | numbering restarts inside an annex or appendix | no. 1: row 7 “ARTÍCULO  1”; row 20 “CONTENIDO DEL ANEXO”; row 22 “ANEXO 1” |
| tratados | `23837` | CONVENIO RELATIVO A LA RADIODIFUSION EN FREC | 28 | numbering restarts inside an annex or appendix | no. 3: row 6 “INDICE”; row 12 “ARTÍCULO  3” |
| tratados | `47860` | ACTAS FINALES DE LA CONFERENCIA MUNDIAL DE R | 27 | numbering restarts inside an annex or appendix | no. 1: row 8 “ARTÍCULOS”; row 9 “APÉNDICES” |
| tratados | `89655` | CONVENIO DE GINEBRA PARA MEJORAR LA SUERTE D | 26 | numbering restarts inside an annex or appendix | no. 1: row 7 “ARTÍCULO  1”; row 81 “ANEXO I” |
| tratados | `108587` | CONVENIO DE MINAMATA SOBRE EL MERCURIO (MULT | 22 | numbering restarts in each Part | no. 1: row 8 “ARTÍCULO 1”; row 50 “ARTÍCULO 1”; row 68 “ARTÍCULO 1” |
| tratados | `16189` | CONVENCION Y ESTATUTO SOBRE EL REGIMEN INTER | 20 | two SCJN rows (or two documents) carry the same number | no. 1: row 4 “ARTÍCULO  1”; row 16 “ARTÍCULO  1” |
| tratados | `23952` | CONVENIO PARA LA REPRESION DE ACTOS ILICITOS | 20 | two SCJN rows (or two documents) carry the same number | no. 1: row 7 “ARTÍCULO  1”; row 31 “CONTENIDO DEL PROTOCOLO” |
| tratados | `44552` | ACTAS FINALES DE LA CONFERENCIA MUNDIAL DE R | 20 | numbering restarts inside an annex or appendix | no. 1: two openers inside one row (row 16) |
| tratados | `60292` | CONVENIO CONSTITUTIVO DEL ORGANISMO MULTILAT | 19 | numbering restarts inside an annex or appendix | no. 1: row 8 “ARTÍCULO  1”; row 86 “ANEXO 1”; row 87 “ANEXO II (SIC)” |
| tratados | `50539` | CONVENIO CONSTITUTIVO DEL FONDO MULTILATERAL | 17 | numbering restarts inside an annex or appendix | no. I: row 7 “ARTÍCULO I”; row 16 “ARTÍCULO I”; row 24 “ANEXO A” |
| tratados | `66295` | CONVENIO INTERNACIONAL PARA EL ESTABLECIMIEN | 16 | two SCJN rows (or two documents) carry the same number | no. 1: row 3 “ARTÍCULO  1”; row 11 “ANEXO DEL CONVENIO” |
| tratados | `67984` | CONVENCION SOBRE UN CODIGO DE CONDUCTA DE LA | 16 | numbering restarts in each Part | no. 3: row 13 “ARTÍCULO  3”; row 73 “PARTE FINAL DE LA CONVEN” |
| tratados | `125345` | ACUERDO SOBRE UN PROGRAMA INTERNACIONAL DE E | 16 | numbering restarts inside an annex or appendix | no. 2: row 10 “ARTÍCULO 2”; row 108 “ARTÍCULO 2” |
| tratados | `84855` | ACTAS FINALES DE LA CONFERENCIA MUNDIAL DE R | 14 | numbering restarts inside an annex or appendix | no. 2: row 7 “ÍNDICE”; row 13 “ANEXO 2”; row 14 “ANEXO 3” |
| tratados | `18122` | CONVENIO PARA LA COOPERACION EN EL MARCO DE  | 12 | two SCJN rows (or two documents) carry the same number | no. 1: row 7 “ARTÍCULO  1”; row 18 “CONTENIDO DEL ESTATUTO” |
| tratados | `23774` | CONVENCION CONSTITUTIVA DE LA ORGANIZACION M | 11 | two SCJN rows (or two documents) carry the same number | no. 29: two openers inside one row (row 7) |
| tratados | `16165` | TRATADO PARA EVITAR O PREVENIR CONFLICTOS EN | 10 | numbering restarts inside an annex or appendix | no. I: row 4 “ARTÍCULO  I”; row 14 “APÉNDICE” |
| tratados | `24154` | ACUERDO PARA LA PROMOCION Y PROTECCION RECIP | 10 | numbering restarts inside an annex or appendix | no. PRIMERO: row 7 “ARTÍCULO PRIMERO”; row 21 “ANEXO” |
| tratados | `11422` | TRATADO DEFINITIVO DE PAZ Y AMISTAD (S.M.C.  | 8 | two SCJN rows (or two documents) carry the same number | no. I: row 2 “ARTÍCULO  I”; row 12 “DECLARACIÓN” |
| tratados | `16296` | ACUERDO DE ASOCIACION ECONOMICA, CONCERTACIO | 6 | numbering restarts inside an annex or appendix | no. 1: row 2 “PRIMERO”; row 3 “SEGUNDO” |
| tratados | `22544` | TRATADO SOBRE DISTRIBUCION DE AGUAS INTERNAC | 6 | two SCJN rows (or two documents) carry the same number | no. 1º: row 4 “ARTÍCULO  1”; row 44 “CONTENIDO DE LA APROBACI” |
| tratados | `85041` | CONVENIO IBEROAMERICANO SOBRE EL USO DE LA V | 6 | numbering restarts in a later chapter, title or document | no. 1º: row 8 “ARTÍCULO 1”; row 22 “PROTOCOLO” |
| tratados | `19438` | TRATADO GENERAL DE ARBITRAJE INTERAMERICANO  | 4 | two SCJN rows (or two documents) carry the same number | no. 1: row 4 “ARTÍCULO  1”; row 16 “CONTENIDO DEL PROTOCOLO” |
| tratados | `23743` | CONVENCION SOBRE EXTRADICION (MULTILATERAL,  | 4 | two SCJN rows (or two documents) carry the same number | no. 2: row 4 “ARTÍCULO  2”; row 27 “RESERVAS” |
| tratados | `23966` | CONVENCION SOBRE PRESCRIPCION EN MATERIA DE  | 4 | two SCJN rows (or two documents) carry the same number | no. 43: row 62 “ARTÍCULO  43”; row 64 “ARTÍCULO  43 (SIC)” |
| tratados | `49685` | ACUERDO PARA LA PROMOCION Y PROTECCION RECIP | 4 | numbering restarts in a later chapter, title or document | no. 1: row 8 “ARTÍCULO  1”; row 39 “PROTOCOLO” |
| tratados | `50610` | TRATADO PARA LA EXTRADICION DE CRIMINALES (1 | 4 | two SCJN rows (or two documents) carry the same number | no. I: row 3 “ARTÍCULO I”; row 25 “ARTÍCULO I” |
| tratados | `76945` | CONVENCION INTERAMERICANA SOBRE TRANSPARENCI | 4 | numbering restarts inside an annex or appendix | no. III: row 10 “ARTÍCULO  III”; row 23 “ANEXOS” |
| tratados | `108558` | CONVENIO SOBRE RECONOCIMIENTO MUTUO DE DIPLO | 4 | two SCJN rows (or two documents) carry the same number | no. 2.1: parsed from one row |
| tratados | `11440` | TRATADO DE PAZ, AMISTAD, LIMITES Y ARREGLO D | 2 | two SCJN rows (or two documents) carry the same number | no. IX: row 10 “ARTÍCULO  IX”; row 27 “TEXTO DE LAS MODIFICACIO” |
| tratados | `14273` | CONVENCION PARA EL ESTABLECIMIENTO DE UNA CO | 2 | two SCJN rows (or two documents) carry the same number | no. IV: row 10 “ARTÍCULO  IV”; row 14 “CONTENIDO DE LAS NOTAS” |
| tratados | `22834` | CONSTITUCION DE LA ORGANIZACION INTERNACIONA | 2 | two SCJN rows (or two documents) carry the same number | no. 18: two openers inside one row (row 6) |
| tratados | `23762` | CONVENCION SOBRE EL INSTITUTO INTERAMERICANO | 2 | two SCJN rows (or two documents) carry the same number | no. IV: two openers inside one row (row 6) |
| tratados | `28571` | ACUERDO DE CONSTITUCION DEL INSTITUTO INTERN | 2 | two SCJN rows (or two documents) carry the same number | no. IV: row 11 “ARTÍCULO  IV”; row 12 “ARTÍCULO  IV (SIC)” |
| tratados | `89155` | PROTOCOLO DE REFORMAS A LA CARTA DE LA ORGAN | 2 | two SCJN rows (or two documents) carry the same number | no. 8: row 8 “ARTÍCULO  II”; row 10 “ARTÍCULO  IV” |

**Per instrument, the 209 that were flagged** (excerpts flagged under each cause when only that rule is off, and the
count the Atlas shows now):

| collection | key | instrument | before | number grammar and markers | table of contents (15) | recitals (17) | restarted numerals (18) | writer | after |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| leyes | `ccom` | CÓDIGO de Comercio | 127 | 127 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lfd` | LEY Federal de Derechos | 92 | 92 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lmeum` | LEY Monetaria de los Estados Unidos Mexi | 38 | 38 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lfpca` | LEY Federal de Procedimiento Contencioso | 34 | 34 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lieps` | LEY del Impuesto Especial sobre Producci | 26 | 24 | 0 | 0 | 0 | 0 | 2 |
| leyes | `liva` | LEY del Impuesto al Valor Agregado | 26 | 26 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lgtoc` | LEY General de Títulos y Operaciones de  | 23 | 23 | 0 | 0 | 0 | 0 | 0 |
| leyes | `ccf` | CÓDIGO Civil Federal | 21 | 21 | 0 | 0 | 0 | 0 | 0 |
| leyes | `cff` | CÓDIGO Fiscal de la Federación | 16 | 16 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lfped` | LEY Federal para Prevenir y Eliminar la  | 11 | 11 | 0 | 0 | 0 | 0 | 0 |
| leyes | `cjm` | CÓDIGO de Justicia Militar | 9 | 7 | 0 | 0 | 0 | 0 | 2 |
| leyes | `lgoaac` | LEY General de Organizaciones y Activida | 9 | 9 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lcf` | LEY de Coordinación Fiscal | 7 | 7 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lgs` | LEY General de Salud | 7 | 7 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lac` | LEY de Aviación Civil | 6 | 6 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lfpc` | LEY Federal de Protección al Consumidor | 6 | 6 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lic` | LEY de Instituciones de Crédito | 6 | 6 | 0 | 0 | 0 | 0 | 0 |
| leyes | `cpf` | CÓDIGO Penal Federal | 5 | 5 | 0 | 0 | 0 | 0 | 0 |
| leyes | `laero` | LEY de Aeropuertos | 5 | 5 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lccbid` | LEY que Establece Bases para la Ejecució | 5 | 5 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lfcdo` | LEY Federal contra la Delincuencia Organ | 5 | 5 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lft` | LEY Federal del Trabajo | 5 | 5 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lsat` | LEY del Servicio de Administración Tribu | 5 | 5 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lebhn` | LEY sobre el Escudo, la Bandera y el Him | 4 | 4 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lfmzaah` | LEY Federal sobre Monumentos y Zonas Arq | 4 | 4 | 0 | 0 | 0 | 0 | 0 |
| leyes | `loefam` | LEY Orgánica del Ejército y Fuerza Aérea | 4 | 4 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lrsic` | LEY para Regular las Sociedades de Infor | 4 | 4 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lcm` | LEY de Concursos Mercantiles | 3 | 3 | 0 | 0 | 0 | 0 | 0 |
| leyes | `cmpp` | CÓDIGO Militar de Procedimientos Penales | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| leyes | `lcpaf` | LEY de Caminos, Puentes y Autotransporte | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lcs` | LEY Sobre el Contrato de Seguro | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| leyes | `ldofgg` | LEY del Diario Oficial de la Federación  | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| leyes | `ldpam` | LEY de los Derechos de las Personas Adul | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lfda` | LEY Federal del Derecho de Autor | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lfpa` | LEY Federal de Procedimiento Administrat | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lgbn` | LEY General de Bienes Nacionales | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lgdnna` | LEY General de los Derechos de Niñas, Ni | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lgv` | LEY General de Víctimas | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lpdusf` | LEY de Protección y Defensa al Usuario d | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lrfxiiib-art123` | LEY Reglamentaria de la Fracción XIII Bi | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| leyes | `lsnieg` | LEY del Sistema Nacional de Información  | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| lineamientos | `180530` | LINEAMIENTOS PARA LA EVALUACION, CERTIFI | 80 | 0 | 80 | 0 | 0 | 0 | 0 |
| lineamientos | `180527` | LINEAMIENTOS DE CAPACITACION PARA LA FOR | 68 | 0 | 68 | 0 | 0 | 0 | 0 |
| lineamientos | `180532` | LINEAMIENTOS Y BASES PARA INVITACION Y P | 60 | 0 | 60 | 0 | 0 | 0 | 0 |
| lineamientos | `180528` | LINEAMIENTOS DE CREACION DEL A PLATAFORM | 34 | 0 | 34 | 0 | 0 | 0 | 0 |
| lineamientos | `180529` | LINEAMIENTOS DE CREACION, ACTUALIZACION  | 30 | 0 | 30 | 0 | 0 | 0 | 0 |
| lineamientos | `93817` | LINEAMIENTOS GENERALES Y ESPECIFICOS PAR | 29 | 0 | 0 | 0 | 29 | 0 | 0 |
| lineamientos | `90710` | LINEAMIENTOS PARA LA ADMINISTRACION, ADQ | 28 | 0 | 0 | 0 | 28 | 0 | 0 |
| lineamientos | `87314` | LINEAMIENTOS PARA LA ELABORACION, MODIFI | 24 | 0 | 0 | 0 | 0 | 0 | 0 |
| lineamientos | `72249` | LINEAMIENTOS PARA LA RECEPCION, REGISTRO | 22 | 0 | 0 | 0 | 22 | 0 | 0 |
| lineamientos | `81411` | LINEAMIENTOS DEL PROGRAMA DE SERVICIO SO | 20 | 0 | 0 | 0 | 0 | 0 | 0 |
| lineamientos | `160908` | LINEAMIENTOS POR LOS QUE SE ESTABLECE EL | 18 | 0 | 0 | 18 | 0 | 0 | 0 |
| lineamientos | `168971` | LINEAMIENTOS QUE REGULAN LA GESTION Y EV | 18 | 0 | 0 | 0 | 18 | 0 | 0 |
| lineamientos | `178432` | LINEAMIENTOS [QUE ESTABLECEN LAS MEDIDAS | 18 | 0 | 0 | 18 | 0 | 0 | 0 |
| lineamientos | `139056` | LINEAMIENTOS POR LOS QUE SE ESTABLECE EL | 12 | 0 | 0 | 12 | 0 | 0 | 0 |
| lineamientos | `88543` | LINEAMIENTOS PARA EL MANEJO DE FONDOS FI | 12 | 0 | 0 | 0 | 0 | 0 | 0 |
| lineamientos | `95755` | LINEAMIENTOS PARA EL MANEJO DE FONDOS FI | 12 | 0 | 0 | 0 | 0 | 0 | 0 |
| lineamientos | `54339` | LINEAMIENTOS RELATIVOS A LAS MEDIDAS DE  | 10 | 0 | 0 | 10 | 0 | 0 | 0 |
| lineamientos | `54342` | LINEAMIENTOS RELATIVOS A LOS GASTOS POR  | 10 | 0 | 0 | 10 | 0 | 0 | 0 |
| lineamientos | `62338` | LINEAMIENTOS DEL SIETE DE MARZO DE 2007, | 10 | 0 | 0 | 10 | 0 | 0 | 0 |
| lineamientos | `178461` | LINEAMIENTOS DE LA COMISION MIXTA DE ESC | 8 | 0 | 0 | 8 | 0 | 0 | 0 |
| lineamientos | `54334` | LINEAMIENTOS RELATIVOS AL FOTOCOPIADO DE | 8 | 0 | 0 | 8 | 0 | 0 | 0 |
| lineamientos | `54335` | LINEAMIENTOS RELATIVOS A LAS MEDIDAS DE  | 8 | 0 | 0 | 8 | 0 | 0 | 0 |
| lineamientos | `54336` | LINEAMIENTOS RELATIVOS A LAS MEDIDAS DE  | 8 | 0 | 0 | 8 | 0 | 0 | 0 |
| lineamientos | `54338` | LINEAMIENTOS RELATIVOS A LAS ACCIONES PA | 8 | 0 | 0 | 8 | 0 | 0 | 0 |
| lineamientos | `54340` | LINEAMIENTOS RELATIVOS A LAS MEDIDAS DE  | 8 | 0 | 0 | 8 | 0 | 0 | 0 |
| lineamientos | `54341` | LINEAMIENTOS RELATIVOS A LOS MECANISMOS  | 8 | 0 | 0 | 8 | 0 | 0 | 0 |
| lineamientos | `63485` | LINEAMIENTOS DEL CATORCE DE NOVIEMBRE DE | 8 | 0 | 0 | 8 | 0 | 0 | 0 |
| lineamientos | `63487` | LINEAMIENTOS Y DISPOSICIONES REGLAMENTAR | 4 | 0 | 0 | 4 | 0 | 0 | 0 |
| lineamientos | `99972` | LINEAMIENTOS DEL FONDO DE RESERVA INDIVI | 4 | 0 | 0 | 4 | 0 | 0 | 0 |
| lineamientos | `109459` | LINEAMIENTOS QUE RIGEN LA OPERACION DEL  | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| reglamentos | `975` | REGLAMENTO GENERAL PARA EL SERVICIO DE S | 136 | 0 | 0 | 0 | 0 | 0 | 136 |
| reglamentos | `180526` | REGLAMENTO INTERNO DEL CONSEJO NACIONAL  | 134 | 0 | 134 | 0 | 0 | 0 | 0 |
| reglamentos | `164927` | REGLAMENTO DE ESTUDIOS DE POSGRADO DEL C | 106 | 0 | 106 | 0 | 0 | 0 | 0 |
| reglamentos | `103948` | REGLAMENTO DE PROCEDIMIENTOS SANCIONADOR | 96 | 0 | 0 | 0 | 0 | 0 | 96 |
| reglamentos | `965` | REGLAMENTO GENERAL DE REGIONES Y ZONAS M | 64 | 0 | 0 | 0 | 0 | 0 | 64 |
| reglamentos | `67602` | REGLAMENTO PARA LA DESTRUCCION DE FORMAT | 56 | 0 | 0 | 0 | 0 | 0 | 56 |
| reglamentos | `1322` | REGLAMENTO DE AUTOTRANSPORTE FEDERAL Y S | 52 | 52 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `952` | REGLAMENTO GENERAL DE INFANTERIA | 42 | 1 | 0 | 0 | 0 | 0 | 41 |
| reglamentos | `116367` | REGLAMENTO DE ELECCIONES DEL INSTITUTO N | 24 | 16 | 0 | 0 | 0 | 0 | 8 |
| reglamentos | `109182` | REGLAMENTO INTERNO DEL TRIBUNAL ELECTORA | 20 | 20 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `46360` | REGLAMENTO DE PRONOSTICOS INTERACTIVOS | 12 | 0 | 0 | 0 | 12 | 0 | 0 |
| reglamentos | `1027` | REGLAMENTO ORGANICO PARA EL CURSO DE INS | 10 | 10 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `31947` | REGLAMENTO  DEL CONCURSO PROGOL DE PRONO | 10 | 10 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `61683` | REGLAMENTO DE LA CORTE SUPREMA DE JUSTIC | 10 | 0 | 0 | 0 | 0 | 0 | 10 |
| reglamentos | `40532` | REGLAMENTO GENERAL DEL COLEGIO DEL AIRE | 9 | 9 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `1152` | REGLAMENTO INTERIOR DEL BANCO DE MEXICO | 7 | 5 | 0 | 0 | 0 | 0 | 2 |
| reglamentos | `14971` | REGLAMENTO DE LA COMISION INTERINSTITUCI | 6 | 0 | 0 | 0 | 0 | 0 | 6 |
| reglamentos | `182893` | REGLAMENTO INTERIOR DEL CONSEJO NACIONAL | 6 | 0 | 0 | 0 | 0 | 0 | 6 |
| reglamentos | `31948` | REGLAMENTO DEL SORTEO CHISPAZO DE PRONOS | 6 | 6 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `31949` | REGLAMENTO DEL SORTEO MELATE-REVANCHA DE | 6 | 4 | 0 | 0 | 0 | 0 | 2 |
| reglamentos | `429` | REGLAMENTO DE TRANSITO EN CARRETERAS FED | 6 | 6 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `1000` | REGLAMENTO DE ADMISION A LAS ESCUELAS NA | 4 | 0 | 0 | 0 | 0 | 0 | 4 |
| reglamentos | `1183` | REGLAMENTO SOBRE LAS INSTITUCIONES NACIO | 4 | 4 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `1791` | REGLAMENTO GENERAL PARA LA CAMPAÑA CONTR | 4 | 4 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `179970` | REGLAMENTO DEL CENTRO PUBLICO DE MECANIS | 4 | 0 | 0 | 0 | 0 | 0 | 4 |
| reglamentos | `21170` | REGLAMENTO DE LA ESCUELA SUPERIOR DE GUE | 4 | 4 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `46354` | REGLAMENTO DE FUNCIONAMIENTO DE LOS SORT | 4 | 4 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `49` | REGLAMENTO DE LA ESCUELA MILITAR DE INGE | 4 | 4 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `100939` | REGLAMENTO DE LA LEY GENERAL DE CULTURA  | 3 | 3 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `36110` | REGLAMENTO INTERIOR DE LA COMISION FEDER | 3 | 3 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `925` | REGLAMENTO DE CEREMONIAL MILITAR | 3 | 1 | 0 | 0 | 0 | 0 | 2 |
| reglamentos | `10911` | REGLAMENTO PARA LA IMPOSICION DE MULTAS  | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| reglamentos | `11120` | REGLAMENTO INTERIOR DE LA SECRETARIA DE  | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `123` | REGLAMENTO DE LA ESCUELA MILITAR DE MATE | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `15305` | REGLAMENTO QUE ESTABLECE LOS LINEAMIENTO | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| reglamentos | `15324` | REGLAMENTO DEL INSTITUTO MEXICANO DE LA  | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `173` | REGLAMENTO DE LA LEY DE PRESUPUESTO, CON | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `178268` | REGLAMENTO GENERAL PARA LAS ESCUELAS DE  | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| reglamentos | `180418` | REGLAMENTO DE SESIONES DE LA SALA SUPERI | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| reglamentos | `21471` | REGLAMENTO DE LA ESCUELA MILITAR DE GRAD | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `33592` | REGLAMENTO DE FUNCIONAMIENTO DE LOS CONC | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `39766` | REGLAMENTO PARA EL SORTEO PRONOSTICOS RA | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `53` | REGLAMENTO PARA EL TRANSPORTE TERRESTRE  | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `565` | REGLAMENTO SOBRE PUBLICACIONES Y REVISTA | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `567` | REGLAMENTO DE LOS SERVICIOS DE GUARDERIA | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| reglamentos | `62098` | REGLAMENTO DEL INSTITUTO FEDERAL ELECTOR | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| reglamentos | `72` | REGLAMENTO DE LA LEY DEL SERVICIO PUBLIC | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| reglamentos | `878` | REGLAMENTO NACIONAL DE CARRERAS DE CABAL | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| reglamentos | `95146` | REGLAMENTO DE VIGILANCIA DEL DESEMPEÑO J | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| tratados | `114738` | CODIGO MARITIMO INTERNACIONAL DE MERCANC | 482 | 0 | 482 | 0 | 0 | 0 | 0 |
| tratados | `96707` | CODIGO INTERNACIONAL PARA LA CONSTRUCCIO | 153 | 0 | 0 | 0 | 0 | 0 | 153 |
| tratados | `50540` | DECISIONES DEL XXIII CONGRESO DE BUCARES | 131 | 1 | 1 | 0 | 0 | 0 | 129 |
| tratados | `11293` | CONVENIO INTERNACIONAL PARA LA SEGURIDAD | 130 | 0 | 0 | 0 | 0 | 0 | 130 |
| tratados | `75190` | ACUERDO DE COMPLEMENTACION ECONOMICA No. | 129 | 129 | 0 | 0 | 0 | 0 | 0 |
| tratados | `43712` | DECISIONES DEL XXII CONGRESO DE BEIJING  | 119 | 0 | 1 | 0 | 0 | 0 | 118 |
| tratados | `23890` | CONVENIO INTERNACIONAL PARA LA PROTECCIO | 84 | 0 | 84 | 0 | 0 | 0 | 0 |
| tratados | `24846` | TRATADO DE LA OMPI SOBRE INTERPRETACION  | 66 | 0 | 66 | 0 | 0 | 0 | 0 |
| tratados | `137355` | PROTOCOLO POR EL QUE SE SUSTITUYE EL TRA | 63 | 63 | 0 | 0 | 0 | 0 | 0 |
| tratados | `125636` | CONVENCION CONJUNTA SOBRE SEGURIDAD EN L | 54 | 0 | 0 | 0 | 0 | 0 | 54 |
| tratados | `15733` | TRATADO DE LA OMPI SOBRE DERECHO DE AUTO | 50 | 0 | 50 | 0 | 0 | 0 | 0 |
| tratados | `24074` | ACUERDO SOBRE EL ESTABLECIMIENTO DE LA C | 50 | 0 | 2 | 0 | 0 | 0 | 48 |
| tratados | `23943` | ACTAS FINALES DE LA CONFERENCIA ADMINIST | 49 | 0 | 0 | 0 | 49 | 0 | 0 |
| tratados | `17152` | TRATADO DE BUDAPEST SOBRE EL RECONOCIMIE | 40 | 0 | 40 | 0 | 0 | 0 | 0 |
| tratados | `89657` | CONVENIO DE GINEBRA RELATIVO AL TRATO DE | 37 | 0 | 0 | 0 | 0 | 0 | 37 |
| tratados | `67952` | ACUERDO RELATIVO A GIROS POSTALES Y LOS  | 36 | 0 | 0 | 0 | 0 | 0 | 36 |
| tratados | `11248` | CONVENCION SOBRE ASISTENCIA EN CASO DE A | 34 | 0 | 0 | 0 | 0 | 0 | 34 |
| tratados | `89658` | CONVENIO DE GINEBRA RELATIVO A LA PROTEC | 34 | 0 | 0 | 0 | 0 | 0 | 34 |
| tratados | `914` | TRATADO SOBRE EL REGISTRO INTERNACIONAL  | 34 | 0 | 34 | 0 | 0 | 0 | 0 |
| tratados | `143097` | ACTA DE GINEBRA DEL ARREGLO DE LA HAYA R | 32 | 0 | 32 | 0 | 0 | 0 | 0 |
| tratados | `23837` | CONVENIO RELATIVO A LA RADIODIFUSION EN  | 32 | 0 | 4 | 0 | 0 | 0 | 28 |
| tratados | `84855` | ACTAS FINALES DE LA CONFERENCIA MUNDIAL  | 32 | 0 | 18 | 0 | 0 | 0 | 14 |
| tratados | `26628` | CONVENIO-MARCO RELATIVO A LA EJECUCION D | 31 | 0 | 0 | 0 | 0 | 0 | 31 |
| tratados | `11294` | ACUERDO SOBRE LA FACULTAD LATINOAMERICAN | 29 | 0 | 0 | 0 | 29 | 0 | 0 |
| tratados | `17154` | ARREGLO DE NIZA RELATIVO A LA CLASIFICAC | 28 | 0 | 28 | 0 | 0 | 0 | 0 |
| tratados | `47860` | ACTAS FINALES DE LA CONFERENCIA MUNDIAL  | 27 | 0 | 0 | 0 | 0 | 0 | 27 |
| tratados | `89655` | CONVENIO DE GINEBRA PARA MEJORAR LA SUER | 26 | 0 | 0 | 0 | 0 | 0 | 26 |
| tratados | `85035` | CONVENIO SOBRE TRANSPORTE AEREO (REPUBLI | 23 | 0 | 0 | 0 | 23 | 0 | 0 |
| tratados | `11327` | CONVENIO SOBRE TRANSPORTES AEREOS (SUIZA | 21 | 0 | 0 | 0 | 21 | 0 | 0 |
| tratados | `11354` | TRATADO DE NAIROBI SOBRE LA PROTECCION D | 20 | 0 | 20 | 0 | 0 | 0 | 0 |
| tratados | `11448` | CONVENIO PARA EL CANJE DE PUBLICACIONES  | 20 | 0 | 0 | 0 | 20 | 0 | 0 |
| tratados | `16189` | CONVENCION Y ESTATUTO SOBRE EL REGIMEN I | 20 | 0 | 0 | 0 | 0 | 0 | 20 |
| tratados | `23952` | CONVENIO PARA LA REPRESION DE ACTOS ILIC | 20 | 0 | 0 | 0 | 0 | 0 | 20 |
| tratados | `44552` | ACTAS FINALES DE LA CONFERENCIA MUNDIAL  | 20 | 0 | 0 | 0 | 0 | 0 | 20 |
| tratados | `60292` | CONVENIO CONSTITUTIVO DEL ORGANISMO MULT | 19 | 0 | 0 | 0 | 0 | 0 | 19 |
| tratados | `50539` | CONVENIO CONSTITUTIVO DEL FONDO MULTILAT | 17 | 0 | 0 | 0 | 0 | 0 | 17 |
| tratados | `85038` | CONVENIO SOBRE TRANSPORTE AEREO (JAMAICA | 17 | 0 | 0 | 0 | 17 | 0 | 0 |
| tratados | `125345` | ACUERDO SOBRE UN PROGRAMA INTERNACIONAL  | 16 | 0 | 0 | 0 | 0 | 0 | 16 |
| tratados | `66295` | CONVENIO INTERNACIONAL PARA EL ESTABLECI | 16 | 0 | 0 | 0 | 0 | 0 | 16 |
| tratados | `67671` | ACUERDO PARA EL ESTABLECIMIENTO DE LA RE | 16 | 0 | 0 | 0 | 16 | 0 | 0 |
| tratados | `67984` | CONVENCION SOBRE UN CODIGO DE CONDUCTA D | 16 | 0 | 0 | 0 | 0 | 0 | 16 |
| tratados | `90408` | ACUERDO MARCO DE LA ALIANZA DEL PACIFICO | 15 | 0 | 0 | 0 | 15 | 0 | 0 |
| tratados | `110814` | CONVENIO BASICO DE COOPERACION PARA EL D | 14 | 0 | 0 | 0 | 14 | 0 | 0 |
| tratados | `107086` | CONVENIO SOBRE SERVICIOS AEREOS (GOBIERN | 12 | 0 | 0 | 0 | 12 | 0 | 0 |
| tratados | `11174` | ACUERDO SOBRE TRANSPORTES AEREOS (REPUBL | 12 | 0 | 0 | 0 | 12 | 0 | 0 |
| tratados | `18122` | CONVENIO PARA LA COOPERACION EN EL MARCO | 12 | 0 | 0 | 0 | 0 | 0 | 12 |
| tratados | `23774` | CONVENCION CONSTITUTIVA DE LA ORGANIZACI | 11 | 0 | 0 | 0 | 0 | 0 | 11 |
| tratados | `11272` | CONVENIO PARA LA TRANSMISION DE VALIJAS  | 10 | 0 | 0 | 0 | 10 | 0 | 0 |
| tratados | `11340` | CONVENIO DE COOPERACION QUE CELEBRAN LOS | 10 | 10 | 0 | 0 | 0 | 0 | 0 |
| tratados | `11379` | TRATADO DE EXTRADICION (CUBA, 1925) | 10 | 10 | 0 | 0 | 0 | 0 | 0 |
| tratados | `16165` | TRATADO PARA EVITAR O PREVENIR CONFLICTO | 10 | 0 | 0 | 0 | 0 | 0 | 10 |
| tratados | `16685` | ACTAS FINALES DE LA CONFERENCIA MUNDIAL  | 10 | 0 | 0 | 0 | 10 | 0 | 0 |
| tratados | `23834` | CONVENIO SOBRE TRANSPORTES AEREOS (REINO | 10 | 0 | 0 | 0 | 10 | 0 | 0 |
| tratados | `24154` | ACUERDO PARA LA PROMOCION Y PROTECCION R | 10 | 0 | 0 | 0 | 0 | 0 | 10 |
| tratados | `90396` | PRINCIPIOS RELATIVOS AL ESTATUTO DE LAS  | 9 | 0 | 0 | 0 | 9 | 0 | 0 |
| tratados | `11422` | TRATADO DEFINITIVO DE PAZ Y AMISTAD (S.M | 8 | 0 | 0 | 0 | 0 | 0 | 8 |
| tratados | `24017` | ACUERDO RELATIVO AL PROCEDIMIENTO DE EST | 8 | 0 | 0 | 0 | 8 | 0 | 0 |
| tratados | `44555` | CONVENIO SOBRE TRANSPORTE AEREO (REPUBLI | 8 | 0 | 0 | 0 | 8 | 0 | 0 |
| tratados | `64685` | CONVENIO SOBRE TRANSPORTE AEREO (REPUBLI | 8 | 0 | 0 | 0 | 8 | 0 | 0 |
| tratados | `11447` | CONVENIO PARA EL CANJE DE PERIODICOS OFI | 6 | 0 | 0 | 0 | 6 | 0 | 0 |
| tratados | `131814` | ACUERDO SOBRE SERVICIOS AEREOS (GOBIERNO | 6 | 0 | 0 | 0 | 6 | 0 | 0 |
| tratados | `16296` | ACUERDO DE ASOCIACION ECONOMICA, CONCERT | 6 | 0 | 0 | 0 | 0 | 0 | 6 |
| tratados | `18864` | ACUERDO RELATIVO AL COMERCIO INTERNACION | 6 | 0 | 0 | 0 | 6 | 0 | 0 |
| tratados | `22544` | TRATADO SOBRE DISTRIBUCION DE AGUAS INTE | 6 | 0 | 0 | 0 | 0 | 0 | 6 |
| tratados | `23710` | CONVENCIONES PARA LA UNIFICACION DE DETE | 6 | 6 | 0 | 0 | 0 | 0 | 0 |
| tratados | `23911` | ACUERDO RELATIVO A LA ASIGNACION Y UTILI | 6 | 0 | 0 | 0 | 6 | 0 | 0 |
| tratados | `23956` | ACUERDO CONCERNIENTE A LA UTILIZACION DE | 6 | 0 | 0 | 0 | 6 | 0 | 0 |
| tratados | `85041` | CONVENIO IBEROAMERICANO SOBRE EL USO DE  | 6 | 0 | 0 | 0 | 0 | 0 | 6 |
| tratados | `107392` | TRATADO DE LIBRE COMERCIO (REPUBLICA DE  | 4 | 0 | 0 | 0 | 4 | 0 | 0 |
| tratados | `129239` | CONVENIO SOBRE TRANSPORTE AEREO (REINO D | 4 | 0 | 0 | 0 | 4 | 0 | 0 |
| tratados | `16580` | CONVENIO SOBRE TRANSPORTE AEREO (REPUBLI | 4 | 0 | 0 | 0 | 4 | 0 | 0 |
| tratados | `19438` | TRATADO GENERAL DE ARBITRAJE INTERAMERIC | 4 | 0 | 0 | 0 | 0 | 0 | 4 |
| tratados | `23743` | CONVENCION SOBRE EXTRADICION (MULTILATER | 4 | 0 | 0 | 0 | 0 | 0 | 4 |
| tratados | `23966` | CONVENCION SOBRE PRESCRIPCION EN MATERIA | 4 | 0 | 0 | 0 | 0 | 0 | 4 |
| tratados | `42131` | ACUERDO PARA EL FORTALECIMIENTO DE LA AS | 4 | 0 | 0 | 0 | 4 | 0 | 0 |
| tratados | `49685` | ACUERDO PARA LA PROMOCION Y PROTECCION R | 4 | 0 | 0 | 0 | 0 | 0 | 4 |
| tratados | `50610` | TRATADO PARA LA EXTRADICION DE CRIMINALE | 4 | 0 | 0 | 0 | 0 | 0 | 4 |
| tratados | `84349` | ACUERDO DE INTEGRACION COMERCIAL (PERU,  | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| tratados | `11418` | CONVENIO DE INTERCAMBIO CULTURAL (REPUBL | 3 | 3 | 0 | 0 | 0 | 0 | 0 |
| tratados | `16277` | TRATADO DE LIBRE COMERCIO ENTRE LOS ESTA | 3 | 3 | 0 | 0 | 0 | 0 | 0 |
| tratados | `87918` | ACUERDO DE RECONOCIMIENTO MUTUO ENTRE EL | 3 | 0 | 0 | 0 | 3 | 0 | 0 |
| tratados | `11311` | CONVENIO DE INTERCAMBIO CULTURAL (REPUBL | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| tratados | `11329` | CONVENIO SOBRE SERVICIOS AEREOS (COREA,  | 2 | 2 | 0 | 0 | 0 | 0 | 0 |
| tratados | `11440` | TRATADO DE PAZ, AMISTAD, LIMITES Y ARREG | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| tratados | `14273` | CONVENCION PARA EL ESTABLECIMIENTO DE UN | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| tratados | `22834` | CONSTITUCION DE LA ORGANIZACION INTERNAC | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| tratados | `23762` | CONVENCION SOBRE EL INSTITUTO INTERAMERI | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| tratados | `28571` | ACUERDO DE CONSTITUCION DEL INSTITUTO IN | 2 | 0 | 0 | 0 | 0 | 0 | 2 |
| tratados | `89155` | PROTOCOLO DE REFORMAS A LA CARTA DE LA O | 2 | 0 | 0 | 0 | 0 | 0 | 2 |

**Reach of the rebuild.** The units changed in 95 leyes, 231 reglamentos, 84 lineamientos and 332 treaties against the
published ones (cumulative with #291); the distinct texts new against the published vectors are 4,558, 1,435, 569
and 11,811. This pass only had to embed what rules 14 to 18 added to the #294 state: 122 (leyes), 55
(reglamentos), 2 (lineamientos) and 29 (tratados) texts, one shard per model each, both models, 0 failed;
`compare_merges.py` passes for all eight runs (0 texts with another vector, 0 non-finite; 58, 127, 72 and 304 files
differ in the 4B, of which 2, 16, 0 and 2 only because they share a text with a changed instrument, a case
`compare_merges.py` now accounts for with its own tests). The three Atlas data sets keep the same 2,676 instruments in
the same order: 209,584 excerpts (203,874 before), 148,911 counted (144,456; 148,482 and 144,111 under BM25), and the
closest instrument is unchanged for 2,544 (0.6B), 2,536 (4B) and 2,518 (BM25) of 2,676. The strong-link gold is
unchanged (161 + 170 = 251 instruments, 275 links); the citations are 7,087 hits in 5,224 excerpts and 4,475 scored
rows (4,468 before), and the verdicts of both evaluations are unchanged (the three data sets tied on the closest
instruments, the 4B better row by row on all five measures against the 0.6B), only the last digits moved. The
excerpt-level table of the Atlas page, its worked examples and its treaty sentence follow the new data. The 3,184-unit
`114738` is rendered by the Instruments page like any other. **Nothing was published.**
