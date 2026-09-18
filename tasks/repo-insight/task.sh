#!/usr/bin/env bash
# repo-insight —— 真实负载任务：浅克隆一个真实开源仓库，做代码与历史统计。
#
# 产出：report.md（人读）+ report.json（机读）+ _outputs.env（关键标量）。
# 只用 git / find / wc / awk + python（拼 JSON），三平台 runner 都自带。
set -uo pipefail

REPO_URL="https://github.com/psf/requests"
DEPTH=300

echo "== repo-insight =="
echo "目标仓库: ${REPO_URL}（浅克隆 depth=${DEPTH}）"

rm -rf /tmp/insight-src
git clone --depth "$DEPTH" --quiet "$REPO_URL" /tmp/insight-src || { echo "clone 失败" >&2; exit 1; }
cd /tmp/insight-src

commits=$(git rev-list --count HEAD)
contributors=$(git shortlog -se HEAD | wc -l | tr -d ' ')
top_author=$(git shortlog -sne HEAD | sort -rn | head -1 | sed 's/^[[:space:]]*[0-9]*[[:space:]]*//')
first_date=$(git log --reverse --format=%aI | head -1)
last_date=$(git log -1 --format=%aI)

py_files=$(find . -name '*.py' -not -path './.git/*' | wc -l | tr -d ' ')
py_lines=$(find . -name '*.py' -not -path './.git/*' -exec cat {} + | wc -l | tr -d ' ')
all_files=$(find . -type f -not -path './.git/*' | wc -l | tr -d ' ')

echo "commits=$commits contributors=$contributors py=$py_files 文件 / $py_lines 行"

mkdir -p "$GHA_OUTPUT_DIR"

# 人读报告
{
  echo "# requests 仓库速览（浅克隆 depth=${DEPTH}）"
  echo
  echo "| 指标 | 值 |"
  echo "|---|---|"
  echo "| 窗口内提交数 | $commits |"
  echo "| 窗口内贡献者 | $contributors |"
  echo "| 提交最多的人 | $top_author |"
  echo "| 窗口起点 | $first_date |"
  echo "| 最新提交 | $last_date |"
  echo "| 文件总数 | $all_files |"
  echo "| Python 文件 | ${py_files}（${py_lines} 行） |"
  echo
  echo "runner: $(uname -s) $(uname -m)，生成于 $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$GHA_OUTPUT_DIR/report.md"

# 机读 JSON：作者名可能含引号/非 ASCII，必须交给 python 拼
PYBIN=""
if command -v python3 >/dev/null 2>&1; then PYBIN=python3
elif command -v python >/dev/null 2>&1; then PYBIN=python; fi
COMMITS="$commits" CONTRIBUTORS="$contributors" TOP_AUTHOR="$top_author" \
FIRST_DATE="$first_date" LAST_DATE="$last_date" \
ALL_FILES="$all_files" PY_FILES="$py_files" PY_LINES="$py_lines" \
OUT_DIR="$GHA_OUTPUT_DIR" "$PYBIN" - <<'PY'
import json, os, platform
data = {
    "repo": "psf/requests",
    "commits_in_window": int(os.environ["COMMITS"]),
    "contributors_in_window": int(os.environ["CONTRIBUTORS"]),
    "top_author": os.environ["TOP_AUTHOR"],
    "window_first_commit": os.environ["FIRST_DATE"],
    "window_last_commit": os.environ["LAST_DATE"],
    "files_total": int(os.environ["ALL_FILES"]),
    "python_files": int(os.environ["PY_FILES"]),
    "python_lines": int(os.environ["PY_LINES"]),
    "runner_platform": platform.system(),
}
with open(os.path.join(os.environ["OUT_DIR"], "report.json"), "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY

# 标量进 manifest.outputs，agent 不用解析文件就能消费
{
  echo "repo=psf/requests"
  echo "commits=$commits"
  echo "contributors=$contributors"
  echo "py_files=$py_files"
  echo "py_lines=$py_lines"
  echo "insight=ok"
} > "$GHA_OUTPUT_DIR/_outputs.env"

echo "== 完成 =="
