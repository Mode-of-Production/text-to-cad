"""Closed-loop mechanism posing: a position solver closes every loop pair across resolve,
LinkWorlds, posed, posed_structure, Timeline, and _motions, while refusing unclosable poses."""

from __future__ import annotations

import math
import shutil
import tempfile
import unittest
from pathlib import Path

import build123d as bd
import numpy as np

from cadgen.ap242 import configuration_sequence, gear_pair, mechanism_state, posed
from cadgen.engine import document, mechanism

# --- Closed-form geometry references (pure math) ---------------------------------------------

GROUND, CRANK, COUPLER, ROCKER = 60.0, 25.0, 55.0, 45.0
PIN_A = (0.0, 0.0)
PIN_D = (GROUND, 0.0)


def _circle_intersections(p0: tuple[float, float], r0: float,
                          p1: tuple[float, float], r1: float) -> list[tuple[float, float]] | None:
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    d = math.hypot(dx, dy)
    if d > r0 + r1 or d < abs(r0 - r1) or d == 0.0:
        return None
    a = (r0 * r0 - r1 * r1 + d * d) / (2.0 * d)
    h = math.sqrt(max(0.0, r0 * r0 - a * a))
    mx, my = p0[0] + a * dx / d, p0[1] + a * dy / d
    return [(mx + h * dy / d, my - h * dx / d), (mx - h * dy / d, my + h * dx / d)]


def _fourbar_closed_form(theta_deg: float, l1: float = GROUND, l2: float = CRANK,
                         l3: float = COUPLER, l4: float = ROCKER) -> dict[str, object] | None:
    t = math.radians(theta_deg)
    b = (l2 * math.cos(t), l2 * math.sin(t))
    d = (l1, 0.0)
    sols = _circle_intersections(b, l3, d, l4)
    if sols is None:
        return None
    c = max(sols, key=lambda pt: pt[1])
    coupler_deg = math.degrees(math.atan2(c[1] - b[1], c[0] - b[0]))
    rocker_deg = math.degrees(math.atan2(c[1] - d[1], c[0] - d[0]))
    return {"theta": theta_deg, "B": b, "C": c, "coupler_deg": coupler_deg, "rocker_deg": rocker_deg}


LID_R = 300.0
CYL_BASE = (500.0, -200.0)


def _lid_pin_and_len(phi_deg: float) -> tuple[tuple[float, float], float]:
    p = math.radians(phi_deg)
    r = (LID_R * math.cos(p), LID_R * math.sin(p))
    return r, math.hypot(r[0] - CYL_BASE[0], r[1] - CYL_BASE[1])


def _lid_angle_from_len(length: float) -> float:
    lo, hi = 0.0, 90.0
    flo = _lid_pin_and_len(lo)[1] - length
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if (_lid_pin_and_len(mid)[1] - length) * flo > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _cyl_dir_deg(phi_deg: float) -> float:
    r, _ = _lid_pin_and_len(phi_deg)
    return math.degrees(math.atan2(r[1] - CYL_BASE[1], r[0] - CYL_BASE[0]))


# --- Assembly builders -----------------------------------------------------------------------


