"""Read the reform history that the Cámara de Diputados publishes per law.

LeyesBiblio (https://www.diputados.gob.mx/LeyesBiblio/) keeps, for every
federal law, a table of the decrees that have reformed it together with the
date each one was published in the Diario Oficial de la Federación. That
table is the curated, authoritative account of a law's evolution: the DOF
publishes the decrees, but only Diputados says which ones amend which law.

The Constitution has its own chronological page (`ref/cpeum_crono.htm`) whose
rows are numbered, so a reform can be cited as "reforma 284". Ordinary laws
use `ref/<abbr>.htm`.
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path

import requests

BASE = "https://www.diputados.gob.mx/LeyesBiblio"
# LeyesBiblio serves Windows-1252, not UTF-8, and does not always say so.
ENCODING = "cp1252"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; LegalIA-leyesmx/0.1)"}

# The Constitution's chronological table is numbered; ordinary laws are not.
PAGINAS = {"cpeum": "ref/cpeum_crono.htm"}

_FECHA = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
# Ordinary laws write the publication date inside the cell, either as
# "DOF DD-MM-YYYY" or bare as "DD-MM-YYYY" — both spellings are in use, often
# on the same page. The markup splits the date across spans often enough that
# the separators need slack.
_FECHA_DOF = re.compile(r"(?:DOF\s*)?(\d{2})\s*-\s*(\d{2})\s*-\s*(\d{4})")
# A row's date lives in the paragraph carrying the links to the decree, which
# is what keeps a date written into the decree's own prose from being read as
# its publication date.
_ENLACE_DECRETO = re.compile(r'href="[^"]+\.(?:pdf|doc)"', re.I)
_TR = re.compile(r"<tr[^>]*>.*?</tr>", re.S)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_P = re.compile(r"<p\b.*?</p>", re.S)
_TAG = re.compile(r"<[^>]+>")
_HREF = re.compile(r'href="([^"]+)"', re.I)
# Diputados names each decree's file <LEY>_ref<N>_<ddmmmyy>. That number is
# more dependable than the table's own numbering column, which several pages
# leave out of the row (see _numero_de_reforma).
_ARCHIVO_REF = re.compile(r"_ref_?(\d+)_", re.I)
# Any other kind of instrument Diputados files in the same table.
_ARCHIVO_OTRO_TIPO = re.compile(
    r"_(?:cant|fe|sent|voto|decla|acuerdo|tarifa|acla|aclara|abro|engrose|"
    r"notifica|lista|mod|prev|aviso)_?\d*_\d{2}[a-z]{3}\d{2}", re.I)
# Where the reform table starts, and the label the original publication sits
# under in the header above it.
_ENCABEZADO_REFORMAS = "Decretos de Reforma"
_PUBLICACION_ORIGINAL = "Publicación Original"
# The reform table is not only reforms. Diputados files a dozen other kinds of
# instrument in it, each named after its kind and each numbered from 1 in the
# same column: `_cant` (restatements of peso amounts), `_fe` (errata), `_sent`
# and `_voto` (SCJN rulings), `_decla` (entry-into-force declarations),
# `_acuerdo`, `_tarifa`, `_acla`, `_abro`, `_notifica` and more. Two series
# sharing the numbering column is why `cnpp` and `ligie_2022` appeared to have
# duplicate reform numbers. A reform is a row linking a `_refNN_` file.
_NO_ES_REFORMA = re.compile(r"Actualización de cantidades|Sentencia de la SCJN", re.I)

# Month abbreviations Diputados uses in file names, e.g. LGPGIR_ref08_04jun14.
_MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
          "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
_FECHA_ARCHIVO = re.compile(r"_(\d{2})(" + "|".join(_MESES) + r")(\d{2})\b", re.I)
# Diputados mirrors each decree as published in the DOF; the "_ima" variant is
# the scan of the gazette page, the plain one carries extractable text.
_ES_PDF = re.compile(r"\.pdf$", re.I)
# Diputados appends an editorial summary of what the decree did; it is not
# part of the decree's title.
_RESUMEN = re.compile(r"\bNota\s*:|\bNuevo\b")
_ENTIDADES = {
    "&nbsp;": " ", "&quot;": '"', "&amp;": "&", "&oacute;": "ó", "&aacute;": "á",
    "&eacute;": "é", "&iacute;": "í", "&uacute;": "ú", "&ntilde;": "ñ",
    "&Oacute;": "Ó", "&Aacute;": "Á", "&Eacute;": "É", "&Iacute;": "Í",
    "&Uacute;": "Ú", "&Ntilde;": "Ñ", "&deg;": "°", "&ordm;": "º",
}


@dataclass
class Reforma:
    """One decree that reformed a law, as Diputados records it.

    `no` is None for the row that is not a reform but the law's original
    publication, which the Constitution's table carries at the top.
    """

    no: int | None          # Diputados' own numbering (Constitution only)
    fecha: str              # DOF publication date, DD-MM-YYYY (as in dofjson)
    decreto: str            # decree title, editorial summary stripped
    ley: str = ""
    pdf: str = ""           # Diputados' own copy of the DOF publication


def pagina_de_reformas(ley: str) -> str:
    """URL of `ley`'s reform table (`ley` is its LeyesBiblio abbreviation)."""
    return f"{BASE}/{PAGINAS.get(ley, f'ref/{ley}.htm')}"


