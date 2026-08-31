#!/usr/bin/env python3
"""Build `lfca`'s corpus by hand — the SCJN has no ordenamiento of its own for
the LEY Federal de Cine y el Audiovisual — from the law it abrogated (SCJN)
plus the DOF note that enacted it (issue #144, follow-up of #124).

Isolated on purpose: this script touches no catalogue, no checkpoint, and no
general script (`fetch_scjn_legislacion.py`, `enlaza_scjn_legislacion.py`,
`nota2md/scjn.py`). It only writes inside
``<outdir>/leyes/lfca/``.

    nota2md download gazette-metadata   # pobla el cache de notas-archivo
    ./scripts/construye_lfca.py --outdir scripts/scjn

--- PROCEDIMIENTO (copiable a la página web del proyecto) -----------------

**Caso de referencia: una ley nueva que abroga a otra y que la SCJN todavía
no indexa.**

La **LEY Federal de Cine y el Audiovisual** (`lfca`, DOF 22-05-2026) está en
el catálogo de la Cámara de Diputados, pero **la SCJN no tiene ningún
ordenamiento propio para ella**: buscar su título exacto —o solo "Cine y el
Audiovisual"— en `legislacion.scjn.gob.mx` regresa 0 candidatos (verificado
en vivo, dos veces, durante #124). No es un problema de redacción del texto
de búsqueda ni de paginación del grid de resultados (eso se cerró en #143):
simplemente no está indexada.

Lo que la SCJN sí tiene indexado es la ley **abrogada** que `lfca` sustituyó:
la **LEY FEDERAL DE CINEMATOGRAFIA**, con su historia completa de reformas y
una fila final del 22-05-2026 que es el texto viejo (1992) con la nota de
editor de abrogación pegada al inicio. Esa ley, además, ya no aparece en el
catálogo de Diputados (`catalogo.json` solo trae `lfca`, con `historial`
vacío), así que ni el crawl ni el enlace la alcanzan por la vía normal.

**Esa última fila se descarta.** Es el único error real de la SCJN en este
caso, y es peor que un snapshot viejo: está fechada el día en que se publicó
`lfca` y su nota de editor anuncia la nueva ley, pero su cuerpo es el
articulado de la ley abrogada. Dejarla sería tener, dentro del corpus de
`lfca`, un snapshot que reclama la fecha de la ley nueva y trae el texto de
la vieja — exactamente la confusión que este directorio existe para evitar.
El texto del DOF es el único snapshot que esa fecha debe tener.

De ahí que el corpus de `lfca` se construya en dos mitades:

1. **Historia (reformas) = ley abrogada, vía SCJN.** Se llama
   `descarga_ordenamiento(sesion, "LEY FEDERAL DE CINEMATOGRAFIA", destino)`
   —el mismo código del crawl general, sin modificarlo— con
   ``destino = <outdir>/leyes/lfca/``. Deja los snapshots nombrados por fecha
   (``DD-MM-YYYY.md``) y con la cabecera SCJN de siempre, que registra en
   `nombre_buscado` que se buscaron por el nombre de la ley abrogada. De ahí
   se descarta la fila del 22-05-2026 (ver arriba); solo se borran archivos
   con `fuente: scjn`, así que volver a correr el script nunca toca el
   snapshot del DOF que él mismo escribió antes.
2. **Texto vigente = DOF.** La ley actual se publicó con **`codNota`
   5788357** (DOF 22-05-2026) y se convierte con `nota2md.legal_provisions`
   por la ruta HTML (la nota trae `cadenaContenido`; no hace falta OCR).

**Nombre de archivo.** El mismo formato que el resto del corpus SCJN: la
fecha de publicación, ``DD-MM-YYYY.md``. Descartada la fila de abrogación,
nada más reclama el 22-05-2026, así que el texto vigente queda como
``22-05-2026.md``, sin el sufijo ``-N`` que `descarga_ordenamiento` aplica a
dos filas del mismo día. (Ese sufijo se sigue respetando si la fecha
estuviera ocupada, para no romper `versiones_de_directorio`.)

**Cabecera.** Es lo único que cambia. El archivo que viene del DOF **no**
lleva el bloque `fuente: scjn` / `nombre_buscado` / `ordenamiento` /
`ratio_similitud` que escribe `nota2md.scjn._cabecera`, sino:

    ---
    fuente: dof
    codNota: 5788357
    nombre_buscado: LEY Federal de Cine y el Audiovisual
    ordenamiento: <título de la nota del DOF>
    fecha_publicacion: 22-05-2026
    categoria: DECRETO
    motivo: la SCJN no indexa esta ley (issue #144); texto tomado del DOF
    ---

`fuente: dof` es la señal de un grep para distinguir estos archivos de todo
lo demás bajo `scripts/scjn/`. No se escriben `ratio_similitud` ni
`sospechoso`: no hubo búsqueda en la SCJN ni `elige_candidato`, así que no
hay ratio que reportar, y `_confianza` los deja en `null`, que es exactamente
la semántica correcta. (Nota menor: `lee_cabecera` solo reconoce campos en
minúsculas, así que `codNota` queda como documentación dentro del archivo; el
índice lo toma de la constante del script, donde se conoce con certeza.) Los
snapshots históricos, que sí vienen de la SCJN, conservan su cabecera normal.

**Enlace a `codNota` (`indice.json`).** Se corre sobre el directorio el mismo
proceso de `enlaza_scjn_legislacion.py` —`title_candidates_por_fecha` +
`enlaza_por_titulo` + `confirm_by_content_diff`— pero buscando en los títulos
del DOF el nombre de la **ley abrogada**, que es el que aparece en el título
de cada decreto de reforma. La entrada del texto vigente no se infiere: su
`codNota` se conoce desde el origen (5788357) y se fija por construcción; sus
`title_candidates` sí se calculan, para que la evidencia quede registrada, y
su `ratio_similitud`/`sospechoso` quedan en `null`.

El `indice.json` cubre **todo** el directorio, incluido el snapshot del DOF:
si esa entrada faltara, cualquier análisis posterior que lea `indice.json`
como el índice del corpus (empaquetado, auditoría, reconstrucción, conteo de
cobertura) vería la historia de la ley abrogada sin el texto que hoy está en
vigor, que es justamente lo que este procedimiento existe para agregar. La
lista va ordenada por `fecha_publicacion` (más antigua primero), así que esa
entrada queda al final.

Además, **todas** las entradas llevan un campo extra, `fuente` (`"scjn"` o
`"dof"`), leído de la cabecera del propio archivo: es lo que permite
distinguir, sin volver a abrir cada `.md`, cuál entrada vino del crawl de la
ley abrogada y cuál del DOF. Un `indice.json` producido por
`enlaza_scjn_legislacion.py` **no** trae ese campo, así que quien lo consuma
debe tratarlo como opcional (ausente ⇒ `"scjn"`).

**Cuándo desaparece esta excepción.** El Mecanismo 2 de #124 (reintento en
cada refresh) salta los instrumentos que ya tienen snapshot en disco, así que
este directorio construido a mano **no** se va a actualizar solo. Si algún
día la SCJN publica el ordenamiento propio de `lfca`, hay que revisar y
reemplazar este directorio: borrarlo y correr
``fetch_scjn_legislacion.py --reintenta lfca`` seguido de
``enlaza_scjn_legislacion.py``, y retirar este script.

**Orden de operaciones.** Correr `enlaza_scjn_legislacion.py` después de este
script **sobrescribiría** su `indice.json` (el directorio ya existe y entra
al barrido normal, que buscaría por "LEY Federal de Cine y el Audiovisual",
no por la ley abrogada, y no conoce el `codNota` del texto vigente). Este
script se corre **después** del enlace general, y es el que deja la última
palabra sobre `scripts/scjn/leyes/lfca/indice.json`.

`lfiiedb` es candidata al mismo patrón; ver `scripts/fetch_lfiiedb_dof.py`.
"""

