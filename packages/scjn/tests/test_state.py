import tempfile
import unittest
from pathlib import Path

import scjn.state as state
from scjn.api import Reforma


class TestEstadoPorInstrumento(unittest.TestCase):
    """Issue #148: per-instrument freshness, so one law can be refreshed
    alone without waiting for a full sweep of the collection."""

    def test_lee_estado_sin_archivo(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(state.lee_estado(Path(tmp)), {})

    def test_lee_estado_malformado_es_como_no_tenerlo(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / state.ARCHIVO_ESTADO).write_text("{no es json")
            self.assertEqual(state.lee_estado(Path(tmp)), {})

    def test_escribe_estado_fusiona_en_vez_de_sobrescribir(self):
        # fetch_scjn_legislacion.py escribe actualizado/rastreado y
        # enlaza_scjn_legislacion.py enlazado: ninguno borra al otro.
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            state.escribe_estado(destino, actualizado="2026-06-09", rastreado="2026-08-27")
            state.escribe_estado(destino, enlazado="2026-08-28")
            self.assertEqual(
                state.lee_estado(destino),
                {
                    "actualizado": "2026-06-09",
                    "rastreado": "2026-08-27",
                    "enlazado": "2026-08-28",
                },
            )

    def test_pendiente_nunca_rastreado(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                state.motivo_pendiente({"actualizado": "2026-06-09"}, Path(tmp), "2026-08-27"),
                state.PENDIENTE_NUNCA_RASTREADO,
            )

    def test_pendiente_sin_actualizado_en_el_catalogo(self):
        # lisipl/lcmopfih/lfcpq: nada los fecha (ni la tabla de reformas de
        # la SCJN ni los titulos del DOF), asi que no hay forma de saber si
        # cambiaron.
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            self.assertEqual(
                state.motivo_pendiente({}, destino, "2026-08-27"),
                state.PENDIENTE_SIN_ACTUALIZADO,
            )

    def test_estado_por_ley_tiene_precedencia_sobre_el_rastreo_completo(self):
        # Una ley rastreada sola queda al dia aunque ningun barrido completo
        # haya corrido despues (corpus_date viejo, o inexistente).
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            state.escribe_estado(destino, actualizado="2026-06-09", rastreado="2026-08-29")
            self.assertIsNone(state.motivo_pendiente({"actualizado": "2026-06-09"}, destino, None))

    def test_cambio_detectado_contra_el_estado_de_la_ley(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            state.escribe_estado(destino, actualizado="2026-06-09", rastreado="2026-08-29")
            self.assertEqual(
                state.motivo_pendiente({"actualizado": "2026-07-01"}, destino, "2026-08-29"),
                state.PENDIENTE_CAMBIO,
            )

    def test_sin_estado_cae_al_criterio_de_coleccion(self):
        # Compatibilidad con el corpus actual, que no tiene estado.json:
        # se sigue decidiendo con .rastreo_completo.json (Mecanismo 2, #124).
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            self.assertIsNone(
                state.motivo_pendiente({"actualizado": "2026-06-09"}, destino, "2026-08-27")
            )
            self.assertEqual(
                state.motivo_pendiente({"actualizado": "2026-06-09"}, destino, "2026-01-01"),
                state.PENDIENTE_CAMBIO,
            )


def _reforma(fecha: str, reforma_id, tiene_articulos: bool = True) -> Reforma:
    return Reforma(reformaId=reforma_id, fecha_publicacion=fecha, tieneArticulos=tiene_articulos)


class TestReformasFaltantes(unittest.TestCase):
    """Issue #211: completeness by row comparison against the SCJN's own
    reform table, instead of a date comparison that cannot see a gap in the
    middle (the `lfd` 92-vs-98 case, issue #178)."""

    def test_nada_falta_cuando_el_corpus_tiene_toda_la_tabla(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "24-05-2026.md").write_text("x")
            (destino / "01-01-2020.md").write_text("x")
            reformas = [_reforma("24-05-2026", 2), _reforma("01-01-2020", 1)]
            self.assertEqual(state.reformas_faltantes(reformas, destino), [])

    def test_detecta_un_hueco_en_medio(self):
        # El caso lfd (issue #178): la reforma mas nueva y la mas vieja
        # estan, pero una del medio nunca se bajo -- ninguna comparacion de
        # fechas ve esto, porque la fecha "mas reciente en disco" no cambia.
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "24-05-2026.md").write_text("x")
            (destino / "01-01-2020.md").write_text("x")
            reformas = [
                _reforma("24-05-2026", 3),
                _reforma("14-06-2023", 2),  # falta esta
                _reforma("01-01-2020", 1),
            ]
            faltantes = state.reformas_faltantes(reformas, destino)
            self.assertEqual([r.reformaId for r in faltantes], [2])

    def test_dos_reformas_el_mismo_dia_se_distinguen_por_posicion(self):
        # 39 fechas del CPEUM tienen hasta 4 decretos el mismo dia -- el
        # nombre de archivo depende de la posicion entre las filas de esa
        # fecha en el orden propio de la tabla (mas reciente primero), el
        # mismo convenio que scjn.header._orden_repeticion lee de vuelta.
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "24-05-2026.md").write_text("x")  # solo la primera de las dos
            reformas = [_reforma("24-05-2026", 2), _reforma("24-05-2026", 1)]
            faltantes = state.reformas_faltantes(reformas, destino)
            # La primera fila de esa fecha (reformaId 2) escribio
            # "24-05-2026.md" (ya en disco); la segunda (reformaId 1) le
            # toca "24-05-2026-2.md", que no esta -- es la que falta, no
            # "las dos porque coinciden en fecha".
            self.assertEqual([r.reformaId for r in faltantes], [1])

    def test_ignora_filas_sin_articulos(self):
        # tieneArticulos=False es un hueco que este corpus nunca puede
        # cerrar (descarga_ordenamiento tampoco escribe nada para ella) --
        # contarla como faltante marcaria la misma fila para siempre.
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            reformas = [_reforma("24-05-2026", 1, tiene_articulos=False)]
            self.assertEqual(state.reformas_faltantes(reformas, destino), [])

    def test_reproduce_el_caso_lfd_92_contra_98(self):
        # issue #178: 98 filas en la tabla de la SCJN, 92 snapshots en disco
        # -- 6 huecos, no detectables por fecha porque la mas nueva esta.
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            reformas = [_reforma(f"{(i % 28) + 1:02d}-01-{2000 + i}", i) for i in range(1, 99)]
            faltantes_ids = {7, 23, 41, 55, 68, 90}
            for reforma in reformas:
                if reforma.reformaId not in faltantes_ids:
                    (destino / f"{reforma.fecha_publicacion}.md").write_text("x")
            faltantes = state.reformas_faltantes(reformas, destino)
            self.assertEqual({r.reformaId for r in faltantes}, faltantes_ids)
            self.assertEqual(len(reformas) - len(faltantes), 92)


