"""Helpers the `scripts/embeddings` test files share (issue #305): one definition each, imported
with ``from conftest import ...`` (this directory is on `sys.path` while pytest collects it)."""

from pathlib import Path


def quiet(*args):
    """A `log=` callback that prints nothing."""


def write_units(directory: Path, coleccion: str, rows) -> None:
    """A `units.parquet` of `rows` (`clave, nombre, unit_type, eId, text_sha1, text` tuples)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    directory.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({
        "coleccion": pa.array([coleccion] * len(rows), type=pa.string()),
        "clave": pa.array([r[0] for r in rows], type=pa.string()),
        "nombre": pa.array([r[1] for r in rows], type=pa.string()),
        "unit_type": pa.array([r[2] for r in rows], type=pa.string()),
        "eId": pa.array([r[3] for r in rows], type=pa.string()),
        "text_sha1": pa.array([r[4] for r in rows], type=pa.string()),
        "text": pa.array([r[5] for r in rows], type=pa.string()),
    }), directory / "units.parquet")
