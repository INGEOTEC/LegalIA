"""The dependency direction is one way (issue #206/#207): `scjn` imports
neither `nota2md` nor `dofjson`, so `nota2md` can depend on `scjn` without a
cycle. Enforced here, not just by intention -- the grep this test runs is
the same one the issue asks to keep."""

import re
import unittest
from pathlib import Path

_PROHIBITED = re.compile(r"nota2md|dofjson")
_PACKAGE_DIR = Path(__file__).resolve().parents[1] / "scjn"


class TestDependencyDirection(unittest.TestCase):
    def test_no_source_file_mentions_nota2md_or_dofjson(self):
        hits = [
            f"{path}:{lineno}: {line}"
            for path in sorted(_PACKAGE_DIR.rglob("*.py"))
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if _PROHIBITED.search(line)
        ]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