def _pide(url: str, timeout: int, intentos: int = 4) -> str:
    """Fetch a LeyesBiblio page, decoded out of its cp1252, retrying briefly.

    Reading all 316 laws is 316 requests, and the server resets a connection
    now and then; dropping a whole law over a transient failure would silently
    leave a hole in the catalogue.
    """
    for intento in range(intentos):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=timeout)
            r.raise_for_status()
            return r.content.decode(ENCODING, errors="replace")
        except requests.exceptions.RequestException:
            if intento == intentos - 1:
                raise
            time.sleep(2 ** intento)


def descarga(ley: str, timeout: int = 60) -> str:
    """The reform page's HTML, decoded out of LeyesBiblio's cp1252."""
    return _pide(pagina_de_reformas(ley), timeout)


def _texto(celda: str) -> str:
    t = _TAG.sub(" ", celda)
    for k, v in _ENTIDADES.items():
        t = t.replace(k, v)
    return re.sub(r"\s+", " ", t).strip()


def _pdf_del_decreto(fila: str, ley: str) -> str:
    """URL of Diputados' copy of the decree, preferring the text-bearing PDF
    over the `_ima` scan of the gazette page."""
    pdfs = [h for h in _HREF.findall(fila) if _ES_PDF.search(h)]
    if not pdfs:
        return ""
    texto = [h for h in pdfs if "_ima" not in h.lower()]
    elegido = (texto or pdfs)[0]
    if elegido.startswith("http"):
        return elegido
    return f"{BASE}/{PAGINAS.get(ley, f'ref/{ley}.htm').rsplit('/', 1)[0]}/{elegido}"


def _ordena(reformas: list[Reforma]) -> list[Reforma]:
    """Oldest first, ties broken by Diputados' own numbering."""
    reformas.sort(key=lambda r: (r.fecha[-4:], r.fecha[3:5], r.fecha[:2], r.no or -1))
    return reformas


def _parse_crono(html: str, ley: str) -> list[Reforma]:
    """The Constitution's chronological table: `No. | Decreto | DD/MM/YYYY | …`,
    each field in its own cell."""
    reformas = []
    for fila in _TR.findall(html):
        celdas = [_texto(c) for c in _TD.findall(fila)]
        fecha = next((m for m in (_FECHA.match(c) for c in celdas) if m), None)
        if fecha is None:
            continue
        d, mo, y = fecha.groups()
        no = int(celdas[0]) if celdas and celdas[0].isdigit() else None
        decreto = next((c for c in celdas if len(c) > 25), "")
        reformas.append(
            Reforma(no=no, fecha=f"{d}-{mo}-{y}",
                    decreto=_RESUMEN.split(decreto)[0].strip(), ley=ley,
                    pdf=_pdf_del_decreto(fila, ley))
        )
    return _ordena(reformas)


