import gzip
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nota2md.linking as linking
import scjn.header as header
import scjn.release as release


class TestTitleCandidatesPorFecha(unittest.TestCase):
    def test_agrupa_por_fecha_solo_los_codnota_cuyo_titulo_menciona_el_nombre(self):
        porf = {
            "22-01-1994": [
                {"codNota": 100, "titulo": "DECRETO que reforma la Ley Federal del Trabajo"},
                {"codNota": 999, "titulo": "DECRETO sobre otro asunto"},
            ],
        }

        agrupado = linking.title_candidates_por_fecha(
            ["22-01-1994"], "Ley Federal del Trabajo", porf
        )

        self.assertEqual(agrupado, {"22-01-1994": [100]})

    def test_una_fecha_sin_registros_en_porf_regresa_lista_vacia(self):
        agrupado = linking.title_candidates_por_fecha(["22-01-1994"], "Ley Federal del Trabajo", {})

        self.assertEqual(agrupado, {"22-01-1994": []})

    def test_no_repite_fechas_duplicadas_en_el_resultado(self):
        porf = {"14-06-2024": [{"codNota": 100, "titulo": "Ley de Amnistia"}]}

        agrupado = linking.title_candidates_por_fecha(
            ["14-06-2024", "14-06-2024"], "Ley de Amnistia", porf
        )

        self.assertEqual(list(agrupado.keys()), ["14-06-2024"])

    def test_recae_en_decreto_o_ley_del_dia_cuando_ninguna_mencion_explicita(self):
        # ccf's 14-11-2025: the reforming decree's own title never spells
        # out "Codigo Civil Federal" -- only its articulo primero does.
        porf = {
            "14-11-2025": [
                {
                    "codNota": 100,
                    "titulo": (
                        "DECRETO por el que se reforman diversas disposiciones de "
                        "diversos ordenamientos legales, en materia de homologacion "
                        "normativa relativa al Codigo Nacional de Procedimientos "
                        "Civiles y Familiares"
                    ),
                },
                {"codNota": 999, "titulo": "AVISO sobre otro asunto"},
            ],
        }

        agrupado = linking.title_candidates_por_fecha(["14-11-2025"], "Codigo Civil Federal", porf)

        self.assertEqual(agrupado, {"14-11-2025": [100]})

    def test_no_recae_en_el_respaldo_cuando_ya_hay_mencion_explicita(self):
        porf = {
            "22-01-1994": [
                {"codNota": 100, "titulo": "DECRETO que reforma la Ley Federal del Trabajo"},
                {"codNota": 200, "titulo": "LEY de otro ordenamiento"},
            ],
        }

        agrupado = linking.title_candidates_por_fecha(
            ["22-01-1994"], "Ley Federal del Trabajo", porf
        )

        self.assertEqual(agrupado, {"22-01-1994": [100]})

    def test_lista_vacia_cuando_tampoco_hay_decreto_o_ley_ese_dia(self):
        porf = {"14-11-2025": [{"codNota": 999, "titulo": "AVISO sobre otro asunto"}]}

        agrupado = linking.title_candidates_por_fecha(["14-11-2025"], "Codigo Civil Federal", porf)

        self.assertEqual(agrupado, {"14-11-2025": []})


class TestTituloEmpiezaConDecretoOLey(unittest.TestCase):
    def test_reconoce_decreto_case_insensible(self):
        self.assertTrue(linking._title_opens_with_decreto_or_ley("decreto por el que se reforma"))

    def test_reconoce_ley(self):
        self.assertTrue(linking._title_opens_with_decreto_or_ley("LEY de Amparo"))

    def test_no_reconoce_acuerdo(self):
        self.assertFalse(linking._title_opens_with_decreto_or_ley("ACUERDO por el que se emite"))

    def test_no_se_deja_enganar_por_una_palabra_que_solo_empieza_igual(self):
        # "LEYES" no es "LEY" -- el limite de palabra evita el falso positivo.
        self.assertFalse(linking._title_opens_with_decreto_or_ley("LEYES secundarias"))


