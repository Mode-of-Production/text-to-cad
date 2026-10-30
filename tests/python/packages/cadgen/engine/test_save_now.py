"""A build that asks to be saved now (``cadgen run --json``) has its cache file and its record's
``cache`` field written before its answer, by a ``cadgen server`` started with a clean environment:
the ask travels in the build request, never in the server's environment. A build that does not ask
keeps the idle timer: nothing is written when it returns, and the idle save writes it later."""

from __future__ import annotations

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
from unittest import mock

from tests.python.support import engine_daemon

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cadgen.engine.registry import Registry  # noqa: E402

SCRIPT = """
import build123d as bd
from cadgen.engine import model

WIDTH = {width}


@model
def {name}():
    return bd.Box(WIDTH, 2, 1)


if __name__ == "__main__":
    {name}()
"""

# What a process needs from the host to start at all; nothing of cadgen's.
HOST = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
        "LOCALAPPDATA", "APPDATA", "COMSPEC", "PATHEXT", "LANG", "LC_ALL")


def clean_environment(root: Path) -> dict[str, str]:
    """The pool's server environment: the host's basics, the fork's source, and the engine, state and
    cache folders. No ``CADGEN_REPORT_JSON``, no ``CADGEN_ENGINE_CACHE_IDLE``: the default idle timer."""
    env = {name: os.environ[name] for name in HOST if name in os.environ}
    env.update(PYTHONPATH=str(SRC), CADGEN_ENGINE_DIR=str(root / "eng"), CADGEN_STATE_DIR=str(root / "state"),
               CADGEN_CACHE_DIR=str(root / "cache"), CADGEN_TELEMETRY="0", DO_NOT_TRACK="1")
    return env


class SaveNowTest(unittest.TestCase):
    """One ``cadgen server`` with a clean environment for the class; each test runs its own script."""

    @classmethod
    def setUpClass(cls) -> None:
        from cadgen.engine import client, daemon

        cls.root = Path(os.path.realpath(tempfile.mkdtemp(prefix="save-now-")))
        cls.addClassCleanup(shutil.rmtree, cls.root, True)
        (cls.root / "eng").mkdir()
        cls.env = clean_environment(cls.root)
        cls.server_env = dict(cls.env)
        process = subprocess.Popen([sys.executable, "-m", "cadgen.cli", "server"], env=cls.server_env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(engine_daemon.reap, process)
        cls.addClassCleanup(engine_daemon.run, [sys.executable, "-m", "cadgen.engine", "stop"], env=dict(cls.env))
        with mock.patch.dict(os.environ, {"CADGEN_ENGINE_DIR": cls.env["CADGEN_ENGINE_DIR"]}):
            deadline = time.monotonic() + 120
            while (found := client._connect(daemon.address())) is None:
                if process.poll() is not None:
                    raise AssertionError("the server exited before its door answered")
                if time.monotonic() > deadline:
                    raise AssertionError("the server's door did not answer in 120 s")
                time.sleep(0.05)
            found.close()
        cls.server = process

    def setUp(self) -> None:
        self.project = Path(tempfile.mkdtemp(prefix="p-", dir=self.root))

    def write(self, name: str, width: float) -> None:
        (self.project / f"{name}.py").write_text(textwrap.dedent(SCRIPT.format(name=name, width=width)),
                                                 encoding="utf-8")

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        done = engine_daemon.run([sys.executable, "-m", "cadgen.cli", "run", *args], cwd=self.project,
                                 env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done

    def state(self, ident: str) -> tuple[dict, Path]:
        """The registry record of document ``ident`` and its cache file's path, read now."""
        return Registry(self.root / "state").get(ident) or {}, self.root / "cache" / "documents" / ident / "document.xbf"

    def saved_at_revision(self, ident: str) -> bool:
        record, xbf = self.state(ident)
        meta = record.get("cache") or {}
        return (xbf.is_file() and meta.get("revision") == record.get("revision")
                and meta.get("bytes") == xbf.stat().st_size)

    def test_the_server_environment_has_no_save_switch(self) -> None:
        self.assertIsNone(self.server.poll())
        self.assertFalse([name for name in self.server_env if name in ("CADGEN_REPORT_JSON", "CADGEN_ENGINE_CACHE_IDLE",
                                                                         "CADGEN_ENGINE_CACHE_FLUSH")])

    def test_json_run_returns_with_its_document_saved(self) -> None:
        self.write("cube", 3.0)
        done = self.run_cli("--json", "cube.py")
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, done.stdout)
        report = json.loads(lines[0])
        self.assertTrue(report["ok"], report)
        # The moment the client returns: nothing awaited.
        self.assertTrue(self.saved_at_revision(report["id"]), self.state(report["id"])[0])

    def test_each_json_build_of_a_changed_script_is_saved_at_its_new_revision(self) -> None:
        self.write("plate", 4.0)
        first = json.loads(self.run_cli("--json", "plate.py").stdout.strip().splitlines()[-1])
        self.assertTrue(self.saved_at_revision(first["id"]))
        before = self.state(first["id"])[0]["revision"]
        self.write("plate", 5.0)
        second = json.loads(self.run_cli("--json", "plate.py").stdout.strip().splitlines()[-1])
        self.assertEqual(second["id"], first["id"])
        record, _xbf = self.state(second["id"])
        self.assertNotEqual(record["revision"], before)
        self.assertTrue(self.saved_at_revision(second["id"]), record)

    def test_plain_run_keeps_the_idle_timer(self) -> None:
        self.write("bar", 6.0)
        done = self.run_cli("bar.py")
        self.assertFalse(done.stdout.lstrip().startswith("{"), done.stdout)
        ident = next(row["id"] for row in Registry(self.root / "state").list()
                     if (row.get("home") or "").endswith("bar.py") or row.get("name") == "bar")
        self.assertFalse(self.saved_at_revision(ident), "a plain run returns before any save")
        deadline = time.monotonic() + 60
        while not self.saved_at_revision(ident):
            self.assertLess(time.monotonic(), deadline, "the idle save never came")
            time.sleep(0.2)

    def test_json_after_the_script_is_its_argument_not_the_flag(self) -> None:
        self.write("rod", 7.0)
        done = self.run_cli("rod.py", "--json")
        self.assertFalse(done.stdout.lstrip().startswith("{"), done.stdout)


if __name__ == "__main__":
    unittest.main()
