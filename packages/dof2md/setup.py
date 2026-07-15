from pathlib import Path

from setuptools import setup, find_packages

readme = Path(__file__).parent / "README.md"

setup(
    name="dof2md",
    version="0.1.0",
    description="Download editions of Mexico's official gazette (DOF) as PDF and convert them to Markdown.",
    long_description=readme.read_text() if readme.exists() else "",
    long_description_content_type="text/markdown",
    url="https://github.com/INGEOTEC/LegalIA/tree/main/packages/dof2md",
    license="Apache-2.0",
    classifiers=[
        "License :: OSI Approved :: Apache Software License",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
    ],
    packages=find_packages(include=["dof2md", "dof2md.*"]),
    install_requires=[
        "requests>=2.31",
        "pymupdf>=1.24",
    ],
    extras_require={
        "test": ["pytest>=7.0"],
        "ocr": ["mineru[pipeline]"],
    },
    entry_points={
        "console_scripts": [
            "dof2md=dof2md.cli:main",
        ],
    },
    python_requires=">=3.9",
)
