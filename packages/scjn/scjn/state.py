"""Per-instrument crawl state: `estado.json`, and whether an instrument
needs to be crawled again.
"""

import json
from pathlib import Path

from scjn.catalog import instrument_up_to_date


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
PENDIENTE_FALTAN_REFORMAS = "faltan_reformas"


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


def reformas_faltantes(reformas, directorio: Path) -> list:
    """The rows of `reformas` (`scjn.api.reformas_of_ordenamiento`'s own
    return value, newest first) that `directorio` has no snapshot for yet —
    a list, in that same newest-first order, not a count and not a boolean
    (issue #211).

    A date comparison alone cannot see this: `lfd` had 92 snapshots against
    98 reforms, and the newest reform was there while six older ones were
    not, so nothing about "the newest date on file" ever looked stale. This
    replicates the file name `scjn.api.descarga_ordenamiento` gives each row
    instead — two reforms sharing a `fecha_publicacion` (the CPEUM alone has
    39 such dates) are told apart by their position among same-date rows in
    `reformas`' own most-recent-first order, exactly the `-2`/`-3`... suffix
    convention `scjn.header._orden_repeticion` reads back — so a genuinely
    missing row is never confused with one two different reforms merely
    happen to date the same as, and a row is reported missing by its own
    identity, never by a coincidental count match.

    A row with `tieneArticulos=False` is excluded: the SCJN says up front it
    holds no consolidated text for it, `descarga_ordenamiento` never writes
    a file for it either, and it is not a gap this corpus could ever close —
    counting it as missing would flag the same row forever. A row that
    fails for some other reason at crawl time (an HTTP 500,
    `reformas_fallidas`) is not distinguishable here — this function only
    ever reads the table, never re-requests a reform — so it is reported
    missing like any other row a caller has not fetched yet; a repeat
    failure is a fact about the SCJN, not a bug in this comparison."""
    existentes = {archivo.name for archivo in directorio.glob("*.md")}
    repeticiones: dict[str, int] = {}
    faltantes = []
    for reforma in reformas:
        if not reforma.tieneArticulos:
            continue
        repeticiones[reforma.fecha_publicacion] = repeticiones.get(reforma.fecha_publicacion, 0) + 1
        orden = repeticiones[reforma.fecha_publicacion]
        sufijo = f"-{orden}" if orden > 1 else ""
        if f"{reforma.fecha_publicacion}{sufijo}.md" not in existentes:
            faltantes.append(reforma)
    return faltantes


def motivo_pendiente(
    entry: dict, directory: Path, corpus_date: str | None, *, reformas=None
) -> str | None:
    """Why `entry` needs to be crawled again, or None when it does not —
    the whole decision issue #148's planner and refresh runs share, made
    offline from `entry` (a law's own catalogue entry, issue #210: read off
    its `estado.json`, no longer off a separate `catalogo.json`) plus what
    is on disk — and, when `reformas` is given, from that SCJN reform table
    too (issue #211), still with no request made *inside* this function
    itself: the caller fetches `reformas` (one request, addressed by
    `id_ordenamiento`, so it can never resolve to the wrong document —
    issue #115's Hallazgo C lives in the *search*, not this call) and hands
    it in.

    - `PENDIENTE_NUNCA_RASTREADO`: no snapshot on disk at all. Always
      pending, whatever the dates say — the `lfca` safety net of issue #124,
      unchanged.
    - `PENDIENTE_FALTAN_REFORMAS`: `reformas` was given (row comparison,
      issue #211's default for a plan anyone acts on) and
      `reformas_faltantes` found rows this instrument has no snapshot for —
      the precise replacement for `PENDIENTE_CAMBIO` in this mode, since a
      row comparison already answers *exactly* what changed and a date
      comparison on top would only ever be redundant or wrong (the `lfd`
      case: complete-looking by date, incomplete by row). `actualizado` is
      not consulted at all in this mode — it stays load-bearing elsewhere
      (`actualizado_dof` is still the only signal for a reform the SCJN has
      not indexed yet, #124's `lfca`; a row comparison against a table that
      does not have the reform cannot see it either — row comparison catches
      *gaps*, the DOF half catches *lag*).
    - `PENDIENTE_SIN_ACTUALIZADO`: `reformas` was not given (no
      `id_ordenamiento` on file yet, or the offline fallback mode — issue
      #211 keeps the date comparison as an explicit, cheaper alternative,
      not something removed) and the catalogue gives no `actualizado` for it
      either, so whether it changed is simply unknowable. Reported as
      pending, but as its own distinct reason: a planner lists these apart
      and lets a human decide, while a full sweep keeps re-crawling them,
      which is what it always did.
    - `PENDIENTE_CAMBIO`: same fallback mode, and the catalogue now reports
      a reform newer than the one this instrument was last crawled against.
      The comparison is against the instrument's own `estado.json` when it
      has one, and only otherwise against the collection-wide `corpus_date`
      (`instrument_up_to_date`) — per-law state takes precedence, so a
      single law refreshed on its own counts as up to date even though no
      full sweep ran after it."""
    if not any(directory.glob("*.md")):
        return PENDIENTE_NUNCA_RASTREADO
    if reformas is not None:
        return PENDIENTE_FALTAN_REFORMAS if reformas_faltantes(reformas, directory) else None
    actualizado = entry.get("actualizado")
    if not actualizado:
        return PENDIENTE_SIN_ACTUALIZADO
    rastreado_contra = lee_estado(directory).get("actualizado")
    if rastreado_contra:
        return PENDIENTE_CAMBIO if actualizado > rastreado_contra else None
    return None if instrument_up_to_date(directory, actualizado, corpus_date) else PENDIENTE_CAMBIO
