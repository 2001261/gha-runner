"""子命令实现：init / submit / status / wait / fetch / logs / list。

退出码是对外契约：0 成功 / 1 失败 / 2 wait 超时仍在跑 / 3 有 C 类待用户完成 /
4 凭据扫描命中已中止 / 125 缺 --yes。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import bundle, core, remote, secrets, setupenv, state, ui
from .ui import (EXIT_FAIL, EXIT_NOT_AUTHORIZED, EXIT_OK, EXIT_SECRET_FOUND,
                 EXIT_STILL_RUNNING, GhaError, NeedsUser, NotAuthorized)


# ------------------------------------------------------------------ 前置门禁

def require_auth() -> None:
    core.require_gh()
    logged_in, _, _ = setupenv.auth_status()
    if not logged_in:
        ui.log(setupenv.AUTH_INSTRUCTIONS)
        raise NeedsUser("gh 未认证（C 类：必须你亲手完成，完成后重跑 ./gha doctor）")


def require_dispatcher(repo: str) -> None:
    if not remote.workflow_present(repo):
        raise GhaError(
            f"仓库 {repo} 里没有 {core.WORKFLOW_NAME} —— "
            f"先跑：./gha init --repo {repo} --yes"
        )


# ------------------------------------------------------------------ init

def cmd_init(args) -> int:
    core.require_gh()
    require_auth()

    # 先跑一遍只读自检；有 C 类未就绪就停下等用户，不留半初始化状态
    ui.section("初始化前自检")
    gate = setupenv.cmd_setup(check_only=True)
    if gate == EXIT_FAIL:
        raise GhaError("环境自检有失败项，先修复再 init")
    if gate != EXIT_OK:
        # needs-user：gh 未装也算在这里，明确告诉用户下一步
        ui.warn("自检有 [install] / [needs-user] 项。init 需要 gh 已装好并已认证。")
        if not core.resolve_gh():
            raise GhaError("gh CLI 不可用 —— 先跑：./gha setup --yes（B 类安装，需用户同意）")

    repo = args.repo or core.cfg_repo()
    if not repo:
        data = core.gh_json(["api", "user"])
        login = (data or {}).get("login") if isinstance(data, dict) else None
        if not login:
            raise GhaError("无法获取 GitHub 账号名，请用 --repo OWNER/NAME 指定")
        repo = f"{login}/agent-runner"
        ui.note(f"未指定 --repo，默认使用：{repo}")
    if not core.valid_repo_slug(repo):
        raise GhaError(f"仓库名格式非法（应为 owner/name）：{repo}")

    vis = (args.visibility or core.cfg_visibility() or "public").lower()
    if vis == "public":
        ui.info("可见性 = public（默认策略）")
        ui.note("收益：ubuntu-latest 给到 4 vCPU / 16 GB；标准 runner 分钟数免费且不限量")
        ui.warn("代价：投递的任务脚本会进 git 历史并公开可见，artifact 任何人可下载")
        ui.note("因此 submit 带凭据/隐私前置扫描，命中即中止并向你求助")
    elif vis == "private":
        ui.info("可见性 = private（你显式指定的）")
        ui.warn("私有仓库 ubuntu-latest 只有 2 vCPU / 8 GB，且消耗分钟数配额（Free 2,000 分钟/月）")
    else:
        raise GhaError(f"--visibility 只能是 public 或 private，收到：{vis}")

    authorized = bool(args.yes)

    # --- 仓库是否存在
    actual = remote.repo_visibility(repo)
    if actual:
        ui.st_ok(f"仓库已存在：{repo}（可见性 {actual.upper()}）")
        if actual != vis:
            ui.warn(f"你要求 {vis}，但仓库实际是 {actual} —— 以实际为准，不擅自改可见性")
            vis = actual
    else:
        if not args.create:
            raise GhaError(f"仓库不存在：{repo} —— 要创建请加 --create（B 类操作，需用户同意）")
        core.confirm_b(
            f"创建 {vis} 仓库 {repo}",
            lambda: remote.create_repo(
                repo, vis,
                "gha-runner scratch repo: offloads high-intensity agent tasks to GitHub Actions"),
            authorized=authorized,
            show=f"gh repo create {repo} --{vis}",
        )
        ui.st_ok(f"仓库已创建：https://github.com/{repo}")

    # --- 推 dispatcher
    br = remote.default_branch(repo)
    if br:
        ui.st_ok(f"默认分支：{br}")
    else:
        ui.note("仓库为空，尚无默认分支")

    if remote.dispatcher_up_to_date(repo):
        ui.st_ok("dispatcher 已是最新（与本地模板 git blob sha 一致），跳过推送")
    else:
        if remote.workflow_present(repo):
            ui.st_install("dispatcher 已存在但与本地模板不一致 —— 需要更新")
        core.confirm_b(
            f"推送 templates/agent-dispatch.yml 到 {repo} 的 .github/workflows/",
            lambda: remote.push_dispatcher(repo, br),
            authorized=authorized,
            show=f"gh api -X PUT repos/{repo}/contents/.github/workflows/{core.WORKFLOW_NAME}",
        )
        for _ in range(6):
            if remote.workflow_present(repo):
                break
            time.sleep(3)
        if remote.workflow_present(repo):
            ui.st_ok(f"dispatcher 已就绪：{core.WORKFLOW_NAME}")
        else:
            ui.warn("dispatcher 已推送但 GitHub 还没注册出来，稍后用 ./gha doctor 复查")

    core.cfg_set("repo", repo)
    core.cfg_set("visibility", vis)
    ui.st_ok(f"配置已写入 config/repo={repo}, config/visibility={vis}")

    # --- 冒烟：同时满足"workflow 必须先在默认分支跑过一次"的前置条件
    if args.skip_smoke:
        ui.st_skip("按要求跳过冒烟（注意：首次 dispatch 到非默认分支前，"
                   "必须先在默认分支跑过一次）")
        return EXIT_OK

    ui.log("")
    ui.info("冒烟验证：投递 tasks/smoke 并等待结果")
    ui.note("这一步会真实触发一次 Actions run（公开仓库分钟数免费）")
    if not authorized:
        ui.st_install("冒烟未执行（缺 --yes）")
        ui.note("命令: ./gha submit tasks/smoke --task-id smoke --wait --yes")
        return EXIT_NOT_AUTHORIZED

    # 注意：--timeout 是【云端 job】的超时分钟数，等待时长是 wait_timeout（秒）。
    # bash 版这里把两者搞混过，冒烟的 job 超时被设成了 300 分钟。
    smoke = core.GHA_HOME / "tasks" / "smoke"
    if not smoke.is_dir():
        ui.st_skip(f"找不到示例任务 {smoke}，跳过冒烟")
        return EXIT_OK

    class _SmokeArgs:
        pass

    sa = _SmokeArgs()
    sa.task_dir = str(smoke)
    sa.task_id = "smoke"
    sa.runner = core.DEFAULT_RUNNER
    sa.shell = "auto"
    sa.timeout = str(core.DEFAULT_TIMEOUT_MINUTES)
    sa.out = None
    sa.wait = True
    sa.wait_timeout = 300
    sa.interval = core.DEFAULT_WAIT_INTERVAL
    sa.keep_ref = False
    sa.allow_public = False
    sa.cache_key = ""
    sa.cache_paths = ""
    sa.yes = True
    sa.force = False
    rc = cmd_submit(sa)
    return rc


# ------------------------------------------------------------------ submit

def cmd_submit(args) -> int:
    task_dir = Path(args.task_dir).expanduser()
    if not task_dir.is_dir():
        raise GhaError(f"任务目录不存在：{task_dir}")
    task_dir = task_dir.resolve()

    core.require_gh()
    require_auth()
    repo = core.need_repo()
    require_dispatcher(repo)

    # --- 参数校验
    task_id = args.task_id or core.derive_task_id(task_dir)
    if not core.valid_task_id(task_id):
        raise GhaError(f"task_id 非法（只允许 [A-Za-z0-9._-]，≤64 字符）：{task_id}")
    runner = args.runner or core.DEFAULT_RUNNER
    if not core.valid_runner(runner):
        raise GhaError(f"runner 非法：{runner}（可选 {'/'.join(core.VALID_RUNNERS)}）")
    shell_kind = args.shell or "auto"
    if not core.valid_shell(shell_kind):
        raise GhaError(f"shell 非法：{shell_kind}（可选 {'/'.join(core.VALID_SHELLS)}）")
    timeout_min = str(args.timeout or core.DEFAULT_TIMEOUT_MINUTES)
    if not core.valid_timeout_minutes(timeout_min):
        raise GhaError(
            f"timeout 必须是 1..{core.MAX_TIMEOUT_MINUTES} 的整数分钟"
            f"（GitHub 单 job 硬上限 6 小时）：{timeout_min}"
        )
    timeout_min_i = int(timeout_min)

    cache_key = (args.cache_key or "").strip()
    if cache_key and not state.validate_cache_key(cache_key):
        raise GhaError(f"cache-key 非法（只允许 [A-Za-z0-9._-]，≤100 字符）：{cache_key}")
    cache_paths = state.normalize_cache_paths(args.cache_paths or "")

    # --- 入口检查（显式指定 --shell 时不匹配就报错，绝不静默回落到别的入口）
    if shell_kind == "auto":
        entry = bundle.find_entry(task_dir)
        if not entry:
            raise GhaError(f"任务目录里没有 task.sh / task.py / task.js：{task_dir}")
    else:
        entry = bundle.find_entry(task_dir, shell_kind)
        if not entry:
            raise GhaError(
                f"指定了 --shell {shell_kind} 但找不到对应入口文件"
                f"（应为 {bundle.ENTRY_BY_SHELL[shell_kind]}）：{task_dir}"
            )
        shell_kind = bundle.shell_of_entry(entry)

    rdir = core.run_dir(task_id, args.out)

    ui.section(f"提交任务 {task_id} → {repo}")
    ui.note(f"任务目录: {task_dir}")
    ui.note(f"入口:     {entry}（{shell_kind}）")
    ui.note(f"runner:   {runner}   超时: {timeout_min_i} 分钟")
    if cache_key:
        ui.note(f"状态复用: cache_key={cache_key}"
                + (f"，额外路径 {len(cache_paths.splitlines())} 个" if cache_paths else ""))
    ui.note(f"结果目录: {rdir}")

    # --- 凭据/隐私扫描：必须在【打包之前】，更要在任何网络写操作之前。
    #     bash 版把它放在打包之后，结果拦截时磁盘上已经留下了一份含密钥的 bundle.tgz。
    vis = remote.repo_visibility(repo) or core.cfg_visibility()
    if vis == "public" and not args.allow_public:
        ui.info("凭据/隐私扫描（仓库是 public）")
        content_hits, name_hits = secrets.scan_secrets(task_dir)
        if content_hits or name_hits:
            ui.log(secrets.format_hits(content_hits, name_hits))
            ui.log("")
            ui.err(f"扫描命中。仓库 {repo} 是【公开】的 —— 任务脚本会进 git 历史并公开可见，")
            ui.err("run 日志与 artifact 任何人都能下载。已【中止提交】，没有任何内容被打包或推送。")
            ui.log(secrets.BLOCK_MESSAGE.format(repo=repo))
            raise ui.SecretFound("凭据/隐私扫描命中，已中止提交")
        ui.st_ok("未命中凭据特征")
    elif args.allow_public:
        ui.warn("已用 --allow-public 跳过凭据扫描 —— 责任自负")
    else:
        ui.note("仓库是 private，跳过公开暴露扫描")

    # --- 打包（扫描通过后才落盘）
    rdir.mkdir(parents=True, exist_ok=True)
    bundle_path = rdir / "bundle.tgz"
    b64_path = rdir / "bundle.b64"
    bundle.pack_bundle(task_dir, bundle_path)
    b64_text, bsize = bundle.make_b64(bundle_path, b64_path)
    b64len = len(b64_text)
    ui.note(f"bundle: {core.human_bytes(bsize)} → base64 {b64len} 字符")
    if bsize > core.BUNDLE_WARN_BYTES:
        ui.warn(f"bundle 超过 {core.human_bytes(core.BUNDLE_WARN_BYTES)} —— "
                f"artifact 存储配额有限（Free 500 MB），上传可能失败")

    # --- 选投递路径
    transport = bundle.choose_transport(b64len)
    ref = ""
    task_path = ""
    if transport == "branch":
        ref = f"agent/{task_id}"
        task_path = f"tasks/{task_id}"
        ui.warn(f"base64 {b64len} 字符超过内联上限 {core.INLINE_B64_LIMIT}"
                f"（dispatch 总 payload 硬上限 65535）")
        ui.info(f"改走分支投递：把原始任务文件推到 {repo} 的 {ref}，再 dispatch --ref {ref}")
        ui.note("原始文件（不是 base64 blob）入库，保留可审计性")

    authorized = bool(args.yes)

    # --- 分支投递：先推文件（B 类）
    if transport == "branch":
        ident = remote.git_identity()
        ui.note("git commit 身份（仅本次命令局部使用，不改全局配置）：")
        ui.note(f"  user.name  = {ident[0]}")
        ui.note(f"  user.email = {ident[1]}")
        core.confirm_b(
            f"推送任务文件到分支 {ref}",
            lambda: remote.push_task_branch(repo, ref, task_path, task_dir, ident),
            authorized=authorized,
            show=f"git push -f origin {ref}  # {task_path}/",
        )

    # --- dispatch（B 类）
    body = remote.build_dispatch_body(
        ref=ref or remote.default_branch(repo) or "main",
        task_id=task_id,
        payload_b64=b64_text if transport == "inline" else "",
        task_path=task_path,
        runner=runner,
        shell=shell_kind,
        timeout_minutes=timeout_min_i,
        cache_key=cache_key,
        cache_paths=cache_paths,
    )
    if not authorized:
        ui.st_install("dispatch 未执行（缺 --yes）—— 这是 B 类写操作")
        ui.note(f"命令: ./gha submit \"{task_dir}\" --task-id {task_id} "
                f"--runner {runner} --timeout {timeout_min_i} --yes")
        raise NotAuthorized("需要 --yes 才能触发 workflow_dispatch")

    ui.info(f"触发 workflow_dispatch：{core.WORKFLOW_NAME}")
    _, api_rid = remote.dispatch(repo, body)

    rid = remote.resolve_dispatch_run_id(repo, task_id, api_rid)

    # --- 落 run 状态，agent 之后用 task_id 就能查
    core.run_set(task_id, "task_id", task_id, args.out)      # artifact 名以这个为准，不是目录名
    core.run_set(task_id, "run_id", rid, args.out)
    core.run_set(task_id, "repo", repo, args.out)
    core.run_set(task_id, "task_dir", str(task_dir), args.out)
    core.run_set(task_id, "timeout_minutes", str(timeout_min_i), args.out)
    if ref:
        core.run_set(task_id, "ref", ref, args.out)
    if args.keep_ref:
        core.run_set(task_id, "keep_ref", "1", args.out)
    if cache_key:
        core.run_set(task_id, "cache_key", cache_key, args.out)
    byrun = core.RUNS_DIR / "by-run" / rid
    byrun.mkdir(parents=True, exist_ok=True)
    core.write_text_file(byrun / "task_id", task_id)

    ui.st_ok(f"已提交：run_id={rid}")
    ui.log(f"    https://github.com/{repo}/actions/runs/{rid}")
    ui.note(f"查询: ./gha status {task_id}")
    ui.note(f"等待: ./gha wait {task_id} --timeout 600")
    ui.note(f"取回: ./gha fetch {task_id}")

    if args.wait:
        ui.log("")
        wrc = _wait(rid, repo, args.wait_timeout, args.interval)
        try:
            _fetch(rid, repo, args.out, force=False, authorized=True)
        except GhaError as e:
            ui.warn(f"取回结果失败：{e.message}")
        return wrc
    return EXIT_OK


# ------------------------------------------------------------------ status / wait

def cmd_status(args) -> int:
    core.require_gh()
    require_auth()
    repo = core.need_repo()
    rid = core.resolve_run_id(args.target, args.out)

    data = remote.run_view(repo, rid,
                           "databaseId,status,conclusion,displayTitle,url,createdAt,updatedAt,jobs")
    if not data:
        raise GhaError(f"查询失败：run {rid} @ {repo}")

    out = {
        "run_id": data.get("databaseId"),
        "status": data.get("status"),
        "conclusion": data.get("conclusion") or "none",
        "title": data.get("displayTitle"),
        "url": data.get("url"),
        "started_at": data.get("createdAt"),
        "updated_at": data.get("updatedAt"),
        "jobs": remote.run_jobs(repo, rid),
    }
    ui.log(json.dumps(out, ensure_ascii=False, sort_keys=True))

    # 顺带给一句人话诊断，agent 不用自己解析
    task = core.resolve_task_id(args.target, args.out)
    rdir = core.run_dir(task, args.out)
    manifest = _read_manifest(rdir)
    v = state.diagnose(manifest, {"status": out["status"], "conclusion": out["conclusion"],
                                  "started_at": out["started_at"], "updated_at": out["updated_at"]},
                       _recorded_timeout(rdir))
    if v.kind not in ("success", "running"):
        ui.note(f"诊断: {v.kind} —— {v.summary.splitlines()[0]}")
    return EXIT_OK


def _recorded_timeout(rdir: Path) -> int:
    raw = core.read_text_file(rdir / "timeout_minutes").strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def _read_manifest(rdir: Path) -> Optional[Dict[str, Any]]:
    p = rdir / "result" / "manifest.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _read_run_json(rdir: Path) -> Optional[Dict[str, Any]]:
    p = rdir / "run.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _wait(rid: str, repo: str, timeout: int, interval: int) -> int:
    interval = max(interval, core.MIN_WAIT_INTERVAL)
    deadline = core.now_epoch() + timeout
    started = core.now_epoch()

    ui.info(f"等待 run {rid} 完成（最多 {timeout}s，每 {interval}s 查一次）")
    ui.note(f"https://github.com/{repo}/actions/runs/{rid}")

    st = cc = ""
    polls = 0
    while True:
        polls += 1
        st, cc = remote.run_state(repo, rid)
        if not st:
            ui.warn(f"第 {polls} 次查询失败（网络或 run 尚未可见），继续重试")
        else:
            ui.log(f"  [{core.now_epoch() - started:>4}s] status={st} "
                   f"conclusion={cc or 'none'}")
            if st == "completed":
                ui.log("")
                if cc == "success":
                    ui.st_ok("run 成功")
                    return EXIT_OK
                ui.err(f"run 结束，conclusion={cc or 'none'}")
                return EXIT_FAIL

        if core.now_epoch() >= deadline:
            ui.log("")
            ui.warn(f"已等 {timeout}s，run 仍在进行（status={st or 'unknown'}）—— 这【不是失败】")
            ui.note(f"稍后继续查: ./gha status {rid}")
            ui.note(f"再等一轮:   ./gha wait {rid} --timeout {timeout}")
            ui.log(json.dumps({"still_running": True, "run_id": rid,
                               "status": st or "unknown"}, ensure_ascii=False))
            return EXIT_STILL_RUNNING
        time.sleep(interval)


def cmd_wait(args) -> int:
    core.require_gh()
    require_auth()
    repo = core.need_repo()
    rid = core.resolve_run_id(args.target, args.out)
    return _wait(rid, repo, args.timeout, args.interval)


# ------------------------------------------------------------------ fetch

def cmd_fetch(args) -> int:
    core.require_gh()
    require_auth()
    repo = core.need_repo()
    return _fetch(args.target, repo, args.out, bool(args.force), bool(args.yes))


def _fetch(target: str, repo: str, out: Optional[str], force: bool, authorized: bool) -> int:
    task = core.resolve_task_id(target, out)
    rid = core.resolve_run_id(target, out)
    rdir = core.run_dir(task, out)
    rdir.mkdir(parents=True, exist_ok=True)

    # artifact 名要用【记录的 task_id】，不是本地目录名 ——
    # --out 可以让两者不同，bash 版在这里找过不存在的 result-<目录名>
    recorded = core.read_text_file(rdir / "task_id").strip()
    if recorded:
        task = recorded

    ui.info(f"取回 run {rid} 的结果 → {rdir}")

    st, cc = remote.run_state(repo, rid)
    if st and st != "completed":
        ui.warn(f"run 还没结束（status={st}）—— 现在下载可能拿不到 artifact。"
                f"先跑：./gha wait {target}")

    # 幂等判定必须按 run_id：只看 result/ 存不存在是不够的 ——
    # 同一个 task_id 重复提交时那会是【上一个 run】的旧结果，
    # 而 run.json 已指向新 run，两个文件来自不同 run，agent 会静默消费错数据。
    prev = core.read_text_file(rdir / "fetched_run").strip()
    result_dir = rdir / "result"
    if (result_dir / "manifest.json").is_file() and prev == rid and not force:
        ui.st_ok(f"result/ 已是 run {rid} 的结果，跳过重复下载（要重下加 --force）")
    else:
        if prev and prev != rid and result_dir.exists():
            ui.note(f"检测到 result/ 来自旧 run {prev}，当前是 {rid} —— 重新下载覆盖")
        for junk in (result_dir, rdir / "fetched_run"):
            if junk.is_dir():
                shutil.rmtree(junk, ignore_errors=True)
            elif junk.exists():
                try:
                    junk.unlink()
                except OSError:
                    pass
        remote.download_artifact(repo, rid, f"result-{task}", result_dir)
        core.write_text_file(rdir / "fetched_run", rid)
        ui.st_ok(f"artifact result-{task} 已下载（run {rid}）")

    # run.json：API 侧事实，与 runner 侧的 manifest.json 分开，不改动 artifact 内容
    data = remote.run_view(repo, rid,
                           "databaseId,status,conclusion,displayTitle,url,createdAt,updatedAt")
    if data:
        run_json = {
            "run_id": data.get("databaseId"),
            "status": data.get("status"),
            "conclusion": data.get("conclusion") or "none",
            "title": data.get("displayTitle"),
            "url": data.get("url"),
            "started_at": data.get("createdAt"),
            "updated_at": data.get("updatedAt"),
        }
        core.write_text_file(rdir / "run.json",
                             json.dumps(run_json, ensure_ascii=False, sort_keys=True))
    else:
        run_json = _read_run_json(rdir) or {}
        ui.warn("查询 run 状态失败，run.json 未更新")

    # 分支投递的清理（B 类）
    ref = core.read_text_file(rdir / "ref").strip()
    if ref:
        if core.read_text_file(rdir / "keep_ref").strip():
            ui.note(f"按要求保留远端分支 {ref}")
        elif authorized:
            if remote.delete_branch(repo, ref):
                ui.st_ok(f"已清理远端分支 {ref}")
            else:
                ui.warn(f"清理远端分支 {ref} 失败（可能已不存在）")
        else:
            ui.st_install(f"未清理远端分支 {ref}（缺 --yes）")

    ui.log("")
    manifest = _read_manifest(rdir)
    if manifest is None:
        ui.warn("下载完成但没找到 manifest.json —— result/ 内容：")
        for p in sorted(result_dir.rglob("*"))[:40]:
            ui.log(f"    {p.relative_to(rdir)}")
        return EXIT_FAIL

    ui.st_ok("结果就绪")
    ui.log(f"    manifest（runner 侧事实）: {result_dir / 'manifest.json'}")
    ui.log(f"    run.json（API 侧事实）:    {rdir / 'run.json'}")
    ui.log(f"    产物目录:                  {result_dir / 'output'}")
    ui.log(f"    完整日志:                  {result_dir / 'stdout.log'}, stderr.log")
    ui.log("")

    # 诊断：把"任务失败"和"撞了 6 小时墙"分开说
    v = state.diagnose(manifest, run_json, _recorded_timeout(rdir))
    if v.kind == "success":
        ui.st_ok(v.summary)
    elif v.kind == "timed_out":
        ui.warn("诊断：撞了时间上限（不是任务自己失败）")
        for line in v.summary.splitlines():
            ui.note(line)
    elif v.kind in ("task_failed", "cancelled"):
        ui.err(f"诊断：{v.summary}")
        tail = (manifest.get("stderr_tail") or "").strip()
        if tail:
            ui.note("stderr 末尾：")
            for line in tail.splitlines()[-8:]:
                ui.note(f"  {line}")
    else:
        ui.warn(f"诊断：{v.summary}")

    ui.log("")
    ui.note("状态复用: " + state.describe_state(manifest).split(": ", 1)[-1])
    ui.note("agent 后续用法：Read manifest.json 看 exit_code / outputs / runner_env / state，")
    ui.note("              再 Read output/ 下的具体产物。字段说明见 references/result-contract.md")
    return EXIT_OK


# ------------------------------------------------------------------ logs / list

def cmd_logs(args) -> int:
    core.require_gh()
    require_auth()
    repo = core.need_repo()
    rid = core.resolve_run_id(args.target, args.out)
    if not args.failed:
        ui.note("完整日志可能很长；结果的干净副本在 .gha-runs/<task>/result/stdout.log")
    flag = "--log-failed" if args.failed else "--log"
    res = core.run_gh(["run", "view", rid, flag], repo=repo)
    if res.stdout:
        ui.log(res.stdout.rstrip("\n"))
    if not res.ok:
        raise GhaError(f"取日志失败（rc={res.rc}）：{res.stderr.strip()[:400]}")
    return EXIT_OK


def cmd_list(args) -> int:
    core.require_gh()
    require_auth()
    repo = core.need_repo()
    runs = remote.list_runs(repo, args.limit)
    ui.log(f"{'RUN_ID':<14}{'STATUS':<12}{'CONCLUSION':<12}{'TASK':<22}URL")
    if not runs:
        ui.note(f"（{repo} 里没有 {core.WORKFLOW_NAME} 的 run）")
        return EXIT_OK
    for r in runs:
        ui.log(f"{str(r.get('databaseId')):<14}"
               f"{str(r.get('status') or ''):<12}"
               f"{str(r.get('conclusion') or '-'):<12}"
               f"{str(r.get('displayTitle') or ''):<22}"
               f"{r.get('url') or ''}")
    return EXIT_OK
