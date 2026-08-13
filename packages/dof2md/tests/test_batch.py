import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dof2md.batch import BatchConverter
from dof2md.mineru_server import ENV_VAR as MINERU_API_URL_ENV_VAR


class TestBatchConverterServerLifecycle(unittest.TestCase):
    def setUp(self):
        self._original_env = os.environ.pop(MINERU_API_URL_ENV_VAR, None)

    def tearDown(self):
        if self._original_env is None:
            os.environ.pop(MINERU_API_URL_ENV_VAR, None)
        else:
            os.environ[MINERU_API_URL_ENV_VAR] = self._original_env

    @patch("dof2md.batch.MineruServer")
    def test_enter_starts_a_server_when_none_is_running(self, mock_server_cls):
        mock_server_cls.return_value = MagicMock()

        with BatchConverter():
            mock_server_cls.assert_called_once_with()
            mock_server_cls.return_value.start.assert_called_once()

        mock_server_cls.return_value.stop.assert_called_once()

    @patch("dof2md.batch.MineruServer")
    def test_skips_starting_a_server_when_caller_already_has_one(self, mock_server_cls):
        os.environ[MINERU_API_URL_ENV_VAR] = "http://127.0.0.1:9999"

        with BatchConverter():
            mock_server_cls.assert_not_called()


class TestBatchConverterCall(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.outdir = Path(self.tmpdir.name)
        self.pdf_path = self.outdir / "nota-300.pdf"
        self.pdf_path.write_bytes(b"%PDF-1.4 fake")
        self.image_paths = [self.outdir / "nota-200-p1.jpg", self.outdir / "nota-200-p2.jpg"]
        for p in self.image_paths:
            p.write_bytes(b"\xff\xd8\xff fake jpeg")
        # Exercise BatchConverter.__call__ directly, without __enter__'ing it
        # (its own server lifecycle is covered separately above).
        self.converter = BatchConverter()

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dof2md.converter.convert_to_markdown")
    def test_single_path_goes_through_convert_to_markdown(self, mock_convert):
        dest = self.converter(self.pdf_path, self.outdir, "nota-300.md")

        self.assertEqual(dest, self.outdir / "nota-300.md")
        mock_convert.assert_called_once()
        (pdf_arg, md_arg), _ = mock_convert.call_args
        self.assertEqual(pdf_arg, self.pdf_path)
        self.assertEqual(md_arg, dest)

    @patch("dof2md.converter.convert_images_to_markdown")
    def test_list_of_paths_goes_through_convert_images_to_markdown(self, mock_convert):
        dest = self.converter(self.image_paths, self.outdir, "nota-200.md")

        self.assertEqual(dest, self.outdir / "nota-200.md")
        mock_convert.assert_called_once()
        (images_arg, md_arg), _ = mock_convert.call_args
        self.assertEqual(images_arg, self.image_paths)
        self.assertEqual(md_arg, dest)

    @patch("dof2md.converter.convert_to_markdown")
    def test_no_titulo_keeps_the_whole_conversion_uncut(self, mock_convert):
        mock_convert.side_effect = lambda pdf, md, **kw: md.write_text(
            "todo el contenido, sin recortar", encoding="utf-8"
        )

        dest = self.converter(self.pdf_path, self.outdir, "nota-300.md")

        self.assertEqual(dest.read_text(encoding="utf-8"), "todo el contenido, sin recortar")

    @patch("dof2md.converter.convert_to_markdown")
    def test_titulo_cuts_the_result(self, mock_convert):
        mock_convert.side_effect = lambda pdf, md, **kw: md.write_text(
            "resto de la nota anterior.\n\n"
            "## Acuerdo de regularización de títulos\n\n"
            "Cuerpo del acuerdo.\n\n"
            "## Norma Oficial Mexicana NOM-042-NUCL\n\n"
            "Nota siguiente, excluir.\n",
            encoding="utf-8",
        )

        dest = self.converter(
            self.pdf_path, self.outdir, "nota-300.md",
            "Acuerdo de regularización de títulos",
            "Norma Oficial Mexicana NOM-042-NUCL",
        )

        text = dest.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("## Acuerdo de regularización"))
        self.assertIn("Cuerpo del acuerdo.", text)
        self.assertNotIn("NOM-042-NUCL", text)
        self.assertNotIn("resto de la nota anterior", text)

    @patch("dof2md.converter.convert_to_markdown")
    def test_keep_pages_writes_full_uncut_copy_alongside(self, mock_convert):
        mock_convert.side_effect = lambda pdf, md, **kw: md.write_text(
            "## T\n\nCuerpo.\n", encoding="utf-8"
        )

        self.converter(
            self.pdf_path, self.outdir, "nota-300.md", "T", None, keep_pages=True
        )

        self.assertEqual(
            (self.outdir / "nota-300.full.md").read_text(encoding="utf-8"),
            "## T\n\nCuerpo.\n",
        )

    @patch("dof2md.converter.convert_to_markdown")
    def test_keep_pages_without_titulo_writes_nothing_extra(self, mock_convert):
        mock_convert.side_effect = lambda pdf, md, **kw: md.write_text(
            "sin recorte", encoding="utf-8"
        )

        self.converter(self.pdf_path, self.outdir, "nota-300.md", keep_pages=True)

        self.assertFalse((self.outdir / "nota-300.full.md").exists())

    @patch("dof2md.converter.convert_to_markdown")
    def test_forwards_timeout_and_keep_mineru_output(self, mock_convert):
        self.converter(
            self.pdf_path, self.outdir, "nota-300.md",
            timeout=42, keep_mineru_output=True,
        )

        _, kwargs = mock_convert.call_args
        self.assertEqual(kwargs["timeout"], 42)
        self.assertTrue(kwargs["keep_mineru_output"])

    @patch("dof2md.converter.convert_to_markdown")
    def test_creates_outdir_if_missing(self, mock_convert):
        nested = self.outdir / "no" / "existe" / "aun"
        mock_convert.side_effect = lambda pdf, md, **kw: md.write_text("x", encoding="utf-8")

        dest = self.converter(self.pdf_path, nested, "nota-300.md")

        self.assertTrue(dest.exists())


if __name__ == "__main__":
    unittest.main()
