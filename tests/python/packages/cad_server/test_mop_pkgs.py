"""MoP fork (061 UV47): ``CAD_RUN_PKGS_DIR`` runs ``cad run`` from a pre-populated packages directory."""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import cad_server
from cad_server.cli import run as cli_run
from tests.python.support import engine_daemon
from tests.python.support.paths import add_sources

SRC = add_sources()


class _ForbiddenMopPkgs:
    def __getattr__(self, name: str):
        raise AssertionError(f"mop_pkgs.{name} must not be accessed when CAD_RUN_PKGS_DIR is unset")


class MopPkgsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(os.path.realpath(tempfile.mkdtemp(prefix="mop-pkgs-")))
        cls.addClassCleanup(shutil.rmtree, cls.folder, True)
        engine = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        cls.addClassCleanup(shutil.rmtree, engine, True)
        cls.base_env = engine_daemon.watched(
            {
                **os.environ,
                "PYTHONPATH": SRC,
                "CAD_ENGINE_DIR": str(engine),
                "CAD_STATE_DIR": str(cls.folder / "state"),
                "CAD_ENGINE_MESH": "inline",
                "UV_OFFLINE": "1",
            },
            cls.addClassCleanup,
        )
        cls.base_env.pop("CAD_RUN_PKGS_DIR", None)
        cls.addClassCleanup(engine_daemon.run, [sys.executable, "-m", "cad_server.engine", "stop"], env=cls.base_env)

    def _case_dirs(self, label: str) -> tuple[Path, Path]:
        work = self.folder / f"work-{label}"
        cache = self.folder / f"cache-{label}"
        work.mkdir(parents=True, exist_ok=True)
        cache.mkdir(parents=True, exist_ok=True)
        return work, cache

    def run_cli(self, *args: str, cwd: Path, cache: Path, pkgs_dir: Path | None = None):
        env = {**self.base_env, "CAD_CACHE_DIR": str(cache)}
        if pkgs_dir is not None:
            env["CAD_RUN_PKGS_DIR"] = str(pkgs_dir)
        else:
            env.pop("CAD_RUN_PKGS_DIR", None)
        return engine_daemon.run([sys.executable, "-m", "cad_server.cli", "run", *args], cwd=cwd, env=env)

    def test_a_pkgs_dir_runs_script_and_creates_no_env_folder(self) -> None:
        work, cache = self._case_dirs("a")
        pkgs = work / "pkgs"
        (pkgs / "tinypkg").mkdir(parents=True)
        (pkgs / "tinypkg" / "__init__.py").write_text('MARKER = "tinypkg-ok"\n', encoding="utf-8")
        script = work / "use_tiny.py"
        script.write_text(
            textwrap.dedent(
                """\
                # /// script
                # dependencies = ["tinypkg"]
                # ///
                import tinypkg
                print(tinypkg.MARKER)
                """
            ),
            encoding="utf-8",
        )
        envs_dir = cache / "base"
        before = set(envs_dir.iterdir()) if envs_dir.exists() else set()

        done = self.run_cli("use_tiny.py", cwd=work, cache=cache, pkgs_dir=pkgs)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("tinypkg-ok", done.stdout)
        after = set(envs_dir.iterdir()) if envs_dir.exists() else set()
        self.assertEqual(after, before)

    def test_b_absent_package_exits_one_with_single_refusal_stderr_line(self) -> None:
        work, cache = self._case_dirs("b")
        pkgs = work / "pkgs"
        refused = pkgs / ".refused"
        refused.mkdir(parents=True)
        (refused / "missing-pkg").write_text("wheel build forbidden in pool\n", encoding="utf-8")

        script_reason = work / "need_missing.py"
        script_reason.write_text(
            textwrap.dedent(
                """\
                # /// script
                # dependencies = ["Missing_Pkg>=1.0"]
                # ///
                print("unreachable")
                """
            ),
            encoding="utf-8",
        )
        done = self.run_cli("need_missing.py", cwd=work, cache=cache, pkgs_dir=pkgs)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        lines = [line for line in done.stderr.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, done.stderr)
        self.assertIn("wheel build forbidden in pool", lines[0])

        script_default = work / "need_unlisted.py"
        script_default.write_text(
            textwrap.dedent(
                """\
                # /// script
                # dependencies = ["unlisted_pkg"]
                # ///
                print("unreachable")
                """
            ),
            encoding="utf-8",
        )
        done_default = self.run_cli("need_unlisted.py", cwd=work, cache=cache, pkgs_dir=pkgs)
        self.assertEqual(done_default.returncode, 1, done_default.stdout + done_default.stderr)
        default_lines = [line for line in done_default.stderr.splitlines() if line.strip()]
        self.assertEqual(len(default_lines), 1, done_default.stderr)
        self.assertIn("not installed in CAD_RUN_PKGS_DIR", default_lines[0])

    def test_d_odd_refusal_files_give_one_line_and_no_traceback(self) -> None:
        work, cache = self._case_dirs("d")
        pkgs = work / "pkgs"
        refused = pkgs / ".refused"
        refused.mkdir(parents=True)
        cases = {
            "badbytes-pkg": (b"\xff\xfe bad \x80 bytes\n", "bad"),
            "empty-pkg": (b"", "no reason recorded"),
            "multi-pkg": (b"\n  first reason  \nsecond line\nthird\n", "first reason"),
        }
        for name, (payload, _) in cases.items():
            (refused / name).write_bytes(payload)
        for name, (_, expected) in cases.items():
            with self.subTest(name=name):
                script = work / f"need_{name}.py"
                script.write_text(
                    f'# /// script\n# dependencies = ["{name}"]\n# ///\nprint("unreachable")\n',
                    encoding="utf-8",
                )
                done = self.run_cli(script.name, cwd=work, cache=cache, pkgs_dir=pkgs)
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertNotIn("Traceback", done.stderr)
                lines = [line for line in done.stderr.splitlines() if line.strip()]
                self.assertEqual(len(lines), 1, done.stderr)
                self.assertIn(expected, lines[0])
                self.assertNotIn("second line", lines[0])

    def test_c_unset_variable_does_not_call_mop_pkgs(self) -> None:
        work, cache = self._case_dirs("c")
        plain = work / "plain.py"
        plain.write_text(
            textwrap.dedent(
                """\
                def _unused():
                    import build123d  # scanned as default integration


                print("plain-ok")
                """
            ),
            encoding="utf-8",
        )
        forbidden = _ForbiddenMopPkgs()
        env = {**self.base_env, "CAD_CACHE_DIR": str(cache)}
        env.pop("CAD_RUN_PKGS_DIR", None)
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.dict(sys.modules, {"cad_server.mop_pkgs": forbidden}),
            mock.patch.object(cad_server, "mop_pkgs", forbidden, create=True),
            contextlib.chdir(work),
        ):
            rc = cli_run.main(["plain.py"])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
