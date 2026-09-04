"""Match every SCJN snapshot of a law to the DOF `codNota` that published it,
and resolve a `codNota` back to the snapshot it produced.

`codNota` linking is the one SCJN-adjacent concern that legitimately needs
both sides -- SCJN snapshots on one hand, DOF titles on the other -- so it
stays here rather than in the `scjn` package (issue #206 section 5, #208).
It is the seam between the two: nothing on the SCJN side imports this
module, and this is the only place in the project that knows how a
snapshot becomes a `codNota`. `localiza_codNota`/`snapshot_de_codNota` are
the seam's other direction -- resolving a `codNota` back to the snapshot it
produced, for `nota2md.builder.legal_provisions` -- and stay here for the
same reason, calling `scjn.release`'s disk-first readers instead of the
network (issue #209).

In: one law's snapshots (`scjn.header.versiones_de_directorio`) plus the
DOF's own titles (`dofjson.legal_provisions_titles`). Out: a `codNota` per
snapshot, with a `title_link_status` and, where the content diff confirmed
it, a score. The six link fields this module produces (`codNota`,
`title_link_status`, `content_diff_confirmed_codNota`, `content_diff_score`,
and the two candidate/count fields `indice.json` carries) are `nota2md`-owned
data that the `scjn` package only passes through when it writes
`indice.json`/`indice-global.json.gz` (`construye_indice_global`) -- it
never interprets them.

The crawl only knows the SCJN's own view of an instrument: a publication
date per snapshot, nothing that ties back to a DOF `codNota`. Issue #105's
original design paired that date against the Cámara de Diputados' own
curated `historial` (its list of codNota per instrument, read through a
release this project no longer publishes -- issues #184 and #187 deleted
both) -- but issue #123's corrected goal is for the SCJN, plus the DOF's own
title dataset, to be the *only* source once an instrument's `nombre` has
picked which one to crawl: `historial` is never consulted here, not even as
a tie-breaker or a fallback. What the catalogue contributes is `nombre`
itself, used for two things: searching the SCJN and, here, testing which
same-day DOF note's own title actually names the instrument. Two SCJN-dated
snapshots can share a `fecha_publicacion` (issue #105's Fase 0 found up to 4
on the CPEUM alone), so this also has to resolve same-day exclusivity: once
an earlier snapshot of that date has claimed a candidate, a later one
sharing the date never claims it again.
"""

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from nota2md.text import normaliza_para_comparar
from scjn.header import VersionInstrumento
from scjn.release import download_scjn_leyes_index, markdown_de_snapshot
from scjn.text import _normaliza

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
    """`archivo`'s own body, with the provenance header `scjn.api.cabecera`
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
# the SCJN's own reform table (`scjn.api.reformas_of_ordenamiento`, newest
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


# --- issue #209: resolving a codNota to the snapshot it produced ---------
#
# The other half of this module's seam: once a corpus has been linked (the
# functions above) and published, resolving a `codNota` back to the snapshot
# it produced is what `nota2md.builder.legal_provisions` calls on every "auto"
# build. It stays here, not in `scjn.release`, because a `codNota` is a DOF
# concept -- these two just call `scjn.release`'s disk-first readers.


def localiza_codNota(
    cod_nota: int, *, instrumento: str | None = None, cache_dir=None
) -> tuple[str, str] | None:
    """Which snapshot of which law the reform `cod_nota` enacted produced, as
    ``(slug, archivo)`` — or None when the release's reverse index has no
    entry for it.

    The reverse-index half of `snapshot_de_codNota`, split out because it
    answers *where* the text is without reading the law's tarball at all: a
    caller that already has that snapshot materialized (see
    `nota2md.builder.legal_provisions` with no `outdir`) needs the name and
    nothing else.

    `cache_dir` is forwarded to `scjn.release.download_scjn_leyes_index` as-is
    (`scjn.cache.CACHE_DIR` when not given) — raises `scjn.release.AssetNotCached`
    while the index is not cached there yet, exactly as that reader does.

    Raises `ValueError` for an ambiguous `cod_nota` exactly as
    `snapshot_de_codNota` does — see its docstring.
    """
    indice = download_scjn_leyes_index(cache_dir=cache_dir)
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


def snapshot_de_codNota(
    cod_nota: int, *, instrumento: str | None = None, cache_dir=None
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
    `nota2md.builder.legal_provisions` does.

    A `cod_nota` whose decree reformed several laws at once has several
    entries. Pass `instrumento` (a slug) to say which one is wanted;
    without it, that raises `ValueError` listing the candidates rather than
    silently returning one of them (D4).

    `cache_dir` is forwarded to `scjn.release`'s readers as-is
    (`scjn.cache.CACHE_DIR` when not given); raises
    `scjn.release.AssetNotCached` while the index or the law's own tarball is
    not cached there yet — `legal_provisions` treats that as "no coverage"
    rather than letting it propagate (issue #209; before this phase, a reader
    would attempt a download instead of raising).
    """
    ubicacion = localiza_codNota(cod_nota, instrumento=instrumento, cache_dir=cache_dir)
    if ubicacion is None:
        return None
    slug, archivo = ubicacion
    markdown = markdown_de_snapshot(slug, archivo, cache_dir=cache_dir)
    return slug, archivo, markdown
