#!/usr/bin/env bash
# hn-browse —— 真实浏览器自动化任务：Playwright 驱动 Chromium 逛 Hacker News。
#
# 多步浏览（不是打开一个静态页就完事）：
#   1. 打开 HN 首页，解析榜单前 5 条（标题 / 积分 / 链接）
#   2. 逐一点进每条的评论区，统计评论数、抓首条评论摘要
#   3. 截首页图，产出 digest.md + digest.json
#
# 同时演示 --cache-key 的高价值用法：playwright 包与 chromium 二进制都装进
# $GHA_STATE_DIR，第二次同 key 的 run 直接跳过安装（首跑约 1-2 分钟 → 复跑十几秒）。
set -uo pipefail

echo "== hn-browse =="
PYBIN=""
if command -v python3 >/dev/null 2>&1; then PYBIN=python3
elif command -v python >/dev/null 2>&1; then PYBIN=python; fi
[ -n "$PYBIN" ] || { echo "::error::没有 python"; exit 1; }

LIBS="$GHA_STATE_DIR/pylibs"
export PLAYWRIGHT_BROWSERS_PATH="$GHA_STATE_DIR/ms-playwright"
INSTALL_START=$(date -u +%s)

if [ -d "$LIBS" ] && [ -d "$PLAYWRIGHT_BROWSERS_PATH" ]; then
  echo "依赖与浏览器均已缓存，跳过安装"
  SKIPPED_INSTALL=1
else
  echo "安装 playwright（进 \$GHA_STATE_DIR，可被缓存复用）..."
  "$PYBIN" -m pip install --quiet --target="$LIBS" playwright || { echo "::error::pip 安装失败"; exit 1; }
  PYTHONPATH="$LIBS" "$PYBIN" -m playwright install chromium --only-shell 2>&1 | tail -1 || { echo "::error::浏览器下载失败"; exit 1; }
  SKIPPED_INSTALL=0
fi
INSTALL_S=$(( $(date -u +%s) - INSTALL_START ))
echo "安装阶段耗时: ${INSTALL_S}s（skipped=$SKIPPED_INSTALL）"

mkdir -p "$GHA_OUTPUT_DIR"
PYTHONPATH="$LIBS" INSTALL_S="$INSTALL_S" SKIPPED_INSTALL="$SKIPPED_INSTALL" \
  "$PYBIN" ./browse.py
code=$?
exit $code
