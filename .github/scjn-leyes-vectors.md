Un vector por cada texto distinto del corpus de **leyes federales**: el texto
vigente de las 315 leyes que publica
[`scjn-leyes`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-leyes),
segmentado en unidades de recuperación por
[`md2akn`](https://pypi.org/project/md2akn/) y embebido con los dos modelos
Qwen3-Embedding (issue #227; es la Fase 3 de #218, ampliada a las tres
colecciones).

Se lee con [`legalvec`](https://pypi.org/project/legalvec/), que es lo único
que necesita entender la estructura de abajo:

```python
import legalvec

legalvec.download_vectors_assets("leyes", ["lft"])
conjunto = legalvec.load_vectors("leyes", "lft", "Qwen/Qwen3-Embedding-0.6B")
conjunto.vectors.shape          # (n, 1024), float16
unidades = legalvec.load_units("leyes")   # text_sha1 -> el texto, el artículo, la ley
```

## Qué contiene

| Asset | Contenido |
|---|---|
| `units.parquet` | Cada unidad de texto que se embebió: `clave`, `unit_type`, `eId`, `num`, `path`, el texto normalizado y su `text_sha1` |
| `leaves.parquet` | Cada hoja del árbol (`eId`) y la unidad que carga su texto |
| `corpus-manifest.json` | Cómo se construyeron las unidades: `cap`, plantilla, versión de `md2akn`, conteos |
| `vectors-manifest.json` | Cómo se construyeron los vectores: modelo, `K`, dtype, pooling |
| `SHA256SUMS.txt` | Suma de cada asset |
| `vectors-<abrev>-<modelo>-<K>.parquet` | Los vectores propios de una ley — 315, por modelo |
| `vectors-shared-<modelo>-<K>.parquet` | Los vectores del texto que varias leyes comparten — uno por modelo |

**Los dos archivos de vectores se leen juntos, siempre.** Un texto que varias
leyes comparten (el mismo transitorio repetido por doce decretos) se embebe y
se publica una sola vez, en el archivo compartido; el archivo propio de una
ley trae sólo lo que es suyo. `legalvec.load_vectors` une los dos y
deduplica por `text_sha1`; ninguno de los dos sirve por separado.

Un archivo de vectores **no trae texto**: sólo `text_sha1` y una lista de
`float16` de tamaño fijo. `units.parquet` es lo que devuelve el texto a
partir del hash — un archivo, no una copia del texto por instrumento.

## Los números

| | 0.6B | 4B |
|---|---|---|
| Modelo | `Qwen/Qwen3-Embedding-0.6B` | `Qwen/Qwen3-Embedding-4B` |
| `K` | 1 024 | 2 560 |
| Vectores | 59 178 | 59 178 |
| De los cuales compartidos | 2 378 | 2 378 |
| Tamaño | 88 MB | 214 MB |
| Textos truncados | 1 | 1 |

Corpus: 315 leyes, 68 581 unidades, **59 178 textos distintos**, `cap`
2 000 caracteres (rige para todo menos el artículo — ver abajo), plantilla
`bare`, `md2akn` 0.4.0. El único texto truncado en ambos modelos es el mismo:
el artículo 513 de la `lft`, la tabla de enfermedades de trabajo, 68 689
tokens. Ambos modelos se publican
(#227, decisión 9): la evaluación por proxies de #217 no se ha corrido, así
que nada se ha ganado el derecho de descartar al 4B. Y van en archivos
separados (decisión 10): quien quiera el 0.6B no tiene por qué bajarse los
2 560 flotantes del 4B para tirarlos.

## Cómo se construyeron

`float16`, pooling de último token, `padding_side="left"`, sin
normalización, sin cuantización. Los detalles exactos están en
`vectors-manifest.json`.

**Desde issue #256, un artículo es siempre una unidad entera, sin importar
su longitud** (`split_articles=False` en `md2akn.text_units()`): ya no se
parte en piezas por el `cap` de 2 000 caracteres — ese tope sigue rigiendo
para el epígrafe de un capítulo, el preámbulo, los transitorios y el
contenido suelto, nunca para un artículo. Un artículo más largo que la
ventana del modelo (32 768 tokens para el 0.6B, 40 960 para el 4B) se
**trunca** a esa ventana en vez de partirse — nunca se ventanea ni se
promedia — y cada texto truncado queda contado en `vectors-manifest.json`
(`texts_truncated`, `max_tokens_embedded`). `md2akn.coverage()` sigue
verificando que ningún carácter del documento se quede fuera.

## Advertencias

- **Esto es dato derivado, no texto legal oficial.** La fuente del texto es
  la SCJN (`fuente: scjn` en cada snapshot) y la fuente oficial sigue siendo
  el DOF. Un vector no es citable.
- **Texto vigente solamente**: una ley aparece con su snapshot más reciente,
  no con su historia. La historia está en `scjn-leyes`.
- **Nada de esto se publica solo.** Ningún GitHub Action crea ni sube estos
  assets (issue #115, Hallazgo C): los construye `scripts/embeddings/` y los
  publica una persona.
