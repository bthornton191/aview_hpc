"""Transport layer for aview_hpc: SSH (historical) vs local (no SSH).

Why (t_652a2eff): on the Linux qual VM ``sjcvl-thornton`` the submitter IS
the sjlsf01 LSF submit host, so ``HPCSession``'s paramiko SSH+SFTP connection
is a self-SSH -- pointless overhead that also depends on a self-key.
Windows submitters still need SSH (Adams View on Windows blocks paramiko
in-process, so the client runs as the frozen exe there and the CLI always
SSHes).  ``HPCSession``'s remote operations therefore sit behind this
transport seam with two implementations:

* :class:`SSHTransport` -- the historical paramiko behaviour, unchanged.
  paramiko is imported lazily, inside ``_connect``, so local mode never
  needs it (and never needs the keyring).
* :class:`LocalTransport` -- run scheduler commands directly with
  ``subprocess.run`` (argv list, ``shell=False``), move files with
  ``shutil``, make dirs with ``tempfile``/``os``.

Selection (:func:`select_transport`):

* the ``transport`` key in ``~/.aview_hpc`` wins when set (``local`` or
  ``ssh``; anything else is a loud config error);
* with no key: ``local`` only when ``host`` resolves to this machine
  (FQDN/IP comparison, :func:`_host_is_local`), otherwise ``ssh``.

Storage rule (local mode): job dirs must live on farm-visible storage.
``LocalTransport.__init__`` refuses a remote root that is ``/tmp`` (or any
other non-farm location when :data:`FARM_VISIBLE_ROOTS` applies -- see the
constant) or that does not exist; the error names the configured value and
the sanctioned roots.  The check is only enforced where the farm layout is
actually known (the Cadence layout, ``/vols/<user>_space``); on any other
host the root must simply exist and not be a system temp dir.
"""

import glob
import logging
import os
import shlex
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from .config import get_config

LOG = logging.getLogger(__name__)

TRANSPORT_KEY = 'transport'
"""``~/.aview_hpc`` key: 'local' or 'ssh' (missing -> auto-detect)."""

FARM_VISIBLE_ROOTS = ('/vols/',)
"""Roots that ARE farm-visible storage on the sjlsf01 submit host layout.

Only consulted when the configured remote root is NOT under one of these:
a root under /vols is accepted as-is (any /vols/<x> path is farm storage),
while a root that is a system temp directory is always refused.  The
refusal is the point: job dirs on local-only storage (``/tmp``) are
invisible to the LSF execution hosts, so the job would run nowhere.
"""

SYSTEM_TEMP_ROOTS = ('/tmp', '/var/tmp', '/dev/shm')
"""Local-only locations a local-mode remote root must never be (or be under)."""

COLUMNS = '120'
"""Column width forced for ``ls`` in local mode.

The SSH path ran ``ls`` through a shell with the remote login environment;
``subprocess.run`` inherits no such width (and LSF's csh login prints
``Command execution in csh`` noise).  ``COLUMNS=120`` keeps ``ls -l`` on one
line so ``parse_ls_output`` sees the file name.
"""

RES_EXTS = ('.res', '.req', '.gra', '.msg', '.out')
LINUX_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def _which_first_word(word: str) -> str:
    """Resolve a command word against PATH like a shell would.

    ``subprocess.run([...], shell=False)`` does NOT search PATH for a bare
    program name the way a login shell does on some minimal environments
    (and inside Adams View PATH may be trimmed); resolving explicitly with
    :func:`shutil.which` makes local mode match the SSH path, where the
    remote shell always searched PATH.  Absolute paths, relatives with a
    slash, and unresolvable names pass through unchanged (the subprocess
    error then names the exact word).
    """
    if os.sep in word or (os.altsep and os.altsep in word):
        return word
    resolved = shutil.which(word)
    return resolved if resolved is not None else word


def _split(command: str) -> List[str]:
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    words = []
    while True:
        token = lexer.read_token()
        if token is None:
            break
        words.append(token)
    return words


