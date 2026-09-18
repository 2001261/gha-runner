"""凭据扫描的单测 —— 安全关键模块，正反例都要覆盖。

两条铁律各有断言：
  * 命中时必须拦住
  * 命中详情里【绝不能出现敏感值本身】，只能是 文件:行号
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gha_runner import core, secrets

# 每个样本的 (文件名, 内容, 用来断言"不得回显"的秘密值)
CONTENT_CASES = {
    "github_pat": ('task.sh', 'GH=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n',
                   "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    "github_oauth": ('task.sh', 'x=gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789\n',
                     "gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    "github_fine_grained": ('task.sh', 'x=github_pat_abcdefghijklmnopqrstuvwx\n',
                            "github_pat_abcdefghijklmnopqrstuvwx"),
    "aws_access_key": ('task.sh', 'export AWS_KEY=AKIAIOSFODNN7EXAMPLE\n',
                       "AKIAIOSFODNN7EXAMPLE"),
    "pem_private_key": ('task.sh', '-----BEGIN RSA PRIVATE KEY-----\nMIIESECRETBODY\n',
                        "MIIESECRETBODY"),
    "openai_key": ('task.sh', 'k=sk-abcdefghijklmnopqrstuvwxyz\n',
                   "sk-abcdefghijklmnopqrstuvwxyz"),
    "slack_token": ('task.sh', 'S=xoxb-1234567890-abcdefghij\n',
                    "xoxb-1234567890-abcdefghij"),
    "bearer_header": ('task.sh', 'curl -H "Bearer abcdefghijklmnopqrstuvwxyz1234"\n',
                      "abcdefghijklmnopqrstuvwxyz1234"),
    # bug #12 回归：bash 版漏掉了带引号的赋值
    "quoted_api_key": ('task.sh', 'api_key = "TESTFAKEVALUE_NOT_A_REAL_KEY_12345"\n',
                       "TESTFAKEVALUE_NOT_A_REAL_KEY_12345"),
    "single_quoted_secret": ('task.sh', "client_secret = 'abcdefgh12345678'\n",
                             "abcdefgh12345678"),
    "password_assign": ('task.sh', 'password=hunter2secret\n', "hunter2secret"),
    "no_space_colon": ('cfg.ini', 'TOKEN:abcd1234efgh\n', "abcd1234efgh"),
}

FILENAME_CASES = {
    "dotenv": (".env", "DB_PASS=hunter2\n", "hunter2"),
    "pem_file": ("server.pem", "not really a key\n", "not really a key"),
    "p12_file": ("keystore.p12", "binary-ish\n", "binary-ish"),
    "key_file": ("signing.key", "k\n", None),
    "ssh_key": ("id_rsa", "-----x-----\n", None),
    "credentials_file": ("credentials", "user:pass1234\n", "pass1234"),
    "token_in_name": ("auth_token.txt", "t\n", None),
    "secret_in_name": ("my-secrets.yml", "s\n", None),
    "nested_dotenv": ("sub/.env.production", "X=y12345678\n", "y12345678"),
}

CLEAN_CASES = {
    "plain_task": ('task.sh', '#!/bin/bash\necho hello\n'),
    "prose_word_token": ('task.sh', 'echo "the word token appears in prose but no assignment"\n'),
    "ordinary_url": ('task.sh', 'curl -s https://api.example.com/v1/data\n'),
    "variable_named_secret_no_value": ('task.sh', 'secret_name=myvar\necho done\n'),
    "short_value_below_threshold": ('task.sh', 'token=abc\n'),
    "env_var_reference": ('task.sh', 'echo "$MY_TOKEN" && env | grep -c PATH\n'),
    "readme_mentioning_secrets": ('README.md', 'Do not put secrets in this directory.\n'),
}


class ScanTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def make(self, relpath: str, content: str) -> Path:
        p = self.tmp / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        # 不用 Path.write_text(newline=...) —— 那是 3.10+ 才有的参数，本包底线是 3.9
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return p

    def assertNoLeak(self, rendered: str, secret) -> None:
        if secret:
            self.assertNotIn(secret, rendered,
                             "命中详情泄露了敏感值本身 —— 这是硬性禁止的")


class TestContentDetection(ScanTestCase):
    def test_all_content_patterns_hit(self):
        for name, (fn, content, secret) in CONTENT_CASES.items():
            with self.subTest(name):
                self.make(fn, content)
                ch, nh = secrets.scan_secrets(self.tmp)
                self.assertTrue(ch, f"{name}: 应命中内容特征但没命中")
                self.assertNoLeak(secrets.format_hits(ch, nh), secret)

    def test_hit_reports_file_and_line_only(self):
        self.make("task.sh", "#!/bin/bash\necho ok\napi_key = \"SUPERSECRETVALUE99\"\n")
        ch, _ = secrets.scan_secrets(self.tmp)
        self.assertEqual(len(ch), 1)
        self.assertTrue(ch[0].endswith("task.sh:3"), f"应是 文件:行号 形式，实际 {ch[0]!r}")
        rendered = secrets.format_hits(ch, [])
        self.assertIn("内容命中:", rendered)
        self.assertNotIn("SUPERSECRETVALUE99", rendered)


class TestFilenameDetection(ScanTestCase):
    def test_all_filename_patterns_hit(self):
        for name, (fn, content, secret) in FILENAME_CASES.items():
            with self.subTest(name):
                self.make(fn, content)
                ch, nh = secrets.scan_secrets(self.tmp)
                self.assertTrue(nh, f"{name}: 应命中文件名特征但没命中")
                self.assertNoLeak(secrets.format_hits(ch, nh), secret)


class TestNoFalsePositives(ScanTestCase):
    def test_clean_cases_pass(self):
        for name, (fn, content) in CLEAN_CASES.items():
            with self.subTest(name):
                for p in list(self.tmp.rglob("*")):
                    if p.is_file():
                        p.unlink()
                self.make(fn, content)
                ch, nh = secrets.scan_secrets(self.tmp)
                self.assertEqual((ch, nh), ([], []),
                                 f"{name}: 不该误杀，却命中了 {ch + nh}")

    def test_binary_file_skipped(self):
        (self.tmp / "blob.bin").write_bytes(b"\x00\x01ghp_" + b"A" * 36 + b"\x00")
        ch, nh = secrets.scan_secrets(self.tmp)
        self.assertEqual(ch, [], "二进制文件应被跳过（等价 grep -I）")


class TestRealTaskDirs(ScanTestCase):
    def test_shipped_smoke_task_is_clean(self):
        smoke = core.GHA_HOME / "tasks" / "smoke"
        if not smoke.is_dir():
            self.skipTest("tasks/smoke 不存在")
        ch, nh = secrets.scan_secrets(smoke)
        self.assertEqual((ch, nh), ([], []), f"随包示例任务必须干净，却命中 {ch + nh}")

    def test_skill_source_is_not_scanned_by_accident(self):
        # secrets.py 自己含有全部特征字符串（它们是正则字面量）。
        # 扫描只作用于任务目录，绝不能把 skill 自己的源码扫进去 ——
        # 否则任何调用都会命中自己。这里断言扫描 tasks/smoke 不会牵连到包目录。
        ch, nh = secrets.scan_secrets(core.GHA_HOME / "tasks" / "smoke")
        self.assertEqual((ch, nh), ([], []))


class TestFormatting(ScanTestCase):
    def test_format_is_deterministic_and_sorted(self):
        self.make("b.sh", 'password=aaaaaaaaaa\npassword=bbbbbbbbbb\n')
        self.make("a.sh", 'password=cccccccccc\n')
        ch, _ = secrets.scan_secrets(self.tmp)
        self.assertEqual(ch, sorted(ch), "命中列表必须排序，否则差分测试不稳定")
        self.assertEqual(secrets.format_hits(ch, []),
                         secrets.format_hits(ch, []))


if __name__ == "__main__":
    unittest.main()
