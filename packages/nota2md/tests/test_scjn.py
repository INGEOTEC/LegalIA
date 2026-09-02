import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from nota2md import scjn


def _hacer_tgz(archivos: dict) -> bytes:
    """Build an in-memory tarball from {member_name: raw_bytes_or_str},
    same helper shape as packages/nota2md/tests/test_utils.py's own."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for nombre, contenido in archivos.items():
            data = contenido if isinstance(contenido, bytes) else contenido.encode("utf-8")
            info = tarfile.TarInfo(name=nombre)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestRatioSimilitudYGuardas(unittest.TestCase):
    def test_es_acuerdo_interno_detecta_un_acuerdo_general_del_pleno(self):
        self.assertTrue(
            scjn.es_acuerdo_interno(
                "ACUERDO GENERAL NÚMERO 3/2018 DEL PLENO DE LA SUPREMA CORTE DE JUSTICIA "
                "DE LA NACIÓN"
            )
        )

    def test_es_acuerdo_interno_no_marca_una_ley_cualquiera(self):
        self.assertFalse(scjn.es_acuerdo_interno("LEY FEDERAL DEL TRABAJO"))

    def test_ratio_similitud_ignora_el_sufijo_de_nombre_anterior(self):
        titulo = (
            "CODIGO CIVIL FEDERAL -ANTES CODIGO CIVIL PARA EL DISTRITO FEDERAL EN "
            "MATERIA COMUN Y PARA TODA LA REPUBLICA EN MATERIA FEDERAL -"
        )
        self.assertEqual(scjn.ratio_similitud(titulo, "Código Civil Federal"), 1.0)

    def test_grupo_instrumento_reconoce_ley_codigo_y_reglamento(self):
        self.assertEqual(scjn.grupo_instrumento("LEY FEDERAL DEL TRABAJO"), "ley")
        self.assertEqual(scjn.grupo_instrumento("CÓDIGO Civil Federal"), "ley")
        self.assertEqual(
            scjn.grupo_instrumento("REGLAMENTO DE LA LEY ADUANERA"), "reglamento"
        )
        self.assertIsNone(scjn.grupo_instrumento("Convenio 107 OIT"))


class TestQuitaNotasEditoriales(unittest.TestCase):
    def test_quita_la_nota_embebida_dentro_de_una_anotacion_de_reforma(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "(REFORMADO [N. DE E. ESTE PÁRRAFO], D.O.F. 19 DE DICIEMBRE DE 2017)"
            ),
            "(REFORMADO, D.O.F. 19 DE DICIEMBRE DE 2017)",
        )

    def test_quita_la_nota_embebida_con_variante_n_de_punto_e(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "(ADICIONADA [N. DE . E. REUBICADA], D.O.F. 15 DE JUNIO DE 2007)"
            ),
            "(ADICIONADA, D.O.F. 15 DE JUNIO DE 2007)",
        )

    def test_deja_vacio_un_parrafo_que_es_enteramente_nota_editorial(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                '[N. DE E. TRANSITORIO DEL "DECRETO POR EL QUE SE REFORMA".]'
            ),
            "",
        )

    def test_deja_vacio_un_parrafo_con_el_marcador_nota_n(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "[NOTA 1. DE CONFORMIDAD CON EL ACUERDO EMITIDO POR EL CONSEJO.]"
            ),
            "",
        )

    def test_quita_un_corchete_sin_marcador_pero_todo_en_mayusculas(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "(REUBICADO [ANTES ARTICULO 57], D.O.F. 23 DE ENERO DE 2004)"
            ),
            "(REUBICADO, D.O.F. 23 DE ENERO DE 2004)",
        )

    def test_no_toca_una_formula_arancelaria_entre_corchetes(self):
        parrafo = "El resultado de la fórmula [(S/365)+V * (I + D)] se aplica."
        self.assertEqual(scjn.quita_notas_editoriales(parrafo), parrafo)

    def test_no_toca_un_nombre_quimico_entre_corchetes(self):
        parrafo = "Se entiende por [4-nitro-3-(trifluorometil)fenilo] la substancia."
        self.assertEqual(scjn.quita_notas_editoriales(parrafo), parrafo)

    def test_no_confunde_la_nota_n_con_una_cita_de_nota_arancelaria(self):
        parrafo = "Mezclas previstas en la Nota 1 b) de este Capítulo."
        self.assertEqual(scjn.quita_notas_editoriales(parrafo), parrafo)

    def test_quita_el_marcador_suelto_que_sigue_a_una_anotacion_ya_cerrada(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "(ADICIONADA, D.O.F. 14 DE NOVIEMBRE DE 2013) N. DE E. SÓLO EN "
                "CUANTO AL CONTENIDO, PORQUE DEL ANÁLISIS DEL TEXTO ORIGINAL "
                "PUBLICADO EL 2 DE AGOSTO DE 2006, SE APRECIA LA EXISTENCIA DE "
                "ESTA FRACCIÓN."
            ),
            "(ADICIONADA, D.O.F. 14 DE NOVIEMBRE DE 2013)",
        )

    def test_quita_el_marcador_suelto_embebido_antes_de_que_la_anotacion_reanude(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "(REFORMADO N. DE E. ESTE PÁRRAFO, D.O.F. 21 DE FEBRERO DE 2018)"
            ),
            "(REFORMADO, D.O.F. 21 DE FEBRERO DE 2018)",
        )

    def test_deja_vacio_un_parrafo_envuelto_en_parentesis_sin_marcador_de_corchete(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "(NOTA 1: EL PLENO DE LA SUPREMA CORTE DECLARÓ LA INVALIDEZ.)"
            ),
            "",
        )

    def test_quita_la_nota_entre_parentesis_que_sigue_a_texto_real_en_el_mismo_parrafo(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "Artículo 58.- La música oficial del Himno Nacional es la "
                "siguiente: (N. DE E., VÉASE D.O.F. 8 DE FEBRERO DE 1984)"
            ),
            "Artículo 58.- La música oficial del Himno Nacional es la siguiente:",
        )

    def test_conserva_la_negrita_de_un_parrafo_ya_formateado_al_limpiarlo(self):
        self.assertEqual(
            scjn.quita_notas_editoriales(
                "**(REFORMADA [N. DE E. ADICIONADA], D.O.F. 15 DE ENERO DE 2026)**"
            ),
            "**(REFORMADA, D.O.F. 15 DE ENERO DE 2026)**",
        )

    def test_es_un_no_op_sobre_un_parrafo_ya_limpio(self):
        parrafo = "**(REFORMADA, D.O.F. 15 DE ENERO DE 2026)**"
        self.assertEqual(scjn.quita_notas_editoriales(parrafo), parrafo)


class TestSlugInstrumento(unittest.TestCase):
    def test_usa_abrev_cuando_esta_disponible(self):
        self.assertEqual(scjn.slug_instrumento({"abrev": "cpeum", "nombre": "CONSTITUCIÓN"}), "cpeum")

    def test_exige_abrev(self):
        # Issue #189: with `leyes` the only collection left, every catalogue
        # entry has an `abrev`, so falling back to the `nombre` would only
        # ever hide a malformed entry behind a plausible-looking slug.
        with self.assertRaises(KeyError):
            scjn.slug_instrumento({"nombre": "Convenio 107 OIT"})

    def test_slugify_forma_un_slug_de_cualquier_texto(self):
        self.assertEqual(scjn.slugify("Convenio 107 OIT"), "convenio-107-oit")


class TestLeeCabecera(unittest.TestCase):
    def test_lee_los_campos_de_la_cabecera_de_procedencia(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "22-01-1994.md"
            archivo.write_text(
                "---\n"
                "fuente: scjn\n"
                "ordenamiento: LEY DE AMNISTIA\n"
                "fecha_publicacion: 22-01-1994\n"
                "fecha_expedicion: 21-01-1994\n"
                "categoria: LEY\n"
                "---\n\n"
                "**TEXTO ORIGINAL.**\n",
                encoding="utf-8",
            )

            campos = scjn.lee_cabecera(archivo)

            self.assertEqual(campos["fuente"], "scjn")
            self.assertEqual(campos["ordenamiento"], "LEY DE AMNISTIA")
            self.assertEqual(campos["fecha_publicacion"], "22-01-1994")
            self.assertEqual(campos["fecha_expedicion"], "21-01-1994")
            self.assertEqual(campos["categoria"], "LEY")

    def test_no_lee_mas_alla_del_cierre_de_la_cabecera(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "x.md"
            archivo.write_text(
                "---\nfecha_publicacion: 22-01-1994\n---\n\nordenamiento: no es esto\n",
                encoding="utf-8",
            )

            campos = scjn.lee_cabecera(archivo)

            self.assertEqual(campos, {"fecha_publicacion": "22-01-1994"})


class TestVersionesDeDirectorio(unittest.TestCase):
    def test_regresa_las_versiones_ordenadas_por_fecha_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp)
            for nombre, fecha in [
                ("14-06-2024.md", "14-06-2024"),
                ("22-01-1994.md", "22-01-1994"),
            ]:
                (outdir / nombre).write_text(
                    f"---\nfecha_publicacion: {fecha}\n---\n\ntexto\n", encoding="utf-8"
                )

            versiones = scjn.versiones_de_directorio(outdir)

            self.assertEqual(
                [v.fecha_publicacion for v in versiones], ["22-01-1994", "14-06-2024"]
            )

    def test_desempata_fechas_repetidas_por_nombre_de_archivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            outdir = Path(tmp)
            for nombre in ["14-06-2024-2.md", "14-06-2024.md"]:
                (outdir / nombre).write_text(
                    "---\nfecha_publicacion: 14-06-2024\n---\n\ntexto\n", encoding="utf-8"
                )

            versiones = scjn.versiones_de_directorio(outdir)

            self.assertEqual(
                [v.archivo.name for v in versiones], ["14-06-2024.md", "14-06-2024-2.md"]
            )


class TestTitleCandidatesPorFecha(unittest.TestCase):
    def test_agrupa_por_fecha_solo_los_codnota_cuyo_titulo_menciona_el_nombre(self):
        porf = {
            "22-01-1994": [
                {"codNota": 100, "titulo": "DECRETO que reforma la Ley Federal del Trabajo"},
                {"codNota": 999, "titulo": "DECRETO sobre otro asunto"},
            ],
        }

        agrupado = scjn.title_candidates_por_fecha(
            ["22-01-1994"], "Ley Federal del Trabajo", porf
        )

        self.assertEqual(agrupado, {"22-01-1994": [100]})

    def test_una_fecha_sin_registros_en_porf_regresa_lista_vacia(self):
        agrupado = scjn.title_candidates_por_fecha(["22-01-1994"], "Ley Federal del Trabajo", {})

        self.assertEqual(agrupado, {"22-01-1994": []})

    def test_no_repite_fechas_duplicadas_en_el_resultado(self):
        porf = {"14-06-2024": [{"codNota": 100, "titulo": "Ley de Amnistia"}]}

        agrupado = scjn.title_candidates_por_fecha(
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

        agrupado = scjn.title_candidates_por_fecha(["14-11-2025"], "Codigo Civil Federal", porf)

        self.assertEqual(agrupado, {"14-11-2025": [100]})

    def test_no_recae_en_el_respaldo_cuando_ya_hay_mencion_explicita(self):
        porf = {
            "22-01-1994": [
                {"codNota": 100, "titulo": "DECRETO que reforma la Ley Federal del Trabajo"},
                {"codNota": 200, "titulo": "LEY de otro ordenamiento"},
            ],
        }

        agrupado = scjn.title_candidates_por_fecha(
            ["22-01-1994"], "Ley Federal del Trabajo", porf
        )

        self.assertEqual(agrupado, {"22-01-1994": [100]})

    def test_lista_vacia_cuando_tampoco_hay_decreto_o_ley_ese_dia(self):
        porf = {"14-11-2025": [{"codNota": 999, "titulo": "AVISO sobre otro asunto"}]}

        agrupado = scjn.title_candidates_por_fecha(["14-11-2025"], "Codigo Civil Federal", porf)

        self.assertEqual(agrupado, {"14-11-2025": []})


class TestTituloEmpiezaConDecretoOLey(unittest.TestCase):
    def test_reconoce_decreto_case_insensible(self):
        self.assertTrue(scjn._title_opens_with_decreto_or_ley("decreto por el que se reforma"))

    def test_reconoce_ley(self):
        self.assertTrue(scjn._title_opens_with_decreto_or_ley("LEY de Amparo"))

    def test_no_reconoce_acuerdo(self):
        self.assertFalse(scjn._title_opens_with_decreto_or_ley("ACUERDO por el que se emite"))

    def test_no_se_deja_enganar_por_una_palabra_que_solo_empieza_igual(self):
        # "LEYES" no es "LEY" -- el limite de palabra evita el falso positivo.
        self.assertFalse(scjn._title_opens_with_decreto_or_ley("LEYES secundarias"))


class TestEnlazaPorTitulo(unittest.TestCase):
    def _version(self, fecha: str, nombre: str = None) -> scjn.VersionInstrumento:
        return scjn.VersionInstrumento(fecha, Path(nombre or f"{fecha}.md"))

    def test_enlaza_cuando_hay_exactamente_un_candidato_esa_fecha(self):
        versiones = [self._version("22-01-1994"), self._version("14-06-2024")]
        candidatos_por_fecha = {"22-01-1994": [100], "14-06-2024": [200]}

        enlazadas = scjn.enlaza_por_titulo(versiones, candidatos_por_fecha)

        self.assertEqual([v.codNota for v in enlazadas], [100, 200])

    def test_deja_sin_enlazar_una_fecha_sin_candidato(self):
        versiones = [self._version("22-01-1994")]

        enlazadas = scjn.enlaza_por_titulo(versiones, {})

        self.assertIsNone(enlazadas[0].codNota)

    def test_deja_sin_enlazar_una_fecha_con_varios_candidatos_ambiguos(self):
        # Sin historial que desempate, el titulo solo no puede elegir entre
        # varios candidatos del mismo dia — issue #127's content diff es lo
        # unico que puede resolver este caso.
        versiones = [self._version("22-01-1994")]
        candidatos_por_fecha = {"22-01-1994": [100, 200]}

        enlazadas = scjn.enlaza_por_titulo(versiones, candidatos_por_fecha)

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

        enlazadas = scjn.enlaza_por_titulo(versiones, candidatos_por_fecha)

        self.assertEqual([v.codNota for v in enlazadas], [100, None])


class TestTitleLinkStatus(unittest.TestCase):
    def test_enlazado_cuando_hay_codnota(self):
        self.assertEqual(scjn.title_link_status(100, [100]), "linked")

    def test_ninguno_sin_candidatos(self):
        self.assertEqual(scjn.title_link_status(None, []), "none")

    def test_reclamado_cuando_el_unico_candidato_ya_fue_tomado(self):
        self.assertEqual(scjn.title_link_status(None, [100]), "claimed")

    def test_ambiguo_con_varios_candidatos(self):
        self.assertEqual(scjn.title_link_status(None, [100, 200]), "ambiguous")


class TestTitleMentionsName(unittest.TestCase):
    def test_reconoce_mencion_explicita_case_e_acento_insensible(self):
        self.assertTrue(
            scjn._title_mentions_name(
                "Ley Federal del Trabajo",
                "DECRETO por el que se reforma la ley federal del trabajo",
            )
        )

    def test_no_reconoce_una_ley_distinta(self):
        self.assertFalse(
            scjn._title_mentions_name(
                "Ley Federal del Trabajo",
                "DECRETO por el que se reforma el Codigo Fiscal de la Federacion",
            )
        )

    def test_no_se_deja_engañar_por_palabras_cortas_compartidas(self):
        # "Ley"/"del"/"de" son demasiado cortas para contar por si solas.
        self.assertFalse(
            scjn._title_mentions_name(
                "Ley Federal del Trabajo",
                "DECRETO por el que se reforma la Ley de Amparo",
            )
        )

    def test_exige_todas_las_palabras_significativas_del_nombre(self):
        self.assertFalse(
            scjn._title_mentions_name(
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
            scjn._title_mentions_name(
                "Ley de Amparo",
                "DECRETO por el que se reforma el Reglamento de la Ley de Amparo",
            )
        )


class TestAddedBlocksYOverlapScore(unittest.TestCase):
    def test_detecta_un_parrafo_nuevo_entre_dos_versiones(self):
        anterior = "Articulo 1.- Texto original.\n\nArticulo 2.- Otro texto."
        nuevo = "Articulo 1.- Texto original.\n\nArticulo 2.- Texto modificado por la reforma."

        agregados = scjn._added_blocks(anterior, nuevo)

        self.assertEqual(len(agregados), 1)
        self.assertIn("modificado", agregados[0])

    def test_no_marca_nada_agregado_cuando_las_versiones_son_iguales(self):
        texto = "Articulo 1.- Texto original.\n\nArticulo 2.- Otro texto."

        self.assertEqual(scjn._added_blocks(texto, texto), [])

    def test_score_es_uno_cuando_el_candidato_cubre_todo_lo_agregado(self):
        agregados = ["articulo 2 texto modificado por la reforma"]
        candidato = "decreto que reforma el articulo 2 texto modificado por la reforma"

        self.assertEqual(scjn._overlap_score(agregados, candidato), 1.0)

    def test_score_es_cero_sin_relacion_alguna(self):
        agregados = ["articulo 2 texto modificado por la reforma"]
        candidato = "decreto sobre un asunto completamente distinto"

        self.assertEqual(scjn._overlap_score(agregados, candidato), 0.0)

    def test_score_es_cero_sin_nada_agregado(self):
        self.assertEqual(scjn._overlap_score([], "cualquier texto"), 0.0)

    def test_distingue_candidatos_que_solo_difieren_en_un_numero_corto(self):
        # Una reforma que solo cambia una tasa/monto/plazo corto (menos de 4
        # digitos) no debe volverse invisible para el score.
        agregados = ["se establece una tasa de 20 por ciento"]
        candidato_correcto = "decreto que fija la tasa en 20 por ciento"
        candidato_equivocado = "decreto que fija la tasa en 15 por ciento"

        score_correcto = scjn._overlap_score(agregados, candidato_correcto)
        score_equivocado = scjn._overlap_score(agregados, candidato_equivocado)

        self.assertGreater(score_correcto, score_equivocado)


class TestConfirmByContentDiff(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _snapshot(self, fecha: str, cuerpo: str, sufijo: str = "") -> scjn.VersionInstrumento:
        archivo = self.outdir / f"{fecha}{sufijo}.md"
        archivo.write_text(f"---\nfecha_publicacion: {fecha}\n---\n\n{cuerpo}", encoding="utf-8")
        return scjn.VersionInstrumento(fecha, archivo)

    def test_la_primera_version_no_tiene_confirmacion_por_no_tener_version_previa(self):
        versiones = [self._snapshot("22-01-1994", "Articulo 1.- Texto original.")]

        resultados = scjn.confirm_by_content_diff(versiones, {}, {})

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

        resultados = scjn.confirm_by_content_diff(
            versiones, candidatos_por_fecha, markdown_por_codNota
        )

        self.assertEqual(resultados[1].confirmed_codNota, 200)
        self.assertGreaterEqual(resultados[1].score, scjn.UMBRAL_CONFIRMACION_DIFF)

    def test_no_confirma_por_debajo_del_umbral_pero_reporta_el_mejor_score(self):
        versiones = [
            self._snapshot("22-01-1994", "Articulo 1.- Texto original."),
            self._snapshot("14-06-2024", "Articulo 1.- Texto con cambios sustanciales agregados."),
        ]
        candidatos_por_fecha = {"14-06-2024": [100]}
        markdown_por_codNota = {100: "DECRETO que apenas menciona cambios de pasada."}

        resultados = scjn.confirm_by_content_diff(
            versiones, candidatos_por_fecha, markdown_por_codNota
        )

        self.assertIsNone(resultados[1].confirmed_codNota)
        self.assertIsNotNone(resultados[1].score)
        self.assertLess(resultados[1].score, scjn.UMBRAL_CONFIRMACION_DIFF)

    def test_score_none_cuando_ningun_candidato_tiene_texto_disponible(self):
        # Issue #127: sin texto disponible, el enlace se queda tal como lo
        # dejaron #124/#126 — ni bloqueado ni degradado.
        versiones = [
            self._snapshot("22-01-1994", "Articulo 1.- Texto original."),
            self._snapshot("14-06-2024", "Articulo 1.- Texto modificado."),
        ]
        candidatos_por_fecha = {"14-06-2024": [100]}

        resultados = scjn.confirm_by_content_diff(versiones, candidatos_por_fecha, {})

        self.assertIsNone(resultados[1].confirmed_codNota)
        self.assertIsNone(resultados[1].score)

    def test_lista_vacia_de_versiones_no_falla(self):
        self.assertEqual(scjn.confirm_by_content_diff([], {}, {}), [])

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

        resultados = scjn.confirm_by_content_diff(
            versiones, candidatos_por_fecha, markdown_por_codNota
        )

        primera, segunda = resultados[1], resultados[2]
        self.assertEqual(primera.confirmed_codNota, 1002)
        self.assertEqual(segunda.confirmed_codNota, 1001)


class TestDownloadScjnLeyesCorpus(unittest.TestCase):
    @staticmethod
    def _respuestas(asset: str, contenido: bytes) -> list:
        return [
            Mock(json=lambda: {"assets": [
                {"name": asset, "browser_download_url": f"https://x/{asset}"}
            ]}, raise_for_status=Mock()),
            Mock(content=contenido, raise_for_status=Mock()),
        ]

    @patch("nota2md.scjn.requests.get")
    def test_lanza_key_error_cuando_el_release_no_tiene_el_asset_de_esa_ley(self, mock_get):
        mock_get.return_value = Mock(json=lambda: {"assets": []}, raise_for_status=Mock())

        with self.assertRaises(KeyError):
            scjn.download_scjn_leyes_corpus("cpeum")

    @patch("nota2md.scjn.requests.get")
    def test_une_indice_con_el_markdown_de_cada_snapshot(self, mock_get):
        indice = [
            {"archivo": "22-01-1994.md", "codNota": 100, "ratio_similitud": 0.9,
             "sospechoso": False, "title_candidates": [100], "title_link_status": "linked",
             "content_diff_confirmed_codNota": None, "content_diff_score": None},
        ]
        contenido = _hacer_tgz({
            "cpeum/indice.json": json.dumps(indice),
            "cpeum/22-01-1994.md": "**TEXTO ORIGINAL.**",
        })
        mock_get.side_effect = self._respuestas("cpeum.tgz", contenido)

        resultado = scjn.download_scjn_leyes_corpus("cpeum")

        self.assertEqual(resultado["slug"], "cpeum")
        self.assertEqual(len(resultado["snapshots"]), 1)
        snap = resultado["snapshots"][0]
        self.assertEqual(snap["codNota"], 100)
        self.assertEqual(snap["title_link_status"], "linked")
        self.assertEqual(snap["markdown"], "**TEXTO ORIGINAL.**")

    @patch("nota2md.scjn.requests.get")
    def test_cada_snapshot_trae_el_texto_dof_de_los_candidatos_considerados(self, mock_get):
        # Lo que hace auditable el enlace de #126/#127 sin volver a la red:
        # el snapshot llega con el texto de cada candidato que se comparo,
        # no solo con el codNota ganador.
        indice = [
            {"archivo": "22-01-1994.md", "codNota": 100, "title_candidates": [100, 101],
             "content_diff_confirmed_codNota": 100, "content_diff_score": 0.8},
            {"archivo": "01-01-1995.md", "codNota": None, "title_candidates": []},
        ]
        contenido = _hacer_tgz({
            "lft/indice.json": json.dumps(indice),
            "lft/22-01-1994.md": "**TEXTO ORIGINAL.**",
            "lft/01-01-1995.md": "**REFORMA.**",
            "lft/notas/nota-100.md": "DECRETO uno.",
            "lft/notas/nota-101.md": "DECRETO dos.",
        })
        mock_get.side_effect = self._respuestas("lft.tgz", contenido)

        snapshots = scjn.download_scjn_leyes_corpus("lft")["snapshots"]

        self.assertEqual(snapshots[0]["notas"], {100: "DECRETO uno.", 101: "DECRETO dos."})
        self.assertEqual(snapshots[1]["notas"], {})

    @patch("nota2md.scjn.requests.get")
    def test_instrumento_sin_indice_json_regresa_snapshots_sin_enlace_en_vez_de_omitirse(
        self, mock_get
    ):
        # Fase 2 (issue #105) pendiente para este instrumento: hay
        # snapshots pero enlaza_scjn_legislacion.py no ha corrido para el.
        contenido = _hacer_tgz({"lfea/01-01-2012.md": "**TEXTO ORIGINAL.**"})
        mock_get.side_effect = self._respuestas("lfea.tgz", contenido)

        resultado = scjn.download_scjn_leyes_corpus("lfea")

        self.assertEqual(resultado["slug"], "lfea")
        snap = resultado["snapshots"][0]
        self.assertEqual(snap["archivo"], "01-01-2012.md")
        self.assertIsNone(snap["codNota"])
        self.assertEqual(snap["markdown"], "**TEXTO ORIGINAL.**")


class TestSearchName(unittest.TestCase):
    def test_usa_nombre_scjn_cuando_esta_presente(self):
        entrada = {"nombre": "IMPUESTO sobre Servicios... (LEY que...)", "nombre_scjn": "LEY DEL IMPUESTO..."}
        self.assertEqual(scjn.search_name(entrada), "LEY DEL IMPUESTO...")

    def test_recae_en_nombre_sin_override(self):
        entrada = {"nombre": "LEY de Amparo"}
        self.assertEqual(scjn.search_name(entrada), "LEY de Amparo")


class TestCatalogKey(unittest.TestCase):
    def test_usa_abrev_cuando_esta_disponible(self):
        self.assertEqual(scjn.catalog_key({"abrev": "ccf", "nombre": "Codigo Civil Federal"}), "ccf")

    def test_exige_abrev(self):
        # Same reason as `slug_instrumento` (issue #189).
        with self.assertRaises(KeyError):
            scjn.catalog_key({"nombre": "Convenio 107 OIT"})


class TestMergeCatalogOverrides(unittest.TestCase):
    def test_conserva_nombre_scjn_de_la_entrada_correspondiente(self):
        nuevo = [{"nombre": "IMPUESTO sobre Servicios...", "abrev": "lisipl"}]
        previo = [
            {"nombre": "IMPUESTO sobre Servicios...", "abrev": "lisipl", "nombre_scjn": "LEY DEL IMPUESTO..."}
        ]

        fusionado = scjn.merge_catalog_overrides(nuevo, previo)

        self.assertEqual(fusionado[0]["nombre_scjn"], "LEY DEL IMPUESTO...")
        # nombre/abrev are the freshly re-downloaded ones, untouched.
        self.assertEqual(fusionado[0]["nombre"], "IMPUESTO sobre Servicios...")

    def test_no_inventa_nombre_scjn_para_una_entrada_sin_override_previo(self):
        nuevo = [{"nombre": "LEY de Amparo", "abrev": "la"}]
        previo = [{"nombre": "LEY de Amparo", "abrev": "la"}]

        fusionado = scjn.merge_catalog_overrides(nuevo, previo)

        self.assertNotIn("nombre_scjn", fusionado[0])

    def test_empareja_por_abrev_aunque_el_nombre_cambie_de_forma(self):
        nuevo = [{"nombre": "LEY Federal de Cine y el Audiovisual", "abrev": "lfca"}]
        previo = [{"nombre": "LEY de Cine (nombre distinto)", "abrev": "lfca", "nombre_scjn": "X"}]

        fusionado = scjn.merge_catalog_overrides(nuevo, previo)

        self.assertEqual(fusionado[0]["nombre_scjn"], "X")

    def test_catalogo_previo_none_regresa_el_nuevo_intacto(self):
        nuevo = [{"nombre": "LEY de Amparo", "abrev": "la"}]
        self.assertEqual(scjn.merge_catalog_overrides(nuevo, None), nuevo)

    def test_catalogo_previo_vacio_regresa_el_nuevo_intacto(self):
        nuevo = [{"nombre": "LEY de Amparo", "abrev": "la"}]
        self.assertEqual(scjn.merge_catalog_overrides(nuevo, []), nuevo)


class TestIsoDateFromNote(unittest.TestCase):
    def test_convierte_fecha_dd_mm_yyyy_a_iso(self):
        self.assertEqual(scjn.iso_date_from_note({"fecha": "24-05-2026"}), "2026-05-24")

    def test_regresa_none_sin_fecha(self):
        self.assertIsNone(scjn.iso_date_from_note({}))


class TestInstrumentoUpToDate(unittest.TestCase):
    def test_no_salta_sin_fecha_de_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            self.assertFalse(scjn.instrument_up_to_date(destino, "2020-01-01", None))

    def test_no_salta_sin_snapshots_en_disco(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)  # vacio -- nunca se le encontro nada en la SCJN
            self.assertFalse(scjn.instrument_up_to_date(destino, "2020-01-01", "2026-01-01"))

    def test_no_salta_sin_actualizado_en_el_catalogo(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            self.assertFalse(scjn.instrument_up_to_date(destino, None, "2026-01-01"))

    def test_salta_cuando_ya_tiene_snapshots_y_esta_al_dia(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            self.assertTrue(scjn.instrument_up_to_date(destino, "2020-01-01", "2026-01-01"))

    def test_no_salta_cuando_actualizado_es_posterior_al_corpus(self):
        # Caso lfca (issue #124): una ley reformada despues del ultimo
        # rastreo completo se re-intenta en cada refresh.
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            self.assertFalse(scjn.instrument_up_to_date(destino, "2026-05-24", "2026-01-01"))


class TestEstadoPorInstrumento(unittest.TestCase):
    """Issue #148: per-instrument freshness, so one law can be refreshed
    alone without waiting for a full sweep of the collection."""

    def test_lee_estado_sin_archivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(scjn.lee_estado(Path(tmp)), {})

    def test_lee_estado_malformado_es_como_no_tenerlo(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / scjn.ARCHIVO_ESTADO).write_text("{no es json")
            self.assertEqual(scjn.lee_estado(Path(tmp)), {})

    def test_escribe_estado_fusiona_en_vez_de_sobrescribir(self):
        # fetch_scjn_legislacion.py escribe actualizado/rastreado y
        # enlaza_scjn_legislacion.py enlazado: ninguno borra al otro.
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            scjn.escribe_estado(destino, actualizado="2026-06-09", rastreado="2026-08-27")
            scjn.escribe_estado(destino, enlazado="2026-08-28")
            self.assertEqual(
                scjn.lee_estado(destino),
                {
                    "actualizado": "2026-06-09",
                    "rastreado": "2026-08-27",
                    "enlazado": "2026-08-28",
                },
            )

    def test_pendiente_nunca_rastreado(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                scjn.motivo_pendiente({"actualizado": "2026-06-09"}, Path(tmp), "2026-08-27"),
                scjn.PENDIENTE_NUNCA_RASTREADO,
            )

    def test_pendiente_sin_actualizado_en_el_catalogo(self):
        # lisipl/lcmopfih/lfcpq: nada los fecha (ni la tabla de reformas de
        # la SCJN ni los titulos del DOF), asi que no hay forma de saber si
        # cambiaron.
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            self.assertEqual(
                scjn.motivo_pendiente({}, destino, "2026-08-27"),
                scjn.PENDIENTE_SIN_ACTUALIZADO,
            )

    def test_estado_por_ley_tiene_precedencia_sobre_el_rastreo_completo(self):
        # Una ley rastreada sola queda al dia aunque ningun barrido completo
        # haya corrido despues (corpus_date viejo, o inexistente).
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            scjn.escribe_estado(destino, actualizado="2026-06-09", rastreado="2026-08-29")
            self.assertIsNone(scjn.motivo_pendiente({"actualizado": "2026-06-09"}, destino, None))

    def test_cambio_detectado_contra_el_estado_de_la_ley(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            scjn.escribe_estado(destino, actualizado="2026-06-09", rastreado="2026-08-29")
            self.assertEqual(
                scjn.motivo_pendiente({"actualizado": "2026-07-01"}, destino, "2026-08-29"),
                scjn.PENDIENTE_CAMBIO,
            )

    def test_sin_estado_cae_al_criterio_de_coleccion(self):
        # Compatibilidad con el corpus actual, que no tiene estado.json:
        # se sigue decidiendo con .rastreo_completo.json (Mecanismo 2, #124).
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            self.assertIsNone(
                scjn.motivo_pendiente({"actualizado": "2026-06-09"}, destino, "2026-08-27")
            )
            self.assertEqual(
                scjn.motivo_pendiente({"actualizado": "2026-06-09"}, destino, "2026-01-01"),
                scjn.PENDIENTE_CAMBIO,
            )


if __name__ == "__main__":
    unittest.main()


class TestDescargaAssetsScjnLeyes(unittest.TestCase):
    """`download_scjn_leyes_assets` (issue #155): the release materialized on
    disk, and idempotent — a second run costs no download at all."""

    URLS = {
        "indice-global.json.gz": "https://x/indice-global.json.gz",
        "lfca.tgz": "https://x/lfca.tgz",
        "lft.tgz": "https://x/lft.tgz",
    }

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("nota2md.cache.descarga", return_value=b"bytes")
    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_sin_slugs_baja_el_indice_y_todos_los_tgz(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)

        resultados = scjn.download_scjn_leyes_assets(cache_dir=self.tmp)

        self.assertEqual(
            [ruta.name for ruta, _ in resultados],
            ["indice-global.json.gz", "lfca.tgz", "lft.tgz"],
        )
        self.assertTrue(all(descargado for _, descargado in resultados))
        self.assertEqual(mock_descarga.call_count, 3)

    @patch("nota2md.cache.descarga", return_value=b"bytes")
    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_slugs_acota_pero_el_indice_siempre_viene(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)

        resultados = scjn.download_scjn_leyes_assets(["lft"], cache_dir=self.tmp)

        self.assertEqual(
            [ruta.name for ruta, _ in resultados],
            ["indice-global.json.gz", "lft.tgz"],
        )

    @patch("nota2md.cache.descarga", return_value=b"bytes")
    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_la_segunda_corrida_no_baja_nada(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)
        scjn.download_scjn_leyes_assets(cache_dir=self.tmp)
        mock_descarga.reset_mock()

        resultados = scjn.download_scjn_leyes_assets(cache_dir=self.tmp)

        self.assertFalse(any(descargado for _, descargado in resultados))
        mock_descarga.assert_not_called()

    @patch("nota2md.cache.descarga", return_value=b"bytes")
    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_refrescar_vuelve_a_bajar(self, mock_assets, mock_descarga):
        mock_assets.return_value = dict(self.URLS)
        scjn.download_scjn_leyes_assets(cache_dir=self.tmp)
        mock_descarga.reset_mock()

        resultados = scjn.download_scjn_leyes_assets(cache_dir=self.tmp, refrescar=True)

        self.assertTrue(all(descargado for _, descargado in resultados))
        self.assertEqual(mock_descarga.call_count, 3)

    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_cache_dir_none_no_tiene_donde_escribir(self, mock_assets):
        mock_assets.return_value = dict(self.URLS)

        with self.assertRaises(ValueError):
            scjn.download_scjn_leyes_assets(cache_dir=None)

    @patch("nota2md.cache.descarga", return_value=b"bytes")
    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_un_slug_que_el_release_no_publica_es_un_error(self, mock_assets, _):
        mock_assets.return_value = dict(self.URLS)

        with self.assertRaises(KeyError):
            scjn.download_scjn_leyes_assets(["no-existe"], cache_dir=self.tmp)

    @patch("nota2md.scjn._assets_scjn_leyes")
    def test_los_slugs_del_release_salen_de_sus_propios_assets(self, mock_assets):
        mock_assets.return_value = dict(self.URLS)

        self.assertEqual(scjn.scjn_leyes_slugs(), ["lfca", "lft"])


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
            scjn.newest_dof_publication_dates({"lfca": self.LFCA}, titulos),
            {"lfca": "2026-05-22"},
        )

    def test_un_titulo_que_no_abre_con_decreto_o_ley_no_cuenta(self):
        # The guard that keeps the SCJN's `CODIGO` category out: measured
        # live, its ~180 "CODIGO DE CONDUCTA DE ..." entries are published
        # under their own name, never under a DECRETO, so none of them gets
        # a date and none of them is ever reported as a discovered law.
        titulos = [self._titulo("CÓDIGO de Conducta de la Guardia Nacional", "01-06-2026")]

        self.assertEqual(
            scjn.newest_dof_publication_dates(
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
            scjn.newest_dof_publication_dates({"lfca": self.LFCA}, titulos), {}
        )

    def test_una_ley_sin_fecha_queda_ausente_no_en_none(self):
        resultado = scjn.newest_dof_publication_dates(
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
            scjn.newest_dof_publication_dates({"lamp": "LEY de Amparo"}, titulos), {}
        )

    def test_consume_el_flujo_una_sola_vez(self):
        titulos = iter([self._titulo("DECRETO que expide la Ley Federal de Cine y el "
                                     "Audiovisual", "22-05-2026")])

        scjn.newest_dof_publication_dates({"lfca": self.LFCA}, titulos)

        self.assertEqual(list(titulos), [])


class TestMintAbrev(unittest.TestCase):
    """The one identifier no source assigns (issue #186)."""

    def test_son_las_iniciales_sin_las_palabras_vacias(self):
        self.assertEqual(scjn.mint_abrev("LEY Federal de Cine y el Audiovisual"), "lfca")
        self.assertEqual(
            scjn.mint_abrev("CÓDIGO Nacional de Procedimientos Civiles y Familiares"),
            "cnpcf",
        )

    def test_es_determinista_y_no_depende_de_los_acentos(self):
        self.assertEqual(
            scjn.mint_abrev("CÓDIGO Nacional"), scjn.mint_abrev("CODIGO NACIONAL")
        )

    def test_una_colision_recibe_un_sufijo_numerado(self):
        self.assertEqual(
            scjn.mint_abrev("LEY Federal de Cine y el Audiovisual", {"lfca"}), "lfca-2"
        )
        self.assertEqual(
            scjn.mint_abrev("LEY Federal de Cine y el Audiovisual", {"lfca", "lfca-2"}),
            "lfca-3",
        )

    def test_el_resultado_ya_es_un_slug(self):
        # Unlike the 14 historical `abrev` with an underscore, a minted one
        # never has to be normalized to become the release's asset name.
        for nombre in ("LEY de Ingresos de la Federación para 2026",
                       "PRESUPUESTO de Egresos de la Federación"):
            abrev = scjn.mint_abrev(nombre)
            self.assertEqual(scjn.slug_instrumento({"abrev": abrev}), abrev)


class TestMergeCatalogWithPrevious(unittest.TestCase):
    """The seed overlaid on the catalogue already on disk (issue #186)."""

    SEED = [
        {"abrev": "lft", "nombre": "LEY Federal del Trabajo"},
        {"abrev": "lfca", "nombre": "LEY Federal de Cine y el Audiovisual"},
    ]

    def test_ordena_por_slug_que_es_el_orden_del_indice_del_release(self):
        catalogo, faltantes = scjn.merge_catalog_with_previous(self.SEED, None)

        self.assertEqual([e["abrev"] for e in catalogo], ["lfca", "lft"])
        self.assertEqual(faltantes, [])

    def test_conserva_el_abrev_previo_verbatim_aunque_el_slug_lo_normalice(self):
        # `lif_2026` in the catalogue is `lif-2026` in the release. The
        # `abrev` is the asset name of an already published law, so it is
        # the release's slug that gives way, not the other way round.
        previo = [{"nombre": "LEY de Ingresos vieja", "abrev": "lif_2026"}]

        catalogo, faltantes = scjn.merge_catalog_with_previous(
            [{"abrev": "lif-2026", "nombre": "LEY de Ingresos de la Federacion"}], previo
        )

        self.assertEqual(faltantes, [])
        self.assertEqual(catalogo, [{"nombre": "LEY de Ingresos de la Federacion",
                                     "abrev": "lif_2026"}])

    def test_el_catalogo_anterior_es_el_piso_y_lo_ausente_se_reporta(self):
        previo = [{"nombre": "ORDENANZA General de la Armada", "abrev": "oga"}]

        catalogo, faltantes = scjn.merge_catalog_with_previous(self.SEED, previo)

        self.assertEqual([e["abrev"] for e in catalogo], ["lfca", "lft", "oga"])
        self.assertEqual([e["abrev"] for e in faltantes], ["oga"])

    def test_conserva_los_campos_escritos_a_mano_del_catalogo_anterior(self):
        previo = [{"nombre": "viejo", "abrev": "lft", "nombre_scjn": "LEY FEDERAL DEL TRABAJO"}]

        catalogo, _ = scjn.merge_catalog_with_previous(self.SEED, previo)
        lft, = [e for e in catalogo if e["abrev"] == "lft"]

        self.assertEqual(lft["nombre_scjn"], "LEY FEDERAL DEL TRABAJO")
        self.assertEqual(lft["nombre"], "LEY Federal del Trabajo")


class TestApplyActualizado(unittest.TestCase):
    def test_gana_la_fecha_mas_nueva_de_todas_las_fuentes(self):
        catalogo = [{"nombre": "LEY Federal del Trabajo", "abrev": "lft"}]

        resultado = scjn.apply_actualizado(
            catalogo, {"lft": "2025-11-14"}, {"lft": "2026-05-14"}
        )

        self.assertEqual(resultado[0]["actualizado"], "2026-05-14")

    def test_sin_fecha_el_campo_queda_ausente_no_en_none(self):
        catalogo = [{"nombre": "LEY", "abrev": "lfcpq", "actualizado": "2020-01-01"}]

        resultado = scjn.apply_actualizado(catalogo, {}, {})

        self.assertNotIn("actualizado", resultado[0])

    def test_conserva_la_posicion_del_campo_en_la_entrada(self):
        catalogo = [{"nombre": "LEY", "abrev": "lft", "actualizado": "2020-01-01",
                     "nombre_scjn": "LEY"}]

        resultado = scjn.apply_actualizado(catalogo, {"lft": "2026-05-14"})

        self.assertEqual(list(resultado[0]), ["nombre", "abrev", "actualizado",
                                              "nombre_scjn"])

    def test_no_muta_el_catalogo_recibido(self):
        catalogo = [{"nombre": "LEY", "abrev": "lft"}]

        scjn.apply_actualizado(catalogo, {"lft": "2026-05-14"})

        self.assertNotIn("actualizado", catalogo[0])


class TestReconstruccionDelCatalogo(unittest.TestCase):
    """The three steps `extract_scjn_titles.py` composes, in order, over the
    one case issue #186 requires to survive a rebuild: `lisipl`'s manual
    `nombre_scjn`. It is the only entry in the real catalogue that has one,
    and it exists precisely because no automated step can re-derive it."""

    LISIPL = (
        "IMPUESTO sobre Servicios Expresamente Declarados de Interés Público por Ley, "
        "en los que Intervengan Empresas Concesionarias de Bienes del Dominio Directo "
        "de la Nación (LEY que establece, reforma y adiciona las disposiciones "
        "relativas a diversos impuestos)"
    )
    NOMBRE_SCJN = (
        "LEY DEL IMPUESTO SOBRE SERVICIOS EXPRESAMENTE DECLARADOS DE INTERES PUBLICO "
        "POR LEY"
    )

    def test_el_override_nombre_scjn_sobrevive_a_una_reconstruccion(self):
        previo = [{"nombre": self.LISIPL, "abrev": "lisipl",
                   "nombre_scjn": self.NOMBRE_SCJN}]
        seed = [{"abrev": "lisipl", "nombre": self.LISIPL}]

        catalogo, faltantes = scjn.merge_catalog_with_previous(seed, previo)
        catalogo = scjn.apply_actualizado(catalogo, {"lisipl": "2026-01-15"})
        catalogo = scjn.merge_catalog_overrides(catalogo, previo)

        self.assertEqual(faltantes, [])
        self.assertEqual(catalogo, [{
            "nombre": self.LISIPL,
            "abrev": "lisipl",
            "nombre_scjn": self.NOMBRE_SCJN,
            "actualizado": "2026-01-15",
        }])


class TestResolveLinks(unittest.TestCase):
    """Issue #187: the content-diff confirmation becomes the link when title
    matching alone could not pick one."""

    @staticmethod
    def _enlazada(fecha, cod=None):
        return scjn.VersionEnlazada(fecha, cod, Path(f"{fecha}.md"))

    @staticmethod
    def _confirmacion(fecha, cod=None, score=None):
        return scjn.ContentDiffConfirmation(fecha, cod, score)

    def test_un_enlace_por_titulo_manda_sobre_el_diff(self):
        # The title link is the stronger claim in the sense that matters
        # here: it is the only candidate that named the law that day, so
        # there was never a choice for the diff to make.
        resuelto = scjn.resolve_links(
            [self._enlazada("14-06-2024", 100)],
            [self._confirmacion("14-06-2024", 200, 0.9)],
            {"14-06-2024": [100]},
        )

        self.assertEqual(resuelto, [(100, "linked")])

    def test_una_fecha_ambigua_confirmada_por_diff_queda_enlazada(self):
        resuelto = scjn.resolve_links(
            [self._enlazada("14-06-2024")],
            [self._confirmacion("14-06-2024", 200, 0.82)],
            {"14-06-2024": [100, 200]},
        )

        self.assertEqual(resuelto, [(200, scjn.ESTADO_ENLACE_CONTENT_DIFF)])

    def test_una_fecha_ambigua_sin_confirmacion_sigue_ambigua(self):
        resuelto = scjn.resolve_links(
            [self._enlazada("14-06-2024")],
            [self._confirmacion("14-06-2024", None, 0.41)],
            {"14-06-2024": [100, 200]},
        )

        self.assertEqual(resuelto, [(None, "ambiguous")])

    def test_una_fecha_sin_candidatos_no_se_puede_promover(self):
        resuelto = scjn.resolve_links(
            [self._enlazada("14-06-2024")], [self._confirmacion("14-06-2024")], {}
        )

        self.assertEqual(resuelto, [(None, "none")])

    def test_nunca_promueve_un_codnota_que_otro_snapshot_ya_reclamo(self):
        # The guard that keeps issue #115's "an absent link is worth more
        # than a wrong one" true across the two mechanisms: each enforces
        # one-codNota-per-snapshot internally, neither knows about the
        # other's claims.
        resuelto = scjn.resolve_links(
            [self._enlazada("14-06-2024", 100), self._enlazada("20-11-2025")],
            [self._confirmacion("14-06-2024"), self._confirmacion("20-11-2025", 100, 0.95)],
            {"14-06-2024": [100], "20-11-2025": [100, 300]},
        )

        self.assertEqual(resuelto, [(100, "linked"), (None, "ambiguous")])

    def test_dos_promociones_del_mismo_codnota_solo_se_conceden_una_vez(self):
        resuelto = scjn.resolve_links(
            [self._enlazada("14-06-2024"), self._enlazada("20-11-2025")],
            [self._confirmacion("14-06-2024", 200, 0.9),
             self._confirmacion("20-11-2025", 200, 0.9)],
            {"14-06-2024": [100, 200], "20-11-2025": [200, 300]},
        )

        self.assertEqual(
            resuelto, [(200, scjn.ESTADO_ENLACE_CONTENT_DIFF), (None, "ambiguous")]
        )

    def test_devuelve_una_entrada_por_snapshot_en_orden(self):
        enlazadas = [self._enlazada("01-01-2020", 1), self._enlazada("02-02-2021"),
                     self._enlazada("03-03-2022", 3)]
        confirmaciones = [self._confirmacion("01-01-2020"),
                          self._confirmacion("02-02-2021", 2, 0.7),
                          self._confirmacion("03-03-2022")]

        resuelto = scjn.resolve_links(
            enlazadas, confirmaciones,
            {"02-02-2021": [2, 22], "03-03-2022": [3]},
        )

        self.assertEqual([cod for cod, _ in resuelto], [1, 2, 3])
