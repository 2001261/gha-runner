#!/usr/bin/env bash
# 冒烟任务：验证 gha-runner 的端到端闭环（提交 → 执行 → artifact → 取回）。
# 只做只读的环境探测，不改动任何东西。
set -uo pipefail

echo "== gha-runner smoke task =="
echo "task_dir   = ${GHA_TASK_DIR:-<unset>}"
echo "output_dir = ${GHA_OUTPUT_DIR:-<unset>}"
echo "cwd        = $(pwd)"
echo "date       = $(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo
echo "-- runner hardware --"
echo "uname      = $(uname -a)"
echo "cores      = $(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo unknown)"
free -m 2>/dev/null | head -2 || echo "(free not available)"
df -h . 2>/dev/null | tail -1

echo
echo "-- inputs --"
if [ -d inputs ]; then
  find inputs -type f -print
else
  echo "(no inputs/ dir)"
fi

# 产物：agent 后续要消费的文件
mkdir -p "$GHA_OUTPUT_DIR"
{
  echo "hello from GitHub Actions"
  echo "cores=$(nproc 2>/dev/null || echo 0)"
  echo "generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$GHA_OUTPUT_DIR/hello.txt"

# 标量结果：会被折进 manifest.json 的 outputs 字段
{
  echo "smoke=ok"
  echo "cores=$(nproc 2>/dev/null || echo 0)"
} > "$GHA_OUTPUT_DIR/_outputs.env"

echo
echo "-- output/ --"
find "$GHA_OUTPUT_DIR" -type f -print
echo
echo "smoke task finished with exit 0"
exit 0