def _expand_shell_word(word: str) -> List[str]:
    """Expand ``~`` and an unquoted glob in one shell word (local mode).

    Mirrors what the remote shell did for the SSH path.  A glob with no
    matches is passed through unchanged (bash's default), so the command's
    own error names the pattern.
    """
    word = os.path.expanduser(word)
    if any(ch in word for ch in '*?['):
        matches = sorted(glob.glob(word))
        if matches:
            return matches
    return [word]


def _has_unquoted_operator(command: str) -> Optional[str]:
    """Return the first unquoted shell control operator in `command`, if any.

    Scans the raw command string tracking quote state: operators inside
    quotes (e.g. a ``;`` inside ``python -c "print(a); print(b)"``) are
    legitimate data, not shell syntax.  Detectable operators: ``&&``,
    ``||``, ``;``, ``|``, ``>``, ``<`` (and a trailing ``&``).
    """
    operators = ('&&', '||', ';', '|', '>', '<')
    quote = None
    i = 0
    while i < len(command):
        ch = command[i]
        if quote is not None:
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
        elif ch == '\\':
            i += 1  # skip the escaped character
        else:
            two = command[i:i + 2]
            if two in ('&&', '||') or ch in operators:
                return two if two in ('&&', '||') else ch
        i += 1
    return None


class Transport:
    """The remote-operation seam used by ``HPCSession``.

    The SSH implementation speaks shell strings (historical behaviour);
    the local implementation speaks argv lists (``shell=False``).
    ``HPCSession`` builds an argv list plus, where a shell string already
    existed, keeps the string for SSH, so the SSH path stays byte-for-byte
    what it was.
    """

    name: str

    def exec_argv(self, argv: List[str]) -> Tuple[str, str]:
        """Run a scheduler command given as an argv list; return (stdout, stderr)."""
        raise NotImplementedError

    def exec_shell(self, command: str) -> Tuple[str, str]:
        """Run a command this package builds as a shell string; return (stdout, stderr)."""
        raise NotImplementedError

    def exec_cleanup(self, backend, remote_dir: Path) -> None:
        """Run the backend's pre-resubmit cleanup in the remote dir."""
        raise NotImplementedError

    def put_file(self, local_path: Path, remote_path: str) -> None:
        """Upload a file (sftp on SSH, copy on local)."""
        raise NotImplementedError

    def get_file(self, remote_path: str, local_path: Path) -> None:
        """Download a file (sftp on SSH, copy on local)."""
        raise NotImplementedError

    def copy_file(self, src_path: str, dst_path: str) -> None:
        """Copy an already-uploaded file server-side (sftp-server/cp)."""
        raise NotImplementedError

    def listdir(self, remote_dir: str) -> List[str]:
        raise NotImplementedError

    def mkdtemp(self, parent: Optional[Path], name: Optional[str],
                n_rand: int = 4) -> Path:
        """Create a job directory (mktemp -d semantics) and chmod it 775."""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class SSHTransport(Transport):
    """The historical behaviour: paramiko SSH + SFTP, unchanged.

    paramiko (and the keyring password lookup) are imported lazily inside
    :meth:`_connect` so a local-mode session never touches either.
    """

    name = 'ssh'

    def __init__(self, host: str, username: Optional[str] = None,
                 key_filename: Optional[str] = None):
        self.host = host
        self.username = username
        self.key_filename = key_filename
        self.ssh, self.ftp = self._connect()

    def _connect(self):
        from paramiko import SSHClient, AutoAddPolicy

        from ._cli import _keyring_get_password

        ssh = SSHClient()
        ssh.set_missing_host_key_policy(AutoAddPolicy())

        password = _keyring_get_password('aview_hpc', self.username or '')
        connect_kwargs = {'username': self.username, 'password': password}

        if password is None:
            # No stored password: use SSH key authentication. paramiko does
            # this by default when no password is given, but being explicit
            # allows a non-default key via the `key_filename` config key.
            connect_kwargs['look_for_keys'] = True
            connect_kwargs['allow_agent'] = True
            if self.key_filename is not None:
                connect_kwargs['key_filename'] = self.key_filename

        try:
            ssh.connect(self.host, **connect_kwargs)
        except socket.gaierror as err:
            raise socket.gaierror(f'Could not connect to {self.host}. '
                                  'Do you need to be on a VPN?') from err
        ftp = ssh.open_sftp()

        return ssh, ftp

    def exec_argv(self, argv: List[str]) -> Tuple[str, str]:
        # Historically the submit command was joined with spaces and run
        # through the remote shell; keep that exact behaviour.
        return self.exec_shell(' '.join(str(a) for a in argv))

    def exec_shell(self, command: str) -> Tuple[str, str]:
        _, stdout, stderr = self.ssh.exec_command(command)
        return stdout.read().decode(), stderr.read().decode()

    def exec_cleanup(self, backend, remote_dir: Path) -> None:
        self.exec_shell(backend.cleanup_command(remote_dir))

    def put_file(self, local_path: Path, remote_path: str) -> None:
        self.ftp.put(str(local_path), remote_path)

    def get_file(self, remote_path: str, local_path: Path) -> None:
        self.ftp.get(remote_path, str(local_path))

    def copy_file(self, src_path: str, dst_path: str) -> None:
        self.ssh.exec_command(f'cp {src_path} {dst_path}')

    def listdir(self, remote_dir: str) -> List[str]:
        return self.ftp.listdir(remote_dir)

    def mkdtemp(self, parent: Optional[Path], name: Optional[str],
                n_rand: int = 4) -> Path:
        cmd = 'mktemp -d'
        if parent:
            cmd += f' -p {Path(parent).as_posix()}'
        if name:
            cmd += f' {name}.' + 'X' * n_rand

        stdout, _ = self.exec_shell(cmd)
        remote_dir = Path(stdout.strip())
        _, out, err = self.ssh.exec_command(f'chmod 775 {remote_dir.as_posix()}')

        if err.read().decode() != '' or out.read().decode() != '':
            raise RuntimeError(f'Could not set permissions on {remote_dir}.')

        return remote_dir

    def close(self) -> None:
        self.ssh.close()
        self.ftp.close()


