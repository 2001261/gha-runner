# 环境初始化与授权

skill 必须在**完全空白的环境**里能自举。当前开发机上 `gh` 装好且已认证，只是因为这台机器
本来就属于一个 GitHub 开发者 —— 不能把这个前提带进 skill。

## 运行方式（三平台兼容）

CLI 是 Python 包 `gha_runner/`（**Python ≥ 3.9，只用标准库**），通过两个启动器进入：

| 平台 | 命令 | 启动器行为 |
|---|---|---|
| macOS / Linux | `./gha <子命令>` | POSIX sh，依次找 `python3` / `python` |
| Windows | `gha.cmd <子命令>` 或 `.\gha.cmd <子命令>` | cmd 批处理（**必须 CRLF 行尾**），依次找 `py -3` / `python` / `python3` |

跨平台行为已在托管 runner 上实测：三平台各跑一遍 69 项单元测试全绿
（ubuntu-latest Python 3.12 / macos-latest Python 3.14 / windows-latest Python 3.12）。

> CLI 从 bash 重写而来（`gha_runner/`），入口是上面的启动器。早期文档里的
> `bash scripts/gha ...` 一律读作 `./gha ...`。

## 三类操作的授权边界

这是本 skill 最重要的一条行为规则。

| 类 | 定义 | agent 行为 |
|---|---|---|
| **A 只读检测** | `command -v`、`gh auth status`、`gh api` 的 GET、`gh repo view`、`gh run view` | 自主执行，无需确认 |
| **B 写操作** | 安装 gh、`gh repo create`、改可见性、`git push`、dispatch、删分支/删 run | **每次**都要先说明再取得用户同意；一次同意只覆盖那一次那一个动作，**不是长期许可**。CLI 层面用 `--yes` 表达 |
| **C 必须真人操作** | `gh auth login` 的浏览器 device flow、`gh auth refresh` 补 scope、组织 SSO 授权、2FA、网页开关 Actions | agent **只能打印准确命令 + 解释浏览器里会发生什么 + 停下等待**；用户说完成后**必须重新跑 A 类检测验证**，不凭口头确认往下走 |

**agent 绝不得**：

- 代用户执行 `gh auth login`（会卡死在交互提示上）
- 读取、回显、记录 token 值
- 把 token 写进任何文件、日志或 run 状态
- 用 `--allow-public` 绕过凭据扫描，除非用户明确要求

CLI 的映射：`gha doctor` = 纯 A 类；`gha setup` / `init` / `submit` / `fetch` 不带 `--yes`
时会把 B 类动作**打印出来但不执行**（返回 125）；C 类永远不由 CLI 执行。

## `gha setup` 检测矩阵

幂等，可反复跑，已满足的项直接跳过。`gha doctor` 就是 `gha setup --check`。

| # | 检查 | 检测方式 | 缺失时 | 类别 |
|---|---|---|---|---|
| 1 | OS / arch | `uname -s` / `uname -m` | 决定下载哪个安装包 | A |
| 2 | `curl` `tar` `base64` `find` `sed` `grep` `mktemp` | `command -v` | 报错并给平台安装指引（通常系统自带） | A |
| 3 | **Python ≥ 3.9** | `python3 --version` / `python --version`（Windows 加试 `py -3`） | **阻塞**：CLI 本体是 Python。macOS/Linux 装 `python3`，Windows `winget install Python.Python.3.12` | A |
| 4 | **`gh` CLI** | `type -P gh`，或 `<skill>/bin/gh` | 见「gh 安装」 | **B** |
| 5 | **gh 认证** | `gh auth status` | 见「认证流程」 | **C** |
| 6 | scope 含 `repo` + `workflow` | 同上输出的 `Token scopes` 行 | `gh auth refresh -h github.com -s repo -s workflow` | **C** |
| 7 | **git + commit 身份** | `command -v git`；`git config user.name` / `user.email` | 见「git 身份」 | A + 确认 |
| 8 | 网络连通 | `curl -m 15 -w '%{http_code}' https://api.github.com` | 非 200/401/403 → 提示代理/防火墙，检查 `HTTPS_PROXY` | A |
| 9 | 仓库 Actions 已启用 | `gh api repos/{o}/{r}/actions/permissions` → `.enabled` | 提示去 Settings → Actions 打开 | **C** |
| 10 | dispatcher 在默认分支 | `gh workflow view agent-dispatch.yml` | 提示跑 `gha init` | A |
| 11 | 配额（尽力而为） | `gh api users/{login}/settings/billing/actions` | 拿不到就跳过，**不阻塞** | A |

退出码：`0` 全绿；`1` 有 `[fail]`；`3` 有 `[needs-user]`（C 类待用户完成）。

**检测第 4 项必须用 `type -P gh`，不能用 `command -v gh`** —— 后者会把同名的 shell 函数也算
进来，拿到字符串 `"gh"` 而不是路径，再执行就变成递归调用自己直到段错误。这个 bug 真实发生过。

## gh 安装

### 首选：本地 bin 安装（不写系统目录、不需要 sudo）

