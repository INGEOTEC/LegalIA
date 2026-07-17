import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dof2md.converter import (
    DEFAULT_TIMEOUT_SECONDS,
    convert_images_to_markdown,
    convert_to_markdown,
)
from dof2md.mineru_server import ENV_VAR as MINERU_API_URL_ENV_VAR


def _fake_mineru_run(cmd, check, timeout=None):
    """Simulates mineru writing its output layout under the -o directory."""
    outdir = Path(cmd[cmd.index("-o") + 1])
    pdf_path = Path(cmd[cmd.index("-p") + 1])
    auto_dir = outdir / pdf_path.stem / "auto"
    auto_dir.mkdir(parents=True)
    (auto_dir / f"{pdf_path.stem}.md").write_text(
        "# Title\n\n![](images/abc123.jpg)\n", encoding="utf-8"
    )
    images_dir = auto_dir / "images"
    images_dir.mkdir()
    (images_dir / "abc123.jpg").write_bytes(b"fake-image-bytes")


def _fake_mineru_run_with_html_table(cmd, check, timeout=None):
    """Simulates mineru emitting a complex table as raw HTML (its real
    fallback), so we can check dof2md rewrites it to a Markdown table."""
    outdir = Path(cmd[cmd.index("-o") + 1])
    input_path = Path(cmd[cmd.index("-p") + 1])
    auto_dir = outdir / input_path.stem / "auto"
    auto_dir.mkdir(parents=True)
    (auto_dir / f"{input_path.stem}.md").write_text(
        "# Título\n\n<table><tr><td>Apartado</td><td>Págs.</td></tr>"
        "<tr><td>COMPETENCIA</td><td>13-14</td></tr></table>\n",
        encoding="utf-8",
    )


def _fake_mineru_run_text_only(cmd, check, timeout=None):
    """Like _fake_mineru_run but with no extracted figures; the Markdown text
    is derived from the input stem so multi-image output order is checkable."""
    outdir = Path(cmd[cmd.index("-o") + 1])
    input_path = Path(cmd[cmd.index("-p") + 1])
    auto_dir = outdir / input_path.stem / "auto"
    auto_dir.mkdir(parents=True)
    (auto_dir / f"{input_path.stem}.md").write_text(
        f"# OCR of {input_path.stem}\n", encoding="utf-8"
    )