class LocalTransport(Transport):
    """Run the scheduler directly on this machine (the submit-host case).

    Scheduler commands (``submit_cmd``, job table, ls) run as argv lists
    with ``shell=False`` -- never a joined shell string (card rule 5).
    File transfer is ``shutil``, job dirs come from ``tempfile.mkdtemp``
    under the configured farm-visible root.  No paramiko, no keyring.
    """

    name = 'local'

    def __init__(self, remote_root: Optional[Path] = None):
        self.remote_root = (Path(remote_root)
                            if remote_root is not None else None)
        if self.remote_root is not None:
            self._check_remote_root(self.remote_root)

    @staticmethod
    def _check_remote_root(root: Path):
        """Fail loudly when the local-mode remote root is not farm-visible.

        A job dir on local-only storage (``/tmp``) is invisible to the LSF
        execution hosts: bsub accepts the job, then the solver cannot read
        the acf and the job dies with the files stranded on the submit
        host.  Refuse up front, naming the root and the fix.
        """
        posix = Path(root).as_posix()
        if any(posix == t or posix.startswith(t + '/')
               for t in SYSTEM_TEMP_ROOTS):
            raise RuntimeError(
                f'remote_tempdir {posix} is not farm-visible storage; '
                'local-mode job directories must live on storage the LSF '
                'execution hosts can see (e.g. /vols/<user>_space), never '
                f'a system temp dir ({", ".join(SYSTEM_TEMP_ROOTS)}). '
                'Set remote_tempdir in ~/.aview_hpc to a farm-visible path.')
        if any(posix.startswith(froot) for froot in FARM_VISIBLE_ROOTS):
            # Under a known farm-visible root: trust the layout.
            if not Path(root).is_dir():
                raise RuntimeError(
                    f'remote_tempdir {posix} does not exist. Create it or '
                    'point remote_tempdir in ~/.aview_hpc at an existing '
                    'farm-visible directory.')
            return
        # Some other host layout (e.g. unit tests, another farm): require
        # that it exists; the /tmp refusal above already applied.
        if not Path(root).is_dir():
            raise RuntimeError(
                f'remote_tempdir {posix} does not exist. Set remote_tempdir '
                'in ~/.aview_hpc to an existing farm-visible directory.')

    def exec_argv(self, argv: List[str]) -> Tuple[str, str]:
        """Run ``subprocess.run(argv, shell=False)``; return (stdout, stderr).

        ``argv[0]`` may be a multi-word ``submit_cmd`` string (e.g.
        ``python3 /home/thornton/scripts/lsf.py``): the SSH path ran it
        through the remote shell, which split it, so local mode applies the
        same POSIX split before resolving the first word against PATH.
        Values that contain spaces (rare, e.g. a ``--res_req``) stay one
        argument -- the SSH path could not pass those unquoted either.

        Nonzero exit raises RuntimeError carrying both streams, matching
        how the SSH path surfaces failures (``HPCSession`` reads stderr
        only when parsing fails; a scheduler that exits nonzero has failed
        even when it prints nothing useful).  A command that cannot run at
        all (not on PATH, not executable) also raises RuntimeError rather
        than a raw OSError -- CDM's retry handlers catch only RuntimeError.
        """
        argv = [str(a) for a in argv]
        if any(ch in argv[0] for ch in ' \t\n'):
            argv = _split(argv[0]) + argv[1:]
        argv[0] = _which_first_word(argv[0])
        LOG.debug('LocalTransport running (shell=False): %s', argv)
        try:
            proc = subprocess.run(argv, shell=False, capture_output=True,
                                  text=True)
        except OSError as err:
            raise RuntimeError(f'Could not run {argv[0]}: {err}') from err
        if proc.returncode != 0:
            raise RuntimeError(
                f'Command exited with status {proc.returncode}: '
                f'{" ".join(argv)}\nstdout: {proc.stdout}\n'
                f'stderr: {proc.stderr}')
        return proc.stdout, proc.stderr

    def exec_shell(self, command: str) -> Tuple[str, str]:
        """Run a package-built shell string as an argv list (no ``shell=True``).

        The string is tokenized with POSIX rules, ``~`` and unquoted globs
        are expanded like the remote shell did, the first word is resolved
        against PATH, and the result runs with ``shell=False``.  Unquoted
        shell control operators (``&&`` ``||`` ``;`` ``|`` ``>`` ``<``)
        are NOT supported and are rejected loudly (quoted occurrences --
        e.g. inside ``python -c "..."`` -- are data and pass through) --
        such a command must be given an argv form instead (card rule 5).
        """
        operator = _has_unquoted_operator(command)
        if operator is not None:
            raise RuntimeError(
                f'LocalTransport cannot run the shell operator {operator!r} '
                f'without a shell (command: {command!r}). '
                'This command needs an explicit argv form.')
        words = _split(command)
        expanded: List[str] = []
        for word in words:
            expanded.extend(_expand_shell_word(word))
        if not expanded:
            raise RuntimeError(f'Empty command: {command!r}')
        expanded[0] = _which_first_word(expanded[0])
        LOG.debug('LocalTransport running (shell=False): %s', expanded)

        env = dict(os.environ)
        env['COLUMNS'] = COLUMNS
        proc = subprocess.run(expanded, shell=False, capture_output=True,
                              text=True, env=env)
        if proc.returncode != 0:
            raise RuntimeError(
                f'Command exited with status {proc.returncode}: '
                f'{" ".join(expanded)}\nstdout: {proc.stdout}\n'
                f'stderr: {proc.stderr}')
        return proc.stdout, proc.stderr

    def exec_cleanup(self, backend, remote_dir: Path) -> None:
        # The backend globs the dir itself (cleanup_argv): no shell, no
        # glob chars in this process.
        self.exec_argv(backend.cleanup_argv(remote_dir))

    def put_file(self, local_path: Path, remote_path: str) -> None:
        shutil.copyfile(str(local_path), remote_path)

    def get_file(self, remote_path: str, local_path: Path) -> None:
        shutil.copyfile(remote_path, str(local_path))

    def copy_file(self, src_path: str, dst_path: str) -> None:
        shutil.copyfile(src_path, dst_path)

    def listdir(self, remote_dir: str) -> List[str]:
        try:
            return os.listdir(remote_dir)
        except FileNotFoundError as err:
            raise FileNotFoundError(
                f'Could not find remote directory {remote_dir}') from err

    def mkdtemp(self, parent: Optional[Path], name: Optional[str],
                n_rand: int = 4) -> Path:
        """``mktemp -d`` semantics: unique dir under `parent`, chmod 775.

        The dir name is ``<name>.XXXX`` (random suffix), matching the SSH
        path's ``mktemp -d -p <parent> <name>.XXXX``.
        """
        if parent is None:
            # No configured root: fall back to the system default ONLY on
            # non-farm layouts; the farm-layout guard lives in __init__,
            # which always has the configured root on the VM.
            parent = Path(tempfile.gettempdir())
        parent = Path(parent)
        if not parent.is_dir():
            raise RuntimeError(
                f'Cannot create job directory: {parent.as_posix()} does not '
                'exist (remote_tempdir in ~/.aview_hpc).')
        prefix = f'{name}.' if name else ''
        path = Path(tempfile.mkdtemp(prefix=prefix, dir=str(parent)))
        os.chmod(path, 0o775)
        return path

    def close(self) -> None:
        # No connection to close; job dirs are intentionally left in place
        # (the SSH path left them on the cluster too).
        return None


