import tempfile
import unittest
from pathlib import Path

import scjn.header as header


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

            campos = header.lee_cabecera(archivo)

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

            campos = header.lee_cabecera(archivo)

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

            versiones = header.versiones_de_directorio(outdir)

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

            versiones = header.versiones_de_directorio(outdir)

            self.assertEqual(
                [v.archivo.name for v in versiones], ["14-06-2024.md", "14-06-2024-2.md"]
            )
