"""MoP fork tests for Sheet.view_from, View.camera/effective_scale/model_to_sheet,
Sheet.components (SheetComponent), and @eng_drawing(out="*.dxf") (061 T007).
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import ezdxf
import pytest

from cad.eng_drawing import SHEET_SIZES, VIEW_DIRECTIONS, Sheet, View, _render_sheet, eng_drawing
from tests.python.support.tmp_root import temporary_directory

UPSTREAM_THREE_VIEWS_ENTITY_COUNT = 37
UPSTREAM_THREE_VIEWS_ENTITY_SHA256 = "14e908eede40d188359d42e0bc1063648f32ebbc483a49218828c424b540f35a"


def _part():
    from build123d import Align, Box, Cylinder, Pos

    align = (Align.CENTER, Align.CENTER, Align.MIN)
    return Box(40, 30, 10, align=align) - Pos(10, 5, 0) * Cylinder(3, 10, align=align)


def _entity_record(e) -> str:
    kind = e.dxftype()
    layer = e.dxf.layer
    if kind == "LINE":
        pts = (
            (round(e.dxf.start.x, 6), round(e.dxf.start.y, 6)),
            (round(e.dxf.end.x, 6), round(e.dxf.end.y, 6)),
        )
        return repr((kind, layer, pts))
    if kind == "LWPOLYLINE":
        pts = tuple((round(x, 6), round(y, 6)) for x, y in e.get_points("xy"))
        return repr((kind, layer, bool(e.closed), pts))
    if kind == "CIRCLE":
        return repr((kind, layer, (round(e.dxf.center.x, 6), round(e.dxf.center.y, 6)), round(e.dxf.radius, 6)))
    if kind == "ARC":
        return repr(
            (
                kind,
                layer,
                (round(e.dxf.center.x, 6), round(e.dxf.center.y, 6)),
                round(e.dxf.radius, 6),
                round(e.dxf.start_angle, 6),
                round(e.dxf.end_angle, 6),
            )
        )
    if kind == "TEXT":
        return repr(
            (
                kind,
                layer,
                str(e.dxf.text),
                round(e.dxf.height, 6),
                (round(e.dxf.insert.x, 6), round(e.dxf.insert.y, 6)),
            )
        )
    return repr((kind, layer))


def _entity_dump(doc, *, exclude_notes_text: bool = False) -> list[str]:
    records = []
    for e in doc.modelspace():
        if exclude_notes_text and e.dxftype() == "TEXT" and e.dxf.layer == "NOTES":
            continue
        records.append(_entity_record(e))
    return sorted(records)


def test_view_from_named_direction_equals_view() -> None:
    part = _part()
    snapshot_dirs = dict(VIEW_DIRECTIONS)
    assert len(snapshot_dirs) == 7
    for key, (direction, up) in snapshot_dirs.items():
        s_named = Sheet("A3", title="NAMED")
        s_named.view(part, key, at=(210, 150), label=key)
        doc_named = _render_sheet(s_named, index=1, count=1, label="cmp")

        s_from = Sheet("A3", title="NAMED")
        s_from.view_from(part, direction, up, at=(210, 150), label=key)
        doc_from = _render_sheet(s_from, index=1, count=1, label="cmp")

        assert _entity_dump(doc_from) == _entity_dump(doc_named), f"mismatch for direction {key}"

        s_unlabelled = Sheet("A3", title="NAMED")
        v_unlabelled = s_unlabelled.view_from(part, direction, up, at=(210, 150))
        assert v_unlabelled.name == "custom"
        doc_unlabelled = _render_sheet(s_unlabelled, index=1, count=1, label="cmp")
        assert _entity_dump(doc_unlabelled, exclude_notes_text=True) == _entity_dump(
            doc_named, exclude_notes_text=True
        )
    assert VIEW_DIRECTIONS == snapshot_dirs


def test_view_from_sets_camera_and_read_only_projection() -> None:
    from build123d import Box

    box = Box(40, 30, 10)
    sheet = Sheet("A3", scale=0.5)
    v_inherit = sheet.view_from(box, (1, 1, 1), (0, 0, 1), at=(150, 150))
    v_explicit = sheet.view(box, "top", at=(300, 150), scale=0.25)

    assert v_inherit.camera == ((1.0, 1.0, 1.0), (0.0, 0.0, 1.0))
    assert all(isinstance(c, float) for vec in v_inherit.camera for c in vec)
    assert v_explicit.camera == VIEW_DIRECTIONS["top"]

    assert v_inherit.effective_scale == 0.5
    assert v_explicit.effective_scale == 0.25

    with pytest.raises(RuntimeError, match="not rendered"):
        _ = v_inherit.model_to_sheet

    with pytest.raises(AttributeError):
        v_inherit.model_to_sheet = lambda p: (0.0, 0.0)  # type: ignore[misc]

    _render_sheet(sheet, index=1, count=1, label="proj")

    with pytest.raises(AttributeError):
        v_inherit.model_to_sheet = lambda p: (0.0, 0.0)  # type: ignore[misc]

    centre = tuple(box.bounding_box().center())
    for view in (v_inherit, v_explicit):
        sx, sy = view.model_to_sheet(centre)
        assert math.hypot(sx - view.at[0], sy - view.at[1]) < 1e-6


@pytest.mark.parametrize(
    "direction,up",
    [
        ((0, 0, 0), (0, 0, 1)),
        ((0, 0, 1), (0, 0, 0)),
        ((0, 0, 1), (0, 0, 2)),
        ((0, 0, 1), (0, 0, -3)),
        ((1.0, 0.0, 0.0), (1.0, 1e-10, 0.0)),
        ((10.0, 0.0, 0.0), (10.0, 0.5e-8, 0.0)),
        ((float("nan"), 0, 1), (0, 1, 0)),
        ((float("inf"), 0, 1), (0, 1, 0)),
        ((0, 0, 1), (0, float("nan"), 0)),
        ((0, 0, 1), (0, float("-inf"), 0)),
        ((1, 0), (0, 0, 1)),
        ((1, 0, 0), (0, 1)),
        ("top", (0, 1, 0)),
        ((0, 0, 1), "up"),
    ],
)
def test_view_from_rejects_zero_direction_and_parallel_up(direction, up) -> None:
    part = _part()
    sheet = Sheet("A4")
    with pytest.raises(ValueError):
        sheet.view_from(part, direction, up, at=(50, 50))


def test_view_from_boundary_and_single_source_guards() -> None:
    part = _part()
    sheet = Sheet("A3")
    # Just above the 1e-9 * nd * nu cross-product threshold: accepted and coerced to float tuples.
    v = sheet.view_from(part, [10, 0, 0], (v for v in (10.0, 2.0e-8, 0.0)), at=(210, 150))
    assert v.camera == ((10.0, 0.0, 0.0), (10.0, 2.0e-8, 0.0))
    assert not hasattr(View, "polyline")
    assert "custom" not in VIEW_DIRECTIONS
    with pytest.raises(ValueError, match="unknown view"):
        sheet.view(part, "custom", at=(100, 100))
    with pytest.raises(ValueError, match="scale"):
        sheet.view_from(part, (0, 0, 1), (0, 1, 0), at=(100, 100), scale=0)
    with pytest.raises(ValueError, match="finite"):
        sheet.view_from(part, (0, 0, 1), (0, 1, 0), at=(float("nan"), 100))


def test_components_draw_called_with_msp_sheet_frame() -> None:
    import cad.eng_drawing as ed

    assert "SheetComponent" in ed.__all__
    assert hasattr(ed, "SheetComponent")

    calls: list[tuple] = []

    class RecordingComponent:
        def draw(self, msp, sheet: Sheet, frame: tuple[float, float, float, float]) -> None:
            calls.append((msp, sheet, frame))

    part = _part()
    comp = RecordingComponent()
    sheet = Sheet("A4", components=[comp])
    sheet.view(part, "top", at=(150, 120))
    doc = _render_sheet(sheet, index=1, count=1, label="comp")
    msp = doc.modelspace()

    assert len(calls) == 1
    got_msp, got_sheet, got_frame = calls[0]
    assert got_msp is msp
    assert got_sheet is sheet

    border = [e for e in msp if e.dxftype() == "LWPOLYLINE" and e.dxf.layer == "SHEET"]
    assert len(border) == 1
    pts = list(border[0].get_points("xy"))
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    expected_frame = (min(xs), min(ys), max(xs), max(ys))
    assert got_frame == expected_frame
    assert got_frame == (10.0, 10.0, SHEET_SIZES["A4"][0] - 10.0, SHEET_SIZES["A4"][1] - 10.0)


def test_components_run_in_list_order_after_all_views() -> None:
    order: list[str] = []

    class MarkerComponent:
        def __init__(self, tag: str, y: float) -> None:
            self.tag = tag
            self.y = y

        def draw(self, msp, sheet: Sheet, frame: tuple[float, float, float, float]) -> None:
            order.append(self.tag)
            msp.add_text(f"COMP_{self.tag}", dxfattribs={"height": 2.5, "layer": "NOTES"}).set_placement(
                (frame[0] + 5.0, frame[1] + self.y)
            )

    part = _part()
    sheet = Sheet("A3", components=[MarkerComponent("FIRST", 60.0), MarkerComponent("SECOND", 70.0)])
    top, front, _right = sheet.three_views(part)
    top.overall()
    front.dim((-20, -15, 0), (20, -15, 0), offset=-12)
    doc = _render_sheet(sheet, index=1, count=1, label="order")

    assert order == ["FIRST", "SECOND"]
    entities = list(doc.modelspace())
    idx_first = next(
        i for i, e in enumerate(entities) if e.dxftype() == "TEXT" and e.dxf.text == "COMP_FIRST"
    )
    idx_second = next(
        i for i, e in enumerate(entities) if e.dxftype() == "TEXT" and e.dxf.text == "COMP_SECOND"
    )
    view_indices = [
        i
        for i, e in enumerate(entities)
        if e.dxf.layer in ("VISIBLE", "HIDDEN", "CENTER", "DIM")
        or (e.dxftype() == "TEXT" and e.dxf.layer == "NOTES" and e.dxf.text in ("TOP", "FRONT", "RIGHT"))
    ]
    assert view_indices
    assert max(view_indices) < idx_first < idx_second


@pytest.mark.parametrize("bad_out", ["x.svg", "x.step", "x.png", "x"])
def test_dxf_out_rejects_non_pdf_non_dxf_suffix(bad_out: str) -> None:
    with pytest.raises(ValueError) as exc_info:
        @eng_drawing(out=bad_out)
        def bad_suffix():
            return Sheet("A4")
    msg = str(exc_info.value)
    assert ".pdf" in msg and ".dxf" in msg


def test_dxf_out_writes_dxf_readable_by_ezdxf() -> None:
    part = _part()
    with temporary_directory(prefix="tmp-cad-mop-dxf-") as td:
        target = Path(td) / "sub" / "sheet.dxf"

        @eng_drawing(out=str(target))
        def make_dxf():
            sheet = Sheet("A4", title="DXF OUT")
            sheet.view_from(part, (1, 1, 1), (0, 0, 1), at=(150, 120))
            return sheet

        written = make_dxf()
        assert written == [target]
        assert target.exists()
        doc = ezdxf.readfile(str(target))
        layers = {e.dxf.layer for e in doc.modelspace()}
        assert "SHEET" in layers
        assert "VISIBLE" in layers


def test_dxf_out_one_sheet_only() -> None:
    part = _part()
    with temporary_directory(prefix="tmp-cad-mop-dxf-multi-") as td:
        target = Path(td) / "two.dxf"

        @eng_drawing(out=str(target))
        def two_sheets():
            s1 = Sheet("A4", title="ONE")
            s1.view(part, "top", at=(150, 120))
            s2 = Sheet("A4", title="TWO")
            s2.view(part, "front", at=(150, 120))
            return [s1, s2]

        with pytest.raises(ValueError):
            two_sheets()
        assert not target.exists()


def test_dxf_out_suffix_pdf_still_works() -> None:
    part = _part()
    with temporary_directory(prefix="tmp-cad-mop-pdf-") as td:
        target = Path(td) / "sheet.pdf"

        @eng_drawing(out=str(target))
        def make_pdf():
            sheet = Sheet("A4", title="PDF OUT")
            sheet.view(part, "top", at=(150, 120))
            return sheet

        written = make_pdf()
        assert written == [target]
        assert target.read_bytes().startswith(b"%PDF")


def test_empty_components_same_as_upstream() -> None:
    part = _part()

    sheet_default = Sheet("A3", title="UPSTREAM")
    sheet_default.three_views(part)
    doc_default = _render_sheet(sheet_default, index=1, count=1, label="u")
    dump_default = _entity_dump(doc_default)
    sha_default = hashlib.sha256("\n".join(dump_default).encode("utf-8")).hexdigest()

    assert len(dump_default) == UPSTREAM_THREE_VIEWS_ENTITY_COUNT
    assert sha_default == UPSTREAM_THREE_VIEWS_ENTITY_SHA256

    sheet_empty = Sheet("A3", title="UPSTREAM", components=[])
    sheet_empty.three_views(part)
    doc_empty = _render_sheet(sheet_empty, index=1, count=1, label="u")
    dump_empty = _entity_dump(doc_empty)
    sha_empty = hashlib.sha256("\n".join(dump_empty).encode("utf-8")).hexdigest()

    assert len(dump_empty) == UPSTREAM_THREE_VIEWS_ENTITY_COUNT
    assert sha_empty == UPSTREAM_THREE_VIEWS_ENTITY_SHA256
