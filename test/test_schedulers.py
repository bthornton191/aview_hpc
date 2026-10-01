"""Unit tests for the scheduler backends and the LSF submit script.

These tests are pure unit tests: they run against captured fixture text
(no live cluster needed). The fixture `bjobs_sample.json` contains records
captured verbatim from the Cadence sjlsf01 farm on 2026-10-01 (LSF
10.1.0.15) plus synthetic records for states not present in the capture.
"""
import importlib.util
import re
import sys
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
        self.assertIn('nthreads', cmd)
        self.assertIn('nexec_host', cmd)

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

        pending = df[df['JobID'] == 5800001].iloc[0]
        self.assertTrue(pd.isna(pending['Start']))
        self.assertEqual(pending['Elapsed'], '00:00:00')

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
        self.assertIn("-R 'select[(OSMJR==8 && OSMNR>=6) || OSMJR>=9]'", cmd)
        self.assertIn("-R 'span[hosts=1]'", cmd)
        self.assertIn(' -n 8 ', cmd)
        self.assertIn(' -J myjob ', cmd)
        self.assertIn(' -oo myjob.log ', cmd)
        self.assertIn(' -eo myjob.err ', cmd)
        self.assertIn(' -u noemail ', cmd)
        self.assertTrue(cmd.startswith('/grid/sfi/farm/bin/bsub '))
        self.assertTrue(cmd.endswith(' myjob_0.lsf'))

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
        self.assertIn('-R', output)
        self.assertIn('span[hosts=1]', output)
        self.assertIn('-n 8', output)
        self.assertIn('MSC_LICENSE_FILE=1700@sjflex5', output)
        self.assertIn('/home/thornton/adams/2023_4_1/mdi -c ru-s i m.acf exit', output)
        self.assertNotIn('Job <', output)

    def test_project_required(self):
        with TemporaryDirectory() as tmpdir:
            acf = self._write_model(tmpdir)
            argv = ['lsf.py', str(acf), '--adams_home', '/a', '--license', '1700@x']
            with patch.object(sys, 'argv', argv):
                with self.assertRaises(SystemExit) as ctx:
                    lsf_script.main()
            self.assertEqual(ctx.exception.code, 2)

    def test_project_format_validated(self):
        with TemporaryDirectory() as tmpdir:
            acf = self._write_model(tmpdir)
            argv = ['lsf.py', str(acf), '--project', 'no-colons',
                    '--adams_home', '/a', '--license', '1700@x']
            with patch.object(sys, 'argv', argv):
                with self.assertRaises(SystemExit):
                    lsf_script.main()

    def test_adams_home_required(self):
        with TemporaryDirectory() as tmpdir:
            acf = self._write_model(tmpdir)
            argv = ['lsf.py', str(acf), '--project', 'MSC:2023.4:NNL:SIMULATION',
                    '--license', '1700@x']
            with patch.object(sys, 'argv', argv):
                with self.assertRaises(SystemExit):
                    lsf_script.main()


if __name__ == '__main__':
    unittest.main()
