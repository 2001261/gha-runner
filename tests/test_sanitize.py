"""输入校验与净化的单测。

对应 bash 版 /tmp/unit_lib.sh 的用例，输入与期望值逐条照搬，
另外补上 15 个已修 bug 里属于本模块的回归测试。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gha_runner import core


class TestValidators(unittest.TestCase):
    def test_valid_task_id(self):
        for ok in ("smoke", "a.b-c_1", "A" * 64, "0"):
            self.assertTrue(core.valid_task_id(ok), ok)

    def test_invalid_task_id(self):
        # bug 回归：斜杠与路径穿越必须被拒
        for bad in ("a/b", "../etc", "", "a b", "a;rm -rf /", "任务", "A" * 65):
            self.assertFalse(core.valid_task_id(bad), bad)

    def test_valid_runner(self):
        for ok in core.VALID_RUNNERS:
            self.assertTrue(core.valid_runner(ok), ok)

    def test_injection_runner_rejected(self):
        for bad in ("evil; rm -rf /", "ubuntu-latest\nfoo", "", "${{ github.token }}"):
            self.assertFalse(core.valid_runner(bad), bad)

    def test_valid_shell(self):
        for ok in ("auto", "bash", "python3", "node"):
            self.assertTrue(core.valid_shell(ok), ok)
        for bad in ("", "sh", "ruby", "bash;id"):
            self.assertFalse(core.valid_shell(bad), bad)

    def test_valid_repo_slug(self):
        for ok in ("o/n", "2001261/agent-runner", "my-org/my.repo"):
            self.assertTrue(core.valid_repo_slug(ok), ok)
        for bad in ("o n/x", "oname", "a/b/c", "", "/n", "o/"):
            self.assertFalse(core.valid_repo_slug(bad), bad)

    def test_valid_timeout_minutes(self):
        for ok in ("1", "60", "360"):
            self.assertTrue(core.valid_timeout_minutes(ok), ok)
        for bad in ("0", "361", "-5", "abc", ""):
            self.assertFalse(core.valid_timeout_minutes(bad), bad)


class TestDeriveTaskId(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_spaces_become_dashes(self):
        d = self.tmp / "my task_v2"
        d.mkdir()
        self.assertEqual(core.derive_task_id(d), "my-task_v2")

    def test_cjk_dir_yields_nonempty(self):
        # bug #7 回归：bash 版对全中文目录名净化后返回【空字符串】
        d = self.tmp / "中文任务"
        d.mkdir()
        tid = core.derive_task_id(d)
        self.assertTrue(tid, "中文目录名不能派生出空 task_id")
        self.assertTrue(core.valid_task_id(tid), f"派生结果非法: {tid!r}")

    def test_distinct_cjk_dirs_do_not_collide(self):
        a = self.tmp / "中文任务"
        b = self.tmp / "另一个中文"
        a.mkdir()
        b.mkdir()
        self.assertNotEqual(core.derive_task_id(a), core.derive_task_id(b))

    def test_stable_across_calls(self):
        d = self.tmp / "中文任务"
        d.mkdir()
        self.assertEqual(core.derive_task_id(d), core.derive_task_id(d))

    def test_leading_trailing_dashes_stripped(self):
        d = self.tmp / "--x--"
        d.mkdir()
        self.assertEqual(core.derive_task_id(d), "x")

    def test_truncated_to_64(self):
        d = self.tmp / ("a" * 100)
        d.mkdir()
        self.assertEqual(len(core.derive_task_id(d)), 64)


if __name__ == "__main__":
    unittest.main()
