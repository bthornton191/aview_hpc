import json
from pickle import loads
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from io import StringIO
from unittest.mock import patch

sys.path.append(str(Path(__file__).parent.parent))

from aview_hpc._cli import check_if_finished, get_config, get_job_table, get_remote_dir_status, get_results, resubmit_job  # noqa
from aview_hpc._cli import main as cli_main  # noqa
from aview_hpc._cli import submit, submit_multi  # noqa

TEST_ACF = Path(__file__).parent / 'models/test.acf'
TEST_ADM = Path(__file__).parent / 'models/test.adm'

TEST_MULTI_ACFS = [Path(__file__).parent / 'models/test_2.acf',
                   Path(__file__).parent / 'models/test_3.acf',
                   Path(__file__).parent / 'models/test_4.acf']

TEST_MULTI_ADMS = [Path(__file__).parent / 'models/test_2.adm',
                   Path(__file__).parent / 'models/test_2.adm',
                   Path(__file__).parent / 'models/test_2.adm']


class TestSubmit(unittest.TestCase):

    def test_submit(self):

        remote_dir, job_name, job_id = submit(acf_file=TEST_ACF, adm_file=TEST_ADM)

        self.assertIsNotNone(remote_dir)
        self.assertIsNotNone(job_name)
        self.assertIsNotNone(job_id)

    def test_submit_with_mins_specified(self):

        remote_dir, job_name, job_id = submit(acf_file=TEST_ACF, adm_file=TEST_ADM, mins=10, max_user_jobs=1)

        self.assertIsNotNone(remote_dir)
        self.assertIsNotNone(job_name)
        self.assertIsNotNone(job_id)

    def test_submit_multi(self):

        remote_dir, job_name, job_id = submit_multi(acf_files=TEST_MULTI_ACFS,
                                                    adm_files=TEST_MULTI_ADMS)

        self.assertIsNotNone(remote_dir)
        self.assertIsNotNone(job_name)
        self.assertIsNotNone(job_id)

    def test_submit_cli(self):
        sys.argv[1:] = ['submit', str(TEST_ACF), '--adm_file', str(TEST_ADM), '--mins', '10']
        with patch('sys.stdout', new=StringIO()) as fakeOutput:
            cli_main()
            output = json.loads(fakeOutput.getvalue().splitlines()[-1])

        self.assertListEqual(['remote_dir', 'job_name', 'job_id'], list(output))


class TestGetResults(unittest.TestCase):

    def setUp(self) -> None:
        self.remote_dir, self.job_name, self.job_id = submit(acf_file=TEST_ACF, adm_file=TEST_ADM)

    def test_get_results(self):

        with TemporaryDirectory() as temp_dir:
            files = []
            t_start = time.perf_counter()
            while not time.perf_counter() - t_start > 60:
                files = get_results(remote_dir=self.remote_dir, local_dir=Path(temp_dir))

                if len(files) > 0:
                    break
                time.sleep(5)

            self.assertTrue(len(files) > 0)
            self.assertTrue(all([f.exists() for f in files]))


class TestGetRemoteDirStatus(unittest.TestCase):

    def setUp(self) -> None:
        remote_dir, _, _ = submit(acf_file=TEST_ACF, adm_file=TEST_ADM)
        self.status = get_remote_dir_status(remote_dir)

    def test_get_remote_dir_status_len(self):
        self.assertTrue(len(self.status) > 0)
        self.assertCountEqual(['name',
                               'permissions',
                               'nlinks',
                               'owner',
                               'group',
                               'size',
                               'modified'],
                              self.status[0].keys())


class TestConfig(unittest.TestCase):

    def test_get_config(self):

        config = get_config()


class TestResubmitJob(unittest.TestCase):

    def setUp(self) -> None:
        self.remote_dir, self.job_name, self.job_id = submit(acf_file=TEST_ACF, adm_file=TEST_ADM)

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


class TestJobTable(unittest.TestCase):

    def setUp(self) -> None:
        self.remote_dir_1, self.job_name_1, self.job_id_1 = submit(acf_file=TEST_ACF, adm_file=TEST_ADM)
        self.remote_dir_2, self.job_name_2, self.job_id_2 = submit(acf_file=TEST_ACF, adm_file=TEST_ADM)

    def test_ascending_job_id(self):
        df = get_job_table()
        self.assertTrue(all(df['JobID'].diff().dropna() > 0))