class TestMotivoPendienteConComparacionPorFila(unittest.TestCase):
    """`motivo_pendiente(..., reformas=...)`: the row comparison the planner
    prefers when a law has an `id_ordenamiento` to fetch its table by
    (issue #211) -- `actualizado` is not consulted at all in this mode."""

    def test_faltan_reformas_cuando_hay_un_hueco(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            reformas = [_reforma("24-05-2026", 2), _reforma("01-01-2020", 1)]
            self.assertEqual(
                state.motivo_pendiente({}, destino, None, reformas=reformas),
                state.PENDIENTE_FALTAN_REFORMAS,
            )

    def test_ninguna_pendiente_cuando_la_tabla_esta_completa(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            reformas = [_reforma("01-01-2020", 1)]
            self.assertIsNone(state.motivo_pendiente({}, destino, None, reformas=reformas))

    def test_nunca_rastreado_tiene_prioridad_sobre_la_comparacion_por_fila(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            reformas = [_reforma("01-01-2020", 1)]
            self.assertEqual(
                state.motivo_pendiente({}, destino, None, reformas=reformas),
                state.PENDIENTE_NUNCA_RASTREADO,
            )

    def test_sin_id_ordenamiento_recae_en_la_comparacion_de_fecha(self):
        # reformas=None (el default) es exactamente el modo de antes --
        # una ley sin id_ordenamiento cae aqui, reportada por el planificador
        # como "modo de respaldo", no silenciosamente.
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            (destino / "01-01-2020.md").write_text("x")
            state.escribe_estado(destino, actualizado="2026-06-09", rastreado="2026-08-29")
            self.assertEqual(
                state.motivo_pendiente({"actualizado": "2026-07-01"}, destino, "2026-08-29"),
                state.PENDIENTE_CAMBIO,
            )
