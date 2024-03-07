import logging
import argparse
import json
import os
import re
import shutil
import socket
import sys
import traceback as tb
from contextlib import contextmanager
from getpass import getpass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Generator, List, Type, Union

import keyring
import pandas as pd
from paramiko import AutoAddPolicy, SSHClient

RE_SUBMISSION_RESPONSE = re.compile(r'.*submitted batch job (\d+)\w*', flags=re.I)
RE_MODEL = re.compile(r'file/.*model[ \t]*=[ \t]*(.+)[ \t]*(?:,|$)', flags=re.I | re.MULTILINE)
RE_NTHREADS = re.compile(r'nthreads[ \t]*=[ \t]*(\d+)\b', flags=re.I)

RES_EXTS = ('.res', '.req', '.gra', '.msg', '.out')
CONFIG_FILE = Path.home() / '.aview_hpc'

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


class HPCSession():
    """A session with the HPC cluster"""

    def __init__(self,
                 host: str = None,
                 username: str = None,
                 job_name: str = None,
                 job_id: int = None,
                 remote_dir: Path = None,
                 remote_tempdir: Path = None):

        config = get_config()
        self.host = host or config.get('host', None)
        self.username = username or config.get('username', None)

        self.remote_tempdir = remote_tempdir or config.get('remote_tempdir', None)
        if self.remote_tempdir is not None:
            self.remote_tempdir = Path(self.remote_tempdir)

        self.remote_dir: Path = (Path(remote_dir)
                                 if remote_dir is not None else None)
        self.job_name: str = job_name
        self.job_id: int = job_id

        self.ssh, self.ftp = self._connect()

        self.uploaded_files = {}

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
               mins: int = None,
               tmp_dir: Path = None,
               _ignore_resubmit=False):
        """Submit an ACF file to the cluster

        Parameters
        ----------
        acf_file : Path
            The path to the ACF file to submit
        adm_file : Path, optional
            The path to the ADM file to submit, by default None
        """
        LOG.debug('`get_results` called with the following arguments:')
        LOG.debug(f'   acf_file: {acf_file}')
        LOG.debug(f'   adm_file: {adm_file}')
        LOG.debug(f'   aux_files: {aux_files}')
        LOG.debug(f'   mins: {mins}')
        LOG.debug(f'   tmp_dir: {tmp_dir}')
        LOG.debug(f'   _ignore_resubmit: {_ignore_resubmit}')

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

            for local_file, tmp_file in zip([acf_file, adm_file, *aux_files],
                                            [acf_file_, adm_file_, *aux_files_]):
                remote_file = (self.remote_dir / local_file.name).as_posix()

                if local_file not in self.uploaded_files:
                    self.ftp.put(tmp_file, remote_file)
                    self.uploaded_files[local_file] = remote_file
                else:
                    # Copy the file that was already uploaded
                    self.ssh.exec_command(f'cp {self.uploaded_files[local_file]} {remote_file}')

        # TODO: Make this command configurable
        cmd = ['~/scripts/asub.py',
               (self.remote_dir / acf_file.name).as_posix()]
        if mins:
            cmd += ['--mins', str(mins)]

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

        return pd.read_csv(stdout, delimiter=',')

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

    def close(self):
        self.ssh.close()
        self.ftp.close()


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
        first_line = next(l for l in lines)
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
        line = next(l for l in text.splitlines())
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
    session = HPCSession(host, username, job_name, job_id, remote_dir)
    try:
        yield session
    finally:
        session.close()


def get_config():
    """Get the configuration for the HPC cluster"""
    if not CONFIG_FILE.exists():
        config = {}

    else:
        with open(CONFIG_FILE) as f:
            config = json.load(f)

    return config


def set_config(host=None, username=None, password=None, **kwargs):
    """Set the configuration for the HPC cluster"""
    config = get_config()
    config['host'] = host or config.get('host', None)
    config['username'] = username or config.get('username', None)

    # All other kwargs
    for k, v in kwargs.items():
        config[k] = v

    if password is not None and config['username'] is not None:
        keyring.set_password('aview_hpc', config['username'], password)
    elif password is not None:
        raise ValueError('A username must be provided to set a password')

    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=4)


def submit(acf_file: Path,
           adm_file: Path = None,
           aux_files: List[Path] = None,
           mins: int = None,
           host=None,
           username=None):
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
        hpc.submit(acf_file, adm_file, aux_files, mins=mins)

        return hpc.remote_dir, hpc.job_name, hpc.job_id


def submit_multi(acf_files: List[Path],
                 adm_files: List[Path],
                 aux_files: List[List[Path]] = None,
                 mins: int = None,
                 host=None,
                 username=None):
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
            hpc.submit(acf_file, adm_file, aux_file, mins=mins, _ignore_resubmit=True)
            remote_dirs.append(hpc.remote_dir)
            job_names.append(hpc.job_name)
            job_ids.append(hpc.job_id)

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
    submit_parser.add_argument('--mins', '-m',
                               type=int,
                               help='The number of minutes to allocate for the job',
                               default=None)
    submit_parser.set_defaults(command='submit')

    # ----------------------------------------------------------------------------------------------
    # Submit Multi
    # ----------------------------------------------------------------------------------------------
    submit_multi_parser = subparsers.add_parser('submit_multi', help='Submit an ACF file to the cluster')
    submit_multi_parser.add_argument('batch_file',
                                     type=Path,
                                     help='A batch file to submit')
    submit_multi_parser.add_argument('--mins', '-m',
                                     type=int,
                                     help='The number of minutes to allocate for the job',
                                     default=None)
    submit_multi_parser.add_argument('--host', '-H',
                                     type=str,
                                     help='The host to connect to',
                                     default=None)
    submit_multi_parser.add_argument('--username', '-u',
                                     type=str,
                                     help='The username to connect with',
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
    # Parse the arguments
    # ----------------------------------------------------------------------------------------------
    args = vars(parser.parse_args(sys.argv[1:]))

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


if __name__ == '__main__':
    main()
