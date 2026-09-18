---
name: gha-runner
description: >-
  把任务委派到 GitHub Actions 云端沙盒化执行、取回结构化结果的调度器（批处理，非交互式
  云电脑）。能力：判定任务适合性、空白环境自举 gh、投递执行、异步轮询、artifact 取回
  供后续产物使用、--cache-key 跨 run 状态复用与超时续跑；兼容 Windows/macOS/Linux。
  触发按任务特征判断，满足其一即应考虑使用：预计运行超过约 2 分钟且无需实时盯着；
  可拆成多个独立子任务并行跑批；需要干净/隔离/可复现的环境，或任务会污染本地环境；
  需要 Linux/apt，或要在本机不具备的平台上验证（如本机是 macOS 但要测 Windows/Linux）；
  本地资源不够（内存/磁盘短时峰值超过本机）；任务长跑且需要 checkpoint 断点续跑；
  需要运行证据可回溯（run log + artifact）；需要定时或事件触发；希望不占 agent 回合、
  提交后立即去做别的事。关键词：GitHub Actions、云端跑任务、把任务丢到云上、卸载计算、
  offload、长耗时任务、并行跑批、matrix 并行、CI、跑不动了换云端、干净 Linux 环境、
  跨平台测试、4 核 16G、留运行证据、撞墙续跑、状态复用、云沙盒、gha-runner。
  不要触发的情况（留在本地）：一两分钟内能完成的轻量任务（固定开销约 25 秒不划算）；
  需要交互式 stdin；依赖本地文件系统、localhost 或内网资源；需要 GPU、GUI 或
  超过 16GB 内存；延迟敏感、要秒级反馈的迭代。用户问"这任务该本地跑还是上云"时也使用。
author: 王晶晶律师（四川恒和信律师事务所）
license: Apache-2.0
metadata:
  short-description: 把高强度任务卸载到 GitHub Actions 并取回结果
  category: infrastructure
  author: 王晶晶律师（四川恒和信律师事务所）
  contact: 使用问题请关注微信公众号「隔壁王律师」
---

# gha-runner

> 作者：王晶晶律师（四川恒和信律师事务所） · 使用问题请关注微信公众号「隔壁王律师」

把高强度任务卸载到 GitHub Actions 上跑，结果以 artifact 取回本地，供后续步骤消费。

CLI 入口（Python ≥ 3.9、纯标准库实现，三平台兼容）：

- macOS / Linux：`./gha <子命令>`
- Windows：`gha.cmd <子命令>`（cmd 里）或 `.\gha.cmd <子命令>`（PowerShell 里）

所有路径都可能含空格，**一律双引号包裹**。下文示例写 `./gha`，Windows 上换成 `gha.cmd`。

## 两条核心判断（先读这个）

1. **Actions 的优势是并行吞吐、隔离环境、异步不占回合、结果可回溯 —— 不是单机速度。**
   实测端到端固定开销约 25 秒（内联投递）。所以判定的关键是"这任务值不值得异步化 /
   能不能并行 / 要不要隔离环境"，而不是"云上会不会更快"。

2. **原则上用公开仓库。** 公开仓库的 `ubuntu-latest` 给到 **4 vCPU / 16 GB**（实测
   `cores=4`、`mem_total_mb=15989`），标准 runner 分钟数**免费且不限量**；私有仓库只有
   2 vCPU / 8 GB，还要吃 Free 计划 2,000 分钟/月的配额。
   私仓只在**用户明确指定**、或**任务判定确实需要**时才用 —— 后者由本 skill **提建议、
   用户做选择**，绝不自行切换可见性。

## 授权边界（必须遵守）

