from pathlib import Path

from setuptools import setup, find_packages

readme = Path(__file__).parent / "README.md"

setup(
    name="nota2md",
    version="0.1.0",
    description="Build the Markdown of a single DOF note (by codNota), from its HTML content or by OCR'ing its scanned page images.",
    long_description=readme.read_text() if readme.exists() else "",
    long_description_content_type="text/markdown",
    url="https://github.com/INGEOTEC/LegalIA/tree/main/packages/nota2md",
    license="Apache-2.0",
    classifiers=[
        "License :: OSI Approved :: Apache Software License",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
    ],
    packages=find_packages(include=["nota2md", "nota2md.*"]),
    # dofjson (always) and dof2md (only for the image/OCR path) are sibling
    # packages in this monorepo, not on PyPI — install them editable from
    # packages/ alongside this one. Only the third-party deps are declared here.
    install_requires=[
        "beautifulsoup4>=4.9",
    ],
    extras_require={
        "test": ["pytest>=7.0"],
    },
    entry_points={
        "console_scripts": [
            "nota2md=nota2md.cli:main",
        ],
    },
    python_requires=">=3.9",
)
