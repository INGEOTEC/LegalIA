"""dofjson (the package) is meant to be the ONE entry point for every piece
of SIDOF/dofweb functionality (issue #104): no other function, in this
package or another one (nota2md, leyesmx...), should need to import
dofjson.sidof or dofjson.dofweb directly. These tests lock that surface in,
so a future addition to sidof/dofweb that forgets to re-export it here
doesn't quietly reintroduce the need to reach into a submodule."""
import unittest

import dofjson
from dofjson import api, dofweb, notas, sidof


class TestUnifiedEntryPoints(unittest.TestCase):
    """get_nota/get_notas are real unifications (SIDOF-then-dofweb fallback,
    see dofjson.api) -- not passthroughs, so they are checked separately."""

    def test_get_nota_and_get_notas_are_dofjson_apis_own(self):
        self.assertIs(dofjson.get_nota, api.get_nota)
        self.assertIs(dofjson.get_notas, api.get_notas)


class TestApiDownloadEntryPoints(unittest.TestCase):
    """download_nota()/download_nota_imagenes()/download_nota_pdf()/
    download_nota_imagen_o_pdf() are defined in dofjson.api itself, not
    dofjson.sidof -- resolving a bare codNota still needs get_nota()'s own
    SIDOF-then-dofweb fallback (to raise a clear error for a dofweb-only
    note instead of crashing later), and dofjson.sidof has no business
    calling back into dofjson.api to get that -- see dofjson.api's own
    module docstring."""

    NOMBRES = (
        "download_nota",
        "download_nota_imagenes",
        "download_nota_pdf",
        "download_nota_imagen_o_pdf",
        "download_edicion_pdf",
    )

    def test_every_one_is_the_api_function_itself(self):
        for nombre in self.NOMBRES:
            with self.subTest(nombre=nombre):
                self.assertIs(getattr(dofjson, nombre), getattr(api, nombre))

    def test_dofjson_sidof_does_not_define_any_of_them(self):
        for nombre in self.NOMBRES:
            with self.subTest(nombre=nombre):
                self.assertFalse(hasattr(sidof, nombre))


class TestSidofPassthroughEntryPoints(unittest.TestCase):
    """These have no dofweb equivalent to fall back to at all, so dofjson
    re-exports dofjson.sidof's own function object directly."""

    PASSTHROUGHS = (
        "get_diario",
        "get_indicadores",
        "get_imagenes",
        "download_pdf",
        "download_imagen",
    )

    def test_every_passthrough_is_the_sidof_function_itself(self):
        for nombre in self.PASSTHROUGHS:
            with self.subTest(nombre=nombre):
                self.assertIs(getattr(dofjson, nombre), getattr(sidof, nombre))


class TestNotasPassthroughEntryPoints(unittest.TestCase):
    """infer_paginas/quita_notas_sin_titulo are pure, source-agnostic helpers
    that live in dofjson.notas (not dofjson.sidof) -- see its docstring."""

    PASSTHROUGHS = ("infer_paginas", "quita_notas_sin_titulo", "notas_del_dia")

    def test_every_passthrough_is_the_notas_function_itself(self):
        for nombre in self.PASSTHROUGHS:
            with self.subTest(nombre=nombre):
                self.assertIs(getattr(dofjson, nombre), getattr(notas, nombre))


class TestDofjsonSurface(unittest.TestCase):
    def test_fuente_web_is_dofwebs_own_constant(self):
        self.assertEqual(dofjson.FUENTE_WEB, dofweb.FUENTE)

    def test_all_lists_exactly_the_exported_names(self):
        esperado = {
            "get_nota", "get_notas", "fetch_daily_legal_provisions", "FUENTE_WEB",
            "download_dof_assets", "iterador_de_assets",
            "legal_provisions_titles", "organigrama",
            "notas_de_tgz",
            *TestApiDownloadEntryPoints.NOMBRES,
            *TestSidofPassthroughEntryPoints.PASSTHROUGHS,
            *TestNotasPassthroughEntryPoints.PASSTHROUGHS,
        }
        self.assertEqual(set(dofjson.__all__), esperado)


if __name__ == "__main__":
    unittest.main()