class TestEnlazaPorTitulo(unittest.TestCase):
    def _version(self, fecha: str, nombre: str = None) -> header.VersionInstrumento:
        return header.VersionInstrumento(fecha, Path(nombre or f"{fecha}.md"))

    def test_enlaza_cuando_hay_exactamente_un_candidato_esa_fecha(self):
        versiones = [self._version("22-01-1994"), self._version("14-06-2024")]
        candidatos_por_fecha = {"22-01-1994": [100], "14-06-2024": [200]}

        enlazadas = linking.enlaza_por_titulo(versiones, candidatos_por_fecha)

        self.assertEqual([v.codNota for v in enlazadas], [100, 200])

    def test_deja_sin_enlazar_una_fecha_sin_candidato(self):
        versiones = [self._version("22-01-1994")]

        enlazadas = linking.enlaza_por_titulo(versiones, {})

        self.assertIsNone(enlazadas[0].codNota)

    def test_deja_sin_enlazar_una_fecha_con_varios_candidatos_ambiguos(self):
        # Sin historial que desempate, el titulo solo no puede elegir entre
        # varios candidatos del mismo dia — issue #127's content diff es lo
        # unico que puede resolver este caso.
        versiones = [self._version("22-01-1994")]
        candidatos_por_fecha = {"22-01-1994": [100, 200]}

        enlazadas = linking.enlaza_por_titulo(versiones, candidatos_por_fecha)

        self.assertIsNone(enlazadas[0].codNota)

    def test_el_primer_snapshot_del_dia_reclama_el_unico_candidato_y_el_segundo_queda_sin_enlazar(
        self,
    ):
        # Dos snapshots de un mismo dia, pero el titulo solo revela un
        # candidato para esa fecha: solo el primero (oldest-first) lo
        # reclama, el segundo se queda sin enlazar en vez de reclamarlo
        # tambien.
        versiones = [
            self._version("14-06-2024", "14-06-2024.md"),
            self._version("14-06-2024", "14-06-2024-2.md"),
        ]
        candidatos_por_fecha = {"14-06-2024": [100]}

        enlazadas = linking.enlaza_por_titulo(versiones, candidatos_por_fecha)

        self.assertEqual([v.codNota for v in enlazadas], [100, None])


class TestTitleLinkStatus(unittest.TestCase):
    def test_enlazado_cuando_hay_codnota(self):
        self.assertEqual(linking.title_link_status(100, [100]), "linked")

    def test_ninguno_sin_candidatos(self):
        self.assertEqual(linking.title_link_status(None, []), "none")

    def test_reclamado_cuando_el_unico_candidato_ya_fue_tomado(self):
        self.assertEqual(linking.title_link_status(None, [100]), "claimed")

    def test_ambiguo_con_varios_candidatos(self):
        self.assertEqual(linking.title_link_status(None, [100, 200]), "ambiguous")


class TestTitleMentionsName(unittest.TestCase):
    def test_reconoce_mencion_explicita_case_e_acento_insensible(self):
        self.assertTrue(
            linking._title_mentions_name(
                "Ley Federal del Trabajo",
                "DECRETO por el que se reforma la ley federal del trabajo",
            )
        )

    def test_no_reconoce_una_ley_distinta(self):
        self.assertFalse(
            linking._title_mentions_name(
                "Ley Federal del Trabajo",
                "DECRETO por el que se reforma el Codigo Fiscal de la Federacion",
            )
        )

    def test_no_se_deja_engañar_por_palabras_cortas_compartidas(self):
        # "Ley"/"del"/"de" son demasiado cortas para contar por si solas.
        self.assertFalse(
            linking._title_mentions_name(
                "Ley Federal del Trabajo",
                "DECRETO por el que se reforma la Ley de Amparo",
            )
        )

    def test_exige_todas_las_palabras_significativas_del_nombre(self):
        self.assertFalse(
            linking._title_mentions_name(
                "Ley Federal de los Derechos del Contribuyente",
                "DECRETO por el que se reforma la Ley Federal del Trabajo",
            )
        )

    def test_no_confirma_con_una_sola_palabra_significativa_aunque_aparezca(self):
        # "LEY de Amparo" solo deja "Amparo" tras el filtro de palabras
        # cortas — una sola palabra, por comun que sea en textos legales,
        # no basta como mencion explicita: cualquier decreto que la use de
        # paso convertiria esto en una busqueda de palabra clave, no en una
        # mencion del ordenamiento.
        self.assertFalse(
            linking._title_mentions_name(
                "Ley de Amparo",
                "DECRETO por el que se reforma el Reglamento de la Ley de Amparo",
            )
        )