| 类 | 定义 | agent 行为 |
|---|---|---|
| **A 只读** | `gha doctor`、`gha status`、`gha list`、`gha logs`、各种 `gh api` GET | 自主执行，不用问 |
| **B 写操作** | 装 gh、建仓、改可见性、`git push`、dispatch、删分支/删 run | **每次**先说明再取得用户同意；一次同意只覆盖那一次那一个动作。CLI 层面用 `--yes` 表达 —— **不带 `--yes` 时这些动作只打印命令不执行**（返回 125） |
| **C 必须真人** | `gh auth login` 浏览器 device flow、`gh auth refresh` 补 scope、组织 SSO、网页开关 Actions | **只能打印准确命令 + 解释浏览器里会发生什么 + 停下等待**。用户说完成后**必须重新跑 `gha doctor` 验证**，不凭口头确认往下走 |

**绝不得**：代用户跑 `gh auth login`（会卡死在交互提示）；读取、回显、记录 token 值；
把 token 写进任何文件或日志；未经用户明确要求就用 `--allow-public` 绕过凭据扫描。

## 工作流

### 第 0 步：环境自举（任何写操作之前必做）

```bash
./gha doctor        # 只读检测，A 类，随时可跑
./gha setup --yes   # 检测 + 执行 B 类安装（需用户同意）
```

`setup` 是幂等状态机，11 项逐个报 `[ok]` / `[install]` / `[needs-user]` / `[fail]`。
退出码 `0` 全绿、`1` 有失败、`3` 有 C 类待用户完成。

不假设机器上已经有 `gh`。缺失时的安装顺序：

1. **首选本地 bin 安装**（`setup --yes` 自动做）：从 `cli/cli` 的 `releases/latest`
   动态解析版本（**不硬编码版本号**），下载对应 OS/arch 的包，只取 `bin/gh` 单文件放进
   `<skill>/bin/gh`。**不写系统目录、不需要 sudo**，macOS 上会清掉 quarantine 属性
2. 备选系统包管理器（B 类，需说明会写系统目录 / 需要 sudo）：`brew install gh`、
   `winget install --id GitHub.cli`、`apt/dnf/pacman install gh`

`GH` 解析优先级：`$GHA_GH_BIN` → **系统 PATH 里的 gh** → `<skill>/bin/gh`。
系统 gh 优先，因为它由包管理器维护更新；`bin/gh` 只是空白环境的兜底缓存。

未认证时打印两条路径让用户选，然后**停下**：

```bash
# 路径 A：交互式（有浏览器，推荐）
gh auth login --hostname github.com --git-protocol https --web
#   → 终端打印一次性 8 位 code，用户在 https://github.com/login/device 输入
#   → 勾选授权 repo 与 workflow 两个 scope

# 路径 B：非交互（无浏览器 / CI）
#   用户在 https://github.com/settings/tokens 建含 repo + workflow 的 classic PAT
gh auth login --hostname github.com --with-token
```

scope 不足时：`gh auth refresh -h github.com -s repo -s workflow`（同为 C 类）。

**git commit 身份**：本机实测 `git config --global user.name/user.email` 常常是空的，
而分支投递需要 commit 身份。处理方式是**绝不改全局配置**，只用
`git -c user.name=… -c user.email=…` 做当次命令的局部覆盖，身份从 `gh api user` 推导
（`<login>` + `<id>+<login>@users.noreply.github.com`），**用之前先展示给用户确认**。

细节与故障速查见 `references/setup.md`。

### 第 1 步：判定这任务该不该上云

精简判据（完整版见 `references/task-suitability.md`）：

**该走 Actions**

- 耗时 **> 2 分钟** 且不需要实时盯着（固定开销约 25 s，且不占 agent 回合）
- 能拆成 N 个独立子任务 → matrix 最多 **256 job/run**，Free 并发 **20**，总吞吐远超单机
- 需要干净可复现的 Linux 环境 / `apt` 系统包 / 会污染本地环境
- 需要定时或事件触发、需要仓库凭据、失败要留证可回溯

**不该走**

- 任务 **< 1 分钟**；需要交互式 stdin；需要访问本地文件或 localhost
- 需要 GPU / >16 GB 内存 / 内网访问 / GUI（larger runner 仅 Team/Enterprise 且照样计费）
- 单 job **> 6 小时**且无法拆分（硬上限）；产物超存储配额（Free 500 MB）

