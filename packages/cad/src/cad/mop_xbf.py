"""MoP fork (061-1 D5): a stored XBF's structure, read for a program outside the engine.

One marked file. It reads only. It changes no upstream file. A consumer (the InFill server) calls
``read`` and ``placed`` here and nothing under ``cad.engine``, so an upstream release that moves the
engine's internals changes this file alone. It leaves the fork when upstream ships a public reader.

``read(path)`` gives:

- ``structure``: the document's structure (``tree``, ``prototypes``, ``instances`` with their
  4x4 column-major placement ``matrix``), as ``cad inspect --query structure`` prints it.
- ``prototypes``: per prototype, in structure order, its whole-shape facts measured in its own
  frame (``volume``, ``area``, ``box``, ``centroid``, ``solids``).

``placed(facts, matrix)`` gives one prototype's facts where an occurrence stands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["read", "placed"]


def read(path: str | Path) -> dict[str, Any]:
    """The structure and the per-prototype facts of the XBF at ``path``."""
    from cad.engine import document, mesh

    loaded = document.Loaded(Path(path))
    return {"structure": loaded.structure(),
            "prototypes": [mesh._whole(prototype.shape) for prototype in loaded.prototypes]}


def placed(facts: dict[str, Any], matrix: list[float]) -> dict[str, Any]:
    """``facts`` (a prototype's, from ``read``) with their points moved by ``matrix``."""
    from cad.engine import refs

    return refs.placed_facts(facts, matrix)
