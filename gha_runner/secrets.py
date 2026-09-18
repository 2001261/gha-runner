"""凭据 / 隐私扫描。

公开仓库默认策略下的关键防线。两条铁律：
  1. **绝不回显命中的值**，只报 文件:行号
  2. 必须在**打包之前**跑 —— 命中时含凭据的 bundle 根本不该被创建，
     否则本地磁盘上会留下一份带密钥的副本（bash 版踩过这个坑）

残留风险（必须向用户说明，不能靠代码消除）：扫描是启发式的，只覆盖任务目录里的
**静态文本**，覆盖不到任务**运行期**才产生的敏感输出 —— 例如脚本去拉私有数据、
日志里打印 token、output/ 里生成含客户信息的文件。这些都会进公开的 run log 和 artifact。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple

# 用 Python 原生正则重写 bash 版的 POSIX ERE。差异点：
#   [[:space:]] → \s ；字符类里的 \" 直接写 "
# 语义与 bash 版逐条对应，差分测试要求两者命中集合一致。
CONTENT_PATTERNS = re.compile(
    r"ghp_[A-Za-z0-9]{36}"
    r"|gho_[A-Za-z0-9]{36}"
    r"|ghu_[A-Za-z0-9]{36}"
    r"|ghs_[A-Za-z0-9]{36}"
    r"|ghr_[A-Za-z0-9]{36}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|[Bb]earer [A-Za-z0-9._~+/-]{20,}"
    r"|(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key"
    r"|private[_-]?key|client[_-]?secret)\s*[:=]\s*[\"']?[^\s\"']{6,}"
)

# 匹配的是相对路径，统一用 / 分隔（Windows 上要先转换）
FILE_PATTERNS = re.compile(
    r"(?:^|/)\.env(?:$|\.)"
    r"|(?:^|/)id_[dr]sa"
    r"|\.pem$"
    r"|\.p12$"
    r"|\.pfx$"
    r"|\.key$"
    r"|(?:^|/)credentials"
    r"|token"
    r"|secret"
    r"|keystore"
)

# 单文件读取上限：超过就只扫前这么多字节，避免误吞超大文件把扫描拖死
MAX_SCAN_BYTES = 8 * 1024 * 1024
# 探测二进制用的前导块
BINARY_PROBE = 8192


def _is_probably_binary(head: bytes) -> bool:
    """等价 grep -I：含 NUL 就当二进制跳过。"""
    return b"\x00" in head


def scan_secrets(directory: Path) -> Tuple[List[str], List[str]]:
    """扫描目录。返回 (内容命中列表, 文件名命中列表)，两者都为空表示干净。

    列表元素已经是可直接展示的字符串，**不含任何命中的原文**。
    排序是为了输出确定性 —— 文件系统遍历顺序不可靠，差分测试需要稳定顺序。
    """
    directory = Path(directory)
    if not directory.is_dir():
        return [], []

    content_hits: List[str] = []
    name_hits: List[str] = []

    files = sorted(p for p in directory.rglob("*") if p.is_file())

    for f in files:
        try:
            rel = f.relative_to(directory).as_posix()
        except ValueError:
            rel = f.name

        # 文件名命中
        if FILE_PATTERNS.search(rel):
            name_hits.append(f"{f} (文件名)")

        # 内容命中
        try:
            with open(f, "rb") as fh:
                head = fh.read(BINARY_PROBE)
                if _is_probably_binary(head):
                    continue
                fh.seek(0)
                raw = fh.read(MAX_SCAN_BYTES)
        except OSError:
            continue

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # 非 UTF-8：退化成 latin-1 逐字节扫，宁可多报不可漏报
            text = raw.decode("latin-1")

        for lineno, line in enumerate(text.splitlines(), 1):
            if CONTENT_PATTERNS.search(line):
                content_hits.append(f"{f}:{lineno}")

    return sorted(content_hits), sorted(name_hits)


def format_hits(content_hits: List[str], name_hits: List[str]) -> str:
    """渲染命中详情。只有 文件:行号，没有值。"""
    out = []
    for h in content_hits:
        out.append(f"    内容命中: {h}")
    for h in name_hits:
        out.append(f"    文件命中: {h}")
    return "\n".join(out)


BLOCK_MESSAGE = """
扫描命中。仓库 {repo} 是【公开】的 —— 任务脚本会进 git 历史并公开可见，
run 日志与 artifact 任何人都能下载。已【中止提交】，没有任何内容被打包或推送。

三个选择（由你决定，gha 不会自行切换可见性）：
  (a) 改用私有仓库：./gha init --repo <owner>/<name> --visibility private --create --yes
      代价：ubuntu-latest 降到 2 vCPU / 8 GB，且消耗分钟数配额
  (b) 先脱敏，再用公开仓库重跑 submit
  (c) 确认可以公开：加 --allow-public 重跑（责任自负）

注意：扫描是启发式的，只覆盖任务目录里的静态文本，覆盖不到任务【运行期】才产生的
      敏感输出（例如脚本去拉私有数据、日志里打印 token）。这类任务请主动选私仓。"""
