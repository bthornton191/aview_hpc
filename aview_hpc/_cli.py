import argparse
import datetime
import json
import logging
import os
import re
import shutil
import sys
import time
import traceback as tb
from contextlib import contextmanager
from getpass import getpass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, Generator, List, Type, Union

import pandas as pd
from adamspy.postprocess.msg import check_if_finished as check_if_msg_finished

from .aview_hpc import get_binary_version
from .config import get_config, set_config
from .schedulers import get_scheduler
from .transport import SSHTransport, select_transport
from .version import version

RE_MODEL = re.compile(r'file/.*model[ \t]*=[ \t]*(.+)[ \t]*(?:,|$)', flags=re.I | re.MULTILINE)
RE_NTHREADS = re.compile(r'nthreads[ \t]*=[ \t]*(\d+)\b', flags=re.I)
LINUX_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
RES_EXTS = ('.res', '.req', '.gra', '.msg', '.out')
LOG = logging.getLogger(__name__)
SLEEP_TIME = 10

KEYRING_TIMEOUT_S = 10
"""How long a keyring password lookup may block before it is treated as
"no stored password" (see :func:`_keyring_get_password`)."""


def _keyring_get_password(service: str, username: str):
    """``keyring.get_password`` that cannot hang a headless session.

    On a headless POSIX session (Adams View in batch on a CI VM, no D-Bus
    session bus) the default ``keyring`` backend is SecretService, whose
    ``get_password`` blocks indefinitely waiting for a D-Bus reply that
    never comes -- there is no timeout anywhere in that stack. This was
    observed live on sjcvl-thornton (2026-10-08): a batch ``aview ru-st b``
    run sat for 15+ minutes inside ``HPCSession._connect`` before being
    killed manually.

    On Windows (and any backend that answers) this is a plain
    ``keyring.get_password`` call wrapped in a thread with a generous
    timeout; ``None`` (no stored password) is the fallback either way, and
    ``HPCSession._connect`` then uses SSH key authentication, so a hung or
    absent keyring degrades to key auth instead of a hang.

    The lookup runs in a DAEMON thread: if the backend never answers, the
    thread is abandoned (it cannot block interpreter exit) and ``None`` is
    returned after the timeout.
    """
    import threading

    result = {}

    def lookup():
        try:
            import keyring
            result['password'] = keyring.get_password(service, username)
        except Exception as err:  # no backend, locked keyring, ...
            LOG.warning(f'keyring lookup for {service}/{username} failed: {err}')
            result['password'] = None

    worker = threading.Thread(target=lookup, daemon=True)
    worker.start()
    worker.join(KEYRING_TIMEOUT_S)
    if worker.is_alive():
        LOG.warning(f'keyring lookup for {service}/{username} did not answer within '
                    f'{KEYRING_TIMEOUT_S}s (headless/no D-Bus session?). '
                    'Falling back to SSH key authentication.')
        return None
    return result.get('password')


