# nota2md

> **v0.5.0 changes what `legal_provisions` returns by default.** A `codNota`
> the [`scjn-leyes`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-leyes)
> release covers now comes back as **the law's whole consolidated text at that
> reform** (written to `outdir/{slug}-{fecha}.md`, `fuente: scjn` header
> included), not as the reform decree the DOF published. Pass
> `source="dof"` — or `--source dof` — for the previous behaviour, which is
> unchanged in every other respect. See [the SCJN
> path](#the-scjn-path--a-laws-consolidated-text-at-each-reform).

Seven entry points, all re-exported off the package itself
(`from nota2md import ...`), for Mexico's official gazette (DOF, Diario
Oficial de la Federación) and the federal laws it publishes:

| Entry point | Given | Returns/writes |
|---|---|---|
| [`legal_provisions`](#legal_provisions--a-single-dof-legal-provision-as-markdown) | a legal provision's `codNota` | the law's consolidated text at that reform, from the SCJN corpus (`outdir/{slug}-{fecha}.md`) — or, when the corpus does not cover it, the DOF's own Markdown (`outdir/nota-{codNota}.md`) |
| [`reconstruct_legal_provisions`](#reconstruct_legal_provisions--a-laws-current-text-from-its-dof-legal-provisions) | a law's reform history (`codNota` list) | its current text, written to `outdir/ley-{codNota}.md` |
| [`download_legal_provisions_provenance_ids`](#download_legal_provisions_provenance_ids--a-laws-reform-history) | a collection name (`"leyes"`, `"reglamentos"`, `"normas"`, `"tratados"`) | every instrument's reform history, in memory |
| [`fetch_daily_legal_provisions`](#cutting-a-legal-provision-out-of-its-page) | a date | that day's browsable legal provisions (title, `codNota`, `codEdicion`...) |
| [`download_legal_provisions_titles`](#download_legal_provisions_titles--every-legal-provision-ever-published-as-titles) | nothing (reads the whole `notas-archivo` release) | every legal provision ever published, as `codNota`+`titulo`+`fecha`, written to a gzipped JSONL file |
| [`download_scjn_leyes_corpus`](#the-scjn-path--a-laws-consolidated-text-at-each-reform) | a law's `slug` | every snapshot of that law in the `scjn-leyes` release, with its `codNota` links, in memory |
| [`download_scjn_leyes_index`](#the-scjn-path--a-laws-consolidated-text-at-each-reform) | nothing (reads one small release asset) | the reverse index `codNota → (law, snapshot)`, in memory |

They compose: `download_legal_provisions_provenance_ids` gets you the `codNota` list
`reconstruct_legal_provisions` needs, and `reconstruct_legal_provisions` gets you a
law's current text the same way `legal_provisions` gets you a single legal
provision's — built from nothing but the DOF's own legal provisions, one
Markdown file at a time.

```python
from nota2md import download_legal_provisions_provenance_ids, legal_provisions, reconstruct_legal_provisions

leyes = download_legal_provisions_provenance_ids("leyes")
cpeum = next(l for l in leyes if l["abrev"] == "cpeum")

dest = reconstruct_legal_provisions(cpeum["historial"], "output", nombre_ley=cpeum["nombre"])
print(f"{cpeum['nombre']} -> {dest}")
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

`fetch_daily_legal_provisions(date)` is the per-day index itself — a day's
browsable legal provisions (title, `codNota`, `codEdicion`...), from SIDOF
and, when SIDOF has nothing for that day, from the DOF's own website:

```python
from nota2md import fetch_daily_legal_provisions
import datetime as dt

for nota in fetch_daily_legal_provisions(dt.date(2026, 7, 15))["NotasMatutinas"]:
    print(nota["codNota"], nota["titulo"])
```

### The SCJN path — a law's consolidated text at each reform

Since v0.5.0, `legal_provisions` answers **the whole law, not the reform
decree**, whenever it can. The SCJN's Buscador keeps, for every reform of
every federal law, a snapshot of the law's consolidated text exactly as it
read right after that reform; those snapshots are crawled, matched to the DOF
`codNota` that enacted them, and published as the
[`scjn-leyes`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-leyes)
release (315 laws, 3,724 snapshots). A `codNota` the release covers is
answered straight out of it — which is what
[`reconstruct_legal_provisions`](#reconstruct_legal_provisions--a-laws-current-text-from-its-dof-legal-provisions)
otherwise has to *infer* by replaying every reform decree in order.

**The SCJN is not an official source of legal text.** dof.gob.mx/SIDOF is.
Every file this path writes therefore keeps the corpus' own provenance header
(`fuente: scjn`, `ordenamiento`, `fecha_publicacion`, …) intact, and lands
under a different name — so the file alone says where it came from:

| | file written |
|---|---|
| SCJN path | `outdir/{slug}-{fecha}.md` (e.g. `lfca-05-01-1999.md`) |
| every DOF path | `outdir/nota-{codNota}.md` (unchanged) |

```python
from nota2md import legal_provisions

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

Two readers of the release are exported for working with the corpus directly:

```python
from nota2md import download_scjn_leyes_corpus, download_scjn_leyes_index

indice = download_scjn_leyes_index()          # codNota -> [{slug, archivo, ...}]
lfca = download_scjn_leyes_corpus("lfca")     # every snapshot of one law
```

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
repeating. It never reads a law's official consolidated ("texto vigente") text;
that exists separately (`nota2md.texto_vigente`) only as independent ground
truth to check reconstructions against, in `tests/test_leyes_44.py`, over 43
real federal laws.

The SCJN corpus becoming `legal_provisions`' default source (issue #117) does
not retire this function (issue #129's audit): the corpus links 2,474 of its
3,724 `leyes` snapshots to a codNota, only 526 of them pre-1999, and covers no
reglamento, tratado or NOM at all — so replaying a law's own reform decrees
remains the only route for everything it does not reach, as well as the
independent cross-check on what it does.

```python
from nota2md import reconstruct_legal_provisions

# cpeum's own historial: [5592105, 5730586, ...] — oldest first, index 0 the
# original publication (see download_legal_provisions_provenance_ids below for where a
# law's own historial list comes from).
dest = reconstruct_legal_provisions(
    [5592105, 5730586], "output", nombre_ley="LEY de Amnistía",
)
print(dest.read_text(encoding="utf-8"))   # -> output/ley-5592105.md
```

Each legal provision it needs is fetched through `legal_provisions` into the
same `outdir`, as `nota-{codNota}.md` — a legal provision already there from
an earlier call (this law's own previous run, or another law's sharing the
same `outdir`) is read back from disk instead of fetched again.

`nombre_ley` (as `download_legal_provisions_provenance_ids` names it, e.g.
`"LEY de Amnistía"`), scopes every legal provision to the one instrument
among the several a single decree may touch — pass it whenever a legal
provision is shared with another law's history, which `leyesmx`'s data does
not mark on its own. Left out, a legal provision is assumed to concern only
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

## `download_legal_provisions_provenance_ids` — a law's reform history

Reads a Mexican legislative-history collection — laws, regulations, Normas
Oficiales Mexicanas, international treaties — back from the
[`historial-legislativo`](https://github.com/INGEOTEC/LegalIA/releases/tag/historial-legislativo)
release that [`leyesmx`](../leyesmx) publishes:

```python
from nota2md import download_legal_provisions_provenance_ids

leyes = download_legal_provisions_provenance_ids("leyes")   # or "reglamentos", "normas", "tratados"
cpeum = next(l for l in leyes if l["abrev"] == "cpeum")
print(cpeum["nombre"], cpeum["reformas"], len(cpeum["historial"]))
```

Downloads that collection's tarball straight into memory — nothing touches
disk — and returns one dict per instrument, merging its catalogue entry (name,
reform count, dates...) with its own `historial`: the `codNota` of its reforms
or decrees, oldest first, index 0 the original publication. That is exactly
what `reconstruct_legal_provisions` expects as its own first argument.

## `download_legal_provisions_titles` — every legal provision ever published, as titles

Implemented in [`dofjson.titulos`](../dofjson) and re-exported here so it sits
alongside the rest of `nota2md`'s entry points. Builds a compact `codNota` +
`titulo` + `fecha` + `codOrgaUno` dataset covering every legal provision
published since 1917 (~1.2 million rows, a few tens of MB compressed), read
straight from the `notas-archivo` release — nothing downloaded touches disk
except the two result files:

```python
from pathlib import Path
from nota2md import download_legal_provisions_titles

download_legal_provisions_titles(Path("titulos.jsonl.gz"))
```

```bash
dofjson --titulos --outdir output    # -> output/titulos.jsonl.gz
```

## `markdown_to_akoma_ntoso` — experimental Akoma Ntoso (OASIS LegalDocML) conversion

**Experimental — not one of the entry points above.** A first-pass mapping
from nota2md's own Markdown (as `legal_provisions()`/`reconstruct_legal_provisions()`
write it) to [Akoma Ntoso](https://www.oasis-open.org/committees/tc_home.php?wg_abbrev=legaldocml)
XML, the OASIS standard vocabulary for structured legal documents — see
[issue #91](https://github.com/INGEOTEC/LegalIA/issues/91) for the
specification review this implements against, now checked against the real
`akomantoso30.xsd` schema of the OASIS Standard (29 August 2018), not just
its prose (see the module's own docstring, `nota2md/akoma_ntoso.py`, for the
full list of corrections that check turned up):

```python
from pathlib import Path
from nota2md import download_legal_provisions_provenance_ids, reconstruct_legal_provisions
from nota2md.akoma_ntoso import markdown_to_akoma_ntoso

leyes = download_legal_provisions_provenance_ids("leyes")
cpeum = next(l for l in leyes if l["abrev"] == "cpeum")

md_path = reconstruct_legal_provisions(cpeum["historial"], Path("output"), nombre_ley=cpeum["nombre"])
xml_path = markdown_to_akoma_ntoso(md_path, Path("output"), fecha="2024-09-15")
```

`fecha` genuinely matters, not just for a resolvable IRI: `FRBRdate` is
mandatory at every FRBR level and typed `xsd:date`, so leaving it out
produces XML that is not schema-valid at all (`numero` is the one that is
truly optional — the opposite of what issue #91 first assumed before
checking the schema). `FRBRauthor` needs no parameter — the DOF is always
the issuing authority, so it is always filled in as `href="#dof"`.

Akoma Ntoso has no native element for "Transitorios" (or "Considerandos");
this follows the convention the standard's own official examples use for
that kind of gap — a plain `<section refersTo="#transitorios">` (the schema
has no `name` attribute on `section` at all, unlike what issue #91 first
guessed), with a matching `<TLCConcept>` declared under
`<meta>/<references>` so `refersTo` actually resolves to something, the way
the official examples' own do. Fracciones/incisos inside an article
(markdown blocks like `"**I.**"`/`"**a)**"`) are nested into Akoma Ntoso's
own `<paragraph>`/`<point>` hierarchy — a DOF article can legally repeat the
same fracción label under two separate "I. a X." lists, which is
disambiguated rather than left to collide (eId must be unique across the
whole `<act>`). Spanish is `"esp"` (`FRBRlanguage` and the IRI's own
language segment), matching the official Uruguayan example, not the ISO
639-2 code `"spa"` this module used at first. See the module's own
docstring for the complete, current list of what is and is not covered.

[Issue #93](https://github.com/INGEOTEC/LegalIA/issues/93) asked for the
other direction too: `akoma_ntoso_to_markdown()` reads an `<akomaNtoso>`
XML file back into nota2md's own Markdown — `<b>` back to `**bold**`, a
fracción/inciso's `<num>` back to its `"**I.**"`/`"**a)**"` label, and so
on:

```python
from nota2md.akoma_ntoso import akoma_ntoso_to_markdown, markdown_to_akoma_ntoso

xml_path = markdown_to_akoma_ntoso(md_path, Path("output"), fecha="2024-09-15")
md_path_de_vuelta = akoma_ntoso_to_markdown(xml_path, Path("output"))
```

Written to ``outdir/{stem}.md`` (`stem` is the XML file's own stem with a
trailing `.akn` dropped, so `markdown_to_akoma_ntoso`'s own
`"{md_path.stem}.akn.xml"` round-trips back to `"{md_path.stem}.md"`, not
`"{md_path.stem}.akn.md"`). It is a best-effort inverse, not a lossless
one: information the forward conversion never keeps in the XML at all —
the note's own H1 title, any "#"/"##" Markdown heading other than
Transitorios' own (Akoma Ntoso has no dedicated element for a `<preamble>`
paragraph that happened to be a heading, unlike `<section>`'s `<heading>`)
— cannot be recovered from the XML alone.

Checked against a real note too, not just hand-written Markdown snippets:
`tests/test_akoma_ntoso_red.py` round-trips a real CONAGUA "acuerdo"
(codNota 5793639, the same one #91/#92 verified by hand) through both
converters and measures how close the result lands to the original with
`difflib`, the same way `test_leyes_44.py` scores a reconstruction against
its own ground truth — ~0.996 similarity once Markdown syntax is folded
away, the gap being almost entirely that dropped H1 title. Like
`test_leyes_44.py`, it makes a real network call and is excluded from the
default run:

```bash
pytest packages/nota2md -q --ignore=packages/nota2md/tests/test_leyes_44.py \
    --ignore=packages/nota2md/tests/test_akoma_ntoso_red.py \
    --ignore=packages/nota2md/tests/test_scjn_release_red.py
```

`tests/test_scjn_release_red.py` is the third of those: it reads the real
`scjn-leyes` release end to end, which is the only way to catch a corpus
re-packaged without re-uploading its `indice-global.json.gz` (a codNota then
resolves to a snapshot file no longer in the law's tarball).

## Installation

```bash
pip install nota2md          # legal_provisions' HTML path, plus reconstruct_legal_provisions
                              # and download_legal_provisions_provenance_ids
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
