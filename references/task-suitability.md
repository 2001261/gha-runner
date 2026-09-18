# 什么任务该走 GitHub Actions（判定规则）

功能 1。判定前先记住一句话：

> **Actions 的优势是并行吞吐、隔离环境、异步不占 agent 回合、结果可回溯 —— 不是单机速度。**

以及本 skill 的可见性前提：

> **原则上用公开仓库。** 公开仓库的 `ubuntu-latest` 是 **4 vCPU / 16 GB**（实测 `cores=4`、
> `mem_total_mb=15989`），标准 runner 分钟数**免费且不限量**；私有仓库只有 2 vCPU / 8 GB，
> 还要吃 Free 计划 2,000 分钟/月的配额。

## 实测的固定开销

判定"值不值得上云"要有真实数字，以下是本 skill 在公开仓库上的实测值：

| 任务 | 任务自身耗时 | run 总时长 | 云端固定开销 |
|---|---|---|---|
| `smoke` | 1 s | 13 s | **≈ 12 s** |
| `slow` | 120 s | 131 s | **≈ 11 s** |

云端固定开销 = 排队 + Set up job + checkout + prepare + manifest + upload artifact。
本地 `submit`（打包 + 扫描 + dispatch + 解析 run_id）另需 **≈ 10 s**；走分支投递时再加
一次浅克隆 + push，约 **10–20 s**。

**结论：端到端固定开销约 25 秒（内联）/ 40 秒（分支）。** 这比很多人以为的"上云要等一两分钟"
低得多 —— 所以判定阈值可以定得比直觉更低。

## 该走 Actions

| 信号 | 依据 |
|---|---|
| **耗时 > 2 分钟**，且不需要实时盯着 | 固定开销约 25 s，2 分钟以上就划算；更关键的是**不占 agent 回合**，可以 `submit` 完立刻干别的 |
| **能拆成 N 个独立子任务** | matrix 单次 run 最多 **256 job**，Free 并发 **20 job** → 20 路并行的总吞吐远超任何单机；单 job 6 h 上限靠拆分绕开 |
| **需要干净、可复现的 Linux 环境** | runner 每次全新，`apt` 可用；本地 macOS 没有 apt，容器化成本高 |
| **会污染本地环境** | 装一堆系统依赖、写 `/usr/local`、拉几个 GB 的数据集 —— runner 用完即弃 |
| **需要定时或事件触发** | cron / push / PR / release；本地没有常驻守护进程 |
| **需要仓库凭据** | Actions 天然持有 `GITHUB_TOKEN`，不用把 token 落到本地 |
| **失败要留证、结果要可回溯** | run log + artifact 保留 7 天（可调到 90 天），会话结束后还能查 |
| **磁盘/内存短时峰值超本机** | 公开仓库 16 GB 内存、工作盘实测可用 86 GB |
| **任务要跑很久但结果不急** | 单 job 上限 6 h；`submit` 不阻塞，`wait --timeout` 超时也只是返回 `still_running` |
| **要跨 run 保留环境/断点续跑** | `--cache-key` 用 actions/cache 复用 `$GHA_STATE_DIR`：装依赖一次、次次命中；**失败/超时 run 也保存状态**（实测），撞 6h 墙后同 key 再投一次接着跑 |

## 不该走 Actions（留在本地）

| 信号 | 依据 |
|---|---|
| **任务 < 1 分钟** | 25 s 固定开销占比过高；一次性小改动、读几个文件、跑个 lint 纯属绕路 |
| **需要交互式输入** | Actions 没有 stdin，无法应答提示 |
| **需要访问本地文件系统 / localhost 服务** | runner 是隔离云环境，看不到你的机器；要传上去的都得打包（内联上限约 45 KB） |
| **需要 GUI / 显示器 / 音频设备** | 标准 runner 无头。**但 headless 浏览器自动化不算在内，可以做**（实测：ubuntu runner 预装 Chrome/Firefox，`--headless=new` 截图、dump DOM 正常），示例见 `tasks/browser-test/` |
| **需要 GPU 或 > 16 GB 内存** | larger runner 仅 Team / Enterprise Cloud 组织可用，且**即使跑在公开仓库也照样计费**；GPU runner 只有 1× Tesla T4 |
| **单 job 需要 > 6 小时且无法拆分、无法 checkpoint 续跑** | 硬上限，到点强制终止；能 checkpoint 的话用 `--cache-key` 续跑绕过 |
| **产物总量超存储配额** | Free 500 MB；bundle 超 100 MB 时 `submit` 会本地预警 |
| **需要访问内网 / VPN 后的资源** | GitHub 托管 runner 在公网，进不去你的内网（要 self-hosted runner，超出本 skill 范围） |
| **延迟敏感、要秒级反馈的迭代** | 每轮 25 s 起步，本地跑更快 |
| **用户显式选了私仓、又想让单任务更快** | 私有 `ubuntu-latest` 只有 2 vCPU / 8 GB，大概率慢于本机 —— 此时应建议改公开仓库 |

