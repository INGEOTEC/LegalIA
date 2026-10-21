"""The per-instrument files of the Instruments page (issue #289).

    uv run --group viz pytest scripts/embeddings/tests/test_export_instrument_files.py -q

A toy corpus of **real Markdown** parsed by `md2akn` itself: five tiny
instruments over three collections (two `leyes`, a `lineamientos` numbered
`PRIMERO.-` so rule 8 fires, a `tratados` with `ARTÍCULO I`/`II` so rule 10
fires), written so that between them they produce a `loose` block, a duplicated
article number, a numbering gap, a document with no articles and none with no
transitorios, an astral character before an article, a text shared by two
instruments and one text truncated per a toy `vectors-manifest.json`. The
fixture writes a `legalvec` cache the way `build_units.py` does (`units.parquet`
through `text_units`, hand-written vectors), prepares a work directory, runs the
matrix and `nearest_texts.py` over it, and injects a snapshot reader returning
the toy Markdown. No Slurm, no network, no `scjn` cache.
"""

import hashlib
import json
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_units  # noqa: E402
import export_instrument_files as exporter  # noqa: E402
import instrument_matrix  # noqa: E402
import md2akn  # noqa: E402
import nearest_texts  # noqa: E402
import prepare_umap_input  # noqa: E402
from md2akn import parse_markdown, text_units  # noqa: E402
from test_instrument_matrix import stub_corpus_reader  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
MODEL = "Qwen/Qwen3-Embedding-4B"
SLUG = "qwen3-4b"
K = 4
COLLECTIONS = ("leyes", "lineamientos", "tratados")
NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)

ALFA = """---
nombre: LEY ALFA
---

PREÁMBULO de la ley 😀 alfa.

**TÍTULO PRIMERO**

**CAPÍTULO I**

Texto suelto antes de los artículos.

| Concepto | Cuota |

| Uno | 1.5 |

| Dos \\| con barra | **2.5** |

**Artículo 1.-** Texto compartido del primero.

**Artículo 2.-** Texto 𝔘 del segundo:

I. Fracción uno;

II. Fracción dos.

**Artículo 2.-** Texto repetido del segundo.

**Artículo 5.-** Texto larguísimo del quinto.

| Tramo | Tasa |

| Hasta 100 | **1.5** |

| Más de 100 | 2 |

**CAPÍTULO II**

Introducción del segundo capítulo.

| fila 01 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 02 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 03 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 04 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 05 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 06 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 07 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 08 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 09 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 10 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 11 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 12 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 13 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 14 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 15 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 16 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 17 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 18 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 19 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 20 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 21 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 22 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 23 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 24 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 25 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 26 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 27 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 28 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 29 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

| fila 30 | xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx |

**TRANSITORIOS**

Texto suelto de los transitorios.

**PRIMERO.-** Entra en vigor.
"""

BETA = """---
nombre: LEY BETA
---

**Artículo 1.-** Texto compartido del primero.

**Artículo 2.-** Otro texto de la ley beta.
"""

GAMMA = """---
nombre: LEY GAMMA
---

Texto de prosa sin artículos ni nada.
"""

LINEAMIENTOS = """---
nombre: LINEAMIENTOS P
---

Acuerdo por el que se emiten los lineamientos.

**PRIMERO.-** Se emiten los lineamientos uno.

**SEGUNDO.-** Se emiten los lineamientos dos.

**TERCERO.-** Se emiten los lineamientos tres.

**CUARTO.-** Se emiten los lineamientos cuatro.
"""

TRATADO = """---
nombre: TRATADO X
---

Los Estados Unidos Mexicanos y la República X, deseosos de cooperar.

**ARTÍCULO I**

Las Partes cooperarán entre sí.

**ARTÍCULO II**

Este Tratado entrará en vigor.

<!-- scjn:block orden=3 numero=0 kind=container type=chapter num="3" label="CAPÍTULO 3" -->

**CAPÍTULO 3**

**COMERCIO**

<!-- scjn:block orden=4 numero=3 kind=container type=annex num="3-A" label="ANEXO 3-A" -->

**ANEXO 3-A**

**LISTA DE MERCANCÍAS**

Las mercancías de la lista.

<!-- scjn:block orden=5 numero=3 kind=container type=annex num="3-B" label="ANEXO 3-B" -->

**ANEXO 3-B**

**LISTA DE SERVICIOS**

Los servicios de la lista.

<!-- scjn:block orden=7 numero=0 kind=container type=annex num="I" label="ANEXO I" -->

**ANEXO I**

**FORMULARIOS**

Los formularios del Tratado.

<!-- scjn:block orden=8 numero=0 kind=container type=appendix num="1" label="APÉNDICE 1" -->

**APÉNDICE 1**

**FORMULARIO DE NOTIFICACIÓN**

Texto del primer apéndice.

<!-- scjn:block orden=9 numero=0 kind=container type=appendix num="2" label="APÉNDICE 2" -->

**APÉNDICE 2**

**MODELO DE CERTIFICADO**

Texto del segundo apéndice.

<!-- scjn:block orden=14 numero=0 kind=other label="RESOLUCIÓN MSC.1(1)" -->

**RESOLUCIÓN MSC.1(1)**

El Comité adopta las enmiendas que figuran en el anexo.

**ANEXO**

**ENMIENDAS AL TRATADO**

**CAPÍTULO II-1**

**CONSTRUCCIÓN**

**PARTE A**

Generalidades de la construcción.

**PARTE B**

Compartimentado de la construcción.

**CAPÍTULO II-2**

**PREVENCIÓN DE INCENDIOS**

**PARTE A**

Generalidades de la prevención.

<!-- scjn:block orden=10 numero=0 kind=other label="NOTA EXPLICATIVA" -->

Las disposiciones marcadas modifican el Tratado.

<!-- scjn:block orden=11 numero=0 kind=other label="RESERVAS AL TRATADO" -->

Al firmar el presente texto, la delegación formula una reserva.

<!-- scjn:block orden=12 numero=0 kind=other label="FIRMA" -->

El Plenipotenciario firmó el Tratado.

<!-- scjn:block orden=13 numero=0 kind=other label="PROMULGACIÓN" -->

Por lo tanto, promulgo el presente Decreto.
"""