class HPCSession():
    """A session with the HPC cluster"""

    def __init__(self,
                 host: str = None,
                 username: str = None,
                 job_name: str = None,
                 job_id: int = None,
                 remote_dir: Path = None,
                 remote_tempdir: Path = None,
                 submit_cmd: str = None,
                 scheduler: str = None):

        config = get_config()
        self.host = host or config.get('host', None)
        self.username = username or config.get('username', None)
        self.submit_cmd = submit_cmd or config.get('submit_cmd', None)
        self.scheduler = scheduler or config.get('scheduler') or 'slurm'
        self.backend = get_scheduler(self.scheduler)

        self.remote_tempdir = remote_tempdir or config.get('remote_tempdir', None)
        if self.remote_tempdir is not None:
            self.remote_tempdir = Path(self.remote_tempdir)

        self.remote_dir: Path = (Path(remote_dir)
                                 if remote_dir is not None else None)
        self.job_name: str = job_name
        self.job_id: int = job_id

        # Transport seam (t_652a2eff): local when running ON the submit
        # host, SSH otherwise.  select_transport logs which one and why.
        self.transport = select_transport(host=self.host)

        self.uploaded_files = {}

    def wait_for_user_jobs(self, max_user_jobs: int):

        while len(self.get_job_table().query(f'State in {self.backend.running_states}')) >= max_user_jobs:
            LOG.info(f'User {self.username} already has {max_user_jobs} jobs running. '
                     'Waiting 60 seconds and trying again...')
            time.sleep(60)

    def _connect(self):
        """Deprecated seam kept for the in-Aview job_monitor dashboard only.

        The transport refactor (t_652a2eff) moved connection handling into
        :class:`aview_hpc.transport.SSHTransport`.  The (un-shipped)
        job_monitor dashboard still reaches for ``session.ssh``/``session.ftp``
        directly; exposing the underlying paramiko objects here keeps that
        code working on the SSH path.  Local-mode sessions have no SSH
        connection at all, so the dashboard must not be used with
        ``transport: local``.
        """
        if self.transport.name != 'ssh':
            raise RuntimeError('session.ssh does not exist in local mode '
                               '(no SSH connection is made on the submit host)')
        assert isinstance(self.transport, SSHTransport)
        return self.transport.ssh, self.transport.ftp

    def get_results(self, local_dir: Path, extensions=None):
        """Get the results files from the cluster

        Parameters
        ----------
        dst : Path
            Local path to place files
        extensions : List[str], optional
            A list of file extensions to get (including the leading '.'), by default `RES_EXTS`

        Returns
        -------
        List[Path]
            A list of the files that were downloaded
        """
        if extensions is None:
            extensions = RES_EXTS

        remote_files = self.transport.listdir(self.remote_dir.as_posix())

        files = [Path(f) for f in remote_files if Path(f).suffix in extensions]
        for file in files:
            self.transport.get_file((self.remote_dir / str(file)).as_posix(),
                                    local_dir / file)

        return [local_dir / f for f in files]

    def submit(self,
               acf_file: Path,
               adm_file: Path = None,
               aux_files: List[Path] = None,
               _ignore_resubmit=False,
               **kwargs):
        """Submit an ACF file to the cluster

        Parameters
        ----------
        acf_file : Path
            The path to the ACF file to submit
        adm_file : Path, optional
            The path to the ADM file to submit, by default None
        """
        LOG.debug('`submit` called with the following arguments:')
        LOG.debug(f'   acf_file: {acf_file}')
        LOG.debug(f'   adm_file: {adm_file}')
        LOG.debug(f'   aux_files: {aux_files}')
        LOG.debug(f'   _ignore_resubmit: {_ignore_resubmit}')
        for k, v in kwargs.items():
            LOG.debug(f'   {k}: {v}')

        if self.job_name is not None and not _ignore_resubmit:
            raise RuntimeError('Please instantiate a new object to submit another job.')
        if aux_files is None:
            aux_files = []

        adm_file = adm_file or get_adm_from_acf(acf_file)
        self.job_name = acf_file.stem
        self.remote_dir = self.mkdtemp_remote(self.job_name)

        with TemporaryDirectory() as tmp_dir:

            acf_file_ = Path(tmp_dir) / acf_file.name
            shutil.copyfile(acf_file, acf_file_)

            if adm_file.parent != Path():
                # If the adm file is not in the current directory...
                remove_adm_path(acf_file_)

            elif acf_file.parent != Path():
                # If the acf file is not in the current directory...
                adm_file = acf_file.parent / adm_file.name

            adm_file_ = Path(tmp_dir) / adm_file.name
            shutil.copyfile(adm_file, adm_file_)

            aux_files_ = [Path(tmp_dir) / file.name for file in aux_files]
            for src, dst in zip(aux_files, aux_files_):
                shutil.copyfile(src, dst)

            LOG.info(f'Uploading files for {self.job_name}')
            for local_file, tmp_file in zip([acf_file, adm_file, *aux_files],
                                            [acf_file_, adm_file_, *aux_files_]):
                remote_file = (self.remote_dir / local_file.name).as_posix()

                size = local_file.stat().st_size
                if local_file not in self.uploaded_files:

                    LOG.info(f' Uploading: {local_file.as_posix():>100} '
                             f' ({size*1e-3:.1f} MB) '
                             f'--> {remote_file}')
                    self.transport.put_file(tmp_file, remote_file)
                    self.uploaded_files[local_file] = remote_file
                else:
                    # Copy the file that was already uploaded
                    LOG.info(f' Copying: {self.uploaded_files[local_file]:>100} '
                             f' ({size*1e-3:.1f} MB) '
                             f'--> {remote_file}')
                    self.transport.copy_file(self.uploaded_files[local_file],
                                             remote_file)

        cmd = [self.submit_cmd,
               (self.remote_dir / acf_file.name).as_posix()]

        for k, v in kwargs.items():
            cmd += [f'--{k}', str(v)]

        LOG.info('Running: ' + ' '.join(cmd))
        output, stderr = self.transport.exec_argv(cmd)
        LOG.info(f'Output: {output}')
        match = self.backend.re_submission_response.search(output)
        if match is None:
            raise RuntimeError(f'Could not submit {acf_file} to the cluster.\n'
                               f'Output: {output}.\n'
                               f'Error: {stderr}')

        self.job_id = int(match.group(1))

    def mkdtemp_remote(self, name=None, n_rand=4):
        """Create a temporary directory on the cluster"""
        return self.transport.mkdtemp(parent=self.remote_tempdir,
                                      name=name, n_rand=n_rand)

    def get_job_table(self, days=7):
        cmd = self.backend.job_table_command(days=days, username=self.username)
        stdout, stderr = self.transport.exec_shell(cmd)

        if stderr != '':
            raise RuntimeError(f'Error while getting job table: {stderr}')

        df = self.backend.parse_job_table(stdout)

        return df.sort_values('JobID')

    @property
    def last_update(self):
        """Get the last time the any file in `remote_dir` was updated"""
        cmd = 'ls -lt ' + ' '.join((Path(self.remote_dir) / f'*{ext}').as_posix() for ext in RES_EXTS)
        stdout, _ = self.transport.exec_shell(cmd)
        date = re.search(' +'.join([f'(?P<month>{"|".join(LINUX_MONTHS)})',
                                    r'(?P<day>\d{1,2})',
                                    r'(?P<hour>\d{2}):(?P<minute>\d{2})']),
                         stdout.splitlines()[0]).groupdict()

        last_updated_file = Path(stdout.splitlines()[0].split()[-1])

        date = {k: int(v) if k != 'month' else LINUX_MONTHS.index(v)+1 for k, v in date.items()}
        dt = datetime.datetime(year=datetime.datetime.now().year, **date)
        return dt, last_updated_file

    @property
    def dir_status(self):
        """Get a list of files and stat info"""
        cmd = 'ls -l --time-style=long-iso ' + self.remote_dir.as_posix()
        stdout, _ = self.transport.exec_shell(cmd)

        return [parse_ls_output(line) for line in stdout.splitlines()
                if line.strip() != '' and not line.startswith('total')]

    def get_job_messages(self):
        """Checks the files in the remote directory and returns a summary of the job

        The summary includes the following:
            - The last time the .res file was updated (if it exists)
            - The text of the .msg file (if it exists)
            - All data available in `get_job_table`

        Parameters
        ----------
        remote_dir : Path
            The remote directory of the job
        """
        with TemporaryDirectory() as tmpdir:
            local_files = self.get_results(Path(tmpdir), extensions=('.msg'))
            msg_file = next((f for f in local_files if f.suffix == '.msg'), None)
            msg = msg_file.read_text() if msg_file else ('No message file found '
                                                         f'in {self.remote_dir}')

        return msg

    def resubmit_job(self, remote_dir: Path, **kwargs):
        """Resubmit a in a given remote directory"""
        self.transport.exec_cleanup(self.backend, remote_dir)
        try:
            acf_file = remote_dir / Path(next(f for f in self.transport.listdir(remote_dir.as_posix())
                                              if f.endswith('.acf')))
        except StopIteration as err:
            raise StopIteration(f'No ACF file found in {remote_dir}') from err

        cmd = [self.submit_cmd, acf_file.as_posix()]
        for k, v in kwargs.items():
            cmd += [f'--{k}', str(v)]

        output, stderr = self.transport.exec_argv(cmd)

        LOG.info(f'Output: {output}')
        match = self.backend.re_submission_response.search(output)
        if match is None:
            raise RuntimeError(f'Could not submit {acf_file} to the cluster.\n'
                               f'Output: {output}.\n'
                               f'Error: {stderr}')

        self.job_id = int(match.group(1))
        self.remote_dir = remote_dir
        self.job_name = remote_dir.stem

    def close(self):
        self.transport.close()