class TestAddedBlocksYOverlapScore(unittest.TestCase):
    def test_detecta_un_parrafo_nuevo_entre_dos_versiones(self):
        anterior = "Articulo 1.- Texto original.\n\nArticulo 2.- Otro texto."
        nuevo = "Articulo 1.- Texto original.\n\nArticulo 2.- Texto modificado por la reforma."

        agregados = linking._added_blocks(anterior, nuevo)

        self.assertEqual(len(agregados), 1)
        self.assertIn("modificado", agregados[0])

    def test_no_marca_nada_agregado_cuando_las_versiones_son_iguales(self):
        texto = "Articulo 1.- Texto original.\n\nArticulo 2.- Otro texto."

        self.assertEqual(linking._added_blocks(texto, texto), [])

    def test_score_es_uno_cuando_el_candidato_cubre_todo_lo_agregado(self):
        agregados = ["articulo 2 texto modificado por la reforma"]
        candidato = "decreto que reforma el articulo 2 texto modificado por la reforma"

        self.assertEqual(linking._overlap_score(agregados, candidato), 1.0)

    def test_score_es_cero_sin_relacion_alguna(self):
        agregados = ["articulo 2 texto modificado por la reforma"]
        candidato = "decreto sobre un asunto completamente distinto"

        self.assertEqual(linking._overlap_score(agregados, candidato), 0.0)

    def test_score_es_cero_sin_nada_agregado(self):
        self.assertEqual(linking._overlap_score([], "cualquier texto"), 0.0)

    def test_distingue_candidatos_que_solo_difieren_en_un_numero_corto(self):
        # Una reforma que solo cambia una tasa/monto/plazo corto (menos de 4
        # digitos) no debe volverse invisible para el score.
        agregados = ["se establece una tasa de 20 por ciento"]
        candidato_correcto = "decreto que fija la tasa en 20 por ciento"
        candidato_equivocado = "decreto que fija la tasa en 15 por ciento"

        score_correcto = linking._overlap_score(agregados, candidato_correcto)
        score_equivocado = linking._overlap_score(agregados, candidato_equivocado)

        self.assertGreater(score_correcto, score_equivocado)


