#!/usr/bin/env python3
"""Populate `lfiiedb`'s corpus from the DOF — the SCJN does not index the LEY
para el Fomento de la Inversión en Infraestructura Estratégica para el
Desarrollo con Bienestar (issue #145, follow-up of #124).

Isolated on purpose, and for exactly one law: nothing here generalizes
towards `nota2md` or the crawl scripts, and this script touches no catalogue,
no checkpoint and no general script (`fetch_scjn_legislacion.py`,
`enlaza_scjn_legislacion.py`, `nota2md/scjn.py`). It only writes inside
``<outdir>/leyes/lfiiedb/``.

    ./scripts/fetch_lfiiedb_dof.py --outdir scripts/scjn

--- PROCEDIMIENTO (copiable a la página web del proyecto) -----------------

**Caso: una ley nueva, sin historia de reformas, que la SCJN no indexa.**

La **LEY para el Fomento de la Inversión en Infraestructura Estratégica para
el Desarrollo con Bienestar** (`lfiiedb`) está en el catálogo de la Cámara de
Diputados, con `actualizado` 2026-04-09, pero `buscar()` (`nota2md/scjn.py`)
regresa **0 candidatos** para su nombre exacto: la SCJN todavía no la indiza
—mismo patrón que `lfca`—, y no es un problema de texto de búsqueda ni de
paginación del grid de resultados (eso ya se cerró para `lisr` en #143). No
hay nada que arreglar del lado de la SCJN: hay que traer la ley de otra
fuente.

Lo bueno es que **es una ley nueva, sin historia de reformas**: un solo
texto, una sola fecha. A diferencia de `lfca` (ver
`scripts/construye_lfca.py`), tampoco abroga una ley que la SCJN sí tenga
indexada, así que no hay historia que recuperar de ningún lado.

**De dónde sale el texto.** Del DOF, `codNota` **5784517**:

- `titulo`: "Decreto por el que se expide la Ley para el Fomento de la
  Inversión en Infraestructura Estratégica para el Desarrollo con Bienestar,
  y se reforman y adicionan diversas disposiciones de la Ley Federal de
  Presupuesto y Responsabilidad Hacendaria."
- `fecha`: 09-04-2026 — coincide exactamente con el `actualizado` del
  catálogo de Diputados; no hay nada que reconciliar.
- Trae `cadenaContenido`, así que alcanza la ruta HTML de `nota2md`
  (`legal_provisions(..., source="html")`): no hace falta OCR ni `dof2md`.

**Nombre de archivo.** El mismo que `descarga_ordenamiento`: la fecha de
publicación, ``DD-MM-YYYY.md``, es decir
``scripts/scjn/leyes/lfiiedb/09-04-2026.md``. Un solo archivo — no hay
reformas. La fecha se toma del campo `fecha` de la nota, no se escribe a
mano, para que el script sea reproducible.

**Cabecera.** Se sigue el esquema de `nota2md.scjn._cabecera` (frontmatter
delimitado por `---`, leíble por `lee_cabecera`), pero dejando explícito que
el origen es el DOF y no la SCJN:

    ---
    fuente: dof
    codNota: 5784517
    nombre_buscado: LEY para el Fomento de la Inversión en Infraestructura Estratégica para el Desarrollo con Bienestar
    ordenamiento: <título de la nota del DOF>
    fecha_publicacion: 09-04-2026
    categoria: DECRETO
    motivo: la SCJN no tiene esta ley indexada (issue #124); texto tomado directamente del DOF
    ---

- `fuente: dof` (no `scjn`) es la señal de un grep para distinguir estos
  archivos de todo lo demás bajo `scripts/scjn/`.
- `codNota` va **en la cabecera**, porque aquí sí se conoce con certeza desde
  el origen (por la ruta SCJN el codNota solo aparece después, en
  `indice.json`, inferido por título/diff). Nota menor: `lee_cabecera` solo
  reconoce campos en minúsculas, así que `codNota` queda como documentación
  dentro del archivo; el índice lo toma de la constante del script.
- **No** se escriben `ratio_similitud` ni `sospechoso`: no hubo búsqueda ni
  `elige_candidato`, así que no hay ratio que reportar. `_confianza`
  (`enlaza_scjn_legislacion.py`) los deja en `null` cuando faltan, que es
  exactamente la semántica correcta.
- `motivo` documenta la excepción dentro del propio archivo, sin que haya que
  ir a leer el issue.

**`indice.json`.** Se escribe con la misma forma que produce
`enlaza_scjn_legislacion.py` —mismas claves, mismo orden— pero con la
información derivada del codNota, que ya se conoce, en vez de inferida:

    [
      {
        "archivo": "09-04-2026.md",
        "fecha_publicacion": "09-04-2026",
        "codNota": 5784517,
        "ratio_similitud": null,
        "sospechoso": null,
        "title_candidates": [5784517],
        "title_link_status": "linked",
        "content_diff_confirmed_codNota": null,
        "content_diff_score": null
      }
    ]

`title_link_status: "linked"` porque el enlace es directo y por construcción,
no probabilístico; `content_diff_*` en `null` porque el diff de contenido
(#127) compara un snapshot contra el anterior, y aquí no hay anterior.

**Orden de operaciones.** Correr `enlaza_scjn_legislacion.py` después de este
script **sobrescribiría** este `indice.json`: el directorio ya existiría y
entraría al barrido normal. Ese barrido produciría exactamente la misma
entrada —el título de la nota nombra la ley, y es el único candidato de ese
día, así que `title_candidates_por_fecha` + `enlaza_por_titulo` llegan al
mismo `codNota` 5784517 con `title_link_status: "linked"`— pero eso es una
coincidencia afortunada, no una garantía. La regla es: **este script se corre
después de `enlaza_scjn_legislacion.py`**, y es el que deja la última palabra
sobre `scripts/scjn/leyes/lfiiedb/indice.json`.

**Cuándo desaparece esta excepción.** Cuando la SCJN indice la ley: se borra
el directorio, se corre ``fetch_scjn_legislacion.py --reintenta lfiiedb``
seguido de ``enlaza_scjn_legislacion.py``, y este script se retira.
"""

