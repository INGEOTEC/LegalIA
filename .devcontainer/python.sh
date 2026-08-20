#!/usr/bin/env bash
set -euo pipefail

uv pip install --system -e 'packages/dof2md[test]'
uv pip install --system -e 'packages/dofjson[test]'
uv pip install --system -e 'packages/nota2md[test]'
uv pip install --system -e 'packages/leyesmx[test]'
uv pip install --system -r requirements.txt
