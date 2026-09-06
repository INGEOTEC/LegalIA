#!/usr/bin/env python3
"""Record each federal law's `materia`, `vigencia` and `resumen` — the SCJN's
own subject classification, in-force status and one-paragraph abstract
(issue #215, off #203's items 1/2/5).

    scjn download                                   # once, if the cache is cold
    ./scripts/fetch_federal_law_metadata.py --dry-run
    ./scripts/fetch_federal_law_metadata.py

The three fields are per **law**, not per reform: they ride on every
`BusquedaFrase` hit and appear nowhere on a `Reforma` row. So each is written
once into that law's own `estado.json` (the one per-law record since issue
#210) and projected into `indice-global.json.gz`'s `instrumentos` entries,
where a reader gets them for a few hundred KB instead of the corpus' ~380 MB.
Nothing is written into a snapshot's provenance header: `vigencia` describes
the law *today*, while a snapshot describes it at one reform in the past, and
the on-disk snapshot format does not change.

## Identity comes from the cache, never from a ranking

Every law is addressed by the `id_ordenamiento` its own newest snapshot header
already records — read straight out of the cached `scjn-leyes` release, which
this script downloads first when it is not there. The SCJN is then searched
with that snapshot's `ordenamiento` (the SCJN's own title for the law), and
**only** the hit carrying that same `idOrdenamiento` is kept
(`scjn.api.instrument_metadata`). A law whose id is not among the results is
reported and left alone, never guessed at — issue #115's Hallazgo C is that
this search can return a completely wrong document for an instrument.

## What it writes, and what you publish

- ``<outdir>/leyes/<slug>/estado.json`` — merged, never overwritten
  (`scjn.state.escribe_estado`), so `actualizado`/`rastreado`/`enlazado` and
  everything else a law already recorded stay untouched. A law with no
  directory there is skipped with a warning rather than created: this script
  crawls nothing, so an empty law directory would be a lie.
- ``<destino>/indice-global.json.gz`` — the **published** index (the one in the
  cache), patched with the three fields and re-stamped. Patched rather than
  rebuilt from local scratch on purpose: the published index has to keep
  describing exactly the published tarballs, which a workspace that is a few
  crawls behind would silently change. `SHA256SUMS.txt` covers only the `.tgz`
  assets, so it does not go stale from this.

Publishing stays manual, as everything in this corpus does (issue #115,
Hallazgo C — no Action ever publishes SCJN-derived data):

    gh release upload scjn-leyes scripts/scjn/leyes-release/indice-global.json.gz \\
        --repo INGEOTEC/LegalIA --clobber

The per-law tarballs pick up the new `estado.json` keys on each law's next
ordinary repack (`empaqueta_scjn_leyes.py`) — three fields are not worth a
380 MB re-upload, and `escribe_indice_global` falls back to the published
index for a law whose local `estado.json` has none, so a later full repack
cannot silently drop them.
"""