import argparse
import json
import sys
from pathlib import Path

# Run straight from a clone, without `pip install -e packages/nota2md` first.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "nota2md"))

from nota2md.builder import fetch_nota, legal_provisions  # noqa: E402

#: Diputados' own name for this instrument, and the directory
#: `slug_instrumento` would pick for it.
NOMBRE_CATALOGO = (
    "LEY para el Fomento de la Inversión en Infraestructura Estratégica "
    "para el Desarrollo con Bienestar"
)
ABREV = "lfiiedb"
#: The DOF note that enacted it (09-04-2026).
COD_NOTA = 5784517
MOTIVO = (
    "la SCJN no tiene esta ley indexada (issue #124); "
    "texto tomado directamente del DOF"
)


def cabecera(nota: dict) -> str:
    """The provenance header for a snapshot whose origin is the DOF and not
    the SCJN — same shape `nota2md.scjn._cabecera` writes (so `lee_cabecera`
    and `versiones_de_directorio` read it back unchanged), declaring
    `fuente: dof` and carrying the `codNota` it is known by from the origin.
    No `ratio_similitud`/`sospechoso`: there was no search to report one for."""
    return "\n".join(
        [
            "---",
            "fuente: dof",
            f"codNota: {COD_NOTA}",
            f"nombre_buscado: {NOMBRE_CATALOGO}",
            f"ordenamiento: {nota['titulo']}",
            f"fecha_publicacion: {nota['fecha']}",
            "categoria: DECRETO",
            f"motivo: {MOTIVO}",
            "---",
        ]
    )


def escribe_ley(destino: Path) -> tuple[Path, str]:
    """The law's own Markdown, from the DOF's HTML path, written as
    ``<destino>/<fecha_publicacion>.md`` — the same naming
    `descarga_ordenamiento` uses. Returns that file and its publication
    date."""
    nota = fetch_nota(COD_NOTA)
    fecha = nota["fecha"]
    destino.mkdir(parents=True, exist_ok=True)
    archivo = destino / f"{fecha}.md"

    temporal = legal_provisions(COD_NOTA, destino / "_dof", source="html", nota=nota)
    markdown = temporal.read_text(encoding="utf-8")
    archivo.write_text(f"{cabecera(nota)}\n\n{markdown}", encoding="utf-8")
    temporal.unlink()
    try:
        (destino / "_dof").rmdir()
    except OSError:
        pass
    return archivo, fecha


def escribe_indice(destino: Path, archivo: Path, fecha: str) -> list[dict]:
    """`enlaza_scjn_legislacion.py`'s own `indice.json` shape — same keys,
    same order — with everything that script infers already known here by
    construction. See this module's docstring for why each field takes the
    value it does."""
    indice = [
        {
            "archivo": archivo.name,
            "fecha_publicacion": fecha,
            "codNota": COD_NOTA,
            "ratio_similitud": None,
            "sospechoso": None,
            "title_candidates": [COD_NOTA],
            "title_link_status": "linked",
            "content_diff_confirmed_codNota": None,
            "content_diff_score": None,
        }
    ]
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
    args = p.parse_args(argv)

    destino = args.outdir / "leyes" / ABREV
    archivo, fecha = escribe_ley(destino)
    print(f"{ABREV}: {archivo} (DOF codNota {COD_NOTA}, {fecha})", file=sys.stderr)
    escribe_indice(destino, archivo, fecha)
    print(f"{ABREV}: 1/1 enlazadas (1 del DOF, 0 de la SCJN)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
