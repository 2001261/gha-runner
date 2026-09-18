# 任务包与结果契约

这份契约是**功能 5「结果能用于 agent 后续产物」**的核心：agent 不需要读日志猜结果，
读 `manifest.json` 就够。

## 一、任务包：agent 要写什么

```
<task_dir>/
├── task.sh          # 入口，三选一；退出码即结果
├── task.py          #   --shell python3 时用这个
├── task.js          #   --shell node 时用这个
├── README.md        # 可选，任务说明（不执行）
└── inputs/          # 可选，任意输入文件，原样打包送上去
```

**入口查找规则**：`--shell` 指定了就先找对应文件；否则按 `task.sh` → `task.py` → `task.js`
顺序找第一个存在的。`--shell auto`（默认）就是这个自动推断。

**task_id 派生规则**：默认取任务目录名，非 `[A-Za-z0-9._-]` 的字符转成 `-`，压缩连续 `-`，
截断到 64 字符。目录名全是非 ASCII（如中文）时净化后会是空串，此时回落到
`task-<路径 cksum>`，保证不同目录不会塌成同名而互相覆盖结果。也可以 `--task-id` 显式指定。

## 二、runner 侧注入的环境变量

| 变量 | 值 | 说明 |
|---|---|---|
| `$GHA_TASK_DIR` | `/home/runner/work/_temp/gha-task` | 解包后的任务目录，**同时是 cwd** |
| `$GHA_OUTPUT_DIR` | `/home/runner/work/_temp/gha-result/output` | 产物写这里，会被打包进 artifact |
| `$GHA_RES` | `/home/runner/work/_temp/gha-result` | 结果根目录（一般不用直接碰） |
| `$GHA_ENTRY` | `task.sh` | 实际执行的入口文件名 |
| `$GHA_SHELL_KIND` | `bash` / `python3` / `node` | 实际使用的解释器 |
| `$GHA_STATE_DIR` | `$HOME/.gha-state` | **跨 run 状态目录**。不论缓存开没开都会被创建；配合 `--cache-key` 使用后，里面的内容靠 actions/cache 跨 run 存活（runner 销毁后重建） |

任务脚本该做的：

```bash
#!/usr/bin/env bash
set -uo pipefail                      # 别用 -e，除非你确实想让它中止
mkdir -p "$GHA_OUTPUT_DIR"

# ... 干活 ...

# 产物：agent 后续要消费的文件
some_tool --out "$GHA_OUTPUT_DIR/report.json"

# 标量结果：会被折进 manifest.json 的 outputs 字段，agent 不用解析文件就能读
{
  echo "rows_processed=123456"
  echo "status=complete"
  echo "report_sha256=$(shasum -a 256 "$GHA_OUTPUT_DIR/report.json" | cut -d' ' -f1)"
} > "$GHA_OUTPUT_DIR/_outputs.env"

exit 0                                 # 退出码就是结果
```

`_outputs.env` 的解析规则：每行 `KEY=value`，**只按第一个 `=` 分割**（所以 `multi=a=b` 会得到
`{"multi": "a=b"}`），空行和 `#` 开头的行跳过，前后空白裁掉。

**注意**：stdout / stderr 由 runner 全量落盘，任务不用自己重定向。任务里的 `cd` 不会影响
后续步骤（每步都是独立 shell）。

## 三、结果目录：`gha fetch` 之后本地长什么样

```
.gha-runs/<task_id>/
├── task_id                 # 真实 task_id（--out 时目录名可能和它不同，artifact 名以这个为准）
├── run_id                  # task_id → run_id 映射，之后用 task_id 就能查
├── repo                    # owner/name
├── task_dir                # 原始任务目录路径
├── ref                     # 仅分支投递时存在，如 agent/big
├── fetched_run             # ★ result/ 里的内容来自哪个 run —— 幂等判定靠它
├── bundle.tgz              # 本地打包副本
├── bundle.b64
├── run.json                # ★ API 侧事实（fetch 写入）
└── result/                 # ★ artifact 原样下载，内容只读不改
    ├── manifest.json       #   runner 侧事实
    ├── exit_code           #   单个整数
    ├── stdout.log          #   全量 stdout
    ├── stderr.log          #   全量 stderr
    └── output/             #   任务产物 + _outputs.env
```

