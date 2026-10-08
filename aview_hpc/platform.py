"""Platform seam + subprocess dispatch for the aview_hpc client.

Historically every client entry point in :mod:`aview_hpc.aview_hpc`
shelled out to a frozen ``aview_hpc.exe`` (downloaded by
:mod:`aview_hpc.get_binary`).  That exe is Windows-only, and the
``subprocess.Popen`` call sites built ``subprocess.STARTUPINFO()`` and a
joined shell string unconditionally -- both raise on POSIX
(``AttributeError`` / ``Unsupported platform: Linux``), which killed the
first farm-backed Linux CI run (2026-10-08, run 37793054830).

This module owns every platform decision the client needs:

* :func:`is_windows` / :func:`hidden_popen_kwargs` -- the single place that
  touches ``subprocess.STARTUPINFO`` (Windows only, gated on the attribute
  actually existing so a POSIX interpreter forced down the Windows branch
  by a test degrades to ``{}`` instead of raising).
* :func:`run_cli` -- runs an aview_hpc CLI *command* and returns its
  captured stdout/stderr.  Windows keeps the frozen-exe subprocess exactly
  as before (argv list, ``shell=False``; the historic ``shell=True`` join
  is redundant quoting that broke paths with spaces).  POSIX runs the
  command **in-process**: ``aview_hpc`` is an ordinary importable package
  there, and the interpreter that imported this module is by definition
  able to import ``aview_hpc._cli`` -- inside Adams View on Linux that is
  Adams' embedded 3.10, which is the interpreter the deps target
  (``/tmp/thornton/adams_py310``) is installed for.  Spawning
  ``sys.executable -m aview_hpc`` instead would use the SYSTEM 3.9 inside
  aview (CDM AGENTS.md rule 2), which cannot see the deps target.
"""

import io
import os
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import List, Optional, Tuple

from .get_binary import get_binary

IS_WINDOWS = os.name == 'nt'
"""True on Windows.  Module-level constant so tests can assert dispatch,
while :func:`hidden_popen_kwargs` re-checks the attribute (see docstring)."""


def hidden_popen_kwargs() -> dict:
    """``Popen`` kwargs that suppress the child console window on Windows.

    Returns ``{}`` on POSIX, where ``subprocess.STARTUPINFO`` /
    ``STARTF_USESHOWWINDOW`` do not exist and would raise ``AttributeError``.
    Gated on ``hasattr`` as well as the platform so a POSIX interpreter
    forced down the Windows branch by a test degrades to ``{}``.
    """
    if IS_WINDOWS and hasattr(subprocess, 'STARTUPINFO'):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return {'startupinfo': startupinfo}
    return {}


def _exe_cmd(argv: List[str]) -> List[str]:
    """Argv list invoking the frozen exe (Windows path only).

    The exe path is not quoted: with ``shell=False`` the list goes to
    ``CreateProcess`` verbatim and quotes would become part of the path.
    """
    return [str(get_binary(print_=False)), *argv]


def _run_cli_inprocess(argv: List[str], cwd: Optional[Path] = None) -> Tuple[str, str]:
    """Run one aview_hpc CLI command in this interpreter (POSIX path).

    ``argv`` is the argument list AFTER the program name (e.g.
    ``['submit', 'x.acf', '--adm_file', 'x.adm']``).  The CLI's ``main()``
    parses ``sys.argv``, so ``sys.argv`` is swapped around the call and
    restored in a ``finally`` -- ``main()`` also exits only via exceptions,
    it never calls ``sys.exit`` on success, so no ``SystemExit`` handling
    is needed (the ``version`` / ``set_config`` error paths print to stderr
    and ``exit(2)``; that is surfaced as a normal ``SystemExit`` exception
    which the caller treats like any other failure).

    stdout/stderr are captured with ``redirect_stdout``/``redirect_stderr``
    so JSON printed by ``main()`` comes back exactly as the subprocess pipe
    did.  Working directory: callers historically ran the exe with
    ``cwd=acf_file.parent`` so relative acf/adm names resolve; ``os.chdir``
    is used for the same effect and restored afterwards (the CLI resolves
    every non-remote ``Path`` argument to an absolute path itself, so the
    chdir only has to cover paths the caller passed as relative strings).
    """
    from ._cli import main as cli_main

    argv = [str(a) for a in argv]

    old_argv = sys.argv
    old_cwd = Path.cwd()
    out_io, err_io = io.StringIO(), io.StringIO()

    sys.argv = ['aview_hpc', *argv]
    try:
        if cwd is not None:
            os.chdir(str(cwd))
        with redirect_stdout(out_io), redirect_stderr(err_io):
            cli_main()
    finally:
        sys.argv = old_argv
        if str(old_cwd) != str(Path.cwd()):
            os.chdir(str(old_cwd))

    return out_io.getvalue(), err_io.getvalue()


def run_cli(argv: List[str], cwd: Optional[Path] = None) -> Tuple[str, str]:
    """Run an aview_hpc CLI command; return ``(stdout, stderr)`` text.

    Windows: frozen exe subprocess (argv list, ``shell=False``, console
    window hidden).  POSIX: in-process call to :func:`aview_hpc._cli.main`
    in the interpreter that imported this package -- see module docstring
    for why ``sys.executable -m aview_hpc`` is NOT used inside Adams View.
    """
    if IS_WINDOWS:
        cmd = _exe_cmd(argv)
        with subprocess.Popen(cmd,
                              **hidden_popen_kwargs(),
                              shell=False,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              cwd=None if cwd is None else str(cwd),
                              text=True) as proc:
            out, err = proc.communicate()
        return out, err

    return _run_cli_inprocess(argv, cwd=cwd)


def run_cli_version() -> str:
    """Version string of the aview_hpc client (exe on Windows, package on POSIX)."""
    if IS_WINDOWS:
        out, _ = run_cli(['version'])
        return out.strip()

    from .version import version as pkg_version
    return pkg_version
