import tempfile
import unittest
from pathlib import Path

import scjn.state as state


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
