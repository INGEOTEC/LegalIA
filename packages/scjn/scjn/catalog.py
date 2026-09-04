"""The federal-law catalogue's own algebra: slugs, the `nombre_scjn`
override, and minting a brand-new law's `abrev`.

Until issue #210 this module also merged a freshly extracted `catalogo.json`
against the previous one on disk (`merge_catalog_with_previous`,
`merge_catalog_overrides`, `apply_actualizado`, `catalog_key`). That file is
gone: a law's own `estado.json` is now the single per-law record, and
`scjn.state.escribe_estado`'s merge-not-overwrite already carries
`nombre_scjn`/`abrev` forward with nothing to reconcile against a second
copy.
"""

import re
from datetime import datetime
from pathlib import Path

from scjn.text import _normaliza


def slugify(texto: str) -> str:
    """`texto` as a filesystem-safe, ASCII, lowercase slug — the release's
    own asset-name shape."""
    return re.sub(r"[^a-z0-9]+", "-", _normaliza(texto)).strip("-")


def slug_instrumento(entrada: dict) -> str:
    """The directory and release-asset name for one catalogue entry (as a
    law's own `estado.json` holds it since issue #210): its own `abrev`,
    slugified.

    Slugifying is not a no-op: the 14 laws whose abbreviation carries an
    underscore come out hyphenated (`lif_2026` -> `lif-2026`) — the slug is
    the release's asset name, while `abrev` itself is what `estado.json`
    keeps verbatim.

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
# search_name is Mecanismo 1: an optional `nombre_scjn` override a human can
# add to one law's own `estado.json` (applied to `lisipl`, not `lfca` — its
# gap is indexing lag, not a different title), which `escribe_estado`'s
# merge-not-overwrite already carries forward across every later write, and
# which `fetch_scjn_legislacion.py` searches with in place of `nombre`.
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


def iso_date_from_note(note: dict) -> str | None:
    """`note`'s own `fecha` (`DD-MM-YYYY`, the shape a DOF note record's
    own `fecha` field takes) as ISO `YYYY-MM-DD`, or None when `note` carries
    no `fecha` at all."""
    fecha = note.get("fecha")
    if not fecha:
        return None
    return datetime.strptime(fecha, "%d-%m-%Y").date().isoformat()


def instrument_up_to_date(directory: Path, updated: str | None, corpus_date: str | None) -> bool:
    """Whether the instrument crawled into `directory` can be skipped on a
    refresh run without touching the SCJN at all: it already has at least
    one snapshot on disk, and its own `updated` (the law's `actualizado`,
    an ISO date) is no later than `corpus_date` (the ISO
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

    >>> import scjn.catalog as catalog
    >>> catalog.mint_abrev("LEY Federal de Cine y el Audiovisual")
    'lfca'
    >>> catalog.mint_abrev("LEY Federal de Cine y el Audiovisual", taken={"lfca"})
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