#: `(coleccion, clave) -> (nombre, archivo, codNota, markdown)`, in the order
#: `prepare_umap_input.py` will number them (leyes first, then the others).
DOCUMENTS = {
    ("leyes", "alfa"): ("LEY ALFA", "01-02-2020.md", 1234, ALFA),
    ("leyes", "beta"): ("LEY BETA", "05-06-2019.md", None, BETA),
    ("leyes", "gamma"): ("LEY GAMMA", "07-08-2018.md", None, GAMMA),
    ("lineamientos", "900"): ("LINEAMIENTOS P", "09-09-2021.md", None, LINEAMIENTOS),
    ("tratados", "700"): ("TRATADO X", "10-10-2017.md", None, TRATADO),
}


def quiet(*args):
    pass


def vector_of(sha: str):
    rng = np.random.default_rng(int(sha[:8], 16))
    return rng.normal(size=K).astype("float32").tolist()


def unit_rows(coleccion):
    rows = []
    for (c, clave), (nombre, archivo, codnota, markdown) in DOCUMENTS.items():
        if c != coleccion:
            continue
        for unit in text_units(parse_markdown(markdown), split_articles=False):
            rows.append({
                "coleccion": c, "clave": clave, "slug": clave if c == "leyes" else None,
                "nombre": nombre, "fecha_publicacion": archivo[:-3], "archivo": archivo,
                "codNota": codnota, "unit_type": unit.unit_type, "eId": unit.eId,
                "piece": unit.piece, "piece_eId": unit.piece_eId, "akn_type": unit.akn_type,
                "num": unit.num, "path": list(unit.path), "start_char": unit.start_char,
                "end_char": unit.end_char, "text": unit.text, "text_sha1": unit.text_sha1,
            })
    return rows


def write_vectors(directory: Path, name: str, hashes) -> None:
    pq.write_table(pa.table({
        "text_sha1": pa.array(list(hashes), type=pa.string()),
        "vector": pa.array([vector_of(h) for h in hashes], type=pa.list_(pa.float32())),
    }), directory / name)


def find_unit(clave, eid):
    coleccion = next(c for (c, k) in DOCUMENTS if k == clave)
    return next(r for r in unit_rows(coleccion) if r["clave"] == clave and r["eId"] == eid)


#: The article of `alfa` the toy manifest says the model truncated.
TRUNCATED_SHA = find_unit("alfa", "tit_PRIMERO__cap_I__art_5")["text_sha1"]


def write_cache(root: Path) -> Path:
    tags = {"leyes": "scjn-leyes-vectors", "lineamientos": "scjn-lineamientos-vectors",
            "tratados": "scjn-tratados-vectors"}
    for coleccion, tag in tags.items():
        directory = root / tag
        directory.mkdir(parents=True)
        rows = unit_rows(coleccion)
        pq.write_table(pa.Table.from_pylist(rows, schema=build_units.UNITS_SCHEMA),
                       directory / "units.parquet")
        owners = {}
        for row in rows:
            owners.setdefault(row["text_sha1"], set()).add(row["clave"])
        shared = sorted(h for h, claves in owners.items() if len(claves) > 1)
        for clave in dict.fromkeys(row["clave"] for row in rows):
            own = sorted({r["text_sha1"] for r in rows if r["clave"] == clave} - set(shared))
            write_vectors(directory, f"vectors-{clave}-{SLUG}-{K}.parquet", own)
        write_vectors(directory, f"vectors-shared-{SLUG}-{K}.parquet", shared)
        truncated = [{"text_sha1": TRUNCATED_SHA, "original_tokens": 99999}] \
            if coleccion == "leyes" else []
        (directory / "vectors-manifest.json").write_text(json.dumps(
            {"models": {SLUG: {"texts_truncated": truncated, "max_tokens_embedded": 3}}}))
    return root


def reader_for(coleccion):
    def read(clave, archivo):
        nombre, expected, _codnota, markdown = DOCUMENTS[(coleccion, clave)]
        assert archivo == expected
        return markdown
    return read


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """The legalvec cache, the prepared work directory, the matrix and #288's
    nearest texts over them; returns `{"cache", "work_dir", "tmp"}`."""
    tmp = tmp_path_factory.mktemp("instrument-files")
    cache = write_cache(tmp / "cache")
    work_dir = tmp / "work"
    prepare_umap_input.prepare(work_dir, model=MODEL, collections=COLLECTIONS, cache_dir=cache,
                               unique_names=True, corpus_reader=stub_corpus_reader(), log=quiet)
    instrument_matrix.build_matrix(work_dir, collections=COLLECTIONS, cache_dir=cache, log=quiet)
    nearest_texts.build_nearest_texts(work_dir, collections=COLLECTIONS, cache_dir=cache,
                                      log=quiet)
    return {"cache": cache, "work_dir": work_dir, "tmp": tmp}


