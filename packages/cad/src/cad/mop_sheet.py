"""MoP fork sheet components (061 T007): Table, Balloons, CuttingLine, SectionHatch.

Each class follows the ``SheetComponent`` rule of ``cad.eng_drawing``:
``draw(msp, sheet, frame)``, run after every view is drawn. Hatch patterns are
ezdxf ``.pat`` definitions (``find_pattern``) put into DXF HATCH entities; holes
of a cut face are HATCH boundary paths, which readers clip.
"""

from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Sequence

import ezdxf
from ezdxf.enums import TextEntityAlignment
from ezdxf.tools import pattern as _pattern
from build123d import Face, GeomType, Keep, Plane, split

SESSION_HATCH_DIR = "./drawings/hatch"
IMAGE_HATCH_DIR = "/opt/mop/hatch"
_BAD_DASH = re.compile(r"(-?\d+\.\d+)\.\d+")


# --- helpers -------------------------------------------------------------------------

def _layer(doc, name: str) -> None:
    if name not in doc.layers:
        doc.layers.add(name)


def _text(msp, text, xy, height, layer, align=TextEntityAlignment.MIDDLE_LEFT) -> None:
    msp.add_text(str(text), height=height, dxfattribs={"layer": layer}).set_placement(xy, align=align)


# --- Table ---------------------------------------------------------------------------

class Table:
    """A free-column table. ``at`` is the top-left corner in sheet mm; rows run down.
    A ``title`` takes a merged row on top, then the column headers, then ``rows``."""

    def __init__(self, at, columns: Sequence[str], rows: Sequence[Sequence], *, title: str | None = None,
                 col_widths: Sequence[float] | None = None, row_height: float = 7,
                 text_height: float = 2.5, layer: str = "TABLE") -> None:
        self.at = (float(at[0]), float(at[1]))
        self.columns = [str(c) for c in columns]
        self.rows = [[str(c) for c in r] for r in rows]
        for r in self.rows:
            if len(r) != len(self.columns):
                raise ValueError(f"row {r!r} has {len(r)} cells, expected {len(self.columns)}")
        self.title = title
        self.row_height = float(row_height)
        self.text_height = float(text_height)
        self.layer = layer
        if col_widths is None:
            col_widths = [max(len(c) for c in [h, *(r[i] for r in self.rows)]) * self.text_height * 0.9 + 4
                          for i, h in enumerate(self.columns)]
        if len(col_widths) != len(self.columns):
            raise ValueError("col_widths must match columns")
        self.col_widths = [float(w) for w in col_widths]

    @property
    def width(self) -> float:
        return sum(self.col_widths)

    @property
    def height(self) -> float:
        return self.row_height * (len(self.rows) + 1 + (1 if self.title else 0))

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        x, y = self.at
        return (x, y - self.height, x + self.width, y)

    def draw(self, msp, sheet, frame) -> None:
        _layer(msp.doc, self.layer)
        attrs = {"layer": self.layer}
        x0, ytop = self.at
        rh, w = self.row_height, self.width
        n = len(self.rows) + 1 + (1 if self.title else 0)
        ybot = ytop - n * rh
        xs = [x0]
        for cw in self.col_widths:
            xs.append(xs[-1] + cw)
        first = 1 if self.title else 0
        for i in range(n + 1):
            msp.add_line((x0, ytop - i * rh), (x0 + w, ytop - i * rh), dxfattribs=attrs)
        for j, x in enumerate(xs):
            top = ytop - first * rh if 0 < j < len(xs) - 1 else ytop
            msp.add_line((x, top), (x, ybot), dxfattribs=attrs)
        pad = self.text_height * 0.6
        if self.title:
            _text(msp, self.title, (x0 + pad, ytop - rh / 2), self.text_height, self.layer)
        for ri, cells in enumerate([self.columns, *self.rows]):
            yc = ytop - (first + ri + 0.5) * rh
            for ci, cell in enumerate(cells):
                _text(msp, cell, (xs[ci] + pad, yc), self.text_height, self.layer)


# --- Balloons ------------------------------------------------------------------------