def parse_ls_output(line: str) -> Dict[str, Union[str, int, datetime.datetime]]:
    """Parse the output of `ls -l --time-style=long-iso`"""
    re_file_info = re.compile(r'\s+'.join([r'(?P<permissions>[drwx\-]+)\.?',
                                           r'(?P<nlinks>\d+)',
                                           r'(?P<owner>[^\s]+)',
                                           r'(?P<group>[^\s]+)',
                                           r'(?P<size>\d+)',
                                           r'(?P<date>\d{4}-\d{2}-\d{2})',
                                           r'(?P<time>\d{2}:\d{2})',
                                           r'(?P<name>.+)']))

    match = re_file_info.match(line)
    if match is None:
        raise ValueError(f'Could not parse the following line: {line}')

    year, month, day = map(int, match.group('date').split('-'))
    hour, minute = map(int, match.group('time').split(':'))
    return {'name': match.group('name'),
            'permissions': match.group('permissions'),
            'nlinks': int(match.group('nlinks')),
            'owner': match.group('owner'),
            'group': match.group('group'),
            'size': int(match.group('size')),
            'modified': datetime.datetime(year, month, day, hour, minute)}


def remove_adm_path(acf_file: Path):
    """Remove the ADM file path from an ACF file

    Parameters
    ----------
    acf_file : Path
        The path to the ACF file to modify
    """
    text = acf_file.read_text()
    lines = text.splitlines()
    try:
        first_line = next(line for line in lines)
    except StopIteration as err:
        raise ValueError(f'{acf_file} has no contents!') from err

    if first_line.strip() != '':
        adm_file = Path(first_line.strip())
        acf_file.write_text('\n'.join([adm_file.name,
                                       *lines[1:]]))
    else:
        idx = next(i for i, l in enumerate(lines) if RE_MODEL.findall(l))
        adm_file = next(m for m in RE_MODEL.findall(lines[idx]))
        acf_file.write_text('\n'.join([*lines[:idx],
                                       lines[idx].replace(adm_file, Path(adm_file).name),
                                       *lines[idx+1:]]))


