#!/usr/bin/env bash
set -euo pipefail

uv pip install --system -e 'packages/dofjson[test]'
# The `ocr` extra is what declares the OCR backend (`document2md>=0.3.0`, its own
# repository since issue #234, from PyPI since issue #235), so installing the
# extra keeps nota2md's image/PDF path importable without naming it twice.
uv pip install --system -e 'packages/nota2md[ocr,test]'
uv pip install --system -r requirements.txt
uv pip install --system -r docs/requirements.txt