def run_export(world, name="out", **kwargs):
    out_dir = world["tmp"] / name
    manifest = exporter.export(
        world["work_dir"], out_dir=out_dir, cache_dir=world["cache"],
        snapshot_reader=reader_for, now=NOW, log=quiet, **kwargs)
    return out_dir, manifest


@pytest.fixture(scope="module")
def exported(world):
    out_dir, manifest = run_export(world)
    return {"out_dir": out_dir, "manifest": manifest, **world}


def read_file(exported, coleccion, clave):
    path = exported["out_dir"] / "files" / coleccion / f"{clave}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def atlas_index(exported) -> dict:
    return {(c, k): i for i, (c, k) in enumerate(
        (e["c"], e["k"]) for e in json.loads(
            (exported["out_dir"] / "instruments.json").read_text(encoding="utf-8"))["instruments"])}


def js_slice(markdown: str, start: int, end: int) -> str:
    """What `markdown.slice(start, end)` returns in JavaScript: the string is a
    sequence of UTF-16 code units."""
    return markdown.encode("utf-16-le")[2 * start:2 * end].decode("utf-16-le")


# -- the files ------------------------------------------------------------------ #

def test_one_file_per_instrument_in_the_atlas_order(exported):
    index = json.loads((exported["out_dir"] / "instruments.json").read_text(encoding="utf-8"))
    pairs = [(e["c"], e["k"]) for e in index["instruments"]]
    assert sorted(pairs) == sorted(DOCUMENTS)
    instruments = pq.read_table(exported["work_dir"] / "instruments.parquet").to_pandas()
    assert pairs == list(zip(instruments["coleccion"], instruments["clave"].astype(str)))
    files = sorted(p.relative_to(exported["out_dir"] / "files").as_posix()
                   for p in (exported["out_dir"] / "files").rglob("*.json"))
    assert files == sorted(f"{c}/{k}.json" for c, k in DOCUMENTS)


def test_the_file_shape_and_every_key(exported):
    document = read_file(exported, "leyes", "alfa")
    assert list(document) == ["meta", "markdown", "tree", "units"]
    meta = document["meta"]
    assert meta["i"] == atlas_index(exported)[("leyes", "alfa")]
    assert (meta["c"], meta["k"], meta["n"]) == ("leyes", "alfa", "LEY ALFA")
    assert meta["archivo"] == "01-02-2020.md" and meta["fecha_publicacion"] == "01-02-2020"
    assert meta["codNota"] == 1234
    assert read_file(exported, "leyes", "beta")["meta"]["codNota"] is None
    assert meta["md2akn_version"] == md2akn.__version__
    assert (meta["model"], meta["model_slug"], meta["top"]) == (MODEL, SLUG, 5)
    assert meta["generated"] == "2026-10-13T12:00:00+00:00" and meta["commit"]
    assert meta["numbering"] == {"mode": "articulo", "roman_articles": 0}
    assert meta["units"] == len(document["units"]) == 16
    assert meta["searched"] == sum(1 for u in document["units"] if u.get("x")) == 10
    assert document["markdown"] == ALFA
    assert read_file(exported, "lineamientos", "900")["meta"]["numbering"]["mode"] == "ordinal"
    treaty = read_file(exported, "tratados", "700")["meta"]["numbering"]
    assert treaty == {"mode": "articulo", "roman_articles": 2}

    root = document["tree"]
    assert root["t"] == "act" and root["e"] == "act"
    body = next(c for c in root["c"] if c["t"] == "body")
    section = next(c for c in body["c"] if c.get("r") == "#transitorios")
    assert section["t"] == "section" and section["n"] == "transitorios"
    title = next(c for c in body["c"] if c["t"] == "title")
    chapter = title["c"][0]
    article = next(c for c in chapter["c"] if c["e"].endswith("art_2"))
    assert [c["t"] for c in article["c"]] == ["content", "paragraph", "paragraph"]
    assert article["c"][1]["e"].endswith("art_2__para_I")
    # null and empty keys are omitted
    assert "h" not in article and "a" not in article and "x" not in article
    assert all(value not in (None, "", []) for value in article.values())
    unit = document["units"][1]
    assert set(unit) <= {"u", "e", "p", "pe", "s", "l", "sha", "v", "x", "f", "near"}
    assert {"u", "e", "s", "l", "sha", "v"} <= set(unit)