def get_adm_from_acf(acf_file: Path):
    text = acf_file.read_text()

    try:
        line = next(line for line in text.splitlines())
    except StopIteration as err:
        raise ValueError(f'{acf_file} has no contents!') from err

    if line.strip() != '':
        file = line.strip()

    else:

        try:
            file = next(m for m in RE_MODEL.findall(text))
        except StopIteration as err:
            raise ValueError(f'No model name was found in {acf_file}') from err

    adm_file = Path(file)
    if not adm_file.suffix:
        adm_file = adm_file.with_suffix('.adm')

    return adm_file


@contextmanager
def hpc_session(host=None,
                username=None,
                job_name=None,
                job_id=None,
                remote_dir=None) -> Generator[HPCSession, HPCSession, None]:

    # This will repeatedly try to connect to the HPC if there is a timeout (gives up after 24 hours).
    # paramiko is imported lazily: local mode (submit host) never makes an
    # SSH connection and must not import paramiko at all -- even when it is
    # installed (the deps target on the VM has it). The import happens only
    # when the failing exception itself came from paramiko, which means
    # paramiko is already in sys.modules.
    for _ in range(60*24):
        try:
            session = HPCSession(host, username, job_name, job_id, remote_dir)
            break
        except Exception as err:
            if type(err).__module__.split('.')[0] == 'paramiko':
                from paramiko import AuthenticationException
                if (isinstance(err, AuthenticationException)
                        and 'timeout' in err.args[0].lower()):
                    LOG.warning('Could not authenticate with the HPC. Retrying...')
                    time.sleep(60)
                    continue
            raise err

    try:
        yield session
    finally:
        session.close()


def submit(acf_file: Path,
           adm_file: Path = None,
           aux_files: List[Path] = None,
           host=None,
           username=None,
           max_user_jobs: int = None,
           **kwargs):
    """Submit an ACF file to the cluster

    Parameters
    ----------
    acf_file : Path
        The path to the ACF file to submit
    adm_file : Path, optional
        The path to the ADM file to submit, by default None
    aux_files : List[Path], optional
        A list of auxiliary files to submit, by default None

    Returns
    -------
    remote_dir : Path
        The remote directory where the files were submitted
    job_name : str
        The name of the job
    job_id : int
        The job ID
    """
    with hpc_session(host=host, username=username) as hpc:
        if max_user_jobs is not None:
            hpc.wait_for_user_jobs(max_user_jobs)

        hpc.submit(acf_file, adm_file, aux_files, **kwargs)
        return hpc.remote_dir, hpc.job_name, hpc.job_id


