from setuptools import setup, find_packages
from pathlib import Path


REQUIREMENTS_PATH = Path(__file__).with_name("requirements.txt")
README_PATH = Path(__file__).with_name("README.md")
INSTALL_REQUIRES = [
    line.strip()
    for line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]

setup(
    name="charles_stock_tools",
    version="0.1",
    description="A Python CLI toolkit for A-share watchlists, market heat, rotation, and screening workflows.",
    long_description=README_PATH.read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    license="MIT",
    packages=find_packages(exclude=("tests", "tests.*")),
    py_modules=[
        "main_controller",
    ],
    entry_points={
        "console_scripts": [
            "omg=main_controller:main",
        ],
    },
    install_requires=INSTALL_REQUIRES,
    package_data={"stocks": ["etf_universe.csv"]},
    include_package_data=True,
    python_requires=">=3.10,<3.14",
)