def _fecha_de_publicacion(celda: str) -> str | None:
    """The DOF date a reform row publishes on, as DD-MM-YYYY.

    Read from the paragraph holding the links to the decree, since that is
    where the date belongs; a date written into the decree's own title would
    otherwise be mistaken for it. Falls back to the whole cell for rows whose
    paragraphs the markup does not delimit cleanly.
    """
    for parrafo in _P.findall(celda):
        if not _ENLACE_DECRETO.search(parrafo):
            continue
        m = _FECHA_DOF.search(_texto(parrafo))
        if m:
            d, mo, y = m.groups()
            return f"{d}-{mo}-{y}"
    m = _FECHA_DOF.search(_texto(celda))
    if m:
        d, mo, y = m.groups()
        return f"{d}-{mo}-{y}"
    return _fecha_del_archivo(celda)


def _fecha_del_archivo(celda: str, hoy: int = 2026) -> str | None:
    """The date encoded in the decree's file name, e.g. `_ref08_04jun14`.

    A fallback for the occasional typo on Diputados' side: `lgpgir`'s reform 8
    is written "DOF 04-06-214", a year short a digit, while the file it links
    says `04jun14`. The two-digit year is read as this century when that would
    not put the reform in the future, and as the last one otherwise — a
    gazette cannot publish a decree that has not happened yet.
    """
    for href in _HREF.findall(celda):
        m = _FECHA_ARCHIVO.search(href)
        if not m:
            continue
        d, mes, yy = m.group(1), m.group(2).lower(), int(m.group(3))
        año = 2000 + yy if 2000 + yy <= hoy else 1900 + yy
        return f"{d}-{_MESES[mes]:02d}-{año}"
    return None


def _numero_de_reforma(fila: str, celda_no: str) -> int | None:
    """Diputados' number for a reform row, or None if the row is not a reform.

    The two sources of the number answer different questions, so each is used
    for what it is good at.

    Whether the row is a reform is decided by the file it links: the table also
    carries a dozen other kinds of instrument (see _NO_ES_REFORMA), each
    numbered from 1 in the same column, and reading those as reforms is what
    made `cnpp` and `ligie_2022` look like they had duplicate numbers.

    Which reform it is comes from the numbering column, which is the more
    accurate of the two where they disagree — both cases in LeyesBiblio are a
    row linking the wrong file: `lgpsedmtp`'s reform 4 links reform 3's PDF,
    and `loapf`'s reform 47 links a file belonging to another law entirely.
    The file name is the fallback, and a needed one: plenty of rows leave the
    column empty though the row is plainly a decree — `reg_senado` does it for
    reforms 23-29, `lft` for 35-36.
    """
    del_archivo = next(
        (m for m in (_ARCHIVO_REF.search(h) for h in _HREF.findall(fila)) if m), None
    )
    if del_archivo is None and _ARCHIVO_OTRO_TIPO.search(fila):
        return None
    if celda_no.isdigit():
        return int(celda_no)
    return int(del_archivo.group(1)) if del_archivo else None


