"""MoP fork: GLB export writes face-less wire/edge leaves as glTF LINE_STRIP primitives."""
from __future__ import annotations

import json
import math
import struct
import tempfile
import textwrap
import unittest
from pathlib import Path

import build123d as bd
import numpy as np

from cad.engine import build as engine_build
from cad.engine import document, export
from cad.engine.export import glb as glb_export
from cad.engine.export.mesh import groups, meshes, shaded

MIXED_SOURCE = '''
import build123d as bd
from cad import glb

@glb("mixed.glb")
def mixed():
    block = bd.Box(20, 10, 5)
    block.label = "block"
    w1 = bd.Wire([*bd.Polyline((0.0, 0.0, 0.0), (15.0, 0.0, 0.0), (15.0, 10.0, 0.0)).edges()])
    w1.label = "w1"
    w2 = bd.Wire([*bd.Polyline(
        (0.0, 0.0, 0.0), (40.0, 0.0, 0.0), (40.0, 20.0, 0.0), (0.0, 20.0, 0.0), (0.0, 0.0, 0.0)
    ).edges()])
    w2.label = "w2"
    outlines = bd.Compound(label="outlines", children=[w1, w2])
    return bd.Compound(label="mixed", children=[block, outlines])
'''


def _read_glb(path: Path) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    magic, version, total = struct.unpack_from("<III", raw, 0)
    assert magic == 0x46546C67 and version == 2 and total == len(raw)
    json_len, json_type = struct.unpack_from("<II", raw, 12)
    assert json_type == 0x4E4F534A
    gltf = json.loads(raw[20 : 20 + json_len])
    bin_offset = 20 + json_len
    bin_len, bin_type = struct.unpack_from("<II", raw, bin_offset)
    assert bin_type == 0x004E4942
    blob = raw[bin_offset + 8 : bin_offset + 8 + bin_len]
    return gltf, blob


def _read_vec3_f32(gltf: dict, blob: bytes, accessor_index: int) -> np.ndarray:
    acc = gltf["accessors"][accessor_index]
    assert acc["type"] == "VEC3" and acc["componentType"] == 5126
    assert "min" in acc and "max" in acc
    bv = gltf["bufferViews"][acc["bufferView"]]
    offset = bv.get("byteOffset", 0)
    arr = np.frombuffer(blob, dtype="<f4", count=acc["count"] * 3, offset=offset)
    return arr.reshape(-1, 3).copy()


def _export_shape_glb(shape: bd.Shape, name: str, path: Path) -> export.View:
    tree = document.collect(shape, name)
    for part in tree.parts:
        part.key = document.geometry_key(part.shape)
    document.merge(tree)
    doc = document.to_xcaf(tree)
    source = export.Source.built(doc, tree, None)
    view = source.view()
    glb_export.write(view, path)
    return view


