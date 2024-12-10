import datetime
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict, List, Union

from adamspy.postprocess.msg import check_if_finished as check_if_msg_finished
from adamspy.postprocess.msg import get_errors
from .get_binary import get_binary


def submit(acf_file: Path,
           adm_file: Path = None,
           aux_files: List[Path] = None,
           wait_for_completion: bool = False,
           max_user_jobs: int = None,
           _log_level=None,
           **kwargs):
    """Submit an ACF file to the cluster

    Parameters
    ----------
    acf_file : Path
        The path to the ACF file to submit
    adm_file : Path, optional
        The path to the ADM file to submit, by default None

    Returns
    -------
    remote_dir : Path
        The remote directory where the files were submitted
    job_name : str
        The name of the job
    job_id : int
        The job ID
    """
    # Cast strings to Path
    acf_file = Path(acf_file)
    adm_file = Path(adm_file) if adm_file is not None else None
    aux_files = [Path(f) for f in aux_files] if aux_files is not None else None

    cmd = [f'"{get_binary()}"']

    if _log_level:
        cmd.extend(['--log_level', _log_level])

    cmd += ['submit', str(acf_file.name)]

    if adm_file is not None:
        cmd += ['--adm_file', str(adm_file.name)]
    if aux_files:
        cmd += ['--aux_files', *[f'"{f.name}"' for f in aux_files]]

    for k, v in kwargs.items():
        cmd += [f'--{k}', str(v)]

    if max_user_jobs:
        cmd += ['--max_user_jobs', str(max_user_jobs)]

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    with subprocess.Popen(' '.join(cmd),
                          startupinfo=startupinfo,
                          shell=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          cwd=acf_file.parent,
                          text=True) as proc:
        out, err = proc.communicate()

    if err:
        raise RuntimeError(err)

    output = json.loads(out)

    remote_dir = Path(output['remote_dir'])
    job_name = output['job_name']
    job_id = int(output['job_id'])

    if wait_for_completion:
        while True:
            check_if_finished(remote_dir)

    return remote_dir, job_name, job_id


def submit_multi(acf_files: List[Path],
                 adm_files: List[Path],
                 aux_files: List[List[Path]] = None,
                 max_user_jobs: int = None,
                 _log_level=None,
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
    Tuple[List[Path], List[str], List[int]]
        A list of tuples of remote directories, job names, and job IDs
    """
    if not len(adm_files) == len(acf_files):
        raise ValueError('The number of ADM files must match the number of ACF files')

    if aux_files is None:
        aux_files = [[]] * len(acf_files)

    cmd = [f'"{get_binary()}"']

    if _log_level:
        cmd.extend(['--log_level', _log_level])

    cmd += ['submit_multi']

    data = {'acf_file': [str(f) for f in acf_files],
            'adm_file': [str(f) for f in adm_files],
            'aux_files': [[str(f) for f in files] for files in aux_files]}

    with TemporaryDirectory() as tmpdir:
        Path(tmpdir, 'data.json').write_text(json.dumps(data, indent=4))

        cmd += [f'"{Path(tmpdir, "data.json")}"']

        for k, v in kwargs.items():
            cmd += [f'--{k}', str(v)]

        if max_user_jobs:
            cmd += ['--max-user-jobs', str(max_user_jobs)]

        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        with subprocess.Popen(' '.join(cmd),
                              startupinfo=startupinfo,
                              shell=True,
                              stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,
                              text=True) as proc:
            out, err = proc.communicate()

            # Wait for the process to finish
            proc.wait()

        if err:
            raise RuntimeError(err)

        output = json.loads(out)

        remote_dirs = [Path(d) for d in output['remote_dirs']]
        job_names = output['job_names']
        job_ids = [int(i) for i in output['job_ids']]

    return remote_dirs, job_names, job_ids


def _get_python_cmd(exe: Path):
    if exe.stem == 'aview':
        top_dir = next(p for p in exe.parents if p.name == 'aview').parent
        mdi = top_dir / 'mdi' if (top_dir / 'mdi').exists() else top_dir / 'common/mdi.bat'
        cmd = [f'"{mdi}"', 'python']

    else:
        cmd = [f'"{exe}"']

    return cmd


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


def check_if_finished_and_get_errors(remote_dir: Path,
                                     ignore_static: bool = False,
                                     ignore_parse: bool = False):
    with TemporaryDirectory() as tmpdir:
        try:
            msg_file = next(f for f in get_results(Path(remote_dir),
                                                   Path(tmpdir),
                                                   extensions=['.msg']))
        except StopIteration:
            finished = False
            errors = []

        else:
            finished = check_if_msg_finished(msg_file)
            errors: List[str] = get_errors(msg_file)

            if ignore_static:
                errors = [e for e in errors
                          if 'static equilibrium analysis has not been successful' not in e.lower()]

            if ignore_parse:
                errors = [e for e in errors
                          if 'errors found parsing command. command ignored.' not in e.lower()]

    return finished, errors


def get_remote_dir_status(remote_dir: Path) -> List[Dict[str, Union[str, int, Path]]]:
    cmd = [str(get_binary()), 'get_remote_dir_status', remote_dir.as_posix()]

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    with subprocess.Popen(cmd,
                          startupinfo=startupinfo,
                          shell=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          text=True) as proc:
        out, err = proc.communicate()

        # Wait for the process to finish
        proc.wait()

    if err:
        raise RuntimeError(err)

    status = json.loads(out)

    # convert types
    for s in status:
        s['modified'] = datetime.datetime.strptime(s['modified'], '%Y-%m-%dT%H:%M:%S')
        s['size'] = int(s['size'])
        s['file'] = Path(remote_dir) / s['name']

    return status


def get_results(remote_dir: Path, local_dir: Path, extensions=None, _log_level=None):
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
    cmd = [str(get_binary())]

    if _log_level:
        cmd.extend(['--log_level', _log_level])

    cmd += ['get_results', str(local_dir), Path(remote_dir).as_posix()]

    if extensions is not None:
        cmd.extend(['--extensions', ' '.join(extensions)])

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    with subprocess.Popen(cmd,
                          startupinfo=startupinfo,
                          shell=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          text=True) as proc:
        out, err = proc.communicate()

        # Wait for the process to finish
        proc.wait()

    if err:
        raise RuntimeError(err)

    output: List[str] = out.splitlines()

    return [Path(p) for p in output if p.strip()]


def get_binary_version():
    cmd = [str(get_binary()), 'version']

    with subprocess.Popen(cmd,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,
                          text=True) as proc:
        out, err = proc.communicate()

    if err:
        raise RuntimeError(err)

    return out.strip()
