"""Everything about the SCJN's reform-dated snapshots that is not the
transport: catalogue slugs, crawl state, the provenance header's reader,
the link from a snapshot to the DOF `codNota` that published it, and the
`scjn-leyes` release's own readers.

The crawl itself lives in `nota2md.scjn_api`, against the SCJN's JSON API
(`/SCOW-API`, issue #172). Until issue #179 this module also carried the
crawler for the legacy WebForms Buscador (`/Buscador/`): a search POST
round-tripping `__VIEWSTATE`/`__EVENTVALIDATION`, a detail page whose `q`
token was scoped to the session that requested it, a reform grid paged
through `__EVENTTARGET`, and one `.docx` download per row parsed by
`docx_a_markdown`. All of it is gone. What replaced it, and why:

- The old Buscador simply did not index everything. Searching it for the
  LEY Federal de Cine y el Audiovisual returned 0 candidates, twice, live
  (issue #124's "Mecanismo 2"); the JSON API answers with
  `idOrdenamiento` 188805 for the same name. That law is now in the corpus.
- An instrument is addressable by a stable `idOrdenamiento` instead of a
  session-scoped URL, so a crawl is resumable and auditable, and the whole
  reform table arrives in one request instead of 31 postbacks.
- The per-reform article text arrives already segmented, so the heuristic
  paragraph classifier that read the `.docx` is now only the formatter
  `scjn_api` applies to it (`scjn_api._formatea_parrafo`).

The API is still **not** an official contract — a Swagger page is not a
stability promise, the same posture `dofjson.dofweb` takes toward the DOF's
own website, and the rate limiting stays. And the SCJN is
still not an official source of legal text: dof.gob.mx/SIDOF remains that
(the SCJN's own site marks its editorial insertions as "N. DE E." — Nota de
Editor). Every Markdown file the crawl writes is therefore tagged with a
`fuente: scjn` header, whose meaning this migration does not change, so it
is never mistaken for text reconstructed from the DOF's own notes — see
nota2md.leyes.reconstruct_legal_provisions, the DOF-only equivalent this
crawl stands in for once matched by date to a codNota — which
`legal_provisions` now does by default, through this module's
`snapshot_de_codNota` (issue #117).
"""

import gzip
import io
import json
import re
import tarfile
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import requests

from nota2md.cache import (
    SIN_CACHE_DIR,
    asset_en_cache,
    bytes_de_asset,
    resuelve_cache_dir,
)
from nota2md.leyes import normaliza_para_comparar

#: User-Agent for the `scjn-leyes` release's own GitHub requests -- the only
#: network this module still does on its own (the SCJN crawl lives in
#: `nota2md.scjn_api`, which carries its own headers).
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LegalIA-nota2md/1.0)"}


def _normaliza(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip().lower()


# --- Fase 3 (issue #115): guard against a wrong-document match ---------
#
# Candidate selection narrowing by Ámbito/Vigencia (issue #105's Fase 0 finding
# 5) resolves *most* of "searching by name alone can return something
# unrelated", but not all of it — issue #115's manual audit of the already-
# crawled corpus found 5 leyes/reglamentos where the SCJN's search returned,
# and the crawler saved, a document with nothing to do with the catalogue
# entry it was searching for. No single similarity threshold catches all 5
# (a title that merely contains the searched name as a substring — e.g. a
# reglamento of the searched-for ley — scores as high as a genuine match), so
# this is three separate guards, each aimed at one shape of the problem:

_ACUERDO_INTERNO = re.compile(
    r"PLENO DE LA (SUPREMA CORTE|SCJN)|ACUERDO GENERAL N[ÚU]MERO\s+\d+/\d{4}", re.I
)
_GRUPO_LEY = re.compile(r"^(ley|c[oó]digo)\b", re.I)
_GRUPO_REGLAMENTO = re.compile(r"^reglamento\b", re.I)
_NOMBRE_ANTERIOR = re.compile(r"\s*-\s*ANTES\b.*$", re.I)

# Below UMBRAL_MINIMO the best candidate left is rejected outright (`ccf`'s
# 0.436: a title that shares only stray words with what was searched).
# Between the two, a candidate is kept but flagged `sospechoso` (`lfd`'s
# 0.676: "LEY Federal de Derechos" vs "LEY FEDERAL DE LOS DERECHOS DEL
# CONTRIBUYENTE" — a real but *different* law, not resolvable by text alone
# without risking false rejections on legitimate near-duplicate titles).
UMBRAL_MINIMO_SIMILITUD = 0.55
UMBRAL_CONFIANZA_SIMILITUD = 0.75


def ratio_similitud(titulo: str, nombre: str) -> float:
    """How closely a candidate's own `titulo` matches the catalogue's
    `nombre` for it, accent/case/whitespace-insensitive — the same
    `SequenceMatcher` ratio `scjn_api.elige_ordenamiento` picks its winner
    by, and that `scjn_api.cabecera` recomputes to decide whether
    `nombre_buscado` is worth
    writing (issue #132). Exposed too so `scripts/empaqueta_scjn_leyes.py`
    can classify an already-crawled snapshot's confidence offline, against
    whatever `ordenamiento` a past crawl already saved to its own header,
    without needing to re-crawl anything.

    A renamed ordenamiento's SCJN title also carries its own former name, as
    a trailing ``-ANTES <título anterior>-`` (confirmed live re-crawling
    `ccf`: "CODIGO CIVIL FEDERAL -ANTES CODIGO CIVIL PARA EL DISTRITO
    FEDERAL...-" scores 0.270 against the catalogue's "Código Civil
    Federal" with the suffix counted in, below even the worst of the 5
    confirmed wrong-document cases — `UMBRAL_MINIMO_SIMILITUD` would reject
    the *correct* document). Stripped before comparing, so a rename never
    counts against the title that is actually current."""
    titulo = _NOMBRE_ANTERIOR.sub("", titulo)
    return SequenceMatcher(None, _normaliza(titulo), _normaliza(nombre)).ratio()


def es_acuerdo_interno(titulo: str) -> bool:
    """Whether `titulo` is one of the SCJN's own internal administrative
    agreements (a Pleno "ACUERDO GENERAL") rather than an ordenamiento of
    the catalogue's own three collections — `lisr`/`lsint`'s failure mode:
    the search returned no actual law as a candidate, only an unrelated
    SCJN acuerdo that happened to mention the searched name in its own long
    title, and nothing in Ámbito/Vigencia/similarity tells those apart from
    a genuine (if oddly worded) match."""
    return bool(_ACUERDO_INTERNO.search(titulo))


def grupo_instrumento(texto: str) -> str | None:
    """"ley" or "reglamento" when `texto` unambiguously starts with one of
    those (a LEY/CÓDIGO is never the REGLAMENTO of itself, or vice versa),
    None when it starts with neither (a tratado's name, mostly) — used to
    reject `lopgjdf`'s failure mode: a reglamento's title can score high on
    pure text similarity against the ley it regulates, since it literally
    contains that ley's own name."""
    if _GRUPO_LEY.match(texto):
        return "ley"
    if _GRUPO_REGLAMENTO.match(texto):
        return "reglamento"
    return None


# --- Editorial commentary removal ("N. DE E." / "NOTA N") --------------
#
# The SCJN's own Markdown mixes two things a reform-annotated paragraph can
# carry: a reform annotation with a real DOF counterpart ("(REFORMADO,
# D.O.F. <date>)" — kept, see reconstruct_legal_provisions and issue #52),
# and the SCJN's own editorial aside, which it marks "N. DE E." (Nota de
# Editor) or, for a sibling convention citing external DOF fee-update
# agreements, "NOTA N" — neither ever published by the DOF itself. Issue
# #114's sweep of the 3,548 snapshots already crawled for `leyes` found this
# in 91% of them (~85k marker occurrences) and catalogued how it is placed:
#
#   - Three ways the marker itself is spelled, all SCJN's own typos of
#     "N. DE E.": missing a period, doubling one, or splitting one across a
#     space ("N DE E", "N. DE. E", "N. DE . E"). The sibling "NOTA N" is
#     only ever this marker when spelled in full caps — a lowercase/mixed
#     "Nota N" is `ligie`'s tariff schedule citing its own explanatory notes
#     ("Nota 2 del Capítulo 22"), real legal text no DOF/SCJN divide applies
#     to, never an SCJN insertion.
#   - Three ways the note is placed relative to real text: (a) an entire
#     `[...]`/`(...)` paragraph of its own; (b) embedded inside a reform
#     annotation's own parenthesis, which resumes with ", D.O.F. <date>)"
#     right after it; (c) trailing bare after a reform annotation has
#     already closed, running to the end of that paragraph (SCJN's own
#     "N. DE E." is not always bracketed at all).
#   - One no-marker case (Fase 0 finding 3): an unmarked, all-caps bracket
#     ("[REPUBLICADAS]", "[ANTES ARTÍCULO 57]"). The one thing that rules out
#     treating "any bracket" as editorial is that real legal text also uses
#     them — tariff formulas and chemical nomenclature — but every instance
#     of those in the corpus is either letter-free or mixed-case, never a
#     bare run of upper-case words, so requiring both traits (all-caps *and*
#     at least one 3+ letter word) tells the two apart without a formula-
#     specific pattern to maintain.

_MARCADOR_N_DE_E = re.compile(r"N\.?\s*DE\.?\s*\.?\s*E\.?\b", re.I)
# Case-sensitive on purpose — see the section docstring's `ligie` case.
_MARCADOR_NOTA = re.compile(r"NOTA\s+\d+\b")
_CORCHETE = re.compile(r"\[([^\[\]]*)\]")
_PALABRA_LARGA = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]{3,}")
_ANOTACION_REANUDA = re.compile(r",\s*D\.O\.F\.", re.I)


def _empieza_con_marcador(texto: str) -> bool:
    despojado = texto.lstrip()
    return bool(_MARCADOR_N_DE_E.match(despojado) or _MARCADOR_NOTA.match(despojado))


def _es_nota_editorial(contenido: str) -> bool:
    """Whether one `[...]` bracket's content is SCJN editorial commentary —
    its own marker, or (no marker) an all-caps run with an actual word in
    it, never a tariff/chemical bracket (see the section docstring)."""
    if _empieza_con_marcador(contenido):
        return True
    if not _PALABRA_LARGA.search(contenido):
        return False
    letras = [c for c in contenido if c.isalpha()]
    return all(c.isupper() for c in letras)