def _bar(p: tuple[float, float], q: tuple[float, float], z: float, name: str) -> bd.Shape:
    length = math.hypot(q[0] - p[0], q[1] - p[1])
    ang = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
    part = bd.Box(length + 8.0, 8.0, 6.0)
    part.label = name
    part.locate(bd.Location(((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0, z), (0.0, 0.0, ang)))
    return part


def _make_fourbar(theta0: float = 60.0, l1: float = GROUND, l2: float = CRANK,
                  l3: float = COUPLER, l4: float = ROCKER,
                  d_limits: tuple[float, float] = (-180.0, 180.0),
                  zero_at_x: bool = True,
                  knob: bool = False) -> tuple[bd.Compound, dict[str, object]]:
    """Build a planar four-bar at crank angle ``theta0`` degrees.
    When ``zero_at_x`` is True, joint A's angle is the crank angle in degrees (60 at theta0=60)."""
    fb0 = _fourbar_closed_form(theta0, l1, l2, l3, l4)
    assert fb0 is not None
    a, b, c, d = (0.0, 0.0), fb0["B"], fb0["C"], (l1, 0.0)
    ground = bd.Box(100.0, 12.0, 6.0)
    ground.label = "ground"
    ground.locate(bd.Location((30.0, -14.0, 0.0)))
    crank = _bar(a, b, 8.0, "crank")
    door = _bar(b, c, 16.0, "door")
    rocker = _bar(d, c, 24.0, "rocker")

    ax_a = bd.Axis((*a, 0.0), (0.0, 0.0, 1.0))
    bd.RevoluteJoint("A", ground, axis=ax_a, angular_range=(-180.0, 180.0))
    bd.RigidJoint("A", crank, joint_location=ax_a.location * bd.Rot(0.0, 0.0, theta0 if zero_at_x else 0.0))

    for label, parent, child, pt, limits in (
        ("B", crank, door, b, (-180.0, 180.0)),
        ("D", ground, rocker, d, d_limits),
        ("C", rocker, door, c, (-180.0, 180.0)),
    ):
        ax = bd.Axis((*pt, 0.0), (0.0, 0.0, 1.0))
        bd.RevoluteJoint(label, parent, axis=ax, angular_range=limits)
        bd.RigidJoint(label, child, joint_location=ax.location)

    children = [ground, crank, door, rocker]
    if knob:
        # An open-tree part on the ground, outside the loop: joint K.
        part = bd.Box(6.0, 6.0, 6.0)
        part.label = "knob"
        part.locate(bd.Location((90.0, -14.0, 6.0)))
        ax_k = bd.Axis((90.0, -14.0, 6.0), (0.0, 0.0, 1.0))
        bd.RevoluteJoint("K", ground, axis=ax_k, angular_range=(-180.0, 180.0))
        bd.RigidJoint("K", part, joint_location=ax_k.location)
        children.append(part)
    asm = bd.Compound(children=children, label="fourbar")
    a90 = 90.0 if zero_at_x else 30.0
    a60 = 60.0 if zero_at_x else 0.0
    a120 = 120.0 if zero_at_x else 60.0
    mechanism_state(asm, "crank90", A=a90)
    configuration_sequence(asm, "sweep", sweep={"A": (a60, a120)}, duration=2.0, ease="linear")
    return asm, fb0


def _make_liftlid() -> tuple[bd.Compound, float]:
    h = (0.0, 0.0, 0.0)
    b0 = (CYL_BASE[0], 0.0, CYL_BASE[1])
    r0 = (LID_R, 0.0, 0.0)
    u0 = np.array(r0) - np.array(b0)
    l0 = float(np.linalg.norm(u0))
    u0 = u0 / l0
    ny = (0.0, -1.0, 0.0)

    cabinet = bd.Box(600.0, 400.0, 500.0)
    cabinet.label = "cabinet"
    cabinet.locate(bd.Location((300.0, 0.0, -250.0)))
    lid = bd.Box(600.0, 400.0, 20.0)
    lid.label = "lid"
    lid.locate(bd.Location((300.0, 0.0, 10.0)))
    cyl = bd.Box(40.0, 40.0, 40.0)
    cyl.label = "cyl"
    cyl.locate(bd.Location(b0))
    rod = bd.Box(40.0, 40.0, 40.0)
    rod.label = "rod"
    rod.locate(bd.Location(r0))

    for label, parent, child, pt, lim in (
        ("hinge", cabinet, lid, h, (0.0, 90.0)),
        ("base", cabinet, cyl, b0, (-90.0, 90.0)),
        ("rodend", rod, lid, r0, (-90.0, 90.0)),
    ):
        ax = bd.Axis(pt, ny)
        bd.RevoluteJoint(label, parent, axis=ax, angular_range=lim)
        bd.RigidJoint(label, child, joint_location=ax.location)

    slide_ax = bd.Axis(b0, tuple(float(v) for v in u0))
    bd.LinearJoint("slide", cyl, axis=slide_ax, linear_range=(0.0, 300.0))
    bd.RigidJoint("slide", rod, joint_location=bd.Location(b0))

    asm = bd.Compound(children=[cabinet, lid, cyl, rod], label="liftlid")
    mechanism_state(asm, "extended", slide=150.0)
    configuration_sequence(asm, "extend", sweep={"slide": (0.0, 150.0)}, duration=2.0, ease="linear")
    return asm, l0


def _structure_of(asm: bd.Shape) -> dict[str, object]:
    tree = document.collect(asm, asm.label or "scene")
    return document.tree_structure(tree, None)


def _link_deltas(kinematics: dict, values: dict[str, tuple[float, ...]]) -> dict[str, np.ndarray]:
    now = mechanism.LinkWorlds(kinematics)(values)
    rest = mechanism.LinkWorlds(kinematics)(
        {k: mechanism._as_tuple(v) for k, v in kinematics["asBuilt"].items()}
    )
    return {link["name"]: a @ np.linalg.inv(r) for link, a, r in zip(kinematics["links"], now, rest)}


class LoopPoseTest(unittest.TestCase):
    def test_fourbar_crank_poses_match_closed_form_and_close_pin_c(self) -> None:
        asm, fb0 = _make_fourbar(60.0, zero_at_x=True)
        structure = _structure_of(asm)
        kin = structure["kinematics"]
        self.assertIsNotNone(kin["loop"])
        self.assertEqual(kin["warnings"], [])
        self.assertIn("sweep", kin["motions"])

        c0 = np.array([fb0["C"][0], fb0["C"][1], 0.0, 1.0])
        for theta in (60.0, 90.0, 120.0):
            ref = _fourbar_closed_form(theta)
            assert ref is not None
            values, _ = mechanism.resolve(kin, {"A": theta})
            deltas = _link_deltas(kin, values)

            door_rot = math.atan2(deltas["door"][1, 0], deltas["door"][0, 0])
            rocker_rot = math.atan2(deltas["rocker"][1, 0], deltas["rocker"][0, 0])
            want_door = math.radians(ref["coupler_deg"] - fb0["coupler_deg"])
            want_rocker = math.radians(ref["rocker_deg"] - fb0["rocker_deg"])
            self.assertAlmostEqual(door_rot, want_door, delta=1e-6, msg=f"coupler at theta={theta}")
            self.assertAlmostEqual(rocker_rot, want_rocker, delta=1e-6, msg=f"rocker at theta={theta}")

            c_on_door = (deltas["door"] @ c0)[:3]
            c_on_rocker = (deltas["rocker"] @ c0)[:3]
            c_want = np.array([ref["C"][0], ref["C"][1], 0.0])
            self.assertLess(float(np.linalg.norm(c_on_door - c_on_rocker)), mechanism.LINEAR_TOLERANCE)
            np.testing.assert_allclose(c_on_door, c_want, atol=mechanism.LINEAR_TOLERANCE)
            np.testing.assert_allclose(c_on_rocker, c_want, atol=mechanism.LINEAR_TOLERANCE)

        # Named state "crank90" resolves to the same pose as {"A": 90.0}.
        state_vals, named = mechanism.resolve(kin, "crank90")
        self.assertEqual(named, "crank90")
        direct_vals, _ = mechanism.resolve(kin, {"A": 90.0})
        for key in direct_vals:
            np.testing.assert_allclose(state_vals[key], direct_vals[key], atol=1e-6)

    def test_lift_lid_prismatic_drive_matches_closed_form(self) -> None:
        asm, l0 = _make_liftlid()
        structure = _structure_of(asm)
        kin = structure["kinematics"]
        self.assertIsNotNone(kin["loop"])
        self.assertEqual(kin["warnings"], [])
        self.assertIn("extend", kin["motions"])

        r0 = np.array([LID_R, 0.0, 0.0, 1.0])
        for stroke in (80.0, 150.0):
            phi_want = _lid_angle_from_len(l0 + stroke)
            alpha_want = _cyl_dir_deg(phi_want) - _cyl_dir_deg(0.0)
            if stroke == 150.0:
                self.assertAlmostEqual(phi_want, 31.59836224985225, places=6)

            values, _ = mechanism.resolve(kin, {"slide": stroke})
            deltas = _link_deltas(kin, values)
            lid_deg = math.degrees(math.atan2(deltas["lid"][2, 0], deltas["lid"][0, 0]))
            cyl_deg = math.degrees(math.atan2(deltas["cyl"][2, 0], deltas["cyl"][0, 0]))
            self.assertAlmostEqual( math.radians(lid_deg), math.radians(phi_want), delta=1e-6 )
            self.assertAlmostEqual( math.radians(cyl_deg), math.radians(alpha_want), delta=1e-6 )

            r_on_lid = (deltas["lid"] @ r0)[:3]
            r_on_rod = (deltas["rod"] @ r0)[:3]
            self.assertLess(float(np.linalg.norm(r_on_lid - r_on_rod)), mechanism.LINEAR_TOLERANCE)

        # Driving the lid hinge revolute solves the prismatic actuator stroke.
        target_phi = 25.0
        _, want_len = _lid_pin_and_len(target_phi)
        vals_from_hinge, _ = mechanism.resolve(kin, {"hinge": target_phi})
        self.assertAlmostEqual(vals_from_hinge["slide"][0], want_len - l0, delta=1e-4)

    def test_unclosable_drive_and_overconstrained_pose_fail_closed(self) -> None:
        # Non-Grashof four-bar: L1=100, L2=40, L3=50, L4=45. At theta=60 it builds,
        # but at theta=170 |B - D| > L3 + L4 = 95, so the loop cannot close.
        asm, _ = _make_fourbar(theta0=60.0, l1=100.0, l2=40.0, l3=50.0, l4=45.0, zero_at_x=True)
        structure = _structure_of(asm)
        kin = structure["kinematics"]

        with self.assertRaises(mechanism.KinematicsError) as ctx:
            mechanism.resolve(kin, {"A": 170.0})
        msg = str(ctx.exception)
        self.assertIn("loop", msg.lower())
        for label in kin["loop"]:
            self.assertIn(label, msg)
        self.assertIn("170", msg)

        # Over-constrained pose that does not close the loop is refused.
        asm_g, fb0 = _make_fourbar(60.0, zero_at_x=True)
        kin_g = _structure_of(asm_g)["kinematics"]
        with self.assertRaises(mechanism.KinematicsError) as over_ctx:
            mechanism.resolve(kin_g, {"A": 90.0, "D": 0.0})
        self.assertIn("loop", str(over_ctx.exception).lower())

        # Over-constrained pose that DOES close the loop is accepted.
        fb90 = _fourbar_closed_form(90.0)
        assert fb90 is not None
        d90 = fb90["rocker_deg"] - fb0["rocker_deg"]
        closed_vals, _ = mechanism.resolve(kin_g, {"A": 90.0, "D": d90})
        self.assertAlmostEqual(closed_vals["A"][0], 90.0, places=6)
        self.assertAlmostEqual(closed_vals["D"][0], d90, places=5)

    def test_crank_sweep_30_steps_stays_on_branch_without_flip(self) -> None:
        asm, fb0 = _make_fourbar(60.0, zero_at_x=True)
        structure = _structure_of(asm)
        kin = structure["kinematics"]
        c0 = np.array([fb0["C"][0], fb0["C"][1], 0.0, 1.0])

        timeline = mechanism.Timeline(kin, "sweep")
        prev_c = None
        for t in np.linspace(0.0, timeline.duration, 30):
            vals = timeline.values(float(t))
            theta = vals["A"][0]
            ref = _fourbar_closed_form(theta)
            assert ref is not None
            deltas = _link_deltas(kin, vals)
            c_now = (deltas["door"] @ c0)[:3]
            self.assertGreater(c_now[1], 0.0, f"branch flipped to lower half-plane at t={t}, theta={theta}")
            np.testing.assert_allclose(c_now[:2], ref["C"], atol=mechanism.LINEAR_TOLERANCE)
            if prev_c is not None:
                self.assertLess(float(np.linalg.norm(c_now - prev_c)), 5.0, "discontinuous jump in sweep")
            prev_c = c_now

    def test_driving_rocker_solves_crank_and_coupler(self) -> None:
        asm, fb0 = _make_fourbar(60.0, zero_at_x=True)
        kin = _structure_of(asm)["kinematics"]
        fb90 = _fourbar_closed_form(90.0)
        assert fb90 is not None
        d_target = fb90["rocker_deg"] - fb0["rocker_deg"]

        vals, _ = mechanism.resolve(kin, {"D": d_target})
        self.assertAlmostEqual(vals["A"][0], 90.0, delta=math.degrees(1e-6))
        deltas = _link_deltas(kin, vals)
        door_rot = math.atan2(deltas["door"][1, 0], deltas["door"][0, 0])
        want_door = math.radians(fb90["coupler_deg"] - fb0["coupler_deg"])
        self.assertAlmostEqual(door_rot, want_door, delta=1e-6)

    def test_two_loops_sharing_a_link_solve_together(self) -> None:
        # Two identical four-bar loops in series sharing the intermediate link (`mid` is
        # the rocker of loop 1 around D and also drives loop 2 around D with a second ground pin E).
        theta0 = 60.0
        fb0 = _fourbar_closed_form(theta0)
        assert fb0 is not None
        a1, b1, c1, d1 = (0.0, 0.0), fb0["B"], fb0["C"], (GROUND, 0.0)
        # Second four-bar translated by +D1 so its crank IS `rocker1` (or driven by a shared link)
        # Wait: let's make `mid` be both the rocker of loop 1 (pinned at D1=(60,0) to C1) and carry
        # pin B2 = (d1[0] + b1[0], d1[1] + b1[1]), driving coupler2 (B2->C2) and rocker2 (D2->C2)!
        # At theta0=60, mid rotates by delta_D; so B2 rotates around D1 by delta_D, meaning loop 2's
        # effective crank angle is 60 + delta_D!
        b2 = (d1[0] + b1[0], d1[1] + b1[1])
        c2 = (d1[0] + c1[0], d1[1] + c1[1])
        d2 = (d1[0] + GROUND, 0.0)

        ground = bd.Box(200.0, 12.0, 6.0)
        ground.label = "ground"
        ground.locate(bd.Location((60.0, -14.0, 0.0)))
        crank1 = _bar(a1, b1, 8.0, "crank1")
        door1 = _bar(b1, c1, 16.0, "door1")
        mid = _bar(d1, c1, 24.0, "mid")
        door2 = _bar(b2, c2, 32.0, "door2")
        rocker2 = _bar(d2, c2, 40.0, "rocker2")

        ax_a = bd.Axis((*a1, 0.0), (0.0, 0.0, 1.0))
        bd.RevoluteJoint("A1", ground, axis=ax_a, angular_range=(-180.0, 180.0))
        bd.RigidJoint("A1", crank1, joint_location=ax_a.location * bd.Rot(0.0, 0.0, theta0))
        for label, parent, child, pt in (
            ("B1", crank1, door1, b1),
            ("D1", ground, mid, d1),
            ("C1", mid, door1, c1),
            ("B2", mid, door2, b2),
            ("D2", ground, rocker2, d2),
            ("C2", rocker2, door2, c2),
        ):
            ax = bd.Axis((*pt, 0.0), (0.0, 0.0, 1.0))
            bd.RevoluteJoint(label, parent, axis=ax, angular_range=(-180.0, 180.0))
            bd.RigidJoint(label, child, joint_location=ax.location)

        asm = bd.Compound(children=[ground, crank1, door1, mid, door2, rocker2], label="sixbar")
        kin = _structure_of(asm)["kinematics"]
        vals, _ = mechanism.resolve(kin, {"A1": 90.0})
        deltas = _link_deltas(kin, vals)

        fb1 = _fourbar_closed_form(90.0)
        assert fb1 is not None
        delta_mid = fb1["rocker_deg"] - fb0["rocker_deg"]
        fb2 = _fourbar_closed_form(60.0 + delta_mid)
        assert fb2 is not None
        delta_rocker2 = fb2["rocker_deg"] - fb0["rocker_deg"]

        mid_rot = math.degrees(math.atan2(deltas["mid"][1, 0], deltas["mid"][0, 0]))
        rocker2_rot = math.degrees(math.atan2(deltas["rocker2"][1, 0], deltas["rocker2"][0, 0]))
        self.assertAlmostEqual(math.radians(mid_rot), math.radians(delta_mid), delta=1e-6)
        self.assertAlmostEqual(math.radians(rocker2_rot), math.radians(delta_rocker2), delta=1e-6)

    def test_free_joint_at_and_beyond_limit(self) -> None:
        fb60 = _fourbar_closed_form(60.0)
        fb90 = _fourbar_closed_form(90.0)
        assert fb60 is not None and fb90 is not None
        d_at_90 = fb90["rocker_deg"] - fb60["rocker_deg"]  # ~ +11.618 deg
        # Limit D to [-20.0, d_at_90]: at A=90, D sits right on its upper bound; at A=110, D would exceed it.
        asm, _ = _make_fourbar(60.0, d_limits=(-20.0, d_at_90), zero_at_x=True)
        kin = _structure_of(asm)["kinematics"]

        vals_on_limit, _ = mechanism.resolve(kin, {"A": 90.0})
        self.assertAlmostEqual(vals_on_limit["D"][0], d_at_90, delta=1e-4)

        with self.assertRaises(mechanism.KinematicsError):
            mechanism.resolve(kin, {"A": 110.0})

    def test_posed_and_open_tree_consumers(self) -> None:
        asm, fb0 = _make_fourbar(60.0, zero_at_x=True)
        moved = posed(asm, {"A": 90.0})
        tree = document.collect(moved, "fourbar")
        assert tree.mechanism is not None
        fb90 = _fourbar_closed_form(90.0)
        assert fb90 is not None
        self.assertAlmostEqual(tree.mechanism.as_built["A"][0], 90.0, places=4)
        self.assertAlmostEqual(
            tree.mechanism.as_built["D"][0],
            fb90["rocker_deg"] - fb0["rocker_deg"],
            places=4,
        )

        # posed_structure and direct LinkWorlds call on relative-zero four-bar (as in loops.py).
        asm_rel, _ = _make_fourbar(60.0, zero_at_x=False)
        struct_rel = _structure_of(asm_rel)
        kin_rel = struct_rel["kinematics"]
        posed_st = mechanism.posed_structure(struct_rel, {"A": 30.0})
        self.assertAlmostEqual(posed_st["kinematics"]["pose"]["values"]["D"],
                               fb90["rocker_deg"] - fb0["rocker_deg"], places=4)
        asb = {k: mechanism._as_tuple(v) for k, v in kin_rel["asBuilt"].items()}
        direct_deltas = _link_deltas(kin_rel, {**asb, "A": (30.0,)})
        self.assertAlmostEqual(
            math.atan2(direct_deltas["door"][1, 0], direct_deltas["door"][0, 0]),
            math.radians(fb90["coupler_deg"] - fb0["coupler_deg"]),
            delta=1e-6,
        )

        # Open-tree mechanism takes the open-tree path with bit-identical output.
        base = bd.Box(40.0, 20.0, 10.0)
        base.label = "base"
        arm = bd.Box(30.0, 10.0, 8.0)
        arm.label = "arm"
        arm.locate(bd.Location((20.0, 0.0, 10.0)))
        ax = bd.Axis((10.0, 0.0, 10.0), (0.0, 1.0, 0.0))
        bd.RevoluteJoint("elbow", base, axis=ax, angular_range=(-90.0, 90.0))
        bd.RigidJoint("elbow", arm, joint_location=ax.location)
        open_asm = bd.Compound(children=[base, arm], label="open_arm")
        open_kin = _structure_of(open_asm)["kinematics"]
        self.assertIsNone(open_kin["loop"])
        open_worlds = mechanism.LinkWorlds(open_kin)
        self.assertEqual(open_worlds.closers, [])
        vals_open, _ = mechanism.resolve(open_kin, {"elbow": 45.0})
        for got, want in zip(open_worlds(vals_open), open_worlds._place(vals_open), strict=True):
            self.assertTrue(np.array_equal(got, want))

    def test_parallelogram_stays_on_parallel_branch_and_refuses_change_point(self) -> None:
        # Parallelogram 60/25/60/25 built at crank angle 60 deg (relative joint angles 0 at build).
        asm, _ = _make_fourbar(theta0=60.0, l1=60.0, l2=25.0, l3=60.0, l4=25.0, zero_at_x=False)
        configuration_sequence(asm, "safe_fold", sweep={"A": (0.0, -58.0)}, duration=2.0, ease="linear")
        configuration_sequence(asm, "fold", sweep={"A": (0.0, -120.0)}, duration=2.0, ease="linear")
        structure = _structure_of(asm)
        kin = structure["kinematics"]

        # Fresh single pose at A=-58 deg (crank at +2 deg, 2 deg before the folded change point):
        # must stay on the parallel branch (D=-58, coupler rotation 0).
        vals58, _ = mechanism.resolve(kin, {"A": -58.0})
        self.assertAlmostEqual(vals58["D"][0], -58.0, delta=math.degrees(1e-6))
        self.assertAlmostEqual(vals58["B"][0] - kin["asBuilt"]["B"], 58.0, delta=math.degrees(1e-6))
        self.assertAlmostEqual(vals58["C"][0] - kin["asBuilt"]["C"], 58.0, delta=math.degrees(1e-6))
        deltas58 = _link_deltas(kin, vals58)
        door_rot58 = math.atan2(deltas58["door"][1, 0], deltas58["door"][0, 0])
        self.assertAlmostEqual(door_rot58, 0.0, delta=1e-6)

        st58 = mechanism.posed_structure(structure, {"A": -58.0})
        self.assertAlmostEqual(st58["kinematics"]["pose"]["values"]["D"], -58.0, delta=math.degrees(1e-6))

        # 2 deg sweep from crank 60 deg (A=0) toward -60 deg (A=-120):
        # every sample before the change point (A > -60) stays on the parallel branch (D == A),
        # and at/past the change point (A <= -60, and stretched change point A >= 120) refuses.
        for a_rel in range(0, -60, -2):
            v, _ = mechanism.resolve(kin, {"A": float(a_rel)})
            self.assertAlmostEqual(v["D"][0], float(a_rel), delta=math.degrees(1e-6), msg=f"a_rel={a_rel}")
            d_links = _link_deltas(kin, v)
            self.assertAlmostEqual(
                math.atan2(d_links["door"][1, 0], d_links["door"][0, 0]),
                0.0,
                delta=1e-6,
                msg=f"coupler rotated at a_rel={a_rel}",
            )

        for singular_or_past in (-60.0, -62.0, -90.0, -120.0, 120.0, 130.0):
            with self.assertRaises(mechanism.KinematicsError, msg=f"must refuse A={singular_or_past}") as ctx:
                mechanism.resolve(kin, {"A": singular_or_past})
            self.assertIn("C", str(ctx.exception))

        # Timeline sweep from 0 to -58 stays on the parallel branch; sweep to -120 refuses.
        tl = mechanism.Timeline(kin, "safe_fold")
        for k in range(30):
            a_expect = -2.0 * k
            t = (a_expect / -58.0) * tl.duration
            vt = tl.values(t)
            self.assertAlmostEqual(vt["D"][0], a_expect, delta=math.degrees(1e-6))
        with self.assertRaises(mechanism.KinematicsError):
            mechanism.Timeline(kin, "fold")

    def test_explicit_seed_and_call_order_invariance(self) -> None:
        # Parallelogram 60/25/60/25 built at 60 deg: A=-90 is past the change point and must fail
        # whether called fresh, after a sweep, or after posed_structure; A=-58 must give D=-58 in all orders.
        asm_par, _ = _make_fourbar(theta0=60.0, l1=60.0, l2=25.0, l3=60.0, l4=25.0, zero_at_x=False)
        struct_par = _structure_of(asm_par)
        kin_par = struct_par["kinematics"]

        # 1. Fresh call
        fresh_58, _ = mechanism.resolve(kin_par, {"A": -58.0})
        with self.assertRaises(mechanism.KinematicsError):
            mechanism.resolve(kin_par, {"A": -90.0})

        # 2. After a step-by-step sweep
        for a_rel in range(0, -60, -2):
            mechanism.resolve(kin_par, {"A": float(a_rel)})
        after_sweep_58, _ = mechanism.resolve(kin_par, {"A": -58.0})
        with self.assertRaises(mechanism.KinematicsError):
            mechanism.resolve(kin_par, {"A": -90.0})

        # 3. After posed_structure
        mechanism.posed_structure(struct_par, {"A": -30.0})
        after_posed_58, _ = mechanism.resolve(kin_par, {"A": -58.0})
        with self.assertRaises(mechanism.KinematicsError):
            mechanism.resolve(kin_par, {"A": -90.0})

        for key in fresh_58:
            np.testing.assert_allclose(after_sweep_58[key], fresh_58[key], atol=1e-9)
            np.testing.assert_allclose(after_posed_58[key], fresh_58[key], atol=1e-9)

        # Grashof crank-rocker (60/25/55/45 built at 60 deg, zero_at_x=False): A=-90 is reachable.
        # Fresh, after a sweep, and after posed_structure must return identical values.
        asm_g, _ = _make_fourbar(theta0=60.0, zero_at_x=False)
        struct_g = _structure_of(asm_g)
        kin_g = struct_g["kinematics"]
        g_fresh, _ = mechanism.resolve(kin_g, {"A": -90.0})
        for a_rel in range(0, -90, -10):
            mechanism.resolve(kin_g, {"A": float(a_rel)})
        g_after_sweep, _ = mechanism.resolve(kin_g, {"A": -90.0})
        mechanism.posed_structure(struct_g, {"A": -45.0})
        g_after_posed, _ = mechanism.resolve(kin_g, {"A": -90.0})
        for key in g_fresh:
            np.testing.assert_allclose(g_after_sweep[key], g_fresh[key], atol=1e-7)
            np.testing.assert_allclose(g_after_posed[key], g_fresh[key], atol=1e-7)

        # Explicit seed on the anti-parallelogram branch at A=-58 stays on anti-parallelogram for that
        # call only, and never leaks into subsequent default-seeded resolve or posed_instances calls.
        b_pt = (25.0 * math.cos(math.radians(2.0)), 25.0 * math.sin(math.radians(2.0)))
        sols_2 = _circle_intersections(b_pt, 60.0, (60.0, 0.0), 25.0)
        assert sols_2 is not None
        c_anti = min(sols_2, key=lambda pt: pt[1])
        coupler_anti = math.degrees(math.atan2(c_anti[1] - b_pt[1], c_anti[0] - b_pt[0]))
        rocker_anti = math.degrees(math.atan2(c_anti[1], c_anti[0] - 60.0))
        anti_ang = rocker_anti - 60.0
        anti_seed = {
            "A": (-58.0,),
            "B": (float(kin_par["asBuilt"]["B"]) + coupler_anti - (-58.0),),
            "C": (float(kin_par["asBuilt"]["C"]) + coupler_anti - anti_ang,),
            "D": (anti_ang,),
        }
        anti_vals, _ = mechanism.resolve(kin_par, {"A": -58.0}, seed=anti_seed)
        self.assertAlmostEqual(anti_vals["D"][0], anti_ang, delta=1e-4)

        # Subsequent resolve without explicit seed must still seed from as_built (parallel branch D=-58).
        clean_after_anti, _ = mechanism.resolve(kin_par, {"A": -58.0})
        self.assertAlmostEqual(clean_after_anti["D"][0], -58.0, delta=math.degrees(1e-6))

        # Reference solve in posed_instances must never write to any seed used by resolve.
        as_built_par = {k: mechanism._as_tuple(v) for k, v in kin_par["asBuilt"].items()}
        kin_par["pose"] = {"values": {k: v[0] for k, v in anti_seed.items()}, "configuration": None}
        try:
            mechanism.posed_instances(struct_par, as_built_par)
        finally:
            kin_par.pop("pose", None)
        clean_after_ref, _ = mechanism.resolve(kin_par, {"A": -58.0})
        self.assertAlmostEqual(clean_after_ref["D"][0], -58.0, delta=math.degrees(1e-6))


def _closes(kinematics: dict, values: dict[str, tuple[float, ...]]) -> bool:
    """Every closing pair of the loop meets within the tolerances at ``values``."""
    worlds = mechanism.LinkWorlds(kinematics)
    _r, _w, pos, ang = worlds._residuals({**worlds.as_built, **values})
    return pos <= mechanism.LINEAR_TOLERANCE and ang <= mechanism.ANGULAR_TOLERANCE


class LoopRound3Test(unittest.TestCase):
    """R7 couplings in an over-constrained pose, R8 round trip and determinism, R9 motions."""

    def test_r7_coupling_in_loop_poses_or_refuses_typed(self) -> None:
        asm, _ = _make_fourbar(theta0=60.0, zero_at_x=False)
        gear_pair(asm, "h", A=1, D=1)
        structure = _structure_of(asm)
        kin = structure["kinematics"]
        as_built = {k: mechanism._as_tuple(v) for k, v in kin["asBuilt"].items()}
        # h=0 is the as-built pose: it poses, as built.
        zero, _ = mechanism.resolve(kin, {"h": 0})
        for label, value in as_built.items():
            np.testing.assert_allclose(zero[label], value, atol=1e-9)
        mechanism.posed_structure(structure, {"h": 0})
        # Any other value: a closed pose, or a typed refusal naming the loop. Never a raw error.
        for value in (10.0, -10.0, 3.0, 45.0):
            for call in (lambda v=value: mechanism.resolve(kin, {"h": v})[0],
                         lambda v=value: mechanism.posed_structure(structure, {"h": v})["kinematics"]["pose"]["values"]):
                try:
                    got = call()
                except mechanism.KinematicsError as error:
                    self.assertIn("close a loop", str(error))
                    continue
                got = {k: mechanism._as_tuple(v) for k, v in got.items()}
                self.assertTrue(_closes(kin, got), msg=f"h={value} returned an open loop")

    def test_r7_coupling_outside_loop_with_loop_joint(self) -> None:
        asm, _ = _make_fourbar(theta0=60.0, zero_at_x=False, knob=True)
        gear_pair(asm, "g", K=1, A=-2)
        structure = _structure_of(asm)
        kin = structure["kinematics"]
        self.assertNotIn("K", kin["loop"])
        alone, _ = mechanism.resolve(kin, {"g": 10})
        both, _ = mechanism.resolve(kin, {"g": 10, "D": alone["D"][0]})
        for label in alone:
            np.testing.assert_allclose(both[label], alone[label], atol=1e-7)
        for pose in ({"g": 10, "D": alone["D"][0] + 20.0}, {"D": alone["D"][0] - 15.0, "g": 10}):
            with self.assertRaises(mechanism.KinematicsError) as ctx:
                mechanism.resolve(kin, pose)
            self.assertIn("close a loop", str(ctx.exception))
            with self.assertRaises(mechanism.KinematicsError):
                mechanism.posed_structure(structure, pose)

    def test_r8_solver_values_fed_back_are_accepted(self) -> None:
        for zero_at_x in (False, True):
            asm, _ = _make_fourbar(theta0=60.0, zero_at_x=zero_at_x)
            kin = _structure_of(asm)["kinematics"]
            base = float(kin["asBuilt"]["A"])
            for offset in (-120.0, -90.0, -60.0, 30.0, 120.0):
                drive = base + offset
                if not -180.0 <= drive <= 180.0:
                    continue
                solved, _ = mechanism.resolve(kin, {"A": drive})
                flat = {label: value[0] for label, value in solved.items()}
                for pose in ({"A": flat["A"], "D": flat["D"]}, {"D": flat["D"], "A": flat["A"]},
                             {"A": flat["A"], "B": flat["B"]}, {"C": flat["C"], "A": flat["A"]}, dict(flat)):
                    again, _ = mechanism.resolve(kin, pose)
                    for label in solved:
                        np.testing.assert_allclose(again[label], solved[label], atol=1e-7,
                                                   err_msg=f"zero_at_x={zero_at_x} A={drive} pose={sorted(pose)}")

    def test_r8_timeline_values_pure_function_of_t(self) -> None:
        asm, _ = _make_fourbar(theta0=60.0, zero_at_x=False)
        configuration_sequence(asm, "swing", [(0.0, {"A": 0.0}), (1.0, {"A": -150.0}), (2.0, {"A": -40.0}),
                                              (3.0, {"A": 100.0})], ease="linear")
        kin = _structure_of(asm)["kinematics"]
        times = [3.0 * k / 60 for k in range(61)]
        sequential = mechanism.Timeline(kin, "swing")
        forward = {t: sequential.values(t) for t in times}
        reverse = mechanism.Timeline(kin, "swing")
        backward = {t: reverse.values(t) for t in reversed(times)}
        for t in times:
            self.assertEqual(forward[t], backward[t], msg=f"t={t}")
            self.assertEqual(forward[t], mechanism.Timeline(kin, "swing").values(t), msg=f"fresh t={t}")
            self.assertEqual(forward[t], sequential.values(t), msg=f"again t={t}")

    def test_r9_unassemblable_loop_motion_left_out_and_warned(self) -> None:
        asm, _ = _make_fourbar(theta0=60.0, l1=60.0, l2=25.0, l3=60.0, l4=25.0, zero_at_x=False)
        configuration_sequence(asm, "cross", sweep={"A": (-30.0, -100.0)}, duration=2.0, ease="linear")
        configuration_sequence(asm, "safe", sweep={"A": (0.0, -40.0)}, duration=2.0, ease="linear")
        kin = _structure_of(asm)["kinematics"]
        self.assertNotIn("cross", kin["motions"])
        self.assertIn("safe", kin["motions"])
        loop = ", ".join(kin["loop"])
        said = [w for w in kin["warnings"] if "'cross'" in w]
        self.assertEqual(len(said), 1, msg=kin["warnings"])
        self.assertIn(loop, said[0])
        self.assertIn("A=-100", said[0])
        self.assertIn("left out", said[0])
        for name in kin["motions"]:
            timeline = mechanism.Timeline(kin, name)
            for k in range(11):
                self.assertTrue(_closes(kin, timeline.values(timeline.duration * k / 10)))


if __name__ == "__main__":
    unittest.main()
