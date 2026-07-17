# LegalIA

A monorepo of Python packages for the analysis of legal texts in the Mexican
context, developed by [INGEOTEC](https://github.com/INGEOTEC).

## Packages

| Package | Description |
|---|---|
| [dof2md](packages/dof2md) | Downloads editions of Mexico's official gazette (DOF, Diario Oficial de la Federación) as PDF and converts them (and scanned page images) to Markdown. |
| [dofjson](packages/dofjson) | Prototype client for SIDOF's undocumented JSON open data service for the DOF. |
| [nota2md](packages/nota2md) | Builds the Markdown of a single DOF note (by codNota), from its HTML content or by OCR'ing its scanned page images. |

Each package lives under `packages/<name>/` with its own `pyproject.toml`,
dependencies, version, and tests — installed and released independently.

## Development

Install a package in editable mode with its test dependencies:

```bash
pip install -e "packages/dof2md[test]"
pytest packages/dof2md
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