def _marcadores_sueltos(texto: str):
    """Every bare (not inside a `[...]`) occurrence of the marker in
    `texto`, oldest-first — a `[...]` bracket's own content is handled by
    `_es_nota_editorial` instead, so it is excluded here."""
    corchetes = [(m.start(), m.end()) for m in _CORCHETE.finditer(texto)]

    def en_corchete(pos: int) -> bool:
        return any(inicio <= pos < fin for inicio, fin in corchetes)

    candidatos = [
        m
        for patron in (_MARCADOR_N_DE_E, _MARCADOR_NOTA)
        for m in patron.finditer(texto)
        if not en_corchete(m.start())
    ]
    return sorted(candidatos, key=lambda m: m.start())


def _quita_marcador_suelto(texto: str) -> str:
    """`texto` with its first bare marker (see `_marcadores_sueltos`)
    removed, if any.

    A bare marker always runs to the end of its own paragraph — SCJN never
    gives it a closing delimiter of its own to bound it, *unless* it sits
    inside a reform annotation's still-open parenthesis (a positive paren
    balance right before it) whose own opening was real annotation text
    ("(REFORMADO N. DE E. ..., D.O.F. ...)"): there, the annotation resumes
    right after the note with its own ", D.O.F. <date>)" field, which is
    kept. When that open parenthesis instead belongs to the note itself
    (nothing but whitespace between it and the marker, e.g. "(N. DE E.,
    ..." or "(NOTA 1: ..."), the note still runs to the paragraph's end —
    only a real annotation verb before the marker bounds it early.
    """
    candidatos = _marcadores_sueltos(texto)
    if not candidatos:
        return texto
    m = candidatos[0]
    antes = texto[: m.start()]
    balance = antes.count("(") - antes.count(")")
    if balance > 0:
        apertura = antes.rfind("(")
        if antes[apertura + 1 :].strip():
            resto = texto[m.start() :]
            reanuda = _ANOTACION_REANUDA.search(resto)
            if reanuda is not None:
                inicio = m.start()
                if antes.rstrip().endswith(("(", "[")):
                    inicio = len(antes.rstrip()) - 1
                return texto[:inicio].rstrip() + resto[reanuda.start() :]
        else:
            return antes[:apertura].rstrip()
    return antes.rstrip()


def _quita_notas_editoriales(nucleo: str) -> str:
    if len(nucleo) >= 2 and nucleo[0] in "([" and nucleo[-1] in ")]":
        if _empieza_con_marcador(nucleo[1:-1].lstrip()):
            return ""

    piezas = []
    cursor = 0
    cambios = False
    for m in _CORCHETE.finditer(nucleo):
        if not _es_nota_editorial(m.group(1)):
            continue
        cambios = True
        antes = nucleo[cursor : m.start()].rstrip(" ")
        if antes.endswith(":") and nucleo[m.end() : m.end() + 1] == ".":
            antes = antes[:-1]  # the note's own closing "." now dangles after ":"
        piezas.append(antes)
        cursor = m.end()
    piezas.append(nucleo[cursor:])
    resultado = "".join(piezas) if cambios else nucleo

    sin_suelto = _quita_marcador_suelto(resultado)
    if sin_suelto != resultado:
        cambios = True
        resultado = sin_suelto

    return resultado.strip() if cambios else nucleo


def quita_notas_editoriales(parrafo: str) -> str:
    """`parrafo` with every SCJN editorial insertion removed (see the
    section docstring above) — a paragraph that turns out to be *only* one
    such insertion comes back empty, rather than as a blank paragraph.

    Takes the already-bolded output of `scjn_api._formatea_parrafo` just as
    readily as a raw source paragraph — a whole-paragraph editorial insertion
    in an already-written snapshot is wrapped in its own "**...**"
    (`scjn_api._es_titular` bolds every all-caps paragraph, editorial or not),
    stripped and restored around the result so a second pass over already-clean output is a no-op,
    byte for byte. That property is what let issue #114's Paso 5 repair, in
    place, the snapshots an earlier crawl had written before this existed;
    that one-time script (`scripts/repara_notas_editoriales_scjn.py`) was
    retired in issue #129 once it ran to a no-op over the whole corpus, but
    the idempotence it relied on is still worth keeping.
    """
    negrita = parrafo.startswith("**") and parrafo.endswith("**") and len(parrafo) > 4
    nucleo = parrafo[2:-2] if negrita else parrafo
    resultado = _quita_notas_editoriales(nucleo)
    if resultado == nucleo:
        return parrafo
    if not resultado:
        return ""
    return f"**{resultado}**" if negrita else resultado


def slugify(texto: str) -> str:
    """`texto` as a filesystem-safe, ASCII, lowercase slug — the release's
    own asset-name shape."""
    return re.sub(r"[^a-z0-9]+", "-", _normaliza(texto)).strip("-")


def slug_instrumento(entrada: dict) -> str:
    """The directory and release-asset name for one catalogue entry (as
    `catalogo.json` holds it, see `scripts/extract_scjn_titles.py`): its own
    `abrev`, slugified.

    Slugifying is not a no-op: the 14 laws whose abbreviation carries an
    underscore come out hyphenated (`lif_2026` -> `lif-2026`), which is why
    this and `catalog_key` are two functions and not one — the slug is the
    release's asset name, the `abrev` is what the catalogue keeps verbatim.

    Raises `KeyError` for an entry with no `abrev`. Until issue #189 this fell
    back to the entry's `nombre`, then to a literal `"instrumento"`, because
    the tratados catalogue had no `abrev` at all; with `leyes` the only
    collection left, a missing `abrev` is a broken catalogue entry and a
    silent shared directory name would be the worst possible way to find
    that out."""
    return slugify(entrada["abrev"])


# --- issue #124 (follow-up): closing the catalogue's own coverage gaps ----
#
# Two catalogue entries the coverage sweep above never finds anything for,
# each for a different reason. `lisipl`: the catalogue's own `nombre` for it
# carries a 250+ character trailing parenthetical alternate name that the
# SCJN's full-text search never matches, even though the SCJN's own title
# for it scores 0.774 on `ratio_similitud` against that `nombre` — above
# UMBRAL_CONFIANZA_SIMILITUD — once it is actually found by a different
# search string. `lfca`: a brand-new law (DOF 2026-05-24) the SCJN has not
# indexed at all yet — confirmed live, searching its own name (or even just
# "Cine y el Audiovisual") returns nothing; not a title mismatch.
#
# search_name/merge_catalog_overrides are Mecanismo 1: an optional
# `nombre_scjn` override a human can add to one catalogue entry (applied to
# `lisipl`, not `lfca` — its gap is indexing lag, not a different title),
# which `extract_scjn_titles.py` now preserves across its own refreshes
# instead of overwriting the whole file blindly, and which
# `fetch_scjn_legislacion.py` searches with in place of `nombre`.
#
# instrument_up_to_date/iso_date_from_note are Mecanismo 2: an `actualizado`
# field (the ISO date of an instrument's own most recent reform — since
# issue #186 the newest of the SCJN's reform table and the DOF's own titles,
# `newest_dof_publication_dates`) that lets a refresh run skip
# re-searching the SCJN for an instrument nothing has changed on since the
# collection's own last full crawl. This is the safety net for a case like
# `lfca`: nothing needs to be typed by hand — a brand-new law's `actualizado`
# is newer than any previous full crawl, so it keeps getting retried on
# every refresh, automatically, until the SCJN catches up.


def search_name(entry: dict) -> str:
    """The string to actually search the SCJN with for one catalogue entry:
    its manual `nombre_scjn` override when present, the catalogue's own
    `nombre` otherwise. `nombre` itself is never touched by this — it stays what
    `enlaza_por_titulo`/`title_candidates_por_fecha` compare against DOF
    titles (issue #126), and what a caller shows in its own progress
    output; only the string handed to `buscar` changes."""
    return entry.get("nombre_scjn") or entry["nombre"]


def catalog_key(entry: dict) -> str:
    """The key two catalogue entries from separate runs are considered the
    same instrument by: its `abrev`, **verbatim** — not slugified, unlike
    `slug_instrumento`. Keeping the two apart is what lets a catalogue that
    already spells a law `lif_2026` keep spelling it that way while the
    release's asset for it is `lif-2026.tgz` (issue #186).

    Raises `KeyError` for an entry with no `abrev`, same as
    `slug_instrumento` and for the same reason (issue #189)."""
    return entry["abrev"]


def merge_catalog_overrides(catalog: list[dict], previous_catalog: list[dict] | None) -> list[dict]:
    """`catalog` (a freshly extracted catalogue) with
    each entry's own `nombre_scjn` carried over from whichever entry of
    `previous_catalog` it corresponds to (`catalog_key`), when that
    previous entry had one.

    `extract_scjn_titles.py` used to overwrite `catalogo.json` from scratch
    on every run — there was nowhere to keep a manual override, and even a
    hand-edited one would vanish on the next refresh. This is what makes it
    survive instead: a fresh run only ever adds or updates what the
    extraction itself gives (`nombre`, `abrev`, `actualizado`), and only ever
    keeps — never invents — `nombre_scjn`.

    `previous_catalog` being empty or None (first run, no `catalogo.json`
    yet) returns `catalog` unchanged."""
    if not previous_catalog:
        return catalog
    previous_by_key = {catalog_key(entry): entry for entry in previous_catalog}
    merged = []
    for entry in catalog:
        previous = previous_by_key.get(catalog_key(entry))
        if previous and previous.get("nombre_scjn"):
            entry = {**entry, "nombre_scjn": previous["nombre_scjn"]}
        merged.append(entry)
    return merged


def merge_catalog_with_previous(
    seed: list[dict], previous: list[dict] | None
) -> tuple[list[dict], list[dict]]:
    """`seed` overlaid on `previous`, sorted by `slug_instrumento`, plus the
    entries of `previous` the seed does not account for.

    The previous catalogue is the floor: those entries stay in the result and
    are returned separately so the caller can report them. An entry present
    in both keeps its previous `abrev` verbatim and takes the seed's
    `nombre`; every other previous field (`nombre_scjn`, and any hand-written
    one) is preserved."""
    por_slug = {slug_instrumento(entrada): dict(entrada) for entrada in (previous or [])}
    faltantes = dict(por_slug)

    for entrada in seed:
        slug = slug_instrumento(entrada)
        faltantes.pop(slug, None)
        anterior = por_slug.get(slug)
        if anterior is None:
            por_slug[slug] = {"nombre": entrada["nombre"], "abrev": entrada["abrev"]}
        else:
            # `nombre` is refreshed, `abrev` never is.
            anterior["nombre"] = entrada["nombre"]

    catalogo = [por_slug[slug] for slug in sorted(por_slug)]
    return catalogo, [faltantes[slug] for slug in sorted(faltantes)]