def test_a_tree_node_carries_chapeau_tail_and_annotations():
    markdown = ("**Artículo 1.-** Son obligaciones:\n\nI. Una cosa;\n\nII. Otra cosa.\n\n"
                "Y después de ellas esto otro.\n\n(REFORMADO, D.O.F. 10 DE JUNIO DE 2011)\n")
    tree = parse_markdown(markdown)
    document = exporter.node_json(tree, exporter.Utf16(markdown))
    article = next(node for node in _walk(document) if node["t"] == "article")
    marks = [child.get("x") for child in article["c"]]
    assert ["chapeau"] in marks and ["tail"] in marks
    notes = [a for node in _walk(document) for a in node.get("a", [])]
    assert len(notes) == 1 and notes[0]["raw"].startswith("(REFORMADO")
    # `None` fields are omitted, the rest are plain JSON values.
    assert notes[0] == {"raw": "(REFORMADO, D.O.F. 10 DE JUNIO DE 2011)",
                        "action": "REFORMADO", "date": "2011-06-10"}
    json.dumps(document)


def _walk(node):
    yield node
    for child in node.get("c", []):
        yield from _walk(child)


# -- offsets -------------------------------------------------------------------- #

def test_offsets_slice_the_markdown_as_a_javascript_string_would(exported):
    markdown = ALFA
    tree = parse_markdown(markdown)
    document = read_file(exported, "leyes", "alfa")
    pairs = list(zip(tree.walk(), _walk(document["tree"])))
    assert len(pairs) == sum(1 for _ in tree.walk())
    for node, entry in pairs:
        assert entry["e"] == node.eId
        assert js_slice(document["markdown"], entry["s"], entry["l"]) == node.text
    parsed = text_units(tree, split_articles=False)
    for unit, entry in zip(parsed, document["units"]):
        assert entry["e"] == unit.eId
        assert js_slice(document["markdown"], entry["s"], entry["l"]) \
            == markdown[unit.start_char:unit.end_char]


def test_an_astral_character_before_an_article_shifts_the_offsets(exported):
    """The preamble holds `😀` and article 2 holds `𝔘`: every later code-point
    offset is short by one, then two, UTF-16 code units."""
    document = read_file(exported, "leyes", "alfa")
    tree = parse_markdown(ALFA)
    parsed = text_units(tree, split_articles=False)
    by_eid = {e["e"]: e for e in document["units"]}
    article_1 = next(u for u in parsed if u.eId.endswith("art_1"))
    article_2b = next(u for u in parsed if u.eId.endswith("art_2_2"))
    assert by_eid[article_1.eId]["s"] == article_1.start_char + 1
    assert by_eid[article_2b.eId]["s"] == article_2b.start_char + 2
    assert exporter.Utf16(ALFA)(len(ALFA)) == len(ALFA) + 2
    assert js_slice(ALFA, by_eid[article_1.eId]["s"], by_eid[article_1.eId]["l"]) \
        .startswith("**Artículo 1.-**")


def test_utf16_offsets_of_text_without_astral_characters_are_unchanged():
    convert = exporter.Utf16("áé ñ — texto")
    assert [convert(i) for i in range(0, 13)] == list(range(13))


# -- the reproducibility check ------------------------------------------------- #

def doctor_units(world, coleccion, change, tmp_name):
    """A copy of the cache whose released `units.parquet` has one row changed."""
    import shutil

    cache = world["tmp"] / tmp_name
    shutil.copytree(world["cache"], cache)
    tag = {"leyes": "scjn-leyes-vectors", "lineamientos": "scjn-lineamientos-vectors",
           "tratados": "scjn-tratados-vectors"}[coleccion]
    path = cache / tag / "units.parquet"
    frame = pq.read_table(path).to_pandas()
    change(frame)
    pq.write_table(pa.Table.from_pandas(frame, schema=build_units.UNITS_SCHEMA,
                                        preserve_index=False), path)
    return cache


def test_a_doctored_released_row_stops_the_export_naming_the_instrument_and_position(world):
    def change(frame):
        row = frame.index[(frame["clave"] == "alfa") & (frame["eId"].str.endswith("art_5"))][0]
        frame.loc[row, "start_char"] = frame.loc[row, "start_char"] + 1

    cache = doctor_units(world, "leyes", change, "doctored")
    with pytest.raises(SystemExit) as error:
        exporter.export(world["work_dir"], out_dir=world["tmp"] / "bad", cache_dir=cache,
                        snapshot_reader=reader_for, now=NOW, log=quiet)
    message = str(error.value)
    assert "leyes/alfa" in message and "excerpt 8 differs" in message
    assert not (world["tmp"] / "bad" / ".done").exists()


def test_a_different_snapshot_is_refused(world):
    def other(coleccion):
        return lambda clave, archivo: DOCUMENTS[(coleccion, clave)][3] + "\nOtro párrafo.\n"

    with pytest.raises(SystemExit, match="excerpts|differs"):
        exporter.export(world["work_dir"], out_dir=world["tmp"] / "bad2",
                        cache_dir=world["cache"], snapshot_reader=other, now=NOW, log=quiet)


# -- the nearest texts ----------------------------------------------------------- #

def test_near_is_present_exactly_on_searched_units(exported):
    for coleccion, clave in DOCUMENTS:
        document = read_file(exported, coleccion, clave)
        for unit in document["units"]:
            transitory = unit["e"].startswith("sec_transitorios")
            searched = unit["u"] != "heading" and not transitory
            assert bool(unit.get("x")) == searched
            assert ("near" in unit) == searched


