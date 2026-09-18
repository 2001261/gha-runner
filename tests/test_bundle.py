"""打包与入口探测的单测。"""

from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from gha_runner import bundle, core


class TestPackBundle(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.src = self.tmp / "task"
        (self.src / "inputs").mkdir(parents=True)
        (self.src / "task.sh").write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
        (self.src / "inputs" / "a.txt").write_text("x\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def test_pack_and_extract_roundtrip(self):
        out = self.tmp / "b.tgz"
        bundle.pack_bundle(self.src, out)
        self.assertTrue(out.is_file())
        self.assertGreater(out.stat().st_size, 0)

        dest = self.tmp / "unp"
        bundle.extract_bundle(out, dest)
        self.assertEqual((dest / "task.sh").read_text(encoding="utf-8"),
                         "#!/bin/bash\necho hi\n")
        self.assertEqual((dest / "inputs" / "a.txt").read_text(encoding="utf-8"), "x\n")

    def test_arcnames_use_dot_slash_prefix(self):
        # 与 `tar czf out -C dir .` 的产物一致，runner 侧 tar xzf 才解得对
        out = self.tmp / "b.tgz"
        bundle.pack_bundle(self.src, out)
        names = bundle.list_bundle(out)
        self.assertIn("./task.sh", names)
        self.assertIn("./inputs/a.txt", names)

    def test_ds_store_excluded(self):
        (self.src / ".DS_Store").write_bytes(b"junk")
        (self.src / "inputs" / ".DS_Store").write_bytes(b"junk")
        out = self.tmp / "b.tgz"
        bundle.pack_bundle(self.src, out)
        names = bundle.list_bundle(out)
        self.assertEqual([n for n in names if ".DS_Store" in n], [],
                         ".DS_Store 必须被排除")

    def test_appledouble_excluded(self):
        (self.src / "._task.sh").write_bytes(b"junk")
        out = self.tmp / "b.tgz"
        bundle.pack_bundle(self.src, out)
        names = bundle.list_bundle(out)
        self.assertEqual([n for n in names if n.rsplit("/", 1)[-1].startswith("._")], [],
                         "macOS AppleDouble 文件必须被排除")

    def test_entries_are_sorted_for_determinism(self):
        for i in range(5):
            (self.src / f"f{i}.txt").write_text(str(i), encoding="utf-8")
        out = self.tmp / "b.tgz"
        bundle.pack_bundle(self.src, out)
        names = bundle.list_bundle(out)
        self.assertEqual(names, sorted(names))

    def test_missing_dir_raises(self):
        from gha_runner.ui import GhaError
        with self.assertRaises(GhaError):
            bundle.pack_bundle(self.tmp / "nope", self.tmp / "x.tgz")

    def test_utf8_filename_survives(self):
        (self.src / "中文输入.txt").write_text("内容", encoding="utf-8")
        out = self.tmp / "b.tgz"
        bundle.pack_bundle(self.src, out)
        dest = self.tmp / "unp2"
        bundle.extract_bundle(out, dest)
        self.assertEqual((dest / "中文输入.txt").read_text(encoding="utf-8"), "内容")

    def test_path_traversal_in_bundle_is_rejected(self):
        # 手工安全解包必须拒绝 ../ 穿越，而不是靠 tarfile 的 filter=
        # （filter= 是 3.12 才加、3.9.17 才回补的，本包底线 3.9 上没有）
        import io
        import tarfile
        from gha_runner.ui import GhaError

        evil = self.tmp / "evil.tgz"
        payload = b"pwned"
        with tarfile.open(evil, "w:gz") as tf:
            ti = tarfile.TarInfo("../escaped.txt")
            ti.size = len(payload)
            tf.addfile(ti, io.BytesIO(payload))

        dest = self.tmp / "safe"
        with self.assertRaises(GhaError):
            bundle.extract_bundle(evil, dest)
        self.assertFalse((self.tmp / "escaped.txt").exists(), "解包不得写出目录之外")

    def test_absolute_path_in_bundle_is_rejected(self):
        import io
        import tarfile
        from gha_runner.ui import GhaError

        evil = self.tmp / "evil2.tgz"
        payload = b"pwned"
        with tarfile.open(evil, "w:gz") as tf:
            ti = tarfile.TarInfo("/tmp/gha-escape-test.txt")
            ti.size = len(payload)
            tf.addfile(ti, io.BytesIO(payload))

        with self.assertRaises(GhaError):
            bundle.extract_bundle(evil, self.tmp / "safe2")
        self.assertFalse(Path("/tmp/gha-escape-test.txt").exists())

    def test_symlink_escape_is_rejected(self):
        import tarfile
        from gha_runner.ui import GhaError

        evil = self.tmp / "evil3.tgz"
        with tarfile.open(evil, "w:gz") as tf:
            ti = tarfile.TarInfo("link")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "../../etc/passwd"
            tf.addfile(ti)

        with self.assertRaises(GhaError):
            bundle.extract_bundle(evil, self.tmp / "safe3")


class TestBase64(unittest.TestCase):
    def test_roundtrip_and_charset(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.bin"
            data = bytes(range(256)) * 40
            f.write_bytes(data)
            text = core.b64encode_file(f)
            self.assertNotIn("\n", text, "base64 必须是单行")
            self.assertEqual(set(text) - set(
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="), set(),
                "base64 只含安全字符 —— 这是它能直接进 JSON 的前提")
            self.assertEqual(core.b64decode(text), data)

    def test_make_b64_writes_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src = d / "t"
            src.mkdir()
            (src / "task.sh").write_text("echo hi\n", encoding="utf-8")
            b = d / "b.tgz"
            bundle.pack_bundle(src, b)
            text, size = bundle.make_b64(b, d / "b.b64")
            self.assertEqual(size, b.stat().st_size)
            self.assertEqual((d / "b.b64").read_text(encoding="ascii"), text)
            self.assertNotIn("\n", (d / "b.b64").read_text(encoding="ascii"))


class TestEntryDetection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _mk(self, name: str, *files: str) -> Path:
        d = self.tmp / name
        d.mkdir()
        for f in files:
            (d / f).write_text("x", encoding="utf-8")
        return d

    def test_finds_bash_entry(self):
        self.assertEqual(bundle.find_entry(self._mk("a", "task.sh")), "task.sh")

    def test_finds_python_entry(self):
        self.assertEqual(bundle.find_entry(self._mk("b", "task.py")), "task.py")

    def test_finds_node_entry(self):
        self.assertEqual(bundle.find_entry(self._mk("c", "task.js")), "task.js")

    def test_explicit_shell(self):
        d = self._mk("d", "task.py")
        self.assertEqual(bundle.find_entry(d, "python3"), "task.py")

    def test_explicit_shell_missing_returns_none(self):
        d = self._mk("e", "task.py")
        self.assertIsNone(bundle.find_entry(d, "bash"))

    def test_no_entry_returns_none(self):
        self.assertIsNone(bundle.find_entry(self._mk("f", "README.md")))

    def test_auto_prefers_sh_then_py_then_js(self):
        d = self._mk("g", "task.js", "task.py", "task.sh")
        self.assertEqual(bundle.find_entry(d), "task.sh")

    def test_shell_of_entry(self):
        self.assertEqual(bundle.shell_of_entry("task.py"), "python3")
        self.assertEqual(bundle.shell_of_entry("task.js"), "node")
        self.assertEqual(bundle.shell_of_entry("task.sh"), "bash")
        self.assertEqual(bundle.shell_of_entry("whatever"), "bash")


class TestTransportChoice(unittest.TestCase):
    def test_inline_below_limit(self):
        self.assertEqual(bundle.choose_transport(core.INLINE_B64_LIMIT), "inline")
        self.assertEqual(bundle.choose_transport(100), "inline")

    def test_branch_above_limit(self):
        self.assertEqual(bundle.choose_transport(core.INLINE_B64_LIMIT + 1), "branch")
        self.assertEqual(bundle.choose_transport(110400), "branch")

    def test_limit_leaves_headroom_under_hard_cap(self):
        # dispatch inputs 总 payload 硬上限 65535 字符
        self.assertLess(core.INLINE_B64_LIMIT, 65535)
        self.assertGreaterEqual(65535 - core.INLINE_B64_LIMIT, 5000,
                                "要给其余 7 个 input 字段留足余量")


if __name__ == "__main__":
    unittest.main()
