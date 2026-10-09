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
  aview (CDM AGENTS.md rule 2), which cannot see the deps target.  The
  in-process path reproduces the exe's error contract and restores all
  process state it touches (see :func:`_run_cli_inprocess`).
"""

import io
import logging
import os
import subprocess
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import List, Optional, Tuple

from .get_binary import get_binary

LOG = logging.getLogger(__name__)

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
    restored in a ``finally``.

    stdout/stderr are captured with ``redirect_stdout``/``redirect_stderr``
    so JSON printed by ``main()`` comes back exactly as the subprocess pipe
    did.  Working directory: callers historically ran the exe with
    ``cwd=acf_file.parent`` so relative acf/adm names resolve; ``os.chdir``
    is used for the same effect and restored afterwards (the CLI resolves
    every non-remote ``Path`` argument to an absolute path itself, so the
    chdir only has to cover paths the caller passed as relative strings).

    Hermetic stdout (t_11595f7b): the CLI's machine-readable stdout must
    contain ONLY the CLI's own final ``print``.  The HOST process may
    configure ROOT logging handlers whose ``emit()`` writes straight to
    ``sys.stdout`` -- the CDM plugin inside Adams View does exactly this
    (``AdamsLogFileHandler.emit`` is ``print(msg)``, ``general/log.py``),
    and under ``redirect_stdout`` every such record landed in the JSON
    capture buffer AHEAD of the payload: CI run 37923731507 submitted LSF
    job 1337242 (DONE, clean stderr) and then died at
    ``json.loads(out)`` char 0.  Swapping ``handler.stream`` (the 0.5.1
    approach) only covers stream-based handlers; print-, socket- or
    queue-based handlers bypass it.  For the duration of the call every
    existing root handler is DETACHED and one scratch ``StreamHandler``
    collects the CLI's logging (which also keeps ``logging.lastResort``
    from ever firing); the original handlers are re-attached, in order,
    in the ``finally`` block.  Host records emitted during the call are
    captured into the scratch buffer -- the same routing the 0.5.1 stream
    swap applied, now complete.

    Error contract (kept identical to the frozen-exe subprocess): any
    failure of ``main()`` -- a real exception, ``SystemExit`` from an
    ``exit(2)`` CLI error path, DNS/SSH errors raised out of the
    scheduler -- is caught here (``KeyboardInterrupt`` excepted) and
    written into the captured stderr buffer as a formatted traceback,
    exactly what a crashed exe subprocess printed on stderr.  The
    ``aview_hpc.aview_hpc`` wrapper then raises its usual
    ``RuntimeError``.  Callers like CDM ``hpc_jobs.py`` / ``base_test.py``
    catch only ``RuntimeError``; letting the raw exception class escape
    would break their retry/abort handling (review round 1, t_d1685315).

    Process state (``sys.excepthook``, root logger level, root handler
    set) is saved before the call and restored in the ``finally``
    block: ``main()`` installs its own ``sys.excepthook`` and sets the
    root level from ``--log_level`` (default INFO); leaving those set
    would permanently change the host process (Adams View) after a
    single client call (review round 1, t_d1685315).

    The CLI logs through the ``logging`` package; its root handler writes
    to stderr, which the caller would misread as a real error (the
    ``aview_hpc.aview_hpc`` wrapper raises on any non-UserWarning stderr).
    For the duration of the in-process call the root logger's handlers
    are pointed at a scratch StringIO and restored afterwards, so log
    records never reach the caller's stderr capture.
    """
    from ._cli import main as cli_main

    argv = [str(a) for a in argv]

    old_argv = sys.argv
    old_cwd = Path.cwd()
    old_excepthook = sys.excepthook
    root = logging.getLogger()
    old_root_level = root.level
    out_io, err_io = io.StringIO(), io.StringIO()

    # The CLI's logging must not leak into the caller's stream captures:
    # aview_hpc.aview_hpc treats any non-UserWarning stderr as a hard
    # error (historic exe contract), and the JSON on stdout must stay
    # parseable. Two leak paths exist:
    #   1. handlers on the root logger. Stream-based handlers were
    #      swapped (0.5.1); print-based handlers (CDM's
    #      AdamsLogFileHandler inside Adams View) STILL wrote to
    #      sys.stdout/sys.stderr via their emit(), polluting the JSON
    #      (t_11595f7b, run 37923731507). Every existing root handler is
    #      therefore DETACHED for the duration and re-attached in order
    #      in the finally block -- regardless of how its emit() writes.
    #   2. NO handlers at all (the case inside a fresh Adams View:
    #      probed on sjcvl-thornton, root.handlers == []) -> logging
    #      falls back to logging.lastResort, a _StderrHandler that
    #      writes straight to sys.stderr, which redirect_stderr captures
    #      as process stderr. The scratch handler below also covers this.
    saved_handlers = list(root.handlers)
    log_io = io.StringIO()
    scratch_handler = logging.StreamHandler(log_io)
    scratch_handler.setLevel(root.level)
    for handler in saved_handlers:
        root.removeHandler(handler)
    root.addHandler(scratch_handler)

    sys.argv = ['aview_hpc', *argv]
    try:
        if cwd is not None:
            os.chdir(str(cwd))
        with redirect_stdout(out_io), redirect_stderr(err_io):
            cli_main()
    except KeyboardInterrupt:
        raise
    except BaseException:
        # Same contract as the frozen exe: a crashed CLI prints its
        # traceback to stderr (the CLI's own excepthook does exactly
        # this when it runs as a script), and the wrapper turns that
        # into RuntimeError. SystemExit (argparse / exit(2) error
        # paths) and scheduler exceptions (paramiko.SSHException,
        # socket.gaierror, OSError) MUST arrive here too, not escape
        # raw -- CDM's retry handlers catch only RuntimeError
        # (hpc_jobs.py:188, base_test.py:982; review round 1).
        print(traceback.format_exc(), file=err_io)
    finally:
        sys.argv = old_argv
        sys.excepthook = old_excepthook
        root.setLevel(old_root_level)
        root.removeHandler(scratch_handler)
        for handler in saved_handlers:
            root.addHandler(handler)
        if str(old_cwd) != str(Path.cwd()):
            os.chdir(str(old_cwd))

    LOG.debug('in-process CLI log output: %s', log_io.getvalue()[-2000:])

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