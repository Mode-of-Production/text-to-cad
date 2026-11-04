"""MoP fork (P2): ``cad run --json`` prints each model's report as one JSON line on stdout."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from tests.python.support import engine_daemon
from tests.python.support.paths import add_sources

SRC = add_sources()
MODEL = """
import build123d as bd
from cad import step


@step("box.step")
def box():
    return bd.Box(2, 1, 1)


if __name__ == "__main__":
    box()
"""


class RunJsonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(os.path.realpath(tempfile.mkdtemp(prefix="run-json-")))
        cls.addClassCleanup(shutil.rmtree, cls.folder, True)
        engine = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        cls.addClassCleanup(shutil.rmtree, engine, True)
        cls.env = engine_daemon.watched({**os.environ, "PYTHONPATH": SRC, "CAD_ENGINE_DIR": str(engine),
                                         "CAD_STATE_DIR": str(cls.folder / "state"),
                                         "CAD_CACHE_DIR": str(cls.folder / "cache"),
                                         "CAD_ENGINE_MESH": "inline"},
                                        cls.addClassCleanup)
        cls.addClassCleanup(engine_daemon.run, [sys.executable, "-m", "cad_server.engine", "stop"], env=cls.env)
        (cls.folder / "model.py").write_text(textwrap.dedent(MODEL), encoding="utf-8")

    def run_(self, *args: str):
        return engine_daemon.run([sys.executable, "-m", "cad_server.cli", "run", *args], cwd=self.folder, env=self.env)

    def test_json_prints_one_report_line_per_model(self) -> None:
        done = self.run_("--json", "model.py")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        self.assertEqual(len(lines), 1, done.stdout)
        report = json.loads(lines[0])
        self.assertTrue(report["ok"])
        self.assertIn("id", report)
        self.assertIn("wall", report)

    def test_without_json_the_report_is_text(self) -> None:
        done = self.run_("model.py")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertFalse(any(line.startswith("{") for line in done.stdout.splitlines()), done.stdout)


if __name__ == "__main__":
    unittest.main()
