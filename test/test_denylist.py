"""Tests for scripts/check_denylist.py identity handling.

The single address ALLOWED may appear in git author/committer/tagger fields
only.  Strings are assembled at runtime so this file passes the denylist.
"""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    'check_denylist', ROOT / 'scripts' / 'check_denylist.py')
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)

DOM = 'cadence' + '.com'
ALLOWED = f'thornton@{DOM}'


def _git(repo, *args, env=None):
    subprocess.run(['git', '-C', str(repo), *args], check=True,
                   capture_output=True, env=env)


class IdentityScanTests(unittest.TestCase):
    def _repo(self, author=ALLOWED, committer=ALLOWED, message='msg',
              content='x = 1\n', tag=None):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(d, ignore_errors=True))
        _git(d, 'init', '-q')
        (d / 'f.txt').write_text(content, encoding='utf-8')
        _git(d, 'add', 'f.txt')
        import os
        env = dict(os.environ, GIT_AUTHOR_NAME='Ben Thornton', GIT_AUTHOR_EMAIL=author,
                   GIT_COMMITTER_NAME='Ben Thornton', GIT_COMMITTER_EMAIL=committer)
        _git(d, '-c', 'commit.gpgsign=false', 'commit', '-q', '-m', message, env=env)
        if tag:
            _git(d, '-c', 'tag.gpgsign=false', 'tag', '-a', 'v1', '-m', tag, env=env)
        return d

    def _hits(self, repo):
        hits = []
        cd.scan_history(repo, [], hits)
        return hits

    def test_allowed_in_author_and_committer(self):
        self.assertEqual(self._hits(self._repo()), [])

    def test_allowed_in_tagger(self):
        self.assertEqual(self._hits(self._repo(tag='release')), [])

    def test_other_address_in_author_fails(self):
        self.assertTrue(self._hits(self._repo(author=f'someone@{DOM}')))

    def test_other_address_in_committer_fails(self):
        self.assertTrue(self._hits(self._repo(committer=f'a.thornton@{DOM}')))

    def test_lookalike_prefix_fails(self):
        self.assertTrue(self._hits(self._repo(author=f'xthornton@{DOM}')))

    def test_allowed_in_message_fails(self):
        self.assertTrue(self._hits(self._repo(message=f'by {ALLOWED}')))

    def test_allowed_in_content_fails(self):
        self.assertTrue(self._hits(self._repo(content=f'mail {ALLOWED}\n')))

    def test_allowed_in_tag_message_fails(self):
        self.assertTrue(self._hits(self._repo(tag=f'see {ALLOWED}')))

    def test_tree_scan_still_rejects_allowed_address(self):
        hits = []
        cd.scan_text('f.txt', ALLOWED, [], hits)
        self.assertTrue(hits)


if __name__ == '__main__':
    unittest.main()