class TestConfirmByContentDiff(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _snapshot(self, fecha: str, cuerpo: str, sufijo: str = "") -> header.VersionInstrumento:
        archivo = self.outdir / f"{fecha}{sufijo}.md"
        archivo.write_text(f"---\nfecha_publicacion: {fecha}\n---\n\n{cuerpo}", encoding="utf-8")
        return header.VersionInstrumento(fecha, archivo)

    def test_la_primera_version_no_tiene_confirmacion_por_no_tener_version_previa(self):
        versiones = [self._snapshot("22-01-1994", "Articulo 1.- Texto original.")]

        resultados = linking.confirm_by_content_diff(versiones, {}, {})

        self.assertEqual(len(resultados), 1)
        self.assertIsNone(resultados[0].confirmed_codNota)
        self.assertIsNone(resultados[0].score)

    def test_confirma_el_candidato_cuyo_texto_cubre_el_cambio_observado(self):
        versiones = [
            self._snapshot("22-01-1994", "Articulo 1.- Texto original.\n\nArticulo 2.- Antes."),
            self._snapshot(
                "14-06-2024",
                "Articulo 1.- Texto original.\n\nArticulo 2.- Reformado por decreto especial.",
            ),
        ]
        candidatos_por_fecha = {"14-06-2024": [100, 200]}
        markdown_por_codNota = {
            100: "DECRETO sin relacion con nada de esto.",
            200: "DECRETO por el que se reforma el articulo 2 para quedar reformado por decreto especial.",
        }

        resultados = linking.confirm_by_content_diff(
            versiones, candidatos_por_fecha, markdown_por_codNota
        )

        self.assertEqual(resultados[1].confirmed_codNota, 200)
        self.assertGreaterEqual(resultados[1].score, linking.UMBRAL_CONFIRMACION_DIFF)

    def test_no_confirma_por_debajo_del_umbral_pero_reporta_el_mejor_score(self):
        versiones = [
            self._snapshot("22-01-1994", "Articulo 1.- Texto original."),
            self._snapshot("14-06-2024", "Articulo 1.- Texto con cambios sustanciales agregados."),
        ]
        candidatos_por_fecha = {"14-06-2024": [100]}
        markdown_por_codNota = {100: "DECRETO que apenas menciona cambios de pasada."}

        resultados = linking.confirm_by_content_diff(
            versiones, candidatos_por_fecha, markdown_por_codNota
        )

        self.assertIsNone(resultados[1].confirmed_codNota)
        self.assertIsNotNone(resultados[1].score)
        self.assertLess(resultados[1].score, linking.UMBRAL_CONFIRMACION_DIFF)

    def test_score_none_cuando_ningun_candidato_tiene_texto_disponible(self):
        # Issue #127: sin texto disponible, el enlace se queda tal como lo
        # dejaron #124/#126 — ni bloqueado ni degradado.
        versiones = [
            self._snapshot("22-01-1994", "Articulo 1.- Texto original."),
            self._snapshot("14-06-2024", "Articulo 1.- Texto modificado."),
        ]
        candidatos_por_fecha = {"14-06-2024": [100]}

        resultados = linking.confirm_by_content_diff(versiones, candidatos_por_fecha, {})

        self.assertIsNone(resultados[1].confirmed_codNota)
        self.assertIsNone(resultados[1].score)

    def test_lista_vacia_de_versiones_no_falla(self):
        self.assertEqual(linking.confirm_by_content_diff([], {}, {}), [])

    def test_no_confirma_el_mismo_codnota_para_dos_reformas_del_mismo_dia(self):
        # Confirmado en vivo sobre ccf/27-12-1983: dos decretos reales y
        # distintos ese dia, con texto que se superpone lo bastante como
        # para que el candidato del primero tambien anote el mejor score
        # del segundo — sin exclusividad, ambos "roban" el mismo codNota y
        # el segundo pierde su propio enlace, ya correcto, de #124/#126.
        v1 = "Decreto especial primero segundo aplicado aqui mismo."
        v2 = v1 + "\n\nOtro parrafo decreto especial primero segundo mencionado de nuevo hoy."
        versiones = [
            self._snapshot("01-01-1980", "Texto original sin relacion alguna."),
            self._snapshot("27-12-1983", v1, sufijo=""),
            self._snapshot("27-12-1983", v2, sufijo="-2"),
        ]
        candidatos_por_fecha = {"27-12-1983": [1001, 1002]}
        markdown_por_codNota = {
            1001: "DECRETO que aplica un cambio especial primero segundo parrafo mencionado.",
            1002: "DECRETO que aplica un cambio especial primero segundo aqui mismo "
            "parrafo mencionado nuevo hoy ademas otras cosas.",
        }

        resultados = linking.confirm_by_content_diff(
            versiones, candidatos_por_fecha, markdown_por_codNota
        )

        primera, segunda = resultados[1], resultados[2]
        self.assertEqual(primera.confirmed_codNota, 1002)
        self.assertEqual(segunda.confirmed_codNota, 1001)


class TestNewestDofPublicationDates(unittest.TestCase):
    """`actualizado`'s DOF half (issue #186): the newest legal provision that
    both names the law and opens with DECRETO/LEY."""

    LFCA = "LEY Federal de Cine y el Audiovisual"

    @staticmethod
    def _titulo(titulo, fecha):
        return {"codNota": 1, "titulo": titulo, "fecha": fecha}

    def test_toma_la_publicacion_mas_reciente_que_nombra_la_ley(self):
        titulos = [
            self._titulo("DECRETO por el que se expide la Ley Federal de Cine y el "
                         "Audiovisual", "22-05-2026"),
            self._titulo("DECRETO por el que se reforma la Ley Federal de Cine y el "
                         "Audiovisual", "03-01-2020"),
        ]

        self.assertEqual(
            linking.newest_dof_publication_dates({"lfca": self.LFCA}, titulos),
            {"lfca": "2026-05-22"},
        )

    def test_un_titulo_que_no_abre_con_decreto_o_ley_no_cuenta(self):
        # The guard that keeps the SCJN's `CODIGO` category out: measured
        # live, its ~180 "CODIGO DE CONDUCTA DE ..." entries are published
        # under their own name, never under a DECRETO, so none of them gets
        # a date and none of them is ever reported as a discovered law.
        titulos = [self._titulo("CÓDIGO de Conducta de la Guardia Nacional", "01-06-2026")]

        self.assertEqual(
            linking.newest_dof_publication_dates(
                {"cdcgn": "CÓDIGO de Conducta de la Guardia Nacional"}, titulos
            ),
            {},
        )

    def test_un_decreto_que_no_nombra_la_ley_no_cuenta(self):
        # The omnibus decree: it really does reform this law, and no
        # title-based match can see that. This is why the SCJN's reform
        # table is the other half rather than a fallback -- 91 of the 316
        # laws come back under-dated when only this source is used.
        titulos = [
            self._titulo("DECRETO por el que se reforman diversas disposiciones de "
                         "diversos ordenamientos legales", "14-11-2025")
        ]

        self.assertEqual(
            linking.newest_dof_publication_dates({"lfca": self.LFCA}, titulos), {}
        )

    def test_una_ley_sin_fecha_queda_ausente_no_en_none(self):
        resultado = linking.newest_dof_publication_dates(
            {"lfca": self.LFCA, "lft": "LEY Federal del Trabajo"},
            [self._titulo("DECRETO por el que se expide la Ley Federal de Cine y el "
                          "Audiovisual", "22-05-2026")],
        )

        self.assertEqual(list(resultado), ["lfca"])

    def test_un_nombre_de_una_sola_palabra_significativa_nunca_coincide(self):
        # `_title_mentions_name`'s own floor, kept here because dropping
        # those names up front is what makes one pass over 1.2 million
        # titles affordable.
        titulos = [self._titulo("DECRETO por el que se reforma la Ley de Amparo",
                                "01-01-2026")]

        self.assertEqual(
            linking.newest_dof_publication_dates({"lamp": "LEY de Amparo"}, titulos), {}
        )

    def test_consume_el_flujo_una_sola_vez(self):
        titulos = iter([self._titulo("DECRETO que expide la Ley Federal de Cine y el "
                                     "Audiovisual", "22-05-2026")])

        linking.newest_dof_publication_dates({"lfca": self.LFCA}, titulos)

        self.assertEqual(list(titulos), [])


class TestResolveLinks(unittest.TestCase):
    """Issue #187: the content-diff confirmation becomes the link when title
    matching alone could not pick one."""

    @staticmethod
    def _enlazada(fecha, cod=None):
        return linking.VersionEnlazada(fecha, cod, Path(f"{fecha}.md"))

    @staticmethod
    def _confirmacion(fecha, cod=None, score=None):
        return linking.ContentDiffConfirmation(fecha, cod, score)

    def test_un_enlace_por_titulo_manda_sobre_el_diff(self):
        # The title link is the stronger claim in the sense that matters
        # here: it is the only candidate that named the law that day, so
        # there was never a choice for the diff to make.
        resuelto = linking.resolve_links(
            [self._enlazada("14-06-2024", 100)],
            [self._confirmacion("14-06-2024", 200, 0.9)],
            {"14-06-2024": [100]},
        )

        self.assertEqual(resuelto, [(100, "linked")])

    def test_una_fecha_ambigua_confirmada_por_diff_queda_enlazada(self):
        resuelto = linking.resolve_links(
            [self._enlazada("14-06-2024")],
            [self._confirmacion("14-06-2024", 200, 0.82)],
            {"14-06-2024": [100, 200]},
        )

        self.assertEqual(resuelto, [(200, linking.ESTADO_ENLACE_CONTENT_DIFF)])

    def test_una_fecha_ambigua_sin_confirmacion_sigue_ambigua(self):
        resuelto = linking.resolve_links(
            [self._enlazada("14-06-2024")],
            [self._confirmacion("14-06-2024", None, 0.41)],
            {"14-06-2024": [100, 200]},
        )

        self.assertEqual(resuelto, [(None, "ambiguous")])

    def test_una_fecha_sin_candidatos_no_se_puede_promover(self):
        resuelto = linking.resolve_links(
            [self._enlazada("14-06-2024")], [self._confirmacion("14-06-2024")], {}
        )

        self.assertEqual(resuelto, [(None, "none")])

    def test_nunca_promueve_un_codnota_que_otro_snapshot_ya_reclamo(self):
        # The guard that keeps issue #115's "an absent link is worth more
        # than a wrong one" true across the two mechanisms: each enforces
        # one-codNota-per-snapshot internally, neither knows about the
        # other's claims.
        resuelto = linking.resolve_links(
            [self._enlazada("14-06-2024", 100), self._enlazada("20-11-2025")],
            [self._confirmacion("14-06-2024"), self._confirmacion("20-11-2025", 100, 0.95)],
            {"14-06-2024": [100], "20-11-2025": [100, 300]},
        )

        self.assertEqual(resuelto, [(100, "linked"), (None, "ambiguous")])

    def test_dos_promociones_del_mismo_codnota_solo_se_conceden_una_vez(self):
        resuelto = linking.resolve_links(
            [self._enlazada("14-06-2024"), self._enlazada("20-11-2025")],
            [self._confirmacion("14-06-2024", 200, 0.9),
             self._confirmacion("20-11-2025", 200, 0.9)],
            {"14-06-2024": [100, 200], "20-11-2025": [200, 300]},
        )

        self.assertEqual(
            resuelto, [(200, linking.ESTADO_ENLACE_CONTENT_DIFF), (None, "ambiguous")]
        )

    def test_devuelve_una_entrada_por_snapshot_en_orden(self):
        enlazadas = [self._enlazada("01-01-2020", 1), self._enlazada("02-02-2021"),
                     self._enlazada("03-03-2022", 3)]
        confirmaciones = [self._confirmacion("01-01-2020"),
                          self._confirmacion("02-02-2021", 2, 0.7),
                          self._confirmacion("03-03-2022")]

        resuelto = linking.resolve_links(
            enlazadas, confirmaciones,
            {"02-02-2021": [2, 22], "03-03-2022": [3]},
        )

        self.assertEqual([cod for cod, _ in resuelto], [1, 2, 3])


def _hacer_tgz(archivos: dict) -> bytes:
    import io

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for nombre, contenido in archivos.items():
            data = contenido if isinstance(contenido, bytes) else contenido.encode("utf-8")
            info = tarfile.TarInfo(name=nombre)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _indice_global(cod_notas: dict, instrumentos: dict | None = None) -> bytes:
    payload = {
        "generado": "2026-08-28T14:35:31+00:00",
        "coleccion": "leyes",
        "instrumentos": instrumentos or {},
        "codNota": cod_notas,
    }
    return gzip.compress(json.dumps(payload).encode("utf-8"))


#: The real reader, captured at import time -- before `conftest.py`'s
#: autouse fixture swaps `nota2md.linking.download_scjn_leyes_index` for its
#: covers-nothing stub, which is exactly what these tests do not want.
_INDICE_REAL = linking.download_scjn_leyes_index


class TestLocalizaYSnapshotDeCodNota(unittest.TestCase):
    """`localiza_codNota`/`snapshot_de_codNota` (issue #209): resolving a
    codNota back to the snapshot it produced, DOF-keyed glue that stays in
    `nota2md.linking` and calls `scjn.release`'s disk-first readers.
    Fabricated against a `tmp_path`-style `scjn-leyes/` directory -- no
    network in sight, since the readers underneath are disk-only."""

    def setUp(self):
        parche = patch.object(linking, "download_scjn_leyes_index", _INDICE_REAL)
        parche.start()
        self.addCleanup(parche.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp))
        self.release_dir = self.tmp / "scjn-leyes"
        self.release_dir.mkdir(parents=True)
        release._MEMO_INDICE_GLOBAL.clear()
        self.addCleanup(release._MEMO_INDICE_GLOBAL.clear)

    def _publica(self, cod_notas: dict, instrumentos: dict | None = None, **tarballs):
        (self.release_dir / release.ASSET_INDICE_GLOBAL).write_bytes(
            _indice_global(cod_notas, instrumentos)
        )
        for slug, archivos in tarballs.items():
            (self.release_dir / f"{slug}.tgz").write_bytes(_hacer_tgz(archivos))

    def test_regresa_el_markdown_del_snapshot_de_esa_reforma(self):
        self._publica(
            {"4967917": [{"slug": "lfca", "archivo": "05-01-1999.md"}]},
            lfca={"lfca/05-01-1999.md": "fuente: scjn\n\n**TEXTO ORIGINAL.**"},
        )

        slug, archivo, markdown = linking.snapshot_de_codNota(4967917, cache_dir=self.tmp)

        self.assertEqual((slug, archivo), ("lfca", "05-01-1999.md"))
        self.assertIn("fuente: scjn", markdown)

    def test_localiza_codnota_regresa_solo_la_ubicacion(self):
        self._publica({"4967917": [{"slug": "lfca", "archivo": "05-01-1999.md"}]})

        self.assertEqual(
            linking.localiza_codNota(4967917, cache_dir=self.tmp), ("lfca", "05-01-1999.md")
        )

    def test_regresa_none_cuando_el_codnota_no_esta_en_el_indice(self):
        self._publica({})

        self.assertIsNone(linking.snapshot_de_codNota(999, cache_dir=self.tmp))

    def test_lanza_value_error_cuando_el_decreto_reforma_varias_leyes(self):
        self._publica(
            {"500": [{"slug": "lft", "archivo": "a.md"},
                     {"slug": "lss", "archivo": "b.md"}]},
            {"lft": {"nombre": "Ley Federal del Trabajo"},
             "lss": {"nombre": "Ley del Seguro Social"}},
        )

        with self.assertRaises(ValueError) as ctx:
            linking.snapshot_de_codNota(500, cache_dir=self.tmp)

        self.assertIn("lft", str(ctx.exception))
        self.assertIn("Ley del Seguro Social", str(ctx.exception))

    def test_instrumento_desempata_entre_varias_leyes(self):
        self._publica(
            {"500": [{"slug": "lft", "archivo": "a.md"},
                     {"slug": "lss", "archivo": "b.md"}]},
            lss={"lss/b.md": "TEXTO DE LA LSS"},
        )

        slug, _, markdown = linking.snapshot_de_codNota(
            500, instrumento="lss", cache_dir=self.tmp
        )

        self.assertEqual(slug, "lss")
        self.assertEqual(markdown, "TEXTO DE LA LSS")

    def test_instrumento_que_no_reforma_ese_codnota_es_un_error_no_un_none(self):
        self._publica({"500": [{"slug": "lft", "archivo": "a.md"}]})

        with self.assertRaises(ValueError):
            linking.snapshot_de_codNota(500, instrumento="lss", cache_dir=self.tmp)

    def test_acepta_un_codnota_en_texto_igual_que_en_entero(self):
        self._publica(
            {"7": [{"slug": "lft", "archivo": "a.md"}]},
            lft={"lft/a.md": "TEXTO"},
        )

        self.assertIsNotNone(linking.snapshot_de_codNota("7", cache_dir=self.tmp))

    def test_indice_no_cacheado_lanza_assetnotcached(self):
        with self.assertRaises(release.AssetNotCached):
            linking.localiza_codNota(4967917, cache_dir=self.tmp)

    def test_tarball_no_cacheado_lanza_assetnotcached(self):
        self._publica({"4967917": [{"slug": "lfca", "archivo": "05-01-1999.md"}]})

        with self.assertRaises(release.AssetNotCached):
            linking.snapshot_de_codNota(4967917, cache_dir=self.tmp)

    @patch("requests.get", side_effect=ConnectionError("network unreachable"))
    def test_no_toca_la_red(self, mock_get):
        self._publica(
            {"4967917": [{"slug": "lfca", "archivo": "05-01-1999.md"}]},
            lfca={"lfca/05-01-1999.md": "**TEXTO.**"},
        )

        linking.snapshot_de_codNota(4967917, cache_dir=self.tmp)

        mock_get.assert_not_called()
