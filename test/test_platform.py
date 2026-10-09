"""Platform-dispatch unit tests (no cluster required).

Covers the Linux-client fix (t_d1685315):

* on Windows, ``run_cli`` invokes the frozen exe (``get_binary()``) as an
  argv list with ``shell=False`` and hidden-window kwargs;
* on POSIX, ``run_cli`` calls ``aview_hpc._cli.main`` IN-PROCESS and never
  constructs a subprocess or touches ``subprocess.STARTUPINFO``;
* ``hidden_popen_kwargs`` returns ``{}`` when the interpreter has no
  ``STARTUPINFO`` (POSIX), so no call site can raise ``AttributeError``.

Runs on any host: the POSIX branch is driven by monkeypatching
``aview_hpc.platform.IS_WINDOWS`` (a module constant, not ``os.name`` --
patching ``os.name`` globally makes ``pathlib.Path`` instantiate the other
platform's flavour and raise).
"""

import logging
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).parent.parent))

from aview_hpc import platform  # noqa: E402
from aview_hpc.platform import (  # noqa: E402
    _exe_cmd,
    hidden_popen_kwargs,
    run_cli,
    run_cli_version,
)


class _FakeCliMain:
    """Records the argv/state it was called with, prints like the real CLI."""

    calls = []

    def __call__(self):
        import aview_hpc.platform as plat
        _FakeCliMain.calls.append(list(sys.argv))
        print(f'FAKE_MAIN:{sys.argv[1:]}')
        print('fake stderr noise', file=sys.stderr)


class TestPlatformConstant(unittest.TestCase):

    def test_constant_matches_os_name(self):
        import os
        self.assertEqual(platform.IS_WINDOWS, os.name == 'nt')


class TestHiddenPopenKwargs(unittest.TestCase):

    def test_posix_returns_empty_dict(self):
        # POSIX: no STARTUPINFO anywhere -> {} (never AttributeError)
        with patch.object(platform, 'IS_WINDOWS', False), \
             patch.object(platform.subprocess, 'STARTUPINFO', None, create=True):
            # removing the attribute entirely is the honest simulation:
            import subprocess as real_subprocess
            had = hasattr(real_subprocess, 'STARTUPINFO')
            if had:
                saved = real_subprocess.STARTUPINFO
                del real_subprocess.STARTUPINFO
                try:
                    self.assertEqual(hidden_popen_kwargs(), {})
                finally:
                    real_subprocess.STARTUPINFO = saved
            else:
                self.assertEqual(hidden_popen_kwargs(), {})

    def test_posix_flag_returns_empty_dict(self):
        # Drive the POSIX branch purely through the seam flag
        with patch.object(platform, 'IS_WINDOWS', False):
            self.assertEqual(hidden_popen_kwargs(), {})

    def test_windows_returns_startupinfo(self):
        # Windows: STARTUPINFO with the hide-window flag (identity of the
        # subprocess module is irrelevant -- only the branch is asserted)
        with patch.object(platform, 'IS_WINDOWS', True):
            kwargs = hidden_popen_kwargs()
            if hasattr(platform.subprocess, 'STARTUPINFO'):
                self.assertIn('startupinfo', kwargs)
                self.assertTrue(kwargs['startupinfo'].dwFlags
                                & platform.subprocess.STARTF_USESHOWWINDOW)
            else:
                # POSIX interpreter forced down the Windows branch degrades
                # to {} instead of raising (mirrors the CDM helper).
                self.assertEqual(kwargs, {})


class TestWindowsDispatch(unittest.TestCase):
    """Windows: frozen exe subprocess, argv list, shell=False."""

    def test_exe_cmd_uses_get_binary_unquoted(self):
        fake_bin = Path('C:/tools/aview_hpc.exe')
        with patch.object(platform, 'get_binary', return_value=fake_bin):
            cmd = _exe_cmd(['submit', 'a.acf'])
        self.assertEqual(cmd, [str(fake_bin), 'submit', 'a.acf'])
        # No embedded quotes: with shell=False quotes would corrupt the path
        self.assertEqual([c for c in cmd if '"' in c], [])

    def test_run_cli_windows_popen(self):
        fake_bin = Path('C:/tools/aview_hpc.exe')
        captured = {}

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                captured['cmd'] = cmd
                captured['kwargs'] = kwargs

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def communicate(self):
                return 'stdout-text', 'stderr-text'

        with patch.object(platform, 'IS_WINDOWS', True), \
             patch.object(platform, 'get_binary', return_value=fake_bin), \
             patch.object(platform.subprocess, 'Popen', FakePopen):
            out, err = run_cli(['get_job_table'])

        self.assertEqual((out, err), ('stdout-text', 'stderr-text'))
        self.assertEqual(captured['cmd'], [str(fake_bin), 'get_job_table'])
        self.assertFalse(captured['kwargs']['shell'])