**含密钥或隐私数据**不是"别用 Actions"，而是触发可见性策略 —— 见第 3 步的扫描。

### 第 2 步：初始化（一次就够）

```bash
./gha init --create --repo <owner>/<name> --yes
```

做的事：跑一遍 `setup` 门禁 → 建仓库（**默认 public**）→ 用 contents API 把
`templates/agent-dispatch.yml` 推到默认分支的 `.github/workflows/`（不需要本地 git，
也就不需要 commit 身份）→ 写 `config/repo` 与 `config/visibility` → 投递 `tasks/smoke`
冒烟并验证 artifact 闭环。

重复跑是安全的：仓库已存在则复用，dispatcher 用 **git blob sha 比对**判断是否需要更新
（本地模板改了会自动推上去）。

**为什么 dispatcher 必须常驻默认分支**：`workflow_dispatch` 只能触发存在于默认分支的工作流。
所以架构是"默认分支一个通用 dispatcher + 每次 dispatch 传任务"，而不是"每任务一个 workflow"。
大 payload 时可以 `--ref` 到别的分支跑那个分支的版本，但默认分支上必须有一份。

### 第 3 步：投递任务

任务包契约：

```
<task_dir>/
├── task.sh          # 入口，或 task.py / task.js；退出码即结果
├── README.md        # 可选
└── inputs/          # 可选，任意输入文件
```

```bash
# 异步提交（推荐）：立刻返回 run_id，不阻塞回合
./gha submit <task_dir> --yes

# 同步等待（短任务）
./gha submit <task_dir> --wait --yes
# --wait 内部已含 fetch；最长等待用 --wait-timeout SEC（默认 600）
# 注意别和 --timeout 搞混：--timeout 是【云端 job】的超时分钟数（上限 360）

# 常用选项
--task-id ID       默认从目录名派生
--runner NAME      ubuntu-latest(默认) | ubuntu-24.04 | macos-latest | windows-latest
--shell NAME       auto(默认) | bash | python3 | node
--timeout MIN      job 超时，默认 360，上限 360
--cache-key KEY    启用跨 run 状态复用（见下文「状态复用与超 6 小时任务」）
--cache-paths P    额外缓存路径，逗号或换行分隔
--out DIR          结果目录，默认 <cwd>/.gha-runs/<task_id>
--keep-ref         分支投递时取回后保留远端分支
--allow-public     跳过凭据扫描（责任自负，需用户明确要求）
```

**凭据/隐私扫描**：仓库是 public 时，`submit` 在**打包之前**扫描任务目录，命中就中止
（退出码 4），打印 `文件:行号`（**绝不回显命中的值**），给出「改私仓 / 先脱敏 / 加
`--allow-public`」三个选择，**等用户决定**。扫描放在打包之前是有意的 —— 命中时含凭据的
`bundle.tgz` 根本不会被创建，不会在本地留下带密钥的副本。

> **必须向用户说明的残留风险**：扫描是启发式的，只覆盖任务目录里的**静态文本**，覆盖不到
> 任务**运行期**才产生的敏感输出（脚本去拉私有数据、日志打印 token、`output/` 里生成含
> 客户信息的文件）。这些都会进公开的 run log 和 artifact。**这类任务要主动建议私仓。**

**投递路径自动选择**：base64 ≤ 60,000 字符走内联（dispatch input，不进 git 历史）；
超过就走分支投递（原始文件提交到 `agent/<task_id>` 分支，`--ref` dispatch，`fetch`
成功后自动删分支）。上限来自 dispatch 总 payload 硬限制 65,535 字符。

### 第 4 步：查状态 / 等待

```bash
./gha status <task_id|run_id>     # 一次性查询，输出 JSON，不阻塞
./gha wait   <task_id> --timeout 600 --interval 15
./gha list   --limit 20
./gha logs   <task_id> [--failed]
```

