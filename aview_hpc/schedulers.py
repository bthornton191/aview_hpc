"""Scheduler backends for aview_hpc.

A backend encapsulates everything that is specific to a job scheduler
(Slurm, LSF, ...) so that :mod:`aview_hpc._cli` can stay scheduler
agnostic:

* the regex used to extract the job id from the submit command's output
* the command used to print the job table and the parser for its output
* the vocabulary of "running" states (after normalization)
* the command that cleans up scheduler script files before a resubmit

Backends are selected with the ``scheduler`` key in the aview_hpc config
file (``slurm`` (the default, for backwards compatibility) or ``lsf``).
"""

import json
import re
from io import StringIO
from pathlib import Path
from typing import List, Optional

import pandas as pd


class SchedulerBackend:
    """Interface for scheduler-specific behavior."""

    name: str

    #: Regex with a single group: the job id. Searched (with `re.search`)
    #: against the combined output of the submit command.
    re_submission_response: 're.Pattern'

    #: Job states (after normalization) that count as "running" for
    #: ``wait_for_user_jobs``.
    running_states: List[str]

    #: Mapping of raw scheduler states to the normalized vocabulary.
    #: Backends whose scheduler already uses the normalized vocabulary
    #: (slurm) leave this empty and pass states through unchanged.
    STATE_MAP: dict = {}

    def job_table_command(self, days: int = 7, username: Optional[str] = None) -> str:
        """Return the command (string) that prints the job table."""
        raise NotImplementedError

    def parse_job_table(self, text: str) -> pd.DataFrame:
        """Parse the job table command's stdout into a DataFrame."""
        raise NotImplementedError

    def cleanup_command(self, remote_dir: Path) -> str:
        """Return the command that removes scheduler files before a resubmit."""
        raise NotImplementedError