def test_near_entries_are_ranked_and_name_an_owner_outside_the_instrument(exported):
    index = atlas_index(exported)
    nearest = pq.read_table(
        instrument_matrix.output_dir(exported["work_dir"]) / "nearest.parquet").to_pandas()
    for (coleccion, clave), i in index.items():
        document = read_file(exported, coleccion, clave)
        searched = [u for u in document["units"] if u.get("x")]
        rows = nearest[nearest["i"] == i]
        assert len(rows) == len(searched)
        for unit, (_, recorded) in zip(searched, rows.iterrows()):
            near = unit["near"]
            assert 1 <= len(near) <= 5
            similarities = [entry[3] for entry in near]
            assert similarities == sorted(similarities, reverse=True)
            assert abs(similarities[0] - float(recorded["similarity"])) < 1e-4
            for j, eid, piece, similarity, also in near:
                assert j != i
                assert isinstance(eid, str) and isinstance(piece, int)
                assert all(other != i for other, _, _ in also)
                assert [o[0] for o in also] == sorted(o[0] for o in also)
                assert all(o[0] > j for o in also)


def test_a_text_shared_by_two_instruments_is_one_entry_naming_its_owner(exported):
    """`alfa` and `beta` carry the same article 1: for `alfa` it is the nearest
    text, at similarity 1, owned by `beta`."""
    index = atlas_index(exported)
    document = read_file(exported, "leyes", "alfa")
    article = next(u for u in document["units"] if u["e"].endswith("art_1"))
    j, eid, piece, similarity, also = article["near"][0]
    assert j == index[("leyes", "beta")] and eid == "art_1" and piece == 0
    assert similarity == 1.0 and also == []
    other = read_file(exported, "leyes", "beta")
    mirror = next(u for u in other["units"] if u["e"] == "art_1")["near"][0]
    assert mirror[0] == index[("leyes", "alfa")]
    assert mirror[1].endswith("art_1")


def test_the_first_owner_is_the_lowest_atlas_index_and_the_rest_follow():
    owners = {7: {5: 12, 3: 40, 9: 2}}
    ranked = exporter.near_entries(1, [(7, 0.91234, [3, 5, 9])], owners, 5)
    assert ranked == [([(3, 40), (5, 12), (9, 2)], 0.9123)]
    with pytest.raises(SystemExit, match="owners"):
        exporter.near_entries(1, [(7, 0.5, [3, 5])], owners, 5)
    assert exporter.near_entries(3, [(7, 0.5, [5, 9])], owners, 5) == [([(5, 12), (9, 2)], 0.5)]


# -- the flags --------------------------------------------------------------------- #

def test_every_flag_is_named_and_explained():
    assert exporter.FLAGS == ("loose", "duplicate_number", "numbering_gap", "over_window",
                              "no_articles", "uncovered", "no_transitorios")
    assert set(exporter.FLAG_MEANINGS) == set(exporter.FLAGS)
    assert all(text.endswith((".", ")")) for text in exporter.FLAG_MEANINGS.values())


def test_the_excerpt_flags_of_alfa(exported):
    document = read_file(exported, "leyes", "alfa")
    flags = {u["e"]: u.get("f", []) for u in document["units"]}
    # A container's own opening text is an `intro` since md2akn 0.5.1 (rule 12,
    # issue #291), not a `loose` excerpt; `loose` is what is left under the
    # transitorios.
    assert flags["tit_PRIMERO__cap_I__intro"] == []
    assert flags["sec_transitorios__p_content"] == ["loose"]
    assert flags["tit_PRIMERO__cap_I__art_2"] == ["duplicate_number"]
    # A repeated number is also "not the previous number plus one".
    assert flags["tit_PRIMERO__cap_I__art_2_2"] == ["duplicate_number", "numbering_gap"]
    assert flags["tit_PRIMERO__cap_I__art_5"] == ["numbering_gap", "over_window"]
    assert flags["tit_PRIMERO__cap_I__art_1"] == []
    assert flags["sec_transitorios__art_PRIMERO"] == []
    counts = document["meta"]["flags"]
    assert counts == {"loose": 1, "duplicate_number": 2, "numbering_gap": 2,
                      "over_window": 1, "score": 6}


def test_a_block_is_the_root_of_its_hierarchy_in_the_exported_tree(exported):
    """Review fix-5 of #291: the toy treaty's resolution block prints its annex, chapters and
    parts. The file's tree holds them in that order, inside the block, with no repeated
    identifier -- the annex is not an empty node with its chapters as siblings, and a chapter's
    `PARTE A` is its own."""
    document = read_file(exported, "tratados", "700")
    body = next(n for n in document["tree"]["c"] if n["t"] == "body")
    resolution = next(n for n in body["c"] if n["e"] == "sec_RESOLUCIÓN_MSC.1(1)")
    assert [(c["t"], c["e"]) for c in resolution["c"]] == [
        ("intro", "sec_RESOLUCIÓN_MSC.1(1)__intro"),
        ("attachment", "sec_RESOLUCIÓN_MSC.1(1)__att_attachment"),
    ]
    annex = resolution["c"][1]
    assert [(c["t"], c["n"]) for c in annex["c"]] == [("chapter", "II-1"), ("chapter", "II-2")]
    assert [[(p["t"], p["n"]) for p in c["c"] if p["t"] == "part"] for c in annex["c"]] == [
        [("part", "A"), ("part", "B")], [("part", "A")]]
    assert document["meta"]["flags"].get("duplicate_number", 0) == 0


