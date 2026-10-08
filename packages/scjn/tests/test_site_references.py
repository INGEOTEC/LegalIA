"""Audit of the repository for references to the SCJN's retired WebForms
*Buscador* (issue #277).

The SCJN's current legislation site is `legislacion.scjn.gob.mx/consulta`
(the page headed "La clave de la Corte"), read through the SCOW JSON backend
`scjn.api` talks to; the legacy WebForms crawler behind
`/Buscador/` path of the same host was retired in issue #179. A link left
pointing at the old site is a live link to a dead page, so this test fails on
any occurrence of that URL anywhere in the tree except the two frozen
history files that describe the migration.

Historical *narrative* that says the old path was replaced does not name the
URL (or lives in the exempt files) and is not touched by this test.

It needs the whole monorepo checkout: a `scjn` tested outside it (from an
sdist, say) has no `packages/`/`website/` next to it, and the test skips with
that reason rather than passing vacuously.
"""

import functools
import os
import unittest
from pathlib import Path

import scjn.api

RAIZ = Path(__file__).resolve().parents[3]

#: Built from pieces so this file does not itself contain the string it
#: forbids.
URL_PROHIBIDA = "legislacion.scjn.gob.mx/" + "Buscador"

SUFIJOS = {".py", ".md", ".rst", ".qmd", ".ipynb", ".js", ".json", ".yml", ".toml"}

#: Directories never walked: version control, environments, rendered site
#: output and Quarto's own index cache, and gitignored scratch (downloaded
#: corpora, run directories).
DIRECTORIOS_EXCLUIDOS = (".git", ".venv", "output", "scripts/scjn", "website/_site")
PREFIJOS_EXCLUIDOS = ("emb-run",)
NOMBRES_EXCLUIDOS = (".quarto", "node_modules", "__pycache__")

#: Frozen history, kept verbatim on purpose (issue #277's decision).
ARCHIVOS_EXENTOS = {
    ".github/migracion-diputados.md",
    ".github/historial-legislativo.md",
}


def _es_excluido(relativa: Path) -> bool:
    texto = relativa.as_posix()
    if texto in ARCHIVOS_EXENTOS:
        return True
    if any(texto == d or texto.startswith(d + "/") for d in DIRECTORIOS_EXCLUIDOS):
        return True
    if any(parte in NOMBRES_EXCLUIDOS for parte in relativa.parts):
        return True
    return any(parte.startswith(PREFIJOS_EXCLUIDOS) for parte in relativa.parts[:1])


@functools.cache
def _archivos_a_auditar():
    """Every `(relative path, path)` to audit (memoized). Walks with pruning: `.venv/`,
    `scripts/scjn/` and the `emb-run*/` work directories hold hundreds of
    thousands of files that must never be listed, let alone read."""
    encontrados = []
    for directorio, subdirectorios, archivos in os.walk(RAIZ):
        base = Path(directorio)
        subdirectorios[:] = sorted(
            d for d in subdirectorios if not _es_excluido((base / d).relative_to(RAIZ))
        )
        for nombre in sorted(archivos):
            ruta = base / nombre
            if ruta.suffix not in SUFIJOS or not ruta.is_file():
                continue
            relativa = ruta.relative_to(RAIZ)
            if not _es_excluido(relativa):
                encontrados.append((relativa, ruta))
    return tuple(encontrados)


@unittest.skipUnless(
    (RAIZ / "packages").is_dir() and (RAIZ / "website").is_dir(),
    "needs the whole monorepo checkout (packages/ and website/ next to this package)",
)
class TestNingunaReferenciaAlBuscadorRetirado(unittest.TestCase):
    def test_ningun_archivo_enlaza_el_sitio_retirado(self):
        encontrados = []
        for relativa, ruta in _archivos_a_auditar():
            try:
                texto = ruta.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if URL_PROHIBIDA in texto:
                encontrados.append(relativa.as_posix())

        self.assertEqual(
            encontrados, [],
            f"{URL_PROHIBIDA} is the retired WebForms site (issue #179); link "
            "https://legislacion.scjn.gob.mx/consulta/home instead",
        )

    def test_el_audit_recorre_el_arbol_y_respeta_las_exclusiones(self):
        auditados = {relativa.as_posix() for relativa, _ in _archivos_a_auditar()}

        self.assertIn("website/pages/leyes.ipynb", auditados)
        self.assertIn("packages/scjn/scjn/api.py", auditados)
        for exento in ARCHIVOS_EXENTOS:
            self.assertNotIn(exento, auditados)
        self.assertFalse(any(a.startswith(".venv/") for a in auditados))
        self.assertFalse(any(a.startswith("scripts/scjn/") for a in auditados))

    def test_la_pagina_de_leyes_enlaza_el_sitio_actual(self):
        for relativa in (
            "website/pages/leyes.ipynb",
            "website/_freeze/pages/leyes/execute-results/html.json",
        ):
            texto = (RAIZ / relativa).read_text(encoding="utf-8")
            self.assertIn("https://legislacion.scjn.gob.mx/consulta/home", texto, relativa)

    def test_el_audit_detecta_una_referencia_prohibida(self):
        # The needle is the host plus the retired path, so it matches the
        # retired site's URLs and nothing of the current one.
        self.assertIn(URL_PROHIBIDA, f"https://{URL_PROHIBIDA}/")
        self.assertNotIn(URL_PROHIBIDA, "https://legislacion.scjn.gob.mx/consulta/home")


class TestElClienteLeeElSitioActual(unittest.TestCase):
    """The audit also pins the *current* site: the SCOW backend `scjn.api`
    reads sits under the same host the retired Buscador did."""

    def test_base_url_es_el_backend_scow_de_legislacion_scjn(self):
        self.assertTrue(
            scjn.api.BASE_URL.startswith("https://legislacion.scjn.gob.mx/SCOW-API"),
            scjn.api.BASE_URL,
        )
        self.assertNotIn("Buscador", scjn.api.BASE_URL)


if __name__ == "__main__":
    unittest.main()