class SlurmBackend(SchedulerBackend):
    """The historical aview_hpc behavior (Hexagon/Romax DEWET cluster)."""

    name = 'slurm'
    re_submission_response = re.compile(r'.*submitted batch job (\d+)\w*', flags=re.I)
    running_states = ['RUNNING']

    #: Raw `sacct -o` field specifiers (NOT the DataFrame column names --
    #: sacct capitalizes these itself, e.g. jobid -> JobID).
    SACCT_O_FIELDS = ['jobid',
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

    def job_table_command(self, days: int = 7, username: Optional[str] = None) -> str:
        # `username` is unused: the historical command did not filter by user.
        return ' '.join(['sacct',
                         f'-S now-{days:.0f}days',
                         '-X',
                         '-P',
                         '--delimiter=,',
                         '-o',
                         ','.join(self.SACCT_O_FIELDS)])

    def parse_job_table(self, text: str) -> pd.DataFrame:
        df = pd.read_csv(StringIO(text), delimiter=',')
        df = df.assign(
            JobName=df['JobName'].str.replace('.slurm', ''),
            Elapsed=df['Elapsed'].str.replace('Unknown', '00:00:00'),
            End=pd.to_datetime(df['End'].str.replace('Unknown', '')).dt.strftime('%G-%m-%dT%H:%M:%S'),
            Start=pd.to_datetime(df['Start'].str.replace('Unknown', '')).dt.strftime('%G-%m-%dT%H:%M:%S'),
        )

        return df

    def cleanup_command(self, remote_dir: Path) -> str:
        return f'rm {Path(remote_dir).as_posix()}/*.slurm'


def _lsf_seconds_to_hms(value) -> str:
    """Convert an LSF ``run_time`` value ("57208 second(s)") to Slurm's
    ``Elapsed`` format (``HH:MM:SS`` or ``D-HH:MM:SS``). Empty -> 00:00:00."""
    match = re.match(r'(\d+)', str(value or ''))
    seconds = int(match.group(1)) if match else 0
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days:
        return f'{days}-{hours:02d}:{minutes:02d}:{seconds:02d}'
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def _lsf_minutes_to_hms(value) -> str:
    """Convert an LSF ``runtimelimit`` value (minutes, "2400.0") to Slurm's
    ``Timelimit`` format. Empty -> ''."""
    match = re.match(r'(\d+(?:\.\d+)?)', str(value or ''))
    if not match:
        return ''
    return _lsf_seconds_to_hms(int(round(float(match.group(1)))) * 60)


class LSFBackend(SchedulerBackend):
    """Backend for IBM Spectrum LSF, verified against the Cadence ``sjlsf01``
    farm (LSF 10.1.0.15) on 2026-10-01.

    Notes on the job table:

    * ``bjobs`` is called via its absolute site-wrapper path because the LSF
      environment is not guaranteed to be on ``PATH`` for a non-interactive
      SSH exec.
    * ``-a`` includes pending, running, suspended and *recently finished*
      jobs. Finished jobs age out of ``bjobs`` after the cluster's
      ``CLEAN_PERIOD`` (commonly on the order of an hour). The ``days``
      argument is accepted for API compatibility with the slurm backend but
      cannot extend that window; history older than the clean period
      requires ``bhist -a -l`` and is NOT included. Consumers that treat
      "absent from the table" as a signal (e.g. CDM's
      ``job_absent_from_table`` grace logic) must account for this.
    * ``-json`` is used because ``bjobs -o "... delimiter=','"`` is rejected
      by this LSF version ("The delimiter in the format string is
      invalid").
    * ``-u`` is intentionally not passed, so bjobs reports only the
      invoking user's jobs. This matches what consumers need and keeps the
      response small (``-u all`` returns >160k records on this farm).
    """

    name = 'lsf'
    BJOBS = '/grid/sfi/farm/bin/bjobs'
    JOB_TABLE_FIELDS = ('jobid job_name stat submit_time start_time '
                        'finish_time run_time runtimelimit nthreads '
                        'command exec_cwd')
    JOB_TABLE_COLUMNS = ['JobID', 'JobName', 'Start', 'End', 'Elapsed',
                         'State', 'Timelimit', 'NNodes', 'NCPUs',
                         'SubmitLine', 'WorkDir']
    STATE_MAP = {'PEND': 'PENDING',
                 'RUN': 'RUNNING',
                 'DONE': 'COMPLETED',
                 'EXIT': 'FAILED',
                 'PSUSP': 'SUSPENDED',
                 'USUSP': 'SUSPENDED',
                 'SSUSP': 'SUSPENDED',
                 'UNKWN': 'UNKNOWN'}
    re_submission_response = re.compile(r'Job <(\d+)> is submitted', flags=re.I)
    running_states = ['RUNNING']

    def job_table_command(self, days: int = 7, username: Optional[str] = None) -> str:
        # `days` cannot be applied -- see the class docstring. `username` is
        # not passed: without -u, bjobs reports the invoking user's jobs.
        return f'{self.BJOBS} -a -o "{self.JOB_TABLE_FIELDS}" -json'

    def parse_job_table(self, text: str) -> pd.DataFrame:
        try:
            data = json.loads(text)
        except ValueError as err:
            raise RuntimeError(f'Could not parse bjobs output as JSON: {text!r}') from err

        records = data.get('RECORDS', [])
        if len(records) == 0:
            return pd.DataFrame(columns=self.JOB_TABLE_COLUMNS)

        df = pd.DataFrame.from_records(records)

        def to_iso(column: str):
            # LSF prints "Sep 30 20:32:14 2026"; running jobs have a
            # projected finish time with a trailing " L".
            cleaned = (df[column].fillna('')
                       .astype(str)
                       .str.strip()
                       .str.replace(r'\s+L$', '', regex=True))
            return (pd.to_datetime(cleaned, format='%b %d %H:%M:%S %Y', errors='coerce')
                    .dt.strftime('%G-%m-%dT%H:%M:%S'))

        df = df.assign(
            JobID=df['JOBID'].astype(int),
            JobName=df['JOB_NAME'].fillna(''),
            State=df['STAT'].fillna('').map(lambda stat: self.STATE_MAP.get(stat, stat)),
            Start=to_iso('START_TIME'),
            End=to_iso('FINISH_TIME'),
            Elapsed=df['RUN_TIME'].fillna('').map(_lsf_seconds_to_hms),
            Timelimit=df['RUNTIMELIMIT'].fillna('').map(_lsf_minutes_to_hms),
            NNodes=pd.to_numeric(df['NEXEC_HOST'], errors='coerce').fillna(1).astype(int),
            NCPUs=pd.to_numeric(df['NTHREADS'], errors='coerce').fillna(1).astype(int),
            SubmitLine=df['COMMAND'].fillna(''),
            WorkDir=df['EXEC_CWD'].fillna(''),
        )

        return df[self.JOB_TABLE_COLUMNS]

    def cleanup_command(self, remote_dir: Path) -> str:
        return f'rm {Path(remote_dir).as_posix()}/*.lsf'


_BACKENDS = {backend.name: backend for backend in (SlurmBackend, LSFBackend)}


def get_scheduler(name=None) -> SchedulerBackend:
    """Get the scheduler backend for `name` ('slurm' (default) or 'lsf')."""
    key = (name or 'slurm').lower()
    try:
        return _BACKENDS[key]()
    except KeyError:
        raise ValueError(f'Unknown scheduler {name!r}. '
                         f'Valid options: {", ".join(sorted(_BACKENDS))}') from None