`gha setup --yes` 会这么做：

1. `curl -sS https://api.github.com/repos/cli/cli/releases/latest` 取 `tag_name`
   —— **不硬编码版本号**，该端点无需认证
2. 按 OS/arch 推导 asset 名：
   - macOS：`gh_<ver>_macOS_arm64.zip` / `_macOS_amd64.zip`
   - Linux：`gh_<ver>_linux_amd64.tar.gz` / `_linux_arm64.tar.gz` / `_386` / `_armv6`
   - Windows：`gh_<ver>_windows_amd64.zip` / `_windows_arm64.zip`
3. 下载 → 解包 → **只取 `bin/gh`（Windows 为 `bin/gh.exe`）单文件**放进 `<skill>/bin/`
   （Go 静态二进制，可独立运行；`share/` 里的 shell 补全非必需）
4. macOS 上 `xattr -d com.apple.quarantine` 去掉隔离属性，否则 Gatekeeper 会拦
5. 幂等：`bin/gh` 已存在就跳过下载

**实测**：macOS/arm64 上下载 `gh_2.101.0_macOS_arm64.zip`，解出 38 MB 的 `bin/gh`，可直接运行。

### `GH` 的解析优先级

```
$GHA_GH_BIN（显式指定）  →  系统 PATH 里的 gh  →  <skill>/bin/gh
```

**系统 gh 优先于 `bin/gh`**：系统 gh 由用户/包管理器维护更新，不会变成陈旧副本；
`bin/gh` 只是空白环境下的兜底缓存，已在 `.gitignore` 里排除。

### 备选：系统包管理器（B 类，需明确同意，且要说明会写系统目录 / 需要 sudo）

```bash
brew install gh                          # macOS（有 Homebrew 时）
winget install --id GitHub.cli           # Windows
scoop install gh                         # Windows
choco install gh                         # Windows
sudo apt install gh                      # Debian/Ubuntu（需先加官方 apt 源）
sudo dnf install gh                      # Fedora/RHEL
sudo pacman -S github-cli                # Arch
```

### 实在装不了 gh

skill **不实现**无-gh 的降级路径（保持简单），`doctor` 会明确报"本 skill 需要 gh CLI"。
手工逃生通道是直接用 REST + PAT：

```bash
# 触发
curl -X POST -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/OWNER/REPO/actions/workflows/agent-dispatch.yml/dispatches \
  -d '{"ref":"main","inputs":{"task_id":"x","payload_b64":"...","task_path":"","runner":"ubuntu-latest","shell":"auto","timeout_minutes":"60"}}'

# 查状态
curl -H "Authorization: Bearer $GITHUB_TOKEN" \
  https://api.github.com/repos/OWNER/REPO/actions/runs/RUN_ID

# 下载 artifact（返回 302 到 blob 存储，需要 -L；拿到的是 zip）
curl -L -H "Authorization: Bearer $GITHUB_TOKEN" \
  https://api.github.com/repos/OWNER/REPO/actions/runs/RUN_ID/artifacts
```

## 认证流程（C 类，最容易做错的一环）

`gh auth status` 显示已登录 → 直接过。未登录时 agent 必须：

1. **停下**，说明为什么需要认证（建仓库 / 推分支 / dispatch / 下载 artifact 都要）
2. 给两条路让用户选：

   **路径 A —— 交互式（本机有浏览器，推荐）**
   ```bash
   gh auth login --hostname github.com --git-protocol https --web
   ```
   终端会打印一次性 8 位 code → 用户在浏览器打开 <https://github.com/login/device> 输入 →
   勾选授权 **`repo`** 与 **`workflow`** 两个 scope → 完成。

   **路径 B —— 非交互（无浏览器 / CI）**
   用户自行在 <https://github.com/settings/tokens> 创建含 `repo` + `workflow` 的 **classic**
   PAT，然后：
   ```bash
   gh auth login --hostname github.com --with-token    # 从 stdin 粘贴
   # 或者
   export GH_TOKEN=<PAT>
   ```

3. 用户说完成后，**重新跑 `gh auth status` 验证** scope 真的含 `repo` 与 `workflow` 才继续
4. 已登录但 scope 不足：`gh auth refresh -h github.com -s repo -s workflow`（同为 C 类，走 device flow）

### 为什么不能用 fine-grained PAT

`gh run watch` 需要 `checks:read`，而 fine-grained PAT **无法授予**这个权限。
本 skill 因此不用 `gh run watch`，改成自己轮询 `gh run view --json`（只需要 `actions:read`），
但 `doctor` 仍会检测 token 类型并给出提示。

### 组织仓库的额外门槛

- **SSO**：组织启用 SAML SSO 时，token 需要单独授权，`gh auth status` 会提示
- **Actions 白名单**：组织可以限制"只有选定 workflow 可运行"，此时 dispatcher 可能跑不起来，
  需要管理员在 Settings → Actions → General 放开
- **fork**：fork 仓库里的 Actions 默认可能需要在网页上手动启用

## git commit 身份

