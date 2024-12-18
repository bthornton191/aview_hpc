import re
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))

from aview_hpc._cli import SLEEP_TIME, check_if_finished, get_job_table  # noqa
from aview_hpc._cli import get_remote_dir_status as get_remote_dir_status_cli  # noqa
from aview_hpc._cli import submit as submit_cli  # noqa
from aview_hpc.aview_hpc import get_remote_dir_status, get_results, resubmit_job, submit, submit_multi  # noqa

TEST_ACF = Path(__file__).parent / 'models/test.acf'
TEST_ADM = Path(__file__).parent / 'models/test.adm'


class TestSubmit(unittest.TestCase):

    def test_submit(self):

        remote_dir, job_name, job_id = submit(acf_file=TEST_ACF,
                                              adm_file=TEST_ADM,
                                              _log_level='DEBUG')

        self.assertIsNotNone(remote_dir)
        self.assertIsNotNone(job_name)
        self.assertIsNotNone(job_id)


class TestSubmitMulti(unittest.TestCase):
    def setUp(self) -> None:
        self.remote_dirs, self.job_names, self.job_ids = submit_multi(acf_files=[TEST_ACF]*3,
                                                                      adm_files=[TEST_ADM]*3)

    def test_delay(self):
        """Confirm that the start times are at least `SLEEP_TIME` apart
        """
        df = get_job_table()
        df = df[df['WorkDir'].isin([d.as_posix() for d in self.remote_dirs])]

        delays = (pd.to_datetime(df['Start'])
                  .diff()
                  .dt
                  .total_seconds()
                  .dropna())

        self.assertTrue(all(delays > SLEEP_TIME))


class TestGetResults(unittest.TestCase):

    def setUp(self) -> None:
        self.remote_dir, self.job_name, self.job_id = submit_cli(acf_file=TEST_ACF,
                                                                 adm_file=TEST_ADM)

    def test_get_results(self):

        with TemporaryDirectory() as temp_dir:

            files = []
            t_start = time.perf_counter()
            while not time.perf_counter() - t_start > 60:
                files = get_results(remote_dir=self.remote_dir,
                                    local_dir=Path(temp_dir),
                                    extensions=['.msg'],
                                    _log_level='DEBUG')
                if len(files) > 0:
                    break
                time.sleep(5)

            self.assertTrue(len(files) > 0)
            self.assertTrue(all([f.exists() for f in files]))


class TestGetRemoteDirStatus(unittest.TestCase):

    def setUp(self) -> None:
        self.remote_dir, self.job_name, self.job_id = submit_cli(acf_file=TEST_ACF,
                                                                 adm_file=TEST_ADM)

    def test_get_remote_dir_status(self):

        status = get_remote_dir_status(remote_dir=self.remote_dir)

        self.assertTrue(len(status) > 0)
        self.assertCountEqual(['name',
                               'permissions',
                               'nlinks',
                               'owner',
                               'group',
                               'size',
                               'modified',
                               'file'],
                              status[0].keys())


class TestResubmitJob(unittest.TestCase):
    NICE = 2147483645
    MEM = '32G'
    MINS = 7200

    def setUp(self) -> None:
        self.remote_dir, self.job_name, self.job_id = submit_cli(acf_file=TEST_ACF,
                                                                 adm_file=TEST_ADM)

        # Wait until job is completed
        t_start = time.perf_counter()
        while not time.perf_counter() - t_start > 120:

            if check_if_finished(self.remote_dir):
                break
            time.sleep(5)

    def test_resubmit_job(self):

        remote_dir, job_name, job_id = resubmit_job(self.remote_dir)

        self.assertIsNotNone(remote_dir)
        self.assertIsNotNone(job_name)
        self.assertIsNotNone(job_id)

    def test_resubmit_job_with_args(self):

        _, _, job_id = resubmit_job(self.remote_dir,
                                    mins=self.MINS,
                                    mem=self.MEM,
                                    nice=self.NICE)

        df = get_job_table()
        row = df[df['JobID'] == job_id].iloc[0]
        submit_line = row['SubmitLine']
        mins = int(re.search(r'--time=(\d+)', submit_line).group(1))
        mem = re.search(r'--mem=(\d+\w?)', submit_line).group(1)
        nice = int(re.search(r'--nice=(\d+)', submit_line).group(1))

        self.assertEqual(mins, self.MINS)
        self.assertEqual(mem, self.MEM)
        self.assertEqual(nice, self.NICE)
