"""Unit tests for the scheduler backends and the LSF submit script.

These tests are pure unit tests: they run against captured fixture text
(no live cluster needed). The fixture `bjobs_sample.json` contains records
captured verbatim from the Cadence sjlsf01 farm on 2026-10-01 (LSF
10.1.0.15) plus synthetic records for states not present in the capture.
"""
import importlib.util
import io
import json
import logging
import os
import re
import shlex
import shutil
import sys
import tempfile
import types
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))

from aview_hpc.schedulers import (LSFBackend, SlurmBackend, get_scheduler,  # noqa
                                  _lsf_minutes_to_hms, _lsf_seconds_to_hms,
                                  _parse_lsf_datetime)

FIXTURES = Path(__file__).parent / 'fixtures'
SACCT_SAMPLE = FIXTURES / 'sacct_sample.csv'
BJOBS_SAMPLE = FIXTURES / 'bjobs_sample.json'
LSF_SCRIPT_PATH = Path(__file__).parent.parent / 'hpc_scripts' / 'lsf.py'


def _load_lsf_script():
    spec = importlib.util.spec_from_file_location('lsf_script', LSF_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lsf_script = _load_lsf_script()


class TestGetScheduler(unittest.TestCase):

    def test_default_is_slurm(self):
        self.assertEqual(get_scheduler().name, 'slurm')
        self.assertEqual(get_scheduler(None).name, 'slurm')
        self.assertEqual(get_scheduler('slurm').name, 'slurm')

    def test_lsf(self):
        self.assertEqual(get_scheduler('lsf').name, 'lsf')

    def test_case_insensitive(self):
        self.assertEqual(get_scheduler('SLURM').name, 'slurm')
        self.assertEqual(get_scheduler('Lsf').name, 'lsf')

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            get_scheduler('pbs')


class TestSlurmBackend(unittest.TestCase):

    def setUp(self):
        self.backend = get_scheduler('slurm')

    def test_job_table_command_unchanged(self):
        """The sacct command must be byte-for-byte what aview_hpc 0.2.x sent."""
        expected = ('sacct -S now-7days -X -P --delimiter=, -o '
                    'jobid,jobname%-40,start,end,Elapsed,state,timelimit,'
                    'nnodes,ncpus,submitline%-70,workdir%-70')
        self.assertEqual(self.backend.job_table_command(days=7), expected)

    def test_job_table_command_days(self):
        self.assertIn('-S now-3days', self.backend.job_table_command(days=3))

    def test_submission_response(self):
        match = self.backend.re_submission_response.search(
            'Submitted batch job 12345 on cluster x\n')
        self.assertEqual(match.group(1), '12345')

    def test_submission_response_after_running_line(self):
        """The server script prints 'Running: ...' before the scheduler's
        response; the regex must find the job id regardless of ordering."""
        output = 'Running: sbatch --time=10 x.slurm\nSubmitted batch job 99\n'
        match = self.backend.re_submission_response.search(output)
        self.assertEqual(match.group(1), '99')

    def test_parse_job_table(self):
        df = self.backend.parse_job_table(SACCT_SAMPLE.read_text())

        self.assertEqual(list(df.columns),
                         ['JobID', 'JobName', 'Start', 'End', 'Elapsed',
                          'State', 'Timelimit', 'NNodes', 'NCPUS',
                          'SubmitLine', 'WorkDir'])
        self.assertEqual(df.loc[0, 'JobName'], 'test_job')
        self.assertEqual(df.loc[0, 'State'], 'COMPLETED')
        self.assertEqual(df.loc[0, 'End'], '2026-09-30T11:00:00')
        self.assertEqual(df.loc[1, 'JobName'], 'pend_job')
        self.assertEqual(df.loc[1, 'Elapsed'], '00:00:00')
        self.assertTrue(pd.isna(df.loc[1, 'Start']))
        self.assertEqual(df.loc[1, 'WorkDir'], '/home/thornton/hpc_tmp/pend_job.efgh')

    def test_cleanup_command(self):
        self.assertEqual(self.backend.cleanup_command(Path('/tmp/x')),
                         'rm /tmp/x/*.slurm')


class TestLSFBackend(unittest.TestCase):

    def setUp(self):
        self.backend = get_scheduler('lsf')

    def test_submission_response(self):
        match = self.backend.re_submission_response.search(
            'Job <12345> is submitted to queue <lnx64>.\n')
        self.assertEqual(match.group(1), '12345')

    def test_submission_response_no_match(self):
        self.assertIsNone(self.backend.re_submission_response.search('No job found'))
        self.assertIsNone(self.backend.re_submission_response.search(
            'Job not submitted: invalid project\n'))

    def test_job_table_command(self):
        cmd = self.backend.job_table_command(days=7)
        self.assertTrue(cmd.startswith('env LC_ALL=C /grid/sfi/farm/bin/bjobs -a -o "'))
        self.assertTrue(cmd.endswith('" -json'))
        self.assertIn('exec_cwd', cmd)
        self.assertIn('sub_cwd', cmd)
        self.assertIn('nthreads', cmd)
        self.assertIn('nexec_host', cmd)
        self.assertIn('exit_code', cmd)

    def test_job_table_command_roundtrip(self):
        """The -o field list must contain every field the parser reads:
        simulate a bjobs response containing EXACTLY the requested fields
        and parse it (guards against a field being added to the parser but
        not the command, or vice versa)."""
        import json as _json
        cmd = self.backend.job_table_command()
        fields = re.search(r'-o "([^"]+)"', cmd).group(1).split()
        sample = _json.loads(BJOBS_SAMPLE.read_text())['RECORDS'][0]
        # Restrict each record to exactly the requested fields
        records = [{f.upper(): rec.get(f.upper(), '') for f in fields} for rec in [sample]]
        response = _json.dumps({'COMMAND': 'bjobs', 'JOBS': len(records),
                                'RECORDS': records})
        df = self.backend.parse_job_table(response)
        self.assertEqual(len(df), 1)
        self.assertEqual(list(df.columns), LSFBackend.JOB_TABLE_COLUMNS)

    def test_state_map(self):
        self.assertEqual(self.backend.STATE_MAP['PEND'], 'PENDING')
        self.assertEqual(self.backend.STATE_MAP['RUN'], 'RUNNING')
        self.assertEqual(self.backend.STATE_MAP['DONE'], 'COMPLETED')
        self.assertEqual(self.backend.STATE_MAP['EXIT'], 'FAILED')
        for stat in ('PSUSP', 'USUSP', 'SSUSP'):
            self.assertEqual(self.backend.STATE_MAP[stat], 'SUSPENDED')

    def test_parse_job_table(self):
        df = self.backend.parse_job_table(BJOBS_SAMPLE.read_text())

        self.assertEqual(list(df.columns), LSFBackend.JOB_TABLE_COLUMNS)
        self.assertEqual(len(df), 6)

        # Real captured record (RUN)
        row = df[df['JobID'] == 5346451].iloc[0]
        self.assertEqual(row['State'], 'RUNNING')
        self.assertEqual(row['WorkDir'], '/home/ambinteg')  # \/ unescaped
        self.assertEqual(row['Start'], '2026-09-30T20:32:15')
        # The " L" marker is stripped and the (projected, for running jobs)
        # finish time is kept; see the LSFBackend docstring.
        self.assertEqual(row['End'], '2026-10-14T17:52:15')
        self.assertEqual(row['Elapsed'], '15:53:28')         # 57208 second(s)
        self.assertEqual(row['NCPUs'], 13)
        self.assertEqual(row['NNodes'], 1)

        # DONE job whose finish time also carries " L" (live-observed on
        # the farm 2026-10-01): the time must be kept, not dropped.
        done = df[df['JobID'] == 5800002].iloc[0]
        self.assertEqual(done['State'], 'COMPLETED')
        self.assertEqual(done['End'], '2026-10-01T14:01:44')

        # Synthetic records
        self.assertEqual(df[df['JobID'] == 5800001].iloc[0]['State'], 'PENDING')
        self.assertEqual(df[df['JobID'] == 5800002].iloc[0]['State'], 'COMPLETED')
        self.assertEqual(df[df['JobID'] == 5800004].iloc[0]['State'], 'SUSPENDED')

        failed_state = df[df['JobID'] == 5800003].iloc[0]['State']
        self.assertEqual(failed_state.lower(), 'failed')  # CDM contract

        # exit_code column (live-verified shapes on sjlsf01 2026-10-09:
        # 'exit 27' job -> "27"; RUN/DONE/PEND -> ""; the fixture's second
        # DONE record carries "0" -- a DONE job that reports 0 must parse).
        # Kept as str: an int cast would NaN-ify every non-EXIT row.
        self.assertEqual(df[df['JobID'] == 5800003].iloc[0]['ExitCode'], '27')
        self.assertEqual(df[df['JobID'] == 5800005].iloc[0]['ExitCode'], '0')
        for jobid in (5346451, 5800001, 5800002, 5800004):
            self.assertEqual(df[df['JobID'] == jobid].iloc[0]['ExitCode'], '')
        # String dtype (pandas 2 'object', pandas 3 'str') -- never numeric.
        self.assertIn(str(df['ExitCode'].dtype), ('object', 'str'))

        pending = df[df['JobID'] == 5800001].iloc[0]
        self.assertTrue(pd.isna(pending['Start']))
        self.assertEqual(pending['Elapsed'], '00:00:00')
        # PEND jobs get a WorkDir from SUB_CWD (t_96514c3c): LSF leaves
        # EXEC_CWD empty until dispatch, so a queued aview_hpc job
        # (lsf.py runs with cwd = remote dir) is only findable via SUB_CWD.
        # This is the set-83 regression shape (run 37939750883: two sub-sims
        # read as "absent" for 900 s and killed while PEND).
        self.assertEqual(pending['State'], 'PENDING')
        self.assertEqual(pending['WorkDir'], '/home/thornton/hpc_tmp/x.abcd')

        # RUN rows: EXEC_CWD is authoritative (SUB_CWD == EXEC_CWD live, but
        # the fill must never be able to override a populated EXEC_CWD).
        self.assertEqual(df[df['JobID'] == 5346451].iloc[0]['WorkDir'], '/home/ambinteg')

    def test_parse_job_table_dollar_home_sub_cwd_degrades(self):
        """"$HOME" (submission cwd not representable) must NOT become a
        WorkDir: the fill degrades to '' exactly as pre-0.5.4, so a
        WorkDir-keyed consumer still reads the row as absent rather than
        matching against a bogus literal path."""
        import json as _json
        records = [{'JOBID': '5800010', 'JOB_NAME': 'x', 'STAT': 'PEND',
                    'SUBMIT_TIME': 'Oct  1 14:02:11 2026',
                    'START_TIME': '', 'FINISH_TIME': '',
                    'RUN_TIME': '0 second(s)', 'RUNTIMELIMIT': '720.0',
                    'NEXEC_HOST': '', 'NTHREADS': '',
                    'COMMAND': '/home/thornton/scripts/lsf.py x.acf',
                    'EXEC_CWD': '', 'SUB_CWD': '$HOME', 'EXIT_CODE': ''}]
        df = self.backend.parse_job_table(
            _json.dumps({'COMMAND': 'bjobs', 'JOBS': 1, 'RECORDS': records}))
        self.assertEqual(df.iloc[0]['State'], 'PENDING')
        self.assertEqual(df.iloc[0]['WorkDir'], '')

    def test_parse_job_table_missing_sub_cwd_key(self):
        """Records without a SUB_CWD key (an older bjobs, or a response
        built from the pre-0.5.4 field list) must parse unchanged: EXEC_CWD
        still becomes WorkDir and an empty EXEC_CWD stays ''."""
        import json as _json
        records = [{'JOBID': '5800011', 'JOB_NAME': 'x', 'STAT': 'PEND',
                    'SUBMIT_TIME': 'Oct  1 14:02:11 2026',
                    'START_TIME': '', 'FINISH_TIME': '',
                    'RUN_TIME': '0 second(s)', 'RUNTIMELIMIT': '720.0',
                    'NEXEC_HOST': '', 'NTHREADS': '',
                    'COMMAND': '/home/thornton/scripts/lsf.py x.acf',
                    'EXEC_CWD': '', 'EXIT_CODE': ''},
                   {'JOBID': '5800012', 'JOB_NAME': 'x', 'STAT': 'RUN',
                    'SUBMIT_TIME': 'Oct  1 14:02:11 2026',
                    'START_TIME': 'Oct  1 14:02:12 2026',
                    'FINISH_TIME': '', 'RUN_TIME': '0 second(s)',
                    'RUNTIMELIMIT': '720.0', 'NEXEC_HOST': '1',
                    'NTHREADS': '8',
                    'COMMAND': '/home/thornton/scripts/lsf.py x.acf',
                    'EXEC_CWD': '/home/thornton/hpc_tmp/r', 'EXIT_CODE': ''}]
        df = self.backend.parse_job_table(
            _json.dumps({'COMMAND': 'bjobs', 'JOBS': 2, 'RECORDS': records}))
        self.assertEqual(df[df['JobID'] == 5800011].iloc[0]['WorkDir'], '')
        self.assertEqual(df[df['JobID'] == 5800012].iloc[0]['WorkDir'],
                         '/home/thornton/hpc_tmp/r')

    def test_parse_job_table_empty(self):
        text = '{"COMMAND":"bjobs","JOBS":0,"RECORDS":[]}'
        df = self.backend.parse_job_table(text)
        self.assertEqual(len(df), 0)
        self.assertEqual(list(df.columns), LSFBackend.JOB_TABLE_COLUMNS)

    def test_parse_job_table_garbage_raises(self):
        with self.assertRaises(RuntimeError):
            self.backend.parse_job_table('No job found')

    def test_cleanup_command(self):
        self.assertEqual(self.backend.cleanup_command(Path('/tmp/x')),
                         'rm /tmp/x/*.lsf')


class TestConfig(unittest.TestCase):
    """Partial set_config updates must not wipe keys set by earlier calls."""

    def test_partial_update_preserves_keys(self):
        from aview_hpc import config as cfg
        with TemporaryDirectory() as td:
            with patch.object(cfg, 'CONFIG_FILE', Path(td) / '.aview_hpc'):
                cfg.set_config(host='h1', username='u1', scheduler='lsf',
                               key_filename='C:/k/id_ed25519', remote_tempdir='/tmp/x')
                # A later partial update (e.g. another CLI call) that does
                # not repeat every key must not null them out.
                cfg.set_config(submit_cmd='python3 /home/thornton/scripts/lsf.py')
                config = cfg.get_config()
                self.assertEqual(config['scheduler'], 'lsf')
                self.assertEqual(config['key_filename'], 'C:/k/id_ed25519')
                self.assertEqual(config['remote_tempdir'], '/tmp/x')
                self.assertEqual(config['submit_cmd'], 'python3 /home/thornton/scripts/lsf.py')

    def test_scheduler_null_in_config_falls_back_to_slurm(self):
        from aview_hpc import config as cfg
        with TemporaryDirectory() as td:
            with patch.object(cfg, 'CONFIG_FILE', Path(td) / '.aview_hpc'):
                cfg.CONFIG_FILE.write_text('{"host": "h", "username": "u", "scheduler": null}')
                config = cfg.get_config()
                self.assertIsNone(config.get('scheduler'))
                # The expression HPCSession uses to pick the backend:
                scheduler = config.get('scheduler') or 'slurm'
                self.assertEqual(get_scheduler(scheduler).name, 'slurm')


class TestLSFHelpers(unittest.TestCase):

    def test_parse_lsf_datetime(self):
        # Locale-independent parser (no %b month-name lookup)
        self.assertEqual(_parse_lsf_datetime('Oct  1 14:01:44 2026'),
                         pd.Timestamp(2026, 10, 1, 14, 1, 44))
        self.assertEqual(_parse_lsf_datetime('Sep 30 20:32:14 2026'),
                         pd.Timestamp(2026, 9, 30, 20, 32, 14))
        self.assertTrue(pd.isna(_parse_lsf_datetime('')))
        self.assertTrue(pd.isna(_parse_lsf_datetime(None)))
        self.assertTrue(pd.isna(_parse_lsf_datetime('garbage')))

    def test_seconds_to_hms(self):
        self.assertEqual(_lsf_seconds_to_hms('57208 second(s)'), '15:53:28')
        self.assertEqual(_lsf_seconds_to_hms('60 second(s)'), '00:01:00')
        self.assertEqual(_lsf_seconds_to_hms('90000 second(s)'), '1-01:00:00')
        self.assertEqual(_lsf_seconds_to_hms(''), '00:00:00')

    def test_minutes_to_hms(self):
        self.assertEqual(_lsf_minutes_to_hms('2400.0'), '1-16:00:00')
        self.assertEqual(_lsf_minutes_to_hms('60.0'), '01:00:00')
        self.assertEqual(_lsf_minutes_to_hms(''), '')


class TestSetConfigCli(unittest.TestCase):
    """The set_config CLI validates the scheduler name at set time."""

    def _run_cli(self, *argv):
        from aview_hpc import _cli
        from aview_hpc import config as cfg
        with TemporaryDirectory() as td, \
                patch.object(cfg, 'CONFIG_FILE', Path(td) / '.aview_hpc'), \
                patch.object(sys, 'argv', ['aview_hpc', 'set_config', *argv]):
            _cli.main()
            return cfg.get_config()

    def test_set_scheduler_valid(self):
        with patch('sys.stdout', new=StringIO()):
            config = self._run_cli('--scheduler', 'lsf')
        self.assertEqual(config['scheduler'], 'lsf')

    def test_set_scheduler_invalid_exits(self):
        with self.assertRaises(SystemExit) as ctx, \
                patch('sys.stderr', new=StringIO()):
            self._run_cli('--scheduler', 'slurm2')
        self.assertEqual(ctx.exception.code, 2)


class TestLSFScript(unittest.TestCase):
    """Unit tests for hpc_scripts/lsf.py (the server-side submit script)."""

    def test_format_wallclock(self):
        self.assertEqual(lsf_script.format_wallclock(5), '0:05')
        self.assertEqual(lsf_script.format_wallclock(60), '1:00')
        self.assertEqual(lsf_script.format_wallclock(720), '12:00')
        self.assertEqual(lsf_script.format_wallclock(725), '12:05')
        with self.assertRaises(ValueError):
            lsf_script.format_wallclock(0)
        with self.assertRaises(ValueError):
            lsf_script.format_wallclock(-5)

    def test_build_bsub_command(self):
        cmd = lsf_script.build_bsub_command(
            script_file='myjob_0.lsf', job_name='myjob', mins=720,
            queue='lnx64', project='MSC:2023.4:NNL:SIMULATION',
            res_req='select[(OSMJR==8 && OSMNR>=6) || OSMJR>=9]', n_cpus=8)

        self.assertIn(' -W 12:00 ', cmd)
        self.assertIn(' -P MSC:2023.4:NNL:SIMULATION ', cmd)
        self.assertIn(' -q lnx64 ', cmd)
        # ONE combined -R string: this LSF rejects multiple -R options when a
        # span/cu/affinity section is involved (live-verified 2026-10-01).
        self.assertIn("-R 'select[(OSMJR==8 && OSMNR>=6) || OSMJR>=9] span[hosts=1]'", cmd)
        self.assertEqual(cmd.count(' -R '), 1)
        self.assertIn(' -n 8 ', cmd)
        self.assertIn(' -J myjob ', cmd)
        self.assertIn(' -oo myjob.log ', cmd)
        self.assertIn(' -eo myjob.err ', cmd)
        self.assertIn(' -u noemail ', cmd)
        self.assertTrue(cmd.startswith('/grid/sfi/farm/bin/bsub '))
        # bsub has no script-file argument (trailing args are the job COMMAND,
        # which made every job exit 127 before this fix): the job script is
        # fed via stdin redirection (live-verified 2026-10-01).
        self.assertTrue(cmd.endswith(' < myjob_0.lsf'))
        # shlex round-trip: the command must parse as intended under /bin/sh
        self.assertEqual(shlex.split(cmd)[-2:], ['<', 'myjob_0.lsf'])

    def test_build_bsub_command_res_req_edge_cases(self):
        # res_req that already has a span section: passed through, no duplicate
        cmd = lsf_script.build_bsub_command(
            script_file='myjob_0.lsf', job_name='myjob', mins=720,
            queue='lnx64', project='MSC:2023.4:NNL:SIMULATION',
            res_req='select[x] span[hosts=1]', n_cpus=8)
        self.assertIn("-R 'select[x] span[hosts=1]'", cmd)
        self.assertEqual(cmd.count('span['), 1)

        # empty res_req degrades to span[hosts=1] alone (no leading space)
        cmd = lsf_script.build_bsub_command(
            script_file='myjob_0.lsf', job_name='myjob', mins=720,
            queue='lnx64', project='MSC:2023.4:NNL:SIMULATION',
            res_req='  ', n_cpus=8)
        self.assertIn("-R 'span[hosts=1]'", cmd)

        # None res_req (explicit None from a caller) degrades the same way
        cmd = lsf_script.build_bsub_command(
            script_file='myjob_0.lsf', job_name='myjob', mins=720,
            queue='lnx64', project='MSC:2023.4:NNL:SIMULATION',
            res_req=None, n_cpus=8)
        self.assertIn("-R 'span[hosts=1]'", cmd)

    def test_build_bsub_command_quotes_path_with_spaces(self):
        cmd = lsf_script.build_bsub_command(
            script_file='my job_0.lsf', job_name='myjob', mins=720,
            queue='lnx64', project='MSC:2023.4:NNL:SIMULATION',
            res_req='select[(OSMJR==8 && OSMNR>=6) || OSMJR>=9]', n_cpus=8)
        self.assertTrue(cmd.endswith(" < 'my job_0.lsf'"))
        # Round-trips through shlex exactly as two trailing tokens
        self.assertEqual(shlex.split(cmd)[-2:], ['<', 'my job_0.lsf'])

    def test_build_job_script(self):
        script = lsf_script.build_job_script(
            Path('/home/thornton/tmp/m/myjob.acf'),
            license_file='1700@sjflex5',
            adams_home='/home/thornton/adams/2023_4_1')
        self.assertIn('export MSC_LICENSE_FILE=1700@sjflex5', script)
        self.assertIn('/home/thornton/adams/2023_4_1/mdi -c ru-s i myjob.acf exit', script)
        self.assertNotIn('LD_LIBRARY_PATH', script)

        script = lsf_script.build_job_script(
            Path('/x/m.acf'), license_file='1700@sjflex5', adams_home='/a/b/',
            ld_library_path='/opt/libs')
        self.assertIn('export LD_LIBRARY_PATH=/opt/libs', script)
        self.assertIn('/a/b/mdi', script)

    def test_get_n_cpus(self):
        with TemporaryDirectory() as tmpdir:
            adm = Path(tmpdir) / 'm.adm'
            adm.write_text('NTHREADS = 8\n')
            self.assertEqual(lsf_script.get_n_cpus(adm), 8)
            adm.write_text('no threads here\n')
            self.assertEqual(lsf_script.get_n_cpus(adm), 1)

    def _write_model(self, tmpdir, nthreads=True):
        acf = Path(tmpdir) / 'm.acf'
        adm = Path(tmpdir) / 'm.adm'
        acf.write_text('m\nSIMULATE/DYNAMIC, END=0.25\nstop\n')
        adm.write_text(f'model\n{"NTHREADS = 8" if nthreads else ""}\n')
        return acf

    def test_dry_run_end_to_end(self):
        with TemporaryDirectory() as tmpdir:
            acf = self._write_model(tmpdir)
            argv = ['lsf.py', str(acf), '--mins', '5',
                    '--project', 'MSC:2023.4:NNL:SIMULATION',
                    '--adams_home', '/home/thornton/adams/2023_4_1',
                    '--license', '1700@sjflex5', '--dry_run']
            with patch.object(sys, 'argv', argv), patch('sys.stdout', new=StringIO()) as out:
                lsf_script.main()
            output = out.getvalue()

        self.assertIn('DRY-RUN: would run:', output)
        self.assertIn('/grid/sfi/farm/bin/bsub', output)
        self.assertIn('-W 0:05 ', output)
        self.assertIn('-P MSC:2023.4:NNL:SIMULATION', output)
        self.assertIn('-q lnx64', output)
        # One combined -R string, not two -R flags (pins the regression the
        # old two-flag form would show: substring 'span[hosts=1]' alone
        # would match either shape).
        bsub_line = next(l for l in output.splitlines()
                         if l.startswith('DRY-RUN: would run:'))
        self.assertEqual(bsub_line.count(' -R '), 1)
        self.assertIn(" -R 'select[(OSMJR==8 && OSMNR>=6) || OSMJR>=9] span[hosts=1]'", bsub_line)
        self.assertIn('-n 8 ', bsub_line)
        # Job script fed via stdin, as the LAST words of the command (nothing
        # may follow the redirect or bsub treats it as the job COMMAND)
        self.assertTrue(bsub_line.endswith('< m.lsf'))
        self.assertIn('MSC_LICENSE_FILE=1700@sjflex5', output)
        self.assertIn('/home/thornton/adams/2023_4_1/mdi -c ru-s i m.acf exit', output)
        self.assertNotIn('Job <', output)

    def test_submit_rejects_forwarded_extras(self):
        """Unknown/extra args forwarded after the options would become the
        bsub job COMMAND line (overriding the stdin-fed job script, exit 127):
        submit() must refuse them instead of appending them."""
        with TemporaryDirectory() as tmpdir:
            acf = self._write_model(tmpdir)
            with self.assertRaises(SystemExit) as ctx:
                lsf_script.submit(Path(acf), mins=5, queue='lnx64',
                                  project='MSC:2023.4:NNL:SIMULATION',
                                  res_req=lsf_script.DEFAULT_RES_REQ,
                                  adams_home='/a', license_file='1700@x',
                                  args=['--acar', 'True'])
            self.assertIn('would become the job COMMAND', str(ctx.exception))

    def test_project_required(self):
        # _main_env isolates env + config: without it the parser falls back
        # to the REAL ~/.aview_hpc on the running machine, and on the LSF
        # submit host that file carries a project key -- main() then runs a
        # REAL bsub instead of exiting (observed on sjcvl-thornton
        # 2026-10-08: jobs 566843/566932/567142 EXITed from this test).
        with TemporaryDirectory() as tmpdir:
            acf = self._write_model(tmpdir)
            argv = ['lsf.py', str(acf), '--adams_home', '/a', '--license', '1700@x']
            with self.assertRaises(SystemExit) as ctx:
                self._main_env(argv, tmpdir=tmpdir, config={})
            self.assertEqual(ctx.exception.code, 2)

    def test_project_format_validated(self):
        with TemporaryDirectory() as tmpdir:
            acf = self._write_model(tmpdir)
            argv = ['lsf.py', str(acf), '--project', 'no-colons',
                    '--adams_home', '/a', '--license', '1700@x']
            with self.assertRaises(SystemExit):
                self._main_env(argv, tmpdir=tmpdir, config={})

    def _main_env(self, argv, env=None, config=None, tmpdir=None):
        """Run lsf.py main() in dry-run with a controlled env + config file."""
        env = dict(env or {})
        cfg_file = Path(tmpdir) / 'aview_hpc_cfg.json'
        if config is not None:
            cfg_file.write_text(json.dumps(config))
        clean = {k: v for k, v in os.environ.items()
                 if k not in ('LSF_PROJECT', 'ADAMS_HOME', 'MSC_LICENSE_FILE')}
        clean.update(env)
        with patch.dict('os.environ', clean, clear=True), \
                patch.object(lsf_script, 'CONFIG_FILE', cfg_file), \
                patch.object(sys, 'argv', argv), \
                patch('sys.stdout', new=StringIO()) as out:
            lsf_script.main()
        return out.getvalue()

    def test_project_with_optional_fields_accepted(self):
        """Cadence IT format allows trailing :optional-field segments."""
        with TemporaryDirectory() as tmpdir:
            acf = self._write_model(tmpdir)
            out = self._main_env(['lsf.py', str(acf), '--project', 'ADAMS:2023.4.1:AE:qual:NNL',
                                  '--adams_home', '/a', '--license', '1700@x', '--dry_run'],
                                 tmpdir=tmpdir)
        self.assertIn('-P ADAMS:2023.4.1:AE:qual:NNL', out)

    def test_project_regex_rejects_bad_shapes(self):
        for bad in ('adams:2023.4.1:AE:qual', 'ADAMS:2023.4.1:ae:qual', 'ADAMS:2023.4.1:AE',
                    'ADAMS::AE:qual', 'ADAMS:2023.4.1:AE:qual:', 'no-colons'):
            self.assertIsNone(lsf_script.RE_PROJECT.fullmatch(bad), bad)
        for good in ('ADAMS:2023.4.1:AE:qual', 'ADAMS:2023.4.1:AE:qual:NNL', 'EDI:23.1:RD:build:x:y'):
            self.assertIsNotNone(lsf_script.RE_PROJECT.fullmatch(good), good)

    def test_config_file_supplies_project_adams_home_license(self):
        """Non-interactive SSH exec sources no login files and the harness never
        forwards these flags, so ~/.aview_hpc must be able to supply them."""
        with TemporaryDirectory() as tmpdir:
            acf = self._write_model(tmpdir)
            out = self._main_env(['lsf.py', str(acf), '--dry_run'], tmpdir=tmpdir,
                                 config={'project': 'ADAMS:2023.4.1:AE:qual:NNL',
                                         'adams_home': '/home/u/adams/2023_4_1',
                                         'license': '1700@sjflex5'})
        self.assertIn('-P ADAMS:2023.4.1:AE:qual:NNL', out)
        self.assertIn('/home/u/adams/2023_4_1/mdi -c ru-s i m.acf exit', out)
        self.assertIn('MSC_LICENSE_FILE=1700@sjflex5', out)

    def test_precedence_cli_over_env_over_config(self):
        with TemporaryDirectory() as tmpdir:
            acf = self._write_model(tmpdir)
            cfg = {'project': 'CFG:1:AE:x', 'adams_home': '/cfg', 'license': '1@cfg'}
            env = {'LSF_PROJECT': 'ENV:1:AE:x', 'ADAMS_HOME': '/env'}
            out = self._main_env(['lsf.py', str(acf), '--project', 'CLI:1:AE:x', '--dry_run'],
                                 env=env, config=cfg, tmpdir=tmpdir)
            self.assertIn('-P CLI:1:AE:x', out)          # CLI beats env + config
            self.assertIn('/env/mdi', out)               # env beats config
            self.assertIn('MSC_LICENSE_FILE=1@cfg', out)  # config used when nothing else

    def test_missing_or_corrupt_config_is_ignored(self):
        with TemporaryDirectory() as tmpdir:
            bad = Path(tmpdir) / 'bad.json'
            bad.write_text('{not json')
            self.assertEqual(lsf_script.load_config(bad), {})
            self.assertEqual(lsf_script.load_config(Path(tmpdir) / 'nope.json'), {})
            lst = Path(tmpdir) / 'list.json'
            lst.write_text('[1, 2]')
            self.assertEqual(lsf_script.load_config(lst), {})
            typed = Path(tmpdir) / 'typed.json'
            typed.write_text(json.dumps({'project': 123, 'license': ['x'], 'adams_home': ' ',
                                         'queue': 'lnx64'}))
            self.assertEqual(lsf_script.load_config(typed), {'queue': 'lnx64'})

    def test_project_still_required_without_any_source(self):
        with TemporaryDirectory() as tmpdir:
            acf = self._write_model(tmpdir)
            with self.assertRaises(SystemExit) as ctx:
                self._main_env(['lsf.py', str(acf), '--adams_home', '/a', '--license', '1@x'],
                               tmpdir=tmpdir, config={})
            self.assertEqual(ctx.exception.code, 2)

    def test_adams_home_required(self):
        # Same isolation as test_project_required: the machine's real
        # ~/.aview_hpc can supply adams_home and turn this into a real bsub.
        with TemporaryDirectory() as tmpdir:
            acf = self._write_model(tmpdir)
            argv = ['lsf.py', str(acf), '--project', 'MSC:2023.4:NNL:SIMULATION',
                    '--license', '1700@x']
            with self.assertRaises(SystemExit):
                self._main_env(argv, tmpdir=tmpdir, config={})


class TestSubmitKwargTranslation(unittest.TestCase):
    """Scheduler backends translate generic submit kwargs (mem/nice/...) into
    the submit script's own vocabulary BEFORE they become --options.

    Background: the CDM qual harness passes mem=JOB_MEM_GB ('32G') and
    nice=MAX_NICE (2147483645) -- sbatch-era options from the retired DEWET
    Slurm cluster. hpc_scripts/lsf.py correctly refuses unknown options
    (trailing words become the bsub job COMMAND line), which killed the
    first farm-backed Linux CI run (37824964058, 2026-10-08). The fix is
    HERE, in the scheduler backend, so every caller stays scheduler-agnostic
    and the same client works on Windows+Slurm, Windows+LSF and Linux+LSF
    purely via ~/.aview_hpc.
    """

    # The exact kwargs the CDM qual harness sends (hpc_jobs.py run_on_hpc /
    # base_test.py submit block): L184-185 mem=JOB_MEM_GB, nice=MAX_NICE.
    CDM_KWARGS = {'mins': 1000, 'mem': '32G', 'nice': 2147483645}

    def test_slurm_backend_is_byte_identical(self):
        """Slurm passthrough is pinned: identity for known AND unknown kwargs,
        same dict object semantics (no copy/rename/drop/reorder)."""
        backend = get_scheduler('slurm')
        original = dict(self.CDM_KWARGS)
        translated = backend.translate_submit_kwargs(self.CDM_KWARGS)
        self.assertEqual(translated, original)

    def test_slurm_identity_for_empty_and_unknown(self):
        backend = get_scheduler('slurm')
        self.assertEqual(backend.translate_submit_kwargs({}), {})
        weird = {'queue': 'gpu', 'totally_unknown': 'x'}
        self.assertEqual(backend.translate_submit_kwargs(weird), weird)

    def test_lsf_translates_cdm_kwargs(self):
        """The exact run-37824964058 kwargs: mem 32G -> mem_mb 32768, nice
        dropped, everything else passed through."""
        backend = get_scheduler('lsf')
        translated = backend.translate_submit_kwargs(self.CDM_KWARGS)
        self.assertEqual(translated, {'mins': 1000, 'mem_mb': 32768})

    def test_lsf_drops_nice_only(self):
        backend = get_scheduler('lsf')
        translated = backend.translate_submit_kwargs({'nice': 2147483645})
        self.assertEqual(translated, {})
        # nice is dropped even when mem is absent
        self.assertEqual(backend.translate_submit_kwargs({'mins': 5, 'nice': 10}),
                         {'mins': 5})

    def test_lsf_dropped_nice_logged_at_debug(self):
        backend = get_scheduler('lsf')
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        logger = logging.getLogger('test_lsf_drop_log')
        logger.setLevel(logging.DEBUG)
        handler = _Capture()
        logger.addHandler(handler)
        try:
            backend.translate_submit_kwargs({'mem': '32G', 'nice': 2147483645},
                                            logger=logger)
        finally:
            logger.removeHandler(handler)

        drops = [r for r in records if r.levelno == logging.DEBUG and 'nice' in r.getMessage()]
        self.assertTrue(drops, 'expected a DEBUG record naming the dropped nice kwarg')

    def test_lsf_mem_unit_handling(self):
        backend = get_scheduler('lsf')
        cases = {'32G': 32768, '32g': 32768, '32GiB': 32768, '32': 32,
                 '512M': 512, '512m': 512, '2048K': 2, '1.5g': 1536,
                 ' 8G ': 8192}
        for value, expected_mb in cases.items():
            self.assertEqual(backend.translate_submit_kwargs({'mem': value})['mem_mb'],
                             expected_mb, value)

    def test_lsf_mem_bad_value_raises(self):
        """A mem value that is not Slurm syntax must raise, not silently
        forward garbage units to the farm."""
        backend = get_scheduler('lsf')
        with self.assertRaises(ValueError):
            backend.translate_submit_kwargs({'mem': 'thirty-two gig'})

    def test_lsf_unknown_kwargs_still_flow_through(self):
        """Unknown kwargs reach the submit script so its unknown-argument
        guard fires -- translation must not swallow genuinely unsupported
        options (that guard is load-bearing; card constraint: don't weaken
        it)."""
        backend = get_scheduler('lsf')
        translated = backend.translate_submit_kwargs({'mem': '32G', 'banana': 'yes'})
        self.assertEqual(translated, {'mem_mb': 32768, 'banana': 'yes'})

    def test_translation_changes_nothing_without_mem_or_nice(self):
        for name in ('slurm', 'lsf'):
            backend = get_scheduler(name)
            kwargs = {'mins': 720, 'queue': 'lnx64'}
            self.assertEqual(backend.translate_submit_kwargs(kwargs), kwargs)

    def test_base_backend_identity(self):
        """The base class default is identity (backends without translation
        needs inherit it; the DEWET slurm script's frozen behavior is the
        precedent)."""
        from aview_hpc.schedulers import SchedulerBackend
        kwargs = {'mem': '32G', 'nice': 1}
        self.assertEqual(SchedulerBackend().translate_submit_kwargs(kwargs), kwargs)


class TestLSFMemMb(unittest.TestCase):
    """hpc_scripts/lsf.py --mem_mb: the translated memory request renders as
    rusage[mem=<MB>] inside the ONE -R (multiple -R are rejected by this
    LSF; see build_bsub_command)."""

    def test_rusage_merged_into_single_r(self):
        cmd = lsf_script.build_bsub_command(
            script_file='myjob_0.lsf', job_name='myjob', mins=720,
            queue='lnx64', project='MSC:2023.4:NNL:SIMULATION',
            res_req='select[(OSMJR==8 && OSMNR>=6) || OSMJR>=9]', n_cpus=8,
            mem_mb=32768)
        self.assertEqual(cmd.count(' -R '), 1)
        self.assertIn(
            "-R 'rusage[mem=32768] select[(OSMJR==8 && OSMNR>=6) || OSMJR>=9] "
            "span[hosts=1]'", cmd)

    def test_mem_mb_without_res_req(self):
        cmd = lsf_script.build_bsub_command(
            script_file='myjob_0.lsf', job_name='myjob', mins=720,
            queue='lnx64', project='MSC:2023.4:NNL:SIMULATION',
            res_req='', n_cpus=8, mem_mb=32768)
        self.assertEqual(cmd.count(' -R '), 1)
        self.assertIn("-R 'rusage[mem=32768] span[hosts=1]'", cmd)

    def test_explicit_rusage_in_res_req_wins(self):
        """A site res_req that already carries rusage[ is never overridden or
        doubled."""
        cmd = lsf_script.build_bsub_command(
            script_file='myjob_0.lsf', job_name='myjob', mins=720,
            queue='lnx64', project='MSC:2023.4:NNL:SIMULATION',
            res_req='rusage[mem=1024] select[x]', n_cpus=8, mem_mb=32768)
        self.assertEqual(cmd.count(' -R '), 1)
        self.assertIn("-R 'rusage[mem=1024] select[x] span[hosts=1]'", cmd)
        self.assertNotIn('32768', cmd)

    def test_no_mem_mb_unchanged(self):
        """mem_mb=None keeps the command byte-identical to the pre-0.4.2
        form (regression pin for existing callers)."""
        with_mem = lsf_script.build_bsub_command(
            script_file='myjob_0.lsf', job_name='myjob', mins=720,
            queue='lnx64', project='MSC:2023.4:NNL:SIMULATION',
            res_req='select[x]', n_cpus=8, mem_mb=None)
        self.assertNotIn('rusage', with_mem)
        self.assertEqual(with_mem.count(' -R '), 1)

    def test_mem_mb_must_be_positive(self):
        with self.assertRaises(ValueError):
            lsf_script.build_bsub_command(
                script_file='myjob_0.lsf', job_name='myjob', mins=720,
                queue='lnx64', project='MSC:2023.4:NNL:SIMULATION',
                res_req='select[x]', n_cpus=8, mem_mb=0)

    def test_mem_mb_cli_dry_run_end_to_end(self):
        """--mem_mb on the real argv path: parsed, forwarded to submit(), and
        rendered in the dry-run bsub line."""
        with TemporaryDirectory() as tmpdir:
            writer = TestLSFScript()
            acf = writer._write_model(tmpdir)
            argv = ['lsf.py', str(acf), '--mins', '5',
                    '--project', 'MSC:2023.4:NNL:SIMULATION',
                    '--adams_home', '/home/thornton/adams/2023_4_1',
                    '--license', '1700@sjflex5', '--mem_mb', '32768',
                    '--dry_run']
            with patch.object(sys, 'argv', argv), patch('sys.stdout', new=StringIO()) as out:
                lsf_script.main()
            output = out.getvalue()

        bsub_line = next(l for l in output.splitlines()
                         if l.startswith('DRY-RUN: would run:'))
        self.assertEqual(bsub_line.count(' -R '), 1)
        self.assertIn('rusage[mem=32768]', bsub_line)

    def test_mem_kwarg_still_rejected_by_lsf_script(self):
        """The generic --mem (untranslated) must STILL be refused by lsf.py:
        the unknown-argument guard is the backstop for submit hosts whose
        aview_hpc predates translation (card constraint: don't weaken it)."""
        with TemporaryDirectory() as tmpdir:
            writer = TestLSFScript()
            acf = writer._write_model(tmpdir)
            argv = ['lsf.py', str(acf), '--mins', '5',
                    '--project', 'MSC:2023.4:NNL:SIMULATION',
                    '--adams_home', '/a', '--license', '1700@x',
                    '--mem', '32G', '--nice', '2147483645', '--dry_run']
            with patch.object(sys, 'argv', argv):
                with self.assertRaises(SystemExit) as ctx:
                    lsf_script.main()
            self.assertIn('would become the job COMMAND', str(ctx.exception))
            self.assertIn('--mem', str(ctx.exception))


class TestSessionAppliesTranslation(unittest.TestCase):
    """HPCSession.submit/resubmit_job run kwargs through the backend's
    translate_submit_kwargs BEFORE the argv is handed to the transport
    (0.5.0 seam), so both SSHTransport and LocalTransport submit the
    translated argv. The SSH case is verified against the transport seam
    (what exec_argv would have sent down the wire); the local case runs
    the REAL LocalTransport against a recording fake submit script."""

    def _make_session(self, scheduler_name, transport):
        from aview_hpc._cli import HPCSession
        session = HPCSession.__new__(HPCSession)
        session.backend = get_scheduler(scheduler_name)
        session.transport = transport
        # Attributes __init__ would have set; skipped by __new__.
        session.job_name = None
        session.job_id = None
        session.remote_dir = None
        return session

    @staticmethod
    def _ssh_style_transport(response='Job <530967> is submitted to queue lnx64'):
        """A transport shaped like SSHTransport as _cli uses it: exec_argv
        joins argv and shells it, so record the JOINED command exactly as
        the SSH path would forward it."""
        forwarded = {}

        def fake_exec_shell(command):
            forwarded['cmd'] = command
            return response, ''

        def fake_exec_argv(argv):
            return fake_exec_shell(' '.join(str(a) for a in argv))

        transport = types.SimpleNamespace(
            exec_argv=fake_exec_argv,
            exec_shell=fake_exec_shell,
            # submit() uploads the acf/adm before running the command; the
            # translation under test happens after, so the file ops can be
            # no-ops (file contents are irrelevant to the argv check).
            put_file=lambda local_path, remote_path: None,
            copy_file=lambda src, dst: None)
        return transport, forwarded

    @staticmethod
    def _fake_submit_script(directory: Path) -> Path:
        """A recording lsf.py stand-in: echoes its argv into the job dir so
        the test can see EXACTLY what a local-transport child received."""
        script = directory / 'fake_submit_recording.py'
        script.write_text(
            '#!{python}\n'
            'import os, sys\n'
            'argv_file = os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), "child_argv.txt")\n'
            'with open(argv_file, "w") as fh:\n'
            '    fh.write(" ".join(sys.argv[1:]))\n'
            'print("Job <530967> is submitted to queue lnx64")\n'
            .format(python=sys.executable))
        return script

    def _local_session(self, scheduler_name='lsf'):
        """A session over a REAL LocalTransport with a recording fake
        submit script as submit_cmd (multi-word, like the VM config)."""
        from aview_hpc.transport import LocalTransport
        tmp = tempfile.mkdtemp(prefix='t8e_local_')
        fake = self._fake_submit_script(Path(tmp))
        job_dir = Path(tmp) / 'wd'
        job_dir.mkdir()
        (job_dir / 'myjob.acf').write_text('myjob.adm\n')
        (job_dir / 'myjob.adm').write_text('model')
        transport = LocalTransport(remote_root=Path(tmp))
        session = self._make_session(scheduler_name, transport)
        session.submit_cmd = '"{python}" {script}'.format(
            python=sys.executable, script=fake.as_posix())
        session.mkdtemp_remote = lambda name=None, n_rand=4: job_dir
        session.uploaded_files = {}
        return session, Path(tmp), job_dir

    def test_submit_lsf_translates_before_ssh_transport(self):
        transport, forwarded = self._ssh_style_transport()
        session = self._make_session('lsf', transport)
        session.submit_cmd = '/home/thornton/scripts/lsf.py'

        models = Path(__file__).parent / 'models'
        session.mkdtemp_remote = lambda name=None, n_rand=4: Path('/remote/wd')
        session.uploaded_files = {}
        session.submit(acf_file=models / 'test.acf', adm_file=models / 'test.adm',
                       mins=1000, mem='32G', nice=2147483645)

        cmd = forwarded['cmd']
        self.assertIn('--mem_mb 32768', cmd)
        # No untranslated --mem / --nice tokens ('--mem_mb' contains the
        # substring '--mem', so split into tokens first).
        tokens = cmd.split()
        self.assertNotIn('--mem', tokens)
        self.assertNotIn('--nice', tokens)
        self.assertNotIn('2147483645', cmd)

    def test_submit_slurm_forwards_verbatim_through_ssh_transport(self):
        transport, forwarded = self._ssh_style_transport(
            'Submitted batch job 4242')
        session = self._make_session('slurm', transport)
        session.submit_cmd = 'sbatch_script.sh'

        models = Path(__file__).parent / 'models'
        session.mkdtemp_remote = lambda name=None, n_rand=4: Path('/remote/wd')
        session.uploaded_files = {}
        session.submit(acf_file=models / 'test.acf', adm_file=models / 'test.adm',
                       mins=1000, mem='32G', nice=2147483645)

        cmd = forwarded['cmd']
        self.assertIn('--mem 32G', cmd)
        self.assertIn('--nice 2147483645', cmd)

    def test_resubmit_lsf_translates_before_ssh_transport(self):
        transport, forwarded = self._ssh_style_transport(
            'Job <530968> is submitted to queue lnx64')
        session = self._make_session('lsf', transport)
        session.submit_cmd = '/home/thornton/scripts/lsf.py'

        def fake_exec_cleanup(backend, remote_dir):
            pass

        session.transport = types.SimpleNamespace(
            exec_argv=lambda argv: (
                forwarded.__setitem__('cmd', ' '.join(str(a) for a in argv)),
                ('Job <530968> is submitted to queue lnx64', ''))[1],
            exec_cleanup=fake_exec_cleanup,
            listdir=lambda d: ['myjob.acf'])

        session.resubmit_job(Path('/remote/wd'), mins=720, mem='32G',
                             nice=2147483645)

        cmd = forwarded['cmd']
        self.assertIn('--mem_mb 32768', cmd)
        self.assertNotIn('--nice', cmd)

    def test_submit_lsf_translates_through_real_local_transport(self):
        """The full local-mode stack: a real LocalTransport spawns a real
        child process (the recording fake submit script), proving the
        translated argv reaches an actually-spawned scheduler child."""
        session, tmp, job_dir = self._local_session()
        try:
            models = Path(__file__).parent / 'models'
            session.submit(acf_file=models / 'test.acf', adm_file=models / 'test.adm',
                           mins=1000, mem='32G', nice=2147483645)

            echo = job_dir / 'child_argv.txt'
            self.assertTrue(echo.exists(),
                            'child did not record argv; dir has '
                            f'{session.transport.listdir(job_dir.as_posix())}')
            argv = echo.read_text().split()
            self.assertIn('--mem_mb', argv)
            self.assertIn('32768', argv)
            self.assertNotIn('--mem', argv)
            self.assertNotIn('--nice', argv)
            self.assertNotIn('2147483645', argv)
            self.assertIn('--mins', argv)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_resubmit_lsf_translates_through_real_local_transport(self):
        session, tmp, job_dir = self._local_session()
        try:
            session.resubmit_job(job_dir, mins=720, mem='32G',
                                 nice=2147483645)

            echo = job_dir / 'child_argv.txt'
            self.assertTrue(echo.exists())
            argv = echo.read_text().split()
            self.assertIn('--mem_mb', argv)
            self.assertIn('32768', argv)
            self.assertNotIn('--nice', argv)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestSubmitMultiRoundTripWithNoisyLayers(unittest.TestCase):
    """t_11595f7b acceptance: a ``submit_multi`` ROUND TRIP where chatter
    reaches stdout at BOTH layers still returns the parsed JSON.

    The full client stack is exercised, in-process, with no cluster:

    ``aview_hpc.aview_hpc.submit_multi`` (the client wrapper the CDM
    harness calls) -> ``run_cli`` (POSIX in-process branch) ->
    ``_cli.main`` (the REAL CLI: argparse, HPCSession.submit, backend
    translation, LocalTransport) -> a fake submit script (stand-in for
    ~/scripts/lsf.py) run as a REAL child process that prints noise to
    ITS stdout before the LSF submission line.

    On top of that, the HOST root logger carries a print-based handler
    (CDM's AdamsLogFileHandler shape) -- the exact CI run 37923731507
    configuration that made ``json.loads(out)`` die at char 0 after LSF
    1337242 was already submitted.
    """

    class _PrintRootHandler(logging.Handler):
        def emit(self, record):
            print(self.format(record))

    def _fake_submit_script(self, directory: Path) -> Path:
        """Stand-in lsf.py: prints noise lines to stdout BEFORE the LSF
        'Job <id> is submitted' line (real lsf.py prints 'Running: bsub
        ...'; bsub prints its own line after)."""
        script = directory / 'fake_lsf_noisy.py'
        script.write_text(
            '#!{python}\n'
            'import sys\n'
            'print("Running: bsub -q lnx64 -P ADAMS:2023.4.1:AE:qual:NNL ...")\n'
            'print("Job <4242> is submitted to queue lnx64")\n'
            .format(python=sys.executable))
        return script

    def _fake_adm(self, directory: Path, name: str) -> Path:
        adm = directory / name
        adm.write_text('model\n')
        return adm

    def test_round_trip_with_stdout_chatter_and_host_print_handler(self):
        import aview_hpc.platform as platform_mod
        from aview_hpc import aview_hpc as client_mod
        from aview_hpc.transport import LocalTransport

        tmp = tempfile.mkdtemp(prefix='t11595_roundtrip_', dir=str(Path.home()))
        root_logger = logging.getLogger()
        saved_handlers = list(root_logger.handlers)
        saved_level = root_logger.level
        ph = self._PrintRootHandler()
        try:
            # Host state exactly as the CDM plugin leaves it inside Adams
            # View: a print-based root handler at INFO.
            for h in saved_handlers:
                root_logger.removeHandler(h)
            root_logger.addHandler(ph)
            root_logger.setLevel(logging.INFO)

            fake = self._fake_submit_script(Path(tmp))
            adm = self._fake_adm(Path(tmp), 'set_1.adm')
            acf = Path(tmp) / 'set_1.acf'
            acf.write_text('set_1.adm\n')

            # The REAL config machinery must resolve to this fake submit
            # command + local transport + lsf backend + a farm-visible
            # root (the tmp dir), without touching ~/.aview_hpc.
            cfg = {'host': 'localhost',
                   'username': 'testuser',
                   'scheduler': 'lsf',
                   'submit_cmd': f'"{sys.executable}" {fake.as_posix()}',
                   'remote_tempdir': tmp,
                   'transport': 'local'}

            with patch.object(platform_mod, 'IS_WINDOWS', False), \
                 patch('aview_hpc._cli.get_config', return_value=cfg), \
                 patch('aview_hpc.config.get_config', return_value=cfg), \
                 patch('aview_hpc.transport.get_config', return_value=cfg):
                remote_dirs, job_names, job_ids = client_mod.submit_multi(
                    acf_files=[acf],
                    adm_files=[adm],
                    aux_files=[[]],
                    mins=50400,   # the harness's real kwarg (base_test MINS)
                    mem='32G',    # JOB_MEM_GB
                    nice=2147483645)  # MAX_NICE

            self.assertEqual(len(remote_dirs), 1)
            self.assertEqual(job_names, ['set_1'])
            self.assertEqual(job_ids, [4242])
            self.assertIn(tmp, str(remote_dirs[0]))
        finally:
            root_logger.removeHandler(ph)
            for h in saved_handlers:
                root_logger.addHandler(h)
            root_logger.setLevel(saved_level)
            shutil.rmtree(tmp, ignore_errors=True)

    def test_parse_cli_json_defensive_layers(self):
        from aview_hpc.aview_hpc import _parse_cli_json
        # Clean buffer: parsed directly.
        self.assertEqual(_parse_cli_json('{"a": 1}\n', 'x'), {'a': 1})
        # Chatter before the payload: last JSON line wins.
        noisy = ('Running: bsub -q lnx64\n'
                 'Output: Job <4242> is submitted to queue lnx64\n'
                 '{"remote_dirs": ["/r/d"], "job_names": ["j"], "job_ids": [7]}\n')
        self.assertEqual(_parse_cli_json(noisy, 'submit_multi'),
                         {'remote_dirs': ['/r/d'], 'job_names': ['j'],
                          'job_ids': [7]})
        # A log line that PARSES as a JSON object but is not the payload
        # would be a false positive only if it came AFTER the payload --
        # the CLI's payload is the final print, so scanning from the end
        # finds the payload first even if a JSON-looking log line exists.
        noisy2 = ('{"log": "not the payload"}\n'
                  '{"remote_dirs": ["/r/d2"], "job_names": ["j2"], "job_ids": [8]}\n')
        self.assertEqual(_parse_cli_json(noisy2, 'submit_multi')['job_ids'], [8])
        # Nothing parseable: RuntimeError (which the CDM harness retries),
        # carrying the captured stdout for diagnosis.
        with self.assertRaises(RuntimeError) as ctx:
            _parse_cli_json('no json here at all\n', 'submit_multi')
        self.assertIn('submit_multi', str(ctx.exception))
        self.assertIn('no json here', str(ctx.exception))


class TestGetJobTableRoundTrip(unittest.TestCase):
    """t_0f552d9b: the ExitCode column must survive the client wrapper's
    CSV round trip as a clean string, the way CDM's scheduler_state reads
    it (str(value) + blank guard -> note ' (exit code 27)').

    The CLI prints the DataFrame with ``df.to_csv(index=False)`` and the
    client wrapper re-parses with ``pd.read_csv``.  A column mixing '27'
    and '' would re-parse as float64 (27.0 / NaN) WITHOUT the dtype pin in
    ``aview_hpc.aview_hpc.get_job_table`` -- making CDM's note read
    '(exit code 27.0)'.  These tests pin the pin.
    """

    def _client_table(self, csv_text):
        """The wrapper's parse step, exactly as shipped."""
        import aview_hpc.aview_hpc as client
        with patch.object(client, 'run_cli', return_value=(csv_text, '')):
            return client.get_job_table()

    def test_exit_code_survives_csv_round_trip_as_string(self):
        # What the CLI prints for the fixture table (EXIT '27' + blanks).
        csv_text = (BJOBS_SAMPLE.parent / 'bjobs_cli_output.csv').read_text()
        df = self._client_table(csv_text)
        failed = df[df['State'] == 'FAILED'].iloc[0]
        self.assertEqual(failed['ExitCode'], '27')
        # String dtype (pandas 2 'object', pandas 3 'str') -- never float64.
        self.assertIn(str(df['ExitCode'].dtype), ('object', 'str'))
        # CDM's exact consumption path: str() + blank guard -> note.
        exit_code = failed['ExitCode']
        note_src = '' if pd.isna(exit_code) else str(exit_code).strip()
        self.assertEqual(note_src, '27')
        self.assertEqual(f' (exit code {note_src})', ' (exit code 27)')
        # Blank rows stay blank, not NaN-as-float.
        done = df[df['State'] == 'COMPLETED'].iloc[0]
        self.assertTrue(done['ExitCode'] == '' or pd.isna(done['ExitCode']))

    def test_dtype_pin_tolerates_a_table_without_the_column(self):
        """A 0.5.2-shaped CSV (no ExitCode) must parse identically: an
        unknown column name in read_csv(dtype=...) is ignored."""
        csv_text = ('JobID,JobName,State\n'
                    '1,old_shape,FAILED\n')
        df = self._client_table(csv_text)
        self.assertEqual(list(df.columns), ['JobID', 'JobName', 'State'])
        self.assertEqual(df['State'].iloc[0], 'FAILED')

    def test_unpinned_parse_would_erosion_the_code(self):
        """RED guard: without the pin the value parses as 27.0 -- this
        documents WHY the pin exists (float64 re-type of a mixed column)."""
        csv_text = ('JobID,ExitCode\n'
                    '1,27\n'
                    '2,\n')
        df = pd.read_csv(io.StringIO(csv_text))
        self.assertEqual(float(df['ExitCode'].iloc[0]), 27.0)
        self.assertEqual(str(df['ExitCode'].dtype), 'float64')


if __name__ == '__main__':
    unittest.main()