def _parse_ref(html: str, ley: str, nombre: str = "") -> list[Reforma]:
    """An ordinary law's page: the original publication, then a table of
    decrees whose row holds the title and the date together in one cell.

    Only numbered rows are reforms. The unnumbered ones restate peso amounts
    rather than amend the law, and Diputados does not number them either.
    "Fe de erratas" entries are corrections, not reforms, and are left out.
    """
    corte = html.find(_ENCABEZADO_REFORMAS)
    cabeza, cuerpo = (html[:corte], html[corte:]) if corte > 0 else (html, "")

    reformas = []
    # The original publication is index 0, as the Constitution's table has it.
    # It is located by its label: on pages that lack one, the first date in the
    # header belongs to something else (`ccom`'s is a peso-amount update).
    etiqueta = cabeza.find(_PUBLICACION_ORIGINAL)
    if etiqueta >= 0:
        original = _FECHA_DOF.search(_texto(cabeza[etiqueta:]))
        if original:
            d, mo, y = original.groups()
            # The DOF publishes a law's original text under the law's own
            # name, so that is what the entry has to be matched on; the
            # placeholder it used to carry matched nothing (see
            # dof.puntua_entrada).
            reformas.append(Reforma(no=None, fecha=f"{d}-{mo}-{y}",
                                    decreto=nombre or "Publicación original",
                                    ley=ley,
                                    pdf=_pdf_del_decreto(cabeza[etiqueta:], ley)))

    for fila in _TR.findall(cuerpo):
        celdas = _TD.findall(fila)
        if len(celdas) < 2:
            continue
        texto_fila = _texto(celdas[1])
        if _NO_ES_REFORMA.search(texto_fila):
            continue
        fecha = _fecha_de_publicacion(celdas[1])
        if fecha is None:
            continue
        no = _numero_de_reforma(fila, _texto(celdas[0]))
        if no is None:
            continue
        # The row's paragraphs are the decree's title and then its links; the
        # title is the first that is prose rather than a date line.
        parrafos = [_texto(p) for p in _P.findall(celdas[1])]
        decreto = next(
            (p for p in parrafos if len(p) > 25 and not _FECHA_DOF.search(p)),
            texto_fila,
        )
        reformas.append(
            Reforma(no=no, fecha=fecha,
                    decreto=_RESUMEN.split(decreto)[0].strip(), ley=ley,
                    pdf=_pdf_del_decreto(fila, ley))
        )
    return _ordena(reformas)


def parse_reformas(html: str, ley: str = "", nombre: str = "") -> list[Reforma]:
    """Every reform in a LeyesBiblio page, oldest first.

    Dates are rewritten to DD-MM-YYYY so they join directly against
    `dofjson`'s `fecha`. Two layouts exist and are told apart by content: the
    Constitution's chronological table puts the date in its own cell, while
    every ordinary law writes it as "DOF DD-MM-YYYY" inside the same cell as
    the decree's title.
    """
    if _ENCABEZADO_REFORMAS in html:
        return _parse_ref(html, ley, nombre)
    return _parse_crono(html, ley)


@dataclass
class Ley:
    """One law in LeyesBiblio's catalogue."""

    no: int                 # Diputados' position in the index (1 = the Constitution)
    abrev: str              # LeyesBiblio abbreviation; `pagina_de_reformas` takes it
    nombre: str


_INDICE = "index.htm"
_ENLACE_REF = re.compile(r'href="ref/([A-Za-z0-9_]+)\.htm"', re.I)
_SOLO_DIGITOS = re.compile(r"^\d{1,3}$")


def descarga_indice(timeout: int = 60) -> str:
    """LeyesBiblio's index page, decoded out of its cp1252."""
    return _pide(f"{BASE}/{_INDICE}", timeout)


def lista_leyes(html: str) -> list[Ley]:
    """Every law the index lists, in its own order.

    A catalogue row is numbered and links to the law's reform page; rows
    without both are layout. The Constitution comes first, as number 1, and
    the abbreviation is what `pagina_de_reformas()` resolves — for the
    Constitution that is remapped to its chronological table (see PAGINAS).
    """
    leyes, vistos = [], set()
    for fila in _TR.findall(html):
        celdas = _TD.findall(fila)
        if len(celdas) < 2:
            continue
        no = _texto(celdas[0])
        enlace = _ENLACE_REF.search(fila)
        if enlace is None or not _SOLO_DIGITOS.match(no):
            continue
        abrev = enlace.group(1)
        if abrev in vistos:
            continue
        vistos.add(abrev)
        # The cell holds the law's name and then its publication dates; the
        # name is the linked text.
        nombre = _texto(re.sub(r"</a>.*", "", celdas[1], flags=re.S))
        leyes.append(Ley(no=int(no), abrev=abrev, nombre=nombre))
    return leyes


@dataclass
class Reglamento:
    """One federal regulation, with its reforms, as LeyesBiblio records it."""

    no: int
    abrev: str              # Diputados' own file stem, lowercased: `reg_ladua`
    nombre: str
    reformas: list          # list[Reforma], oldest first


