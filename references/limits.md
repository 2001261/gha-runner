# GitHub Actions 硬限制与配额速查

本表数字来自 GitHub 官方文档，**标注「实测」的项已由本 skill 在真实 run 上验证过**（仓库
`2001261/agent-runner`，2026-09-18）。做任务拆分和判定前先查这张表。

## 1. 时间与并发

| 项 | 限制 | 来源 |
|---|---|---|
| 单 job 执行时间 | **6 小时**（到点强制终止） | [Actions limits](https://docs.github.com/en/actions/reference/limits) |
| 单个 workflow run 生命周期 | **35 天**（含排队与人工审批等待） | 同上 |
| 人工审批门禁最长挂起 | 30 天 | 同上 |
| 并发 job 数（标准 runner） | Free **20** / Pro **40** / Team **60** / Enterprise **500** | 同上 |
| 并发 macOS job | Free/Pro/Team **5**，Enterprise **50** | 同上 |
| matrix 单次 run 的 job 数上限 | **256** | 同上 |
| workflow 触发速率 | 每仓库 **1,500 事件 / 10 秒** | 同上 |
| workflow 文件大小 | 500 KB | 同上 |
| 工作流内 `GITHUB_TOKEN` 速率 | 每仓库 1,000 请求/小时 | [REST rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api) |

**推论**：超过 6 小时的任务**必须**拆成多个 job（matrix 或 `needs:` 串联，每个 job 各有
独立的 6 小时预算）或多个 run，没有别的办法。拆分后总吞吐受并发数限制（Free 20），不是无限的。

### 超时的实测行为（2026-09-18，`--timeout 1` + `sleep 300`）

- 超时走的是**取消机制**：run/job 的 `conclusion` 是 **`cancelled`**，不存在 `timed_out` 这个值。
- **job 级 `timeout-minutes` 触发后，`if: always()` 的步骤照常执行** —— 官方文档没有明说这条，
  实测确认：manifest 与 artifact（含任务已产出的部分日志）都完整带回来了。
- 被强杀的任务留不下退出码：`exit_code=-1` 且 `exit_code_recorded=false`。
- 因此本地诊断用三个信号组合判定"撞了时间上限"：`conclusion==cancelled` +
  `exit_code_recorded==false` + 时长 ≥ 上限−90s 误差。实测 71s（上限 60s）被正确识别。

## 2. Runner 硬件（关键：公开仓库和私有仓库不一样）

`ubuntu-latest`（Linux x64）：

| | 公开仓库 | 私有仓库 |
|---|---|---|
| vCPU | **4**（实测 = 4） | 2 |
| 内存 | **16 GB**（实测 `MemTotal` = 15993 MB） | 8 GB |
| SSD | 14 GB（实测 `df` 可用 ≈ 86 GB，因为挂的是宿主大盘） | 14 GB |

- macOS（两者相同）：3 核 M 系列 / 7 GB（实测 macos-latest：3 核 / 7168 MB / Python 3.14）
- Windows（两者相同）：4 核 / 16 GB（实测 windows-latest：4 核 / 16378 MB / Windows Server 2025 / Python 3.12，**只有 `python` 没有 `python3`**）
- `ubuntu-slim`：1 CPU / 5 GB，**job 超时只有 15 分钟**
- **Larger runner**：2→96 vCPU、8 GB→384 GB、75 GB→2040 GB，仅 Team / Enterprise Cloud
  组织可用，且**即使跑在公开仓库上也照样计费**
- GPU runner：1× Tesla T4 / 16 GB VRAM，最多并发 100

来源：[GitHub-hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)、
[Larger runners](https://docs.github.com/en/actions/reference/runners/larger-runners)

> **这条是「原则上用公开仓库」的主要依据**：同样的 `ubuntu-latest`，公开仓库给的算力是
> 私有仓库的两倍，而成本是零。

## 3. 计费

| | 公开仓库 | 私有仓库 |
|---|---|---|
| 标准 runner 分钟数 | **免费且不限量** | Free 计划含 **2,000 分钟/月**，Pro/Team 3,000，Enterprise Cloud 50,000 |
| Artifact / 缓存存储 | Free 500 MB，Pro 1 GB，Team 2 GB，Enterprise 50 GB（与 Packages 共享配额） | 同左 |
| Larger runner | **照样计费** | 照样计费 |

Windows / macOS 分钟数有倍率（不是 1:1 扣），Linux 是 1:1。
来源：[About billing for GitHub Actions](https://docs.github.com/en/billing/managing-billing-for-github-actions/about-billing-for-github-actions)

## 4. `workflow_dispatch` 的硬约束（本 skill 的架构就是绕着这几条设计的）

| 约束 | 值 | 影响 |
|---|---|---|
| 工作流必须存在于**默认分支** | 是 | 所以 `templates/agent-dispatch.yml` 常驻默认分支；不能"每个任务一个分支上的独立 workflow" |
| 可否 dispatch 到其它分支 | **可以**，`--ref <branch>` 跑那个分支的版本 | 大 payload 走分支投递就是靠这个（已实测） |
| inputs 数量上限 | **25** | 本 skill 用了 8 个（task_id / payload_b64 / task_path / runner / shell / timeout_minutes / cache_key / cache_paths） |
| inputs 总 payload 上限 | **65,535 字符** | 所以内联 base64 的阈值取 60,000，留余量 |
| 支持的 input `type` | `string` / `choice` / `boolean` / `number` / `environment` | `choice` 的 options **不能有空字符串**（本 skill 用 `auto` 代替空值） |

来源：[Events that trigger workflows](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows)

对比：`repository_dispatch` 的 `client_payload` 上限是 **10 个顶层属性** / 同样 65,535 字符。

## 5. 结果传回（为什么必须走 artifact）

| 机制 | 能否 run 结束后取回 | 限制 |
|---|---|---|
| **artifact**（`actions/upload-artifact`） | **能**，`gh run download` | 保留 1–90 天（默认 90）；单 job 最多 500 个；产物**不可变**，同名重传会失败，除非 `overwrite: true` |
| job outputs（`GITHUB_OUTPUT`） | **不能** | 仅 run 内 `needs` 可见；[jobs REST 端点](https://docs.github.com/en/rest/actions/workflow-jobs)的响应体里**没有 outputs 字段** |
| job summary（`GITHUB_STEP_SUMMARY`） | 只能在网页上看 | **单 step 上限 1 MiB**，超出则该 step 的 summary 上传失败（不影响 job 成败）；每 job 最多显示 20 个 summary |
| run log | 能，`gh run view --log` | 含大量 Actions 自身噪音，不如 artifact 里的干净副本 |

**v4 的重要变化**：不支持多个 job 往同一个 artifact 追加/合并（v3 可以）。所以本 skill
只在**一个 job 的最后一步**上传一次。

**artifact vs cache**：cache 是给依赖加速用的，7 天不访问就被回收、单仓库默认 10 GB、
按 key 不可变；要跨 run 传结果只能用 artifact。

来源：[actions/upload-artifact](https://github.com/actions/upload-artifact)、
[Caching](https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflow)

> 单个 artifact 的体积上限**官方没有明确记载**，实际受存储配额约束。本 skill 在 bundle
> 超过 100 MB 时给本地预警。

## 6. `run:` 步骤的 shell 陷阱（实测踩到的）

声明 `shell: bash` 后，GitHub 实际用
`/usr/bin/bash --noprofile --norc -e -o pipefail {0}` 调用脚本 —— **`-e` 是默认开着的**。

后果：任务非零退出时，脚本会在 `code=$?` 那一行之前就被 `-e` 打断，退出码根本来不及记录。
本 skill 的 `Run task` 步骤显式 `set +e`，先记录退出码和日志，最后再 `exit "$code"`
让 run 的 conclusion 如实反映结果；`Build manifest` / `Job summary` / `upload-artifact`
三步挂 `if: always()` 保证失败任务的结果也能带回来。

**实测证据**：修复前失败任务的 `exit_code` 是 `-1`（记录不到），修复后是真实的 `3`，
且 `partial.txt` 这类"失败前已产出的部分结果"完整保留。

## 7. 跨 run 状态缓存（actions/cache，实测 2026-09-18）

- 本 skill 的缓存设计：**存唯一键 `<cache_key>-<run_id>`，按前缀 `<cache_key>-` 恢复最近一次**。
  原因：`actions/cache/save` 撞上已存在的 key 直接报 "Cache already exists" 失败，key 不可变。
- **前缀命中时 restore 的 `cache-hit` 输出是 `false`**（它只对精确 key 为真），真正的命中信号是
  `cache-matched-key` 非空。manifest 的 `state.cache_hit` 已经按后者判定。
- **失败任务（exit 3）的 `Save state` 照样执行**（挂 `if: always()`），实测：失败 run 推进的
  计数器被后续 run 完整读到。这意味着中断/失败的任务可以从状态续跑。
- 每个 run 产生一个新 cache entry（实测 4 次 run = 4 个 entry）；entry **7 天未访问过期**，
  仓库缓存总量上限 **10 GB**，超出按 LRU 淘汰。状态目录大 + 跑得频繁会触发 churn，CLI 在
  `state_dir_bytes` 超 10 GB 的 1/4 时预警。
- 不给 `--cache-key` 时完全不产生 cache entry（实测 caches 列表里只有显式 key 的条目）。

## 8. Windows runner 实测踩到的坑（windows-latest，Windows Server 2025）

1. **GNU tar 把盘符当远程主机**：`tar xzf "D:\a\_temp\x.tgz"` 报
   `Cannot connect to D: resolve failed`（`host:path` 语法）。解法：`cd` 进去用相对路径解包。
2. **只有 `python` 没有 `python3`**：所有解释器探测必须两个都试。
3. **stdout 是 cp1252**：Python `print` 中文直接 `UnicodeEncodeError`。解法：
   `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`（gha_runner 与 dispatcher 内嵌
   python 都已加）；**任务脚本侧**由模板在 `Run task` 里统一 `export PYTHONIOENCODING=utf-8`
   兜底（实测：修复前 task.py 在 Windows 上 print 中文 exit=1，修复后正常）。
4. `$HOME` = `C:\Users\runneradmin`，`nproc`/`sysctl`/`/proc/meminfo` 都没有 ——
   CPU 数用 `os.cpu_count()` 兜底，内存用 PowerShell CIM 兜底。
5. Git Bash 下 `tail -F` / `wc -c` / `find` / `sed` 均可用（模板的 `command -v` 守卫实测没触发）。

## 9. 安全约束

| 场景 | 规则 |
|---|---|
| fork 发来的 `pull_request` | 除 `GITHUB_TOKEN` 外**所有 secrets 都不传**；`GITHUB_TOKEN` 是**只读** |
| `pull_request_target` | 在 base 分支上下文运行，`GITHUB_TOKEN` 有**读写**权限，**secrets 可用** —— 这正是它被反复警告的风险点 |
| `permissions:` 未声明 | 取仓库/组织/企业级"默认工作流权限"设置；新建个人仓库的默认值是 `contents` 与 `packages` 只读。**一旦声明了任意一项，未声明的全部变成 `none`** |
| 脚本注入 | 绝不要把 `${{ inputs.x }}` 直接插值进 `run:` 块 —— input 内容会被当成 shell 代码执行。正确做法是先映射到 `env:`，脚本里用 `"$VAR"` 引用。本 skill 的 dispatcher 全部走 `env:` |

来源：[Automatic token authentication](https://docs.github.com/en/actions/security-guides/automatic-token-authentication)、
[Workflow syntax](https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions)

## 10. gh CLI 侧

| 项 | 说明 |
|---|---|
| `gh workflow run` 打印 run URL | 自 **gh 2.87** 起（对应 API 改为返回 200 + `workflow_run_id` / `run_url`） |
| `gh run watch` | **不支持 fine-grained PAT**（需要 `checks:read`，而 fine-grained PAT 授予不了）。本 skill 因此不用 `gh run watch`，改成自己轮询 `gh run view --json` |
| `gh run download -n X -D DIR` | 指定单个 artifact 时，内容**直接**解到 `DIR` 下（不再套一层 artifact 名目录） |
| `gh api POST .../dispatches` | **实测**返回体里含 `workflow_run_id`，所以 run_id 解析第一层就命中，不需要靠列表反查 |
| `.conclusion` 字段 | run 进行中时是**空字符串 `""`**，不是 `null` —— jq 的 `//` 只兜 `null`，所以判空要两种都判 |

## 11. 官方学习仓库（参考资料里给的那批）

`github.com/orgs/skills/repositories?q=GitHub+Actions` 下的 8 个：

| 仓库 | 内容 |
|---|---|
| `skills/hello-github-actions` | 创建并运行第一个 workflow |
| `skills/workflow-artifacts` | artifact 的上传/预览/下载/复用 —— 与本 skill 的结果回传最相关 |
| `skills/reusable-workflows` | 可复用 workflow 与跨仓库调用 |
| `skills/write-javascript-actions` | 自己写 JavaScript Action |
| `skills/create-ai-powered-actions` | 用 GitHub Models 做智能 Action |
| `skills/ai-in-actions` | 在 workflow 里直接调 AI 模型 |
| `skills/publish-docker-images` | 构建并发布 Docker 镜像 |
| `skills/deploy-to-azure` | 部署到 Azure |

同组织下另有 `skills/test-with-actions`（CI 测试工作流）可参考。
