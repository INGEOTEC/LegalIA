Un vector por cada texto distinto del corpus de **tratados internacionales**: el
texto vigente de los 1 373 tratados con texto que publica
[`scjn-tratados`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-tratados),
segmentado en unidades de recuperación por
[`md2akn`](https://pypi.org/project/md2akn/) y embebido con los dos modelos
Qwen3-Embedding (issue #280).

Se lee con [`legalvec`](https://pypi.org/project/legalvec/):

```python
import legalvec

legalvec.download_vectors_assets("tratados", ["11097"])
conjunto = legalvec.load_vectors("tratados", "11097", "Qwen/Qwen3-Embedding-0.6B")
unidades = legalvec.load_units("tratados")
```

**La clave es el `id_ordenamiento`, nunca un slug.** Un tratado no tiene
`abrev` ni vínculo con el DOF (issue #277), y `id_ordenamiento` es la única
clave que la SCJN garantiza estable.

**Los artículos romanos son artículos.** Un tratado numera sus artículos
`ARTÍCULO I`, `ARTÍCULO II`, …; `md2akn` 0.5.0 (regla 10, issue #279) los lee
como unidades de artículo, y por eso estos vectores no se pueden construir con
una versión anterior de `md2akn`.

## Publicado en tres partes

Un release de GitHub admite a lo más 1 000 assets, y aquí hay 2 753. Así que
esta colección se publica como una serie numerada, igual que el corpus del
que sale (issue #223):

| Release | Assets |
|---|---|
| `scjn-tratados-vectors` | 995 vectores + `units.parquet`, `leaves.parquet`, `corpus-manifest.json`, `vectors-manifest.json`, `SHA256SUMS.txt` |
| [`scjn-tratados-vectors-2`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-tratados-vectors-2) | 1 000 vectores |
| [`scjn-tratados-vectors-3`](https://github.com/INGEOTEC/LegalIA/releases/tag/scjn-tratados-vectors-3) | 753 vectores |

`legalvec` resuelve la serie completa preguntándole a GitHub — prueba
`-2`, `-3`, … hasta que una da 404 — así que un lector nunca necesita saber
cuántas partes hay, y nada publicado registra ese número.

## Qué contiene

| Asset | Contenido |
|---|---|
| `units.parquet` | Cada unidad de texto que se embebió: `clave`, `unit_type`, `eId`, `num`, `path`, el texto normalizado, su `text_sha1` y `envelope` (issue #301) |
| `leaves.parquet` | Cada hoja del árbol (`eId`) y la unidad que carga su texto |
| `corpus-manifest.json` | Cómo se construyeron las unidades |
| `vectors-manifest.json` | Cómo se construyeron los vectores |
| `SHA256SUMS.txt` | Suma de cada asset de la serie completa |
| `vectors-<id_ordenamiento>-<modelo>-<K>.parquet` | Los vectores propios de un tratado — 1 373, por modelo |
| `vectors-shared-<modelo>-<K>.parquet` | Los vectores del texto que varios comparten — uno por modelo |

Los dos archivos de vectores se leen juntos, siempre: un texto que varios
tratados comparten se embebe y se publica una sola vez, en el compartido.
`legalvec.load_vectors` une los dos y deduplica por `text_sha1`.

Un artículo es siempre una unidad entera, sin importar su longitud
(`split_articles=False`, issue #256): el `cap` de 2 000 caracteres sólo rige
para el epígrafe de un capítulo, el preámbulo, el contenido suelto y las
firmas. Un artículo más largo que la ventana del modelo se **trunca** a esa
ventana (nunca se parte ni se promedia); cada texto truncado queda contado en
`vectors-manifest.json` (`texts_truncated`, `max_tokens_embedded`): 11 textos en esta colección, todos
más largos que la ventana de los dos modelos (de 41 360 a 115 193 tokens).

## Los números

| | 0.6B | 4B |
|---|---|---|
| `K` | 1 024 | 2 560 |
| Vectores | 58 189 | 58 189 |
| De los cuales compartidos | 1 274 | 1 274 |
| Tamaño | 94 MB | 225 MB |

Corpus: 1 373 tratados, 60 599 unidades, **58 189 textos distintos**,
`cap` 2 000, plantilla `bare`, `md2akn` 0.5.1 (issue #291: los artículos
decimales, las introducciones de contenedor y las tablas, y la revisión de los números de artículo repetidos:
reglas 14 a 18, y la lectura de la estructura que declara la API de la SCJN (revisión fix-2 de #291: marcadores de esquema en el Markdown, reglas 19 a 24, y revisión fix-4 de #291: los bloques `other` de la API, las divisiones donde la numeración reinicia y la numeración del Reglamento de Radiocomunicaciones, reglas 29 a 32, revisión fix-5 de #291: un bloque de la API es la raíz de su jerarquía, regla 33, y revisión fix-6 de #291: cada bloque de la API acota su texto, un encabezado sin contenido es un encabezado, las leyendas y notas de una tabla son de la tabla, un artículo-encabezado abre una sección y `N (SIC)` es un artículo, reglas 34 a 39)). Issue #300 añadió los bloques que la API de la SCJN omitía y la regla 40. Issue #301 añade a `units.parquet` la columna `envelope`, verdadera para la cabecera de la SCJN, el decreto de promulgación y las fórmulas de cierre: es parte del corpus y se vectoriza como cualquier otra unidad, pero el Atlas no la compara. Los otros 82
instrumentos del release `scjn-tratados` no aparecen: la SCJN los clasifica
pero no sirve texto consolidado de ninguno (`snapshots: 0`, issue #222,
decisión 6).

La deduplicación es **dentro** de la colección, no entre colecciones: un
texto que un tratado comparte con una ley tiene un vector en cada release
(issue #227, decisión 8).

## Advertencias

- **Esto es dato derivado, no texto legal oficial.** La fuente del texto es
  la SCJN; la oficial sigue siendo el DOF. Un vector no es citable.
- **Texto vigente solamente**, sin historia y sin `codNota`: esta colección
  no está ligada al DOF (issue #277).
- **44 tratados no tienen unidades de artículo**: son canjes de notas y
  protocolos en prosa, sin artículos romanos ni numerados que reconocer; su
  texto queda en unidades de preámbulo y contenido suelto.
- **Nada de esto se publica solo** (issue #115, Hallazgo C).