import argparse
import gzip
import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/scjn` first.
_RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_RAIZ / "packages" / "scjn"))

from scjn.api import ScjnApi, ScjnApiError, instrument_metadata  # noqa: E402
from scjn.header import parse_header  # noqa: E402
from scjn.release import (  # noqa: E402
    ASSET_INDICE_GLOBAL,
    CAMPOS_METADATOS,
    download_scjn_leyes_assets,
    download_scjn_leyes_index,
    iter_current_federal_laws,
    local_slugs,
)
from scjn.state import escribe_estado  # noqa: E402

COLECCION = "leyes"


def identities(slugs=None, *, cache_dir=None):
    """How each cached law is addressed at the SCJN: one
    ``{"slug", "nombre", "id_ordenamiento", "ordenamiento"}`` per law, read
    off the release already on disk.

    Both SCJN-side fields come from the law's newest snapshot's own
    provenance header (`scjn.header.parse_header` over what
    `iter_current_federal_laws` already yields) — every one of the 315 laws
    published today carries them. A snapshot old enough to predate them
    (nothing in the release as of issue #215) yields `id_ordenamiento` None
    and is reported unresolved rather than searched for by name, which is the
    whole point: a law is described only when it can be addressed by id.
    """
    for ley in iter_current_federal_laws(slugs, cache_dir=cache_dir):
        cabecera = parse_header(ley["markdown"] or "")
        yield {
            "slug": ley["slug"],
            "nombre": ley["nombre"],
            "id_ordenamiento": cabecera.get("id_ordenamiento"),
            "ordenamiento": cabecera.get("ordenamiento"),
        }


def fetch_metadata(identidades, *, api=None, clasificado=None, log=print):
    """Ask the SCJN for each law's `materia`/`vigencia`/`resumen`, keyed by
    slug, plus the slugs it could not answer for.

    One search request per law, at `ScjnApi`'s own rate limit. A law is
    described only when the search surfaces its own `id_ordenamiento`
    (`instrument_metadata`); anything else — an id the search never returns,
    a header with no id at all, a transport failure — lands in the unresolved
    list, which the caller reports rather than papers over. A hit that
    carries none of the three fields counts as unresolved too: writing
    `clasificado` alone would claim the SCJN was asked and had nothing to
    say, which is not the same thing.
    """
    api = api or ScjnApi()
    clasificado = clasificado or date.today().isoformat()
    metadatos = {}
    sin_resolver = []
    total = len(identidades)
    for i, identidad in enumerate(identidades, 1):
        slug = identidad["slug"]
        id_ordenamiento = identidad["id_ordenamiento"]
        if not id_ordenamiento:
            sin_resolver.append((slug, "no id_ordenamiento in its newest snapshot header"))
            continue
        nombre = identidad["ordenamiento"] or identidad["nombre"]
        if not nombre:
            sin_resolver.append((slug, "no title to search the SCJN with"))
            continue
        try:
            hit = instrument_metadata(api, nombre, id_ordenamiento)
        except ScjnApiError as exc:
            sin_resolver.append((slug, f"the SCJN did not answer: {exc}"))
            continue
        if hit is None:
            sin_resolver.append(
                (slug, f"idOrdenamiento {id_ordenamiento} not among the results for {nombre!r}")
            )
            continue
        campos = {
            campo: getattr(hit, campo)
            for campo in CAMPOS_METADATOS
            if getattr(hit, campo)
        }
        if not campos:
            sin_resolver.append((slug, "the SCJN carries none of the three fields for it"))
            continue
        campos["clasificado"] = clasificado
        metadatos[slug] = campos
        log(
            f"[{i}/{total}] {slug}: "
            f"{campos.get('vigencia', '—')} / {campos.get('materia', '—')}"
        )
    return metadatos, sin_resolver


def write_to_corpus(outdir: Path, metadatos: dict, *, log=print) -> list[str]:
    """Merge each law's metadata into its own `estado.json` under
    ``<outdir>/leyes/``, and return the slugs actually written.

    A merge (`scjn.state.escribe_estado`), so `actualizado`/`rastreado`/
    `enlazado`/`id_ordenamiento` and everything else already on file survive
    untouched. A slug with no directory there is skipped with a warning: this
    script never crawls, so creating a law directory holding nothing but
    three fields would misrepresent what the workspace has."""
    base = outdir / COLECCION
    escritos = []
    for slug in sorted(metadatos):
        directorio = base / slug
        if not directorio.is_dir():
            log(f"  aviso: {directorio} does not exist — skipped, nothing crawled here")
            continue
        escribe_estado(directorio, **metadatos[slug])
        escritos.append(slug)
    return escritos


def patch_indice_global(destino: Path, metadatos: dict, *, cache_dir=None) -> tuple[Path, int]:
    """Write the published `indice-global.json.gz`, patched with each law's
    metadata, into `destino` — and return the path plus how many instruments
    gained at least one field.

    The published index (the one in the cache) is the base, not a freshly
    built one: it is the only copy that is guaranteed to describe exactly the
    tarballs the release currently ships, and this asset is uploaded next to
    them. Written with the same conventions `empaqueta_scjn_leyes.escribe_indice_global`
    uses — gzip stamped with mtime 0, keys in the order they were built — so
    the two produce the same shape; `generado` is re-stamped, since the asset
    genuinely changed.

    A slug in `metadatos` that the index does not list is ignored: the index
    is the release's own listing, and this function does not get to add a law
    to it."""
    indice = json.loads(json.dumps(download_scjn_leyes_index(cache_dir=cache_dir)))
    # `download_scjn_leyes_index` turns the codNota keys back into ints and
    # memoizes the result; JSON needs the strings back, and the round trip
    # above also keeps this function from mutating that memo in place.
    indice["codNota"] = {str(cod): entradas for cod, entradas in indice["codNota"].items()}
    indice["generado"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    tocados = 0
    for slug, entrada in indice["instrumentos"].items():
        campos = {c: v for c, v in metadatos.get(slug, {}).items() if c in CAMPOS_METADATOS}
        if not campos:
            continue
        # Rebuilt rather than updated in place so the keys land in the same
        # order `construye_indice_global` writes them in.
        indice["instrumentos"][slug] = {
            **{c: v for c, v in entrada.items() if c not in CAMPOS_METADATOS},
            **campos,
        }
        tocados += 1

    destino.mkdir(parents=True, exist_ok=True)
    salida = destino / ASSET_INDICE_GLOBAL
    crudo = json.dumps(indice, ensure_ascii=False, sort_keys=False).encode("utf-8")
    with open(salida, "wb") as bruto, \
            gzip.GzipFile(filename="", mode="wb", fileobj=bruto, mtime=0) as gz:
        gz.write(crudo)
    return salida, tocados


def _reporta(metadatos: dict, sin_resolver: list, log) -> None:
    """The distributions worth reading before publishing: what the SCJN says
    is still in force, and how the corpus divides by subject."""
    vigencias = Counter(m.get("vigencia", "—") for m in metadatos.values())
    materias = Counter(m.get("materia", "—") for m in metadatos.values())
    con_resumen = sum(1 for m in metadatos.values() if m.get("resumen"))
    log("")
    log(f"metadatos: {len(metadatos)} ley(es) resuelta(s), {len(sin_resolver)} sin resolver")
    log(f"  con resumen: {con_resumen}")
    log("  vigencia:")
    for valor, cuenta in vigencias.most_common():
        log(f"    {cuenta:4d}  {valor}")
    log(f"  materia ({len(materias)} valor(es) distinto(s)), las 15 mas comunes:")
    for valor, cuenta in materias.most_common(15):
        log(f"    {cuenta:4d}  {valor}")
    if sin_resolver:
        log("  sin resolver:")
        for slug, motivo in sin_resolver:
            log(f"    {slug}: {motivo}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--outdir", type=Path, default=_RAIZ / "scripts" / "scjn", metavar="DIR",
        help="workspace holding <outdir>/leyes/<slug>/, where each law's own "
             "estado.json is merged (default: %(default)s)",
    )
    parser.add_argument(
        "--destino", type=Path, default=_RAIZ / "scripts" / "scjn" / "leyes-release",
        metavar="DIR",
        help=f"where the patched {ASSET_INDICE_GLOBAL} is written, ready to upload "
             "(default: %(default)s)",
    )
    parser.add_argument(
        "--slug", action="append", default=None, dest="slugs", metavar="SLUG",
        help="only this law (repeatable). Not given: every law in the release. "
             f"Note the patched {ASSET_INDICE_GLOBAL} always keeps every other "
             "law's already-published fields, so a partial run is still publishable",
    )
    parser.add_argument(
        "--cache-dir", default=None, metavar="DIR",
        help="scjn-leyes release cache to read the corpus from "
             "(scjn.cache.CACHE_DIR when not given)",
    )
    parser.add_argument(
        "--sin-descarga", action="store_true",
        help="do not download missing release assets first; fail on a cold cache",
    )
    parser.add_argument(
        "--espera", type=float, default=None, metavar="SEGUNDOS",
        help="seconds between SCJN requests (scjn.api.ESPERA_DEFAULT when not given)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="ask the SCJN and report, writing neither estado.json nor the index",
    )
    args = parser.parse_args(argv)

    def log(mensaje: str) -> None:
        print(mensaje, file=sys.stderr)

    if not args.sin_descarga:
        log("scjn-leyes: making sure the release is on disk...")
        download_scjn_leyes_assets(
            args.slugs, cache_dir=args.cache_dir,
            log=(lambda m: None) if args.slugs is None else log,
        )
    slugs = args.slugs or local_slugs(args.cache_dir)
    log(f"scjn-leyes: {len(slugs)} ley(es) en el cache")

    identidades = list(identities(slugs, cache_dir=args.cache_dir))
    api = ScjnApi() if args.espera is None else ScjnApi(espera=args.espera)
    metadatos, sin_resolver = fetch_metadata(identidades, api=api, log=log)
    _reporta(metadatos, sin_resolver, log)

    if args.dry_run:
        log("\n--dry-run: nada escrito")
        return 0

    escritos = write_to_corpus(args.outdir, metadatos, log=log)
    log(f"\n{COLECCION}: {len(escritos)} estado.json actualizado(s) en {args.outdir / COLECCION}")

    salida, tocados = patch_indice_global(args.destino, metadatos, cache_dir=args.cache_dir)
    log(f"{salida}: {tocados} instrumento(s) con metadatos ({salida.stat().st_size} bytes)")
    log(
        "\nPara publicar (a mano, como todo en este corpus — issue #115, Hallazgo C):\n"
        f"    gh release upload scjn-leyes {salida} --repo INGEOTEC/LegalIA --clobber"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