`task_id` 和 `run_id` 都能用 —— 映射存在 `.gha-runs/<task_id>/run_id`，不用自己记。

**`wait` 超时不算失败**：返回退出码 `2` 并打印 `{"still_running":true,...}`。
**不要无界轮询** —— 长任务应该 `submit` 完就去干别的，需要时再 `status`。

### 第 5 步：取回结果并用于后续产物

```bash
./gha fetch <task_id> --yes       # 幂等（按 run_id 判定），--force 强制重下
```

之后 agent 的标准动作：

```
1. Read .gha-runs/<task_id>/run.json               → conclusion 是否 success
2. Read .gha-runs/<task_id>/result/manifest.json   → exit_code / exit_code_recorded / task_ok
                                                     outputs（标量结果）
                                                     output_files（有哪些产物）
                                                     runner_env.cores（确认拿到 4 核）
3. Read .gha-runs/<task_id>/result/output/<文件>    → 真正用于后续产物
4. 失败时先看 manifest 的 stderr_tail，不够再 gha logs --failed
```

**`manifest.json`（runner 侧事实）和 `run.json`（API 侧事实）是分开的** —— runner 跑的
时候 run 还没结束，它不可能知道自己的 `conclusion`；而 job outputs 在 run 结束后又无法从
API 取回。artifact 内容保持只读，`fetch` 不去改它。

**失败任务的结果一样能拿回来**：`Build manifest` / `Job summary` / `Upload artifact`
三步都挂了 `if: always()`，所以任务 `exit 3` 时 run conclusion 如实是 `failure`，但
artifact 仍然上传，`exit_code=3`，**失败前已产出的部分结果完整保留**。这对长任务很关键 ——
跑了 5 小时在第 6 小时挂掉，中间产物不会白扔。

字段级说明见 `references/result-contract.md`。

## 状态复用与超 6 小时任务

每个 run 的 runner 都是**用完即毁**的全新虚拟机。要跨 run 保留现场（装好的依赖、
进度文件、中间数据），submit 时给 `--cache-key`：

```bash
./gha submit tasks/myjob --cache-key myjob-v1 --wait --yes
```

- 任务脚本把要保留的东西写进 **`$GHA_STATE_DIR`**（`~/.gha-state`，缓存不开也存在）。
  下一次同 key 的 run 启动时目录内容原样恢复（`actions/cache`，存唯一键 `<key>-<run_id>`、
  按前缀恢复最近一次）。
- **失败和超时的 run 也会保存状态**（实测）——这是续跑的基础。是否恢复成功看
  `manifest.state.cache_hit` / `restored_key`，状态体积看 `state_dir_bytes`。
- 代价：每 run 一个新缓存条目，7 天未访问过期，仓库上限 10 GB（LRU）。长项目定期换新 key。
- 详细机制与实测数据：`references/limits.md` 第 7 节。

**单 job 硬上限 6 小时，没有延长办法。** 超时的 run 结论实测是 `cancelled`（不是
`timed_out`），`gha fetch` 会用「退出码未记录 + 时长达标」组合识别并明确报"撞了时间上限"。
应对策略按优先级：

1. **能拆就拆**：matrix 分片并行（最多 256 job），或 `needs:` 串联多 job（各有独立 6h 预算）
2. **不能拆就续跑**：任务自己周期性把进度 checkpoint 到 `$GHA_STATE_DIR`，
   撞墙后用同一 `--cache-key` 再提交一次接着跑

## 子命令速查