def _unpatched_triangle_glb_bytes(view: export.View) -> bytes:
    """Independent reference encoding of the unpatched triangle-only GLB layout."""
    held = {index: shaded(mesh) for index, mesh in meshes(view).items()}
    blob = bytearray()
    views: list[dict] = []
    accessors: list[dict] = []

    def add(array: np.ndarray, target: int, kind: str, component: int) -> int:
        while len(blob) % 4:
            blob.append(0)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": array.nbytes, "target": target})
        blob.extend(array.tobytes())
        entry = {
            "bufferView": len(views) - 1,
            "componentType": component,
            "count": int(array.shape[0]),
            "type": kind,
        }
        if kind == "VEC3":
            entry["min"] = array.min(axis=0).tolist()
            entry["max"] = array.max(axis=0).tolist()
        accessors.append(entry)
        return len(accessors) - 1

    materials: list[dict] = []
    material_at: dict[tuple, int] = {}
    gltf_meshes: list[dict] = []
    shared: dict[int, tuple[int, int, dict]] = {}
    mesh_at: dict[tuple, int] = {}

    for placed in view.placements:
        idx = placed.prototype
        mesh = held[idx]
        if not mesh.triangles:
            continue
        proto = view.prototypes[idx]
        proto_color = tuple(proto["color"]) if proto.get("color") is not None else None
        color = placed.color if placed.color is not None and placed.color != proto_color else None
        mkey = (idx, color)
        if mkey not in mesh_at:
            if idx not in shared:
                shared[idx] = (
                    add(np.ascontiguousarray(mesh.positions, "<f4"), 34962, "VEC3", 5126),
                    add(np.ascontiguousarray(mesh.normals, "<f4"), 34962, "VEC3", 5126),
                    {},
                )
            pos_acc, nrm_acc, spans = shared[idx]
            prims = []
            for look, indices in groups(mesh, proto, color):
                rgba, _ = look
                lkey = (look, None)
                if lkey not in material_at:
                    materials.append({
                        "pbrMetallicRoughness": {
                            "baseColorFactor": list(rgba),
                            "metallicFactor": 0.0,
                            "roughnessFactor": 0.6,
                        },
                        "doubleSided": False,
                        "alphaMode": "BLEND" if rgba[3] < 1.0 else "OPAQUE",
                    })
                    material_at[lkey] = len(materials) - 1
                span = indices.tobytes()
                if span not in spans:
                    spans[span] = add(np.ascontiguousarray(indices, "<u2"), 34963, "SCALAR", 5123)
                prims.append({
                    "attributes": {"POSITION": pos_acc, "NORMAL": nrm_acc},
                    "indices": spans[span],
                    "material": material_at[lkey],
                })
            gltf_meshes.append({"name": proto["name"], "primitives": prims})
            mesh_at[mkey] = len(gltf_meshes) - 1

    nodes: list[dict] = [{
        "name": view.name,
        "rotation": [-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)],
        "scale": [0.001, 0.001, 0.001],
        "children": [],
    }]
    for placed in view.placements:
        proto = view.prototypes[placed.prototype]
        proto_color = tuple(proto["color"]) if proto.get("color") is not None else None
        color = placed.color if placed.color is not None and placed.color != proto_color else None
        m_idx = mesh_at.get((placed.prototype, color))
        if m_idx is None:
            continue
        nodes.append({
            "name": "/".join(placed.path[1:]) or placed.path[-1],
            "mesh": m_idx,
            "matrix": [float(v) for v in placed.matrix.T.ravel()],
        })
        nodes[0]["children"].append(len(nodes) - 1)

    doc_json = {
        "asset": {"version": "2.0", "generator": "cadgen engine"},
        "scene": 0,
        "scenes": [{"name": view.name, "nodes": [0]}],
        "nodes": nodes,
        "meshes": gltf_meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    text = json.dumps(doc_json, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    while len(blob) % 4:
        blob.append(0)
    return b"".join([
        struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(text) + 8 + len(blob)),
        struct.pack("<II", len(text), 0x4E4F534A),
        text,
        struct.pack("<II", len(blob), 0x004E4942),
        bytes(blob),
    ])


class GlbLinesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="glb-lines-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_mixed_box_and_wires_nodes_and_primitives(self) -> None:
        script = self.folder / "mixed.py"
        script.write_text(textwrap.dedent(MIXED_SOURCE), encoding="utf-8")
        reports = engine_build.build(script)
        self.assertTrue(reports[0]["ok"], reports)
        gltf, blob = _read_glb(self.folder / "mixed.glb")

        by_name = {node["name"]: node for node in gltf["nodes"][1:]}
        self.assertIn("block", by_name)
        self.assertIn("outlines/w1", by_name)
        self.assertIn("outlines/w2", by_name)

        box_mesh = gltf["meshes"][by_name["block"]["mesh"]]
        self.assertTrue(box_mesh["primitives"])
        for prim in box_mesh["primitives"]:
            self.assertEqual(prim.get("mode", 4), 4)

        for name, expected_count, closed in (("outlines/w1", 3, False), ("outlines/w2", 5, True)):
            mesh = gltf["meshes"][by_name[name]["mesh"]]
            self.assertEqual(len(mesh["primitives"]), 1)
            prim = mesh["primitives"][0]
            self.assertEqual(prim.get("mode"), 3)
            self.assertEqual(list(prim["attributes"].keys()), ["POSITION"])
            self.assertNotIn("indices", prim)
            self.assertNotIn("material", prim)
            pts = _read_vec3_f32(gltf, blob, prim["attributes"]["POSITION"])
            self.assertEqual(pts.shape, (expected_count, 3))
            if closed:
                np.testing.assert_allclose(pts[0], pts[-1], atol=1e-6)
            else:
                self.assertFalse(np.allclose(pts[0], pts[-1], atol=1e-4))

    def test_circular_arc_wire_points_lie_on_arc(self) -> None:
        radius = 50.0
        arc = bd.CenterArc((0.0, 0.0, 0.0), radius, 0.0, 90.0)
        wire = bd.Wire([arc])
        wire.label = "arc_wire"
        root = bd.Compound(label="arc_scene", children=[wire])
        glb_path = self.folder / "arc.glb"
        _export_shape_glb(root, "arc_scene", glb_path)
        gltf, blob = _read_glb(glb_path)

        by_name = {node["name"]: node for node in gltf["nodes"][1:]}
        self.assertIn("arc_wire", by_name)
        prim = gltf["meshes"][by_name["arc_wire"]["mesh"]]["primitives"][0]
        self.assertEqual(prim.get("mode"), 3)
        pts = _read_vec3_f32(gltf, blob, prim["attributes"]["POSITION"])
        self.assertGreater(len(pts), 2)
        radii = np.hypot(pts[:, 0], pts[:, 1])
        self.assertLessEqual(float(np.max(np.abs(radii - radius))), 0.05)
        self.assertLessEqual(float(np.max(np.abs(pts[:, 2]))), 0.05)
        self.assertGreaterEqual(float(np.min(pts[:, 0])), -0.05)
        self.assertGreaterEqual(float(np.min(pts[:, 1])), -0.05)

    def test_box_only_glb_bytes_equal_unpatched_path(self) -> None:
        a = bd.Box(10, 20, 30)
        a.label = "a"
        b = bd.Box(12, 8, 6).moved(bd.Location((50, 10, 0)))
        b.label = "b"
        root = bd.Compound(label="pair", children=[a, b])
        glb_path = self.folder / "pair.glb"
        view = _export_shape_glb(root, "pair", glb_path)
        self.assertEqual(glb_path.read_bytes(), _unpatched_triangle_glb_bytes(view))

    def test_placed_wire_world_positions(self) -> None:
        raw_pts = [(0.0, 0.0, 0.0), (15.0, 0.0, 0.0), (15.0, 10.0, 0.0)]
        shift = (10.0, 20.0, 30.0)
        block = bd.Box(10, 10, 10)
        block.label = "block"
        w1 = bd.Wire([*bd.Polyline(*raw_pts).edges()])
        w1.label = "w1"
        outlines = bd.Compound(label="outlines", children=[w1]).moved(bd.Location(shift))
        root = bd.Compound(label="placed_scene", children=[block, outlines])

        glb_path = self.folder / "placed.glb"
        _export_shape_glb(root, "placed_scene", glb_path)
        gltf, blob = _read_glb(glb_path)

        by_name = {node["name"]: node for node in gltf["nodes"][1:]}
        self.assertIn("outlines/w1", by_name)
        node = by_name["outlines/w1"]
        prim = gltf["meshes"][node["mesh"]]["primitives"][0]
        local_pts = _read_vec3_f32(gltf, blob, prim["attributes"]["POSITION"]).astype(np.float64)

        matrix = np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T
        scale = np.array(gltf["nodes"][0]["scale"], dtype=np.float64)
        world_m = (local_pts @ matrix[:3, :3].T + matrix[:3, 3]) * scale

        bd_verts_m = np.array([[v.X, v.Y, v.Z] for v in outlines.wires()[0].vertices()], dtype=np.float64) * 1e-3
        self.assertEqual(world_m.shape, bd_verts_m.shape)
        for pt in bd_verts_m:
            dists = np.linalg.norm(world_m - pt, axis=1)
            self.assertLessEqual(float(np.min(dists)), 1e-4)
        expected_ordered_m = (np.array(raw_pts, dtype=np.float64) + np.array(shift, dtype=np.float64)) * 1e-3
        np.testing.assert_allclose(world_m, expected_ordered_m, atol=1e-4)


if __name__ == "__main__":
    unittest.main()
