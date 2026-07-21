import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from setuptools import Distribution
from setuptools.command.build_py import build_py


ROOT = Path(__file__).resolve().parents[1]


def load_build_py_command():
    with patch("setuptools.setup") as setup:
        runpy.run_path(str(ROOT / "setup.py"), run_name="__test_setup__")
    return setup.call_args.kwargs["cmdclass"]["build_py"]


class TestPackaging(unittest.TestCase):
    def test_build_py_removes_stale_build_lib(self):
        clean_build_py = load_build_py_command()

        with tempfile.TemporaryDirectory() as tmp:
            build_lib = Path(tmp) / "build" / "lib"
            stale_pyc = build_lib / "stocks" / "__pycache__" / "stale.pyc"
            stale_pyc.parent.mkdir(parents=True)
            stale_pyc.write_bytes(b"stale")

            command = clean_build_py(Distribution())
            command.build_lib = str(build_lib)
            command.build_root = Path(tmp) / "build"
            with patch.object(build_py, "run") as parent_run:
                command.run()

            self.assertFalse(stale_pyc.exists())
            parent_run.assert_called_once()

    def test_build_py_preserves_custom_output_directory(self):
        clean_build_py = load_build_py_command()

        with tempfile.TemporaryDirectory() as tmp:
            custom_lib = Path(tmp) / "custom-lib"
            unrelated = custom_lib / "unrelated.txt"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text("keep", encoding="utf-8")

            command = clean_build_py(Distribution())
            command.build_lib = str(custom_lib)
            command.build_root = Path(tmp) / "build"
            with patch.object(build_py, "run"):
                command.run()

            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
