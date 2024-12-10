import argparse
import datetime
import json
import logging
import os
import re
import shutil
import socket
import sys
import time
import traceback as tb
from contextlib import contextmanager
from getpass import getpass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, Generator, List, Type, Union

import keyring
import pandas as pd
from paramiko import AuthenticationException, AutoAddPolicy, SSHClient, SSHException

from .aview_hpc import get_binary_version, resubmit_job
from .config import get_config, set_config
from .get_binary import get_binary
from .version import version

RE_SUBMISSION_RESPONSE = re.compile(r'.*submitted batch job (\d+)\w*', flags=re.I)
RE_MODEL = re.compile(r'file/.*model[ \t]*=[ \t]*(.+)[ \t]*(?:,|$)', flags=re.I | re.MULTILINE)
RE_NTHREADS = re.compile(r'nthreads[ \t]*=[ \t]*(\d+)\b', flags=re.I)
LINUX_MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
RES_EXTS = ('.res', '.req', '.gra', '.msg', '.out')
LOG = logging.getLogger(__name__)
JOB_TABLE_COLUMNS = ['jobid',
                     'jobname%-40',
                     'start',
                     'end',
                     'Elapsed',
                     'state',
                     'timelimit',
                     'nnodes',
                     'ncpus',
                     'submitline%-70',
                     'workdir%-70']
SLEEP_TIME = 10


