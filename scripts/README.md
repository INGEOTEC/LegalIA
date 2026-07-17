# scripts

Utilidades operativas del monorepo (no son paquetes instalables).

## `descargar_notas.py`

Descarga incremental del **índice de notas del DOF**, día por día, desde el 2
de enero de 1917 hasta hoy, usando el paquete [`dofjson`](../packages/dofjson).

Para cada fecha hace exactamente lo que el comando

```bash
dofjson AAAA-MM-DD --endpoint notas
```

es decir: consulta `get_notas(fecha)`, descarta las notas sin título con
`quita_notas_sin_titulo` y guarda el índice del día como JSON. **No** baja el
contenido de cada nota ni las imágenes escaneadas: sólo el índice.

Reanudable e idempotente: cada corrida sólo baja los días que faltan.

```bash
pip install -e "packages/dofjson"        # única dependencia

python scripts/descargar_notas.py                       # 1917-01-02 -> hoy
python scripts/descargar_notas.py --desde 1980-01-01 --hasta 1980-12-31
python scripts/descargar_notas.py --pausa 1.0           # más lento (amable con el servidor)
```

### Dónde se guarda

En `notas-archivo/` (configurable con `--outdir`), un directorio **local que no
se versiona** (está en `.gitignore`). Un archivo por día, con el mismo nombre
que produce el comando `dofjson`:

```
notas-archivo/
  .completados                 # registro de días ya bajados (para reanudar)
  2026/
    15072026-notas.json        # índice del 15-07-2026 (get_notas ya filtrado)
    16072026-notas.json
  1980/
    02011980-notas.json
```

### Reanudar / días faltantes

El registro `.completados` guarda los días ya bajados; al re-ejecutar, se saltan
y se continúa con lo que falte. Los días con error de red **no** se marcan, así
que se reintentan; los días sin edición (404) sí se marcan. El día de "hoy"
nunca se marca, para recoger notas publicadas más tarde. Puedes interrumpir con
Ctrl-C y volver a correrlo cuando quieras.

> El rango completo son ~40 000 días: es una descarga larga y pensada para
> correrse por partes. Empieza por un rango acotado con `--desde/--hasta` si
> sólo quieres una época.