**`fetched_run` 为什么必须存在**：幂等判定只看"`result/` 存不存在"是不够的。同一个
`task_id` 重复提交时，`result/` 里躺着的是**上一个 run** 的旧结果，而 `run.json` 已经指向
新 run —— 两个文件来自不同的 run，agent 会静默消费到错配的数据。所以 `fetch` 只在
`fetched_run == 当前 run_id` 时才跳过下载，否则重新下载覆盖。

（这个 bug 真实发生过：`run.json` 指向 run 35324416881 / 08:26:52，而 `manifest.json`
是 run 35323564587 / 08:16:54 的。）

**为什么 manifest.json 和 run.json 分开**：runner 在跑的时候，run 还没结束，它**不可能**
知道自己的 `conclusion`。而 job outputs 在 run 结束后又无法从 API 取回。所以：

- `result/manifest.json` = runner 侧能知道的事实（退出码、耗时、硬件、产物、日志尾巴）
- `run.json` = 本地 `gha fetch` 从 API 取的运行事实（status、conclusion、url、时间戳）
- **artifact 内容保持只读**，fetch 不会去改 `manifest.json`

`.gha-runs/` 默认落在**调用方的 cwd**（不是 skill 目录），可用 `--out DIR` 覆盖 ——
结果要落在正在做的项目里才方便后续消费。

## 四、`manifest.json` 字段（schema 2）

下面是 `tasks/smoke` 类任务在真实 run 上的典型输出（字段值以实际为准）：

```json
{
  "schema": 2,
  "task_id": "smoke",
  "exit_code": 0,
  "exit_code_recorded": true,
  "task_ok": true,
  "runner": "ubuntu-latest",
  "runner_os": "Linux",
  "shell": "bash",
  "entry": "task.sh",
  "job_started_at": 1789719414,
  "job_finished_at": 1789719415,
  "duration_s": 1,
  "runner_env": {
    "cores": 4,
    "mem_total_mb": 15989,
    "disk_free_gb": 86,
    "python": "3.12.3",
    "os": "Linux 6.17.0-1022-azure"
  },
  "outputs": { "smoke": "ok", "cores": "4" },
  "output_files": [
    { "path": "_outputs.env", "bytes": 17 },
    { "path": "hello.txt",    "bytes": 68 }
  ],
  "state": {
    "cache_key": "",
    "cache_hit": false,
    "restored_key": "",
    "state_dir": "/home/runner/.gha-state",
    "state_dir_bytes": 0,
    "cached_paths": ["/home/runner/.gha-state"]
  },
  "artifacts": ["result-smoke"],
  "stdout_bytes": 760,
  "stderr_bytes": 0,
  "stdout_tail": "== gha-runner smoke task ==\n...",
  "stderr_tail": ""
}
```

**读侧兼容 schema 1**：schema 1 没有 `state` 小节，用 `.get("state")` 之类的容错读取即可。

| 字段 | 类型 | 含义 / agent 该怎么用 |
|---|---|---|
| `schema` | int | 契约版本，当前 `2`（读侧兼容 `1`，差异仅 `state` 小节）。不匹配就别硬解析 |
| `task_id` | string | 任务标识 |
| **`exit_code`** | int | 任务退出码。**agent 判成败先看这个**，不是看 run conclusion |
| **`exit_code_recorded`** | bool | `false` 表示 runner 没能记录到退出码（`exit_code` 会是 `-1`），通常是 job 被强制终止（超时 / 取消）。此时**不要**把 `-1` 当成任务的真实退出码 |
| **`task_ok`** | bool | `exit_code == 0` 且步骤正常收尾。最省事的成败判据 |
| `runner` / `runner_os` | string | 实际 runner 标签与 OS |
| `shell` / `entry` | string | 实际用的解释器与入口文件 |
| `job_started_at` / `job_finished_at` | int | Unix 秒（UTC） |
| `duration_s` | int | 任务执行耗时（不含排队与 setup） |
| **`runner_env.cores`** | int | 实测 CPU 核数。**公开仓库 = 4，私有仓库 = 2** —— 用它可以反推自己拿到的是哪种规格 |
| `runner_env.mem_total_mb` | int | 实测总内存（公开仓库 ≈ 15989 MB） |
| `runner_env.disk_free_gb` | int | 工作目录可用空间 |
| **`outputs`** | object | `_outputs.env` 折进来的标量结果。**agent 后续产物最直接的输入** |
| **`output_files`** | array | `output/` 下的文件清单（相对路径 + 字节数），先看这个再决定 Read 哪个 |
| `state.cache_key` | string | 本次 run 用的缓存键；空串 = 未启用状态复用 |
| **`state.cache_hit`** | bool | **是否恢复到了上一次同 key 的状态**。注意：因为存的是唯一键 `<key>-<run_id>`，这个字段按"前缀命中（`restored_key` 非空）"判定，而不是 restore action 那个只对精确键为真的 `cache-hit` 输出 |
| `state.restored_key` | string | 实际恢复自哪个缓存条目，形如 `<cache_key>-<run_id>`；空 = 首次运行 |
| `state.state_dir` | string | `$GHA_STATE_DIR` 的实际路径（Windows 上是 `C:/Users/runneradmin/.gha-state`） |
| `state.state_dir_bytes` | int | 状态目录体积。**续跑类任务盯这个**，接近 10 GB 上限的 1/4 时 CLI 会预警 churn |
| `state.cached_paths` | array | 实际纳入缓存的路径清单 |
| `artifacts` | array | artifact 名，形如 `result-<task_id>` |
| `stdout_bytes` / `stderr_bytes` | int | 全量日志字节数 |
| `stdout_tail` / `stderr_tail` | string | 各自**末尾 4000 字节**，UTF-8 容错解码。排查问题先看这两个，不够再读 `stdout.log` |

