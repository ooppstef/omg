import shutil
from pathlib import Path

from setuptools import find_packages, setup
from setuptools.command.build_py import build_py


REQUIREMENTS_PATH = Path(__file__).with_name("requirements.txt")
README_PATH = Path(__file__).with_name("README.md")
INSTALL_REQUIRES = [
    line.strip()
    for line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]


class CleanBuildPy(build_py):
    """Build packages from a clean tree so stale files cannot enter wheels."""

    build_root = Path(__file__).resolve().parent / "build"

    def run(self):
        if self.build_lib:
            build_lib = Path(self.build_lib).resolve()
            build_root = Path(self.build_root).resolve()
            if build_root in build_lib.parents:
                shutil.rmtree(build_lib, ignore_errors=True)
        super().run()


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
    package_data={
        "stocks": [
            "etf_universe.csv",
            "ai/bundled_skills/*/SKILL.md",
            "templates/*.html",
        ]
    },
    include_package_data=True,
    python_requires=">=3.10,<3.14",
    cmdclass={"build_py": CleanBuildPy},
)
