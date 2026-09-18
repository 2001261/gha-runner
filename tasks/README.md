# 真实场景端到端测试

两个互相配合的真实负载任务，演练本 skill 的完整闭环。

## repo-insight —— 真实计算 + 三平台并行

浅克隆 `psf/requests`（depth=300），统计提交数/贡献者/代码量，产出
`report.md` + `report.json` + 关键标量。

```bash
# 三平台并行投递（异步，不占回合）
./gha submit tasks/repo-insight --task-id insight-ubuntu  --runner ubuntu-latest  --yes
./gha submit tasks/repo-insight --task-id insight-macos   --runner macos-latest   --yes
./gha submit tasks/repo-insight --task-id insight-windows --runner windows-latest --yes
```

预期：三份 manifest 的 `outputs` 指标一致（同一代码库），`runner_env` 各不相同
（Linux 4 核 / macOS 3 核 / Windows 4 核）。

## resume-demo —— 撞墙续跑（核心亮点）

10 个分块、每块 12 秒（全程约 2 分钟），每块 checkpoint 到 `$GHA_STATE_DIR`。

```bash
# 第一次：故意给 1 分钟超时 —— 必然在半路被强杀
./gha submit tasks/resume-demo --task-id resume-1 --cache-key resume-demo --timeout 1 --yes
./gha fetch resume-1        # 诊断应为「撞了时间上限」

# 第二次：同 cache-key，从上次 checkpoint 接着跑
./gha submit tasks/resume-demo --task-id resume-2 --cache-key resume-demo --timeout 3 --yes
./gha fetch resume-2        # resumed_from > 0，任务完整跑完
```

预期：run 1 的 `state.cache_hit=false`、诊断"撞了时间上限"但 checkpoint 已保存
（`Save state` 挂 `if: always()`）；run 2 的 `cache_hit=true`、`outputs.resumed_from`
等于 run 1 完成的块数，最终 `chunks_done=10`。

## browser-test —— headless 浏览器自动化（探针）

用 ubuntu runner 预装的 Chrome（`--headless=new`）访问真实网页，截图 + dump DOM：

```bash
./gha submit tasks/browser-test --task-id browser-1 --wait --yes
```

预期：`outputs.page_title="Example Domain"`，`output/page.png` 是真实渲染的截图。

## hn-browse —— 真实多步浏览任务（Playwright）

Playwright 驱动 Chromium 逛 Hacker News：解析首页榜单前 5 条 → 逐一点进评论区 →
统计评论数、抓首条评论 → 产出 `digest.md` + `digest.json` + 首页截图。

```bash
# 首跑：安装 playwright + chromium（约 400 MB 进 $GHA_STATE_DIR）
./gha submit tasks/hn-browse --task-id hn-1 --cache-key hn-browse --wait --yes
# 复跑：同 key 缓存命中，跳过安装
./gha submit tasks/hn-browse --task-id hn-2 --cache-key hn-browse --wait --yes
```

实测（2026-09-18）：首跑安装 7s / 总 11s，复跑安装 0s / 总 8s，缓存命中 `cache_hit=true`。
runner 网络快，安装本身不慢；缓存真正的价值是**状态延续**（登录态、进度、数据）
而非单纯省时。
