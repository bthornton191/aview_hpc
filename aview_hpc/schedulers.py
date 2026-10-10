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

import glob
import json
import logging
import math
import re
from io import StringIO
from pathlib import Path
from typing import List, Optional

import pandas as pd

LOG = logging.getLogger(__name__)


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

    def cleanup_argv(self, remote_dir: Path) -> List[str]:
        """The argv form of :meth:`cleanup_command` (local transport).

        The default resolves the directory's scheduler files in-process and
        lists the files explicitly -- no shell, no glob in the child.  A
        backend whose cleanup needs more than ``rm <files>`` overrides this.
        """
        raise NotImplementedError

    def translate_submit_kwargs(self, kwargs: dict, logger=None) -> dict:
        """Translate generic submit kwargs into this scheduler's vocabulary.

        Callers (e.g. the CDM qual harness) pass scheduler-AGNOSTIC options
        to ``submit``/``submit_multi``/``resubmit_job``: ``mem`` (a memory
        request, Slurm syntax e.g. ``'32G'``), ``nice`` (a scheduling
        priority), ``mins`` (a wall-clock limit). Those kwargs are forwarded
        as ``--<name> <value>`` to the per-scheduler submit script named by
        the ``submit_cmd`` config key, which accepts only ITS OWN option
        set and deliberately rejects anything else (trailing words would
        become the job COMMAND line on LSF; lsf.py exits with "Unsupported
        arguments forwarded to bsub").

        The base implementation is the identity: a backend that has no
        translation needs does nothing, and unknown kwargs keep flowing to
        the submit script exactly as before (so its guard still fires for
        genuinely unsupported options).
        """
        return kwargs


class SlurmBackend(SchedulerBackend):
    """The historical aview_hpc behavior (Hexagon/Romax DEWET cluster)."""

    name = 'slurm'
    re_submission_response = re.compile(r'.*submitted batch job (\d+)\w*', flags=re.I)
    running_states = ['RUNNING']

    def translate_submit_kwargs(self, kwargs: dict, logger=None) -> dict:
        """Identity: the DEWET sbatch-era submit script took ``--mem`` and
        ``--nice`` verbatim, and its behavior is frozen. Byte-identical
        passthrough for every kwarg, known or unknown."""
        return kwargs

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

    def cleanup_argv(self, remote_dir: Path) -> List[str]:
        files = sorted(glob.glob(f'{Path(remote_dir).as_posix()}/*.slurm'))
        return ['rm', '-f', *files] if files else ['true']


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


def _parse_lsf_datetime(value) -> 'pd.Timestamp':
    """Parse LSF's ``Oct  1 14:01:44 2026`` format without locale
    sensitivity: ``%b`` resolves month names against the *client* process
    locale, which an embedding application may have changed. Returns NaT
    for anything unparseable."""
    match = RE_LSF_TIME.match(str(value or '').strip())
    if match is None:
        return pd.NaT
    month_name, day, hour, minute, second, year = match.groups()
    month = LSF_MONTHS.get(month_name.title())
    if month is None:
        return pd.NaT
    return pd.Timestamp(year=int(year), month=month, day=int(day),
                        hour=int(hour), minute=int(minute), second=int(second))


