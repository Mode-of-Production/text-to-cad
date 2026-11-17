"""MoP fork (061 UV47): run ``cad run`` against a pre-populated packages directory.

When ``CAD_RUN_PKGS_DIR`` is set, ``settle`` verifies that every package declared in
``spec.requirements`` is present in ``CAD_RUN_PKGS_DIR`` or in the base environment, puts
``CAD_RUN_PKGS_DIR`` at the front of ``PYTHONPATH``, and clears the per-program spec so
``cad run`` uses the base environment without creating a virtual environment. A missing
package raises ``base.Refused`` with the single-line reason from
``<CAD_RUN_PKGS_DIR>/.refused/<normalized-name>`` (or ``not installed in CAD_RUN_PKGS_DIR``
when that file is absent).
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.machinery
import importlib.metadata
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from cad_server._internal import base, extras

__all__ = ["ENV", "settle"]

ENV = "CAD_RUN_PKGS_DIR"


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def _base_paths(env: base.Environment) -> list[str]:
    if env.own:
        return [item for item in sys.path if item]
    if env.path:
        return [item for item in env.path if item]
    prefix = Path(env.prefix)
    candidates = [prefix / "Lib" / "site-packages", *prefix.glob("lib/python*/site-packages")]
    return [str(path) for path in candidates if path.is_dir()]


def _refusal_reason(pkgs_dir: Path, name: str) -> str:
    refused_file = pkgs_dir / ".refused" / name
    try:
        raw = refused_file.read_bytes()
    except FileNotFoundError:
        return f"not installed in {ENV}"
    except OSError:
        return "no reason recorded"
    # Bytes, decoded leniently: a reason file with non-UTF-8 bytes must not crash the run.
    for line in raw.decode("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return "no reason recorded"


def settle(
    installation: base.Installation,
    prepared: Any,
    spec: extras.Spec,
    *,
    told: Callable[[str], None] | None = None,
) -> tuple[Any, extras.Spec]:
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return prepared, spec
    pkgs_dir = os.path.abspath(raw)
    pkgs_path = Path(pkgs_dir)
    env = base.ensure(installation, told=told, engine=True)
    assert env is not None
    search_paths = [pkgs_dir, *_base_paths(env)]

    if spec.requirements:
        importlib.invalidate_caches()
        dist_names = {
            _norm(dist.metadata["Name"])
            for dist in importlib.metadata.distributions(path=search_paths)
            if dist.metadata["Name"]
        }
        for requirement in spec.requirements:
            name = extras._name(requirement)
            if name is None:
                local = extras.local_package(requirement)
                if local is not None and os.path.exists(local):
                    continue
                fallback = _norm(Path(local or requirement).stem) or requirement
                raise base.Refused(f"{requirement}: {_refusal_reason(pkgs_path, fallback)}")
            if name in dist_names:
                continue
            mod = name.replace("-", "_")
            if mod.isidentifier() and importlib.machinery.PathFinder.find_spec(mod, search_paths) is not None:
                continue
            raise base.Refused(f"{name}: {_refusal_reason(pkgs_path, name)}")

    current = [item for item in os.environ.get("PYTHONPATH", "").split(os.pathsep) if item and item != pkgs_dir]
    os.environ["PYTHONPATH"] = os.pathsep.join([pkgs_dir, *current])
    base_spec = extras.Spec()
    return dataclasses.replace(prepared, spec=base_spec, given=[], script=None, declared=[]), base_spec
