#!/usr/bin/env bash
# cachetest —— 验证跨 run 状态复用（actions/cache + $GHA_STATE_DIR）。
#
# 逻辑：每次运行把 $GHA_STATE_DIR/counter.txt 里的计数 +1。
#   第一次跑（缓存未命中）：读到 0，写成 1
#   第二次跑（同一 cache_key）：读到 1，写成 2 —— 证明状态真的跨 run 存活
# 计数与命中情况同时写进 output/ 和 _outputs.env，供本地校验。
set -uo pipefail

STATE_DIR="${GHA_STATE_DIR:-$HOME/.gha-state}"
COUNTER="$STATE_DIR/counter.txt"

echo "== gha-runner cachetest task =="
echo "state_dir = $STATE_DIR"

mkdir -p "$STATE_DIR"
n=0
if [ -f "$COUNTER" ]; then
  n=$(cat "$COUNTER" | tr -d '[:space:]')
  case "$n" in ''|*[!0-9]*) n=0 ;; esac
fi
echo "读到的计数 = $n"
n=$((n + 1))
echo "$n" > "$COUNTER"
echo "写入的计数 = $n"

# 顺带落一个带时间戳的文件，证明状态目录里可以放任意产物
date -u +%Y-%m-%dT%H:%M:%SZ >> "$STATE_DIR/runs.log"

mkdir -p "$GHA_OUTPUT_DIR"
{
  echo "count=$n"
  echo "state_dir=$STATE_DIR"
} > "$GHA_OUTPUT_DIR/result.txt"

# _outputs.env：折进 manifest.outputs 的小标量
{
  echo "count=$n"
  echo "cachetest=ok"
} > "$GHA_OUTPUT_DIR/_outputs.env"

echo "== cachetest done (count=$n) =="
