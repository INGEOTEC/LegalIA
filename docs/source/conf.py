# Configuration file for the Sphinx documentation builder.
#
# For a full list of options see:
# http://www.sphinx-doc.org/en/master/config

# -- Path setup ---------------------------------------------------------
#
# Packages are installed editable by Read the Docs (see .readthedocs.yaml)
# rather than reached via sys.path manipulation here, so conf.py only needs
# to import the already-installed packages to read their __version__.

import dof2md
import dofjson
import md2akn
import nota2md

# -- Project information -------------------------------------------------

project = "LegalIA"
copyright = "2026, INGEOTEC"
author = "INGEOTEC"

# Each package under packages/<name>/ is versioned and released
# independently (see CLAUDE.md's read order: dofjson -> nota2md -> dof2md
# -> md2akn), so there is no single project version to set here. Instead
# each package gets its own substitution, used on index.rst's version table
# and in each *_api.rst page's own header.
rst_epilog = f"""
.. |dofjson_version| replace:: {dofjson.__version__}
.. |nota2md_version| replace:: {nota2md.__version__}
.. |dof2md_version| replace:: {dof2md.__version__}
.. |md2akn_version| replace:: {md2akn.__version__}
"""

# -- General configuration ------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx.ext.graphviz",
    "sphinx.ext.doctest",
]

# Render the architecture-flow diagram (index.rst) as inline SVG rather than
# a linked PNG, so it stays crisp at any zoom and matches the page background.
graphviz_output_format = "svg"

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "requests": ("https://requests.readthedocs.io/en/latest/", None),
}
# spaCy's own docs site (spacy.io) is not built with Sphinx and publishes no
# objects.inv, so md2akn's spaCy pipeline component (:doc:`md2akn_api`) has
# nothing to cross-reference against — checked directly (no
# https://spacy.io/objects.inv) rather than assumed.

templates_path = ["_templates"]
source_suffix = ".rst"
master_doc = "index"
language = "en"
exclude_patterns = []

pygments_style = "sphinx"
highlight_language = "python"
autodoc_member_order = "bysource"
autodoc_class_signature = "separated"
add_function_parentheses = False
add_module_names = False

# dof2md never imports mineru itself in-process — it only shells out to the
# mineru-api CLI as a subprocess (see dof2md/mineru_server.py) — so autodoc
# needs no mock for it; dof2md is installed with --no-deps in
# .readthedocs.yaml precisely so the heavy mineru[pipeline] install (and its
# libgl1/opencv system dependency, see test.yml) never has to happen for a
# docs build.

# -- Options for HTML output ----------------------------------------------

html_theme = "furo"
htmlhelp_basename = "LegalIAdoc"

# -- Options for LaTeX/manual/texinfo output -------------------------------

latex_documents = [
    (master_doc, "LegalIA.tex", "LegalIA Documentation", author, "manual"),
]
man_pages = [(master_doc, "legalia", "LegalIA Documentation", [author], 1)]
texinfo_documents = [
    (
        master_doc,
        "LegalIA",
        "LegalIA Documentation",
        author,
        "LegalIA",
        "Analysis of legal texts in the Mexican context.",
        "Miscellaneous",
    ),
]