class TestPosixDispatch(unittest.TestCase):
    """POSIX: in-process CLI call, no subprocess, no STARTUPINFO."""

    def setUp(self):
        _FakeCliMain.calls = []

    def test_run_cli_posix_inprocess(self):
        def _fail_popen(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError('Popen must not be called on POSIX')

        with patch.object(platform, 'IS_WINDOWS', False), \
             patch('aview_hpc._cli.main', _FakeCliMain()), \
             patch.object(platform.subprocess, 'Popen', _fail_popen):
            out, err = run_cli(['version'])

        self.assertIn('FAKE_MAIN', out)
        self.assertEqual(_FakeCliMain.calls, [['aview_hpc', 'version']])

    def test_run_cli_posix_restores_argv(self):
        argv_before = list(sys.argv)
        with patch.object(platform, 'IS_WINDOWS', False), \
             patch('aview_hpc._cli.main', _FakeCliMain()):
            run_cli(['submit', 'x.acf', '--mins', '5'])
        self.assertEqual(sys.argv, argv_before)

    # ------------------------------------------------------------------
    # Error contract (review round 1): the POSIX in-process path must
    # fail EXACTLY like the frozen exe subprocess -- a traceback written
    # to the captured stderr, which the aview_hpc.aview_hpc wrapper turns
    # into RuntimeError. CDM hpc_jobs.py:188 and base_test.py:982 catch
    # only RuntimeError, so raw paramiko.SSHException / socket.gaierror /
    # SystemExit escaping run_cli would abort sets on any transient blip.
    # ------------------------------------------------------------------

    def _assert_runtime_error_contract(self, run_cli_call, needle):
        from aview_hpc.aview_hpc import _clean_err
        out, err = run_cli_call
        self.assertEqual(out, '')
        # the exception class must NOT escape; a formatted traceback is
        # written into the stderr buffer instead
        self.assertIn('Traceback (most recent call last)', err)
        self.assertIn(needle, err)
        # and the wrapper classifies this stderr as a hard error (the
        # same check aview_hpc.aview_hpc runs before raising RuntimeError)
        self.assertTrue(_clean_err(err))

    def test_run_cli_posix_ssh_exception_becomes_stderr_traceback(self):
        # paramiko.SSHException escaping _cli.main (SSH banner / connect
        # failure) -- reproduced live by the reviewer on the round-1 head.
        import paramiko

        def ssh_fail_main():
            raise paramiko.SSHException('Error reading SSH protocol banner')

        def call():
            with patch.object(platform, 'IS_WINDOWS', False), \
                 patch('aview_hpc._cli.main', ssh_fail_main):
                return run_cli(['get_results', '/r/dir', '/l/dir'])

        self._assert_runtime_error_contract(call(), 'SSHException')

    def test_run_cli_posix_oserror_gaierror_becomes_stderr_traceback(self):
        # socket.gaierror (bad host / DNS blip) -- reproduced live by the
        # reviewer via get_job_table() with a bad host.
        def gai_fail_main():
            raise OSError(-2, 'Name or service not known')

        def call():
            with patch.object(platform, 'IS_WINDOWS', False), \
                 patch('aview_hpc._cli.main', gai_fail_main):
                return run_cli(['get_job_table'])

        self._assert_runtime_error_contract(call(), 'OSError')

    def test_run_cli_posix_system_exit_becomes_stderr_traceback(self):
        # CLI error paths call sys.exit(2) (set_config with a bogus
        # scheduler). SystemExit is a BaseException, NOT an Exception --
        # an `except Exception` here would let it escape raw.
        def exit_fail_main():
            raise SystemExit(2)

        def call():
            with patch.object(platform, 'IS_WINDOWS', False), \
                 patch('aview_hpc._cli.main', exit_fail_main):
                return run_cli(['set_config', '--scheduler', 'bogus'])

        out, err = call()
        self.assertEqual(out, '')
        self.assertIn('Traceback (most recent call last)', err)
        self.assertIn('SystemExit', err)

    def test_run_cli_posix_keyboard_interrupt_still_raises(self):
        # KeyboardInterrupt is re-raised, never converted: a ^C must not
        # be reported to the caller as a completed CLI failure.
        def interrupt_main():
            raise KeyboardInterrupt()

        with patch.object(platform, 'IS_WINDOWS', False), \
             patch('aview_hpc._cli.main', interrupt_main):
            with self.assertRaises(KeyboardInterrupt):
                run_cli(['version'])

    # ------------------------------------------------------------------
    # Process-state restoration (review round 1): _cli.main() installs
    # its own sys.excepthook and sets the root logger level from
    # --log_level (default INFO); run_cli must restore BOTH.
    # ------------------------------------------------------------------

    def test_run_cli_posix_restores_excepthook_and_root_level(self):
        import logging as _logging
        root = _logging.getLogger()
        old_excepthook = sys.excepthook
        old_level = root.level

        def stateful_main():
            # exactly what the real main() does (probed: _cli.py:690 and
            # _cli.py:862) -- mutates both pieces of process state
            sys.excepthook = lambda *a: None
            root.setLevel(_logging.DEBUG)
            print('{"ok": true}')

        try:
            with patch.object(platform, 'IS_WINDOWS', False), \
                 patch('aview_hpc._cli.main', stateful_main):
                out, err = run_cli(['version'])
            self.assertIn('{"ok": true}', out)
            # both must be back to the pre-call values
            self.assertIs(sys.excepthook, old_excepthook)
            self.assertEqual(root.level, old_level)
        finally:
            sys.excepthook = old_excepthook
            root.setLevel(old_level)

    def test_run_cli_posix_restores_state_after_cli_crash(self):
        # Restoration must also hold on the FAILURE path (the finally
        # block), not just after a successful call.
        import logging as _logging
        root = _logging.getLogger()
        old_excepthook = sys.excepthook
        old_level = root.level

        def crashing_stateful_main():
            sys.excepthook = lambda *a: None
            root.setLevel(_logging.DEBUG)
            raise RuntimeError('boom')

        try:
            with patch.object(platform, 'IS_WINDOWS', False), \
                 patch('aview_hpc._cli.main', crashing_stateful_main):
                out, err = run_cli(['version'])
            self.assertIs(sys.excepthook, old_excepthook)
            self.assertEqual(root.level, old_level)
            self.assertIn('RuntimeError: boom', err)
        finally:
            sys.excepthook = old_excepthook
            root.setLevel(old_level)

    def test_run_cli_posix_log_output_does_not_leak_to_stderr(self):
        # The CLI's logging handlers write to stderr by default; the
        # wrapper treats non-UserWarning stderr as a hard error, so log
        # records must be captured separately (VM probe t6/t7 regression:
        # with NO handlers configured -- the case inside Adams View --
        # logging falls to logging.lastResort, which writes straight to
        # sys.stderr and was captured as a process error).
        import logging as _logging
        root = _logging.getLogger()

        # Case 1: no handlers at all (the in-Aview situation).
        saved_handlers = list(root.handlers)
        for h in saved_handlers:
            root.removeHandler(h)
        try:
            def noisy_main():
                _logging.getLogger('aview_hpc.test').warning(
                    'keyring lookup did not answer (headless)')
                print('{"ok": true}')

            with patch.object(platform, 'IS_WINDOWS', False), \
                 patch('aview_hpc._cli.main', noisy_main):
                out, err = run_cli(['version'])
        finally:
            for h in saved_handlers:
                root.addHandler(h)

        self.assertIn('{"ok": true}', out)
        self.assertNotIn('keyring lookup', err)
        self.assertEqual(root.handlers, saved_handlers)

        # Case 2: an existing stderr handler must be swapped and restored.
        handler = _logging.StreamHandler(sys.stderr)
        root.addHandler(handler)
        try:
            with patch.object(platform, 'IS_WINDOWS', False), \
                 patch('aview_hpc._cli.main', noisy_main):
                out, err = run_cli(['version'])
        finally:
            root.removeHandler(handler)

        self.assertIn('{"ok": true}', out)
        self.assertNotIn('keyring lookup', err)
        self.assertIs(handler.stream, sys.stderr)

    def test_run_cli_version_posix_is_package_version(self):
        with patch.object(platform, 'IS_WINDOWS', False), \
             patch('aview_hpc._cli.main', _FakeCliMain()):
            self.assertEqual(run_cli_version(),
                             platform.__dict__['_pkg_version_for_tests']
                             if '_pkg_version_for_tests' in platform.__dict__
                             else __import__('aview_hpc.version',
                                             fromlist=['version']).version)


class PrintBasedRootHandler(logging.Handler):
    """A root-logger handler whose emit() writes via print(), exactly like
    the CDM plugin's AdamsLogFileHandler inside Adams View (general/log.py:
    ``emit`` is ``with message_window_quiet(): print(msg)``).

    It has NO ``stream`` attribute, so the 0.5.1 stream-swap could not
    re-point it: under ``redirect_stdout`` its records landed in the JSON
    capture buffer ahead of the payload (t_11595f7b, CI run 37923731507:
    LSF 1337242 DONE, then json.loads(out) died at char 0).
    """

    def emit(self, record):
        print(self.format(record))


class TestHermeticInprocessCapture(unittest.TestCase):
    """The in-process CLI stdout must contain ONLY the CLI's payload,
    no matter what logging handlers the HOST process installed on the
    root logger (t_11595f7b)."""

    def _run_with_host_handlers(self, handlers, main):
        import logging as _logging
        root = _logging.getLogger()
        saved = list(root.handlers)
        saved_level = root.level
        for h in saved:
            root.removeHandler(h)
        for h in handlers:
            root.addHandler(h)
        root.setLevel(_logging.INFO)
        try:
            with patch.object(platform, 'IS_WINDOWS', False), \
                 patch('aview_hpc._cli.main', main):
                return run_cli(['submit_multi', 'data.json'])
        finally:
            for h in handlers:
                root.removeHandler(h)
            for h in saved:
                root.addHandler(h)
            root.setLevel(saved_level)

    @staticmethod
    def _cli_like_main():
        """Logs INFO records (like _cli.HPCSession.submit does) and then
        prints the JSON payload (like _cli.main's submit_multi branch)."""
        import json

        def main():
            logging.getLogger('aview_hpc._cli').info('Arguments: {...}')
            logging.getLogger('aview_hpc._cli').info(
                'Running: /home/thornton/scripts/lsf.py '
                '/home/thornton/hpc_tmp/set_1.acf --mins 50400 --mem_mb 32768')
            logging.getLogger('aview_hpc._cli').info(
                'Output: Job <1337242> is submitted to queue lnx64')
            print(json.dumps({'remote_dirs': ['/r/set_1.x'],
                              'job_names': ['set_1'],
                              'job_ids': [1337242]}))
        return main

    def test_print_based_root_handler_cannot_pollute_stdout(self):
        # The CI-37923731507 reproduction: a print-based root handler
        # (CDM's AdamsLogFileHandler) emitting INFO records under
        # redirect_stdout. Pre-fix this prepended log lines to the JSON
        # and json.loads died at char 0; stdout must now be ONLY the JSON.
        import json
        out, err = self._run_with_host_handlers([PrintBasedRootHandler()],
                                                self._cli_like_main())
        self.assertEqual(json.loads(out),
                         {'remote_dirs': ['/r/set_1.x'],
                          'job_names': ['set_1'],
                          'job_ids': [1337242]})
        self.assertNotIn('Running:', out)
        self.assertNotIn('Job <1337242>', out)
        self.assertEqual(err, '')

    def test_stream_handler_host_keeps_working_and_is_restored(self):
        # A host StreamHandler (stream-based) must not leak into either
        # capture, and must be re-attached with its original stream.
        import logging as _logging
        import io as _io
        stream = _io.StringIO()
        handler = _logging.StreamHandler(stream)
        out, err = self._run_with_host_handlers([handler],
                                                self._cli_like_main())
        import json
        json.loads(out)  # must parse: whole buffer is the payload
        self.assertNotIn('Running:', out)
        # The CLI's records went to the scratch buffer, not the host
        # handler's stream (host handlers are detached for the duration).
        self.assertEqual(stream.getvalue(), '')
        self.assertIs(handler.stream, stream)
        # ... and the handler object is fully functional afterwards
        # (re-attach it, as its owner would, and emit):
        root = _logging.getLogger()
        root.addHandler(handler)
        try:
            logging.getLogger('aview_hpc.test').warning('host handler alive')
            self.assertIn('host handler alive', stream.getvalue())
        finally:
            root.removeHandler(handler)

    def test_handlers_restored_in_order_after_call(self):
        import logging as _logging
        root_before = list(_logging.getLogger().handlers)
        first = PrintBasedRootHandler()
        second = PrintBasedRootHandler()
        out, _ = self._run_with_host_handlers([first, second],
                                              self._cli_like_main())
        root = _logging.getLogger()
        # The ORIGINAL handler set is restored, in its original order,
        # and neither the test handlers nor a scratch handler remain.
        self.assertEqual(root.handlers, root_before)
        self.assertNotIn(first, root.handlers)
        self.assertNotIn(second, root.handlers)

    def test_no_handlers_case_still_captured_not_stderr(self):
        # Fresh Adams View: root.handlers == [] -> lastResort would write
        # to sys.stderr; the scratch handler must still absorb the CLI's
        # records (0.5.1 behaviour preserved).
        out, err = self._run_with_host_handlers([], self._cli_like_main())
        import json
        json.loads(out)
        self.assertEqual(err, '')


class TestAviewHpcModuleUsesDispatch(unittest.TestCase):
    """The client entry points must go through run_cli (no direct exe use)."""

    def test_no_startupinfo_in_module(self):
        import inspect
        from aview_hpc import aview_hpc as av
        src = inspect.getsource(av)
        self.assertNotIn('STARTUPINFO', src)

    def test_submit_multi_builds_argv_without_quotes(self):
        from aview_hpc import aview_hpc as av
        captured = {}

        def fake_run_cli(argv, cwd=None):
            captured['argv'] = argv
            return ('{"remote_dirs": ["/r/d1"], "job_names": ["j1"], "job_ids": [7]}', '')

        with patch.object(av, 'run_cli', fake_run_cli), \
             patch.object(av, 'run_cli_version', lambda: '0'):
            av.submit_multi(acf_files=[Path('m1.acf')],
                            adm_files=[Path('m1.adm')],
                            aux_files=[[Path('aux dir/file.bin')]])

        argv = captured['argv']
        self.assertIn('submit_multi', argv)
        # No shell-style quoting may survive into the argv list
        self.assertEqual([a for a in argv if '"' in a], [])
        # The batch json path is a plain absolute-ish path
        batch = argv[argv.index('submit_multi') + 1]
        self.assertTrue(batch.endswith('data.json'))


class TestKeyringGuard(unittest.TestCase):
    """The keyring lookup must never block a headless session indefinitely."""

    def test_hung_backend_returns_none_within_timeout(self):
        # SecretService on a headless POSIX session blocks forever; the
        # guard must give up after KEYRING_TIMEOUT_S and return None
        # (=> SSH key auth), not hang.
        from aview_hpc import _cli
        import threading
        import time as _time

        release = threading.Event()

        def hung_get_password(service, username):
            release.wait(30)  # simulate a backend that never answers
            return 'should-not-be-returned'

        import sys as _sys
        fake_keyring = types.ModuleType('keyring')
        fake_keyring.get_password = hung_get_password
        with patch.dict(_sys.modules, {'keyring': fake_keyring}), \
             patch.object(_cli, 'KEYRING_TIMEOUT_S', 1):
            t0 = _time.perf_counter()
            pw = _cli._keyring_get_password('aview_hpc', 'thornton')
            elapsed = _time.perf_counter() - t0
        release.set()
        self.assertIsNone(pw)
        self.assertLess(elapsed, 5)

    def test_fast_backend_returns_password(self):
        from aview_hpc import _cli
        import sys as _sys
        fake_keyring = types.ModuleType('keyring')
        fake_keyring.get_password = lambda s, u: 'sekret'
        with patch.dict(_sys.modules, {'keyring': fake_keyring}):
            self.assertEqual(_cli._keyring_get_password('aview_hpc', 'thornton'),
                             'sekret')

    def test_missing_backend_returns_none(self):
        # keyring not installed at all (or import error) -> None, no raise
        from aview_hpc import _cli
        import sys as _sys
        with patch.dict(_sys.modules, {'keyring': None}):
            # patching a module to None makes `import keyring` raise ImportError
            self.assertIsNone(_cli._keyring_get_password('aview_hpc', 'thornton'))

    def test_cli_module_importable_without_keyring_paramiko(self):
        # The deferred imports mean _cli imports cleanly on an interpreter
        # that lacks keyring/paramiko entirely (the CDM deps-target case
        # before those were installed).
        import importlib
        import sys as _sys
        saved = {m: _sys.modules.pop(m)
                 for m in ('keyring', 'paramiko') if m in _sys.modules}
        try:
            import aview_hpc._cli as c
            importlib.reload(c)
        finally:
            _sys.modules.update(saved)
        self.assertTrue(hasattr(c, 'main'))


if __name__ == '__main__':
    unittest.main()
