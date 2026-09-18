# smoke —— gha-runner 自检任务

验证 `gha-runner` 的完整闭环：环境自举 → 打包投递 → 云端执行 → artifact → 本地取回。

只做只读的环境探测（`uname` / `nproc` / `free` / `df`），不修改任何东西，不含凭据，
可以安全地跑在**公开仓库**上。

## 跑法

```bash
./gha submit tasks/smoke --wait --yes
./gha fetch smoke
```

## 预期结果

`.gha-runs/smoke/result/` 下：

| 文件 | 预期 |
|---|---|
| `manifest.json` | `exit_code=0`、`conclusion=success`、`runner_env.cores=4`（公开仓库 ubuntu-latest） |
| `manifest.json` 的 `outputs` | 含 `smoke=ok` 与 `cores=4` |
| `output/hello.txt` | 三行：问候 + cores + 生成时间 |
| `stdout.log` | 含 runner 硬件探测输出 |
| `exit_code` | `0` |

`runner_env.cores` 是 4 而不是 2，说明确实拿到了**公开仓库**的 runner 规格
（私有仓库的 `ubuntu-latest` 只有 2 vCPU / 8 GB）。
