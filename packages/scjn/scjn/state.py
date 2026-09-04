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
