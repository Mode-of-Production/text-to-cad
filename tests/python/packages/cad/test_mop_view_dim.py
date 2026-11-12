"""MoP fork tests for View.dim's aligned default (061 T447, UV58)."""

from __future__ import annotations

import math

import pytest

from cad.eng_drawing import Sheet, _render_sheet

L, ANGLE = 80.0, math.radians(30)
P1 = (0.0, 0.0, 0.0)
P2 = (L * math.cos(ANGLE), L * math.sin(ANGLE), 0.0)


def _bar():
    from build123d import Align, Box, Rot

    return Rot(0, 0, 30) * Box(L, 4, 4, align=(Align.MIN, Align.CENTER, Align.CENTER))


def _dims(orientation, p1=P1, p2=P2):
    sheet = Sheet("A3", title="DIM", scale=1)
    sheet.view(_bar(), "top", at=(200, 150)).dim(p1, p2, orientation=orientation)
    doc = _render_sheet(sheet, index=1, count=1, label="dim")
    return [e for e in doc.modelspace() if e.dxftype() == "DIMENSION"]


def test_dim_default_aligned() -> None:
    (d,) = _dims(None)
    assert d.get_measurement() == pytest.approx(L, abs=1e-6)
    # The dimension line is parallel to the edge (ezdxf writes it as a rotated dim).
    assert d.dxf.angle % 180 == pytest.approx(30, abs=1e-6)


def test_dim_h_v_args() -> None:
    (h,) = _dims("h")
    (v,) = _dims("v")
    assert h.get_measurement() == pytest.approx(L * math.cos(ANGLE), abs=1e-6)
    assert v.get_measurement() == pytest.approx(L * math.sin(ANGLE), abs=1e-6)
    with pytest.raises(ValueError):
        Sheet("A3", title="X").view(_bar(), "top", at=(200, 150)).dim(P1, P2, orientation="diag")


@pytest.mark.parametrize("p2, upstream", [((L, 0.0, 0.0), "h"), ((0.0, 40.0, 0.0), "v")])
def test_dim_default_axis_points_match_upstream(p2, upstream) -> None:
    # Upstream's None picked whichever axis the pair spans; on an axis pair the
    # aligned default must draw exactly that.
    default = _dims(None, P1, p2)
    explicit = _dims(upstream, P1, p2)
    assert [d.get_measurement() for d in default] == pytest.approx([d.get_measurement() for d in explicit])
    assert [(d.dxf.defpoint, d.dxf.dimtype) for d in default] == [(d.dxf.defpoint, d.dxf.dimtype) for d in explicit]
