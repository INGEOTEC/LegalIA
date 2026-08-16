"""dofjson (the package) is meant to be the ONE entry point for every piece
of SIDOF/dofweb functionality (issue #104): no other function, in this
package or another one (nota2md, leyesmx...), should need to import
dofjson.client or dofjson.dofweb directly. These tests lock that surface in,
so a future addition to client/dofweb that forgets to re-export it here
doesn't quietly reintroduce the need to reach into a submodule."""
import unittest

import dofjson
from dofjson import api, client, dofweb


class TestUnifiedEntryPoints(unittest.TestCase):
    """get_nota/get_notas are real unifications (SIDOF-then-dofweb fallback,
    see dofjson.api) -- not passthroughs, so they are checked separately."""

    def test_get_nota_and_get_notas_are_dofjson_apis_own(self):
        self.assertIs(dofjson.get_nota, api.get_nota)
        self.assertIs(dofjson.get_notas, api.get_notas)


class TestPassthroughEntryPoints(unittest.TestCase):
    """Every other function has no dofweb equivalent to fall back to, so
    dofjson re-exports dofjson.client's own function object directly."""

    PASSTHROUGHS = (
        "get_diario",
        "get_indicadores",
        "get_imagenes",
        "download_pdf",
        "download_imagen",
        "download_nota",
        "download_nota_imagenes",
        "download_nota_pdf",
        "download_nota_imagen_o_pdf",
        "infer_paginas",
        "quita_notas_sin_titulo",
    )

    def test_every_passthrough_is_the_client_function_itself(self):
        for nombre in self.PASSTHROUGHS:
            with self.subTest(nombre=nombre):
                self.assertIs(getattr(dofjson, nombre), getattr(client, nombre))

    def test_fuente_web_is_dofwebs_own_constant(self):
        self.assertEqual(dofjson.FUENTE_WEB, dofweb.FUENTE)

    def test_all_lists_exactly_the_exported_names(self):
        esperado = {"get_nota", "get_notas", "FUENTE_WEB", *self.PASSTHROUGHS}
        self.assertEqual(set(dofjson.__all__), esperado)


if __name__ == "__main__":
    unittest.main()
