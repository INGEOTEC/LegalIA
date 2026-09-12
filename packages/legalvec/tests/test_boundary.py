"""This package depends on nothing else in this monorepo.

The same guard `packages/scjn/tests/test_boundary.py` puts on `scjn`, for the
same reason: `legalvec` reads a release of *this project's own* derived data
and needs `pyarrow`/`numpy` to do it — it is not a client for the SCJN, and
it must not grow into one. A grep is enough; an import-time check would only
notice a dependency that is actually reached at import.
"""

import pathlib
import unittest

FUENTE = pathlib.Path(__file__).resolve().parents[1] / "legalvec"


class TestSinDependenciasDelMonorepo(unittest.TestCase):
    def test_no_importa_scjn_nota2md_dofjson_ni_md2akn(self):
        ofensas = []
        for ruta in sorted(FUENTE.rglob("*.py")):
            texto = ruta.read_text(encoding="utf-8")
            for linea in texto.splitlines():
                limpia = linea.strip()
                if not (limpia.startswith("import ") or limpia.startswith("from ")):
                    continue
                for paquete in ("scjn", "nota2md", "dofjson", "md2akn", "dof2md"):
                    if limpia.startswith(f"import {paquete}") or limpia.startswith(f"from {paquete}"):
                        ofensas.append(f"{ruta.name}: {limpia}")
        self.assertEqual(ofensas, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