## 边界情况

### 「任务含密钥 / 隐私数据」

这**不是**"别用 Actions"，而是触发**可见性策略**：

`submit` 在**打包之前**扫描任务目录，命中凭据特征就中止（退出码 4），打印命中的
`文件:行号`（**绝不回显命中的值**），给出三个选择：

- (a) 改用私有仓库：`gha init --repo <owner>/<name> --visibility private --create --yes`
- (b) 先脱敏，再用公开仓库重跑
- (c) 确认可以公开：加 `--allow-public`（责任自负）

**由用户决定，skill 不会自行切换可见性。**

扫描覆盖的特征：`ghp_` / `gho_` / `ghu_` / `ghs_` / `ghr_` / `github_pat_` / `sk-` /
`AKIA` / `xox[baprs]-` / `-----BEGIN ... PRIVATE KEY-----` / `Bearer <20+ 字符>`，
以及 `password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|client[_-]?secret`
后跟 `:` 或 `=` 的赋值（含带引号的值）；文件名层面拦 `.env` / `id_[dr]sa` / `*.pem` /
`*.p12` / `*.pfx` / `*.key` / `credentials*` / `*token*` / `*secret*` / `*keystore*`。

**残留风险（必须向用户说明）**：扫描是启发式的，只覆盖任务目录里的**静态文本**，覆盖不到
任务**运行期**才产生的敏感输出 —— 比如脚本去拉私有数据、日志里打印 token、`output/`
里生成含客户信息的文件。这些都会进公开的 run log 和 artifact。**这类任务要主动建议私仓**，
不能依赖扫描。

**误报**：`token=` / `api_key` 这类模式会命中正常的示例代码和文档。设计上是"中止并问用户"
而不是静默失败，`--allow-public` 是出口。

### 「任务要跑 8 小时」

单 job 上限 6 h，硬限制。三条路：

1. **拆分**成多个 ≤6 h 的子任务，用 matrix 并行（首选，还能提速）
2. **续跑**：任务周期性把进度 checkpoint 到 `$GHA_STATE_DIR`，submit 带 `--cache-key`；
   撞墙（或失败）后用同一 key 再投一次，从上次现场接着跑。实测超时后 `if: always()` 的
   保存步骤**仍然会执行**，状态不会随强杀丢失
3. 真的不可拆也不可续 → 不适合本 skill，考虑 self-hosted runner 或别的方案

### 「产物有 2 GB」

Free 计划存储配额 500 MB，会上传失败。做法：产物在 runner 侧压缩/分片，或者只把
**摘要与关键结果**放进 artifact，大文件传到别处（对象存储、release asset）再把链接
写进 `_outputs.env`。

### 「只是想跑个 10 秒的脚本」

留在本地。25 s 固定开销 + 一次网络往返，比直接跑慢得多。

### 「任务要读我本地的一个 3 GB 数据集」

内联上限约 45 KB，分支路径虽然没这个限制但要把 3 GB 推进 git —— 都不合适。
正确做法是让任务自己去公开源下载数据，或者这任务就不该上云。

## 判定的推荐动作序列

```
1. 估任务耗时
   < 1 分钟        → 本地跑，结束
   1–2 分钟        → 本地跑（除非需要隔离环境或要留证）
   > 2 分钟        → 继续

2. 能否拆成独立子任务？
   能   → matrix 并行，总耗时 ≈ 单份耗时（受并发数 20 限制）
   不能 → 单 job，确认 ≤ 6 h

3. 任务会不会碰密钥 / 私有数据 / 客户数据（含运行期产出）？
   会   → 【建议】私仓，把两个方案的代价摆给用户，由用户选
   不会 → 公开仓库（默认，4 vCPU + 免费分钟数）

4. 需要 GPU / >16GB 内存 / 内网访问 / GUI？
   需要 → 本 skill 覆盖不了，说明原因并给替代方案
   不需要 → 继续

5. submit（不加 --wait）→ 立刻回去干别的
   → 需要结果时 status / wait --timeout / fetch
```
