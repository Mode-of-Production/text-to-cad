"""Tests for Ticket CG-A (rounds 1, 2 & 3): mechanism harvest never drops a joint silently and keeps rigid edges."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import cadgen.engine as engine_pkg
from cadgen.engine import build as engine_build
from cadgen.engine import cli as engine_cli
from cadgen.engine import document, mechanism


class HarvestKeepTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-harvest-keep-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def build_model(self, name: str, source: str, *, ok: bool = True) -> dict:
        script = self.folder / f"{name}.py"
        script.write_text(textwrap.dedent(source), encoding="utf-8")
        os.utime(script, ns=(time.time_ns(), time.time_ns()))
        report = engine_build.build(script, [name], cwd=self.folder)[0]
        self.assertEqual(report["ok"], ok, report)
        return report

    def test_single_box_with_unconnected_revolute_warns_in_run_json(self) -> None:
        report = self.build_model("lone_hinge", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/lone_hinge.xbf")
            def lone_hinge():
                box = bd.Box(20, 20, 20)
                box.label = "box"
                bd.RevoluteJoint("hinge", box, axis=bd.Axis((0, 0, 0), (0, 0, 1)), angular_range=(0, 90))
                return bd.Compound(children=[box], label="scene")
        """)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = engine_cli._reported([report], 0.01, None, as_json=True)
        self.assertEqual(rc, 0)
        line = stdout.getvalue().strip().splitlines()[-1]
        payload = json.loads(line)
        self.assertIn("kinematics", payload)
        warnings = payload["kinematics"]["warnings"]
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("box", warnings[0])
        self.assertIn("hinge", warnings[0])
        loaded = document.Loaded(self.folder / "XBF/lone_hinge.xbf")
        self.assertEqual(loaded.mechanism.pairs if loaded.mechanism else [], [])

    def test_connected_door_plus_unconnected_shelf_joint_keeps_door_and_warns_shelf(self) -> None:
        report = self.build_model("cabinet", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/cabinet.xbf")
            def cabinet():
                carcass = bd.Box(600, 400, 800)
                carcass.label = "carcass"
                door = bd.Pos(0, -200, 0) * bd.Box(600, 18, 780)
                door.label = "door"
                shelf = bd.Pos(0, 0, 100) * bd.Box(560, 360, 18)
                shelf.label = "shelf"
                axis = bd.Axis((-300, -200, 0), (0, 0, 1))
                bd.RevoluteJoint("door_hinge", carcass, axis=axis, angular_range=(0, 110))
                bd.RigidJoint("door_hinge", door, axis.location)
                bd.LinearJoint("shelf_slide", shelf, axis=bd.Axis((0, 0, 100), (0, -1, 0)), linear_range=(0, 200))
                return bd.Compound(children=[carcass, door, shelf], label="cabinet")
        """)
        self.assertIn("kinematics", report)
        warnings = report["kinematics"]["warnings"]
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("shelf", warnings[0])
        self.assertIn("shelf_slide", warnings[0])
        loaded = document.Loaded(self.folder / "XBF/cabinet.xbf")
        self.assertIsNotNone(loaded.mechanism)
        self.assertEqual([pair.label for pair in loaded.mechanism.pairs], ["door_hinge"])

    def test_connect_to_missing_holder_warns_once(self) -> None:
        report = self.build_model("dangling", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/dangling.xbf")
            def dangling():
                base = bd.Box(40, 40, 10)
                base.label = "base_plate"
                ghost = bd.Box(10, 10, 10)
                ghost.label = "ghost"
                rev = bd.RevoluteJoint("orphan_pin", base, axis=bd.Axis((0, 0, 5), (0, 0, 1)), angular_range=(-90, 90))
                mount = bd.RigidJoint("ghost_mount", ghost, bd.Location((0, 0, 5)))
                rev.connect_to(mount, angle=0)
                return bd.Compound(children=[base], label="dangling")
        """)
        self.assertIn("kinematics", report)
        warnings = report["kinematics"]["warnings"]
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("base_plate", warnings[0])
        self.assertIn("orphan_pin", warnings[0])
        self.assertIn("holder", warnings[0])

    def test_drawer_box_five_panels_keeps_merged_link_and_four_rigid_edges(self) -> None:
        report = self.build_model("drawer5", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/drawer5.xbf")
            def drawer5():
                body = bd.Pos(0, 0, 300) * bd.Box(800, 500, 600)
                body.label = "body"
                bottom = bd.Pos(0, 0, 509) * bd.Box(700, 400, 18)
                bottom.label = "bottom"
                left = bd.Pos(-341, 0, 575) * bd.Box(18, 400, 150)
                left.label = "left"
                right = bd.Pos(341, 0, 575) * bd.Box(18, 400, 150)
                right.label = "right"
                front = bd.Pos(0, -191, 575) * bd.Box(700, 18, 150)
                front.label = "front"
                back = bd.Pos(0, 191, 575) * bd.Box(700, 18, 150)
                back.label = "back"

                slide_ax = bd.Axis((0, 0, 500), (0, -1, 0))
                bd.LinearJoint("slide", body, axis=slide_ax, linear_range=(0, 450))
                bd.RigidJoint("slide", bottom, bd.Location((0, 0, 500)))

                for jname, panel, pt in (
                    ("r1", left, (-350, 0, 509)),
                    ("r2", right, (350, 0, 509)),
                    ("r3", front, (0, -200, 509)),
                    ("r4", back, (0, 200, 509)),
                ):
                    loc = bd.Axis(pt, (0, 0, 1)).location
                    bd.RigidJoint(jname, bottom, loc)
                    bd.RigidJoint(jname, panel, loc)

                return bd.Compound(children=[body, bottom, left, right, front, back], label="drawer5")
        """)
        self.assertNotIn("kinematics", report)
        loaded = document.Loaded(self.folder / "XBF/drawer5.xbf")
        found = loaded.mechanism
        self.assertIsNotNone(found)
        self.assertEqual(len(found.links), 2)
        self.assertEqual(found.links[1].members, [(1,), (2,), (3,), (4,), (5,)])
        expected_edges = [
            ((1,), (2,), "r1"),
            ((1,), (3,), "r2"),
            ((1,), (4,), "r3"),
            ((1,), (5,), "r4"),
        ]
        self.assertEqual(getattr(found, "rigid_edges", None), expected_edges)
        kinematics = loaded.structure()["kinematics"]
        self.assertEqual(kinematics.get("rigid_edges"), [[list(a), list(b), name] for a, b, name in expected_edges])

    def test_self_attack_two_unconnected_joints_on_two_parts_give_two_warnings(self) -> None:
        report = self.build_model("two_unpaired", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/two_unpaired.xbf")
            def two_unpaired():
                left_wing = bd.Box(30, 10, 5)
                left_wing.label = "left_wing"
                right_wing = bd.Pos(50, 0, 0) * bd.Box(30, 10, 5)
                right_wing.label = "right_wing"
                bd.RevoluteJoint("flap_l", left_wing, axis=bd.Axis((0, 0, 0), (1, 0, 0)), angular_range=(-30, 30))
                bd.CylindricalJoint("rod_r", right_wing, axis=bd.Axis((50, 0, 0), (0, 1, 0)),
                                    angular_range=(-45, 45), linear_range=(0, 20))
                return bd.Compound(children=[left_wing, right_wing], label="wings")
        """)
        self.assertIn("kinematics", report)
        warnings = report["kinematics"]["warnings"]
        self.assertEqual(len(warnings), 2, warnings)
        joined = "\n".join(warnings)
        self.assertIn("left_wing", joined)
        self.assertIn("flap_l", joined)
        self.assertIn("right_wing", joined)
        self.assertIn("rod_r", joined)

    def test_self_attack_rigid_only_assembly_keeps_edges_or_warnings(self) -> None:
        report_paired = self.build_model("rigid_paired", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/rigid_paired.xbf")
            def rigid_paired():
                frame = bd.Box(100, 100, 10)
                frame.label = "frame"
                panel = bd.Pos(0, 0, 10) * bd.Box(50, 50, 10)
                panel.label = "panel"
                bd.RigidJoint("weld_a", frame, bd.Location((0, 0, 10)))
                bd.RigidJoint("weld_a", panel, bd.Location((0, 0, 10)))
                return bd.Compound(children=[frame, panel], label="rigid_paired")
        """)
        self.assertNotIn("kinematics", report_paired)
        loaded = document.Loaded(self.folder / "XBF/rigid_paired.xbf")
        self.assertIsNotNone(loaded.mechanism)
        self.assertEqual(loaded.mechanism.pairs, [])
        self.assertEqual(getattr(loaded.mechanism, "rigid_edges", None), [((0,), (1,), "weld_a")])

        report_unpaired = self.build_model("rigid_dangling", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/rigid_dangling.xbf")
            def rigid_dangling():
                frame = bd.Box(100, 100, 10)
                frame.label = "frame"
                ghost = bd.Box(10, 10, 10)
                ghost.label = "ghost"
                w1 = bd.RigidJoint("lonely_weld", frame, bd.Location((0, 0, 10)))
                w2 = bd.RigidJoint("ghost_weld", ghost, bd.Location((0, 0, 10)))
                w1.connect_to(w2)
                return bd.Compound(children=[frame], label="rigid_dangling")
        """)
        self.assertIn("kinematics", report_unpaired)
        warnings = report_unpaired["kinematics"]["warnings"]
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("frame", warnings[0])
        self.assertIn("lonely_weld", warnings[0])

    def test_self_attack_old_document_without_rigid_edges_field_reads(self) -> None:
        from OCP.Standard import Standard_GUID

        self.build_model("drawer_old", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/drawer_old.xbf")
            def drawer_old():
                body = bd.Pos(0, 0, 300) * bd.Box(800, 500, 600)
                body.label = "body"
                bottom = bd.Pos(0, 0, 509) * bd.Box(700, 400, 18)
                bottom.label = "bottom"
                left = bd.Pos(-341, 0, 575) * bd.Box(18, 400, 150)
                left.label = "left"
                bd.LinearJoint("slide", body, axis=bd.Axis((0, 0, 500), (0, -1, 0)), linear_range=(0, 450))
                bd.RigidJoint("slide", bottom, bd.Location((0, 0, 500)))
                loc = bd.Axis((-350, 0, 509), (0, 0, 1)).location
                bd.RigidJoint("r1", bottom, loc)
                bd.RigidJoint("r1", left, loc)
                return bd.Compound(children=[body, bottom, left], label="drawer_old")
        """)
        loaded = document.Loaded(self.folder / "XBF/drawer_old.xbf")
        root = next(label for label in mechanism._children_of(loaded.doc.Main())
                    if label.IsAttribute(Standard_GUID(mechanism.GUID)))
        top = root.FindChild(1, False)
        rigids_label = top.FindChild(7, False)
        self.assertFalse(rigids_label.IsNull(), "write must store the rigid_edges label")
        rigids_label.ForgetAllAttributes(True)
        read_old = mechanism.read(loaded.doc, loaded.occurrences)
        self.assertIsNotNone(read_old)
        self.assertEqual(read_old.rigid_edges, [])
        self.assertEqual(read_old.warnings, [])
        self.assertEqual([pair.label for pair in read_old.pairs], ["slide"])

    def test_r4_every_harvest_exit_pins_warnings_and_pairs(self) -> None:
        # Exit 3 (moving pairs kept + unpaired joint): cabinet with 2 connected hinges + 1 unconnected LinearJoint
        report_cab = self.build_model("cabinet_two_hinges", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/cabinet_two_hinges.xbf")
            def cabinet_two_hinges():
                carcass = bd.Box(600, 400, 800)
                carcass.label = "carcass"
                left_door = bd.Pos(-150, -200, 0) * bd.Box(300, 18, 780)
                left_door.label = "left_door"
                right_door = bd.Pos(150, -200, 0) * bd.Box(300, 18, 780)
                right_door.label = "right_door"
                shelf = bd.Pos(0, 0, 100) * bd.Box(560, 360, 18)
                shelf.label = "shelf"
                ax_l = bd.Axis((-300, -200, 0), (0, 0, 1))
                ax_r = bd.Axis((300, -200, 0), (0, 0, 1))
                bd.RevoluteJoint("hinge_l", carcass, axis=ax_l, angular_range=(0, 110))
                bd.RigidJoint("hinge_l", left_door, ax_l.location)
                bd.RevoluteJoint("hinge_r", carcass, axis=ax_r, angular_range=(-110, 0))
                bd.RigidJoint("hinge_r", right_door, ax_r.location)
                bd.LinearJoint("drawer_track", shelf, axis=bd.Axis((0, 0, 100), (0, -1, 0)), linear_range=(0, 200))
                return bd.Compound(children=[carcass, left_door, right_door, shelf], label="cabinet_two_hinges")
        """)
        self.assertIn("kinematics", report_cab)
        self.assertEqual(len(report_cab["kinematics"]["warnings"]), 1)
        self.assertIn("shelf", report_cab["kinematics"]["warnings"][0])
        self.assertIn("drawer_track", report_cab["kinematics"]["warnings"][0])
        loaded_cab = document.Loaded(self.folder / "XBF/cabinet_two_hinges.xbf")
        self.assertEqual([p.label for p in loaded_cab.mechanism.pairs], ["hinge_l", "hinge_r"])

        # Exit 2 (pairings non-empty, moving_pairs empty, rigid_edges empty, warnings non-empty: B1 rr case)
        report_rr = self.build_model("rr_two_moving", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/rr_two_moving.xbf")
            def rr_two_moving():
                p1 = bd.Box(20, 20, 5)
                p1.label = "plate"
                p2 = bd.Pos(0, 0, 5) * bd.Box(20, 5, 5)
                p2.label = "lever"
                a = bd.RevoluteJoint("turn", p1, axis=bd.Axis((0, 0, 2.5), (0, 0, 1)), angular_range=(0, 90))
                b = bd.RevoluteJoint("spin", p2, axis=bd.Axis((0, 0, 2.5), (0, 0, 1)), angular_range=(0, 90))
                a.connected_to = b
                return bd.Compound(children=[p1, p2], label="rr_two_moving")
        """)
        self.assertIn("kinematics", report_rr)
        self.assertEqual(len(report_rr["kinematics"]["warnings"]), 1)
        self.assertIn("turn", report_rr["kinematics"]["warnings"][0])
        self.assertIn("spin", report_rr["kinematics"]["warnings"][0])
        loaded_rr = document.Loaded(self.folder / "XBF/rr_two_moving.xbf")
        self.assertIsNotNone(loaded_rr.mechanism)
        self.assertEqual(loaded_rr.mechanism.links, [])
        self.assertEqual(loaded_rr.mechanism.pairs, [])
        self.assertEqual(loaded_rr.mechanism.rigid_edges, [])

    def test_r5_two_identical_drawers_and_same_label_parts_keep_distinct_edges_and_refuse_malformed(self) -> None:
        from OCP.Standard import Standard_GUID
        from OCP.XCAFDoc import XCAFDoc_AssemblyItemRef

        # Seed vector 2: Two identical drawers (same part labels) of 5 rigid panels each -> 8 distinct rigid edges
        report_drawers = self.build_model("two_drawers", """
            import build123d as bd
            from cadgen.engine import xbf

            def make_drawer(z_off, tag):
                bottom = bd.Pos(0, 0, z_off + 9) * bd.Box(700, 400, 18)
                bottom.label = "bottom"
                left = bd.Pos(-341, 0, z_off + 75) * bd.Box(18, 400, 150)
                left.label = "left"
                right = bd.Pos(341, 0, z_off + 75) * bd.Box(18, 400, 150)
                right.label = "right"
                front = bd.Pos(0, -191, z_off + 75) * bd.Box(700, 18, 150)
                front.label = "front"
                back = bd.Pos(0, 191, z_off + 75) * bd.Box(700, 18, 150)
                back.label = "back"
                bd.RigidJoint("slide_" + tag, bottom, bd.Location((0, 0, z_off)))
                for jname, panel, pt in (
                    ("r1", left, (-350, 0, z_off + 9)),
                    ("r2", right, (350, 0, z_off + 9)),
                    ("r3", front, (0, -200, z_off + 9)),
                    ("r4", back, (0, 200, z_off + 9)),
                ):
                    loc = bd.Axis(pt, (0, 0, 1)).location
                    bd.RigidJoint(jname, bottom, loc)
                    bd.RigidJoint(jname, panel, loc)
                return bd.Compound(children=[bottom, left, right, front, back], label="drawer")

            @xbf("XBF/two_drawers.xbf")
            def two_drawers():
                body = bd.Pos(0, 0, 400) * bd.Box(800, 500, 800)
                body.label = "body"
                d1 = make_drawer(200, "1")
                d2 = make_drawer(500, "2")
                bd.LinearJoint("slide_1", body, axis=bd.Axis((0, 0, 200), (0, -1, 0)), linear_range=(0, 400))
                bd.LinearJoint("slide_2", body, axis=bd.Axis((0, 0, 500), (0, -1, 0)), linear_range=(0, 400))
                return bd.Compound(children=[body, d1, d2], label="two_drawers")
        """)
        self.assertNotIn("kinematics", report_drawers)
        loaded_drawers = document.Loaded(self.folder / "XBF/two_drawers.xbf")
        edges = loaded_drawers.mechanism.rigid_edges
        self.assertEqual(len(edges), 8, edges)
        self.assertEqual(len(set(edges)), 8, "all 8 rigid edges across identical drawers must be distinct")
        expected_8 = [
            ((1, 0), (1, 1), "r1"), ((1, 0), (1, 2), "r2"), ((1, 0), (1, 3), "r3"), ((1, 0), (1, 4), "r4"),
            ((2, 0), (2, 1), "r1"), ((2, 0), (2, 2), "r2"), ((2, 0), (2, 3), "r3"), ((2, 0), (2, 4), "r4"),
        ]
        self.assertEqual(sorted(edges), sorted(expected_8))

        # Verify OCAF child :7 uses XCAFDoc_AssemblyItemRef on endpoints, and module docstring documents :7
        self.assertIn(':7 "rigid_edges"', mechanism.__doc__)
        root = next(label for label in mechanism._children_of(loaded_drawers.doc.Main())
                    if label.IsAttribute(Standard_GUID(mechanism.GUID)))
        rigids_label = root.FindChild(1, False).FindChild(7, False)
        first_edge = rigids_label.FindChild(1, False)
        for end_tag in (1, 2):
            item = XCAFDoc_AssemblyItemRef()
            self.assertTrue(document._find(first_edge.FindChild(end_tag, False), item, XCAFDoc_AssemblyItemRef.GetID_s()))

        # Same-label flat parts: two parts both labelled 'panel' fastened to 'frame'
        self.build_model("same_label_flat", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/same_label_flat.xbf")
            def same_label_flat():
                frame = bd.Box(100, 100, 10)
                frame.label = "frame"
                p1 = bd.Pos(-30, 0, 10) * bd.Box(20, 20, 10)
                p1.label = "panel"
                p2 = bd.Pos(30, 0, 10) * bd.Box(20, 20, 10)
                p2.label = "panel"
                for name, panel, x in (("w1", p1, -30), ("w2", p2, 30)):
                    bd.RigidJoint(name, frame, bd.Location((x, 0, 5)))
                    bd.RigidJoint(name, panel, bd.Location((x, 0, 5)))
                return bd.Compound(children=[frame, p1, p2], label="same_label_flat")
        """)
        loaded_flat = document.Loaded(self.folder / "XBF/same_label_flat.xbf")
        self.assertEqual(loaded_flat.mechanism.rigid_edges, [((0,), (1,), "w1"), ((0,), (2,), "w2")])

        # Malformed edge in OCAF child 7: refused with a warning, not silently skipped
        bad_edge = mechanism._label(rigids_label, 99, "corrupt_weld")
        mechanism._label(bad_edge, 1, "only_one_string_end")
        reread = mechanism.read(loaded_drawers.doc, loaded_drawers.occurrences)
        self.assertEqual(len(reread.rigid_edges), 8)
        self.assertEqual(len(reread.warnings), 1, reread.warnings)
        self.assertIn("corrupt_weld", reread.warnings[0])

    def test_r6a_connect_to_outside_tree_warns_and_falls_back_to_label_pairing(self) -> None:
        report = self.build_model("outside_with_label_match", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/outside_with_label_match.xbf")
            def outside_with_label_match():
                base = bd.Box(40, 40, 10)
                base.label = "base_plate"
                arm = bd.Pos(0, 0, 10) * bd.Box(30, 5, 5)
                arm.label = "arm"
                ghost = bd.Box(10, 10, 10)
                ghost.label = "ghost"
                rev = bd.RevoluteJoint("pin", base, axis=bd.Axis((0, 0, 5), (0, 0, 1)), angular_range=(-90, 90))
                bd.RigidJoint("pin", arm, bd.Location((0, 0, 5)))
                mount = bd.RigidJoint("ghost_mount", ghost, bd.Location((0, 0, 5)))
                rev.connect_to(mount, angle=0)
                return bd.Compound(children=[base, arm], label="outside_with_label_match")
        """)
        self.assertIn("kinematics", report)
        warnings = report["kinematics"]["warnings"]
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("ghost_mount", warnings[0])
        loaded = document.Loaded(self.folder / "XBF/outside_with_label_match.xbf")
        self.assertIsNotNone(loaded.mechanism)
        self.assertEqual([pair.label for pair in loaded.mechanism.pairs], ["pin"])
        self.assertEqual([(link.name, link.members) for link in loaded.mechanism.links],
                         [("base_plate", [(0,)]), ("arm", [(1,)])])

    def test_r6b_rigid_groups_without_moving_pair_form_separate_links(self) -> None:
        report = self.build_model("rigid_two_clusters", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/rigid_two_clusters.xbf")
            def rigid_two_clusters():
                parts = []
                for name, x, z in (("a", 0, 0), ("b", 0, 10), ("c", 500, 0), ("d", 500, 10)):
                    p = bd.Pos(x, 0, z) * bd.Box(10, 10, 10)
                    p.label = name
                    parts.append(p)
                a, b, c, d = parts
                bd.RigidJoint("w1", a, bd.Location((0, 0, 5)))
                bd.RigidJoint("w1", b, bd.Location((0, 0, 5)))
                bd.RigidJoint("w2", c, bd.Location((500, 0, 5)))
                bd.RigidJoint("w2", d, bd.Location((500, 0, 5)))
                return bd.Compound(children=parts, label="rigid_two_clusters")
        """)
        self.assertNotIn("kinematics", report)
        loaded = document.Loaded(self.folder / "XBF/rigid_two_clusters.xbf")
        self.assertIsNotNone(loaded.mechanism)
        self.assertEqual([(link.name, link.members) for link in loaded.mechanism.links],
                         [("a", [(0,), (1,)]), ("c", [(2,), (3,)])])
        self.assertEqual(loaded.mechanism.rigid_edges, [((0,), (1,), "w1"), ((2,), (3,), "w2")])

    def test_r6c_datum_rigid_joints_do_not_warn_and_unpaired_moving_joints_have_unique_warnings(self) -> None:
        report_pegs = self.build_model("pegs_datums", """
            import build123d as bd
            from cadgen.engine import xbf

            def peg():
                p = bd.Box(2, 2, 6)
                p.label = "peg"
                bd.RigidJoint("base", p, bd.Location((0, 0, -3)))
                return p

            @xbf("XBF/pegs_datums.xbf")
            def pegs_datums():
                first, second, third = peg(), bd.Pos(10, 0, 0) * peg(), bd.Pos(20, 0, 0) * peg()
                bd.RigidJoint("front", first, bd.Location())
                bd.RigidJoint("rear", third, bd.Location())
                return bd.Compound(children=[first, second, third], label="pegs_datums")
        """)
        self.assertNotIn("kinematics", report_pegs)
        loaded_pegs = document.Loaded(self.folder / "XBF/pegs_datums.xbf")
        self.assertIsNone(loaded_pegs.mechanism)

        report_dup = self.build_model("same_label_unpaired_moving", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/same_label_unpaired_moving.xbf")
            def same_label_unpaired_moving():
                p1 = bd.Box(10, 10, 10)
                p1.label = "panel"
                p2 = bd.Pos(30, 0, 0) * bd.Box(10, 10, 10)
                p2.label = "panel"
                bd.RevoluteJoint("hinge", p1, axis=bd.Axis((0, 0, 0), (0, 0, 1)), angular_range=(0, 90))
                bd.RevoluteJoint("hinge", p2, axis=bd.Axis((30, 0, 0), (0, 0, 1)), angular_range=(0, 90))
                return bd.Compound(children=[p1, p2], label="same_label_unpaired_moving")
        """)
        self.assertIn("kinematics", report_dup)
        warnings = report_dup["kinematics"]["warnings"]
        self.assertEqual(len(warnings), 2, warnings)
        self.assertEqual(len(set(warnings)), 2, "unpaired non-rigid warnings must not contain duplicate strings")

    @contextlib.contextmanager
    def _with_base_mechanism(self, base_dir: Path):
        repo_root = Path(__file__).resolve().parents[5]
        proc = subprocess.run(
            ["git", "show", "5df06799:packages/cadgen/src/cadgen/engine/mechanism.py"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            encoding="utf-8",
        )
        base_file = base_dir / "_base_mechanism_5df06799.py"
        base_file.write_text(proc.stdout, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("cadgen.engine.mechanism", base_file)
        assert spec is not None and spec.loader is not None
        base_mod = importlib.util.module_from_spec(spec)
        old_sys = sys.modules.get("cadgen.engine.mechanism")
        old_attr = getattr(engine_pkg, "mechanism", None)
        sys.modules["cadgen.engine.mechanism"] = base_mod
        setattr(engine_pkg, "mechanism", base_mod)
        try:
            spec.loader.exec_module(base_mod)
            yield base_mod
        finally:
            if old_sys is not None:
                sys.modules["cadgen.engine.mechanism"] = old_sys
            if old_attr is not None:
                setattr(engine_pkg, "mechanism", old_attr)

    @staticmethod
    def _comparable_kinematics(loaded: document.Loaded) -> tuple[dict, bytes]:
        m = loaded.mechanism
        assert m is not None
        stored = {
            "name": m.name,
            "links": [(link.name, list(link.members)) for link in m.links],
            "ground": m.ground,
            "pairs": [
                (
                    p.label,
                    p.kind,
                    p.links,
                    tuple(tuple(round(v, 9) for v in f) for f in p.frames) if p.frames else None,
                    p.limits,
                )
                for p in m.pairs
            ],
            "as_built": {k: tuple(round(v, 9) for v in vals) for k, vals in m.as_built.items()},
            "configurations": m.configurations,
            "motions": {k: v.signature() for k, v in m.motions.items()},
            "couplings": {k: v.signature() for k, v in m.couplings.items()},
        }
        kin = dict(loaded.structure()["kinematics"])
        kin.pop("rigid_edges", None)
        kin.pop("warnings", None)
        return stored, json.dumps(kin, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def test_r7_ground_and_stored_kinematics_identical_to_base_5df06799(self) -> None:
        models = {
            "pinslot_first": """
                import build123d as bd
                from cadgen.engine import xbf

                @xbf("XBF/pinslot_first.xbf")
                def pinslot_first():
                    carcass = bd.Box(600, 400, 800)
                    carcass.label = "carcass"
                    door = bd.Pos(0, -209, 0) * bd.Box(600, 18, 780)
                    door.label = "door"
                    ax = bd.Axis((-300, -200, 0), (0, 0, 1))
                    bd.RevoluteJoint("hinge", carcass, axis=ax, angular_range=(0, 110))
                    bd.RigidJoint("hinge", door, ax.location)
                    p = bd.Pos(1000, 0, 0) * bd.Box(20, 20, 5)
                    p.label = "plate"
                    q = bd.Pos(1000, 0, 5) * bd.Box(20, 5, 5)
                    q.label = "lever"
                    a1 = bd.RevoluteJoint("turn", p, axis=bd.Axis((1000, 0, 2.5), (0, 0, 1)), angular_range=(0, 90))
                    a2 = bd.RevoluteJoint("spin", q, axis=bd.Axis((1000, 0, 2.5), (0, 0, 1)), angular_range=(0, 90))
                    a1.connected_to = a2
                    return bd.Compound(children=[p, q, carcass, door], label="pinslot_first")
            """,
            "mixed_hinge_pinslot_cluster": """
                import build123d as bd
                from cadgen.engine import xbf

                @xbf("XBF/mixed_hinge_pinslot_cluster.xbf")
                def mixed_hinge_pinslot_cluster():
                    carcass = bd.Box(600, 400, 800)
                    carcass.label = "carcass"
                    door = bd.Pos(0, -209, 0) * bd.Box(600, 18, 780)
                    door.label = "door"
                    ax = bd.Axis((-300, -200, 0), (0, 0, 1))
                    bd.RevoluteJoint("hinge", carcass, axis=ax, angular_range=(0, 110))
                    bd.RigidJoint("hinge", door, ax.location)
                    p = bd.Pos(1000, 0, 0) * bd.Box(20, 20, 5)
                    p.label = "plate"
                    q = bd.Pos(1000, 0, 5) * bd.Box(20, 5, 5)
                    q.label = "lever"
                    a1 = bd.RevoluteJoint("turn", p, axis=bd.Axis((1000, 0, 2.5), (0, 0, 1)), angular_range=(0, 90))
                    a2 = bd.RevoluteJoint("spin", q, axis=bd.Axis((1000, 0, 2.5), (0, 0, 1)), angular_range=(0, 90))
                    a1.connected_to = a2
                    c = bd.Pos(-1000, 0, 0) * bd.Box(10, 10, 10)
                    c.label = "c"
                    d = bd.Pos(-1000, 0, 10) * bd.Box(10, 10, 10)
                    d.label = "d"
                    bd.RigidJoint("w", c, bd.Location((-1000, 0, 5)))
                    bd.RigidJoint("w", d, bd.Location((-1000, 0, 5)))
                    return bd.Compound(children=[carcass, door, p, q, c, d], label="mixed_hinge_pinslot_cluster")
            """,
        }

        base_dir = self.folder / "base_5df06799"
        base_dir.mkdir(parents=True, exist_ok=True)
        base_results: dict[str, tuple[dict, bytes]] = {}
        with self._with_base_mechanism(base_dir):
            for name, src in models.items():
                script = base_dir / f"{name}.py"
                script.write_text(textwrap.dedent(src), encoding="utf-8")
                os.utime(script, ns=(time.time_ns(), time.time_ns()))
                rep = engine_build.build(script, [name], cwd=base_dir)[0]
                self.assertTrue(rep["ok"], rep)
                loaded_base = document.Loaded(base_dir / f"XBF/{name}.xbf")
                base_results[name] = self._comparable_kinematics(loaded_base)

        for name, src in models.items():
            self.build_model(name, src)
            loaded_pat = document.Loaded(self.folder / f"XBF/{name}.xbf")
            pat_stored, pat_kin_bytes = self._comparable_kinematics(loaded_pat)
            base_stored, base_kin_bytes = base_results[name]
            self.assertEqual(pat_stored, base_stored, f"stored kinematics mismatch vs base for {name}")
            self.assertEqual(pat_kin_bytes, base_kin_bytes, f"structure kinematics JSON mismatch vs base for {name}")

    def test_r8_outside_tree_connect_to_keeps_all_reasons_in_order_and_reports_label_fallback(self) -> None:
        # 1. Outside-tree connect_to + label tie -> one warning naming both reasons in order
        report_tie = self.build_model("outside_tree_with_tie", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/outside_tree_with_tie.xbf")
            def outside_tree_with_tie():
                base = bd.Box(40, 40, 10)
                base.label = "base_plate"
                arm1 = bd.Pos(-20, 0, 10) * bd.Box(10, 5, 5)
                arm1.label = "arm1"
                arm2 = bd.Pos(20, 0, 10) * bd.Box(10, 5, 5)
                arm2.label = "arm2"
                ghost = bd.Box(10, 10, 10)
                ghost.label = "ghost"
                rev = bd.RevoluteJoint("pin", base, axis=bd.Axis((0, 0, 5), (0, 0, 1)), angular_range=(-90, 90))
                bd.RigidJoint("pin", arm1, bd.Location((0, 0, 5)))
                bd.RigidJoint("pin", arm2, bd.Location((0, 0, 5)))
                mount = bd.RigidJoint("ghost_mount", ghost, bd.Location((0, 0, 5)))
                rev.connect_to(mount, angle=0)
                return bd.Compound(children=[base, arm1, arm2], label="outside_tree_with_tie")
        """)
        self.assertIn("kinematics", report_tie)
        warnings_tie = report_tie["kinematics"]["warnings"]
        self.assertEqual(len(warnings_tie), 1, warnings_tie)
        w_tie = warnings_tie[0]
        self.assertIn("ghost_mount", w_tie)
        self.assertIn("holder was not found", w_tie)
        self.assertIn("matches the rigid joints", w_tie)
        self.assertIn("arm1", w_tie)
        self.assertIn("arm2", w_tie)
        self.assertLess(w_tie.index("holder was not found"), w_tie.index("matches the rigid joints"))

        # 2. Outside-tree connect_to + no label match -> one warning naming both reasons in order
        report_nomatch = self.build_model("outside_tree_no_match", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/outside_tree_no_match.xbf")
            def outside_tree_no_match():
                base = bd.Box(40, 40, 10)
                base.label = "base_plate"
                ghost = bd.Box(10, 10, 10)
                ghost.label = "ghost"
                rev = bd.RevoluteJoint("orphan_pin", base, axis=bd.Axis((0, 0, 5), (0, 0, 1)), angular_range=(-90, 90))
                mount = bd.RigidJoint("ghost_mount", ghost, bd.Location((0, 0, 5)))
                rev.connect_to(mount, angle=0)
                return bd.Compound(children=[base], label="outside_tree_no_match")
        """)
        warnings_nomatch = report_nomatch["kinematics"]["warnings"]
        self.assertEqual(len(warnings_nomatch), 1, warnings_nomatch)
        w_nomatch = warnings_nomatch[0]
        self.assertIn("holder was not found", w_nomatch)
        self.assertIn("no rigid candidate", w_nomatch)
        self.assertLess(w_nomatch.index("holder was not found"), w_nomatch.index("no rigid candidate"))

        # 3. Outside-tree connect_to + successful label fallback -> says it paired by label
        report_paired = self.build_model("outside_tree_paired_by_label", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/outside_tree_paired_by_label.xbf")
            def outside_tree_paired_by_label():
                base = bd.Box(40, 40, 10)
                base.label = "base_plate"
                arm = bd.Pos(0, 0, 10) * bd.Box(30, 5, 5)
                arm.label = "arm"
                ghost = bd.Box(10, 10, 10)
                ghost.label = "ghost"
                rev = bd.RevoluteJoint("pin", base, axis=bd.Axis((0, 0, 5), (0, 0, 1)), angular_range=(-90, 90))
                bd.RigidJoint("pin", arm, bd.Location((0, 0, 5)))
                mount = bd.RigidJoint("ghost_mount", ghost, bd.Location((0, 0, 5)))
                rev.connect_to(mount, angle=0)
                return bd.Compound(children=[base, arm], label="outside_tree_paired_by_label")
        """)
        warnings_paired = report_paired["kinematics"]["warnings"]
        self.assertEqual(len(warnings_paired), 1, warnings_paired)
        w_paired = warnings_paired[0]
        self.assertIn("holder was not found", w_paired)
        self.assertIn("paired by label", w_paired)
        self.assertLess(w_paired.index("holder was not found"), w_paired.index("paired by label"))

    def test_r9_pairless_ground_stated_in_docstring_and_pinned(self) -> None:
        doc = mechanism.__doc__ or ""
        self.assertIn("links[0]", doc)
        self.assertIn("pair-less", doc)
        self.build_model("pairless_ground_pin", """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/pairless_ground_pin.xbf")
            def pairless_ground_pin():
                loose = bd.Pos(-200, 0, 0) * bd.Box(10, 10, 10)
                loose.label = "loose"
                a = bd.Pos(0, 0, 0) * bd.Box(10, 10, 10)
                a.label = "first_cluster"
                b = bd.Pos(0, 0, 10) * bd.Box(10, 10, 10)
                b.label = "b"
                c = bd.Pos(200, 0, 0) * bd.Box(10, 10, 10)
                c.label = "second_cluster"
                d = bd.Pos(200, 0, 10) * bd.Box(10, 10, 10)
                d.label = "d"
                bd.RigidJoint("w1", a, bd.Location((0, 0, 5)))
                bd.RigidJoint("w1", b, bd.Location((0, 0, 5)))
                bd.RigidJoint("w2", c, bd.Location((200, 0, 5)))
                bd.RigidJoint("w2", d, bd.Location((200, 0, 5)))
                return bd.Compound(children=[loose, a, b, c, d], label="pairless_ground_pin")
        """)
        loaded = document.Loaded(self.folder / "XBF/pairless_ground_pin.xbf")
        self.assertIsNotNone(loaded.mechanism)
        self.assertEqual(loaded.mechanism.pairs, [])
        self.assertEqual(loaded.mechanism.ground, 0)
        self.assertEqual(
            [(link.name, link.members) for link in loaded.mechanism.links],
            [("first_cluster", [(1,), (2,)]), ("second_cluster", [(3,), (4,)])],
        )
        kin = loaded.structure().get("kinematics")
        self.assertIsNotNone(kin)
        self.assertEqual(kin["ground"], 0)
        self.assertEqual(kin["pairs"], [])
        self.assertEqual(kin["links"][0]["name"], "first_cluster")
        self.assertEqual(kin["links"][0]["instances"], [0, 1, 2])
        self.assertEqual(kin["links"][1]["name"], "second_cluster")
        self.assertEqual(kin["links"][1]["instances"], [3, 4])
        self.assertEqual(kin["rigid_edges"], [[[1], [2], "w1"], [[3], [4], "w2"]])

    def test_r10_moving_joint_tie_does_not_change_rigid_mating_vs_base_5df06799(self) -> None:
        # A moving joint whose label ties between two rigid joints on two other parts refuses only that
        # moving pair; the two rigid joints still fasten their parts, exactly as base _by_label did.
        name = "tie_keeps_fastening"
        src = """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf("XBF/tie_keeps_fastening.xbf")
            def tie_keeps_fastening():
                body = bd.Box(300, 300, 100)
                body.label = "body"
                lid = bd.Pos(0, 0, 55) * bd.Box(300, 300, 10)
                lid.label = "lid"
                axis = bd.Axis((0, 150, 50), (1, 0, 0))
                bd.RevoluteJoint("flap", body, axis=axis, angular_range=(0, 95))
                bd.RigidJoint("flap", lid, axis.location)
                handle = bd.Pos(0, -140, 65) * bd.Box(40, 10, 10)
                handle.label = "handle"
                stand = bd.Pos(-800, 0, 0) * bd.Box(30, 30, 30)
                stand.label = "stand"
                bd.LinearJoint("grip", stand, axis=bd.Axis((-800, 0, 15), (0, 0, 1)), linear_range=(0, 40))
                bd.RigidJoint("grip", lid, bd.Location((0, -140, 60)))
                bd.RigidJoint("grip", handle, bd.Location((0, -140, 60)))
                return bd.Compound(children=[body, lid, handle, stand], label="tie_keeps_fastening")
        """
        base_dir = self.folder / "base_5df06799"
        base_dir.mkdir(parents=True, exist_ok=True)
        with self._with_base_mechanism(base_dir):
            script = base_dir / f"{name}.py"
            script.write_text(textwrap.dedent(src), encoding="utf-8")
            os.utime(script, ns=(time.time_ns(), time.time_ns()))
            base_report = engine_build.build(script, [name], cwd=base_dir)[0]
            self.assertTrue(base_report["ok"], base_report)
            base_stored, base_kin = self._comparable_kinematics(document.Loaded(base_dir / f"XBF/{name}.xbf"))

        report = self.build_model(name, src)
        loaded = document.Loaded(self.folder / f"XBF/{name}.xbf")
        stored, kin = self._comparable_kinematics(loaded)
        self.assertEqual(stored, base_stored, "a moving-joint tie must not change the links of base")
        self.assertEqual(kin, base_kin)
        lid_members = next(link.members for link in loaded.mechanism.links if (1,) in link.members)
        self.assertIn((2,), lid_members, "the fastened part must move with its rigid mate")
        self.assertEqual(loaded.mechanism.rigid_edges, [((1,), (2,), "grip")])
        warnings = (report.get("kinematics") or {}).get("warnings") or []
        base_warnings = (base_report.get("kinematics") or {}).get("warnings") or []
        self.assertEqual(len(warnings), 1, warnings)
        self.assertEqual(warnings, base_warnings)
        self.assertIn("stand", warnings[0])
        self.assertIn("alike", warnings[0])


if __name__ == "__main__":
    unittest.main()