class HPCSession():
    """A session with the HPC cluster"""

    def __init__(self,
                 host: str = None,
                 username: str = None,
                 job_name: str = None,
                 job_id: int = None,
                 remote_dir: Path = None,
                 remote_tempdir: Path = None,
                 submit_cmd: str = None):

        config = get_config()
        self.host = host or config.get('host', None)
        self.username = username or config.get('username', None)
        self.submit_cmd = submit_cmd or config.get('submit_cmd', None)

        self.remote_tempdir = remote_tempdir or config.get('remote_tempdir', None)
        if self.remote_tempdir is not None:
            self.remote_tempdir = Path(self.remote_tempdir)

        self.remote_dir: Path = (Path(remote_dir)
                                 if remote_dir is not None else None)
        self.job_name: str = job_name
        self.job_id: int = job_id

        self.ssh, self.ftp = self._connect()

        self.uploaded_files = {}

    def wait_for_user_jobs(self, max_user_jobs: int):

        while len(self.get_job_table().query('State=="RUNNING"')) >= max_user_jobs:
            LOG.info(f'User {self.username} already has {max_user_jobs} jobs running. '
                     'Waiting 60 seconds and trying again...')
            time.sleep(60)

    def _connect(self):
        ssh = SSHClient()
        ssh.set_missing_host_key_policy(AutoAddPolicy())

        try:
            ssh.connect(self.host,
                        username=self.username,
                        password=keyring.get_password('aview_hpc', self.username))
        except socket.gaierror as err:
            raise socket.gaierror(f'Could not connect to {self.host}. '
                                  'Do you need to be on a VPN?') from err
        ftp = ssh.open_sftp()

        return ssh, ftp

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

        try:
            remote_files = self.ftp.listdir(self.remote_dir.as_posix())
        except FileNotFoundError as err:
            raise FileNotFoundError(f'Could not find remote directory {self.remote_dir}') from err

        files = [Path(f) for f in remote_files if Path(f).suffix in extensions]
        for file in files:
            self.ftp.get((self.remote_dir / str(file)).as_posix(), local_dir / file)

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
                    self.ftp.put(tmp_file, remote_file)
                    self.uploaded_files[local_file] = remote_file
                else:
                    # Copy the file that was already uploaded
                    LOG.info(f' Copying: {self.uploaded_files[local_file]:>100} '
                             f' ({size*1e-3:.1f} MB) '
                             f'--> {remote_file}')
                    self.ssh.exec_command(f'cp {self.uploaded_files[local_file]} {remote_file}')

        cmd = [self.submit_cmd,
               (self.remote_dir / acf_file.name).as_posix()]

        for k, v in kwargs.items():
            cmd += [f'--{k}', str(v)]

        LOG.info('Running: ' + ' '.join(cmd))
        _, stdout, stderr = self.ssh.exec_command(' '.join(cmd))
        output = stdout.read().decode()
        LOG.info(f'Output: {output}')
        if not RE_SUBMISSION_RESPONSE.match(output):
            raise RuntimeError(f'Could not submit {acf_file} to the cluster.\n'
                               f'Output: {output}.\n'
                               f'Error: {stderr.read().decode()}')

        self.job_id = int(RE_SUBMISSION_RESPONSE.match(output).group(1))

    def mkdtemp_remote(self, name=None, n_rand=4):
        """Create a temporary directory on the cluster"""
        cmd = 'mktemp -d'
        if self.remote_tempdir:
            cmd += f' -p {self.remote_tempdir.as_posix()}'
        if name:
            cmd += f' {name}.' + 'X' * n_rand

        _, stdout, _ = self.ssh.exec_command(cmd)
        remote_dir = Path(stdout.read().decode().strip())
        _,  stdout, stderr = self.ssh.exec_command(f'chmod 775 {remote_dir.as_posix()}')

        if stderr.read().decode() != '' or stdout.read().decode() != '':
            raise RuntimeError(f'Could not set permissions on {remote_dir}.\n'
                               f'Error: {stderr.read().decode()}')

        return remote_dir

    def get_job_table(self, days=7):
        cmd = ['sacct',
               f'-S now-{days:.0f}days',
               '-X',
               '-P',
               '--delimiter=,',
               '-o',
               ','.join(JOB_TABLE_COLUMNS)]
        _,  stdout, stderr = self.ssh.exec_command(' '.join(cmd))

        stderr = stderr.read().decode()
        if stderr != '':
            raise RuntimeError(f'Error while getting job table: {stderr}')

        df = pd.read_csv(stdout, delimiter=',')
        return df.assign(
            JobName=df['JobName'].str.replace('.slurm', ''),
            Elapsed=df['Elapsed'].str.replace('Unknown', '00:00:00'),
            End=pd.to_datetime(df['End'].str.replace('Unknown', '')).dt.strftime('%G-%m-%dT%H:%M:%S'),
            Start=pd.to_datetime(df['Start'].str.replace('Unknown', '')).dt.strftime('%G-%m-%dT%H:%M:%S'),
        )

    @property
    def last_update(self):
        """Get the last time the any file in `remote_dir` was updated"""
        cmd = 'ls -lt ' + ' '.join((Path(self.remote_dir) / f'*{ext}').as_posix() for ext in RES_EXTS)
        _, stdout, _ = self.ssh.exec_command(cmd)
        stdout = stdout.read().decode()
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
        _, stdout, _ = self.ssh.exec_command(cmd)

        return [parse_ls_output(line) for line in stdout.read().decode().splitlines()
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

    def resubmit_job(self, remote_dir: Path):
        """Resubmit a in a given remote directory"""
        self.ssh.exec_command(f'rm {remote_dir.as_posix()}/*.slurm')
        try:
            acf_file = Path(next((f for f in self.ftp.listdir(remote_dir.as_posix())
                                  if f.endswith('.acf'))))
        except StopIteration as err:
            raise StopIteration(f'No ACF file found in {remote_dir}') from err

        _, stdout, stderr = self.ssh.exec_command(f'{self.submit_cmd} {acf_file.as_posix()}')
        output = stdout.read().decode()

        LOG.info(f'Output: {output}')
        if not RE_SUBMISSION_RESPONSE.match(output):
            raise RuntimeError(f'Could not submit {acf_file} to the cluster.\n'
                               f'Output: {output}.\n'
                               f'Error: {stderr.read().decode()}')

        self.job_id = int(RE_SUBMISSION_RESPONSE.match(output).group(1))
        self.remote_dir = remote_dir
        self.job_name = remote_dir.stem

    def close(self):
        self.ssh.close()
        self.ftp.close()


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

    # This will repeatedly try to connect to the HPC if there is a timeout (gives up after 24 hours)
    for _ in range(60*24):
        try:
            session = HPCSession(host, username, job_name, job_id, remote_dir)
            break
        except AuthenticationException as err:
            if 'timeout' in err.args[0].lower():
                msg = 'Could not authenticate with the HPC. Retrying...'
                LOG.warning(msg)
                time.sleep(60)
            else:
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

                except (SSHException, ConnectionResetError) as err:
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
        resubmit_job(args['remote_dir'])


if __name__ == '__main__':
    main()