def submit_multi(acf_files: List[Path],
                 adm_files: List[Path],
                 aux_files: List[List[Path]] = None,
                 host=None,
                 username=None,
                 max_user_jobs: int = None,
                 **kwargs):
    """Submit multiple ACF files to the cluster

    Parameters
    ----------
    acf_files : List[Path]
        A list of ACF files to submit
    adm_files : List[Path], optional
        A list of ADM files to submit, by default None
    aux_files : List[List[Path]], optional
        A list of lists of auxiliary files to submit, by default None

    Returns
    -------
    List[Tuple[Path, str, int]]
        A list of tuples of remote directories, job names, and job IDs
    """
    if not len(adm_files) == len(acf_files):
        raise ValueError('The number of ADM files must match the number of ACF files')
    if aux_files is None:
        aux_files = [[]] * len(acf_files)

    # paramiko lazily: only needed when the transport actually SSHes
    # (a remote host); local mode (submit host) must not import it at all,
    # even when installed. Retry catches are built from the exception's
    # own module origin, same as hpc_session.
    retry_exceptions: tuple = (ConnectionResetError,)

    remote_dirs: List[Path] = []
    job_names: List[str] = []
    job_ids: List[int] = []
    with hpc_session(host=host, username=username) as hpc:
        for acf_file, adm_file, aux_file in zip(acf_files, adm_files, aux_files):

            # This is in a loop so that it can keep trying if there is a connection issue
            for i in range(N := 120):
                try:

                    if max_user_jobs is not None:
                        hpc.wait_for_user_jobs(max_user_jobs)

                    hpc.submit(acf_file, adm_file, aux_file, _ignore_resubmit=True, **kwargs)
                    remote_dirs.append(hpc.remote_dir)
                    job_names.append(hpc.job_name)
                    job_ids.append(hpc.job_id)

                except retry_exceptions as err:
                    # This may happen if the VPN disconnects
                    LOG.warning(f'Could not submit {acf_file} to the cluster. '
                                f'due to the following error: {err}')

                    if i < N-1:
                        # Keep Trying
                        LOG.warning('Waiting 60 seconds and trying again...')
                        time.sleep(60)

                    else:
                        # Waited long enough, raise the error
                        raise err
                else:
                    # If successful...
                    LOG.info(f'{acf_file} submitted.')
                    break

            LOG.info(f'Waiting {SLEEP_TIME} seconds before submitting the next job...')
            time.sleep(SLEEP_TIME)

    return remote_dirs, job_names, job_ids


def get_results(remote_dir: Path, local_dir: Path, host=None, username=None, extensions=None):
    """Get the results files from the cluster

    Parameters
    ----------
    job_name : str
        The name of the job
    job_id : int
        The job ID
    dst : Path
        Local path to place files
    extensions : List[str], optional
        A list of file extensionsto get (including the leading '.'), by default `RES_EXTS`

    Returns
    -------
    List[Path]
        A list of paths to the downloaded files
    """
    LOG.debug('`get_results` called with the following arguments:')
    LOG.debug(f'   remote_dir: {remote_dir}')
    LOG.debug(f'   local_dir: {local_dir}')
    LOG.debug(f'   host: {host}')
    LOG.debug(f'   username: {username}')
    LOG.debug(f'   extensions: {extensions}')

    with hpc_session(host=host,
                     username=username,
                     remote_dir=remote_dir) as hpc:
        files = hpc.get_results(local_dir, extensions)

    return files


def get_job_table(host=None, username=None) -> pd.DataFrame:
    with hpc_session(host=host, username=username) as hpc:
        df = hpc.get_job_table()

    return df


def get_job_messages(remote_dir: Path, host=None, username=None):
    with hpc_session(host=host, username=username, remote_dir=remote_dir) as hpc:
        msg = hpc.get_job_messages()

    return msg