class TestConvertToMarkdown(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.pdf_path = Path(self.tmpdir.name) / "02011980-MAT.pdf"
        self.pdf_path.write_bytes(b"%PDF-1.4 fake")
        self.md_path = Path(self.tmpdir.name) / "02011980-MAT.md"

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dof2md.converter.subprocess.run", side_effect=_fake_mineru_run)
    @patch("dof2md.converter.shutil.which", return_value="/usr/local/bin/mineru")
    def test_invokes_mineru_with_pipeline_backend(self, mock_which, mock_run):
        convert_to_markdown(self.pdf_path, self.md_path)

        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], "mineru")
        self.assertIn("-b", cmd)
        self.assertEqual(cmd[cmd.index("-b") + 1], "pipeline")
        self.assertEqual(cmd[cmd.index("-p") + 1], str(self.pdf_path))

    @patch("dof2md.converter.subprocess.run", side_effect=_fake_mineru_run)
    @patch("dof2md.converter.shutil.which", return_value="/usr/local/bin/mineru")
    def test_relocates_images_next_to_output(self, mock_which, mock_run):
        convert_to_markdown(self.pdf_path, self.md_path)

        images_dir = self.md_path.parent / "02011980-MAT_images"
        self.assertTrue((images_dir / "abc123.jpg").exists())
        self.assertIn(
            "](02011980-MAT_images/abc123.jpg)", self.md_path.read_text(encoding="utf-8")
        )

    @patch("dof2md.converter.shutil.which", return_value=None)
    def test_raises_clear_error_when_mineru_missing(self, mock_which):
        with self.assertRaises(RuntimeError):
            convert_to_markdown(self.pdf_path, self.md_path)

    @patch("dof2md.converter.subprocess.run", side_effect=_fake_mineru_run_with_html_table)
    @patch("dof2md.converter.shutil.which", return_value="/usr/local/bin/mineru")
    def test_rewrites_html_tables_to_markdown(self, mock_which, mock_run):
        convert_to_markdown(self.pdf_path, self.md_path)

        text = self.md_path.read_text(encoding="utf-8")
        self.assertNotIn("<table>", text)
        self.assertIn("| Apartado | Págs. |", text)
        self.assertIn("| COMPETENCIA | 13-14 |", text)

    @patch("dof2md.converter.subprocess.run", side_effect=_fake_mineru_run)
    @patch("dof2md.converter.shutil.which", return_value="/usr/local/bin/mineru")
    def test_omits_api_url_when_env_var_unset(self, mock_which, mock_run):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(MINERU_API_URL_ENV_VAR, None)
            convert_to_markdown(self.pdf_path, self.md_path)

        cmd = mock_run.call_args[0][0]
        self.assertNotIn("--api-url", cmd)

    @patch("dof2md.converter.subprocess.run", side_effect=_fake_mineru_run)
    @patch("dof2md.converter.shutil.which", return_value="/usr/local/bin/mineru")
    def test_passes_api_url_when_env_var_set(self, mock_which, mock_run):
        with patch.dict(os.environ, {MINERU_API_URL_ENV_VAR: "http://127.0.0.1:9999"}):
            convert_to_markdown(self.pdf_path, self.md_path)

        cmd = mock_run.call_args[0][0]
        self.assertIn("--api-url", cmd)
        self.assertEqual(cmd[cmd.index("--api-url") + 1], "http://127.0.0.1:9999")

    @patch("dof2md.converter.subprocess.run", side_effect=_fake_mineru_run)
    @patch("dof2md.converter.shutil.which", return_value="/usr/local/bin/mineru")
    def test_passes_default_timeout_to_subprocess(self, mock_which, mock_run):
        convert_to_markdown(self.pdf_path, self.md_path)

        self.assertEqual(mock_run.call_args.kwargs["timeout"], DEFAULT_TIMEOUT_SECONDS)

    @patch("dof2md.converter.subprocess.run", side_effect=_fake_mineru_run)
    @patch("dof2md.converter.shutil.which", return_value="/usr/local/bin/mineru")
    def test_passes_custom_timeout_to_subprocess(self, mock_which, mock_run):
        convert_to_markdown(self.pdf_path, self.md_path, timeout=42)

        self.assertEqual(mock_run.call_args.kwargs["timeout"], 42)

    @patch(
        "dof2md.converter.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="mineru", timeout=3600),
    )
    @patch("dof2md.converter.shutil.which", return_value="/usr/local/bin/mineru")
    def test_propagates_timeout_expired(self, mock_which, mock_run):
        with self.assertRaises(subprocess.TimeoutExpired):
            convert_to_markdown(self.pdf_path, self.md_path)


class TestConvertImagesToMarkdown(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.md_path = Path(self.tmpdir.name) / "nota-5793654.md"
        self.images = [
            Path(self.tmpdir.name) / "nota-5793654-20260715-080-U-000.jpg",
            Path(self.tmpdir.name) / "nota-5793654-20260715-081-U-000.jpg",
        ]
        for img in self.images:
            img.write_bytes(b"\xff\xd8\xff fake jpeg")

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dof2md.converter.subprocess.run", side_effect=_fake_mineru_run_text_only)
    @patch("dof2md.converter.shutil.which", return_value="/usr/local/bin/mineru")
    def test_ocrs_each_image_and_concatenates_in_order(self, mock_which, mock_run):
        convert_images_to_markdown(self.images, self.md_path)

        self.assertEqual(mock_run.call_count, 2)
        text = self.md_path.read_text(encoding="utf-8")
        first = text.index("nota-5793654-20260715-080-U-000")
        second = text.index("nota-5793654-20260715-081-U-000")
        self.assertLess(first, second)

    @patch("dof2md.converter.subprocess.run", side_effect=_fake_mineru_run)
    @patch("dof2md.converter.shutil.which", return_value="/usr/local/bin/mineru")
    def test_namespaces_extracted_figures_per_page(self, mock_which, mock_run):
        convert_images_to_markdown(self.images, self.md_path)

        text = self.md_path.read_text(encoding="utf-8")
        # Each page's figures live under nota-5793654_images/<image stem>/ so
        # same-named figures from different pages cannot collide.
        for img in self.images:
            self.assertTrue(
                (self.md_path.parent / "nota-5793654_images" / img.stem / "abc123.jpg").exists()
            )
            self.assertIn(f"](nota-5793654_images/{img.stem}/abc123.jpg)", text)

    @patch("dof2md.converter.shutil.which", return_value="/usr/local/bin/mineru")
    def test_rejects_empty_image_list(self, mock_which):
        with self.assertRaises(ValueError):
            convert_images_to_markdown([], self.md_path)

    @patch("dof2md.converter.shutil.which", return_value=None)
    def test_raises_when_mineru_missing(self, mock_which):
        with self.assertRaises(RuntimeError):
            convert_images_to_markdown(self.images, self.md_path)


if __name__ == "__main__":
    unittest.main()
