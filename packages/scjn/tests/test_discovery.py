"""Unit tests for `scjn.discovery` (issue #222's Fase 0) -- every pass
exercised against a stub API, no network involved."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scjn import discovery
from scjn.api import Ordenamiento, Reforma


class FakeApi:
    """A minimal `ScjnApi` stand-in: `resultados` maps
    `(frase, categoria)` to a page-1 list of `Ordenamiento`s (never more than
    one page in these tests -- pagination itself is tested separately with
    `PagedFakeApi`); `reformas` maps `id_ordenamiento` to its `Reforma` list.
    Counts every `reformas_of_ordenamiento` call so the coverage-audit cache
    tests can assert it is not called twice for the same id."""

    def __init__(self, resultados: dict, reformas: dict):
        self.resultados = resultados
        self.reformas = reformas
        self.llamadas_reformas: list[str] = []
        self.busquedas: list[tuple] = []

    def search_ordenamiento(self, frase, *, tamanio_pagina, categoria, ambito, pagina):
        if pagina > 1:
            return []
        self.busquedas.append((frase, categoria, ambito))
        # A `(frase, categoria, ambito)` key (issue #277) wins over the
        # ambito-less `(frase, categoria)` one every earlier test uses.
        return self.resultados.get((frase, categoria, ambito), self.resultados.get((frase, categoria), []))

    def reformas_of_ordenamiento(self, id_ordenamiento):
        self.llamadas_reformas.append(str(id_ordenamiento))
        return self.reformas.get(str(id_ordenamiento), [])


class PagedFakeApi:
    """A stub whose single `(frase, categoria)` key answers multiple pages,
    to exercise `pagina_categoria`'s own stop condition."""

    def __init__(self, paginas: list[list[Ordenamiento]]):
        self.paginas = paginas

    def search_ordenamiento(self, frase, *, tamanio_pagina, categoria, ambito, pagina):
        if pagina - 1 < len(self.paginas):
            return self.paginas[pagina - 1]
        return []


def _hit(id_ordenamiento, nombre, categoria="LINEAMIENTOS", ambito=None):
    return Ordenamiento(
        idOrdenamiento=id_ordenamiento, ordenamiento=nombre,
        categoriaOrdenamiento=categoria, ambito=ambito,
    )


class TestPaginaCategoria(unittest.TestCase):
    def test_para_de_paginar_en_la_pagina_corta(self):
        completa = [_hit(str(i), f"n{i}") for i in range(2)]
        api = PagedFakeApi([completa, completa, [_hit("99", "ultimo")]])
        # tamanio_pagina=2 matches `completa`'s length, so pages 1 and 2 are
        # both "full" and page 3 (length 1) is what stops the walk.
        resultado = list(discovery.pagina_categoria(api, "q", "CAT", tamanio_pagina=2))
        self.assertEqual(len(resultado), 5)


class TestDiscoverByCategory(unittest.TestCase):
    def test_une_varias_frases_sin_duplicar(self):
        api = FakeApi(
            resultados={
                ("lineamientos", "LINEAMIENTOS"): [_hit("1", "A"), _hit("2", "B")],
                ("de", "LINEAMIENTOS"): [_hit("2", "B"), _hit("3", "C")],
            },
            reformas={},
        )
        hallados = discovery.discover_by_category(api, "LINEAMIENTOS", ("lineamientos", "de"))
        self.assertEqual(set(hallados), {"1", "2", "3"})


class TestCandidatesOutsideCategory(unittest.TestCase):
    def test_excluye_ya_incluidos(self):
        api = FakeApi(
            resultados={("lineamientos", ""): [_hit("1", "A"), _hit("2", "B", categoria="ACUERDO (S)")]},
            reformas={},
        )
        otros = discovery.candidates_outside_category(api, "lineamientos", {"1": _hit("1", "A")})
        self.assertEqual(set(otros), {"2"})