#: `regla.htm` lists the regulations *in force* that implement a federal law,
#: and is the only LeyesBiblio page that carries their reform history.
#: `regley_abro.htm` has the same shape for abrogated ones and can be passed to
#: `parse_reglamentos()` as well. `norma/reglamento.htm` ("Reglamentos
#: Federales Vigentes") is a directory of current texts with no history at all
#: — 145 dates and not one `_refNN_` file — so no reform list can come from it.
PAGINA_REGLAMENTOS = "regla.htm"
PAGINA_REGLAMENTOS_ABROGADOS = "regley_abro.htm"

# A regulation's whole history sits inline in its row, one <a> per entry: the
# href says what kind of entry it is and the link text is the date. Reading the
# anchors is therefore enough — the row's Spanish labels ("Original",
# "Reforma", "Cantidades") never have to be interpreted.
#
# Two naming generations coexist. Newer entries carry the reform number and a
# readable date, `Reg_LAero_ref03_29sep17.doc`; older ones carry neither, just
# the date run together, `Reg_LAero_ref080800.doc`. So the number is taken from
# chronological order instead of the file name (see `parse_reglamentos`).
# Regulations write the date with slashes, laws with hyphens.
_FECHA_ENTRADA = re.compile(r"(?:DOF\s*)?(\d{2})\s*/\s*(\d{2})\s*/\s*(\d{4})")
# Each entry is an italic label followed by one or more dated anchors. A single
# paragraph can switch kind part-way — "Reformas <a>a</a>, <a>b</a>, Fe de E.
# <a>c</a>" — so the cell is walked in order, carrying the label forward, and
# every anchor belongs to the last label seen.
_ETIQUETA_O_ANCLA = re.compile(
    r"<i\b[^>]*>(?P<etiqueta>.*?)</i>"
    r'|<a\b[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<texto>.*?)</a>',
    re.S | re.I,
)
_ES_ORIGINAL = re.compile(r"original", re.I)
_ES_REFORMA = re.compile(r"reforma", re.I)
# The stem identifies the regulation; three naming generations exist and only
# the newest states the reform number, so `_numera()` derives it from order.
_ARCHIVO_ENTRADA = re.compile(r"^([A-Za-z0-9_]+?)_(?:ref|orig)?_?\d", re.I)
_ARCHIVO_NO_REFORMA = re.compile(
    r"_(?:cant|fe|abro|acla|aclara|vig|sent|voto)_?\d*_?\d", re.I)


def descarga_reglamentos(pagina: str = PAGINA_REGLAMENTOS, timeout: int = 60) -> str:
    """The regulations index, decoded out of LeyesBiblio's cp1252."""
    return _pide(f"{BASE}/{pagina}", timeout)


def parse_reglamentos(html: str) -> list[Reglamento]:
    """Every regulation in the index, each with its reforms oldest first.

    Regulations are laid out unlike laws: there is no page per regulation, so
    the whole history lives in the index row, and each entry is an anchor whose
    file name carries its kind and number. Only `_refNN_` entries are reforms —
    `_cant` (restated peso amounts), `_fe`, `_vig` and `_abro` are other
    instruments, exactly as in the laws' tables.

    The identifier is Diputados' own file stem lowercased (`Reg_LAdua` ->
    `reg_ladua`), which is stable and unique across the collection.
    """
    reglamentos = []
    for fila in _TR.findall(html):
        celdas = _TD.findall(fila)
        if len(celdas) < 2 or not _SOLO_DIGITOS.match(_texto(celdas[0])):
            continue

        # The name is the row's first paragraph. It is styled bold by a span
        # rather than a <b> on many rows, so the markup is no help in finding
        # it; its position is.
        parrafos = _P.findall(celdas[1])
        nombre = _texto(parrafos[0]) if parrafos else _texto(celdas[1])[:120]

        abrev, clase, original, reformas = None, None, None, []
        for m in _ETIQUETA_O_ANCLA.finditer(celdas[1]):
            if m.group("etiqueta") is not None:
                texto = _texto(m.group("etiqueta"))
                if _ES_ORIGINAL.search(texto):
                    clase = "original"
                elif _ES_REFORMA.search(texto):
                    clase = "reforma"
                elif texto.strip(" ,"):
                    clase = "otro"       # Fe de E., Cantidades, Aclaración…
                continue

            archivo = m.group("href").rsplit("/", 1)[-1]
            stem = _ARCHIVO_ENTRADA.match(archivo)
            if stem is None:
                continue                 # the row's current-text PDF/WORD
            if abrev is None:
                abrev = stem.group(1).lower()
            fecha = _FECHA_ENTRADA.search(_texto(m.group("texto")))
            if fecha is None:
                continue
            d, mo, y = fecha.groups()
            entrada = Reforma(no=None, fecha=f"{d}-{mo}-{y}", ley=abrev,
                              decreto=nombre, pdf=_url_absoluta(m.group("href")))
            if clase == "original":
                original = original or entrada
            elif clase == "reforma" and not _ARCHIVO_NO_REFORMA.search(archivo):
                reformas.append(entrada)

        if abrev is None:
            # No history is linked at all — 49 of the 137 rows are like this.
            # The row still states the original publication date in its own
            # column, so the regulation is recorded with that and no reforms.
            abrev = _abrev_del_texto_vigente(celdas)
            fecha = _FECHA_ENTRADA.search(_texto(celdas[2])) if len(celdas) > 2 else None
            if abrev is None or fecha is None:
                continue
            d, mo, y = fecha.groups()
            original = Reforma(no=None, fecha=f"{d}-{mo}-{y}", ley=abrev,
                               decreto=nombre, pdf="")

        reglamentos.append(Reglamento(
            no=int(_texto(celdas[0])), abrev=abrev, nombre=nombre,
            reformas=([original] if original else []) + _numera(reformas),
        ))
    return reglamentos