import argparse
import json
import sys
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/nota2md` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "nota2md"))

from dofjson.titulos import legal_provisions_titles  # noqa: E402
from nota2md.builder import fetch_nota, legal_provisions  # noqa: E402
from nota2md.scjn import (  # noqa: E402
    confirm_by_content_diff,
    descarga_ordenamiento,
    enlaza_por_titulo,
    lee_cabecera,
    nueva_sesion,
    title_candidates_por_fecha,
    title_link_status,
    versiones_de_directorio,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from enlaza_scjn_legislacion import (  # noqa: E402
    _confianza,
    _confirmaciones_por_contenido,
    _resolver_cache_dir,
    carga_porf,
)

#: Diputados' own name for the instrument this corpus belongs to.
NOMBRE_CATALOGO = "LEY Federal de Cine y el Audiovisual"
#: Its directory, the same `slug_instrumento` would pick for it.
ABREV = "lfca"
#: The abrogated law the SCJN *does* index, and whose reforms built the text
#: `lfca` inherits — what the SCJN is searched with, and what DOF titles are
#: tested against when linking each snapshot to its own codNota.
NOMBRE_ABROGADA = "LEY FEDERAL DE CINEMATOGRAFIA"
#: The DOF note that enacted `lfca` itself (22-05-2026).
COD_NOTA_VIGENTE = 5788357
#: The day `lfca` was published — and the date of the SCJN's own last row for
#: the abrogated law, which is discarded (see `descarta_fila_de_abrogacion`).
FECHA_ABROGACION = "22-05-2026"
MOTIVO = "la SCJN no indexa esta ley (issue #144); texto tomado del DOF"


def descarta_fila_de_abrogacion(destino: Path) -> list[Path]:
    """Delete whatever the SCJN's own last row for the abrogated law left in
    `destino` for `FECHA_ABROGACION`, and say which files those were.

    That row is the one real error the SCJN makes here, and it is worse than
    a stale snapshot: it is dated the day `lfca` was published and its
    editorial note announces `lfca`'s enactment, but its body is the *old*
    1992 text of the LEY FEDERAL DE CINEMATOGRAFIA. Kept, it would sit in
    this corpus as a snapshot that claims the new law's date while carrying
    the abrogated law's articles — the exact confusion this whole directory
    exists to avoid. The DOF's own text (`COD_NOTA_VIGENTE`) is the only
    snapshot that date should have, and it takes the date's plain filename
    (no `-N` suffix) since nothing else claims it any more.

    Only `fuente: scjn` files are ever removed, so re-running this script
    never touches the DOF snapshot it wrote itself on a previous run."""
    descartados = []
    for archivo in sorted(destino.glob("*.md")):
        campos = lee_cabecera(archivo)
        if campos.get("fecha_publicacion") == FECHA_ABROGACION and (
            campos.get("fuente") == "scjn"
        ):
            archivo.unlink()
            descartados.append(archivo)
    return descartados


def _cabecera_dof(nota: dict) -> str:
    """The provenance header for the snapshot that comes from the DOF rather
    than the SCJN — same YAML-ish shape `nota2md.scjn._cabecera` writes (so
    `lee_cabecera`/`versiones_de_directorio` read it back unchanged), but
    declaring `fuente: dof` and carrying the `codNota` it is known by from
    the origin. No `ratio_similitud`/`sospechoso`: there was no SCJN search
    and no `elige_candidato`, so there is no ratio to report."""
    return "\n".join(
        [
            "---",
            "fuente: dof",
            f"codNota: {COD_NOTA_VIGENTE}",
            f"nombre_buscado: {NOMBRE_CATALOGO}",
            f"ordenamiento: {nota['titulo']}",
            f"fecha_publicacion: {nota['fecha']}",
            "categoria: DECRETO",
            f"motivo: {MOTIVO}",
            "---",
        ]
    )


def escribe_texto_vigente(destino: Path) -> Path:
    """`COD_NOTA_VIGENTE`'s own DOF Markdown, written into `destino` under
    the snapshot naming the rest of the corpus uses: its publication date,
    `DD-MM-YYYY.md`. `descarga_ordenamiento`'s own `-N` suffix is still
    honoured if the date were somehow already taken, but with the SCJN's own
    abrogation row discarded (`descarta_fila_de_abrogacion`) nothing else
    claims 22-05-2026, so this is the plain `22-05-2026.md`."""
    nota = fetch_nota(COD_NOTA_VIGENTE)
    fecha = nota["fecha"]
    destino.mkdir(parents=True, exist_ok=True)

    existente = next(
        (
            archivo
            for archivo in destino.glob("*.md")
            if lee_cabecera(archivo).get("fuente") == "dof"
        ),
        None,
    )
    if existente is not None:
        return existente

    orden = 1
    while (destino / f"{fecha}{'' if orden == 1 else f'-{orden}'}.md").exists():
        orden += 1
    archivo = destino / f"{fecha}{'' if orden == 1 else f'-{orden}'}.md"

    temporal = legal_provisions(COD_NOTA_VIGENTE, destino / "_dof", source="html", nota=nota)
    markdown = temporal.read_text(encoding="utf-8")
    archivo.write_text(f"{_cabecera_dof(nota)}\n\n{markdown}", encoding="utf-8")
    temporal.unlink()
    try:
        (destino / "_dof").rmdir()
    except OSError:
        pass
    return archivo


def construye_indice(destino: Path, porf: dict, archivo_dof: Path) -> list[dict]:
    """`enlaza_scjn_legislacion.py`'s own `indice.json`, computed over
    `destino` with the abrogated law's name as the text to look for in DOF
    titles — plus, on every entry, the `fuente` its own header declares, and
    with `archivo_dof`'s entry linked by construction to `COD_NOTA_VIGENTE`
    instead of inferred."""
    versiones = versiones_de_directorio(destino)
    candidatos_por_fecha = title_candidates_por_fecha(
        (v.fecha_publicacion for v in versiones), NOMBRE_ABROGADA, porf
    )
    enlazadas = enlaza_por_titulo(versiones, candidatos_por_fecha)
    confirmaciones = _confirmaciones_por_contenido(
        versiones, candidatos_por_fecha, destino / "notas", {}
    )

    indice = []
    for idx, v in enumerate(enlazadas):
        es_dof = v.archivo == archivo_dof
        candidatos_dia = candidatos_por_fecha.get(v.fecha_publicacion, [])
        # The known-from-origin codNota is never inferred, and never left to
        # another snapshot of the same day to claim by title alone.
        cod = COD_NOTA_VIGENTE if es_dof else (None if v.codNota == COD_NOTA_VIGENTE else v.codNota)
        confianza = (
            {"ratio_similitud": None, "sospechoso": None} if es_dof else _confianza(v.archivo)
        )
        indice.append(
            {
                "archivo": v.archivo.name,
                "fecha_publicacion": v.fecha_publicacion,
                "codNota": cod,
                **confianza,
                "title_candidates": candidatos_dia,
                "title_link_status": title_link_status(cod, candidatos_dia),
                "content_diff_confirmed_codNota": confirmaciones[idx].confirmed_codNota,
                "content_diff_score": confirmaciones[idx].score,
                "fuente": lee_cabecera(v.archivo).get("fuente", "scjn"),
            }
        )
    (destino / "indice.json").write_text(
        json.dumps(indice, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return indice


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--outdir", type=Path, required=True,
        help="la misma raiz que usan los scripts generales (p.ej. scripts/scjn)",
    )
    p.add_argument(
        "--cache-dir", default=None, metavar="DIR",
        help="directorio con los assets .tgz de notas-archivo de donde se leen "
             "los titulos del DOF (poblalo con `nota2md download gazette-metadata`); "
             "sin valor: dofjson.titulos.CACHE_DIR; 'none': a memoria",
    )
    p.add_argument(
        "--espera", type=float, default=1.0, help="segundos entre peticiones a la SCJN",
    )
    args = p.parse_args(argv)

    destino = args.outdir / "leyes" / ABREV

    print(f"[1/3] SCJN: {NOMBRE_ABROGADA} (la ley abrogada) -> {destino}", file=sys.stderr)
    sesion = nueva_sesion()
    escritos = descarga_ordenamiento(
        sesion, NOMBRE_ABROGADA, destino, espera=args.espera,
        on_progreso=lambda m: print(f"  {m}", file=sys.stderr),
    )
    if not escritos:
        print(
            f"  aviso: la SCJN no regreso nada para {NOMBRE_ABROGADA!r} — "
            "el corpus quedaria solo con el texto vigente del DOF",
            file=sys.stderr,
        )
    descartados = descarta_fila_de_abrogacion(destino)
    for archivo in descartados:
        print(
            f"  descartado {archivo.name}: la fila del {FECHA_ABROGACION} de la SCJN "
            "anuncia la abrogacion pero trae el texto de la ley vieja",
            file=sys.stderr,
        )
    # Counted off disk, not off `escritos`: on a re-run `descarga_ordenamiento`
    # also reports back the file it skipped for 22-05-2026, which by then is
    # the DOF's own snapshot, not a row of the abrogated law.
    de_scjn = sum(1 for a in destino.glob("*.md") if lee_cabecera(a).get("fuente") == "scjn")
    print(f"  {de_scjn} snapshot(s) de la ley abrogada", file=sys.stderr)

    print(f"[2/3] DOF: codNota {COD_NOTA_VIGENTE} (el texto vigente)", file=sys.stderr)
    archivo_dof = escribe_texto_vigente(destino)
    print(f"  {archivo_dof.name}", file=sys.stderr)

    print("[3/3] enlace a codNota -> indice.json", file=sys.stderr)
    titulos = legal_provisions_titles(
        _resolver_cache_dir(args.cache_dir), log=lambda *_: None
    )
    indice = construye_indice(destino, carga_porf(titulos), archivo_dof)

    enlazados = sum(1 for e in indice if e["codNota"] is not None)
    de_scjn = sum(1 for e in indice if e["fuente"] == "scjn")
    de_dof = sum(1 for e in indice if e["fuente"] == "dof")
    print(
        f"{ABREV}: {enlazados}/{len(indice)} enlazadas "
        f"({de_scjn} snapshot(s) SCJN de la ley abrogada + {de_dof} del DOF)",
        file=sys.stderr,
    )
    for entrada in indice:
        if entrada["codNota"] is None and len(entrada["title_candidates"]) > 1:
            print(
                f"  aviso: {entrada['fecha_publicacion']}: varios codNota mencionan "
                f"{NOMBRE_ABROGADA!r} ese dia ({entrada['title_candidates']}) — "
                "revisar a mano (issue #115/#126/#127)",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