def test_the_instrument_flags(exported):
    gamma = read_file(exported, "leyes", "gamma")["meta"]["flags"]
    assert gamma == {"no_articles": 1, "no_transitorios": 1, "score": 1}
    beta = read_file(exported, "leyes", "beta")["meta"]["flags"]
    assert beta == {"no_transitorios": 1, "score": 0}
    assert read_file(exported, "tratados", "700")["meta"]["flags"]["no_transitorios"] == 1
    # numbering gaps look only at integer numbers: roman and ordinal ones never gap.
    assert "numbering_gap" not in read_file(exported, "tratados", "700")["meta"]["flags"]
    assert "numbering_gap" not in read_file(exported, "lineamientos", "900")["meta"]["flags"]


def test_an_uncovered_document_is_flagged(world, monkeypatch):
    """`coverage()` holds on every real document, so `uncovered` is exercised by
    a stub that reports characters nothing accounts for."""
    real = exporter.coverage

    def uncovered(tree, units):
        report = real(tree, units)
        if tree.span.doc.text == BETA:
            return type(report)(**{**report.__dict__, "uncovered_chars": 3})
        return report

    monkeypatch.setattr(exporter, "coverage", uncovered)
    out_dir, manifest = run_export(world, "uncovered")
    document = json.loads((out_dir / "files" / "leyes" / "beta.json").read_text(encoding="utf-8"))
    assert document["meta"]["flags"] == {"uncovered": 3, "no_transitorios": 1, "score": 1}
    index = json.loads((out_dir / "instruments.json").read_text(encoding="utf-8"))
    beta = next(e for e in index["instruments"] if e["k"] == "beta")
    assert beta["f"]["uncovered"] == 3 and beta["s"] == 1
    assert manifest["flags"]["uncovered"] == {"instruments": 1, "count": 3}


def test_an_introduction_and_a_table_are_nodes_with_their_own_excerpts(exported):
    """md2akn 0.5.1's rules 12 and 13 (issues #291, #294): a container's opening text is an
    `intro` node, a run of `| ... |` rows a `table` node, and a long table is cut into
    pieces, one excerpt each, whose offsets still index the raw document."""
    document = read_file(exported, "leyes", "alfa")
    markdown = document["markdown"]

    def nodes(node):
        yield node
        for child in node.get("c", []):
            yield from nodes(child)

    by_eid = {n["e"]: n for n in nodes(document["tree"])}
    intro = by_eid["tit_PRIMERO__cap_I__intro"]
    assert intro["t"] == "intro" and [c["t"] for c in intro["c"]] == ["content", "table"]
    table = by_eid["tit_PRIMERO__cap_I__intro__table_1"]
    assert table["t"] == "table" and table["n"] == "1"
    # Offsets are UTF-16 code units (an astral character precedes the table).
    text = js_slice(markdown, table["s"], table["l"])
    assert text.startswith("| Concepto | Cuota |")
    assert text.rstrip().endswith("| Dos \\| con barra | **2.5** |")

    units = {(u["e"], u.get("p", 0)): u for u in document["units"]}
    assert units[("tit_PRIMERO__cap_I__intro", 0)]["u"] == "intro"
    assert units[("tit_PRIMERO__cap_I__intro__table_1", 0)]["u"] == "table"
    # The small table is one piece; the long one is cut at row boundaries.
    big = [u for u in document["units"] if u["e"] == "tit_PRIMERO__cap_II__intro__table_1"]
    assert [u.get("p", 0) for u in big] == [1, 2] and {u["u"] for u in big} == {"table"}
    first, second = big
    assert first["l"] == second["s"]
    assert js_slice(markdown, first["s"], first["l"]).startswith("| fila 01 |")
    assert js_slice(markdown, second["s"], second["l"]).startswith("| fila ")
    # An introduction and a table are searched, so they carry nearest texts; the
    # flags that existed for loose text do not apply to them.
    for unit in (units[("tit_PRIMERO__cap_I__intro", 0)], first, second):
        assert unit["x"] is True and "near" in unit and "f" not in unit


def test_the_numbering_helpers():
    assert [exporter.integer_number(n) for n in ("27", "1º", "1o.", "4 Bis", "I", "Primero", "2,3",
                                                  None)] == [27, 1, 1, None, None, None, None, None]
    assert exporter.is_roman("XV BIS") and exporter.is_roman("II") and not exporter.is_roman("1")
    assert not exporter.is_roman("Primero") and not exporter.is_roman(None)


# -- the index --------------------------------------------------------------------------- #

def test_the_index_and_its_totals(exported):
    index = json.loads((exported["out_dir"] / "instruments.json").read_text(encoding="utf-8"))
    meta = index["meta"]
    assert meta["instruments"] == 5 and meta["top"] == 5
    assert meta["collections"] == {"leyes": 3, "lineamientos": 1, "tratados": 1}
    assert meta["release"] == "atlas-instruments" and meta["files"] == "files/"
    assert meta["flag_meanings"] == exporter.FLAG_MEANINGS
    assert meta["model_slug"] == SLUG and meta["md2akn_version"] == md2akn.__version__
    alfa = next(e for e in index["instruments"] if e["k"] == "alfa")
    assert alfa == {"c": "leyes", "k": "alfa", "n": "LEY ALFA", "archivo": "01-02-2020.md",
                    "u": 16, "x": 10, "a": 5,
                    "f": {"loose": 1, "duplicate_number": 2, "numbering_gap": 2,
                          "over_window": 1}, "s": 6}
    assert meta["units"] == sum(e["u"] for e in index["instruments"])
    assert meta["searched"] == sum(e["x"] for e in index["instruments"])
    assert meta["flags"]["loose"] == {"instruments": 1, "count": 1}
    assert meta["flags"]["duplicate_number"] == {"instruments": 1, "count": 2}
    assert meta["flags"]["no_articles"] == {"instruments": 1, "count": 1}
    assert meta["flags"]["no_transitorios"]["instruments"] == 4
    assert exported["manifest"]["flags"] == meta["flags"]