def apply_actualizado(catalogo: list[dict], *fuentes: dict[str, str]) -> list[dict]:
    """Each entry with `actualizado` set to the newest date any of `fuentes`
    (slug -> ISO date) gives for it, and **removed** when none does — an entry
    that used to carry a date and no longer can must not keep a stale one.

    The newest wins rather than the first source that answers: the SCJN's
    reform table and the DOF's own titles each miss reforms the other sees
    (see `extract_scjn_titles.py`, which measured both), and over-reporting a
    law as pending only costs a re-crawl, while under-reporting it loses the
    reform silently."""
    resultado = []
    for entrada in catalogo:
        slug = slug_instrumento(entrada)
        candidatos = [f[slug] for f in fuentes if slug in f]
        entrada = dict(entrada)
        if candidatos:
            # Assigned, not rebuilt, so an entry that already had the field
            # keeps it where it was in the file.
            entrada["actualizado"] = max(candidatos)
        else:
            entrada.pop("actualizado", None)
        resultado.append(entrada)
    return resultado


def iso_date_from_note(note: dict) -> str | None:
    """`note`'s own `fecha` (dofjson's `DD-MM-YYYY`, the shape
    `nota2md.builder.fetch_nota` returns it in) as ISO `YYYY-MM-DD`, or None
    when `note` carries no `fecha` at all."""
    fecha = note.get("fecha")
    if not fecha:
        return None
    return datetime.strptime(fecha, "%d-%m-%Y").date().isoformat()


def instrument_up_to_date(directory: Path, updated: str | None, corpus_date: str | None) -> bool:
    """Whether the instrument crawled into `directory` can be skipped on a
    refresh run without touching the SCJN at all: it already has at least
    one snapshot on disk, and its own `updated` (`catalogo.json`'s
    `actualizado`, an ISO date) is no later than `corpus_date` (the ISO
    date this collection was last crawled start-to-finish) — plain string
    comparison, since both are ISO `YYYY-MM-DD`.

    An instrument with no snapshot on disk yet is never skipped, regardless
    of `updated`/`corpus_date`: "nothing downloaded" is never mistaken for
    "already up to date" — the safety net for an instrument the SCJN has
    not indexed at all yet (see the section docstring above, `lfca`): every
    refresh keeps retrying it until the SCJN catches up, with nothing to
    configure by hand."""
    if corpus_date is None or not updated:
        return False
    if not any(directory.glob("*.md")):
        return False
    return updated <= corpus_date


# --- issue #148: per-instrument freshness, so one law can be refreshed alone -
#
# Mecanismo 2 above compares against a date that belongs to the *collection*
# (`.rastreo_completo.json`, one date for all ~315 laws, written only when a
# full sweep finishes). That is enough to skip work on a full sweep, but it
# cannot answer "this one law is up to date as of X" — which is exactly what
# updating a single law needs, both to decide whether it is pending and to
# know which release asset has to be re-uploaded.
#
# So each instrument's own directory carries an `estado.json` recording the
# `actualizado` it was actually crawled against (the catalogue's value at
# crawl time, not today's), plus when it was crawled and linked. It is a
# regular file, not a hidden checkpoint, on purpose: it ships inside the
# instrument's own `.tgz` (issue #128), so the published release carries its
# own freshness metadata and nothing has to trust local scratch to know what
# was published.

ARCHIVO_ESTADO = "estado.json"

#: Why one instrument is (or is not) pending a refresh — `motivo_pendiente`'s
#: own vocabulary, kept as constants since callers branch on it.
PENDIENTE_NUNCA_RASTREADO = "nunca_rastreado"
PENDIENTE_SIN_ACTUALIZADO = "sin_actualizado"
PENDIENTE_CAMBIO = "cambio"


