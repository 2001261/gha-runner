"""core 的单测：run 状态解析、config、blob sha、B 类闸门。

这里集中放 15 个已修 bug 里属于 core 的回归测试。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from gha_runner import core
from gha_runner.ui import EXIT_NOT_AUTHORIZED, GhaError, NotAuthorized


class WithTempRuns(unittest.TestCase):
    """把 core.RUNS_DIR 指到临时目录，避免测试污染真实的 .gha-runs/。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._saved = core.RUNS_DIR
        core.RUNS_DIR = self.tmp / ".gha-runs"

    def tearDown(self):
        core.RUNS_DIR = self._saved
        self._tmp.cleanup()


class TestResolveRunId(WithTempRuns):
    def test_pure_digits_without_mapping_is_the_run_id(self):
        self.assertEqual(core.resolve_run_id("35324416881"), "35324416881")

    def test_known_task_id_maps_to_run_id(self):
        core.run_set("smoke", "run_id", "111")
        self.assertEqual(core.resolve_run_id("smoke"), "111")

    def test_numeric_task_id_prefers_local_mapping(self):
        # task_id 本身可以是纯数字 —— 必须先查映射，查不到才当 run_id。
        # bash 版的 case 模式写反了（''*[!0-9]* 匹配的是"含非数字"），这是 bug #3 的回归测试。
        core.run_set("123", "run_id", "999")
        self.assertEqual(core.resolve_run_id("123"), "999")

    def test_numeric_without_mapping_falls_back_to_run_id(self):
        self.assertEqual(core.resolve_run_id("123"), "123")

    def test_unknown_non_numeric_raises(self):
        # bug #4 回归：校验失败必须真的中断（bash 版 die 在 $( ) 里 exit 传不出来，
        # 导致校验形同虚设、脚本继续往下跑）
        with self.assertRaises(GhaError):
            core.resolve_run_id("no-such-task")

    def test_out_override(self):
        out = self.tmp / "custom"
        core.run_set("ignored", "run_id", "555", str(out))
        self.assertEqual(core.resolve_run_id("ignored", str(out)), "555")


class TestResolveTaskId(WithTempRuns):
    def test_known_task_id_passes_through(self):
        core.run_set("smoke", "run_id", "111")
        self.assertEqual(core.resolve_task_id("smoke"), "smoke")

    def test_run_id_reverse_maps_via_by_run(self):
        (core.RUNS_DIR / "by-run" / "777").mkdir(parents=True)
        (core.RUNS_DIR / "by-run" / "777" / "task_id").write_text("big\n", encoding="utf-8")
        self.assertEqual(core.resolve_task_id("777"), "big")

    def test_run_id_without_reverse_map_returns_itself(self):
        self.assertEqual(core.resolve_task_id("888"), "888")

    def test_out_dir_name_may_differ_from_task_id(self):
        # bug #15 回归：--out 让目录名 ≠ task_id 时，artifact 名必须取记录的 task_id，
        # 否则会去找不存在的 result-<目录名>
        out = self.tmp / "customdir"
        core.run_set("whatever", "task_id", "smoke", str(out))
        core.run_set("whatever", "run_id", "42", str(out))
        self.assertEqual(core.run_get("whatever", "task_id", str(out)), "smoke")
        self.assertEqual(core.resolve_run_id("whatever", str(out)), "42")


class TestRunState(WithTempRuns):
    def test_set_get_roundtrip(self):
        core.run_set("t", "repo", "o/n")
        self.assertEqual(core.run_get("t", "repo"), "o/n")

    def test_get_missing_returns_empty(self):
        self.assertEqual(core.run_get("t", "nothing"), "")

    def test_value_is_stripped(self):
        core.run_set("t", "k", "  v  ")
        self.assertEqual(core.run_get("t", "k"), "v")

    def test_files_use_lf_not_crlf(self):
        # Windows 上写 CRLF 会污染 run 状态，导致 head -1 / 字符串比对出错
        core.run_set("t", "run_id", "42")
        raw = (core.run_dir("t") / "run_id").read_bytes()
        self.assertNotIn(b"\r", raw)