LSF_MONTHS = {m: i + 1 for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])}
RE_LSF_TIME = re.compile(r'([A-Za-z]{3})\s+(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})\s+(\d{4})')


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
      invalid"). The zero-jobs case was live-verified on 2026-10-01: bjobs
      prints valid JSON with ``"RECORDS": []`` (and exits 0), so an empty
      table parses cleanly.
    * The command is prefixed with ``env LC_ALL=C`` so month names in the
      date fields parse identically regardless of the account's locale
      (the remote login shell is csh, which does not support inline
      ``VAR=value`` assignment).
    * ``-u`` is intentionally not passed, so bjobs reports only the
      invoking user's jobs. This matches what consumers need and keeps the
      response small (``-u all`` returns >160k records on this farm).
    * ``sub_cwd`` is requested and used to fill ``WorkDir`` for PEND jobs:
      LSF leaves ``EXEC_CWD`` empty until a job is dispatched, while
      ``SUB_CWD`` is fixed at submission and equals the submission cwd
      (live-verified 2026-10-09: an aview_hpc job -- ``hpc_scripts/lsf.py``
      runs with cwd = remote dir -- reports ``SUB_CWD`` = the remote dir
      already while PEND; once RUN, ``EXEC_CWD == SUB_CWD ==`` remote dir).
      ``WorkDir`` is therefore ``EXEC_CWD`` or, when empty, ``SUB_CWD``
      (with ``'$HOME'``/empty degrading to empty, i.e. the pre-0.5.4
      behavior for non-representable cwds). The slurm backend does not
      mirror this: its sacct command is frozen byte-for-byte (pinned by a
      unit test).
    * ``exit_code`` is requested and surfaced as the ``ExitCode`` column
      (live-verified 2026-10-09: an ``exit 27`` job reports ``"27"``, a
      bkill'd job ``"2"``, and RUN/DONE/PEND report ``""``). It is kept as
      a string -- consumers (CDM ``scheduler_state``) render it as a note
      (``' (exit code 27)'``) beside the mapped state, never as a decision
      input. ``term_reason`` is NOT a valid field name on this LSF 10.1.
      The slurm backend deliberately does not mirror this: its sacct
      command is frozen byte-for-byte (pinned by a unit test).
    * For jobs still running, LSF's finish time is a *projection*; for
      finished jobs it is the actual end. Both may carry a trailing
      ``" L"`` marker (observed live on both), which is stripped. This
      differs from slurm, which reports unknown End for running jobs --
      consumers that need "job still running" should use ``State``, not
      ``End``.
    """

    name = 'lsf'
    BJOBS = '/grid/sfi/farm/bin/bjobs'
    JOB_TABLE_FIELDS = ('jobid job_name stat submit_time start_time '
                        'finish_time run_time runtimelimit nexec_host '
                        'nthreads command exec_cwd sub_cwd exit_code')
    JOB_TABLE_COLUMNS = ['JobID', 'JobName', 'Start', 'End', 'Elapsed',
                         'State', 'Timelimit', 'NNodes', 'NCPUs',
                         'SubmitLine', 'WorkDir', 'ExitCode']
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
        if days != 7:
            LOG.warning('The lsf backend cannot limit the job table to %s days: '
                        'bjobs -a only reports jobs within the cluster CLEAN_PERIOD. '
                        'The `days` argument is ignored.', days)
        return f'env LC_ALL=C {self.BJOBS} -a -o "{self.JOB_TABLE_FIELDS}" -json'

    def parse_job_table(self, text: str) -> pd.DataFrame:
        try:
            data = json.loads(text)
        except ValueError as err:
            raise RuntimeError(f'Could not parse bjobs output as JSON: {text!r}') from err

        records = data.get('RECORDS', [])
        if len(records) == 0:
            return pd.DataFrame(columns=self.JOB_TABLE_COLUMNS)

        df = pd.DataFrame.from_records(records)

        # PEND jobs are INVISIBLE to WorkDir-keyed consumers (e.g. CDM's
        # scheduler_state / wait_for_hpc_jobs): LSF leaves EXEC_CWD empty
        # until a job is dispatched to a host, so a queued job's row has no
        # WorkDir and reads as "absent" -- which the CDM harness then kills
        # after its 900 s ABSENT_FROM_TABLE_GRACE_S (CI run 37939750883
        # set 83: two sub-sims presumed lost while they sat ~18 min in PEND
        # and later completed fine). SUB_CWD is fixed at submission time and
        # equals the remote dir for aview_hpc submissions (hpc_scripts/
        # lsf.py runs with cwd = remote dir; live-verified 2026-10-09: RUN
        # rows report SUB_CWD == EXEC_CWD == remote dir), so it fills the
        # gap inside the assign below: WorkDir = EXEC_CWD, or SUB_CWD when
        # EXEC_CWD is empty. Only an ABSOLUTE SUB_CWD is usable: LSF reports
        # the literal '$HOME' when the submission cwd cannot be represented
        # (live-observed 2026-10-09), and that must degrade to '' -- the
        # pre-0.5.4 behavior -- instead of becoming a bogus WorkDir.
        sub_cwd = (df['SUB_CWD'].fillna('')
                   if 'SUB_CWD' in df.columns else pd.Series('', index=df.index))
        sub_cwd = sub_cwd.where(sub_cwd.str.startswith('/'), '')

        def to_iso(column: str):
            # LSF prints "Sep 30 20:32:14 2026". Some finish times carry a
            # trailing " L" marker (observed on both running and DONE jobs
            # on this farm, live 2026-10-01) -- it is stripped and the time
            # is kept. For running jobs this is LSF's projected finish time
            # (slurm would report unknown); see the class docstring. Parsing
            # is locale-independent (see _parse_lsf_datetime).
            cleaned = (df[column].fillna('')
                       .astype(str)
                       .str.strip()
                       .str.replace(r'\s+L$', '', regex=True))
            parsed = pd.to_datetime(cleaned.map(_parse_lsf_datetime))
            return parsed.dt.strftime('%G-%m-%dT%H:%M:%S')

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
            # See the sub_cwd note above the assign: EXEC_CWD is empty while
            # a job is PEND, so WorkDir falls back to an absolute SUB_CWD.
            WorkDir=(df['EXEC_CWD'].fillna('')
                     .mask(df['EXEC_CWD'].fillna('') == '', sub_cwd)),
            # Kept as a STRING ('27', ''): non-EXIT rows report '', so an
            # integer cast would NaN-ify them, and LSF exit codes are not
            # always plain ints (signal forms exist on other LSF setups).
            # Consumers read it via str() and blank-guard (CDM
            # scheduler_state); the client wrapper pins the dtype through
            # its CSV round-trip (see aview_hpc.aview_hpc.get_job_table).
            ExitCode=df['EXIT_CODE'].fillna(''),
        )

        return df[self.JOB_TABLE_COLUMNS]

    def cleanup_command(self, remote_dir: Path) -> str:
        return f'rm {Path(remote_dir).as_posix()}/*.lsf'

    def cleanup_argv(self, remote_dir: Path) -> List[str]:
        files = sorted(glob.glob(f'{Path(remote_dir).as_posix()}/*.lsf'))
        return ['rm', '-f', *files] if files else ['true']

    # --- submit-kwarg translation -----------------------------------------
    # Slurm syntax to LSF units, e.g. '32G' -> 32768 (MB).
    RE_MEM_SLURM = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*([kmg]?)(?:i?b)?\s*$', re.I)
    MEM_UNIT_MULTIPLIER_MB = {'': 1, 'k': 1 / 1024, 'm': 1, 'g': 1024}

    def translate_submit_kwargs(self, kwargs: dict, logger=None) -> dict:
        """Translate generic submit kwargs into the LSF submit script's
        vocabulary (``hpc_scripts/lsf.py`` on the submit host).

        ``mem`` (Slurm syntax, e.g. ``'32G'``) becomes ``mem_mb`` -- the
        integer MB form lsf.py's ``--mem_mb`` option accepts, which it
        renders as ``rusage[mem=<MB>]`` merged into the single ``-R``
        (multiple ``-R`` are rejected by this LSF; see lsf.py
        build_bsub_command).

        ``nice`` is DROPPED: bsub has no unprivileged nice equivalent (the
        closest, ``-sp`` priority, needs farm-admin policy and changes
        scheduling semantics). Dropping is logged at DEBUG so the decision
        is visible without reddening a healthy submit.

        Every other kwarg passes through unchanged, and unknown kwargs
        still reach the submit script so its unknown-argument guard fires
        (deliberately: a genuinely unsupported option must die loudly, not
        be silently swallowed here).

        Background: the CDM qual harness has always passed
        ``mem=JOB_MEM_GB, nice=MAX_NICE`` -- sbatch-era options from the
        retired DEWET cluster. Under slurm they were forwarded verbatim;
        under lsf they were rejected ("Unsupported arguments forwarded to
        bsub", CI run 37824964058 2026-10-08). Translating HERE keeps the
        client scheduler-configurable: the same generic kwargs work on
        Windows+Slurm, Windows+LSF and Linux+LSF purely via ~/.aview_hpc,
        with no scheduler-specific values in any caller.
        """
        translated = {}
        dropped = []
        for key, value in kwargs.items():
            if key == 'mem':
                translated['mem_mb'] = self._mem_to_mb(value)
            elif key == 'nice':
                dropped.append(f'nice={value}')
            else:
                translated[key] = value
        log = logger if logger is not None else LOG
        if dropped:
            log.debug('LSF backend dropped %s: bsub has no equivalent option '
                      '(see translate_submit_kwargs).', ', '.join(dropped))
        return translated

    def _mem_to_mb(self, value) -> int:
        """Slurm memory syntax ('32G', '512M', '2048', '1.5g') to integer MB.

        ROUNDS UP: rusage[mem=] is a per-process reservation ceiling on this
        farm, and rounding a 1.5g request DOWN to 1536 MB would silently
        shrink the request below what the caller asked for.
        """
        match = self.RE_MEM_SLURM.match(str(value))
        if match is None:
            raise ValueError(
                f'Cannot translate mem={value!r} to LSF rusage megabytes: '
                'expected Slurm syntax like 32G, 512M or 2048 (k/m/g suffix).')
        number, unit = match.groups()
        multiplier = self.MEM_UNIT_MULTIPLIER_MB[unit.lower()]
        mb = float(number) * multiplier
        return int(math.ceil(mb))


_BACKENDS = {backend.name: backend for backend in (SlurmBackend, LSFBackend)}


def get_scheduler(name=None) -> SchedulerBackend:
    """Get the scheduler backend for `name` ('slurm' (default) or 'lsf')."""
    key = (name or 'slurm').lower()
    try:
        return _BACKENDS[key]()
    except KeyError:
        raise ValueError(f'Unknown scheduler {name!r}. '
                         f'Valid options: {", ".join(sorted(_BACKENDS))}') from None
