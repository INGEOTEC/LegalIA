# Diputados migration audit (issue #185, Fase 0 of #184)

Every call site that depends on the Cámara de Diputados (LeyesBiblio), every
reach of the four-collection abstraction, and every workflow, each assigned to
the phase of #184 that owns it. **Nothing is deleted or changed by this
document** — it is the deletion list Fase 4 (#189) and Fase 5 (#190) work
from, and it exists so that work is a lookup rather than a re-grep.

Compiled on 2026-09-01 from `git grep` over the tracked tree at commit
`e551a88` (`branch-184-192`), not from memory. The greps that produced it:

```bash
git grep -niI "diputados\|leyesbiblio\|leyesmx"
git grep -niI "COLECCIONES\|_ASSETS\|_INDICES\|_une_con_historial\|coleccion"
git grep -nI  "download_legal_provisions_provenance_ids"
```

`git grep` rather than plain `grep`: `scripts/scjn/`, `output/` and
`notas-archivo/` are `.gitignore`d scratch directories holding crawl output,
and matching inside them would have buried the real call sites under tens of
thousands of hits in legal text.

## 1. Coverage: how much of the seed the release already carries

The question Fase 0 owes an answer to. Measured against the
Diputados-derived catalogue on disk (`scripts/scjn/leyes/catalogo.json`, 316
entries) and the published `scjn-leyes` release
(`indice-global.json.gz`, `generado` 2026-09-01T03:26:45+00:00).

| | Count |
|---|---|
| Laws in the Diputados-derived catalogue | 316 |
| `instrumentos` in `indice-global.json.gz` | 315 |
| Laws with a `<slug>.tgz` asset in the release | 315 |
| Catalogue laws the release covers | **315 (99.7%)** |
| Catalogue laws the release does *not* cover | **1** |
| Release entries with no catalogue counterpart | 0 |

The one gap:

- **`oga` — ORDENANZA General de la Armada.** Never crawled: the SCJN does not
  index it at all. It is the last branch of the decision rule the website's
  Figure 3 draws — published in 1912, so it predates the DOF archive itself
  and there is no `codNota` to fall back to either (`leyesmx`'s README records
  it, with the Código de Comercio of 1889, as one of the ten entries that
  never got a `codNota`). The release's own summary agrees:
  `website/pages/data/scjn-leyes-summary.json` lists exactly
  `never_crawled: ["ORDENANZA General de la Armada"]` against
  `catalogue_entries: 316`, `instruments_with_directory: 315`. Out of scope
  rather than a defect, and it does not block any phase.

Two results that look like gaps and are not:

- **Matching must be on `slug_instrumento`, not on `catalog_key`.** Matched by
  `catalog_key` (which returns `abrev` verbatim) the release appears to miss
  14 laws; matched by `slug_instrumento` (which normalizes) it misses one.
  The 14 are the laws whose historical `abrev` contains an underscore, which
  the slug hyphenates: `lif_2026`, `pef_2026`, `ligie_2022`, `reg_diputados`,
  `reg_senado`, `lrart27_mn`, `lrart3_mmce`, `lrart5_prof`, `lrart6_mdr`,
  `lrart76_fracvi`, `lrfiyii_art105`, `lrfv_art76`, `lrfxiiib_art123`,
  `lrfxviii_art73`. **#186 must merge on `slug_instrumento` and keep the
  previous catalogue's own `abrev` verbatim** — the abbreviation and the slug
  are not the same string for these 14.
- **`actualizado` is missing for 3 laws, in the catalogue and in the release
  alike**: `lisipl`, `lcmopfih`, `lfcpq`. Their `estado.json` records
  `"actualizado": null` because their Diputados `historial` was empty. That is
  `PENDIENTE_SIN_ACTUALIZADO`, an existing and handled state, not a
  regression introduced by dropping Diputados.

## 2. `abrev` provenance — the paragraph that has to outlive `diputados.py`

Once `leyesmx/diputados.py` is gone, nothing in the repo says where `cpeum` or
`lft` came from, and the values are load-bearing: an `abrev` is the
`scjn-leyes` slug, the release's asset name, and the key every catalogue merge
joins on.

They are **not** derived, computed or chosen by this project. Each one is the
token in the Cámara de Diputados' own LeyesBiblio URL for that law's reform
page — `ref/<abrev>.htm` — read off the index page verbatim by
`leyesmx.diputados.lista_leyes` (`abrev = enlace.group(1)`), lowercased for
regulations, where it was the file stem instead (`reg_ladua`). So they are the
Chamber's internal file names, adopted wholesale in 2026 and frozen the moment
they became slugs of a published release.

The consequence for every later phase: **an `abrev` is never re-derived.**
Minting one is only ever needed for a law that has no `abrev` at all — a
newly published law nobody has assigned one to — which is why #186 owns a
documented, deterministic minting rule and why that rule is never applied to
an existing entry.

## 3. Historical measurements worth keeping (from `packages/leyesmx/README.md`)

That README is deleted with the package in Fase 4, and these numbers justified
design choices that will otherwise look arbitrary in the git history. They are
records of what was measured on the Diputados-derived build, not commitments
about anything the SCJN+DOF pipeline will produce.

- **Linking rates.** Laws: 316 instruments, 3,450 entries, 3,440 with a
  `codNota`, 3,337 of those a verbatim title match. Regulations: 137 / 287 /
  277 / 261. Every one of the 3,136 *numbered* reforms of a law was linked;
  all ten unlinked entries were original publications (Código de Comercio
  1889 and Ordenanza General de la Armada 1912 predate the DOF archive; some
  days carry very few provisions in the dataset — Ley Aduanera's 15-12-1995
  has 12; Ley de Fondos de Inversión was published under its former name).
  **These figures stop being an acceptance criterion** — see §7.
- **Two metrics, chosen by what the source gives.** A numbered reform comes
  with the decree's own title, so containment settles it; an original
  publication comes with only the instrument's *name*, so similarity of name
  is the only available question. Using containment where only a name exists
  is not weaker but wrong: it linked the Ley Federal del Trabajo's 1970
  publication to a Mexico City traffic-regulation decree, and the Código
  Fiscal's to the 1982 budget. **#187 must carry these two incidents into the
  comments of whatever it writes** — they are the reason the metric is chosen
  by the data available and not by preference.
- **Treaty pairing (deleted, not migrated).** 1,956 treaties, 2,745 decrees,
  517 pairs with identical names, 272 more matched, 1,167 single-decree.
  Plain string similarity gave **0.88** to a 1977 Gabon trade agreement paired
  against a 1994 framework agreement — higher than it gave real pairs —
  because treaty names are formulaic boilerplate. Weighting each word by
  rarity put that false pair at 0.56 and real ones at 0.72–0.78, so the
  threshold was set at **0.70**.
- **NOM codes (deleted, not migrated).** 4,674 NOMs across 8,880 provisions,
  2,044 with more than one. Sixty years of the gazette left **253 distinct
  code shapes**, so codes are kept as opaque normalized strings: parsing the
  parts read a year as a dependency and mislabelled **927** provisions on the
  first attempt. Short citations folded into a unique extension recovered 114
  citations; the 287 codes that admit several extensions, and their 669
  provisions, went to `citas-ambiguas.json` rather than being guessed at.
- **A `null` that was not Diputados' fault.** Reform 139 of the Constitution
  (`08-03-1999`) was unlinked until `dofjson.dofweb` was written: SIDOF
  reports the days it loses as days with no gazette. The recovery path is in
  `dofjson` and is unaffected by this migration.

## 4. Deletion list — code

Phase in the last column is the phase of #184 that owns the entry.

### 4.1 `packages/leyesmx` — the whole package — **deleted in #189**

Not on PyPI, so it was a plain deletion: no deprecation release, no version
bump, nothing to announce. 2,426 lines including tests — every row of the
table below is gone from the tree; the table stays as the record of what was
there.

| Path | Lines | What it is | Phase |
|---|---|---|---|
| `leyesmx/diputados.py` | 565 | LeyesBiblio scraping: three page layouts, `parse_reformas`, `lista_leyes`, `pagina_de_reformas`, the cp1252 decode, decree-PDF mirroring | #189 |
| `leyesmx/dof.py` | 190 | entry→`codNota` linking: `similitud`, `similitud_nombre`, `notas_por_fecha`, `enlaza`, the name-match floor | #189 |
| `leyesmx/tratados.py` | 225 | out of scope: rarity-weighted treaty pairing, the 0.70 threshold | #189 |
| `leyesmx/normas.py` | 134 | out of scope: NOM code extraction, short-citation folding, `citas-ambiguas.json` | #189 |
| `leyesmx/cli.py` | 301 | `--ley todas\|reglamentos\|normas\|tratados\|<abrev>`, `--decretos`, `--cache-dir`, `--out` | #189 |
| `leyesmx/__main__.py`, `__init__.py` | 6 | entry point | #189 |
| `tests/test_diputados.py` | 399 | **LeyesBiblio HTML fixtures** — see §4.2 | #189 |
| `tests/test_dof.py` | 216 | linking metrics — see §4.2 | #189 |
| `tests/test_tratados.py` | 164 | treaty pairing | #189 |
| `tests/test_normas.py` | 133 | NOM codes | #189 |
| `tests/test_cli.py` | 93 | `escribe_json`'s "index N is reform N", cache-dir resolution | #189 |
| `README.md`, `pyproject.toml` | — | see §3 before deleting the README | #189 |

### 4.2 Tests whose fixtures are LeyesBiblio HTML

All of them are inline HTML strings built by helpers in the test file itself
(`tabla()`, `fila()` in `test_diputados.py`) — there is no `fixtures/`
directory under `packages/leyesmx/tests/`, so nothing outside these files has
to be hunted down.

- `tests/test_diputados.py` — `TestPaginaDeReformas`, `TestParseReformas`,
  `TestDescarga` (the cp1252 decode), `TestPdfDelDecreto`,
  `TestDescargaDecreto`, `TestLeyOrdinaria`, `TestListaLeyes`. Every one is a
  parser test: **it dies with the parser**, there is nothing to migrate.
- `tests/test_dof.py` — `TestNormaliza`, `TestSimilitud`, `TestEnlaza`,
  `TestSimilitudNombre`, `TestPuntuaEntrada`, `TestMinimoPorNombre`. These
  assert *linking* behaviour, not LeyesBiblio markup, so they are the ones
  that could in principle survive. They do not: `nota2md.scjn` already carries
  its own equivalents (`ratio_similitud`, `title_candidates_por_fecha`,
  `enlaza_por_titulo`, `title_link_status`, `confirm_by_content_diff`) with
  their own tests in `packages/nota2md/tests/test_scjn.py`, and #184 says the
  `leyesmx.dof` implementation is deleted rather than ported. What must
  survive is the *reasoning* — the two mislinking incidents in §3 — as
  comments in whatever #187 writes.

### 4.3 `nota2md` — the four-collection abstraction and its public API

| Symbol / file | What it is | Phase |
|---|---|---|
| ~~`nota2md/utils.py`, whole module~~ **deleted in #187** | `download_legal_provisions_provenance_ids`, `COLECCIONES`, `_ASSETS`, `_INDICES`, `_une_con_historial`, `_con_historial_por_archivo` / `_por_mapa` / `_paralelo`, `listar_assets`, `_miembros`, `RELEASES_API` | #187 / #189 |
| ~~`nota2md/__init__.py`~~ **done in #187** | the `from nota2md.utils import ...`, the `__all__` entry, the docstring sentence and the entry-point count | #187 |
| ~~`packages/nota2md/tests/test_utils.py`~~ **deleted in #187** | went with `utils.py` | #187 |
| ~~`nota2md/texto_vigente.py`~~ **deleted in #188** | cleaned Diputados' consolidated "texto vigente" PDF into Markdown; nothing else imported it (`scjn_api` keeps its own copies on purpose) | #188 |
| ~~`packages/nota2md/tests/test_texto_vigente.py`~~ **deleted in #188** | asserted the Diputados page header/footer strip | #188 |
| ~~`packages/nota2md/tests/fixtures/leyes/*.md` + `historial_44.json`~~ **regenerated in #188** | now the SCJN's own consolidated text per law plus its `indice.json` history, written by `scripts/regenera_fixtures_leyes.py`; 42 laws (`lfgr` excluded) | #188 |
| ~~`nota2md/scjn.py` `COLECCION_SCJN_LEYES`~~ **deleted in #189** | the constant, and `construye_indice_global`'s `coleccion` parameter it defaulted; the payload keeps the literal `"coleccion": "leyes"` field, which the published index has and `test_scjn_release_red.py` asserts | #189 |
| ~~`nota2md/scjn.py` `slug_instrumento` / `catalog_key`~~ **done in #189** | both now raise `KeyError` without an `abrev`; the name-slugging half became `slugify(texto)`, which `extract_scjn_titles.py`'s discovery uses on a name that has no catalogue entry yet | #189 |

**`download_legal_provisions_provenance_ids` is deleted outright** (#184's
decision, recorded in `CLAUDE.md`): no shim, no deprecation, no name kept for
compatibility. It is a public-API removal, so it takes a changelog note
regardless of whether a version bump follows. Its consumers, all of them:

| Consumer | Disposition | Phase |
|---|---|---|
| `scripts/extract_scjn_titles.py:64,96` | the only production caller; rebuilt on `download_scjn_leyes_catalog` | #186 |
| ~~`scripts/fetch_legal_provisions_provenance.py`~~ | deleted with the function | **#187, done** |
| ~~`packages/nota2md/tests/test_utils.py`~~ | deleted with `utils.py` | **#187, done** |
| `packages/nota2md/README.md` (lines 27, 34, 41, 43, 317, 330, 348, 356, 358, 456) | rewritten | #190 |
| `README.md` (root, lines 21, 72, 77, 80) | rewritten | #190 |
| `scripts/README.md:17,84` | rewritten | #190 |
| ~~`packages/leyesmx/README.md:62,68,70`~~ | deleted with the package | **#189, done** |
| `CLAUDE.md:37,86,238` | rewritten (line 95 and 123 are #184's own section and describe the removal) | #190 |
| `nota2md/scjn.py:317,521,924`, `nota2md/scjn_api.py:572` | docstring/comment references, rewritten without the dead name | #190 |

`website/pages/leyes.ipynb` does **not** call it — it reads
`data/scjn-leyes-summary.json`. Its Diputados content is prose and one Mermaid
node; #190 strips the mentions and #192 rewrites the page.

### 4.4 `nota2md` — what looks like collection dispatch and must **not** be deleted

Recorded here because a mechanical `grep reglamento` over `packages/` would
delete it and break the surviving `leyes` collection.

- **`nota2md.scjn.grupo_instrumento` and `nota2md.scjn_api.grupo_de_categoria`
  keep their `"reglamento"` branch.** They do not select a collection; they
  are the guard in `scjn_api.elige_ordenamiento` (filter 2) that stops a
  *ley* search from matching the *reglamento* of that same ley, whose title
  literally contains the law's name — the `lopgjdf` failure mode. Deleting the
  reglamento side of `_GRUPO_REGLAMENTO` / `_CATEGORIA_GRUPO` would silently
  degrade linking for laws. #189's "collection branches in
  `nota2md.scjn`/`scjn_api`" must be read as excluding these two.
- Same for `grupo_instrumento`'s `None` return (a title starting with neither
  word), which never excludes anyone and is not a "tratado" branch.

### 4.5 Scripts

| Script | Diputados / collection footprint | Disposition | Phase |
|---|---|---|---|
| `extract_scjn_titles.py` | the only remaining consumer of the Diputados-derived `historial`; `COLECCIONES = ("leyes","reglamentos","tratados")` (l. 68), `--coleccion` (l. 118), `_actualizado()` resolving `historial[-1]` through `dofjson`, and 5 docstring references | rebuilt on `download_scjn_leyes_catalog`; `--coleccion` and `COLECCIONES` removed; `actualizado` re-derived per #186 | #186 |
| `fetch_scjn_legislacion.py` | 84 `coleccion` references — `COLECCIONES` (l. 220), `--coleccion` (l. 717), the per-collection `outdir` layout, `refresca_catalogo()` (l. 309) which calls `extract_scjn_titles.py` with `--coleccion`, `_fecha_release_historial()` (l. 406, used at l. 475), and the positional resume checkpoint (`_lee_progreso`/`_guarda_progreso`, `{"indice": n}` — a position in the catalogue list, which is why #186 must fix the catalogue's order) | collapse to one collection; the `historial-legislativo` publication-date lookup is removed or repointed at `scjn-leyes` | #186 |
| `enlaza_scjn_legislacion.py` | 22 references — `COLECCIONES` (l. 84), `--coleccion` (l. 282), the `extract_scjn_titles.py` hint it prints | collapse to one collection | #186 / #187 |
| `empaqueta_scjn_leyes.py` | 15 references, all `leyes`-only already; carries the **no-automated-publish rule** (l. 9–15) that #184 must not override, and the `estado.json` description (l. 54, 157, 167) which named Diputados | kept, rule intact; docstrings rewritten in **#189**, and #190 removed the comparison to the once-automated `reformas.yml` | **#189/#190, done** |
| ~~`empaqueta_historial.py`~~ | the four-asset map `COLECCIONES`, `--datos packages/leyesmx/data` | deleted — no asset left to pack | **#187, done** |
| ~~`fetch_legal_provisions_provenance.py`~~ | wrapped `download_legal_provisions_provenance_ids` | deleted | **#187, done** |
| `resume_scjn_leyes.py` | 7 references, `leyes`-only, feeds `website/pages/data/scjn-leyes-summary.json` | keep | — |
| ~~`fetch_lfiiedb_dof.py`~~ | 3 Diputados references in its prose (l. 20, 40, 125) | prose rewritten (l. 125 in #189, l. 20 and 40 in #190); the rest of the page stays the Spanish procedure record it always was | **#190, done** |
| `verifica_scjn_api.py`, `reparar_notas_archivo.py`, `spike_scjn_api.py`, `md2akn_sweep.py` | 1 incidental reference or none | untouched | — |
| ~~`scripts/README.md`~~ | 6 collection references + the `empaqueta_historial.py` section (l. 11–21) and the `actualizado` description (l. 84–106) | rewritten across #186–#189; #190 added the **seeding step** and the **byte-reproducibility check** the deleted workflow carried, both as commands, plus the frozen-release paragraph | **#190, done** |

## 5. `.github/workflows/` — a verdict for every file

The directory had five files; it has four since #190. This is a statement
about all of them, re-checked file by file in #190.

| File | Runs Diputados code? | Verdict | Phase |
|---|---|---|---|
| ~~`reformas.yml`~~ **deleted in #190** | **Yes, it did** — `pip install -e packages/leyesmx`, `python -m leyesmx --ley ...` for the four collections, monthly cron plus `workflow_dispatch` | **Deleted, not repointed**, and no workflow replaces it. Two independent reasons: three of its four collections are out of scope, and the fourth would be SCJN-derived, which `scripts/empaqueta_scjn_leyes.py` (issue #115, Hallazgo C) forbids publishing without a human. Two things it carried have to survive as documentation in `scripts/README.md`: the **seeding step** (unpack the existing release first, so a single-`abrev` run does not publish a `leyes.tgz` holding one law) and the **byte-reproducibility** of the tarballs that lets "upload only what changed" be a byte comparison rather than a guess. | #190 |
| `test.yml` | No | **Done in #189**: `leyesmx` removed from the matrix and the "Install sibling package (dofjson) for leyesmx" step removed, in the same commit as the package, so CI never goes red between commits. Note for CI expectations: this workflow runs `pytest packages/<pkg>` with **no** `--ignore`, so the four network test files run here even though `CLAUDE.md`'s routine command excludes them. | #189 |
| `publish-pypi.yml` | No | Kept **unchanged**, re-verified in #190. #184's text says it has a `leyesmx` entry to remove; it does not — its tag triggers are `dof2md-v*`/`dofjson-v*`/`nota2md-v*` and its `workflow_dispatch` choices are `dof2md`/`dofjson`/`nota2md`. `leyesmx` was never publishable through it. | — |
| `notas-archivo.yml` | No — installs `packages/dofjson` only | Kept unchanged, re-verified in #190. Publishes the DOF's own archive, which this epic does not touch. | — |
| `website.yml` | No | Kept unchanged as a workflow. The *content* it publishes: #190 removed the Diputados mentions and the retired collections from `pages/leyes.ipynb` and recommitted its `_freeze/`; #192 rewrites the page. | — |

The directory now contains those four files, none of which runs Diputados
code and none of which publishes SCJN-derived data. That second property is
permanent: no future workflow may publish SCJN-derived data either.

## 6. Data, releases and docs

| Artifact | Disposition | Phase |
|---|---|---|
| ~~`historial-legislativo` release, `leyes.tgz`~~ **deleted in #190** | **Asset deleted** (`gh release delete-asset`; sha256 `8348dd9d…f15e`, 28,538 bytes, last built 2026-07-28, kept out of git as every dataset is). The reform history of laws lives in the `scjn-leyes` release itself — each law's `indice.json` plus `indice-global.json.gz`. No new dataset, no new asset. | #190 |
| `historial-legislativo` release, `reglamentos.tgz` / `normas.tgz` / `tratados.tgz` | **Done in #190**: kept downloadable, **labelled by name in the release notes** as a frozen record nothing in the repo can regenerate, with the date each was last built. Stated in plain words, not left to be inferred from a timestamp. `SHA256SUMS.txt` still covers them. | #190 |
| `.github/historial-legislativo.md` (the release notes body) | **Rewritten in #190** and pushed to the release with `gh release edit --notes-file`. It used to name LeyesBiblio as the origin, the monthly workflow as the publisher, four maintained collections, and points at `packages/leyesmx/README.md` — all four statements stop being true. | #190 |
| ~~`packages/leyesmx/data/` in `.gitignore` (l. 32–34)~~ | Removed with the package | **#189, done** |
| ~~`.devcontainer/python.sh:7`, `.vscode/settings.json:7`~~ | `leyesmx` install / analysis path removed | **#189, done** |
| ~~`README.md` (root, l. 21, 72–80, 110)~~ | **#190, done**: the four-package list is `dofjson`/`nota2md`/`dof2md`/`md2akn` and the entry-point count matches `nota2md.__all__` (eight). The `download_legal_provisions_provenance_ids` example was already gone with #187's rewrite. | **#190, done** |
| ~~`docs/source/conf.py:21`, `docs/source/index.rst:33,35`~~ | Read order drops to four packages, ending in `md2akn` | **#190, done** |
| ~~`packages/dofjson/dofjson/api.py:4`, `packages/dofjson/tests/test_dofjson.py:3`~~ | "(nota2md, leyesmx...)" in prose — rewritten in **#189**, since they were hits on that phase's own definition-of-done grep | **#189, done** |
| ~~`packages/nota2md/README.md:333,353` and the `download_legal_provisions_provenance_ids` section~~ | #187's banner already covered the removal; #190 refreshed the one stale measurement left (the corpus links 3,291 of 3,724 snapshots since #187, not 2,474) and dropped "no reglamento, tratado or NOM" as a scope statement | **#190, done** |
| `website/pages/leyes.ipynb` (l. 189, 191, 263) | **#190, done** for its own half: the "Why the Supreme Court and not only the Chamber of Deputies" argument, the pipeline figure's LeyesBiblio node, the seeding section, the `nombre_buscado` prose, the overview table's row label and the two stale publishing claims; the notebook was re-executed and `_freeze/` recommitted. **#192 rewrites the page** and takes precedence over anything #190 says about it | #190 → #192 |
| `packages/dof2md/tests/test_cutter.py:53` | False positive — "ciento cuarenta diputados" inside a legal-text fixture. Expected to survive the definition-of-done grep. | — |
| `packages/nota2md/tests/fixtures/scjn_api/reformas.json`, `tests/fixtures/leyes/*.md` | Incidental mentions inside captured legal text; the `fixtures/leyes/*.md` files are separately replaced by #188 | #188 |

## 7. What this audit deliberately does **not** contain

- **No Diputados baseline.** #185's Deliverable 2 (rebuild the collection once
  with `python -m leyesmx --ley todas` and keep Diputados' reform dates and
  numbering as a fixture) is **omitted in full**, at the user's explicit
  instruction: the decision is to remove Diputados outright, and the SCJN is
  already known to be sufficient.
- **No acceptance comparison for #187.** The 316 laws / 3,450 entries / 3,440
  linked figures are recorded in §3 as history and are **not** an acceptance
  criterion for the rebuilt history. #184's definition of done is read
  accordingly.
- **Reform numbering is redefined, not reproduced.** "Reform N" becomes the
  chronological order of the SCJN's own reform table. No attempt is made to
  match Diputados' numbering, and no measurement against it will exist once
  the scraper is gone.

## 8. Phase index

Every entry above, gathered by owning phase.

- **#186 (Fase 1)** — `extract_scjn_titles.py`, `fetch_scjn_legislacion.py`,
  `enlaza_scjn_legislacion.py`: `catalogo.json` without Diputados, the
  `--coleccion` flags, the catalogue's order and the resume checkpoint that
  depends on it, `actualizado`, and the minting rule for a new law's `abrev`.
- **#187 (Fase 2), done** — reform history from SCJN + DOF, inside the
  `scjn-leyes` pipeline; the two mislinking incidents preserved as comments.
  Also took, from #189's list, the deletion of `nota2md/utils.py`,
  `download_legal_provisions_provenance_ids`, `test_utils.py`,
  `fetch_legal_provisions_provenance.py` and `empaqueta_historial.py`, since
  the run's phase-1 notes assign the public-API removal to this phase.
  **`.github/workflows/reformas.yml` now calls a script that no longer
  exists** — harmless on this branch, and #190 deletes the workflow.
- **#188 (Fase 3), done** — `texto_vigente.py` and `test_texto_vigente.py`
  deleted, `tests/fixtures/leyes/*.md` and `historial_44.json` regenerated
  from the `scjn-leyes` release, `test_leyes_44.py` restated (its ground
  truth is no longer independent, and it says so).
- **#189 (Fase 4), done** — `packages/leyesmx` deleted whole,
  `COLECCION_SCJN_LEYES` gone, `slug_instrumento`/`catalog_key` now require
  `abrev` (`slugify` split out for the one caller that slugs a bare name),
  `test.yml`'s matrix, `.gitignore`, `.devcontainer` and `.vscode` cleaned,
  and the `--coleccion` flags and `COLECCIONES` tuples removed from
  `fetch_scjn_legislacion.py`, `enlaza_scjn_legislacion.py` and
  `empaqueta_scjn_leyes.py` — `leyes` is now a **literal path segment**, said
  out loud in each script's own docstring so it reads as a decision.
- **#190 (Fase 5), done** — `reformas.yml` deleted with no replacement and a
  re-checked verdict for the four remaining workflows (§5); `leyes.tgz`
  deleted from the `historial-legislativo` release, `SHA256SUMS.txt`
  regenerated over the three frozen assets (`sha256sum -c` re-verified
  against what the release now serves), and
  `.github/historial-legislativo.md` rewritten and pushed as the release
  body; `CLAUDE.md`, the root `README.md`, `docs/`, `packages/nota2md/README.md`
  and `scripts/README.md` updated; the Diputados half of
  `website/pages/leyes.ipynb` removed and its `_freeze/` recommitted.
- **#192** — the Quarto site, which overrides #190 wherever they overlap.
