"""MoP fork tests for US Letter, Tabloid, and portrait sheet sizes in cad.eng_drawing."""

from __future__ import annotations

import pytest

import cadgen.eng_drawing as cadgen_eng_drawing
from cad.eng_drawing import SHEET_SIZES, Sheet, _render_sheet


def _entity_points(entity) -> list[tuple[float, float]]:
    kind = entity.dxftype()
    if kind == "LINE":
        return [
            (float(entity.dxf.start.x), float(entity.dxf.start.y)),
            (float(entity.dxf.end.x), float(entity.dxf.end.y)),
        ]
    if kind == "LWPOLYLINE":
        return [(float(p[0]), float(p[1])) for p in entity.get_points("xy")]
    return []


def test_letter_sizes() -> None:
    assert "LETTER" in SHEET_SIZES
    assert "TABLOID" in SHEET_SIZES
    assert SHEET_SIZES["LETTER"] == (279.4, 215.9)
    assert SHEET_SIZES["TABLOID"] == (431.8, 279.4)

    letter = Sheet(size="LETTER")
    assert letter.width == 279.4
    assert letter.height == 215.9

    tabloid = Sheet(size="TABLOID")
    assert tabloid.width == 431.8
    assert tabloid.height == 279.4

    a3 = Sheet(size="A3")
    assert a3.width == 420.0
    assert a3.height == 297.0

    assert cadgen_eng_drawing.SHEET_SIZES is SHEET_SIZES
    with pytest.raises(TypeError):
        Sheet(size="LETTER", width=100.0)  # type: ignore[call-arg]
    with pytest.raises(AttributeError):
        letter.width = 100.0  # type: ignore[misc]


def test_portrait_keys() -> None:
    expected = {
        "LETTER_PORTRAIT": (215.9, 279.4),
        "TABLOID_PORTRAIT": (279.4, 431.8),
        "A4_PORTRAIT": (210.0, 297.0),
        "A3_PORTRAIT": (297.0, 420.0),
    }
    for key, dims in expected.items():
        assert key in SHEET_SIZES
        assert SHEET_SIZES[key] == dims
        assert isinstance(SHEET_SIZES[key][0], float)
        assert isinstance(SHEET_SIZES[key][1], float)
        sheet = Sheet(size=key)
        assert (sheet.width, sheet.height) == dims
        assert sheet.width < sheet.height

    assert Sheet(size="LETTER_PORTRAIT").width == 215.9
    assert Sheet(size="LETTER_PORTRAIT").height == 279.4
    assert Sheet(size="A3_PORTRAIT").width == 297.0
    assert Sheet(size="A3").width == 420.0

    for base in ("LETTER", "TABLOID", "A4", "A3"):
        portrait = f"{base}_PORTRAIT"
        assert SHEET_SIZES[portrait] == (SHEET_SIZES[base][1], SHEET_SIZES[base][0])


def test_portrait_title_block_and_border_inside_sheet() -> None:
    from build123d import Box

    assert "LETTER_PORTRAIT" in SHEET_SIZES
    for size_key in ("LETTER_PORTRAIT", "A4_PORTRAIT"):
        sheet = Sheet(size=size_key, title="PORTRAIT PART", part_number="P-01")
        sheet.view(Box(20, 15, 10), "top", at=(100.0, 150.0))
        doc = _render_sheet(sheet, index=1, count=1, label="portrait_test")
        msp = doc.modelspace()

        frame_pts = [
            pt
            for e in msp.query("LWPOLYLINE LINE")
            if e.dxf.layer == "SHEET"
            for pt in _entity_points(e)
        ]
        title_pts = [
            pt
            for e in msp.query("LWPOLYLINE LINE")
            if e.dxf.layer == "TITLE"
            for pt in _entity_points(e)
        ]
        assert frame_pts
        assert title_pts

        for x, y in frame_pts + title_pts:
            assert 0.0 <= x <= sheet.width
            assert 0.0 <= y <= sheet.height

        frame_x0 = min(x for x, _ in frame_pts)
        frame_x1 = max(x for x, _ in frame_pts)
        frame_y0 = min(y for _, y in frame_pts)
        frame_y1 = max(y for _, y in frame_pts)

        title_x0 = min(x for x, _ in title_pts)
        title_x1 = max(x for x, _ in title_pts)
        title_y0 = min(y for _, y in title_pts)
        title_y1 = max(y for _, y in title_pts)

        assert title_x0 >= frame_x0 - 1e-6
        assert title_x1 <= frame_x1 + 1e-6
        assert title_y0 >= frame_y0 - 1e-6
        assert title_y1 <= frame_y1 + 1e-6
        assert title_x1 == pytest.approx(frame_x1)
        assert title_y0 == pytest.approx(frame_y0)


def test_unknown_size_still_raises() -> None:
    for invalid in ("NOPE", "LETTER_LANDSCAPE", "A2_PORTRAIT", "letter", ""):
        with pytest.raises(ValueError, match="unknown sheet size.*one of"):
            Sheet(size=invalid)
