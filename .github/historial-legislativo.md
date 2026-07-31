Historial de cambios de la legislación federal mexicana, ligado a las legal
provisions del Diario Oficial de la Federación que lo publicaron. Cada entrada
es un `codNota`: el título, la fecha y el organismo emisor se recuperan
uniéndolo contra el
[archivo de legal provisions del DOF](https://github.com/INGEOTEC/LegalIA/releases/tag/notas-archivo).

| Asset | Contenido |
|---|---|
| `leyes.tgz` | Las leyes y códigos federales del índice de LeyesBiblio, más `leyes.json` con el catálogo |
| `reglamentos.tgz` | Los reglamentos de leyes federales vigentes, más `reglamentos.json` |
| `normas.tgz` | Las Normas Oficiales Mexicanas (`noms.json`), su catálogo y las citas que no identifican una norma |
| `tratados.tgz` | Los tratados internacionales (`tratados.json`) y su catálogo |

Se reconstruye el día 2 de cada mes con
[`.github/workflows/reformas.yml`](https://github.com/INGEOTEC/LegalIA/blob/master/.github/workflows/reformas.yml),
que verifica este release y sube solo los assets cuyo contenido cambió. Los
`.tgz` son reproducibles byte a byte, así que datos idénticos producen el mismo
archivo y `SHA256SUMS.txt` sirve para comprobarlos:

```bash
gh release download historial-legislativo --repo INGEOTEC/LegalIA
sha256sum -c SHA256SUMS.txt
```

Cómo se construye cada colección, qué queda fuera y por qué, está documentado en
[`packages/leyesmx/README.md`](https://github.com/INGEOTEC/LegalIA/blob/master/packages/leyesmx/README.md).
