"""argparse 子命令树与统一错误处理。

子命令与 flag 与 bash 版逐字一致（对外契约）；新增 --cache-key / --cache-paths。
所有可预期失败都是 GhaError 子类，在这里统一转成退出码 —— 不打印 traceback。
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__, commands, core, setupenv, ui
from .ui import EXIT_FAIL, EXIT_OK, GhaError

PROG = "gha"

EPILOG = """\
授权边界（重要）:
  A 只读检测      — gha 自主执行（doctor / status / list / logs / wait / fetch）
  B 写操作        — 装 gh、建仓、git push、dispatch、删分支；需 --yes。
                    不带 --yes 时只打印将执行的命令并退出 125，绝不执行。
                    agent 每次都要先取得用户同意，一次同意只覆盖那一次那一个动作。
  C 必须真人操作  — gh auth login / auth refresh / SSO / 网页开关；
                    gha 只打印命令并停下等待，用户完成后必须重新跑 doctor 验证。

退出码:
  0 成功   1 失败   2 wait 超时但 run 仍在跑（不是失败）
  3 有 C 类待用户完成   4 凭据扫描命中已中止   125 缺 --yes，B 类动作未执行

详见 SKILL.md 与 references/setup.md。
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=PROG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="gha — 把高强度任务卸载到 GitHub Actions 并取回结果",
        epilog=EPILOG,
    )
    p.add_argument("--version", action="version", version=f"gha-runner {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<子命令>")

    # --- setup / doctor
    s = sub.add_parser("setup", help="环境自举：检测并按需安装 gh、引导认证、补 git 身份",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("--check", action="store_true", help="只检测，不执行任何 B 类动作（= doctor）")
    s.add_argument("--yes", action="store_true", help="允许执行 B 类安装（下载 gh 到 <skill>/bin/）")
    s.set_defaults(func=_do_setup)

    d = sub.add_parser("doctor", help="= setup --check，只读检测")
    d.set_defaults(func=_do_doctor)

    # --- init
    i = sub.add_parser("init", help="建/接仓库，推 dispatcher 到默认分支并冒烟",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    i.add_argument("--create", action="store_true", help="仓库不存在时创建（B 类）")
    i.add_argument("--repo", help="OWNER/NAME；不给则用 config 里的，再不给用 <login>/agent-runner")
    i.add_argument("--visibility", choices=("public", "private"),
                   help="默认 public（4 vCPU/16GB、分钟数免费不限量）；private 仅在你显式指定时用")
    i.add_argument("--skip-smoke", action="store_true", help="跳过冒烟验证")
    i.add_argument("--yes", action="store_true", help="允许 B 类写操作（建仓、推 workflow、冒烟 dispatch）")
    i.set_defaults(func=commands.cmd_init)

    # --- submit
    sb = sub.add_parser("submit", help="打包任务并提交执行（默认立即返回，不阻塞）",
                        formatter_class=argparse.RawDescriptionHelpFormatter)
    sb.add_argument("task_dir", help="任务目录，内含 task.sh / task.py / task.js")
    sb.add_argument("--task-id", help="默认从目录名派生；只允许 [A-Za-z0-9._-]")
    sb.add_argument("--runner", default=core.DEFAULT_RUNNER,
                    help=f"默认 {core.DEFAULT_RUNNER}；可选 {'/'.join(core.VALID_RUNNERS)}")
    sb.add_argument("--shell", default="auto",
                    help="auto(默认，按入口文件名推断) | bash | python3 | node")
    sb.add_argument("--timeout", default=str(core.DEFAULT_TIMEOUT_MINUTES),
                    help="【云端 job】超时分钟数，默认 360，硬上限 360（6 小时）")
    sb.add_argument("--out", help=f"结果目录，默认 <cwd>/.gha-runs/<task_id>")
    sb.add_argument("--wait", action="store_true", help="提交后阻塞等待（内部已含 fetch）")
    sb.add_argument("--wait-timeout", type=int, default=core.DEFAULT_WAIT_TIMEOUT,
                    help="配合 --wait，最长等待【秒】数，默认 600；超时返回 2，不算失败")
    sb.add_argument("--interval", type=int, default=core.DEFAULT_WAIT_INTERVAL,
                    help="配合 --wait，轮询间隔秒数，默认 15，最小 3")
    sb.add_argument("--keep-ref", action="store_true", help="分支投递时取回后保留远端分支")
    sb.add_argument("--allow-public", action="store_true",
                    help="跳过凭据/隐私扫描（责任自负，需用户明确要求）")
    sb.add_argument("--cache-key", help="启用跨次环境复用；不给则完全关闭缓存")
    sb.add_argument("--cache-paths", help="额外要缓存的路径，逗号或换行分隔")
    sb.add_argument("--yes", action="store_true", help="允许 B 类写操作（push、dispatch）")
    sb.set_defaults(func=commands.cmd_submit)

    # --- status
    st = sub.add_parser("status", help="一次性查询 run 状态，输出 JSON，不阻塞")
    st.add_argument("target", help="task_id 或 run_id")
    st.add_argument("--out")
    st.set_defaults(func=commands.cmd_status)

    # --- wait
    w = sub.add_parser("wait", help="阻塞等待到终态或超时（超时返回 2，不算失败）")
    w.add_argument("target", help="task_id 或 run_id")
    w.add_argument("--timeout", type=int, default=core.DEFAULT_WAIT_TIMEOUT,
                   help="最长等待【秒】数，默认 600")
    w.add_argument("--interval", type=int, default=core.DEFAULT_WAIT_INTERVAL,
                   help="轮询间隔秒数，默认 15，最小 3")
    w.add_argument("--out")
    w.set_defaults(func=commands.cmd_wait)

    # --- fetch
    f = sub.add_parser("fetch", help="下载 artifact 到本地，打印 manifest.json 路径")
    f.add_argument("target", help="task_id 或 run_id")
    f.add_argument("--out")
    f.add_argument("--force", action="store_true", help="忽略幂等缓存，强制重下")
    f.add_argument("--yes", action="store_true", help="允许清理分支投递留下的远端分支（B 类）")
    f.set_defaults(func=commands.cmd_fetch)

    # --- logs
    lg = sub.add_parser("logs", help="取 run 日志")
    lg.add_argument("target", help="task_id 或 run_id")
    lg.add_argument("--failed", action="store_true", help="只看失败步骤的日志")
    lg.add_argument("--out")
    lg.set_defaults(func=commands.cmd_logs)

    # --- list
    ls = sub.add_parser("list", help="列本仓库的 run")
    ls.add_argument("--limit", type=int, default=20)
    ls.set_defaults(func=commands.cmd_list)

    return p


def _do_setup(args) -> int:
    return setupenv.cmd_setup(check_only=bool(args.check), yes=bool(args.yes))


def _do_doctor(args) -> int:
    return setupenv.cmd_setup(check_only=True, yes=False)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK

    # argparse 把 --task-id 存成 task_id，commands 里按这个约定取
    try:
        return int(args.func(args) or EXIT_OK)
    except GhaError as e:
        ui.err(e.message)
        return e.code
    except KeyboardInterrupt:
        ui.warn("被用户中断")
        return 130
    except BrokenPipeError:
        # 被 head / grep 截断输出，不是错误
        return EXIT_OK


if __name__ == "__main__":      # pragma: no cover
    sys.exit(main())
