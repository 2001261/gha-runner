# gha-runner

> 把高强度任务委派给 GitHub Actions 云端执行，取回结构化结果 —— 给 AI agent 用的
> 云端批处理卸载层（CLI + Agent Skill）。

每个 run 都是一台**用完即毁**的云端沙盒：公开仓库的 `ubuntu-latest` 给到
**4 vCPU / 16 GB / 86 GB 可用磁盘**（实测），分钟数**免费且不限量**。gha-runner 把
"该不该上云 → 环境自举 → 投递 → 等待 → 取回 → 结果喂给后续产物"这条链路做成了
一条命令的事。

## 它能干什么（全部经过真实 run 验证）

| 能力 | 实测证据 |
|---|---|
| 云端跑任务，结果结构化取回 | `manifest.json`（退出码/硬件/标量结果/产物清单）+ artifact 产物目录，agent 直接 Read 消费 |
| 三平台并行 | 同一仓库分析任务在 ubuntu / macos / windows 上算出**完全一致**的结果（595 commits / 170 贡献者 / 12032 行） |
| 跨 run 状态复用 | `--cache-key` + `$GHA_STATE_DIR`：装好的依赖下次还在，**被超时强杀的任务能从 checkpoint 续跑**（实测：10 块任务跑 7 块被杀，第二个 run 从第 8 块接跑完成） |
| Headless 浏览器自动化 | Playwright 驱动 Chromium 逛 Hacker News：解析榜单 → 逐条点进评论区 → 生成摘要（`tasks/hn-browse/`） |
| 失败任务结果不丢 | 任务 `exit 3`，artifact 照样上传，失败前产出的部分结果完整保留 |
| 超时智能诊断 | 撞时间上限 ≠ 任务失败：三信号组合识别（GitHub 对超时的 conclusion 是 `cancelled` 而不是 `timed_out`，这是实测踩出来的） |

端到端固定开销约 **25 秒**（内联投递）——比直觉的"上云要等一两分钟"低得多。

## 它**不**是什么

不是交互式云电脑。SSH / RDP / 浏览器桌面那套被 GitHub ToS 明确禁止（与仓库无关的
通用计算、多人同机访问），做了会炸整个 GitHub 账号，所以**刻意不做**。它是批处理
执行器：交一个任务、收一份报告。任务与任务之间的环境延续靠 `actions/cache`。

## 快速开始

环境要求：**Python ≥ 3.9**（纯标准库，零依赖）+ **gh CLI**（没有的话 `setup` 会引导安装）。

```bash
git clone <本仓库地址> && cd gha-runner

# 0. 环境自检（只读，随时可跑）
./gha doctor                    # Windows 用 gha.cmd

# 1. 初始化：建仓 + 推 dispatcher + 冒烟验证（B 类写操作，需 --yes）
./gha init --create --repo <owner>/<name> --yes

# 2. 跑内置示例
./gha submit tasks/smoke --wait --yes

# 3. 跑你自己的任务：一个目录 + 一个入口文件（task.sh / task.py / task.js）
./gha submit path/to/my-task --wait --yes
```

任务脚本里约定两个环境变量：

```bash
echo "结果文件" > "$GHA_OUTPUT_DIR/report.txt"      # 产物，会被打包带回
echo "rows=123456" > "$GHA_OUTPUT_DIR/_outputs.env" # 标量，折进 manifest.outputs
echo "进度" > "$GHA_STATE_DIR/progress.txt"          # 跨 run 状态（配 --cache-key）
```

取回与消费：

```bash
./gha fetch <task_id> --yes
# .gha-runs/<task_id>/result/manifest.json   ← exit_code / outputs / runner_env / state
# .gha-runs/<task_id>/result/output/         ← 任务产物
```

## 三平台兼容

CLI 本体是 Python（≥3.9，纯标准库），通过两个启动器进入：`./gha`（macOS/Linux）、
`gha.cmd`（Windows）。跨平台行为不是"设计上支持"，是在托管 runner 上**实测**的：
把 `gha_runner/` + `tests/` 打成任务包投到三个平台的 runner 上跑 69 项单元测试，
全绿。Windows 特有的坑（tar 盘符、没有 `python3`、cp1252 控制台）都已修复并记录在
`references/limits.md` 第 8 节。