def get_last_update(remote_dir: Path, host=None, username=None):
    with hpc_session(host=host, username=username, remote_dir=remote_dir) as hpc:
        last_update, last_file = hpc.last_update

    return last_update, last_file


def get_remote_dir_status(remote_dir: Path, host=None, username=None):
    with hpc_session(host=host, username=username, remote_dir=remote_dir) as hpc:
        status = hpc.dir_status

    return status


def resubmit_job(remote_dir: Path, host=None, username=None, **kwargs):
    with hpc_session(host=host, username=username) as hpc:
        hpc.resubmit_job(remote_dir, **kwargs)
        return hpc.remote_dir, hpc.job_name, hpc.job_id


def check_if_finished(remote_dir: Path):
    with TemporaryDirectory() as tmpdir:
        try:
            msg_file = next(f for f in get_results(remote_dir,
                                                   Path(tmpdir),
                                                   extensions=['.msg']))
        except StopIteration:
            finished = False

        else:
            finished = check_if_msg_finished(msg_file)

    return finished


def excepthook(exc_type: Type[Exception], exc_value: Exception, exc_tb: List[str]):
    """Print traceback to stderr"""
    print(''.join(tb.format_exception(exc_type, exc_value, exc_tb)), file=sys.stderr)


@contextmanager
def cwd_as(cwd: Path):
    cwd_ = Path.cwd()
    os.chdir(cwd)

    try:
        yield
    finally:
        os.chdir(cwd_)


