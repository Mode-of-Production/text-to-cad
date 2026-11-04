"""MoP fork (061-1 D5): ``cad.mop_xbf`` reads a built XBF's structure and per-prototype facts."""
from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from cad import mop_xbf
from cad.engine import build as engine_build

SOURCE = '''
import build123d as bd
from cad import xbf

@xbf("pair.xbf")
def pair():
    a = bd.Box(10, 20, 30)
    a.label = "a"
    b = bd.Box(10, 20, 30).moved(bd.Location((100, 0, 0)))
    b.label = "b"
    return bd.Compound(children=[a, b], label="pair")
'''


class MopXbfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp(prefix="mop-xbf-"))
        script = cls.folder / "pair.py"
        script.write_text(textwrap.dedent(SOURCE), encoding="utf-8")
        engine_build.build(script)
        cls.path = cls.folder / "pair.xbf"

    def test_read_gives_structure_and_prototype_facts(self) -> None:
        found = mop_xbf.read(self.path)
        structure = found["structure"]
        self.assertIn("tree", structure)
        self.assertEqual(len(found["prototypes"]), len(structure["prototypes"]))
        self.assertAlmostEqual(found["prototypes"][0]["volume"], 6000.0, places=3)
        self.assertTrue(all("matrix" in item for item in structure["instances"]))

    def test_placed_moves_the_box_by_the_matrix(self) -> None:
        found = mop_xbf.read(self.path)
        boxes = sorted(mop_xbf.placed(found["prototypes"][item.get("prototype", 0)], item["matrix"])["box"]["min"][0]
                       for item in found["structure"]["instances"])
        self.assertAlmostEqual(boxes[-1] - boxes[0], 100.0, places=6)


if __name__ == "__main__":
    unittest.main()