def lee_estado(directory: Path) -> dict:
    """`directory`'s own `estado.json`, or `{}` when there is none — the
    first run after this mechanism was added, an instrument crawled before
    it existed, or a malformed file. Unreadable is treated the same as
    absent rather than raising: the cost of getting it wrong is one extra
    crawl of one law, and refusing to run would be worse."""
    try:
        campos = json.loads((directory / ARCHIVO_ESTADO).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return campos if isinstance(campos, dict) else {}


def escribe_estado(directory: Path, **campos) -> dict:
    """Merge `campos` into `directory`'s own `estado.json` and return the
    result. A merge, not an overwrite, because the fields have different
    owners: `fetch_scjn_legislacion.py` writes `actualizado`/`rastreado`,
    `enlaza_scjn_legislacion.py` writes `enlazado`, and neither should erase
    the other's record of what it did."""
    estado = {**lee_estado(directory), **campos}
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ARCHIVO_ESTADO).write_text(
        json.dumps(estado, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return estado


def motivo_pendiente(entry: dict, directory: Path, corpus_date: str | None) -> str | None:
    """Why `entry` needs to be crawled again, or None when it does not —
    the whole decision issue #148's planner and refresh runs share, made
    offline from `catalogo.json` plus what is on disk. Never touches the
    SCJN.

    - `PENDIENTE_NUNCA_RASTREADO`: no snapshot on disk at all. Always
      pending, whatever the dates say — the `lfca` safety net of issue #124,
      unchanged.
    - `PENDIENTE_SIN_ACTUALIZADO`: the catalogue gives no `actualizado` for
      it (3 laws today: neither the SCJN's reform table nor the DOF's
      titles date them), so whether it changed is simply unknowable. Reported as pending, but as its own
      distinct reason: a planner lists these apart and lets a human decide,
      while a full sweep keeps re-crawling them, which is what it always did.
    - `PENDIENTE_CAMBIO`: the catalogue now reports a reform newer than the
      one this instrument was last crawled against. The comparison is against
      the instrument's own `estado.json` when it has one, and only otherwise
      against the collection-wide `corpus_date` (`instrument_up_to_date`) —
      per-law state takes precedence, so a single law refreshed on its own
      counts as up to date even though no full sweep ran after it."""
    if not any(directory.glob("*.md")):
        return PENDIENTE_NUNCA_RASTREADO
    actualizado = entry.get("actualizado")
    if not actualizado:
        return PENDIENTE_SIN_ACTUALIZADO
    rastreado_contra = lee_estado(directory).get("actualizado")
    if rastreado_contra:
        return PENDIENTE_CAMBIO if actualizado > rastreado_contra else None
    return None if instrument_up_to_date(directory, actualizado, corpus_date) else PENDIENTE_CAMBIO


# --- issue #123/#126: match each snapshot to the codNota that published it,
# by title mention alone --------------------------------------------------
#
# The crawl only knows the SCJN's own view of an instrument: a
# publication date per snapshot, nothing that ties back to a DOF `codNota`.
# Issue #105's original design paired that date against the Cámara de
# Diputados' own curated `historial` (its list of codNota per instrument,
# read through a release this project no longer publishes — issues #184 and
# #187 deleted both) — but issue #123's corrected goal is for the
# SCJN, plus the DOF's own title dataset, to be the *only* source once an
# instrument's `nombre` has picked which one to crawl: `historial` is never
# consulted here, not even as a tie-breaker or a fallback. What the catalogue
# contributes is `nombre` itself, used for two things: searching
# the SCJN (`buscar`) and, here, testing which same-day DOF note's own title
# actually names the instrument. Two SCJN-dated snapshots can share a
# `fecha_publicacion` (issue #105's Fase 0 found up to 4 on the CPEUM alone),
# so this also has to resolve same-day exclusivity: once an earlier snapshot
# of that date has claimed a candidate, a later one sharing the date never
# claims it again.


def _fecha(cadena: str) -> datetime:
    return datetime.strptime(cadena, "%d-%m-%Y")


_CABECERA_CAMPO = re.compile(r"^([a-z_]+):\s*(.*)$")


def lee_cabecera(archivo: Path) -> dict:
    """The provenance header `scjn_api.cabecera` writes at the top of
    `archivo`, back
    as a dict (`fuente`, `nombre_buscado`, `ordenamiento`,
    `fecha_publicacion`, and whichever of `fecha_expedicion`/`categoria` that
    snapshot's row had) — reading back a file a previous crawl run already
    wrote, without re-fetching it. `nombre_buscado` is absent on a file a
    crawl wrote before issue #124 added it, same as `ratio_similitud`/
    `sospechoso` for issue #115 — and, since issue #132, also absent on a
    file whose `ordenamiento` was already identical to what was searched
    for, which is not a missing field but `scjn_api.cabecera` declining to write a
    redundant one."""
    texto = archivo.read_text(encoding="utf-8")
    lineas = texto.split("\n")
    campos = {}
    for linea in lineas[1:]:
        if linea.strip() == "---":
            break
        m = _CABECERA_CAMPO.match(linea)
        if m:
            campos[m.group(1)] = m.group(2)
    return campos


@dataclass
class VersionInstrumento:
    """One SCJN snapshot already on disk: its own publication date and the
    file `scjn_api.descarga_ordenamiento` wrote it to."""

    fecha_publicacion: str
    archivo: Path


def _orden_repeticion(version: "VersionInstrumento") -> int:
    """The `-N` suffix `scjn_api.descarga_ordenamiento` appends to the 2nd+
    file of a
    repeated `fecha_publicacion` (see its own docstring), as a sort key: 1
    for a plain `<fecha>.md` (no suffix), N for `<fecha>-N.md`. `fecha` is
    known already (the header, not the filename, is the source of truth),
    so only the part of the stem after it is a repetition suffix — the
    date's own dashes never get mistaken for one."""
    resto = version.archivo.stem[len(version.fecha_publicacion) :]
    return int(resto[1:]) if resto else 1


def versiones_de_directorio(outdir: Path) -> list[VersionInstrumento]:
    """Every snapshot `scjn_api.descarga_ordenamiento` has already written to
    `outdir`, oldest first — read back from each file's own header rather
    than re-crawling, so a later Fase 2 pass can run over a crawl's output
    independently of the crawl itself."""
    versiones = [
        VersionInstrumento(lee_cabecera(archivo)["fecha_publicacion"], archivo)
        for archivo in outdir.glob("*.md")
    ]
    return sorted(versiones, key=lambda v: (_fecha(v.fecha_publicacion), _orden_repeticion(v)))


_TITLE_MEANINGFUL_WORD = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]{4,}")


def meaningful_words(nombre: str) -> list[str]:
    """`nombre`'s own words worth matching on: accent-folded, lowercased, and
    only those of 4+ letters, so "LEY"/"DEL"/"DE" never count on their own.

    Split out of `_title_mentions_name` so a caller matching one stream of
    titles against *hundreds* of instrument names can normalize each name
    once instead of once per (title, name) pair — see
    `newest_dof_publication_dates`, which is otherwise a 1.2-million by 316
    product."""
    return _TITLE_MEANINGFUL_WORD.findall(_normaliza(nombre))


def _mentions_words(palabras: list[str], titulo_normalizado: str) -> bool:
    """`_title_mentions_name`'s test, with both sides already normalized."""
    if len(palabras) < 2:
        return False
    return all(palabra in titulo_normalizado for palabra in palabras)


def _title_mentions_name(nombre: str, titulo: str) -> bool:
    """Whether `titulo` (some other same-day DOF note's own title) explicitly
    names the instrument `nombre` refers to: every one of `nombre`'s own
    meaningful words (4+ letters, so "LEY"/"DEL"/"DE" never count on their
    own) must appear in `titulo`, case/accent-insensitive. Mirrors the same
    "does this title name that instrument" question that the retired
    `leyesmx.dof` asked of an entry with no decree title of its own — the
    only shape of the question the SCJN's dates-only reform table ever
    allows, see this module's issue #187 section.

    A `nombre` left with fewer than 2 meaningful words (e.g. "LEY de
    Amparo" — only "Amparo" survives the filter) never counts as mentioned,
    even when that lone word does appear: a single common legal term is too
    weak a signal on its own, and would otherwise turn this into a blanket
    keyword search matching any unrelated same-day note that happens to use
    it in passing. Those instruments simply get no candidate at all from
    `title_candidates_por_fecha` (`status="none"`) rather than an unreliable
    one."""
    return _mentions_words(meaningful_words(nombre), _normaliza(titulo))


_DECRETO_O_LEY = re.compile(r"^(?:decreto|ley)\b", re.I)


def _title_opens_with_decreto_or_ley(titulo: str) -> bool:
    """Whether `titulo`'s own first word is "DECRETO" or "LEY", case-
    insensitive — the fallback `title_candidates_por_fecha` reaches for
    when no same-day title names the instrument at all (`_title_mentions_name`
    finds nothing). A reform decree does not always spell out every law it
    amends in its own title: `ccf`'s 14-11-2025, "DECRETO por el que se
    reforman diversas disposiciones de diversos ordenamientos legales, en
    materia de homologación normativa relativa al Código Nacional de
    Procedimientos Civiles y Familiares", reforms the Código Civil Federal in
    its own artículo primero without "Código Civil Federal" ever appearing in
    the title. But every DOF publication that actually reforms or enacts
    something still opens with one of these two words, unlike an "ACUERDO"/
    "AVISO"/"RESOLUCIÓN", which never is one — so a same-day note that opens
    with neither is not worth considering even as a weak fallback."""
    return bool(_DECRETO_O_LEY.match(titulo.strip()))


def title_candidates_por_fecha(fechas, nombre: str, porf: dict) -> dict[str, list[int]]:
    """Every same-day DOF codNota whose own title explicitly names `nombre`
    (`_title_mentions_name`), grouped by `fecha_publicacion` — the primary
    source of candidate codNota this module uses for a reform's own link
    (issue #123's corrected design: Diputados' `historial` is never
    consulted, here or anywhere downstream of it). `fechas` only needs to
    cover the dates actually worth checking (typically the ones
    `versiones_de_directorio` returns for this instrument); `porf` groups by
    fecha every dofjson title record worth considering (see
    `dofjson.legal_provisions_titles`).

    When no same-day title names the instrument at all, this falls back to
    every same-day codNota whose own title opens with "DECRETO" or "LEY"
    (`_title_opens_with_decreto_or_ley`) — a reform's own title does not
    always spell out every law it amends (see that helper's own docstring
    for `ccf`'s 14-11-2025 case). The fallback only ever fills in an
    otherwise-empty pool for a date; it never adds candidates on top of an
    already-found explicit mention.

    A date absent from `porf` entirely comes back with an empty candidate
    list, same as a date where no same-day note mentions the name or opens
    with DECRETO/LEY — both read downstream as "nothing to link this date",
    not an error."""
    resultado = {}
    for fecha in dict.fromkeys(fechas):
        notas_dia = porf.get(fecha, [])
        candidatos = sorted(
            n["codNota"] for n in notas_dia if _title_mentions_name(nombre, n["titulo"])
        )
        if not candidatos:
            candidatos = sorted(
                n["codNota"] for n in notas_dia if _title_opens_with_decreto_or_ley(n["titulo"])
            )
        resultado[fecha] = candidatos
    return resultado


# --- issue #186: `actualizado`, and an `abrev` for a law nobody named yet --
#
# `actualizado` used to be the publication date of the last codNota in
# Diputados' `historial`. Its job is unchanged (`motivo_pendiente`: has this
# law changed since we crawled it?), but its source has to be something
# *outside* the crawl being scheduled. The SCJN's own reform table is the
# obvious candidate and is the wrong one to lead with: if the SCJN has not
# indexed a brand-new reform yet, an SCJN-derived `actualizado` never moves,
# the law is never reported pending, and the reform is never picked up even
# after the SCJN does index it. The DOF has the opposite failure mode — the
# date moves the day the decree is published, so the law stays pending until
# the SCJN catches up, which is exactly the `lfca` safety net issue #124
# built `instrument_up_to_date` around. So the DOF leads and the SCJN's
# reform table is the fallback (`fetch_scjn_legislacion.py --scjn`).
#
# Measured against the published corpus while this was written: the DOF date
# below for `lfca` is 2026-05-22, the same day its `estado.json` records, and
# `lft`'s is 2026-05-14. The DECRETO/LEY guard is what keeps the SCJN's
# `CODIGO`-category noise out — "CODIGO DE CONDUCTA DE LA GUARDIA NACIONAL",
# "CODIGO DE ETICA DEL BANCO DE MEXICO" and their ~180 siblings are published
# under their own name, never under a DECRETO, so all three of the ones tried
# came back with no date at all.


def newest_dof_publication_dates(instrumentos: dict[str, str], titulos) -> dict[str, str]:
    """For each ``slug -> nombre`` in `instrumentos`, the ISO date of the most
    recent DOF legal provision that both **names** it (`_title_mentions_name`)
    and **opens with "DECRETO" or "LEY"** (`_title_opens_with_decreto_or_ley`)
    — the catalogue's `actualizado`, and the confirmation step that keeps an
    SCJN catalogue entry from inventing a law (issue #186).

    `titulos` is any iterable of `dofjson.legal_provisions_titles` records
    (`titulo` plus `fecha` as `DD-MM-YYYY`), consumed exactly once. A slug for
    which nothing qualifies is **absent** from the result rather than mapped
    to None: absent is what the planner reads as "always re-check".

    Both guards are needed and neither is enough alone. Without the name
    test, every decree of the day matches; without the DECRETO/LEY test, an
    instrument's own non-legislative namesakes match — see the module
    comment above for the numbers.
    """
    palabras_por_slug = {slug: meaningful_words(nombre) for slug, nombre in instrumentos.items()}
    # A name left with fewer than two meaningful words can never be matched
    # (`_mentions_words`), so it is dropped here rather than tested 1.2
    # million times.
    palabras_por_slug = {s: p for s, p in palabras_por_slug.items() if len(p) >= 2}

    mas_reciente: dict[str, str] = {}
    for nota in titulos:
        titulo = nota["titulo"]
        if not _title_opens_with_decreto_or_ley(titulo):
            continue
        normalizado = _normaliza(titulo)
        fecha = nota.get("fecha")
        if not fecha:
            continue
        iso = f"{fecha[6:10]}-{fecha[3:5]}-{fecha[0:2]}"
        for slug, palabras in palabras_por_slug.items():
            if _mentions_words(palabras, normalizado) and iso > mas_reciente.get(slug, ""):
                mas_reciente[slug] = iso
    return mas_reciente


#: Words an `abrev` is never built out of: they are in almost every federal
#: law's name and carry no distinguishing information.
_ABREV_VACIAS = {
    "de", "del", "la", "las", "el", "los", "y", "e", "en", "para", "por",
    "sobre", "que", "al", "a", "un", "una", "su", "sus", "con",
}


def mint_abrev(nombre: str, taken=()) -> str:
    """A new law's `abrev`, minted deterministically from its `nombre`: the
    first letter of every word that is not a stop word (`_ABREV_VACIAS`),
    accent-folded and lowercased, with `-2`, `-3`, … appended until it is not
    in `taken`.

    Nothing else in the project assigns one. An `abrev` is the `scjn-leyes`
    slug and asset name, so this rule exists to be applied **once**, when a
    law first enters the catalogue, and the value is then carried verbatim
    forever: re-minting one would rename that law's release asset and orphan
    it. `extract_scjn_titles.py` never applies it on its own — it reports the
    candidate and a human writes the entry (issue #186).

    The result is already slug-safe (`slug_instrumento` is the identity on
    it), unlike the 14 historical `abrev` that carry an underscore.

    >>> mint_abrev("LEY Federal de Cine y el Audiovisual")
    'lfca'
    >>> mint_abrev("LEY Federal de Cine y el Audiovisual", taken={"lfca"})
    'lfca-2'
    """
    palabras = re.findall(r"[a-z0-9]+", _normaliza(nombre))
    base = "".join(p[0] for p in palabras if p not in _ABREV_VACIAS)
    if not base:
        # A name that is nothing but stop words is not a real law name, but
        # returning "" would collide with itself on the very next call.
        base = "ley"
    candidato = base
    sufijo = 1
    while candidato in taken:
        sufijo += 1
        candidato = f"{base}-{sufijo}"
    return candidato


@dataclass
class VersionEnlazada:
    """One SCJN snapshot together with the `codNota` of the DOF note that
    published it, when `title_candidates_por_fecha` left exactly one
    same-day candidate for its date and no earlier same-day snapshot has
    already claimed it (`enlaza_por_titulo`) — `codNota` is None otherwise
    (no candidate, more than one, or already claimed), left for issue #127's
    content diff to resolve when it can."""

    fecha_publicacion: str
    codNota: int | None
    archivo: Path


def enlaza_por_titulo(
    versiones: list[VersionInstrumento], candidatos_por_fecha: dict[str, list[int]]
) -> list[VersionEnlazada]:
    """Pair every SCJN snapshot of one instrument with the codNota of the DOF
    note that published it, using only `candidatos_por_fecha`
    (`title_candidates_por_fecha`'s own output) — Diputados' historial is
    never consulted, here or anywhere in this module.

    A date whose candidate pool (after excluding any codNota already claimed
    by an earlier same-day snapshot) is empty or has more than one entry
    comes back with `codNota=None`: title mention alone cannot pick a winner
    without risking a wrong pick, and a missing link is a fact about the
    source worth surfacing, not an error.

    When two snapshots share a date and its pool has exactly one candidate
    (issue #105's Fase 0 found up to 4 same-day reforms on the CPEUM), only
    the first — `versiones` is already oldest-first — claims it; the second
    sees an empty remaining pool and stays unlinked, the same same-date
    exclusivity `confirm_by_content_diff` (#127) already enforces on its own
    `usados_por_fecha`."""
    usados: set[int] = set()
    enlazadas = []
    for version in versiones:
        candidatos = [
            cod
            for cod in candidatos_por_fecha.get(version.fecha_publicacion, [])
            if cod not in usados
        ]
        cod = candidatos[0] if len(candidatos) == 1 else None
        if cod is not None:
            usados.add(cod)
        enlazadas.append(VersionEnlazada(version.fecha_publicacion, cod, version.archivo))
    return enlazadas


def title_link_status(codNota: int | None, candidatos: list[int]) -> str:
    """How `enlaza_por_titulo` resolved one snapshot's own date, for
    `indice.json`/manual audit:

    - "linked": it has a `codNota`.
    - "none": no same-day candidate names the instrument at all.
    - "claimed": the date's one candidate was already taken by an earlier
      same-day snapshot.
    - "ambiguous": more than one same-day candidate names the instrument,
      with nothing but content diff (#127) able to break the tie.
    """
    if codNota is not None:
        return "linked"
    if not candidatos:
        return "none"
    if len(candidatos) == 1:
        return "claimed"
    return "ambiguous"


# --- issue #127: confirm the reform-codNota link by content diff ----------
#
# #126's title mention is still just text similarity: it says a same-day
# note plausibly concerns this instrument, not that its content is what
# actually changed. The SCJN gives, at each date, the *whole* law's text
# (never a diff) — so the change a reform made between two consecutive
# SCJN-dated snapshots has to be computed here, and then checked against
# each candidate codNota's own DOF text: whichever candidate's own content
# actually accounts for that change is confirmed with certainty, resolving
# the ambiguous, same-day-multiple-candidate cases #126 alone cannot. This
# is only possible for a codNota with digital DOF text (`cadenaContenido`)
# — never a reason to fall back to OCR just for one more confidence signal
# — and the diff itself is never kept: only whether a candidate was
# confirmed this way, alongside the score that decided it.

# A reform that only changes a short number or date (a tasa, monto, plazo
# or fecha — common in Mexican decrees) leaves a diff whose only real
# content is a token shorter than 4 letters; requiring 4+ letters alone
# would make that change invisible to `_overlap_score`, letting two
# candidates that differ only in which number they restate score
# identically. Digits are matched at any length (first alternative) so
# "10" vs "20" still tells two otherwise-similar candidates apart.
_PALABRA_DIFF = re.compile(r"\d+(?:[.,]\d+)?|\w{4,}")


def _cuerpo_de_snapshot(archivo: Path) -> str:
    """`archivo`'s own body, with the provenance header `scjn_api.cabecera`
    wrote at
    its top stripped off — header and body are separated by the first blank
    line, so a diff between two snapshots never mistakes a header field
    (e.g. a different
    `ratio_similitud`) for a change in the law's own text."""
    return archivo.read_text(encoding="utf-8").partition("\n\n")[2]


def _added_blocks(anterior: str, nuevo: str) -> list[str]:
    """`nuevo`'s own paragraph-level blocks (already run through
    `normaliza_para_comparar`) that are not already in `anterior` — a
    block-granularity diff via `SequenceMatcher.get_opcodes`, so a
    paragraph that merely got reflowed (same words, different line breaks)
    is not mistaken for a change. This approximates what a reform between
    two consecutive SCJN-dated snapshots actually changed in the law's full
    text; it is never persisted, only used to score candidates below."""
    bloques_antes = [normaliza_para_comparar(b) for b in anterior.split("\n\n") if b.strip()]
    bloques_despues = [normaliza_para_comparar(b) for b in nuevo.split("\n\n") if b.strip()]
    sm = SequenceMatcher(None, bloques_antes, bloques_despues, autojunk=False)
    agregados = []
    for tag, _, _, j1, j2 in sm.get_opcodes():
        if tag in ("insert", "replace"):
            agregados.extend(bloques_despues[j1:j2])
    return agregados


def _overlap_score(bloques_agregados: list[str], texto_candidato_normalizado: str) -> float:
    """How much of what `_added_blocks` says changed also shows up in
    `texto_candidato_normalizado` (a candidate codNota's own DOF text,
    already run through `normaliza_para_comparar`): the fraction of the
    added blocks' own meaningful words (4+ letters) that also appear in
    it. 1.0 when everything that changed is accounted for by this one
    candidate's own text; 0.0 when nothing changed to compare against, or
    when the candidate's text shares none of it."""
    palabras_agregadas = set(_PALABRA_DIFF.findall(" ".join(bloques_agregados)))
    if not palabras_agregadas:
        return 0.0
    palabras_candidato = set(_PALABRA_DIFF.findall(texto_candidato_normalizado))
    return len(palabras_agregadas & palabras_candidato) / len(palabras_agregadas)


#: Below this fraction of the diff's own words found in a candidate's text,
#: the match is treated as coincidental rather than confirmed — kept
#: deliberately higher than #126's title-mention bar (which only needs to
#: single out one plausible candidate) since this signal is meant to give
#: *certainty*, not just plausibility.
UMBRAL_CONFIRMACION_DIFF = 0.6


@dataclass
class ContentDiffConfirmation:
    """One SCJN-dated snapshot's content-diff confirmation (issue #127).

    `confirmed_codNota` is the candidate whose own DOF text best accounts
    for what changed between this snapshot and the previous one, when its
    score clears `UMBRAL_CONFIRMACION_DIFF`; None otherwise — including
    when this is the instrument's very first (oldest) snapshot, which has
    no previous version to diff against at all.

    `score` is that best candidate's own overlap score even when it did
    NOT clear the threshold (so a near-miss is visible, not indistinguishable
    from "nothing to compare"), and is None only when no candidate for this
    date had any DOF text available to compare in the first place — the
    case issue #127 says must leave the link exactly as #124/#126 already
    settled it, neither blocked nor degraded.
    """

    fecha_publicacion: str
    confirmed_codNota: int | None
    score: float | None


def confirm_by_content_diff(
    versiones: list[VersionInstrumento],
    candidatos_por_fecha: dict[str, list[int]],
    markdown_por_codNota: dict[int, str],
) -> list[ContentDiffConfirmation]:
    """One `ContentDiffConfirmation` per entry of `versiones` (oldest first,
    as `versiones_de_directorio` already returns them) — always the same
    length, so a caller can `zip` this against `versiones`/`enlaza_por_titulo`'s
    own per-version results.

    `candidatos_por_fecha` is every codNota worth checking for a given
    `fecha_publicacion` — typically `title_candidates_por_fecha`'s own
    output (issue #126), the same dict `enlaza_por_titulo` itself takes.
    `markdown_por_codNota` is each of those candidates' own DOF Markdown,
    already fetched by the caller (never fetched here — this module does no
    network I/O) and present only for candidates that actually have digital
    text; a candidate absent from it is simply skipped, not treated as
    disqualifying.

    Two snapshots can share a `fecha_publicacion` (up to 4 on the CPEUM
    alone, per `enlaza_por_titulo`'s own docstring) — a codNota already
    confirmed for an earlier same-day entry is excluded from every later
    one sharing that date, the same one-codNota-per-snapshot exclusivity
    `enlaza_por_titulo` itself already enforces via its own `usados` set.
    Without this, two distinct same-day reforms with genuinely overlapping
    decree text (confirmed live on `ccf`'s 27-12-1983, two companion
    decrees both amending the Código Civil and Código de Procedimientos
    Civiles) can otherwise both score highest against the same one
    candidate, silently claiming it twice and discarding the other
    snapshot's own already-correct title-based link for no reason.
    """
    if not versiones:
        return []
    resultados = [ContentDiffConfirmation(versiones[0].fecha_publicacion, None, None)]
    usados_por_fecha: dict[str, set[int]] = {}
    for anterior, actual in zip(versiones, versiones[1:]):
        agregados = _added_blocks(
            _cuerpo_de_snapshot(anterior.archivo), _cuerpo_de_snapshot(actual.archivo)
        )
        usados = usados_por_fecha.setdefault(actual.fecha_publicacion, set())
        candidatos = candidatos_por_fecha.get(actual.fecha_publicacion, [])
        mejor_cod, mejor_score = None, 0.0
        tiene_texto = False
        for cod in candidatos:
            if cod in usados:
                continue
            texto = markdown_por_codNota.get(cod)
            if texto is None:
                continue
            tiene_texto = True
            score = _overlap_score(agregados, normaliza_para_comparar(texto))
            if score > mejor_score:
                mejor_cod, mejor_score = cod, score
        confirmado = mejor_cod if mejor_score >= UMBRAL_CONFIRMACION_DIFF else None
        if confirmado is not None:
            usados.add(confirmado)
        resultados.append(
            ContentDiffConfirmation(
                actual.fecha_publicacion, confirmado, mejor_score if tiene_texto else None
            )
        )
    return resultados


# --- issue #187: the law's reform history is the corpus itself -----------
#
# With the Cámara de Diputados gone (#184), the "which decree reformed which
# law" relation is no longer a curated list this project downloads: it *is*
# each law's own `indice.json` in the `scjn-leyes` release, one entry per
# reform, oldest first, plus `indice-global.json.gz` inverting it by codNota.
# Two consequences the rest of the project depends on:
#
# **"Reform N" is redefined, once and loudly.** It is now the position of the
# entry in the law's own `indice.json` — that is, the chronological order of
# the SCJN's own reform table (`scjn_api.reformas_of_ordenamiento`, newest
# first, reversed to oldest-first by `versiones_de_directorio`). It is *not*
# Diputados' numbering and is not measured against it: Diputados filed errata,
# peso restatements, SCJN rulings and entry-into-force declarations in the
# same numbered column, so the two count different things and any claim that
# they agree would be unverifiable now that the source is gone. Code that used
# to say "reform 139 of the Constitution" and mean Diputados' 139 means the
# SCJN's 139th row today.
#
# **The metric is picked by what the source gives, not by preference.** All of
# this module's linking is *name*-based (`_title_mentions_name`), including
# for a law's original publication, and that is deliberate rather than
# incidental. The retired `leyesmx.dof` scored a numbered reform by whether
# the DOF title was *contained* in the decree title Diputados supplied — fine
# when a decree title exists, and wrong when it does not. Applying containment
# where only the instrument's name is available linked the Ley Federal del
# Trabajo's 1970 publication to a Mexico City traffic-regulation decree, and
# the Código Fiscal's to the 1982 budget. The SCJN supplies no decree title at
# all, only dates, so the name is all there ever is here and containment never
# becomes available to reach for.

#: `title_link_status` for a snapshot linked by content diff rather than by
#: title (issue #187). Kept distinct from "linked" so the release's own index
#: says which of the two signals decided a link — they are not equally
#: verifiable, and a human auditing the corpus should not have to guess.
ESTADO_ENLACE_CONTENT_DIFF = "content_diff"


def resolve_links(
    enlazadas: list[VersionEnlazada],
    confirmaciones: list[ContentDiffConfirmation],
    candidatos_por_fecha: dict[str, list[int]],
) -> list[tuple[int | None, str]]:
    """One ``(codNota, title_link_status)`` per snapshot: the title link when
    `enlaza_por_titulo` found one, and otherwise the content-diff
    confirmation, promoted to *be* the link instead of only annotating it.

    Until issue #187 a date where several same-day notes named the law came
    back `ambiguous` with `codNota=None` even when `confirm_by_content_diff`
    had already singled one candidate out — and the release's index drops
    every entry with no `codNota`, so that answer was computed, written to
    `indice.json`, and then thrown away. Replayed over the crawled corpus
    (3,707 snapshots, 1,166 of them `ambiguous`), promoting it links **834
    more snapshots across 188 laws** — the collection goes from 2,457 linked
    to 3,291, 66% to 89% — each one already carrying a candidate whose own
    DOF text accounts for at least `UMBRAL_CONFIRMACION_DIFF` of what
    actually changed in the law. Nothing is ever un-linked by this.

    Promoting it does not weaken the "an absent link is worth more than a
    wrong one" rule that issue #115's five wrong-document matches bought:

    - the candidate had to name the law in its own title in the first place
      (it comes from `title_candidates_por_fecha`), so this only ever picks
      *among* the candidates title matching already accepted;
    - it had to clear `UMBRAL_CONFIRMACION_DIFF`, a bar deliberately set
      higher than the title-mention bar because it is meant to give
      certainty rather than plausibility;
    - a codNota already claimed by a title link, anywhere in this
      instrument, is never promoted onto a second snapshot — the same
      one-codNota-per-snapshot exclusivity the two mechanisms each already
      enforce internally, extended across them.

    In practice only an `ambiguous` date can be promoted: `none` has no
    candidates for the diff to score, and `claimed` had its one candidate
    excluded from the diff too.
    """
    usados = {e.codNota for e in enlazadas if e.codNota is not None}
    resuelto: list[tuple[int | None, str]] = []
    for enlazada, confirmacion in zip(enlazadas, confirmaciones):
        if enlazada.codNota is not None:
            resuelto.append((enlazada.codNota, "linked"))
            continue
        confirmado = confirmacion.confirmed_codNota
        if confirmado is not None and confirmado not in usados:
            usados.add(confirmado)
            resuelto.append((confirmado, ESTADO_ENLACE_CONTENT_DIFF))
            continue
        candidatos = candidatos_por_fecha.get(enlazada.fecha_publicacion, [])
        resuelto.append((None, title_link_status(None, candidatos)))
    return resuelto


# --- issues #128/#117: read the packaged corpus (release loaders) --------
#
# `scripts/empaqueta_scjn_leyes.py` packages every already-crawled+linked
# `leyes` instrument (snapshots plus `indice.json`, carrying #115/#126/#127's
# confidence signals, plus the DOF notes each link was decided against) into
# one `<slug>.tgz` asset per law of the `scjn-leyes` release — see
# that script for why publishing it is, and stays, a deliberate manual step,
# never automated. These are that release's own readers, same shape as
# the reader of the retired `historial-legislativo` release, but deliberately
# never shared code with it: the two releases had different tags, different
# asset layouts and no caller in common. That reader is gone (#187); these
# are what a law's reform history is read through now.
#
# Three of them, in the order a caller reaches for them:
# `download_scjn_leyes_index` (the reverse index, a few hundred KB),
# `snapshot_de_codNota` (one reform's consolidated law text, what
# `legal_provisions` dispatches to) and `download_scjn_leyes_corpus` (a whole
# law, snapshots and links, for auditing the corpus itself). All three read
# through `nota2md.cache` -- on-disk by default, straight into memory with
# `cache_dir=None`.

_SCJN_LEYES_RELEASE = "scjn-leyes"
_SCJN_LEYES_RELEASES_API = (
    f"https://api.github.com/repos/INGEOTEC/LegalIA/releases/tags/{_SCJN_LEYES_RELEASE}"
)

#: The reverse index published alongside the per-law tarballs: the union of
#: every `indice.json`, inverted by codNota and stripped of all text, so
#: resolving "which law does this codNota reform" costs a few hundred KB
#: instead of the 380 MB the whole corpus weighs.
ASSET_INDICE_GLOBAL = "indice-global.json.gz"


def construye_indice_global(instrumentos: list[dict], generado: str) -> tuple[dict, dict]:
    """The `indice-global.json.gz` payload for `instrumentos`, plus the counts
    the packaging manifest reports — see `ASSET_INDICE_GLOBAL`.

    Each entry of `instrumentos` is ``{"slug", "nombre", "asset", "indice"}``,
    where `indice` is that law's own `indice.json` (empty/absent for a law
    crawled but never linked). The result is::

        {"generado", "coleccion",
         "instrumentos": {slug: {"nombre", "asset", "snapshots"}},
         "codNota": {"4967917": [{"slug", "archivo", "title_link_status",
                                  "content_diff_confirmed_codNota",
                                  "content_diff_score"}]}}

    Two shapes here are deliberate, not incidental:

    * The `codNota` keys are **strings** — JSON has no integer keys. Readers
      (`download_scjn_leyes_index`) convert them back to `int` on load.
    * Each value is a **list**, not a single object: one decree routinely
      reforms several laws at once, and collapsing that into a dict would
      silently keep whichever law happened to be packaged last. Leaving it a
      list is what lets `snapshot_de_codNota` raise on the ambiguity instead
      of guessing (issue #117, D4).

    Only snapshots with a `codNota` actually linked make it in (D2): the index
    is the list of what we *know*, so an `ambiguous` or `unlinked` snapshot is
    counted in the returned tally and left out of the payload.

    `coleccion` is written as the literal `"leyes"`. It stopped being a
    parameter in issue #189, when `leyes` became the project's only
    collection, but stays in the payload: it is a field of a *published*
    asset that readers can already see, and dropping it would change the
    release format for no gain (`tests/test_scjn_release_red.py` asserts the
    published index still has it).
    """
    entradas_instrumentos: dict[str, dict] = {}
    por_cod_nota: dict[str, list[dict]] = {}
    conteos = {"linked": 0, "ambiguous": 0, "unlinked": 0, "sin_indice": 0}

    for instrumento in sorted(instrumentos, key=lambda i: i["slug"]):
        slug = instrumento["slug"]
        indice = instrumento.get("indice") or []
        entradas_instrumentos[slug] = {
            "nombre": instrumento["nombre"],
            "asset": instrumento.get("asset") or f"{slug}.tgz",
            "snapshots": len(indice) or instrumento.get("snapshots", 0),
        }
        if not indice:
            conteos["sin_indice"] += 1
            continue

        for entrada in indice:
            cod = entrada.get("codNota")
            if cod is None:
                # `title_link_status` says *why* it is not linked when
                # enlaza_scjn_legislacion.py got that far; a snapshot from
                # before that field existed is simply unlinked.
                estado = entrada.get("title_link_status", "unlinked")
                conteos[estado] = conteos.get(estado, 0) + 1
                continue
            conteos["linked"] += 1
            por_cod_nota.setdefault(str(cod), []).append({
                "slug": slug,
                "archivo": entrada["archivo"],
                "title_link_status": entrada.get("title_link_status"),
                "content_diff_confirmed_codNota": entrada.get(
                    "content_diff_confirmed_codNota"
                ),
                "content_diff_score": entrada.get("content_diff_score"),
            })

    indice_global = {
        "generado": generado,
        "coleccion": "leyes",
        "instrumentos": entradas_instrumentos,
        "codNota": {cod: por_cod_nota[cod] for cod in sorted(por_cod_nota, key=int)},
    }
    return indice_global, conteos


def _assets_scjn_leyes(timeout: int = 30) -> dict[str, str]:
    """Every asset of the `scjn-leyes` release, name -> download URL."""
    response = requests.get(_SCJN_LEYES_RELEASES_API, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    return {
        asset["name"]: asset["browser_download_url"] for asset in response.json()["assets"]
    }


def _url_de_asset(nombre: str, timeout: int) -> str:
    """The download URL of `nombre` in the `scjn-leyes` release.

    Raises `KeyError` while the release does not publish that asset yet —
    expected before a human has read `scripts/empaqueta_scjn_leyes.py`'s own
    manifest and published it by hand (this corpus has no automated publish
    path, on purpose — see that script)."""
    urls = _assets_scjn_leyes(timeout)
    if nombre not in urls:
        raise KeyError(
            f"el release '{_SCJN_LEYES_RELEASE}' no publica el asset '{nombre}' "
            "todavia — ver issue #128: este corpus solo se publica a mano, tras "
            "revision humana"
        )
    return urls[nombre]


def _bytes_de_asset(nombre: str, cache_dir, refrescar: bool, timeout: int) -> bytes:
    """`nombre`'s bytes, off the on-disk cache when there is one (see
    `nota2md.cache`) and straight into memory when there is not.

    The release index is only consulted when the asset is not already
    cached: a cache hit costs no HTTP request at all, which is the whole
    point of caching a corpus that is only ever republished by hand."""
    directorio = resuelve_cache_dir(cache_dir)
    if directorio is not None:
        ruta = directorio / _SCJN_LEYES_RELEASE / nombre
        if ruta.exists() and not refrescar:
            return ruta.read_bytes()
    return bytes_de_asset(
        _SCJN_LEYES_RELEASE, nombre, _url_de_asset(nombre, timeout),
        cache_dir=cache_dir, refrescar=refrescar, timeout=timeout,
    )


#: `download_scjn_leyes_index`'s in-process memo, keyed by which cache
#: directory it was read through ("" for no cache). A batch of thousands of
#: `legal_provisions` calls must not re-read (let alone re-download and
#: re-decompress) the same index once per note.
_MEMO_INDICE_GLOBAL: dict[str, dict] = {}


def download_scjn_leyes_index(
    *, cache_dir=SIN_CACHE_DIR, refrescar: bool = False, timeout: int = 60
) -> dict:
    """The `scjn-leyes` release's reverse index (`ASSET_INDICE_GLOBAL`), as the
    dict `construye_indice_global` wrote — except that its `codNota` keys come
    back as `int`, not the strings JSON forced them into.

    Memoized per cache directory for the life of the process, so resolving a
    whole batch of notes reads the file once. `refrescar=True` bypasses both
    the memo and the on-disk cache and re-downloads.

    Raises `KeyError` while the asset is not published yet (see
    `_url_de_asset`); `legal_provisions` treats that as "no coverage" rather
    than letting it propagate.
    """
    directorio = resuelve_cache_dir(cache_dir)
    clave = str(directorio) if directorio is not None else ""
    if not refrescar and clave in _MEMO_INDICE_GLOBAL:
        return _MEMO_INDICE_GLOBAL[clave]

    contenido = _bytes_de_asset(ASSET_INDICE_GLOBAL, cache_dir, refrescar, timeout)
    indice = json.loads(gzip.decompress(contenido).decode("utf-8"))
    indice["codNota"] = {int(cod): entradas for cod, entradas in indice["codNota"].items()}
    _MEMO_INDICE_GLOBAL[clave] = indice
    return indice


def localiza_codNota(
    cod_nota: int,
    *,
    instrumento: str | None = None,
    cache_dir=SIN_CACHE_DIR,
    refrescar: bool = False,
    timeout: int = 60,
) -> tuple[str, str] | None:
    """Which snapshot of which law the reform `cod_nota` enacted produced, as
    ``(slug, archivo)`` — or None when the release's reverse index has no
    entry for it.

    The reverse-index half of `snapshot_de_codNota`, split out because it
    answers *where* the text is without reading the law's tarball at all: a
    caller that already has that snapshot materialized (see
    `legal_provisions` with no `outdir`) needs the name and nothing else.

    Raises `ValueError` for an ambiguous `cod_nota` exactly as
    `snapshot_de_codNota` does — see its docstring.
    """
    indice = download_scjn_leyes_index(
        cache_dir=cache_dir, refrescar=refrescar, timeout=timeout
    )
    candidatos = indice["codNota"].get(int(cod_nota), [])

    if instrumento is not None:
        candidatos = [c for c in candidatos if c["slug"] == instrumento]
        if not candidatos:
            raise ValueError(
                f"el codNota {cod_nota} no reforma el instrumento {instrumento!r} "
                "segun el indice del release 'scjn-leyes'"
            )
    if not candidatos:
        return None
    if len(candidatos) > 1:
        nombres = ", ".join(
            f"{c['slug']} ({indice['instrumentos'].get(c['slug'], {}).get('nombre', '?')})"
            for c in candidatos
        )
        raise ValueError(
            f"el codNota {cod_nota} reforma mas de un instrumento: {nombres}. "
            "Pasa instrumento=<slug> para elegir uno"
        )

    candidato = candidatos[0]
    return candidato["slug"], candidato["archivo"]


def markdown_de_snapshot(
    slug: str,
    archivo: str,
    *,
    cache_dir=SIN_CACHE_DIR,
    refrescar: bool = False,
    timeout: int = 60,
) -> str:
    """The text of one snapshot, read out of its law's `<slug>.tgz` asset —
    the tarball half of `snapshot_de_codNota`, for a caller that already
    resolved `(slug, archivo)` with `localiza_codNota`."""
    contenido = _bytes_de_asset(f"{slug}.tgz", cache_dir, refrescar, timeout)
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        # Every member is prefixed with `<slug>/` so the tarball unpacks
        # anywhere -- see scripts/empaqueta_scjn_leyes.py.
        miembro = tar.extractfile(tar.getmember(f"{slug}/{archivo}"))
        return miembro.read().decode("utf-8")


def snapshot_de_codNota(
    cod_nota: int,
    *,
    instrumento: str | None = None,
    cache_dir=SIN_CACHE_DIR,
    refrescar: bool = False,
    timeout: int = 60,
) -> tuple[str, str, str] | None:
    """The consolidated law text the SCJN holds for the reform `cod_nota`
    enacted, as ``(slug, archivo, markdown)`` — or None when the release's
    reverse index has no entry for it.

    `archivo` is the snapshot's own file name inside the tarball
    (``DD-MM-YYYY.md``, with issue #113's `-N` suffix when a law was reformed
    more than once on the same date); `markdown` is that file's text, its
    `fuente: scjn` provenance header included.

    None here is not an error, just "not covered": only snapshots with a
    `codNota` we are actually certain of are in the index at all (issue #117,
    D2), and the caller is expected to fall back to the DOF — which is what
    `legal_provisions` does.

    A `cod_nota` whose decree reformed several laws at once has several
    entries. Pass `instrumento` (a slug) to say which one is wanted;
    without it, that raises `ValueError` listing the candidates rather than
    silently returning one of them (D4).
    """
    ubicacion = localiza_codNota(
        cod_nota, instrumento=instrumento, cache_dir=cache_dir,
        refrescar=refrescar, timeout=timeout,
    )
    if ubicacion is None:
        return None
    slug, archivo = ubicacion
    markdown = markdown_de_snapshot(
        slug, archivo, cache_dir=cache_dir, refrescar=refrescar, timeout=timeout
    )
    return slug, archivo, markdown


def download_scjn_leyes_corpus(
    slug: str, timeout: int = 60, *, cache_dir=SIN_CACHE_DIR, refrescar: bool = False
) -> dict:
    """One `leyes` instrument of the SCJN-based corpus (issue #128), by its
    own `slug`, as ``{"slug": ..., "snapshots": [...]}`` — one entry per
    snapshot, each carrying its own `indice.json` fields (`fecha_publicacion`,
    `codNota`, `ratio_similitud`, `sospechoso`, `title_candidates`,
    `title_link_status`, `content_diff_confirmed_codNota`,
    `content_diff_score`) plus its own Markdown body as `markdown` and, as
    `notas`, the DOF text of every candidate that was considered for it
    (``{codNota: markdown}``) — so the link can be audited without going
    back to the network.

    An instrument crawled but never linked (`scripts/enlaza_scjn_legislacion.py`
    has not run for it yet — Fase 2 pendiente) is still packaged with its raw
    snapshots; each of those comes back with only `archivo`/`codNota=None`/
    `markdown`/`notas={}` set, no confidence fields, rather than being dropped.

    Reads only that law's own `<slug>.tgz` asset (the release has one per
    law, not one for the whole collection — bringing down 380 MB to read a
    single law would be absurd), off the on-disk cache when there is one and
    straight into memory when `cache_dir=None` says there is not — see
    `nota2md.cache` for how `cache_dir`/`refrescar` resolve. Raises
    `KeyError` while the `scjn-leyes` release does not publish that asset
    yet — expected before a human has read
    `scripts/empaqueta_scjn_leyes.py`'s own manifest and published it by
    hand (this corpus has no automated publish path, on purpose — see that
    script).
    """
    contenido = _bytes_de_asset(f"{slug}.tgz", cache_dir, refrescar, timeout)

    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        miembros = {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}

    indice = None
    cuerpos: dict[str, str] = {}
    notas: dict[int, str] = {}
    for nombre, contenido in miembros.items():
        # Every member is prefixed with `<slug>/` so the tarball unpacks
        # anywhere; the prefix carries no information the caller needs.
        _, _, relativo = nombre.partition("/")
        if relativo == "indice.json":
            indice = json.loads(contenido)
        elif relativo.startswith("notas/"):
            cod = relativo[len("notas/nota-"):].removesuffix(".md")
            notas[int(cod)] = contenido.decode("utf-8")
        else:
            cuerpos[relativo] = contenido.decode("utf-8")

    if indice is not None:
        snapshots = [
            {
                **entrada,
                "markdown": cuerpos.get(entrada["archivo"]),
                "notas": {
                    cod: notas[cod]
                    for cod in entrada.get("title_candidates", [])
                    if cod in notas
                },
            }
            for entrada in indice
        ]
    else:
        snapshots = [
            {"archivo": nombre, "codNota": None, "markdown": texto, "notas": {}}
            for nombre, texto in sorted(cuerpos.items())
        ]
    return {"slug": slug, "snapshots": snapshots}


def iter_current_federal_laws(
    slugs: list[str] | None = None,
    *,
    cache_dir=SIN_CACHE_DIR,
    refrescar: bool = False,
    timeout: int = 60,
) -> Iterator[dict]:
    """The current text of every federal law the `scjn-leyes` release
    publishes, one ``{"slug", "nombre", "fecha_publicacion", "codNota",
    "archivo", "markdown"}`` dict per law -- "current" meaning the snapshot
    with the newest `fecha_publicacion` in that law's own `indice.json`.

    A true generator: one `<slug>.tgz` asset is opened, its winning snapshot
    read, and the tarball's bytes dropped before the next `slug` is reached,
    so iterating the whole corpus (currently ~315 laws, 380 MB uncompressed)
    never holds more than one law in memory at a time.
    `download_scjn_leyes_corpus` would not do here -- it decodes every
    snapshot and every `notas/` entry of a law just to keep the one that
    turns out to be the newest.

    `slugs=None` (the default) walks every law the release currently
    publishes a tarball for (`scjn_leyes_slugs`), not the keys of
    `indice-global.json.gz`'s `instrumentos`: a law that has been crawled but
    not linked yet has a `.tgz` and no entry there (same reasoning as
    `scjn_leyes_slugs` itself). `nombre` still comes from `instrumentos`,
    which `construye_indice_global` populates for every crawled law
    regardless of whether it has an `indice.json`.

    A law never linked (`enlaza_scjn_legislacion.py` has not run for it yet)
    has no `indice.json` at all: the winner is then the raw snapshot whose
    file name -- `DD-MM-YYYY.md`, or `DD-MM-YYYY-N.md` for a same-day repeat
    (`scjn_api.descarga_ordenamiento`'s `-N` suffix) -- carries the newest
    date, and `codNota` comes back `None`.

    `fecha_publicacion`, both in `indice.json` and in a raw snapshot's own
    file name, is `DD-MM-YYYY` (`scjn_api._fecha`'s format), not ISO --
    comparing it lexicographically would rank `"05-01-1999"` ahead of
    `"22-05-1998"`. The winner is chosen by parsing the date with `_fecha`;
    `archivo` breaks a tie between two snapshots published the same day, only
    to make the pick deterministic, not because either ordering is more
    correct.

    Raises `KeyError` for a `slug` the release does not publish a tarball
    for, exactly as `download_scjn_leyes_corpus` does (`_bytes_de_asset`).
    """
    if slugs is None:
        slugs = scjn_leyes_slugs(timeout)
    instrumentos = download_scjn_leyes_index(
        cache_dir=cache_dir, refrescar=refrescar, timeout=timeout
    )["instrumentos"]

    for slug in slugs:
        contenido = _bytes_de_asset(f"{slug}.tgz", cache_dir, refrescar, timeout)
        with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
            try:
                miembro_indice = tar.getmember(f"{slug}/indice.json")
            except KeyError:
                miembro_indice = None

            if miembro_indice is not None:
                indice = json.loads(tar.extractfile(miembro_indice).read())
                ganador = max(
                    indice, key=lambda e: (_fecha(e["fecha_publicacion"]), e["archivo"])
                )
                cod_nota = ganador.get("codNota")
                fecha_publicacion = ganador["fecha_publicacion"]
                archivo = ganador["archivo"]
            else:
                # Never linked: no indice.json, so nothing but the raw
                # snapshots' own file names says which is newest. estado.json
                # and notas/ are shipped alongside them but are not snapshots.
                candidatos = [
                    relativo
                    for m in tar.getmembers()
                    if m.isfile()
                    for relativo in (m.name.partition("/")[2],)
                    if relativo not in ("indice.json", ARCHIVO_ESTADO)
                    and not relativo.startswith("notas/")
                ]
                archivo = max(candidatos, key=lambda nombre: (_fecha(nombre[:10]), nombre))
                cod_nota = None
                fecha_publicacion = archivo[:10]

            miembro_md = tar.getmember(f"{slug}/{archivo}")
            markdown = tar.extractfile(miembro_md).read().decode("utf-8")

        yield {
            "slug": slug,
            "nombre": instrumentos.get(slug, {}).get("nombre"),
            "fecha_publicacion": fecha_publicacion,
            "codNota": cod_nota,
            "archivo": archivo,
            "markdown": markdown,
        }


def _estado_de_asset(slug: str, cache_dir, refrescar: bool, timeout: int) -> dict:
    """One law's own `estado.json` as the release ships it inside `<slug>.tgz`,
    or `{}` when that tarball carries none (a law packaged before issue #148
    added the file).

    Only that one member is read out of the tarball; the snapshots and the
    `notas/` are never decoded. A whole law's Markdown weighs orders of
    magnitude more than the four fields wanted here, and the catalogue reader
    does this once per law."""
    contenido = _bytes_de_asset(f"{slug}.tgz", cache_dir, refrescar, timeout)
    with tarfile.open(fileobj=io.BytesIO(contenido), mode="r:gz") as tar:
        for miembro in tar:
            # Every member is prefixed with `<slug>/` so the tarball unpacks
            # anywhere — the same convention `download_scjn_leyes_corpus` strips.
            if miembro.isfile() and miembro.name.partition("/")[2] == ARCHIVO_ESTADO:
                return json.loads(tar.extractfile(miembro).read())
    return {}


def download_scjn_leyes_catalog(
    *,
    freshness: bool = True,
    cache_dir=SIN_CACHE_DIR,
    refrescar: bool = False,
    timeout: int = 60,
) -> list[dict]:
    """The federal-law catalogue as the `scjn-leyes` release already publishes
    it: one ``{"abrev", "nombre", "actualizado"}`` dict per law, sorted by
    `abrev`.

    This is the seed the Cámara de Diputados used to be scraped for — which
    laws exist, their name, their abbreviation — read back out of the release
    rather than rebuilt (issue #184). `nombre` and `abrev` come from
    `indice-global.json.gz`'s `instrumentos`, whose slug *is* the `abrev`
    (`slug_instrumento`); `actualizado` comes from each law's own
    `estado.json`, which records the date its last reform carried when it was
    crawled (issue #148).

    `actualizado` is **absent** — not None, not a placeholder — for a law whose
    `estado.json` has none (3 laws today: `lcmopfih`, `lfcpq`, `lisipl`).
    Absent means "freshness unknown, always review", which is what
    `motivo_pendiente` already does with a catalogue entry that has no
    `actualizado`.

    One caveat this reader cannot paper over, and which matters to whoever
    rebuilds `catalogo.json`: the slug is `slug_instrumento`'s *normalized*
    `abrev`, so the 14 laws whose historical `abrev` contains an underscore
    (`lif_2026`, `pef_2026`, `ligie_2022`, the `lrart*`/`lrf*` reglamentarias,
    `reg_diputados`, `reg_senado`) come back hyphenated. Existing `abrev`
    values are preserved verbatim, so a caller holding a previous catalogue
    must match on `slug_instrumento` and keep its own `abrev`.

    `freshness=False` skips the tarballs entirely and returns `abrev`/`nombre`
    only, off the index alone — a few hundred KB. The default reads one
    tarball per law, which is the whole 380 MB corpus the first time; with a
    `cache_dir` already populated (`download_scjn_leyes_assets`, or `nota2md
    download federal-laws`) it costs no request at all.

    Raises `KeyError` while the release does not publish an asset it needs —
    see `_url_de_asset`.
    """
    indice = download_scjn_leyes_index(
        cache_dir=cache_dir, refrescar=refrescar, timeout=timeout
    )
    catalogo = []
    for slug in sorted(indice["instrumentos"]):
        entrada = {"abrev": slug, "nombre": indice["instrumentos"][slug]["nombre"]}
        if freshness:
            actualizado = _estado_de_asset(slug, cache_dir, refrescar, timeout).get(
                "actualizado"
            )
            if actualizado:
                entrada["actualizado"] = actualizado
        catalogo.append(entrada)
    return catalogo


def scjn_leyes_slugs(timeout: int = 30) -> list[str]:
    """Every law the `scjn-leyes` release publishes a tarball for, by slug.

    Read off the release's own asset listing rather than off
    `indice-global.json.gz`: a law crawled but not linked yet has a `.tgz`
    and no index entry, and "download the corpus" means the tarballs, not
    the ones the linking phase already got to.
    """
    return sorted(
        nombre.removesuffix(".tgz")
        for nombre in _assets_scjn_leyes(timeout)
        if nombre.endswith(".tgz")
    )


def download_scjn_leyes_assets(
    slugs: list[str] | None = None,
    *,
    cache_dir=SIN_CACHE_DIR,
    refrescar: bool = False,
    timeout: int = 60,
    log=None,
) -> list[tuple[Path, bool]]:
    """Put the `scjn-leyes` release's assets on disk: the reverse index plus
    one tarball per law, into ``<cache_dir>/scjn-leyes/``.

    `slugs` picks which laws to fetch; None (the default) means every law the
    release publishes. The index is always included — it is what
    `legal_provisions` resolves a codNota through, and it costs a few hundred
    KB against the corpus' 380 MB.

    Returns one ``(path, downloaded)`` pair per asset, in the order they were
    fetched, with `downloaded` False for an asset that was already cached —
    matched by name and never revalidated, like every other read of this
    release (see `nota2md.cache`). `refrescar=True` re-downloads regardless.

    Unlike the release *readers*, this is the "materialize it on disk" verb,
    so ``cache_dir=None`` is meaningless here and raises: downloading into
    memory and discarding it is not something a caller can want.
    """
    directorio = resuelve_cache_dir(cache_dir)
    if directorio is None:
        raise ValueError(
            "download_scjn_leyes_assets writes the release to disk; "
            "cache_dir=None ('no cache') has nothing to write to"
        )

    # Named slugs give the asset names outright, so a fully cached re-run can
    # skip the release listing too and cost no HTTP request at all; without
    # them the listing *is* how "every law" is known, so it is unavoidable.
    urls = None
    if slugs is None:
        urls = _assets_scjn_leyes(timeout)
        nombres = [ASSET_INDICE_GLOBAL] + [
            f"{slug}.tgz"
            for slug in sorted(n.removesuffix(".tgz") for n in urls if n.endswith(".tgz"))
        ]
    else:
        nombres = [ASSET_INDICE_GLOBAL] + [f"{slug}.tgz" for slug in slugs]

    resultados = []
    for i, nombre in enumerate(nombres, 1):
        destino = directorio / _SCJN_LEYES_RELEASE / nombre
        ya_estaba = destino.exists() and not refrescar
        if ya_estaba:
            ruta = destino
        else:
            if urls is None:
                urls = _assets_scjn_leyes(timeout)
            if nombre not in urls:
                raise KeyError(
                    f"el release '{_SCJN_LEYES_RELEASE}' no publica el asset '{nombre}'"
                )
            ruta = asset_en_cache(
                _SCJN_LEYES_RELEASE, nombre, urls[nombre],
                cache_dir=directorio, refrescar=refrescar, timeout=timeout,
            )
        if log is not None:
            estado = "already cached" if ya_estaba else "downloaded"
            log(f"[{i}/{len(nombres)}] {nombre}: {estado}")
        resultados.append((ruta, not ya_estaba))
    return resultados
