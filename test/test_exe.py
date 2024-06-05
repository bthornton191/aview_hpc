import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.append(str(Path(__file__).parent.parent))

from aview_hpc.aview_hpc import submit, get_results, get_remote_dir_status  # noqa
from aview_hpc._cli import submit as submit_cli  # noqa

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
