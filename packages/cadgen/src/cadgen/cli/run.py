"""``cadgen run ARGS`` -- this installation's ``python ARGS``: a model script (``cadgen run model.py``),
``-m`` or ``-c``, by the interpreter cadgen is installed in.

The ``cadgen`` command (``cadgen._internal.proxy``) runs ``cadgen +<version> run ...`` with that
version's python itself; this is the same verb on an installation's own console script.
"""

from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] in (["-h"], ["--help"]):
        sys.stdout.write("usage: cadgen run [--json] SCRIPT.py [ARGS...] | -m MODULE [ARGS...] | -c CODE\n\n"
                         "Run this installation's python with the arguments (a model script builds by running it).\n")
        return 0
    if args[:1] == ["--json"]:
        # The run's report as one JSON line, and its documents saved before the build answers: the
        # client asks both of each build it sends (``api._built``), in this run's process only.
        args = args[1:]
        os.environ["CADGEN_REPORT_JSON"] = "1"
    command = [sys.executable, *args]
    if os.name == "nt":  # no exec on Windows: a child, and its exit code
        import subprocess

        return subprocess.call(command)
    sys.stdout.flush()
    os.execv(sys.executable, command)
    return 1  # not reached


if __name__ == "__main__":
    raise SystemExit(main())