class Balloons:
    """Part-number balloons: ``items = [(view, model_point_3d, text, sheet_xy_of_balloon)]``."""

    def __init__(self, items, *, radius: float = 4, layer: str = "BALLOON") -> None:
        self.items = list(items)
        self.radius = float(radius)
        self.layer = layer

    def draw(self, msp, sheet, frame) -> None:
        _layer(msp.doc, self.layer)
        attrs = {"layer": self.layer}
        for view, p3, text, (bx, by) in self.items:
            px, py = view.model_to_sheet(p3)
            d = math.hypot(px - bx, py - by)
            if d > self.radius:
                k = self.radius / d
                msp.add_line((bx + (px - bx) * k, by + (py - by) * k), (px, py), dxfattribs=attrs)
            msp.add_circle((bx, by), self.radius, dxfattribs=attrs)
            msp.add_circle((px, py), 0.5, dxfattribs=attrs)
            _text(msp, text, (bx, by), sheet.text_height, self.layer, TextEntityAlignment.MIDDLE_CENTER)


# --- CuttingLine ---------------------------------------------------------------------

class CuttingLine:
    """A chain line between two model points on a parent view, with an arrow and
    ``label`` at each end. The arrows point to the +90 degree side of p1 -> p2."""

    def __init__(self, view, p1, p2, label: str = "A", *, layer: str = "CUTTING") -> None:
        self.view, self.p1, self.p2, self.label, self.layer = view, p1, p2, label, layer

    def draw(self, msp, sheet, frame) -> None:
        doc = msp.doc
        _layer(doc, self.layer)
        if "DASHDOT" not in doc.linetypes:
            doc.linetypes.add("DASHDOT", pattern=[20.0, 12.0, -3.0, 0.5, -3.0], description="Dash dot")
        a = self.view.model_to_sheet(self.p1)
        b = self.view.model_to_sheet(self.p2)
        msp.add_line(a, b, dxfattribs={"layer": self.layer, "linetype": "DASHDOT"})
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L == 0:
            raise ValueError("cutting line has zero length on the sheet")
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        nx, ny = -uy, ux
        th = sheet.text_height
        solid = {"layer": self.layer, "linetype": "CONTINUOUS"}
        for x, y in (a, b):
            tx, ty = x + nx * 8, y + ny * 8
            msp.add_line((x, y), (tx, ty), dxfattribs=solid)
            bx, by = tx - nx * 3, ty - ny * 3
            msp.add_lwpolyline([(tx, ty), (bx + ux, by + uy), (bx - ux, by - uy)], close=True, dxfattribs=solid)
            _text(msp, self.label, (tx + nx * th, ty + ny * th), th, self.layer, TextEntityAlignment.MIDDLE_CENTER)


# --- hatch patterns ------------------------------------------------------------------

def _pat_dirs(search) -> list[Path]:
    dirs: list[str] = [str(s) for s in (search or [])]
    dirs += [d for d in os.environ.get("MOP_HATCH_DIRS", "").split(os.pathsep) if d]
    dirs += [SESSION_HATCH_DIR, IMAGE_HATCH_DIR]
    return [Path(d) for d in dirs]


def _parse_pat(text: str) -> dict:
    return _pattern.parse(_BAD_DASH.sub(r"\1", text))


def find_pattern(name: str, search=None):
    """The ezdxf definition of hatch pattern ``name`` (case-insensitive). Folders are
    tried in order: ``search``, ``MOP_HATCH_DIRS``, ``./drawings/hatch``,
    ``/opt/mop/hatch`` (each recursively, ``*.pat``); the first hit wins. Without a
    hit, ezdxf's built-in tables are used. Raises KeyError when nothing has it."""
    key = str(name).upper()
    for folder in _pat_dirs(search):
        if not folder.is_dir():
            continue
        for f in sorted(folder.rglob("*.pat")):
            for pname, definition in _parse_pat(f.read_text(encoding="utf-8", errors="replace")).items():
                if pname.upper() == key:
                    return definition
    for table in (_pattern.ISO_PATTERN, _pattern.IMPERIAL_PATTERN):
        for pname, definition in table.items():
            if pname.upper() == key:
                return definition
    raise KeyError(f"hatch pattern {name!r} not found")


# --- SectionHatch --------------------------------------------------------------------

def _cut(shape, origin, normal):
    """``shape`` with the half on the ``normal`` side of the plane kept."""
    plane = Plane(origin=tuple(origin), z_dir=tuple(normal))
    return split(shape, plane, keep=Keep.TOP)