def main():
    sys.excepthook = excepthook

    parser = argparse.ArgumentParser(
        prog='aview_hpc',
        description='Submits an ACF file to the HPC cluster')

    parser.add_argument('--log_level',
                        type=str,
                        default='INFO',
                        help=argparse.SUPPRESS,
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'])

    subparsers = parser.add_subparsers(title='command',
                                       required=True,
                                       dest='command',
                                       description='Available subcommands')

    # ----------------------------------------------------------------------------------------------
    # Submit
    # ----------------------------------------------------------------------------------------------
    submit_parser = subparsers.add_parser('submit', help='Submit an ACF file to the cluster')
    submit_parser.add_argument('acf_file', type=Path, help='The ACF file to submit')
    submit_parser.add_argument('--adm_file', type=Path, help='The ADM file to submit')
    submit_parser.add_argument('--aux_files', '-a',
                               type=Union[Path, None],
                               nargs='+',
                               help='Auxiliary files to submit',
                               default=None)
    submit_parser.add_argument('--max-user-jobs', '-M',
                               type=int,
                               help=('A self imposed maximum number of jobs this user can '
                                     'have running at once.'),
                               default=None)
    submit_parser.set_defaults(command='submit')

    # ----------------------------------------------------------------------------------------------
    # Submit Multi
    # ----------------------------------------------------------------------------------------------
    submit_multi_parser = subparsers.add_parser('submit_multi', help='Submit an ACF file to the cluster')
    submit_multi_parser.add_argument('batch_file',
                                     type=Path,
                                     help='A batch file to submit')
    submit_multi_parser.add_argument('--host', '-H',
                                     type=str,
                                     help='The host to connect to',
                                     default=None)
    submit_multi_parser.add_argument('--username', '-u',
                                     type=str,
                                     help='The username to connect with',
                                     default=None)
    submit_multi_parser.add_argument('--max-user-jobs', '-M',
                                     type=int,
                                     help=('A self imposed maximum number of jobs this user can '
                                           'have running at once.'),
                                     default=None)
    submit_multi_parser.set_defaults(command='submit_multi')

    # ----------------------------------------------------------------------------------------------
    # Get results
    # ----------------------------------------------------------------------------------------------
    get_results_parser = subparsers.add_parser('get_results', help='Get the results files from the cluster')
    get_results_parser.add_argument('local_dir', type=Path, help='Local path to place files')
    get_results_parser.add_argument('remote_dir', type=Path, help='The remote directory of the job')
    get_results_parser.add_argument('--extensions', '-e',
                                    type=str,
                                    nargs='+',
                                    default=RES_EXTS,
                                    help='File extensions to get (including the leading \'.\')')
    get_results_parser.add_argument('--host', '-H',
                                    type=str,
                                    help='The host to connect to',
                                    default=None)
    get_results_parser.add_argument('--username', '-u',
                                    type=str,
                                    help='The username to connect with',
                                    default=None)
    get_results_parser.set_defaults(command='get_results')

    # ----------------------------------------------------------------------------------------------
    # Get Remote Dir Status
    # ----------------------------------------------------------------------------------------------
    get_remote_dir_status_parser = subparsers.add_parser('get_remote_dir_status',
                                                         help='Get a list of files and stat info')
    get_remote_dir_status_parser.add_argument('remote_dir',
                                              type=Path,
                                              help='The remote directory of the job')
    get_remote_dir_status_parser.add_argument('--host', '-H',
                                              type=str,
                                              help='The host to connect to',
                                              default=None)
    get_remote_dir_status_parser.add_argument('--username', '-u',
                                              type=str,
                                              help='The username to connect with',
                                              default=None)
    get_remote_dir_status_parser.set_defaults(command='get_remote_dir_status')

    # ----------------------------------------------------------------------------------------------
    # Set Config
    # ----------------------------------------------------------------------------------------------
    set_config_parser = subparsers.add_parser('set_config', help='Set the HPC config')
    set_config_parser.add_argument('--host', '-H',
                                   type=str,
                                   default=None,
                                   help='The host to connect to')
    set_config_parser.add_argument('--username', '-u',
                                   type=str,
                                   default=None,
                                   help='The username to connect with')
    set_config_parser.add_argument('--remote_tempdir', '-r',
                                   type=Path,
                                   default=None,
                                   help='A directory on the host to use for temporary files')
    set_config_parser.add_argument('--scheduler',
                                   type=str,
                                   default=None,
                                   help='The job scheduler on the host (slurm or lsf)')
    set_config_parser.add_argument('--key_filename',
                                   type=str,
                                   default=None,
                                   help='Path to an SSH private key used when no keyring password is set')
    set_config_parser.add_argument('--transport',
                                   type=str,
                                   default=None,
                                   help=('How to reach the scheduler host: ssh (paramiko) or '
                                         'local (run directly -- only when this machine IS '
                                         'the submit host). Default: auto-detect from host.'))
    set_config_parser.set_defaults(command='set_config')

    # ----------------------------------------------------------------------------------------------
    # Get Config
    # ----------------------------------------------------------------------------------------------
    get_config_parser = subparsers.add_parser('get_config', help='Get the HPC config')
    get_config_parser.set_defaults(command='get_config')

    # ----------------------------------------------------------------------------------------------
    # Get Binary
    # ----------------------------------------------------------------------------------------------
    get_binary_parser = subparsers.add_parser('get_binary',
                                              help='Get the binary')
    get_binary_parser.set_defaults(command='get_binary')

    # ----------------------------------------------------------------------------------------------
    # Version
    # ----------------------------------------------------------------------------------------------
    version_parser = subparsers.add_parser('version',
                                           help='Get the version of the binary')
    version_parser.set_defaults(command='version')
    version_parser.add_argument('--binary', action='store_true', help='Get the version of the binary')

    # ----------------------------------------------------------------------------------------------
    # Get Job Table
    # ----------------------------------------------------------------------------------------------
    get_job_table_parser = subparsers.add_parser('get_job_table',
                                                 help='Get the job table')
    get_job_table_parser.set_defaults(command='get_job_table')

    # ----------------------------------------------------------------------------------------------
    # Resubmit Job
    # ----------------------------------------------------------------------------------------------
    get_job_table_parser = subparsers.add_parser('resubmit_job',
                                                 help='Resubmit a job in a given remote directory')
    get_job_table_parser.set_defaults(command='resubmit_job')
    get_job_table_parser.add_argument('remote_dir',
                                      type=Path,
                                      help='The remote directory of the job')

    # ----------------------------------------------------------------------------------------------
    # Parse the arguments
    # ----------------------------------------------------------------------------------------------
    # Handle unknown arguments
    known, unknown = parser.parse_known_args()
    for arg in (a for a in unknown if a.startswith('-')):
        parser._get_positional_actions()[0].choices[known.command].add_argument(arg.split('=')[0], type=str)

    args = vars(parser.parse_args())

    LOG.info(f'Arguments: {args}')
    command = args.pop('command')
    logging.getLogger().setLevel(args.pop('log_level'))

    # Convert all Path objects to absolute paths
    for k, v in args.items():
        if isinstance(v, Path) and 'remote' not in k:
            args[k] = v.resolve().absolute()

    # ----------------------------------------------------------------------------------------------
    # submit()
    # ----------------------------------------------------------------------------------------------
    if command == 'submit':
        REMOTE_DIR, JOB_NAME, JOB_ID = submit(**args)
        print(json.dumps({'remote_dir': REMOTE_DIR.as_posix(),
                          'job_name': JOB_NAME,
                          'job_id': JOB_ID}))

    # ----------------------------------------------------------------------------------------------
    # submit_multi()
    # ----------------------------------------------------------------------------------------------
    elif command == 'submit_multi':

        batch_file = args.pop('batch_file')
        data = json.loads(Path(batch_file).read_text())
        acf_files = [Path(f) for f in data['acf_file']]
        adm_files = [Path(f) for f in data['adm_file']]
        aux_files = [[Path(f) for f in files] for files in data['aux_files']]

        REMOTE_DIRS, JOB_NAMES, JOB_IDS = submit_multi(acf_files=acf_files,
                                                       adm_files=adm_files,
                                                       aux_files=aux_files,
                                                       **args)
        print(json.dumps({'remote_dirs': [d.as_posix() for d in REMOTE_DIRS],
                          'job_names': JOB_NAMES,
                          'job_ids': JOB_IDS}))

    # ----------------------------------------------------------------------------------------------
    # get_remote_dir_status()
    # ----------------------------------------------------------------------------------------------
    elif command == 'get_remote_dir_status':
        STATUS = get_remote_dir_status(**args)

        # Convert datetime objects to strings
        STATUS = [{k: v.strftime('%G-%m-%dT%H:%M:%S')
                   if isinstance(v, datetime.datetime) else v
                   for k, v in status.items()} for status in STATUS]
        print(json.dumps(STATUS))

    # ----------------------------------------------------------------------------------------------
    # get_results()
    # ----------------------------------------------------------------------------------------------
    elif command == 'get_results':
        FILES = get_results(**args)
        print('\n'.join([str(f) for f in FILES]))

    # ----------------------------------------------------------------------------------------------
    # set_config()
    # ----------------------------------------------------------------------------------------------
    elif command == 'set_config':
        if args.get('scheduler') is not None:
            try:
                get_scheduler(args['scheduler'])
            except ValueError as err:
                print(f'Error: {err}', file=sys.stderr)
                sys.exit(2)

        if args.get('transport') is not None \
                and str(args['transport']).strip().lower() not in ('local', 'ssh'):
            print(f"Error: invalid transport {args['transport']!r}: expected 'local' or 'ssh'.",
                  file=sys.stderr)
            sys.exit(2)

        if args['username'] is not None and args['host'] is not None:
            password = getpass(f'Enter password for {args["username"]}@{args["host"]} '
                               'or press enter to skip:')
            args['password'] = password if password.strip() != '' else None

        set_config(**args)

    # ----------------------------------------------------------------------------------------------
    # get_config()
    # ----------------------------------------------------------------------------------------------
    elif command == 'get_config':
        CONFIG = get_config()
        print('\n'.join([f'{k}={v}' for k, v in CONFIG.items()]))

    # ----------------------------------------------------------------------------------------------
    # get_binary
    # ----------------------------------------------------------------------------------------------
    elif command == 'get_binary':
        from .get_binary import get_binary
        binary = get_binary()
        print(binary)

    # ----------------------------------------------------------------------------------------------
    # version
    # ----------------------------------------------------------------------------------------------
    elif command == 'version':
        if args['binary']:
            print(get_binary_version())
        else:
            print(version)

    # ----------------------------------------------------------------------------------------------
    # get_job_table
    # ----------------------------------------------------------------------------------------------
    elif command == 'get_job_table':
        df = get_job_table()

        # Print the dataframe as a csv
        print(df.to_csv(index=False))

    # ----------------------------------------------------------------------------------------------
    # resubmit_job
    # ----------------------------------------------------------------------------------------------
    elif command == 'resubmit_job':

        REMOTE_DIR, JOB_NAME, JOB_ID = resubmit_job(**args)
        print(json.dumps({'remote_dir': REMOTE_DIR.as_posix(),
                          'job_name': JOB_NAME,
                          'job_id': JOB_ID}))


if __name__ == '__main__':
    main()