class TestRescueByReformCategory(unittest.TestCase):
    def test_rescata_solo_con_fila_de_la_categoria_objetivo(self):
        api = FakeApi(
            resultados={},
            reformas={
                "1": [Reforma(reformaId=1, fecha_publicacion="01-01-2020", categoria="LINEAMIENTOS")],
                "2": [Reforma(reformaId=2, fecha_publicacion="01-01-2020", categoria="ACUERDO (S)")],
            },
        )
        candidatos = {"1": _hit("1", "A", categoria="ACUERDO (S)"), "2": _hit("2", "B", categoria="ACUERDO (S)")}
        rescatados = discovery.rescue_by_reform_category(api, candidatos, "LINEAMIENTOS")
        self.assertEqual(set(rescatados), {"1"})


class TestCoverageAudit(unittest.TestCase):
    def _api(self):
        return FakeApi(
            resultados={("acuerdo", "ACUERDO"): [_hit("9", "X", categoria="ACUERDO")]},
            reformas={"9": [Reforma(reformaId=1, fecha_publicacion="01-01-2020", categoria="LINEAMIENTOS")]},
        )

    def test_rescata_y_cachea_en_disco(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            api = self._api()
            rescatados = discovery.coverage_audit(
                api, set(), "LINEAMIENTOS", (("acuerdo", "ACUERDO"),), cache_dir=cache_dir,
            )
            self.assertEqual(set(rescatados), {"9"})
            self.assertTrue((cache_dir / "auditoria_cobertura.json.gz").exists())
            self.assertEqual(api.llamadas_reformas, ["9"])

    def test_una_segunda_corrida_reusa_el_cache_sin_llamar_a_la_scjn(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            discovery.coverage_audit(
                self._api(), set(), "LINEAMIENTOS", (("acuerdo", "ACUERDO"),), cache_dir=cache_dir,
            )
            api2 = self._api()
            rescatados = discovery.coverage_audit(
                api2, set(), "LINEAMIENTOS", (("acuerdo", "ACUERDO"),), cache_dir=cache_dir,
            )
            self.assertEqual(set(rescatados), {"9"})
            self.assertEqual(api2.llamadas_reformas, [])

    def test_una_coleccion_distinta_reaplica_su_propio_predicado_offline(self):
        """The cache stores each swept instrument's raw reform table, not a
        yes/no verdict -- a later collection's own `categoria` gets applied
        against the same cached rows without another SCJN request."""
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            discovery.coverage_audit(
                self._api(), set(), "LINEAMIENTOS", (("acuerdo", "ACUERDO"),), cache_dir=cache_dir,
            )
            api2 = self._api()
            rescatados_otra_categoria = discovery.coverage_audit(
                api2, set(), "REGLAMENTO", (("acuerdo", "ACUERDO"),), cache_dir=cache_dir,
            )
            self.assertEqual(rescatados_otra_categoria, {})
            self.assertEqual(api2.llamadas_reformas, [])

    def test_sin_cache_dir_no_escribe_nada(self):
        rescatados = discovery.coverage_audit(
            self._api(), set(), "LINEAMIENTOS", (("acuerdo", "ACUERDO"),), cache_dir=None,
        )
        self.assertEqual(set(rescatados), {"9"})


class TestDiscover(unittest.TestCase):
    def test_pipeline_sin_auditoria(self):
        api = FakeApi(
            resultados={
                ("lineamientos", "LINEAMIENTOS"): [_hit("1", "LINEAMIENTOS A")],
                ("lineamientos", ""): [
                    _hit("1", "LINEAMIENTOS A"),
                    _hit("2", "MANUAL DE LINEAMIENTOS B", categoria="MANUAL"),
                ],
            },
            reformas={
                "1": [Reforma(reformaId=1, fecha_publicacion="01-01-2020", tieneArticulos=True)],
                "2": [Reforma(reformaId=2, fecha_publicacion="01-01-2020", categoria="LINEAMIENTOS",
                               tieneArticulos=False)],
            },
        )
        candidatos = discovery.discover(
            api, categoria="LINEAMIENTOS", frases_union=("lineamientos",),
            frase_rescate="lineamientos", log=lambda *_a, **_k: None,
        )
        self.assertEqual({c["id_ordenamiento"] for c in candidatos}, {"1", "2"})
        por_id = {c["id_ordenamiento"]: c for c in candidatos}
        self.assertEqual(por_id["1"]["reformas_con_texto"], 1)
        self.assertEqual(por_id["2"]["reformas_con_texto"], 0)

    def test_pipeline_con_auditoria_cobertura(self):
        with TemporaryDirectory() as tmp:
            api = FakeApi(
                resultados={
                    ("lineamientos", "LINEAMIENTOS"): [],
                    ("lineamientos", ""): [],
                    ("acuerdo", "ACUERDO"): [_hit("9", "X", categoria="ACUERDO")],
                },
                reformas={
                    "9": [Reforma(reformaId=1, fecha_publicacion="01-01-2020", categoria="LINEAMIENTOS")],
                },
            )
            candidatos = discovery.discover(
                api, categoria="LINEAMIENTOS", frases_union=("lineamientos",),
                frase_rescate="lineamientos", auditoria_cobertura=True,
                frases_auditoria=(("acuerdo", "ACUERDO"),), cache_dir=Path(tmp),
                log=lambda *_a, **_k: None,
            )
            self.assertEqual({c["id_ordenamiento"] for c in candidatos}, {"9"})

    def test_auditoria_cobertura_sin_frases_es_un_error(self):
        api = FakeApi(resultados={}, reformas={})
        with self.assertRaises(ValueError):
            discovery.discover(
                api, categoria="LINEAMIENTOS", frases_union=("lineamientos",),
                frase_rescate="lineamientos", auditoria_cobertura=True,
                log=lambda *_a, **_k: None,
            )


AMBITO_TRATADOS = "TRATADOS INTERNACIONALES"


class TestDiscoverPorAmbito(unittest.TestCase):
    """`discover(..., categoria="", ambito=...)` (issue #277): `scjn-tratados`
    is a member by ambito, any category -- the whole ambito is paged, and
    neither the reform-category rescue nor the coverage audit runs."""

    def _api(self):
        return FakeApi(
            resultados={
                ("tratado", "", AMBITO_TRATADOS): [
                    _hit("1", "CONVENIO A", categoria="CONVENIO", ambito=AMBITO_TRATADOS),
                    _hit("2", "ACUERDO B", categoria="ACUERDO (S)", ambito=AMBITO_TRATADOS),
                ],
                ("de", "", AMBITO_TRATADOS): [
                    _hit("2", "ACUERDO B", categoria="ACUERDO (S)", ambito=AMBITO_TRATADOS),
                    _hit("3", "CODIGO C", categoria="CODIGO", ambito=AMBITO_TRATADOS),
                ],
            },
            reformas={
                "1": [Reforma(reformaId=1, fecha_publicacion="01-01-2020", tieneArticulos=True)],
                "2": [
                    Reforma(reformaId=2, fecha_publicacion="01-01-2020", tieneArticulos=False),
                    Reforma(reformaId=3, fecha_publicacion="01-01-2021", tieneArticulos=True),
                ],
            },
        )

    def test_pagina_el_ambito_sin_filtro_de_categoria(self):
        api = self._api()

        candidatos = discovery.discover(
            api, categoria="", ambito=AMBITO_TRATADOS, frases_union=("tratado", "de"),
            log=lambda *_a, **_k: None,
        )

        self.assertEqual({c["id_ordenamiento"] for c in candidatos}, {"1", "2", "3"})
        self.assertEqual(
            api.busquedas, [("tratado", "", AMBITO_TRATADOS), ("de", "", AMBITO_TRATADOS)]
        )

    def test_cada_candidato_lleva_su_ambito_y_cualquier_categoria_entra(self):
        candidatos = discovery.discover(
            self._api(), categoria="", ambito=AMBITO_TRATADOS, frases_union=("tratado", "de"),
            log=lambda *_a, **_k: None,
        )

        self.assertEqual({c["ambito"] for c in candidatos}, {AMBITO_TRATADOS})
        self.assertEqual(
            {c["categoria_ordenamiento"] for c in candidatos}, {"CONVENIO", "ACUERDO (S)", "CODIGO"}
        )

    def test_no_aplica_rescate_ni_auditoria_solo_cuenta_reformas_por_candidato(self):
        api = self._api()

        discovery.discover(
            api, categoria="", ambito=AMBITO_TRATADOS, frases_union=("tratado", "de"),
            log=lambda *_a, **_k: None,
        )

        # One `reformas_of_ordenamiento` per candidate (the reform count) and
        # nothing else: no rescue table fetches, no sweep.
        self.assertEqual(sorted(api.llamadas_reformas), ["1", "2", "3"])

    def test_auditoria_de_cobertura_no_aplica_por_ambito(self):
        with self.assertRaises(ValueError):
            discovery.discover(
                self._api(), categoria="", ambito=AMBITO_TRATADOS, frases_union=("tratado",),
                auditoria_cobertura=True, frases_auditoria=(("ley", "LEY"),),
                log=lambda *_a, **_k: None,
            )

    def test_el_default_federal_se_comporta_como_antes(self):
        api = FakeApi(
            resultados={("lineamientos", "LINEAMIENTOS"): [_hit("1", "A")], ("lineamientos", ""): []},
            reformas={"1": []},
        )

        candidatos = discovery.discover(
            api, categoria="LINEAMIENTOS", frases_union=("lineamientos",),
            frase_rescate="lineamientos", log=lambda *_a, **_k: None,
        )

        self.assertEqual([c["id_ordenamiento"] for c in candidatos], ["1"])
        self.assertIn(("lineamientos", "LINEAMIENTOS", "FEDERAL"), api.busquedas)


class TestReportOutsideAmbito(unittest.TestCase):
    def test_lista_solo_lo_de_fuera_con_categoria_de_tratado_y_ordenado(self):
        api = FakeApi(
            resultados={
                ("tratado", "", ""): [
                    _hit("1", "DENTRO", categoria="TRATADO", ambito=AMBITO_TRATADOS),
                    _hit("2", "ZETA ESTATAL", categoria="TRATADO", ambito="ESTATAL"),
                    _hit("3", "LEY SOBRE TRATADOS", categoria="LEY", ambito="FEDERAL"),
                    _hit("4", "ALFA FEDERAL", categoria="convenio", ambito="FEDERAL"),
                ],
                ("convenio", "", ""): [
                    _hit("2", "ZETA ESTATAL", categoria="TRATADO", ambito="ESTATAL"),
                ],
            },
            reformas={},
        )

        fuera = discovery.report_outside_ambito(
            api, AMBITO_TRATADOS, ("TRATADO", "CONVENIO"), ("tratado", "convenio")
        )

        self.assertEqual([h.idOrdenamiento for h in fuera], ["4", "2"])
        # It pages across every ambito and category, never the collection's.
        self.assertEqual({(c, a) for _, c, a in api.busquedas}, {("", "")})

    def test_nunca_llama_a_la_tabla_de_reformas(self):
        api = FakeApi(
            resultados={("tratado", "", ""): [_hit("2", "X", categoria="TRATADO", ambito="ESTATAL")]},
            reformas={},
        )

        discovery.report_outside_ambito(api, AMBITO_TRATADOS, ("TRATADO",), ("tratado",))

        self.assertEqual(api.llamadas_reformas, [])


if __name__ == "__main__":
    unittest.main()
