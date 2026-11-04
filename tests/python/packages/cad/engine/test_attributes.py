"""Part attributes (MoP fork): ``shape.attributes`` written as XCAF ``TDataStd_NamedData`` on
the part's label, read back as ``Prototype.attributes``, kept by a rebuild and by a warm edit."""
from __future__ import annotations

import importlib.util
import tempfile
import textwrap
import unittest
from pathlib import Path

import build123d as bd

from cad.engine import build as engine_build
from cad.engine import document
from cad.engine.api import export

SOURCE = '''
import build123d as bd
from cad import xbf

@xbf("panel.xbf")
def panel():
    side = bd.Box(600, 18, 720)
    side.label = "side"
    side.attributes = {"grain": "GRAIN", "thickness": 18.0, "count": 2}
    back = bd.Box(600, 6, 720).moved(bd.Location((0, 300, 0)))
    back.label = "back"
    return bd.Compound(children=[side, back], label="panel")
'''

# Two parts of one geometry, each its own construction: the build merges them (``document.merge``)
# unless something of their own sets them apart -- here, the attribute.
TWINS = '''
import build123d as bd
from cad import model, xbf

def board(label, **attributes):
    made = bd.Box(400, 200, 18)
    made.label = label
    if attributes:
        made.attributes = attributes
    return made

def made():
    return bd.Compound(children=[board("one"), board("two", grain="width").moved(bd.Location((0, 0, 50)))],
                       label="twins")

@xbf("twins.xbf")
def twins():
    return made()

@model
def held():
    return made()
'''


class AttributesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="part-attributes-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def roundtrip(self, shape):
        tree = document.collect(shape, "test")
        path = self.folder / "test.xbf"
        document.save(document.to_xcaf(tree), path, compare=False, tree=tree)
        return tree, path, document.Loaded(path)

    def test_attributes_survive_the_file_and_the_structure(self):
        left = bd.Box(10, 10, 10)
        left.label = "left"
        left.attributes = {"grain": "x", "t": 18.0, "n": 3}
        right = bd.Box(10, 10, 10).moved(bd.Location((20, 0, 0)))
        right.label = "right"
        tree, path, loaded = self.roundtrip(bd.Compound(children=[left, right], label="pair"))
        by_name = {p.name: p for p in loaded.prototypes}
        self.assertEqual(by_name["left"].attributes, {"grain": "x", "n": 3, "t": 18.0})
        self.assertIsInstance(by_name["left"].attributes["n"], int)
        self.assertEqual(by_name["right"].attributes, {})
        read = loaded.structure()["prototypes"]
        made = document.tree_structure(tree, path)["prototypes"]
        self.assertEqual([p.get("attributes") for p in read], [p.get("attributes") for p in made])

    def test_parts_differing_only_by_attributes_are_two_prototypes(self):
        one = bd.Box(5, 5, 5)
        two = bd.Box(5, 5, 5)
        two.wrapped = one.wrapped.Moved(bd.Location((10, 0, 0)).wrapped)
        one.attributes = {"grain": "x"}
        two.attributes = {"grain": "y"}
        _, _, loaded = self.roundtrip(bd.Compound(children=[one, two]))
        self.assertEqual(sorted(p.attributes["grain"] for p in loaded.prototypes), ["x", "y"])

    def test_a_build_does_not_merge_twins_that_differ_by_attributes(self):
        script = self.folder / "twins.py"
        script.write_text(textwrap.dedent(TWINS), encoding="utf-8")
        report = engine_build.build(script, ["twins"], cwd=self.folder)[0]
        self.assertTrue(report["ok"], report.get("error"))
        found = {p.name: p.attributes for p in document.Loaded(self.folder / "twins.xbf").prototypes}
        self.assertEqual(found, {"one": {}, "two": {"grain": "width"}})

    def test_an_exported_xbf_keeps_the_attributes(self):
        script = self.folder / "twins.py"
        script.write_text(textwrap.dedent(TWINS), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("twins_model", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        copy = self.folder / "copy.xbf"  # the held document written out, as `cad export <id> X.xbf` does
        export(module.held, copy, daemon=False, progress=False)
        found = {p.name: p.attributes for p in document.Loaded(copy).prototypes}
        self.assertEqual(found, {"one": {}, "two": {"grain": "width"}})

    def test_bad_attributes_are_refused(self):
        for bad in ([("grain", "x")], {"": "x"}, {"grain": True}, {"grain": None}, {1: "x"}, {"grain": [1]}):
            shape = bd.Box(1, 1, 1)
            shape.attributes = bad
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "attribute"):
                document.collect(shape, "bad")

    def test_rebuild_keeps_and_warm_edit_updates_the_attribute(self):
        script = self.folder / "model.py"

        def build(grain):
            script.write_text(textwrap.dedent(SOURCE.replace("GRAIN", grain)), encoding="utf-8")
            report = engine_build.build(script, ["panel"], cwd=self.folder)[0]
            self.assertTrue(report["ok"], report.get("error"))
            found = {p.name: p.attributes for p in document.Loaded(self.folder / "panel.xbf").prototypes}
            self.assertEqual(found["back"], {})
            return found["side"]

        self.assertEqual(build("x"), {"count": 2, "grain": "x", "thickness": 18.0})
        self.assertEqual(build("x")["grain"], "x", "a rebuild of the same script keeps it")
        self.assertEqual(build("z")["grain"], "z", "a warm edit of the attribute alone rewrites the label")


if __name__ == "__main__":
    unittest.main()
