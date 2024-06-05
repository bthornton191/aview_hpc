import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from aview_hpc._cli import HPCSession

TEST_ACF = Path(__file__).parent / 'models/test.acf'
TEST_ADM = Path(__file__).parent / 'models/test.adm'

TEST_MULTI_ACFS = [Path(__file__).parent / 'models/test_2.acf',
                   Path(__file__).parent / 'models/test_3.acf',
                   Path(__file__).parent / 'models/test_4.acf']

TEST_MULTI_ADMS = [Path(__file__).parent / 'models/test_2.adm',
                   Path(__file__).parent / 'models/test_2.adm',
                   Path(__file__).parent / 'models/test_2.adm']


class TestHPCSession(unittest.TestCase):

    def setUp(self):
        self.hpc = HPCSession()
        self.hpc.submit(acf_file=TEST_ACF, adm_file=TEST_ADM)

    def tearDown(self):
        self.hpc.close()

    def test_get_results(self):

        # Call the method under test
        with TemporaryDirectory() as temp_dir:

            files = []
            t_start = time.perf_counter()
            while not time.perf_counter() - t_start > 60:
                files = self.hpc.get_results(local_dir=Path(temp_dir),
                                             extensions=['.msg'])
                if len(files) > 0:
                    break
                time.sleep(5)

            # Assert the results
            self.assertTrue(len(files) > 0)
            self.assertTrue(all([f.exists() for f in files]))

    def test_dir_status(self):

        # Call the method under test
        status = self.hpc.dir_status

        self.assertTrue(len(status) > 0)
        self.assertCountEqual(['name',
                               'permissions',
                               'nlinks',
                               'owner',
                               'group',
                               'size',
                               'modified'],
                              status[0].keys())


if __name__ == '__main__':
    unittest.main()
