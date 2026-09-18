# cachetest —— 跨 run 状态复用验证任务

验证 `gha submit --cache-key` 的缓存闭环：runner 销毁后，`$GHA_STATE_DIR` 里的内容
靠 `actions/cache` 存活，下一次同 key 的 run 能读到上一次的状态。

## 跑法

```bash
# 第一次：缓存未命中，count 应为 1
./gha submit tasks/cachetest --cache-key cachetest-demo --wait
./gha fetch cachetest

# 第二次：同一 cache_key，count 应为 2（证明状态跨 run 存活）
./gha submit tasks/cachetest --cache-key cachetest-demo --wait
./gha fetch cachetest
```

注意：每次 submit 会生成新的 task_id（带时间戳后缀时按实际输出的 id fetch）。

## 预期结果

| 校验点 | 第一次 | 第二次（同 key） |
|---|---|---|
| `manifest.json` 的 `state.cache_hit` | `false` | `true` |
| `manifest.json` 的 `outputs.count` | `"1"` | `"2"` |
| `output/result.txt` | `count=1` | `count=2` |
| `state.state_dir_bytes` | > 0 | > 0 |

## 机制说明

- 缓存保存的 key 是 `<cache_key>-<run_id>`（每次 run 唯一，避免 "Cache already exists"）；
  恢复时按前缀 `<cache_key>-` 匹配最近一次。
- 缓存条目 7 天未访问过期；仓库缓存总量上限 10 GB，超出后 LRU 淘汰。
- 除 `$GHA_STATE_DIR` 外，已存在的常见工具缓存目录（`.cache/pip`、`.npm` 等）
  也会自动纳入；额外路径用 `--cache-paths` 指定。
