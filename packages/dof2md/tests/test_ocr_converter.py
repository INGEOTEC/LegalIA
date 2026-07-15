import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dof2md.ocr_converter import convert_scanned_to_markdown


def _fake_mineru_run(cmd, check):
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


class TestConvertScannedToMarkdown(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.pdf_path = Path(self.tmpdir.name) / "02011980-MAT.pdf"
        self.pdf_path.write_bytes(b"%PDF-1.4 fake")
        self.md_path = Path(self.tmpdir.name) / "02011980-MAT.md"

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("dof2md.ocr_converter.subprocess.run", side_effect=_fake_mineru_run)
    @patch("dof2md.ocr_converter.shutil.which", return_value="/usr/local/bin/mineru")
    def test_invokes_mineru_with_pipeline_backend(self, mock_which, mock_run):
        convert_scanned_to_markdown(self.pdf_path, self.md_path)

        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], "mineru")
        self.assertIn("-b", cmd)
        self.assertEqual(cmd[cmd.index("-b") + 1], "pipeline")
        self.assertEqual(cmd[cmd.index("-p") + 1], str(self.pdf_path))

    @patch("dof2md.ocr_converter.subprocess.run", side_effect=_fake_mineru_run)
    @patch("dof2md.ocr_converter.shutil.which", return_value="/usr/local/bin/mineru")
    def test_relocates_images_next_to_output(self, mock_which, mock_run):
        convert_scanned_to_markdown(self.pdf_path, self.md_path)

        images_dir = self.md_path.parent / "02011980-MAT_images"
        self.assertTrue((images_dir / "abc123.jpg").exists())
        self.assertIn(
            "](02011980-MAT_images/abc123.jpg)", self.md_path.read_text(encoding="utf-8")
        )

    @patch("dof2md.ocr_converter.shutil.which", return_value=None)
    def test_raises_clear_error_when_mineru_missing(self, mock_which):
        with self.assertRaises(RuntimeError):
            convert_scanned_to_markdown(self.pdf_path, self.md_path)


if __name__ == "__main__":
    unittest.main()
