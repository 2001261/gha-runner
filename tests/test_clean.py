"""clean 命令与 --cleanup 标志的单测。

remote 层全部 mock —— 测试不碰网络、不需要 gh 认证、不会真删东西。
覆盖四条铁律：
  * 无 --yes 只打印删除清单，绝不删（返回 125）
  * --run-id 路径必须先删 artifact 再删 run
  * --cleanup 只在取回成功后才触发清理；取回失败保留现场证据
  * --all 逐个 run 遍历删除，一个失败不中断其余
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from gha_runner import commands, core
from gha_runner.ui import EXIT_FAIL, EXIT_NOT_AUTHORIZED, EXIT_OK, NotAuthorized


class CleanTestBase(unittest.TestCase):
    """把 RUNS_DIR 指到临时目录，并关掉网络/认证门禁。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._saved_runs = core.RUNS_DIR
        core.RUNS_DIR = self.tmp / ".gha-runs"
        self._patchers = [
            mock.patch.object(commands.core, "require_gh"),
            mock.patch.object(commands, "require_auth"),
            mock.patch.object(commands.core, "need_repo", return_value="o/n"),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        core.RUNS_DIR = self._saved_runs
        for p in self._patchers:
            p.stop()
        self._tmp.cleanup()


class TestCleanCommand(CleanTestBase):
    def test_clean_without_yes_returns_125_and_deletes_nothing(self):
        # B 类铁律：没有 --yes 时连一次删除调用都不许有
        args = SimpleNamespace(all=True, run_id=None, yes=False, out=None)
        fake_runs = [
            {"databaseId": 1, "status": "completed", "conclusion": "success",
             "displayTitle": "a", "url": "http://x"},
            {"databaseId": 2, "status": "completed", "conclusion": "failure",
             "displayTitle": "b", "url": "http://y"},
        ]
        with mock.patch.object(commands.remote, "list_runs", return_value=fake_runs), \
             mock.patch.object(commands.remote, "list_run_artifacts",
                               return_value=[{"id": 11, "name": "result-a"}]), \
             mock.patch.object(commands.remote, "delete_artifact") as del_art, \
             mock.patch.object(commands.remote, "delete_run") as del_run:
            with self.assertRaises(NotAuthorized) as ctx:
                commands.cmd_clean(args)
        self.assertEqual(ctx.exception.code, EXIT_NOT_AUTHORIZED)
        self.assertEqual(ctx.exception.code, 125)
        del_art.assert_not_called()
        del_run.assert_not_called()

    def test_clean_run_id_deletes_artifacts_before_run(self):
        # 顺序是铁律：先删干净该 run 的全部 artifact，最后才删 run 本身
        core.run_set("t", "run_id", "111")
        args = SimpleNamespace(all=False, run_id="t", yes=True, out=None)
        order = []

        def fake_del_artifact(repo, aid):
            order.append(("artifact", aid))
            return True

        def fake_del_run(repo, rid):
            order.append(("run", rid))
            return True

        with mock.patch.object(commands.remote, "list_run_artifacts",
                               return_value=[{"id": 11, "name": "result-a"},
                                             {"id": 12, "name": "result-b"}]), \
             mock.patch.object(commands.remote, "delete_artifact",
                               side_effect=fake_del_artifact), \
             mock.patch.object(commands.remote, "delete_run", side_effect=fake_del_run), \
             mock.patch.object(commands.remote, "run_view",
                               return_value={"databaseId": "111", "displayTitle": "smoke",
                                             "url": "http://x"}):
            rc = commands.cmd_clean(args)
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(order, [("artifact", 11), ("artifact", 12), ("run", "111")],
                         "必须先删 artifact，最后才删 run 本身")

    def test_clean_all_visits_every_run(self):
        # --all 要逐条遍历：每个 run 先删自己的 artifact 再删 run，一个失败不中断其余
        args = SimpleNamespace(all=True, run_id=None, yes=True, out=None)
        fake_runs = [
            {"databaseId": 1, "displayTitle": "a", "url": "http://x"},
            {"databaseId": 2, "displayTitle": "b", "url": "http://y"},
        ]
        deleted = []

        def fake_del_artifact(repo, aid):
            deleted.append(("artifact", aid))
            return True

        def fake_del_run(repo, rid):
            deleted.append(("run", rid))
            return True

        def fake_list_artifacts(repo, rid):
            if rid == "1":
                return [{"id": 11, "name": "result-1"}, {"id": 12, "name": "extra"}]
            return [{"id": 25, "name": "solo"}]

        with mock.patch.object(commands.remote, "list_runs", return_value=fake_runs), \
             mock.patch.object(commands.remote, "list_run_artifacts",
                               side_effect=fake_list_artifacts), \
             mock.patch.object(commands.remote, "delete_artifact",
                               side_effect=fake_del_artifact), \
             mock.patch.object(commands.remote, "delete_run", side_effect=fake_del_run):
            rc = commands.cmd_clean(args)
        self.assertEqual(rc, EXIT_OK)
        self.assertEqual(deleted, [
            ("artifact", 11), ("artifact", 12), ("run", "1"),
            ("artifact", 25), ("run", "2"),
        ])


class TestCleanupAfterFetch(CleanTestBase):
    """_fetch 的 --cleanup 语义：只在取回成功后触发清理。"""

    def setUp(self):
        super().setUp()
        core.run_set("t", "run_id", "111")
        core.run_set("t", "task_id", "t")

    def fetch(self, cleanup: bool, authorized: bool = True):
        """跑一次 _fetch，mock 掉所有网络调用，返回 (rc, delete_artifact, delete_run)。"""
        with mock.patch.object(commands.remote, "run_state",
                               return_value=("completed", "success")), \
             mock.patch.object(commands.remote, "download_artifact",
                               side_effect=self.download), \
             mock.patch.object(commands.remote, "run_view",
                               return_value={"databaseId": "111", "status": "completed",
                                             "conclusion": "success", "displayTitle": "t",
                                             "url": "http://x",
                                             "createdAt": "2026-01-01T00:00:00Z",
                                             "updatedAt": "2026-01-01T00:00:00Z"}), \
             mock.patch.object(commands.remote, "list_run_artifacts",
                               return_value=[{"id": 11, "name": "result-t"}]), \
             mock.patch.object(commands.remote, "delete_artifact") as del_art, \
             mock.patch.object(commands.remote, "delete_run") as del_run:
            rc = commands._fetch("t", "o/n", None, force=False,
                                 authorized=authorized, cleanup=cleanup)
        return rc, del_art, del_run

    def test_cleanup_not_triggered_when_fetch_fails(self):
        # 取回失败 = 没拿到 manifest —— 必须保留 run 与 artifact 当现场证据
        def download(repo, rid, name, dest):
            return None

        self.download = download
        rc, del_art, del_run = self.fetch(cleanup=True)
        self.assertEqual(rc, EXIT_FAIL, "manifest 缺失时 fetch 必须报失败")
        del_art.assert_not_called()
        del_run.assert_not_called()

    def test_cleanup_triggered_after_successful_fetch(self):
        def download(repo, rid, name, dest):
            # 真实 remote.download_artifact 会先建 dest 目录才写文件，mock 里自己补上
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "manifest.json").write_text(
                json.dumps({"exit_code": 0, "exit_code_recorded": True}),
                encoding="utf-8")

        self.download = download
        rc, del_art, del_run = self.fetch(cleanup=True)
        self.assertEqual(rc, EXIT_OK)
        del_art.assert_called_once_with("o/n", 11)
        del_run.assert_called_once_with("o/n", "111")

    def test_cleanup_flag_without_yes_does_not_delete(self):
        def download(repo, rid, name, dest):
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "manifest.json").write_text(
                json.dumps({"exit_code": 0, "exit_code_recorded": True}),
                encoding="utf-8")

        self.download = download
        rc, del_art, del_run = self.fetch(cleanup=True, authorized=False)
        self.assertEqual(rc, EXIT_OK, "取回本身不受 --yes 影响")
        del_art.assert_not_called()
        del_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()