def test_the_index_is_written_where_asked_and_agrees_with_the_atlas(world, tmp_path):
    index = tmp_path / "site" / "instruments.json"
    index.parent.mkdir()
    atlas = tmp_path / "atlas.json"
    instruments = pq.read_table(world["work_dir"] / "instruments.parquet").to_pandas()
    atlas.write_text(json.dumps({"instruments": [
        {"c": r["coleccion"], "k": str(r["clave"]), "n": r["nombre"], "p": int(r["units"])}
        for _, r in instruments.iterrows()]}))
    out_dir, _ = run_export(world, "with-index", index_path=index, instruments_as=atlas)
    assert index.read_bytes() == (out_dir / "instruments.json").read_bytes()

    bent = json.loads(atlas.read_text())
    bent["instruments"][2]["k"] = "other"
    atlas.write_text(json.dumps(bent))
    with pytest.raises(SystemExit, match="instrument 2 differs.*`k`"):
        run_export(world, "with-bad-atlas", instruments_as=atlas)
    short = {"instruments": bent["instruments"][:3]}
    short["instruments"][2]["k"] = str(instruments.iloc[2]["clave"])
    atlas.write_text(json.dumps(short))
    with pytest.raises(SystemExit, match="5 instruments here, 3"):
        run_export(world, "with-short-atlas", instruments_as=atlas)


# -- packaging ------------------------------------------------------------------------------ #

def test_asset_names_carry_the_model_slug():
    assert exporter.asset_names(MODEL) == {
        "model_slug": "qwen3-4b", "asset": "atlas-instruments-qwen3-4b.tar.gz",
        "manifest": "manifest-qwen3-4b.json", "sums": "SHA256SUMS-qwen3-4b.txt"}
    assert exporter.asset_names("Qwen/Qwen3-Embedding-0.6B")["asset"] \
        == "atlas-instruments-qwen3-0.6b.tar.gz"


def test_manifest_sums_and_tarball(exported):
    out_dir, manifest = exported["out_dir"], exported["manifest"]
    assert manifest["files"] == 5 and manifest["release"] == "atlas-instruments"
    assert manifest["asset"] == "atlas-instruments-qwen3-4b.tar.gz"
    total = sum(p.stat().st_size for p in (out_dir / "files").rglob("*.json"))
    assert manifest["bytes"] == total
    assert manifest["largest"]["bytes"] == max(
        p.stat().st_size for p in (out_dir / "files").rglob("*.json"))
    assert manifest["largest"]["file"].startswith("files/")
    assert manifest["nearest_texts"]["agrees_with_nearest"] is True
    assert json.loads((out_dir / manifest["manifest"]).read_text(encoding="utf-8")) == manifest

    sums = (out_dir / manifest["sums"]).read_text(encoding="utf-8").splitlines()
    assert len(sums) == 2
    for line in sums:
        digest, name = line.split("  ")
        assert hashlib.sha256((out_dir / name).read_bytes()).hexdigest() == digest

    with tarfile.open(out_dir / manifest["asset"]) as archive:
        names = archive.getnames()
        members = {m.name: m for m in archive.getmembers()}
        assert names[0] == "manifest.json"
        assert names[1] == "files"
        assert sorted(names[2:]) == names[2:]
        assert {"files/leyes/alfa.json", "files/tratados/700.json"} <= set(names)
        assert all(m.mtime == 0 and m.uid == 0 and m.gid == 0 for m in members.values())
        assert json.loads(archive.extractfile("manifest.json").read()) == manifest


def test_the_tarball_is_byte_reproducible(world, exported):
    out_dir, _ = run_export(world, "again")
    assert (out_dir / "atlas-instruments-qwen3-4b.tar.gz").read_bytes() \
        == (exported["out_dir"] / "atlas-instruments-qwen3-4b.tar.gz").read_bytes()


def test_publish_instructions_name_the_release_and_the_three_assets(exported):
    text = (exported["out_dir"] / "PUBLICAR.md").read_text(encoding="utf-8")
    assert "gh release create atlas-instruments" in text
    assert "gh release upload atlas-instruments" in text and "--clobber" in text
    for asset in ("atlas-instruments-qwen3-4b.tar.gz", "manifest-qwen3-4b.json",
                  "SHA256SUMS-qwen3-4b.txt"):
        assert asset in text
    assert "before the pull request" in text
    assert ".github/atlas-instruments.md" in text
    assert "sha256sum -c SHA256SUMS-qwen3-4b.txt" in text