def _host_is_local(host: str) -> bool:
    """True when `host` resolves to this machine (FQDN or IP comparison).

    ``localhost`` and loopback IPs are local by definition.  Otherwise every
    address `host` resolves to (IPv4 and IPv6) is compared against every
    address this machine's own hostname resolves to.  DNS failure on either
    side simply means "not local" (the transport then falls back to ssh,
    which either works or reports the real connect error).
    """
    if not host:
        return False
    if host in ('localhost', '127.0.0.1', '::1') or host.startswith('127.'):
        return True

    def _addrinfo(name):
        addrs = set()
        for family in (socket.AF_INET, socket.AF_INET6):
            try:
                addrs |= {ai[4][0] for ai in socket.getaddrinfo(
                    name, None, family=family, proto=socket.IPPROTO_TCP)}
            except socket.gaierror:
                pass
        return addrs

    local_addrs = _addrinfo(socket.gethostname())
    if '.' not in socket.gethostname():
        # Short hostname: also try the FQDN, which is what the config is
        # expected to carry on the VM (e.g. sjcvl-thornton).
        local_addrs |= _addrinfo(socket.getfqdn())
    host_addrs = _addrinfo(host)

    return bool(host_addrs) and bool(host_addrs & local_addrs)


def select_transport(host: Optional[str] = None,
                     config: Optional[dict] = None) -> Transport:
    """Pick the transport and log the choice; construct and return it.

    Rules (card deliverable 3):

    * ``transport`` key 'local' -> LocalTransport;
    * ``transport`` key 'ssh'   -> SSHTransport;
    * any other value -> loud config error (ValueError);
    * key missing -> local only when `host` resolves to this machine
      (:func:`_host_is_local`), otherwise ssh.

    The chosen transport (and why) is logged at INFO so a submit log shows
    unambiguously whether SSH was used.
    """
    if config is None:
        config = get_config() or {}
    host = host or config.get('host')
    key = config.get(TRANSPORT_KEY)

    if key is not None and str(key).strip().lower() not in ('local', 'ssh'):
        raise ValueError(
            f"Invalid transport {key!r} in ~/.aview_hpc: expected 'local' or "
            "'ssh' (or remove the key to auto-detect from the host).")

    if str(key or '').strip().lower() == 'local':
        reason = "transport key 'local' in ~/.aview_hpc"
        chosen = 'local'
    elif str(key or '').strip().lower() == 'ssh':
        reason = "transport key 'ssh' in ~/.aview_hpc"
        chosen = 'ssh'
    elif _host_is_local(host):
        reason = f'host {host!r} resolves to this machine'
        chosen = 'local'
    else:
        reason = f'host {host!r} is not this machine'
        chosen = 'ssh'

    LOG.info(f'Chosen transport: {chosen} ({reason})')

    if chosen == 'local':
        return LocalTransport(remote_root=config.get('remote_tempdir'))
    return SSHTransport(host=host or '',
                        username=config.get('username'),
                        key_filename=config.get('key_filename'))