class TestConfig(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._saved = core.CONFIG_DIR
        core.CONFIG_DIR = Path(self._tmp.name)

    def tearDown(self):
        core.CONFIG_DIR = self._saved
        self._tmp.cleanup()

    def test_set_get_roundtrip(self):
        core.cfg_set("repo", "o/n")
        self.assertEqual(core.cfg_repo(), "o/n")

    def test_need_repo_raises_when_unset(self):
        with self.assertRaises(GhaError):
            core.need_repo()

    def test_need_repo_returns_value(self):
        core.cfg_set("repo", "2001261/agent-runner")
        self.assertEqual(core.need_repo(), "2001261/agent-runner")


class TestGitBlobSha(unittest.TestCase):
    def test_empty_blob_known_constant(self):
        # git hash-object -t blob /dev/null 的著名常量
        self.assertEqual(core.git_blob_sha(b""),
                         "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")

    def test_matches_real_git_when_available(self):
        git = shutil.which("git")
        if not git:
            self.skipTest("git 不可用")
        data = core.TEMPLATE.read_bytes() if core.TEMPLATE.is_file() else b"hello\n"
        expect = core.git_blob_sha(data)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "blob"
            f.write_bytes(data)
            got = subprocess.run([git, "hash-object", str(f)],
                                 stdout=subprocess.PIPE, check=True).stdout.decode().strip()
        self.assertEqual(expect, got, "hashlib 算出的 blob sha 必须与 git hash-object 一致")

    def test_dispatcher_template_sha_is_stable(self):
        if not core.TEMPLATE.is_file():
            self.skipTest("模板不存在")
        a = core.git_blob_sha(core.TEMPLATE.read_bytes())
        b = core.git_blob_sha(core.TEMPLATE.read_bytes())
        self.assertEqual(a, b)
        self.assertEqual(len(a), 40)


class TestConfirmB(unittest.TestCase):
    def test_authorized_executes(self):
        calls = []
        core.confirm_b("做某事", lambda: calls.append(1), authorized=True)
        self.assertEqual(calls, [1])

    def test_unauthorized_does_not_execute(self):
        # B 类闸门：没有 --yes 时绝不能执行副作用
        calls = []
        with self.assertRaises(NotAuthorized) as ctx:
            core.confirm_b("做某事", lambda: calls.append(1), authorized=False,
                           show="some-command --flag")
        self.assertEqual(calls, [], "未授权时不得执行")
        self.assertEqual(ctx.exception.code, EXIT_NOT_AUTHORIZED)
        self.assertEqual(ctx.exception.code, 125)


class TestGhResolution(unittest.TestCase):
    def test_resolve_returns_absolute_path_or_none(self):
        # bug #1 回归：bash 版用 `command -v gh`，若存在名为 gh 的 shell 函数会返回
        # 字符串 "gh" 而不是路径，执行时递归调用自己直到段错误。
        # shutil.which 只查 PATH 上的可执行文件，结构上不可能返回函数名。
        got = core.resolve_gh(force=True)
        if got is None:
            self.skipTest("本机没有 gh")
        self.assertTrue(Path(got).is_absolute(), f"必须是绝对路径，实际 {got!r}")
        self.assertNotEqual(got, "gh")
        self.assertTrue(Path(got).is_file())

    def test_explicit_env_override_wins(self):
        sentinel = shutil.which("sh") or shutil.which("cmd")
        if not sentinel:
            self.skipTest("找不到可用作哨兵的可执行文件")
        import os
        old = os.environ.get("GHA_GH_BIN")
        os.environ["GHA_GH_BIN"] = sentinel
        try:
            self.assertEqual(core.resolve_gh(force=True), sentinel)
        finally:
            if old is None:
                os.environ.pop("GHA_GH_BIN", None)
            else:
                os.environ["GHA_GH_BIN"] = old
            core.resolve_gh(force=True)


class TestHumanBytes(unittest.TestCase):
    def test_units(self):
        self.assertEqual(core.human_bytes(0), "0 B")
        self.assertEqual(core.human_bytes(512), "512 B")
        self.assertEqual(core.human_bytes(2048), "2.0 KB")
        self.assertEqual(core.human_bytes(5 * 1024 * 1024), "5.0 MB")


if __name__ == "__main__":
    unittest.main()