`run.json` 字段：

```json
{
  "run_id": 35323564587,
  "status": "completed",
  "conclusion": "success",
  "title": "smoke",
  "url": "https://github.com/2001261/agent-runner/actions/runs/35323564587",
  "started_at": "2026-09-18T08:16:47Z",
  "updated_at": "2026-09-18T08:17:00Z"
}
```

`conclusion` 取值：`success` / `failure` / `cancelled` / `skipped`；run 进行中时 `status`
是 `queued` 或 `in_progress`，此时 `conclusion` 是空串 `""`。
**注意**：job 超时产生的 conclusion 是 **`cancelled`**，不存在 `timed_out`（实测，见
`references/limits.md` 第 1 节）。区分"超时"与"人为取消"要靠 `exit_code_recorded` + 时长组合，
`gha fetch`/`status` 的诊断输出已经做了这件事。

## 五、agent 消费结果的标准动作

```
1. gha fetch <task_id> --yes
2. Read .gha-runs/<task_id>/run.json      → 看 conclusion 是不是 success
3. Read .gha-runs/<task_id>/result/manifest.json
      → exit_code / exit_code_recorded / task_ok 判成败
      → outputs 拿标量结果
      → output_files 决定接下来读哪个产物
4. Read .gha-runs/<task_id>/result/output/<具体文件>   → 真正用于后续产物
5. 失败时：先看 manifest 的 stderr_tail，不够再 gha logs <task_id> --failed
```

**失败任务的结果一样能拿回来**。`Build manifest` / `Job summary` / `Upload artifact`
三步都挂了 `if: always()`，所以任务 `exit 3` 时：

- run conclusion = `failure`（如实反映，不伪装成功）
- artifact **仍然**上传
- `exit_code = 3`、`task_ok = false`
- **失败前已产出的部分结果完整保留**（实测：任务在 `exit 3` 前写的 `partial.txt` 和
  `_outputs.env` 都回来了）

这一点对长任务很关键 —— 跑了 5 小时在第 6 小时挂掉，前面的中间产物不会白扔。

## 六、投递路径与大小限制

| | 内联路径 | 分支路径 |
|---|---|---|
| 触发条件 | base64 ≤ **60,000** 字符（约 45 KB 原始数据） | 超过内联上限时自动切换 |
| 机制 | `payload_b64` 作为 dispatch input | 原始任务文件提交到 `agent/<task_id>` 分支的 `tasks/<task_id>/`，再 `--ref` dispatch |
| 为什么是 60,000 | dispatch 总 payload 硬上限 **65,535 字符**，留余量给其余 7 个 input | — |
| 是否入 git 历史 | **否**（只在 dispatch 事件里） | **是**（公开仓库 = 公开可见） |
| 事后清理 | 无需 | `fetch` 成功后删除远端分支（`--keep-ref` 可保留） |
| 前置条件 | 无 | workflow 必须已在默认分支跑过一次（`init` 的冒烟负责满足） |