def test_install_copies_the_files_only(exported, tmp_path):
    target = tmp_path / "site" / "files"
    target.mkdir(parents=True)
    (target / "stale.json").write_text("{}")
    count = exporter.install(exported["out_dir"], target, log=quiet)
    assert count == 5
    assert not (target / "stale.json").exists()
    assert sorted(p.name for p in target.iterdir()) == ["leyes", "lineamientos", "tratados"]
    assert not list(target.rglob("manifest*"))
    unfinished = tmp_path / "unfinished"
    unfinished.mkdir()
    with pytest.raises(SystemExit, match="nothing finished"):
        exporter.install(unfinished, tmp_path / "x", log=quiet)


def test_done_makes_a_rerun_a_no_op_and_force_recomputes(world, monkeypatch, capsys):
    out_dir, _ = run_export(world, "rerun")
    assert (out_dir / ".done").exists()
    stamp = (out_dir / "instruments.json").stat().st_mtime_ns

    def explode(*args, **kwargs):
        raise AssertionError("must not export")

    monkeypatch.setattr(exporter, "export", explode)
    assert exporter.main(["--work-dir", str(world["work_dir"]), "--out-dir", str(out_dir)]) == 0
    assert "nothing to export" in capsys.readouterr().out
    assert (out_dir / "instruments.json").stat().st_mtime_ns == stamp

    seen = {}
    monkeypatch.setattr(exporter, "export", lambda *args, **kwargs: seen.update(kwargs))
    assert exporter.main(["--work-dir", str(world["work_dir"]), "--out-dir", str(out_dir),
                          "--force", "--top", "3"]) == 0
    assert seen["top"] == 3


def test_force_replaces_files_it_no_longer_has(world):
    out_dir, _ = run_export(world, "stale")
    (out_dir / "files" / "leyes" / "ghost.json").write_text("{}")
    run_export(world, "stale")
    assert not (out_dir / "files" / "leyes" / "ghost.json").exists()


def test_it_refuses_a_work_directory_without_the_nearest_texts(world, tmp_path):
    import shutil

    work_dir = tmp_path / "work"
    shutil.copytree(world["work_dir"], work_dir)
    (work_dir / "nearest-texts" / ".done").unlink()
    with pytest.raises(SystemExit, match="nearest_texts.py"):
        exporter.export(work_dir, out_dir=tmp_path / "out", cache_dir=world["cache"],
                        snapshot_reader=reader_for, log=quiet)


def test_it_refuses_a_work_directory_prepared_without_unique_names(world, tmp_path):
    import shutil

    work_dir = tmp_path / "work"
    shutil.copytree(world["work_dir"], work_dir)
    record = json.loads((work_dir / "input.json").read_text(encoding="utf-8"))
    record["unique_names"] = False
    (work_dir / "input.json").write_text(json.dumps(record))
    with pytest.raises(SystemExit, match="unique-names"):
        exporter.export(work_dir, out_dir=tmp_path / "out", cache_dir=world["cache"],
                        snapshot_reader=reader_for, log=quiet)


# -- the repository wiring (static) ---------------------------------------------------- #

def workflow_text() -> str:
    return (REPO / ".github" / "workflows" / "website.yml").read_text(encoding="utf-8")


def test_the_workflow_fetches_and_verifies_the_instrument_files_before_publishing():
    text = workflow_text()
    step = text.index("name: Fetch the instrument files")
    publish = text.index("quarto-dev/quarto-actions/publish")
    assert step < publish
    assert text.index("atlas-pairs-bm25") < step
    block = text[step:publish]
    assert "gh release download atlas-instruments" in block
    assert "atlas-instruments-qwen3-4b.tar.gz" in block
    assert "SHA256SUMS-qwen3-4b.txt" in block
    assert "sha256sum -c" in block and 'test "$count" -gt 0' in block
    assert "website/pages/instruments/files" in block


def test_the_files_are_gitignored_and_not_tracked():
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/website/pages/instruments/files/" in ignored
    try:
        tracked = subprocess.run(["git", "-C", str(REPO), "ls-files", "website/pages/instruments/files"],
                                 capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    assert tracked.strip() == ""


def test_the_release_body_exists_and_names_the_assets():
    body = (REPO / ".github" / "atlas-instruments.md").read_text(encoding="utf-8")
    for asset in ("atlas-instruments-qwen3-4b.tar.gz", "manifest-qwen3-4b.json",
                  "SHA256SUMS-qwen3-4b.txt"):
        assert asset in body


def test_the_committed_index_agrees_with_the_atlas():
    path = REPO / "website" / "pages" / "instruments" / "instruments.json"
    if not path.exists():
        pytest.skip("website/pages/instruments/instruments.json is not committed yet")
    index = json.loads(path.read_text(encoding="utf-8"))
    atlas = json.loads((REPO / "website" / "pages" / "atlas" / "atlas-qwen3-4b.json")
                       .read_text(encoding="utf-8"))["instruments"]
    assert len(index["instruments"]) == len(atlas) == index["meta"]["instruments"]
    for position, (mine, theirs) in enumerate(zip(index["instruments"], atlas)):
        assert (mine["c"], mine["k"], mine["n"]) == (theirs["c"], theirs["k"], theirs["n"]), position
        assert mine["u"] == theirs["p"], position
    assert index["meta"]["model_slug"] == "qwen3-4b"
    assert set(index["meta"]["flag_meanings"]) == set(exporter.FLAGS)