## 工作原理

```
本地                                    GitHub
┌──────────────┐   workflow_dispatch   ┌─────────────────────────┐
│ 任务目录      │ ───────────────────► │ 常驻默认分支的            │
│  task.sh     │   (小:内联base64      │ 通用 dispatcher          │
│  inputs/     │    大:agent/<id>分支) │  ├─ 解包任务              │
└──────────────┘                       │  ├─ 恢复状态(cache)       │
        ▲                              │  ├─ 跑任务(set +e!)       │
        │   artifact (result-<id>)     │  ├─ 保存状态(always)      │
        └───────────────────────────── │  └─ 上传 manifest+产物    │
              gh run download          └─────────────────────────┘
```

为什么是这个架构：`workflow_dispatch` 只能触发存在于默认分支的工作流，所以是
"一个通用 dispatcher + 每次 dispatch 传任务"，而不是每个任务一个 workflow。

## 安全模型

三档授权边界，CLI 强制执行：

- **A 类只读**（doctor/status/list/logs）：自主执行
- **B 类写操作**（安装 gh、建仓、推分支、dispatch）：**不带 `--yes` 只打印命令不执行**（退出码 125）
- **C 类必须真人**（gh 认证、补 scope）：CLI 只打印指引并停下等待

公开仓库投递前自动做**凭据/隐私扫描**（命中即中止，退出码 4，且扫描发生在打包**之前**，
不会在本地留下含密钥的 bundle）。扫描是启发式的，覆盖不了运行期才产生的敏感输出——
这类任务请用私有仓库。

## 已知限制

- 单 job **6 小时**硬上限，无延长办法 → matrix 拆分 / `needs:` 串联 / checkpoint 续跑
- 无 GPU、无 GUI、进不了内网；内存上限 16 GB
- artifact 保留 7 天，Free 存储配额 500 MB；actions/cache 仓库上限 10 GB（LRU + 7 天过期）
- 交互式沙盒（实时进去操作）不在能力范围，需要的话请用 E2B / Modal 这类专用服务

## 目录结构

```
├── SKILL.md                Agent Skill 定义（触发器 + 使用说明）
├── gha / gha.cmd           CLI 启动器（Unix / Windows）
├── gha_runner/             CLI 本体（Python ≥3.9，纯标准库）
├── references/             判定规则、安装认证、限制实测、结果契约
├── templates/              dispatcher 工作流模板
├── tasks/                  示例任务（smoke / cachetest / repo-insight /
│                           resume-demo / browser-test / hn-browse）
├── assets/                 README 用的图片（公众号二维码）
└── tests/                  69 项单元测试
```

## 开发与测试

```bash
python3 -m unittest discover -s tests -t .   # 69 项单元测试

# 用本工具测本工具：把测试投到三个平台的 runner 上跑（真·自举）
./gha submit <打包的测试目录> --runner windows-latest --wait --yes
```

## 文档地图

| 文件 | 内容 |
|---|---|
| `SKILL.md` | skill 定义：触发条件、工作流、授权边界 |
| `references/task-suitability.md` | 什么任务该上云的完整判定规则 |
| `references/setup.md` | 环境检测矩阵、认证流程、故障速查 |
| `references/limits.md` | 硬限制与配额（官方来源 + 实测标注） |
| `references/result-contract.md` | 任务包契约、manifest schema、模板实现要点 |

## 作者与支持

**王晶晶律师 · 四川恒和信律师事务所**

如遇到使用问题，请关注微信公众号「**隔壁王律师**」寻求帮助：

![微信公众号「隔壁王律师」二维码](assets/wechat-qr.jpg)

（若图片无法加载请访问 http://weixin.qq.com/r/mp/xiARCabEX-sgreJq93XU ）

## License

Copyright 2026 王晶晶（四川恒和信律师事务所）

[Apache-2.0](LICENSE)