def _cut_faces(shape, origin, normal):
    plane = Plane(origin=tuple(origin), z_dir=tuple(normal))
    nz = plane.z_dir
    found = []
    for solid in _cut(shape, origin, normal).solids():
        for f in solid.faces():
            if f.geom_type != GeomType.PLANE:
                continue
            if abs(f.normal_at().dot(nz)) > 1 - 1e-6 and abs(plane.to_local_coords(f.center()).Z) < 1e-6:
                found.append(f)
    if found:
        return found
    # Fallback (split gave no cut face): section by a large face on the plane.
    big = max(shape.bounding_box().size) * 4 + 1
    return list((shape & Face.make_rect(big, big, plane=plane)).faces())


def _wire_points(wire, samples: int = 48):
    pieces = []
    for e in wire.edges():
        n = 1 if e.geom_type == GeomType.LINE else samples
        pieces.append([tuple(e @ (i / n)) for i in range(n + 1)])
    tol = 1e-5

    def same(p, q):
        return all(abs(a - b) < tol for a, b in zip(p, q))

    chain = list(pieces.pop(0))
    while pieces:
        for i, pc in enumerate(pieces):
            if same(chain[-1], pc[0]):
                chain += pc[1:]
            elif same(chain[-1], pc[-1]):
                chain += pc[::-1][1:]
            else:
                continue
            pieces.pop(i)
            break
        else:
            raise ValueError("wire edges do not form one chain")
    if same(chain[0], chain[-1]):
        chain.pop()
    return chain


class SectionHatch:
    """Hatch the cut faces of ``shapes`` (a list of parts or ``{name: part}``) cut by
    the plane (``plane_origin``, ``plane_normal``) on the parent ``view``."""

    def __init__(self, view, shapes, plane_origin, plane_normal, *, pattern: str = "ANSI31",
                 by_part: dict | None = None, scale: float = 1.0, angle: float = 0.0,
                 layer: str = "HATCH", search=None) -> None:
        self.view = view
        if isinstance(shapes, dict):
            self.parts = [(str(k), v) for k, v in shapes.items()]
        else:
            self.parts = [(getattr(s, "label", "") or f"part{i}", s) for i, s in enumerate(shapes)]
        self.origin, self.normal = plane_origin, plane_normal
        self.pattern, self.by_part = pattern, dict(by_part or {})
        self.scale, self.angle, self.layer, self.search = float(scale), float(angle), layer, search

    @staticmethod
    def _touch(a, b) -> bool:
        ba, bb = a.bounding_box(), b.bounding_box()
        tol = 1e-6
        return (ba.min.X <= bb.max.X + tol and bb.min.X <= ba.max.X + tol
                and ba.min.Y <= bb.max.Y + tol and bb.min.Y <= ba.max.Y + tol
                and ba.min.Z <= bb.max.Z + tol and bb.min.Z <= ba.max.Z + tol)

    def draw(self, msp, sheet, frame) -> None:
        _layer(msp.doc, self.layer)
        flip = False
        prev = None
        for name, shape in self.parts:
            flip = (not flip) if prev is not None and self._touch(prev, shape) else False
            prev = shape
            loops = []
            for face in _cut_faces(shape, self.origin, self.normal):
                loops.append((face.outer_wire(), True))
                loops += [(w, False) for w in face.inner_wires()]
            if not loops:
                continue
            pat = self.by_part.get(name, self.pattern)
            hatch = msp.add_hatch(color=7, dxfattribs={"layer": self.layer})
            for wire, outer in loops:
                pts = [self.view.model_to_sheet(p) for p in _wire_points(wire)]
                hatch.paths.add_polyline_path(
                    pts, is_closed=True,
                    flags=ezdxf.const.BOUNDARY_PATH_EXTERNAL if outer else ezdxf.const.BOUNDARY_PATH_DEFAULT)
            if str(pat).upper() == "SOLID":
                hatch.set_solid_fill(color=7)
            else:
                hatch.set_pattern_fill(str(pat), color=7, angle=self.angle + (90.0 if flip else 0.0),
                                       scale=self.scale, definition=find_pattern(pat, self.search))