| 命令 | 类别 | 说明 |
|---|---|---|
| `gha doctor` | A | 11 项只读检测 |
| `gha setup [--check] [--yes]` | A/B | 环境自举；`--yes` 才执行安装 |
| `gha init [--create] [--repo O/N] [--visibility public\|private] [--skip-smoke] [--yes]` | B | 建仓 + 推 dispatcher + 冒烟 |
| `gha submit <dir> [选项] [--yes]` | B | 打包 + 扫描 + 投递 |
| `gha status <task\|run>` | A | 一次性查询，输出 JSON |
| `gha wait <task\|run> [--timeout S] [--interval S]` | A | 阻塞等待；超时退出码 2 |
| `gha fetch <task\|run> [--out DIR] [--force] [--yes]` | A/B | 下载 artifact；`--yes` 才清远端分支 |
| `gha logs <task\|run> [--failed]` | A | 取 run 日志 |
| `gha list [--limit N]` | A | 列本仓库的 run |

退出码：`0` 成功 / `1` 失败 / `2` wait 超时仍在跑 / `3` 有 C 类待用户完成 /
`4` 凭据扫描命中已中止 / `125` 缺 `--yes`，B 类动作未执行。

## 文件

```
SKILL.md                        本文件
references/setup.md             环境检测矩阵、各平台安装、认证三条路径、故障速查
references/task-suitability.md  完整判定规则、边界情况、推荐动作序列
references/limits.md            已核实的硬限制与配额（含官方链接与实测标注）
references/result-contract.md   任务包契约、结果目录、manifest schema、状态复用、runner 实现要点
templates/agent-dispatch.yml    常驻默认分支的通用 dispatcher
gha                             CLI 启动器（POSIX sh，macOS/Linux）
gha.cmd                         CLI 启动器（Windows cmd，必须 CRLF 行尾）
gha_runner/                     CLI 本体（Python ≥ 3.9，纯标准库）
tests/                          单元测试（unittest，69 项）
bin/                            空白环境下 setup 下载的 gh 兜底副本（已 gitignore）
config/{repo,visibility}        init 写入的运行时配置（已 gitignore）
tasks/smoke/                    自检用示例任务
tasks/cachetest/                跨 run 状态复用示例任务
tasks/repo-insight/             真实仓库分析任务（三平台并行示例）
tasks/resume-demo/              撞墙续跑演练（checkpoint + --cache-key）
tasks/browser-test/             headless 浏览器探针
tasks/hn-browse/                真实多步浏览任务（Playwright 逛 HN）
```

`.gha-runs/` 生成在**调用方 cwd**（不是 skill 目录），已 gitignore —— 里面可能有任务产物。

## 实现约束（改代码前必读）

- **Python ≥ 3.9、只用标准库**：不引入任何第三方依赖；`pathlib.Path.write_text(newline=)`
  是 3.10+、`tarfile` 的 `filter=` 是 3.12+，这类新 API 一律禁用。每个文件统一
  `from __future__ import annotations`
- **三平台兼容**：路径一律 `pathlib`；不假设 `python3` 存在（Windows 上只有 `python` /
  `py -3`）；`gha.cmd` 必须保持 **CRLF** 行尾（cmd.exe 对 LF-only 批处理的 label 解析有坑）
- **打印中文前先防 cp1252**：Windows 控制台默认 cp1252，`print` 中文会
  `UnicodeEncodeError`。`ui.py` 在 import 时已把 stdout/stderr `reconfigure(encoding="utf-8",
  errors="replace")`；dispatcher 模板内嵌的 python 也做了同样处理 —— 改模板时别弄丢
- **Windows 的 GNU tar 把 `D:\` 盘符当远程主机**：模板解包用 `cd "$RUNNER_TEMP"` +
  相对路径，别把带盘符的路径直接喂 tar
- **所有路径双引号包裹**（开发目录路径本身就含空格）
- 退出码是对外契约：`0/1/2/3/4/125`，改语义前先看 `gha_runner/ui.py` 的注释
- `references/result-contract.md` 第八节列了 dispatcher 模板的 11 条实现要点，全是实测踩出来的
- 回归手段：`python3 -m unittest discover -s tests -t .`（69 项）；三平台真实验证靠
  把 `gha_runner/` + `tests/` 打成任务包 `./gha submit --runner windows-latest` 投到
  scratch 仓库跑（本仓库软件项目的 CI，ToS 干净）
