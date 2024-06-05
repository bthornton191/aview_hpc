import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from aview_hpc._cli import submit_multi

sys.path.append(str(Path(__file__).parent.parent))

from aview_hpc._cli import get_config, get_results, submit, get_remote_dir_status  # noqa
from aview_hpc.aview_hpc import check_if_finished  # noqa

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
