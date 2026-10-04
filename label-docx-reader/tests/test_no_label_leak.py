"""Tests never print label text: a failure names a file, a code or a digest (AGENTS.md).

The corpus holds real labels (the EMA's ePIs and templates, the FDA's templates), so a test that
printed what it read would put a label's text in a CI log. Two rules:

1. no test may print, log, warn or write to the process's standard output or error by any route
   it names (``print``, ``pprint``, ``breakpoint``, ``logging``, ``warnings``, ``traceback``,
   ``faulthandler``, ``sys.stdout`` or ``from sys import stdout``, ``os.write`` to 1 or 2, a
   path to a stream under /dev/ or /proc/self/). A ``.stdout`` of a subprocess or a captured
   result, a local variable named ``stderr`` and ``os.write`` to a pipe are not the process's
   streams and are allowed. It is a lint over names, not a sandbox: a route built at run time
   (``getattr(sys, "std" + "out")``, a path assembled from parts, ``ctypes``) passes it, and
   review is what catches that;
2. an assertion on what a corpus document reads to compares digests (``sha256_hex``), codes or
   counts, never the text: pytest prints both sides of a failed comparison, the arguments of a
   call it explains, and the object an attribute is taken from. A check that is not ``==``
   (``in``, ``!=``, ``is``, a truth) is worked out before the ``assert``, and the ``assert``
   names only its outcome.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent

FORBIDDEN_CALLS = {"print", "breakpoint", "pprint", "pp"}
# Modules that write to a stream or a log: importing one at all is refused.
FORBIDDEN_MODULES = {"logging", "pprint", "warnings", "faulthandler", "traceback"}
# The process's own streams, as attributes of sys.
STREAMS = {"stdout", "stderr", "__stdout__", "__stderr__"}
OS_WRITES = {"write", "writev"}
# Paths that are the process's streams or terminal, assembled so this module names none of them.
STREAM_PATHS = (
    *("/dev/" + name for name in ("stdout", "stderr", "tty", "fd/")),
    "/proc/self/" + "fd/",
)


def sha256_hex(data: bytes | str | None) -> str:
    """What a test compares in place of a label's text or bytes: equal exactly when they are.

    None (nothing kept, say) is "None", which no digest is, so it still equals only None.
    """
    if data is None:
        return "None"
    if isinstance(data, str):
        data = data.encode("utf-8", "surrogatepass")
    return hashlib.sha256(data).hexdigest()


def _bound(tree: ast.AST, module: str, names: set[str] | None = None) -> set[str]:
    """The names ``module`` is imported under, or (with ``names``) those its members are."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and names is None:
            # import os, import os as o, and import os.path (which binds os).
            out |= {a.asname or module for a in node.names if a.name == module}
            out |= {module for a in node.names if not a.asname and a.name.startswith(module + ".")}
        elif isinstance(node, ast.ImportFrom) and node.module == module and names is not None:
            out |= {a.asname or a.name for a in node.names if a.name in names}
    return out


def _violations(source: str) -> list[str]:
    """What in a test module could print, log or warn, by the rules in the module docstring."""
    tree = ast.parse(source)
    sys_names, os_names = _bound(tree, "sys"), _bound(tree, "os")
    os_writes = _bound(tree, "os", OS_WRITES)
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                out.append(f"calls {func.id}()")
            # os.write(1, ...) or write(2, ...) from os: the descriptor of a stream, by number.
            writes = (isinstance(func, ast.Name) and func.id in os_writes) or (
                isinstance(func, ast.Attribute)
                and func.attr in OS_WRITES
                and isinstance(func.value, ast.Name)
                and func.value.id in os_names
            )
            first = node.args[0] if node.args else None
            if writes and isinstance(first, ast.Constant) and first.value in (1, 2):
                out.append(f"writes to descriptor {first.value}")
        elif isinstance(node, ast.Attribute):
            # sys.stdout, an alias's (import sys as s), or a module's sys (os.sys.stdout).
            value = node.value
            of_sys = (isinstance(value, ast.Name) and value.id in sys_names) or (
                isinstance(value, ast.Attribute) and value.attr == "sys"
            )
            if node.attr in STREAMS and of_sys:
                out.append(f"reaches sys.{node.attr}")
            if node.attr in FORBIDDEN_CALLS:
                out.append(f"calls .{node.attr}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.extend(f"names {path}" for path in STREAM_PATHS if path in node.value)
        elif isinstance(node, ast.Import):
            out.extend(
                f"imports {alias.name}"
                for alias in node.names
                if alias.name.split(".")[0] in FORBIDDEN_MODULES
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.module.split(".")[0] in FORBIDDEN_MODULES:
                out.append(f"imports from {node.module}")
            out.extend(
                f"imports {alias.name}"
                for alias in node.names
                if alias.name in FORBIDDEN_CALLS or (node.module == "sys" and alias.name in STREAMS)
            )
    return out


@pytest.mark.parametrize("path", sorted(TESTS.glob("*.py")), ids=lambda path: path.name)
def test_no_test_module_prints_or_logs(path: Path) -> None:
    assert _violations(path.read_text(encoding="utf-8")) == [], path.name


@pytest.mark.parametrize(
    "source",
    [
        "print('x')",
        "breakpoint()",
        "import sys\nsys.stdout.write('x')",
        "import sys\nsys.stderr.write('x')",
        "import sys as s\ns.stdout.write('x')",
        "import sys\nout = sys.stdout\nout.write('x')",
        "import sys\nsys.__stderr__.write('x')",
        "import os\nos.sys.stdout.write('x')",
        "from sys import stdout\nstdout.write('x')",
        "from sys import stderr as e\ne.write('x')",
        "import pprint\npprint.pprint('x')",
        "from pprint import pprint\npprint('x')",
        "from pprint import pp",
        "import warnings\nwarnings.warn('x')",
        "from warnings import warn\nwarn('x')",
        "import logging\nlogging.info('x')",
        "import logging.handlers",
        "from logging import getLogger",
        "import traceback\ntraceback.print_exc()",
        "import faulthandler\nfaulthandler.dump_traceback()",
        "import os\nos.write(1, b'x')",
        "import os as o\no.writev(2, [b'x'])",
        "import os.path\nos.write(2, b'x')",
        "from os import write\nwrite(2, b'x')",
        "from os import write as w\nw(1, b'x')",
        # Split here so this module's own source names no stream path.
        "open('/dev/" + "stdout', 'w').write('x')",
        "open('/dev/" + "stderr', 'a')",
        "open('/dev/" + "tty', 'w')",
        "open('/dev/" + "fd/2', 'w')",
        "open('/proc/self/" + "fd/1', 'w')",
    ],
)
def test_every_way_to_print_is_caught(source: str) -> None:
    assert _violations(source), source


@pytest.mark.parametrize(
    "source",
    [
        # A subprocess's pipes and a captured result are not this process's streams.
        "import subprocess\nsubprocess.run(['x'], stdout=subprocess.PIPE).stdout",
        "import subprocess\nsubprocess.Popen(['x'], stderr=subprocess.PIPE).stderr.read()",
        "def t(capsys):\n    capsys.readouterr().err",
        # Local names that are not sys's.
        "stdout, stderr = b'', b''\nstderr.decode()",
        "import os\nread, write = os.pipe()\nos.write(write, b'x')",
        "import os\nos.write(4, b'x')",
    ],
)
def test_streams_of_other_objects_and_other_descriptors_are_allowed(source: str) -> None:
    assert _violations(source) == [], source
