"""MoP fork: a package installed editable (a .pth line in a packages folder) is a library, not
the model's own, so a build does not forget its modules (T062b r3 m1)."""
import site
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cadgen.engine.kernel import trace


class EditableInstallIsALibrary(unittest.TestCase):
    def test_pth_folder_is_a_library_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            packages, source, model = Path(tmp, "site-packages"), Path(tmp, "src"), Path(tmp, "model")
            for folder in (packages, source / "lib", model):
                folder.mkdir(parents=True)
            (packages / "_editable_lib.pth").write_text(f"{source}\nimport os\n", encoding="utf-8")
            (source / "lib" / "a.py").write_text("", encoding="utf-8")
            (model / "m.py").write_text("", encoding="utf-8")
            trace._library_roots.cache_clear()
            trace.first_party_file.cache_clear()
            try:
                with mock.patch.object(site, "getsitepackages", return_value=[str(packages)]):
                    self.assertFalse(trace.first_party_file(str(source / "lib" / "a.py")))
                    self.assertTrue(trace.first_party_file(str(model / "m.py")))
            finally:
                trace._library_roots.cache_clear()
                trace.first_party_file.cache_clear()


if __name__ == "__main__":
    unittest.main()
