# nota2md

[![Documentation Status](https://readthedocs.org/projects/legalia/badge/?version=latest)](https://legalia.readthedocs.io/en/latest/nota2md_api.html)

> **`download_legal_provisions_provenance_ids` is gone** (issue #187). It read
> the `historial-legislativo` release, which was built by scraping the Cámara
> de Diputados' LeyesBiblio; the project now rests on the SCJN and the DOF
> alone (issue #184). There is no shim and the name is not kept.
>
> A law's reform history is the
> [`scjn-leyes`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-leyes)
> release itself: `download_scjn_leyes_corpus("<abrev>")` gives one entry per
> reform, oldest first, each with the `codNota` that published it — which is
> exactly what `reconstruct_legal_provisions` takes.
>
> **"Reform N" is redefined**, once and deliberately: it is now the position
> of the reform in the SCJN's own chronological reform table, not Diputados'
> numbering. The two count different things — Diputados filed errata, peso
> restatements, Court rulings and entry-into-force declarations in the same
> numbered column — so a saved "reform 139 of the Constitution" may not point
> at the same decree it used to. This is not measured against the old
> numbering, and cannot be: its source is gone.

> **v0.5.0 changes what `legal_provisions` returns by default**, makes its
> `outdir` optional, and replaces `download_legal_provisions_titles` with the
> `legal_provisions_titles` stream (issues #117, #165, #166).
>
> Left out, `outdir` puts the Markdown in `nota2md`'s own cache and returns its
> `Path`: `legal_provisions(5773097)` →
> `<CACHE_DIR>/scjn-leyes/md/ccf-14-11-2025.md`. Passing an `outdir` behaves
> exactly as before. And by default a `codNota`
> the [`scjn-leyes`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-leyes)
> release covers now comes back as **the law's whole consolidated text at that
> reform** (written to `outdir/{slug}-{fecha}.md`, `fuente: scjn` header
> included), not as the reform decree the DOF published. Pass
> `source="dof"` — or `--source dof` — for the previous behaviour, which is
> unchanged in every other respect. See [the SCJN
> path](#the-scjn-path--a-laws-consolidated-text-at-each-reform).

Eight entry points, all re-exported off the package itself
(`from nota2md import ...`), for Mexico's official gazette (DOF, Diario
Oficial de la Federación) and the federal laws it publishes:

| Entry point | Given | Returns/writes |
|---|---|---|
| [`legal_provisions`](#legal_provisions--a-single-dof-legal-provision-as-markdown) | a legal provision's `codNota` (an `outdir` is optional) | the law's consolidated text at that reform, from the SCJN corpus (`outdir/{slug}-{fecha}.md`) — or, when the corpus does not cover it, the DOF's own Markdown (`outdir/nota-{codNota}.md`); with no `outdir`, written into the cache and returned as a `Path` |
| [`reconstruct_legal_provisions`](#reconstruct_legal_provisions--a-laws-current-text-from-its-dof-legal-provisions) | a law's reform history (`codNota` list) | its current text, written to `outdir/ley-{codNota}.md` |
| [`fetch_daily_legal_provisions`](#cutting-a-legal-provision-out-of-its-page) | a date (or a day already in hand) | that whole day's legal provisions as one flat list, each naming its `edicion` |
| [`get_document`](#get_document--a-note-whose-text-is-already-markdown) | a legal provision's `codNota`, or a `get_nota` record already in hand | that same record, with `cadenaContenido` holding its **Markdown** instead of DOF HTML |
| [`legal_provisions_titles`](#legal_provisions_titles--every-legal-provision-ever-published-as-titles) | nothing (reads the whole `notas-archivo` cache) | every legal provision ever published, as a stream of `codNota`+`titulo`+`fecha`+`codOrgaUno` records |
| [`download_scjn_leyes_corpus`](#the-scjn-path--a-laws-consolidated-text-at-each-reform) | a law's `slug` | every snapshot of that law in the `scjn-leyes` release, with its `codNota` links, in memory |
| [`download_scjn_leyes_index`](#the-scjn-path--a-laws-consolidated-text-at-each-reform) | nothing (reads one small release asset) | the reverse index `codNota → (law, snapshot)`, in memory |
| [`download_scjn_leyes_catalog`](#the-scjn-path--a-laws-consolidated-text-at-each-reform) | nothing (the index, plus one tarball per law for `actualizado`) | every federal law the release publishes, as `abrev`+`nombre`+`actualizado` |

They compose: `download_scjn_leyes_corpus` gets you the `codNota` list
`reconstruct_legal_provisions` needs, and `reconstruct_legal_provisions` gets you a
law's current text the same way `legal_provisions` gets you a single legal
provision's — built from nothing but the DOF's own legal provisions, one
Markdown file at a time.

```python
from nota2md import download_scjn_leyes_corpus, legal_provisions, reconstruct_legal_provisions

cpeum = download_scjn_leyes_corpus("cpeum")
# one entry per reform, oldest first; `codNota` is None where the link could
# not be established (see `title_link_status` in the same entry).
historial = [s["codNota"] for s in cpeum["snapshots"] if s["codNota"]]

dest = reconstruct_legal_provisions(historial, "output", nombre_ley="CONSTITUCIÓN Política de los Estados Unidos Mexicanos")
print(dest)
```

## `legal_provisions` — a single DOF legal provision as Markdown

Builds the Markdown of a **single DOF legal provision**, identified by its
`codNota`.

Where [`dof2md`](../dof2md) converts a whole edition PDF and
[`dofjson`](../dofjson) is a thin client for SIDOF's JSON service,
`legal_provisions` ties them together to produce the Markdown for one legal
provision, from any of four sources:

| Source | How | When |
|---|---|---|
| **SCJN** | Reads the law's consolidated text as it read right after this reform out of the `scjn-leyes` release — no DOF request at all. See [the SCJN path](#the-scjn-path--a-laws-consolidated-text-at-each-reform). | The default, whenever the corpus covers this `codNota` with a link we are certain of. `source="dof"` turns it off. |
| **HTML** | Converts the legal provision's `cadenaContenido` HTML directly (a DOF-tailored BeautifulSoup converter). | The legal provision has digital text. Preferred: clean, already scoped to the one legal provision, no OCR. |
| **Image** | Downloads the legal provision's scanned page image(s) via `dofjson`, OCRs them with `dof2md`/mineru, then slices out the one legal provision. | Image-only legal provisions — or any legal provision, when you want the certified scanned original. |
| **PDF** | Downloads the legal provision's own PDF (the edition PDF sliced to the legal provision's pages, via `dofjson.download_nota_pdf`), OCRs it with `dof2md`/mineru, then slices out the one legal provision. | When you'd rather OCR a PDF than page images. |

Both OCR paths (image and PDF) mirror the HTML path's output style (`#`/`##`
headings, `**bold**`, `*italic*`, GitHub tables — `dof2md` rewrites mineru's
HTML tables to Markdown), so a legal provision's Markdown looks much the same
whichever source it came from.

### Legal provisions SIDOF does not have

SIDOF is missing whole days of the gazette (see [`dofjson`](../dofjson)), and
the legal provisions published on them have no SIDOF record at all — no
`cadenaContenido`, and no `codDiario` or page numbers for the OCR paths to
start from. When SIDOF answers `{"Nota": []}` for a `codNota`,
`legal_provisions` looks the legal provision up on the DOF's own website
instead, which serves the same HTML:

```bash
nota2md 4997808 --outdir output   # DOF 03-03-1999, a day SIDOF lost
```

The HTML path is the only one that can build these legal provisions; asking
for `--source image` or `--source pdf` on one raises rather than fetching the
wrong pages.

### Cutting a legal provision out of its page

A scanned page (or a sliced PDF) usually holds more than one legal provision:
it can begin with the tail of the previous legal provision and end with the
start of the next. `legal_provisions` uses the per-day legal provision index —
which lists every legal provision's title in order — to locate two boundaries
in the OCR'd text (where **this** legal provision's title appears, and where
the **next** legal provision's title appears) and keeps only what lies
between. Matching is fuzzy (accent-folded, marker-stripped, `difflib`
alignment) to tolerate OCR differences, and it also drops the next legal
provision's organism header that the DOF prints above its title.

`fetch_daily_legal_provisions(date)` is the whole day — every legal
provision published on it (title, `codNota`, `pagina`...) as one flat list in
publication order, each note naming the `edicion` it appeared in, from SIDOF
and, when SIDOF has nothing for that day, from the DOF's own website:

```python
from nota2md import fetch_daily_legal_provisions
import datetime as dt

for nota in fetch_daily_legal_provisions(dt.date(2026, 7, 15)):
    print(nota["edicion"], nota["codNota"], nota["titulo"])
```

It is `dofjson.fetch_daily_legal_provisions`, re-exported (issue #180, the
same pattern as `legal_provisions_titles`). It also takes a day already in
hand instead of a date, in which case nothing is fetched. The underlying
`dofjson.get_notas()` answers the day keyed by edition
(`NotasMatutinas`/`NotasVespertinas`/`NotasExtraordinarias`) — the wire shape
of both sources — so indexing one of those keys silently drops the rest of
the day; this flattens all three, in publication order (issue #169).

### `get_document` — a note whose text is already Markdown

`dofjson.get_nota(codNota)` answers the note's record with its digital text
in `cadenaContenido`, as DOF HTML. `get_document` is that same record with
`cadenaContenido` holding the **Markdown** instead — every other key
(`codNota`, `titulo`, `fecha`, `fuente`, `codDiario`, page numbers...) passed
through untouched, so it can go anywhere a `get_nota` record can, including
as `legal_provisions`' own `nota` argument:

```python
from nota2md import get_document

documento = get_document(5793655)
documento["titulo"]            # unchanged
documento["cadenaContenido"]   # Markdown, not HTML
```

This is the package's single note-to-Markdown step (issue #170): the pairing
of `cadenaContenido` with `html_to_markdown` is spelled out here and nowhere
else. Given a record already in hand, the call is pure and offline, and the
dict passed in is never mutated:

```python
documento = get_document(nota=fetch_nota(5793655))
```

A note with no digital text (scanned, pre-1999ish) comes back with
`cadenaContenido` as it was — `None` or empty, and no error raised. It is not
silently OCR'd: OCR is `dof2md`'s heavy path, and `legal_provisions` already
owns the decision of when to take it. `html_to_markdown` itself stays public,
as the string-to-string primitive this is built on.

### The SCJN path — a law's consolidated text at each reform

Since v0.5.0, `legal_provisions` answers **the whole law, not the reform
decree**, whenever it can. The SCJN's legislative database keeps, for every
reform of every federal law, a snapshot of the law's consolidated text
exactly as it read right after that reform; those snapshots are crawled —
through the SCJN's own SCOW JSON API since issue #172, which replaced the
legacy WebForms Buscador and, unlike it, indexes laws as new as the LEY
FEDERAL DE CINE Y EL AUDIOVISUAL — matched to the DOF `codNota` that enacted
them, and published as the
[`scjn-leyes`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-leyes)
release (315 laws, 3,724 snapshots). A `codNota` the release covers is
answered straight out of it — which is what
[`reconstruct_legal_provisions`](#reconstruct_legal_provisions--a-laws-current-text-from-its-dof-legal-provisions)
otherwise has to *infer* by replaying every reform decree in order.

**The SCJN is not an official source of legal text.** dof.gob.mx/SIDOF is.
Every file this path writes therefore keeps the corpus' own provenance header
(`fuente: scjn`, `ordenamiento`, `fecha_publicacion`, …) intact, and lands
under a different name — so the file alone says where it came from:

| | file written | with no `outdir` (v0.5.0) |
|---|---|---|
| SCJN path | `outdir/{slug}-{fecha}.md` (e.g. `lfca-05-01-1999.md`) | `<CACHE_DIR>/scjn-leyes/md/{slug}-{fecha}.md` |
| every DOF path | `outdir/nota-{codNota}.md` (unchanged) | `<CACHE_DIR>/dof/nota-{codNota}.md` |

```python
from nota2md import legal_provisions

# no outdir at all: written into nota2md's cache, path returned (v0.5.0)
legal_provisions(4967917)     # -> <CACHE_DIR>/scjn-leyes/md/lfca-05-01-1999.md

# default: the whole law at that reform, if the corpus covers this codNota
legal_provisions(4967917, "output")                  # -> output/lfca-05-01-1999.md

# the original source, always — DOF/SIDOF, with OCR/reconstruction as needed
legal_provisions(4967917, "output", source="dof")    # -> output/nota-4967917.md
```

A `codNota` whose decree reformed several laws at once resolves to more than
one law, and that raises `ValueError` listing the candidates rather than
picking one; say which with `instrumento="<slug>"`. A `codNota` the corpus
does not cover, an asset not published yet, or a network failure reading the
release all fall back to the DOF path (the last two with a `warnings.warn`,
so the fallback is never silent).

Three readers of the release are exported for working with the corpus directly:

```python
from nota2md import (
    download_scjn_leyes_catalog,
    download_scjn_leyes_corpus,
    download_scjn_leyes_index,
)

indice = download_scjn_leyes_index()          # codNota -> [{slug, archivo, ...}]
lfca = download_scjn_leyes_corpus("lfca")     # every snapshot of one law

# which federal laws exist, their name and their freshness
catalogo = download_scjn_leyes_catalog(freshness=False)
# -> [{"abrev": "ccf", "nombre": "CÓDIGO Civil Federal"}, ...]  (315 laws)
```

`download_scjn_leyes_catalog` is the federal-law **catalogue**: one
`{"abrev", "nombre", "actualizado"}` dict per law, sorted by `abrev`.
`abrev`/`nombre` come from the release's index; `actualizado` — the date of
the law's most recent reform as of the crawl — comes from each law's own
`estado.json`, and is **absent** rather than null for the three laws that
have none (absent means "freshness unknown, always review"). Reading
`actualizado` means opening one tarball per law, so `freshness=False`
answers off the index alone; run `nota2md download federal-laws` first (see
below) and the default costs no request either.

Beware one thing if you are joining this against a catalogue of your own: the
release slug is the *normalized* `abrev`, so the 14 laws whose abbreviation
carries an underscore (`lif_2026`, `pef_2026`, `ligie_2022`, `reg_senado`,
the `lrart*`/`lrf*` reglamentarias …) come back hyphenated. Match on
`nota2md.scjn.slug_instrumento` and keep your own `abrev`.

A fourth reader, `iter_current_federal_laws`, answers a narrower question
lazily instead of loading a whole law's history to answer it: not "every
snapshot", just the *current* text of every federal law, one at a time.

```python
from nota2md import iter_current_federal_laws

# run `nota2md download federal-laws` first to avoid downloading on the fly
for ley in iter_current_federal_laws():
    print(ley["slug"], ley["fecha_publicacion"], len(ley["markdown"]))
```

Each item is `{"slug", "nombre", "fecha_publicacion", "codNota", "archivo",
"markdown"}` — the law's newest snapshot, picked by `fecha_publicacion`
without decoding any of its other snapshots or its `notas/`. `slugs=[...]`
narrows the laws visited (and their order); left out, it walks every law the
release currently ships a tarball for. It is a generator: iterating one law
at a time never opens more than one `<slug>.tgz`, so consuming the whole
corpus this way never holds more than one law in memory.

#### Cache

The release assets the SCJN path reads are cached on disk, exactly as
[`dofjson`](../dofjson) caches `notas-archivo`:

```
<CACHE_DIR>/scjn-leyes/indice-global.json.gz
<CACHE_DIR>/scjn-leyes/<slug>.tgz
```

`CACHE_DIR` defaults to the OS per-user cache directory (`~/.cache/nota2md` on
Linux), overridable with `$NOTA2MD_CACHE_DIR` or by reassigning
`nota2md.cache.CACHE_DIR`. Per call, `cache_dir=<path>` names a directory,
`cache_dir=None` skips the cache entirely (download into memory), and
`refrescar=True` re-downloads — an asset already on disk is a hit **by file
name, never revalidated**, since this corpus is only ever republished by hand.
It is `nota2md`'s own directory, not `dofjson`'s: two releases, two
lifecycles, so clearing one does not clear the other.

### Usage

```bash
# no --outdir: written into nota2md's cache, path printed
nota2md 5793655

# the law's consolidated text from the SCJN corpus when it covers this
# codNota, otherwise the DOF (HTML when available, else OCR)
nota2md 5793655 --outdir output

# the original source only: DOF/SIDOF, never the SCJN
nota2md 5793655 --source dof --outdir output

# one decree, several laws: say which one
nota2md 5793655 --instrumento lft --outdir output

# cache: a directory of your own, or none at all
nota2md 5793655 --cache-dir /mnt/datos/nota2md --outdir output
nota2md 5793655 --cache-dir none --refrescar --outdir output

# fetch the whole SCJN corpus up front instead of asset by asset on demand
# (see `nota2md download` below)
nota2md download federal-laws

# force the scanned-image + OCR path, sourcing the next legal provision's
# title from a saved notas index (avoids an extra request; works offline)
dofjson 2026-07-15 --outdir output          # writes 15072026-notas.json
nota2md 5793655 --source image --notas output/15072026-notas.json --outdir output

# force the PDF + OCR path (edition PDF sliced to the legal provision's pages)
nota2md 5793655 --source pdf --notas output/15072026-notas.json --outdir output
```

Programmatically:

```python
from nota2md import legal_provisions

legal_provisions(5793655, "output")                 # SCJN if covered, else DOF
legal_provisions(5793655, "output", source="dof")   # -> output/nota-5793655.md
```

The HTML path needs only `beautifulsoup4`; the image and PDF paths additionally
need `dof2md` (and mineru), imported lazily so the HTML path works without them.

### Batch conversion — reusing one OCR server across many legal provisions

Left alone, every `legal_provisions(..., source="image"|"pdf")` call manages
its own OCR server as needed. Building many legal provisions in one run
(e.g. every legal provision in a law's `historial` that has no HTML) can
instead share one already-warm `mineru-api` server across all of them, by
passing an already-`__enter__`'d `dof2md.BatchConverter` as `converter`:

```python
from dof2md import BatchConverter
from nota2md import legal_provisions

with BatchConverter() as ins:
    for cod_nota in codigos_sin_html:
        legal_provisions(cod_nota, "output", source="image", converter=ins)
```

This is the same `BatchConverter` [`dof2md`](../dof2md) itself uses to
convert any batch of documents — DOF-sourced or not.

## `reconstruct_legal_provisions` — a law's current text from its DOF legal provisions

Builds a law's current (vigente) text from nothing but its DOF legal
provisions: starts from the original publication and replays each reform
decree's own "se
reforma/adiciona/deroga el artículo N... para quedar como sigue" instruction
on top of it, article by article — filling back in, from the article's own
previous text, every fracción or inciso a reform elides with "..." instead of
repeating. It never reads anyone else's consolidated text of the law:
`tests/test_leyes_44.py` checks it against the SCJN's own consolidated text at
each law's most recent reform, over 42 real federal laws, and that test says in
full what the check does and does not establish (issue #188).

The SCJN corpus becoming `legal_provisions`' default source (issue #117) does
not retire this function (issue #129's audit): the corpus links 3,291 of its
3,724 `leyes` snapshots to a codNota (2,474 by title alone, 834 more promoted
from their content-diff confirmation in issue #187), only 526 of them
pre-1999, and covers nothing outside federal laws — so replaying a law's own
reform decrees remains the only route for everything it does not reach, as
well as the independent cross-check on what it does.

```python
from nota2md import reconstruct_legal_provisions

# cpeum's own historial: [5592105, 5730586, ...] — oldest first, index 0 the
# original publication (see the `scjn-leyes` readers above for where a law's
# own historial list comes from).
dest = reconstruct_legal_provisions(
    [5592105, 5730586], "output", nombre_ley="LEY de Amnistía",
)
print(dest.read_text(encoding="utf-8"))   # -> output/ley-5592105.md
```

Each legal provision it needs is fetched through `legal_provisions` into the
same `outdir`, as `nota-{codNota}.md` — a legal provision already there from
an earlier call (this law's own previous run, or another law's sharing the
same `outdir`) is read back from disk instead of fetched again.

`nombre_ley` (the catalogue's own `nombre`, e.g. `"LEY de Amnistía"`) scopes
every legal provision to the one instrument among the several a single decree
may touch — pass it whenever a legal provision is shared with another law's
history, which the release's per-law index does not mark on its own (the
reverse index does: `download_scjn_leyes_index` maps one `codNota` to *every*
law it reformed). Left out, a legal provision is assumed to concern only
this law, which holds for most of them but silently mixes in another law's
articles for the rest.

`source`, `min_confidence` and `keep_pages` are the same parameters
`legal_provisions` itself takes, forwarded as-is to the call made for every
legal provision in the history. The default here is `source="html"`, not
`legal_provisions`'s own `"auto"`: the article-merge this function does
(`_fusiona_articulo`) was designed and checked against HTML-derived
Markdown, so a legal provision missing `cadenaContenido` still fails by
default. Passing `source="image"` or `"pdf"` OCRs it instead of failing, but
the merge's behavior on OCR output has not been validated — review the
result before trusting it.

## `legal_provisions_titles` — every legal provision ever published, as titles

Implemented in [`dofjson.titulos`](../dofjson) and re-exported here so it sits
alongside the rest of `nota2md`'s entry points. Yields a compact `codNota` +
`titulo` + `fecha` + `codOrgaUno` record for every legal provision published
since 1917 (~1.2 million of them), streamed off the `notas-archivo` cache —
nothing is written, and a populated cache means no network at all (issue
#166; it used to write a `titulos.jsonl.gz` dataset):

```bash
nota2md download gazette-metadata    # populate the cache, once
```

```python
from nota2md import legal_provisions_titles

for titulo in legal_provisions_titles():
    ...
```

## `nota2md download` — putting the releases on disk

Everything above reads from two GitHub releases, downloading whatever it
needs on the fly. `nota2md download` fetches them ahead of time instead, into
the per-user cache directory each package already uses — so a notebook, a
batch run or an offline session finds them already there, and no script has
to be written first:

```bash
nota2md download federal-laws        # the scjn-leyes release (~380 MB, 315 laws)
nota2md download gazette-metadata    # the notas-archivo release (~59 MB, 116 assets)
nota2md download all                 # both, each into its own cache
```

`federal-laws` brings down the reverse index plus one tarball per law.
`--slug` (repeatable) narrows it to the laws you actually want; the index
always comes along, since it is what resolves a `codNota` to a law:

```bash
nota2md download federal-laws --slug lft --slug lfca
```

Both are **idempotent**: an asset already on disk is matched by file name and
never revalidated, so a second run downloads nothing and finishes in
milliseconds. Each line of output says which of the two happened, and a final
line names the directory written to:

```console
$ nota2md download federal-laws --slug lfca
[1/2] indice-global.json.gz: already cached
[2/2] lfca.tgz: already cached
scjn-leyes: 2 assets in /home/user/.cache/nota2md/scjn-leyes (0 downloaded, 2 already cached)
```

Pass `--refrescar` to re-download over what is there — the only way a release
re-published under the same asset names reaches an already-populated cache.

### Where the data lands

The two releases keep **two separate cache directories**, one per package:
they have different lifecycles, and clearing one must not clear the other.
`nota2md download` does not merge them; `all` is a shorthand for two
invocations, not a shared destination.

| | `federal-laws` (`scjn-leyes`) | `gazette-metadata` (`notas-archivo`) |
|---|---|---|
| Package | `nota2md` | `dofjson` |
| Linux | `~/.cache/nota2md/scjn-leyes/` | `~/.cache/dofjson/` |
| macOS | `~/Library/Caches/nota2md/scjn-leyes/` | `~/Library/Caches/dofjson/` |
| Windows | `%LOCALAPPDATA%\nota2md\Cache\scjn-leyes\` | `%LOCALAPPDATA%\dofjson\Cache\` |
| Override | `$NOTA2MD_CACHE_DIR`, or `nota2md.cache.CACHE_DIR` | `dofjson.titulos.CACHE_DIR` |
| Per-run override | `--cache-dir DIR` | `--cache-dir DIR` |

`--cache-dir` therefore means a different thing on each subcommand — a
`nota2md` directory on `federal-laws`, a `dofjson` one on `gazette-metadata`
— which each subcommand's own `--help` says plainly. `--cache-dir none`
(valid on `nota2md <codNota>`, where it means "skip the cache, download into
memory") is rejected here: this verb exists to write the release to disk, and
"no cache" has nowhere to write.

From Python, the same two downloads are `nota2md.scjn.download_scjn_leyes_assets`
and `dofjson.download_dof_assets`.

## Installation

```bash
pip install nota2md          # legal_provisions' HTML path, plus reconstruct_legal_provisions
                              # and the scjn-leyes release readers
pip install nota2md[ocr]     # also pulls in dof2md, for legal_provisions' image/PDF OCR paths
```

`dofjson` and `requests` are hard dependencies and install automatically. For
development in this monorepo, install the siblings editable instead so local
edits are picked up:

```bash
pip install -e "packages/dofjson"
pip install -e "packages/dof2md"          # only needed for the image/PDF OCR paths
pip install -e "packages/nota2md[test]"
```

## Development

```bash
pytest packages/nota2md
```