实测：82,800 字节的 bundle（base64 110,400 字符）自动走分支路径，80 KB 二进制文件
经 `git push` → runner checkout 后 **cksum 完全一致**（`1270327097 81920`）。

**产物大小**：artifact 保留 7 天（`retention-days: 7`），受仓库存储配额约束（Free 500 MB）。
bundle 超过 100 MB 时 `submit` 会本地预警。

## 七、跨 run 状态复用（`--cache-key`）

每个 run 的 runner 都是全新销毁式虚拟机。要跨 run 保留环境状态（装好的依赖、进度文件、
中间数据），用 `--cache-key <key>` 启用状态复用：

```bash
./gha submit tasks/myjob --cache-key myjob-v1 --wait
```

- 任务脚本把要跨 run 保留的东西写进 **`$GHA_STATE_DIR`**（`~/.gha-state`，无论缓存开关都存在）。
- 机制：`actions/cache`，**存唯一键 `<key>-<run_id>`、按前缀 `<key>-` 恢复最近一次**（key 不可变，
  这是官方推荐解法）。额外路径用 `--cache-paths`（逗号/换行分隔）；已存在的常见工具缓存目录
  （`.cache/pip`、`.npm` 等）会自动纳入。
- **失败/超时的 run 也会保存状态**（`Save state` 挂 `if: always()`，已实测）——这正是续跑的基础：
  撞了 6 小时墙之后，再用同一 `--cache-key` 提交一次就能接着上次的现场继续。
- 判定是否恢复成功看 `manifest.state.cache_hit` / `restored_key`；状态体积看 `state_dir_bytes`。
- 代价：每 run 一个新 entry，7 天未访问过期 + 仓库 10 GB LRU。任务结束不再需要状态时，
  换个新 key 或不管它（自然过期）即可。
- **超过 6 小时的任务**没有延长办法：拆成 `needs:` 串联的多个 job（各有独立 6h 预算）、
  matrix 分片并行，或配合 `--cache-key` 做"跑满 6h → 存现场 → 新 run 续跑"。
  详见 `references/limits.md` 第 1、7 节。

## 八、runner 侧实现要点（改模板前必读）

这几条都是实测踩出来的，改 `templates/agent-dispatch.yml` 时别破坏：

1. **`shell: bash` 会让 GitHub 用 `-e -o pipefail` 调用脚本**。`Run task` 步骤必须显式
   `set +e`，否则任务一非零退出，`code=$?` 那行根本执行不到，`exit_code` 记录不上（会变成 `-1`）
2. `Run task` 最后 `exit "$code"` —— 让 run conclusion 如实反映任务结果。**不要**改成
   `exit 0`，否则失败任务在 run 列表里显示成 success，agent 会误判
3. `Build manifest` / `Job summary` / `Upload artifact` **必须**挂 `if: always()`
4. `upload-artifact` 的 `if-no-files-found` 必须是 **`warn`**，不能是 `error` ——
   否则"没结果"这件事本身会变成一个新的失败，掩盖真正的原因
5. `GITHUB_ENV` 只对**后续** step 可见。同一个 step 里写完就要读的变量必须同时 `export`
   （`GHA_FINISHED` 就是这么修好的，否则 `duration_s` 恒为 0）
6. 所有 input 一律通过 `env:` 传进脚本，**绝不**把 `${{ inputs.x }}` 直接插值进 `run:` 块
   —— 那是 GitHub 明确警告的脚本注入向量
7. manifest 用 python 组装，不要手工拼 JSON：`stdout_tail` / `stderr_tail` 是任意文本，
   含引号、换行、反斜杠和多字节字符，手工拼必错。**windows-latest 上只有 `python` 没有
   `python3`**，探测必须两个都试；且 Windows 的 stdout 是 cp1252，print 中文前要
   `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`
8. `choice` 类型的 input **不接受空字符串选项**，所以"自动推断"用 `auto` 而不是 `""`
9. `timeout-minutes` 要 `${{ fromJSON(inputs.timeout_minutes) }}` —— input 在表达式里是字符串
10. artifact **不可变**，且 v4 不支持多 job 往同名 artifact 追加。所以 artifact 名带
    `task_id`，且只在一个 job 的最后一步上传一次
11. **Windows 的 GNU tar 把 `"D:\..."` 盘符当远程主机名**（`Cannot connect to D: resolve failed`）。
    解包必须 `cd "$RUNNER_TEMP"` 后用相对路径，不能把带盘符的路径直接喂给 tar