**本机实测 `git config --global user.name` 与 `user.email` 都是空的** —— 空白环境里这几乎是
必然。推分支（大 payload 路径）需要 commit 身份，否则 `git commit` 直接失败。

处理方式：**绝不改全局配置**，只在当次命令上做局部覆盖：

```bash
git -c user.name="$NAME" -c user.email="$EMAIL" commit -m "..."
```

身份从 GitHub 账号推导（`gh api user`）：

```
user.name  = <login>                                例：2001261
user.email = <id>+<login>@users.noreply.github.com  例：50127984+2001261@users.noreply.github.com
```

推导出来后会**先展示给用户确认**再使用。`doctor` 报 `[install]` 而不是 `[fail]`，
因为这不算故障。

**实测**：走完大 payload 的分支投递后，`git config --global --get user.name` 仍为空 ——
证明全局配置没被污染。

## 在空白环境下验证（本机可复现）

| 方法 | 做法 | 验证什么 |
|---|---|---|
| **PATH 屏蔽** | `env -i PATH=/usr/bin:/bin HOME=$HOME ./gha doctor` | `gh` 与 `brew` 都不可见 → 应报 `[install] gh`，给出准确补救命令，且**不擅自安装** |
| **HOME 隔离** | `HOME=$(mktemp -d) ./gha doctor` | gh 视为从未认证 → 应报 `[needs-user]`，打印两条认证路径 |
| **docker**（真隔离） | `docker run --rm -it -v "$PWD":/skill ubuntu:24.04 bash` | Linux 分支的下载 URL、tar 解包、依赖差异 |

`setup --yes` 在 PATH 屏蔽环境下会真实下载并安装 `bin/gh`；重复跑第二次应跳过下载（幂等）。

## 常见故障速查

| 症状 | 原因 | 处理 |
|---|---|---|
| 任务的 `exit_code` 记录成 `-1` | `shell: bash` 让 GitHub 用 `-e` 调用脚本，任务非零退出时 `code=$?` 来不及执行 | `run:` 块开头显式 `set +e`（dispatcher 模板已处理，勿破坏） |
| `manifest.json` 里 `job_finished_at` 是 0 | 在**同一个** step 里 `echo X >> $GITHUB_ENV` 然后读 `$X` —— `GITHUB_ENV` 只对后续 step 可见 | 同时 `export X=...` |
| `conclusion` 判空失效 | run 进行中时 API 返回 `""` 而不是 `null` | 判空要同时兜 `null` 和 `""` |
| `dispatch` 返回 404 / "workflow not found" | 工作流不在默认分支上，或 GitHub 还没注册完 | 确认 `.github/workflows/agent-dispatch.yml` 在默认分支；推送后轮询 `gh workflow view` 几秒 |
| 下载 artifact 报 "no artifacts found" | run 还没结束，或 artifact 名不匹配（应为 `result-<task_id>`），或已过 7 天保留期 | `gh run view <id> --json artifacts` 排查 |
| `fetch` 说"已存在跳过下载"，但 `run.json` 和 `manifest.json` 时间戳对不上 | 幂等判定只看 `result/` 存不存在，同一个 task_id 重复提交时复用了**上一个 run** 的旧结果 | 用 `fetched_run` 记录 result 来自哪个 run，只在它等于当前 run_id 时才跳过；否则重下覆盖 |
| `gh api` 的错误响应被当成成功数据 | `gh api` 遇到 404 会把错误 JSON 打到 **stdout**（"gh: Not Found" 才走 stderr），`2>/dev/null` 拦不住 | 别只判响应非空，要确认目标字段真的存在 |
| 等待时长不生效 / job 超时被设成几百分钟 | `--timeout` 是**云端 job** 的超时分钟数，`--wait-timeout` 才是本地等待秒数，两者容易混 | 见 `./gha --help`；`submit --wait --wait-timeout 300` 才是"最多等 300 秒" |
| Windows 上 tar 报 `Cannot connect to D:` | GNU tar 把盘符当 `host:path` 远程语法 | `cd` 进目录后用相对路径（模板已处理） |
| Windows 上 python 任务 print 中文崩溃 | stdout 默认 cp1252 | 模板已统一 `PYTHONIOENCODING=utf-8`；本地 CLI 侧在 `ui.py` 里做了 reconfigure |

下面三条是**已删除的 bash 版 CLI 的病史**（Python 重写后不复存在，留档供考古）：

| 症状 | 原因 | 当时的修法 |
|---|---|---|
| `gh` 命令存在但一执行就段错误 | 定义了名为 `gh` 的 shell 函数，`command -v gh` 返回函数名导致递归 | 用 `type -P gh` |
| `xxx﹖: unbound variable`，变量名后带乱码字节 | `$VAR` 后面紧跟全角字符（如 `$repo（`），bash 把多字节首字节吃进了变量名 | 一律写成 `${VAR}` |
| `die` 之后脚本继续往下跑 | 在 `$( )` 子 shell 里调用了 `die`，`exit` 只退出了子 shell | 判定逻辑放父 shell |
