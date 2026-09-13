#!/usr/bin/env bash
set -euo pipefail

uv pip install --system -e 'packages/dofjson[test]'
# document2md has its own repository since issue #234; until 0.3.0 reaches PyPI
# it comes from a pinned commit there (issue #235 replaces this with the plain
# PyPI name). Installed before nota2md so its image/PDF OCR path is importable.
uv pip install --system 'document2md @ git+https://github.com/INGEOTEC/document2md@5bdaa2d700a5b2f0e5f14704daef56ff58dd3d8f'
uv pip install --system -e 'packages/nota2md[test]'
uv pip install --system -r requirements.txt
uv pip install --system -r docs/requirements.txt