def _abrev_del_texto_vigente(celdas: list) -> str | None:
    """The regulation's identifier from the link to its current text.

    The only place it appears for a row that links no history:
    `regley/Reg_LAgra_MCDETS.pdf` -> `reg_lagra_mcdets`.
    """
    for celda in celdas[2:]:
        for href in _HREF.findall(celda):
            nombre = href.rsplit("/", 1)[-1]
            if nombre.lower().endswith((".pdf", ".doc")):
                return nombre.rsplit(".", 1)[0].lower()
    return None


def _numera(reformas: list) -> list:
    """Number a regulation's reforms 1..N by publication date.

    Diputados numbers them chronologically, but only says so in the newer file
    names; the older ones carry no number at all. Position is the one signal
    present for every entry, and it agrees with every number that *is* stated
    — `reg_laero`'s two unnumbered files are 2000 and 2003, and the next file
    it names explicitly is `ref03`.
    """
    ordenadas = sorted(
        reformas, key=lambda r: (r.fecha[-4:], r.fecha[3:5], r.fecha[:2])
    )
    for posicion, entrada in enumerate(ordenadas, 1):
        entrada.no = posicion
    return ordenadas


def numeracion_declarada(reglamentos: list) -> list[tuple[str, int, int]]:
    """Where a regulation's file names disagree with chronological numbering.

    Only for checking the assumption `_numera()` rests on; the numbering itself
    does not consult it. Returns `(abrev, declarado, por_posicion)` per
    mismatch, and an empty list when every stated number agrees.
    """
    desacuerdos = []
    for r in reglamentos:
        declarados = {}
        for entrada in r.reformas:
            if entrada.no is None:
                continue
            m = _ARCHIVO_REF.search(entrada.pdf.rsplit("/", 1)[-1])
            if m:
                declarados[entrada.no] = int(m.group(1))
        for posicion, declarado in declarados.items():
            if declarado != posicion:
                desacuerdos.append((r.abrev, declarado, posicion))
    return desacuerdos


def _url_absoluta(href: str) -> str:
    return href if href.startswith("http") else f"{BASE}/{href}"


def descarga_decreto(reforma: Reforma, dest: Path, timeout: int = 120) -> Path:
    """Download Diputados' copy of a reform's decree, as published in the DOF.

    A second, independent route to the primary source: Diputados mirrors every
    decree it lists, so a reform stays reachable even when the DOF's own
    service cannot serve the day it was published — as happens with the
    Constitution's reform 139 of 08-03-1999.
    """
    if not reforma.pdf:
        raise ValueError(f"la reforma {reforma.no} no trae PDF en LeyesBiblio")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(reforma.pdf, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest
