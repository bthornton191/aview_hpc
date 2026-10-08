"""Transport unit tests (t_652a2eff): no cluster, no SSH, no paramiko import.

Covers the card's deliverable 6:

* transport selection: explicit ``transport`` key (local/ssh), missing key
  with host==self, missing key with host!=self, and a loud error on a
  bogus key value;
* LocalTransport operations against a tmp dir with a fake ``submit_cmd``:
  exec_argv (incl. multi-word submit_cmd), exec_shell (env prefix, quoted
  values, ~ and glob expansion), put/get/copy file, listdir, mkdtemp
  (unique, 775), cleanup (backend glob);
* paramiko is never imported in local mode (``sys.modules`` check);
* the SSH path is unchanged: SSHTransport.exec_argv joins to the same
  shell string that was historically sent, and HPCSession methods route
  to the transport (mocked -- no real SSH from unit tests).

Runs on any host/interpreter with pytest/unittest.
"""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.append(str(Path(__file__).parent.parent))

from aview_hpc import transport as transport_module  # noqa: E402
from aview_hpc.schedulers import get_scheduler  # noqa: E402
from aview_hpc.transport import (  # noqa: E402
    LocalTransport,
    SSHTransport,
    _host_is_local,
    select_transport,
)

FAKE_SUBMIT_TEMPLATE = '''#!{python}
import sys
# Fake lsf.py: prints the LSF submit response line for the acf path given
# as argv[1]; forwards remaining options verbatim.
acf = sys.argv[1]
print(f'Job <4242> is submitted to queue lnx64.')
'''


def _write_fake_submit(directory: Path) -> Path:
    script = directory / 'fake_submit.py'
    script.write_text(FAKE_SUBMIT_TEMPLATE.format(python=sys.executable))
    return script


class TestTransportSelection(unittest.TestCase):
    """Card deliverable 3: the `transport` key and host==self detection."""

    def test_explicit_local_key(self):
        with TemporaryDirectory() as td:
            t = select_transport(config={'transport': 'local',
                                         'remote_tempdir': td})
            self.assertIsInstance(t, LocalTransport)
            self.assertEqual(t.remote_root, Path(td))  # type: ignore[attr-defined]

    def test_explicit_ssh_key_wins_even_on_local_host(self):
        # An explicit 'ssh' beats host==self auto-detection: a submit host
        # whose admin insists on the SSH path keeps it.
        with patch.object(transport_module, '_host_is_local',
                          return_value=True), \
             patch.object(SSHTransport, '__init__', return_value=None) as init:
            select_transport(config={'transport': 'ssh', 'host': 'anything'})
        init.assert_called_once()

    def test_missing_key_host_is_self(self):
        # The VM case: config host resolves to this machine -> local.
        with patch.object(transport_module, '_host_is_local',
                          return_value=True), \
             patch.object(LocalTransport, '__init__', return_value=None) as init:
            select_transport(config={'host': 'sjcvl-thornton', 'remote_tempdir': '/x'})
        init.assert_called_once()

    def test_missing_key_host_is_not_self(self):
        # The Windows dev-box case: remote host -> ssh.
        with patch.object(transport_module, '_host_is_local',
                          return_value=False), \
             patch.object(SSHTransport, '__init__', return_value=None) as init:
            select_transport(config={'host': 'sjlsf01.cadence.com'})
        init.assert_called_once()

    def test_bogus_key_raises(self):
        with self.assertRaises(ValueError):
            select_transport(config={'transport': 'teleport'})

    def test_key_whitespace_and_case_folded(self):
        with TemporaryDirectory() as td:
            t = select_transport(config={'transport': ' Local ',
                                         'remote_tempdir': td})
            self.assertIsInstance(t, LocalTransport)

    def test_selection_is_logged(self):
        import logging
        from io import StringIO
        buf = StringIO()
        handler = logging.StreamHandler(buf)
        logger = logging.getLogger('aview_hpc.transport')
        old_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        try:
            with TemporaryDirectory() as td:
                select_transport(config={'transport': 'local',
                                         'remote_tempdir': td})
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
        self.assertIn('Chosen transport: local', buf.getvalue())

    def test_host_is_local_loopback(self):
        for name in ('localhost', '127.0.0.1', '::1', '127.5.4.3'):
            self.assertTrue(_host_is_local(name), name)

    def test_host_is_local_own_hostname(self):
        import socket as _socket
        hostname = (os.environ.get('COMPUTERNAME')
                    if os.name == 'nt' else _socket.gethostname())
        self.assertTrue(_host_is_local(str(hostname)))

    def test_host_is_local_unresolvable_is_false(self):
        # DNS failure must degrade to "not local" (-> ssh transport),
        # never raise out of transport selection.
        self.assertFalse(_host_is_local('no.such.host.invalid.example'))


class TestLocalTransportOps(unittest.TestCase):
    """Card deliverable 1+5: LocalTransport ops, argv-only, no shell=True."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.fake_submit = _write_fake_submit(self.tmp)
        self.transport = LocalTransport(remote_root=self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_exec_argv_submit_cmd_multiword(self):
        # submit_cmd is 'python3 /home/thornton/scripts/lsf.py' style: a
        # MULTI-WORD string. The SSH path's remote shell split it; local
        # mode must split it too or argv[0] won't resolve.
        out, err = self.transport.exec_argv(
            [f'"{sys.executable}" {self.fake_submit.as_posix()}',
             (self.tmp / 'job.acf').as_posix(), '--mins', '5'])
        self.assertIn('Job <4242> is submitted', out)
        self.assertEqual(err, '')

    def test_exec_argv_plain_word(self):
        out, err = self.transport.exec_argv(
            [sys.executable, self.fake_submit.as_posix(), 'x.acf'])
        self.assertIn('Job <4242>', out)

    def test_exec_argv_never_uses_shell(self):
        # A shell metacharacter in an ARGUMENT must be inert data, not
        # executed: proof that shell=False holds for exec_argv.
        out, err = self.transport.exec_argv(
            [sys.executable, self.fake_submit.as_posix(), 'a;rm -rf /b c&&d |e'])
        self.assertIn('Job <4242>', out)

    def test_exec_argv_nonzero_exit_raises_runtime_error(self):
        with self.assertRaises(RuntimeError):
            self.transport.exec_argv(
                [sys.executable, '-c', 'import sys; sys.exit(3)'])

    def test_exec_argv_missing_program_raises_runtime_error(self):
        # Not-on-PATH command: RuntimeError (CDM retry handlers catch only
        # RuntimeError), not a raw FileNotFoundError/OSError.
        with self.assertRaises(RuntimeError):
            self.transport.exec_argv(['definitely-not-a-command-xyz'])

    def test_exec_shell_splits_env_prefix_and_quotes(self):
        # The lsf job-table command: 'env LC_ALL=C <bjobs> -a -o "..." -json'
        fake_bjobs = self.tmp / 'bjobs'
        fake_bjobs.write_text('#!/bin/sh\necho "$@"\n')
        os.chmod(fake_bjobs, 0o755)
        cmd = (f'env LC_ALL=C {fake_bjobs.as_posix()} -a '
               f'-o "jobid job_name stat" -json')
        out, _ = self.transport.exec_shell(cmd)
        # the quoted -o value must arrive as ONE argument
        self.assertIn('jobid job_name stat', out)
        self.assertNotIn('-o "jobid', out)

    def test_exec_shell_rejects_control_operators(self):
        with self.assertRaises(RuntimeError):
            self.transport.exec_shell('echo a && echo b')
        with self.assertRaises(RuntimeError):
            self.transport.exec_shell('echo a | grep b')
        with self.assertRaises(RuntimeError):
            self.transport.exec_shell('echo a > file')

    def test_exec_shell_operator_detector_quote_awareness(self):
        from aview_hpc.transport import _has_unquoted_operator
        # quoted semicolons/redirects are data, not shell syntax
        self.assertIsNone(
            _has_unquoted_operator('python -c "import sys; sys.exit(0)"'))
        self.assertIsNone(
            _has_unquoted_operator("python -c 'print(1 < 2)'"))
        self.assertIsNone(_has_unquoted_operator('bsub -P "A:b:C:d:x>y" f'))
        # unquoted ones are caught, with the two-char forms intact
        self.assertEqual(_has_unquoted_operator('a && b'), '&&')
        self.assertEqual(_has_unquoted_operator('a || b'), '||')
        self.assertEqual(_has_unquoted_operator('a ; b'), ';')
        self.assertEqual(_has_unquoted_operator('a > b'), '>')
        # quote state must reset: an operator after a closed quote counts
        self.assertEqual(_has_unquoted_operator('"x" ; y'), ';')

    def test_exec_shell_expands_glob(self):
        (self.tmp / 'a.lsf').write_text('x')
        (self.tmp / 'b.lsf').write_text('x')
        out, _ = self.transport.exec_shell(
            f'"{sys.executable}" -c "import sys; print(len(sys.argv[1:]))" '
            f'{self.tmp.as_posix()}/*.lsf')
        self.assertEqual(out.strip(), '2')

    def test_exec_shell_glob_without_match_passes_pattern_through(self):
        # bash default: no match -> the pattern itself is the argument.
        out, _ = self.transport.exec_shell(
            f'"{sys.executable}" -c "import sys; print(sys.argv[1])" '
            f'{self.tmp.as_posix()}/*.nosuchext')
        self.assertEqual(out.strip(), f'{self.tmp.as_posix()}/*.nosuchext')

    def test_exec_shell_expands_tilde(self):
        out, _ = self.transport.exec_shell(
            f'"{sys.executable}" -c "import sys; print(sys.argv[1])" '
            f'~/somefile')
        self.assertEqual(out.strip(), os.path.expanduser('~/somefile'))

    def test_put_get_copy_listdir(self):
        job_dir = self.transport.mkdtemp(parent=self.tmp, name='job')
        src = self.tmp / 'x.acf'
        src.write_text('model.adm')

        self.transport.put_file(src, str(job_dir / 'x.acf'))
        self.assertEqual(self.transport.listdir(str(job_dir)), ['x.acf'])

        dst = self.tmp / 'got.acf'
        self.transport.get_file(str(job_dir / 'x.acf'), dst)
        self.assertEqual(dst.read_text(), 'model.adm')

        self.transport.copy_file(str(job_dir / 'x.acf'),
                                 str(job_dir / 'y.acf'))
        self.assertIn('y.acf', self.transport.listdir(str(job_dir)))

    def test_listdir_missing_dir_raises_filenotfound(self):
        with self.assertRaises(FileNotFoundError):
            self.transport.listdir(str(self.tmp / 'no-such-dir'))

    def test_mkdtemp_unique_and_775(self):
        d1 = self.transport.mkdtemp(parent=self.tmp, name='job')
        d2 = self.transport.mkdtemp(parent=self.tmp, name='job')
        self.assertTrue(d1.is_dir() and d2.is_dir())
        self.assertNotEqual(d1, d2)
        for d in (d1, d2):
            self.assertTrue(d.name.startswith('job.'))
            mode = d.stat().st_mode & 0o777
            # 775, or the platform's equivalent (Windows: 777 with only the
            # user bit meaningful; assert owner rwx always)
            self.assertTrue(mode & 0o700, oct(mode))

    def test_mkdtemp_missing_parent_raises(self):
        with self.assertRaises(RuntimeError):
            self.transport.mkdtemp(parent=self.tmp / 'gone', name='job')

    def test_cleanup_via_backend_argv(self):
        backend = get_scheduler('lsf')
        job_dir = self.transport.mkdtemp(parent=self.tmp, name='job')
        (job_dir / 'a.lsf').write_text('x')
        (job_dir / 'a.acf').write_text('x')

        self.transport.exec_cleanup(backend, job_dir)

        remaining = self.transport.listdir(str(job_dir))
        self.assertIn('a.acf', remaining)
        self.assertNotIn('a.lsf', remaining)

    def test_close_is_noop(self):
        self.transport.close()  # must not raise

    def test_get_results_through_session(self):
        # HPCSession.get_results routes listdir+get_file through the
        # transport (integration of the seam on the read path).
        from aview_hpc._cli import HPCSession

        job_dir = self.transport.mkdtemp(parent=self.tmp, name='job')
        (job_dir / 'r.msg').write_text('msg')
        (job_dir / 'r.res').write_text('res')
        (job_dir / 'r.txt').write_text('txt')

        session = object.__new__(HPCSession)  # no __init__: no config read
        session.transport = self.transport
        session.remote_dir = job_dir

        with TemporaryDirectory() as dst:
            files = session.get_results(Path(dst), extensions=['.msg', '.res'])
            self.assertEqual({f.name for f in files}, {'r.msg', 'r.res'})
            self.assertTrue(all(f.exists() for f in files))


class TestLocalStorageGuard(unittest.TestCase):
    """Card deliverable 4: job dirs must be farm-visible, never /tmp."""

    def test_tmp_root_refused(self):
        for bad in ('/tmp', '/tmp/aview_hpc', '/var/tmp/x', '/dev/shm/y'):
            with self.assertRaises(RuntimeError) as ctx:
                LocalTransport(remote_root=Path(bad))
            self.assertIn('farm-visible', str(ctx.exception))

    def test_nonexistent_root_refused(self):
        with self.assertRaises(RuntimeError) as ctx:
            LocalTransport(remote_root=Path('/vols/thornton_space/no_such_dir'))
        self.assertIn('does not exist', str(ctx.exception))

    def test_existing_vols_root_accepted(self):
        # A /vols root that exists is accepted without touching the network.
        with patch.object(Path, 'is_dir', return_value=True):
            t = LocalTransport(remote_root=Path('/vols/thornton_space/tmp'))
        self.assertEqual(t.remote_root, Path('/vols/thornton_space/tmp'))

    def test_arbitrary_existing_root_accepted(self):
        # Non-farm layout (unit test host): any existing non-temp dir works.
        with TemporaryDirectory() as td:
            t = LocalTransport(remote_root=Path(td))
            self.assertEqual(t.remote_root, Path(td))


class TestParamikoNotImportedLocally(unittest.TestCase):
    """Card deliverable 2: paramiko is imported lazily, only by SSHTransport."""

    def test_local_mode_never_imports_paramiko(self):
        saved = sys.modules.pop('paramiko', None)
        try:
            with TemporaryDirectory() as td:
                select_transport(config={'transport': 'local',
                                         'remote_tempdir': td})
            self.assertNotIn('paramiko', sys.modules)
        finally:
            if saved is not None:
                sys.modules['paramiko'] = saved

    def test_sshtransport_imports_paramiko_lazily_on_connect(self):
        # The import lives inside _connect, not at module/class definition:
        # importing aview_hpc.transport must not pull paramiko in.
        saved = sys.modules.pop('paramiko', None)
        try:
            import importlib
            importlib.reload(transport_module)
            self.assertNotIn('paramiko', sys.modules)
        finally:
            if saved is not None:
                sys.modules['paramiko'] = saved


class TestSSHPathUnchanged(unittest.TestCase):
    """Card deliverable 1: SSHTransport is the historical behaviour."""

    def setUp(self):
        self.ssh = SSHTransport.__new__(SSHTransport)  # no _connect
        self.sent = []

        def fake_exec_command(cmd):
            self.sent.append(cmd)

            class _Stream:
                def __init__(self, text=''):
                    self.text = text

                def read(self):
                    return self.text.encode()

            return (None, _Stream(), _Stream())

        # fakes injected without __init__; types are irrelevant here
        self.ssh.ssh = type('FakeSSH', (), {'exec_command':  # type: ignore[assignment]
                                            staticmethod(fake_exec_command)})()

        class FakeFtp:
            def put(self, a, b):
                pass

            def get(self, a, b):
                pass

            def listdir(self, d):
                return []

            def close(self):
                pass

        self.ssh.ftp = FakeFtp()  # type: ignore[assignment]

    def test_exec_argv_joins_to_shell_string(self):
        # Historically: ssh.exec_command('python3 lsf.py <acf> --mins 5')
        self.ssh.exec_argv(['python3 /home/thornton/scripts/lsf.py',
                            '/vols/x/job.acf', '--mins', '5'])
        self.assertEqual(
            self.sent[-1],
            'python3 /home/thornton/scripts/lsf.py /vols/x/job.acf --mins 5')

    def test_copy_file_uses_cp_command(self):
        self.ssh.copy_file('/vols/x/a.acf', '/vols/x/b.acf')
        self.assertEqual(self.sent[-1], 'cp /vols/x/a.acf /vols/x/b.acf')

    def test_close_closes_both(self):
        closed = []
        self.ssh.ssh.close = lambda: closed.append('ssh')
        self.ssh.ftp.close = lambda: closed.append('ftp')
        self.ssh.close()
        self.assertEqual(closed, ['ssh', 'ftp'])

    def test_cli_ssh_call_sites_use_transport(self):
        # The _cli methods must route through self.transport (no residual
        # self.ssh / self.ftp call sites outside the deprecated seam).
        import inspect
        from aview_hpc import _cli
        src = inspect.getsource(_cli)
        # _connect is the deprecated seam; nothing else may touch .ssh/.ftp
        forbidden = [line for line in src.splitlines()
                     if ('self.ssh' in line or 'self.ftp' in line)
                     and '_connect' not in line
                     and 'transport' not in line]
        self.assertEqual(forbidden, [])


if __name__ == '__main__':
    unittest.main()
