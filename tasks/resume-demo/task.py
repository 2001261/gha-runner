#!/usr/bin/env python3
# resume-demo —— 演练"runner 销毁后续跑"：10 个分块、每块 12 秒、逐块 checkpoint 到
# $GHA_STATE_DIR。第一次 submit 用 --timeout 1 故意撞墙，第二次同 --cache-key 接着跑完。
#
# 真实场景对应：长批处理 / 大文件转换 / 爬取任务 —— 任何"进度可序列化"的活。

import json
import os
import time

CHUNKS = 10
CHUNK_SECONDS = 12

state_dir = os.environ.get("GHA_STATE_DIR") or os.path.expanduser("~/.gha-state")
os.makedirs(state_dir, exist_ok=True)
progress_path = os.path.join(state_dir, "progress.json")

try:
    with open(progress_path, encoding="utf-8") as f:
        done = json.load(f).get("done", [])
except (OSError, ValueError):
    done = []

resumed_from = len(done)
print("== resume-demo ==")
print("状态目录: %s" % state_dir)
print("已完成 %d/%d 块，%s" % (
    len(done), CHUNKS,
    "本次是全新开始" if resumed_from == 0 else "从第 %d 块续跑（上次 run 的现场恢复成功）" % (resumed_from + 1),
), flush=True)

for i in range(resumed_from, CHUNKS):
    time.sleep(CHUNK_SECONDS)
    done.append(i)
    # 每块做完立刻 checkpoint —— 这就是"随时被杀都不丢进度"的写法
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump({"done": done, "updated_at": time.time()}, f)
    print("chunk %d/%d 完成，已 checkpoint" % (i + 1, CHUNKS), flush=True)

out_dir = os.environ["GHA_OUTPUT_DIR"]
os.makedirs(out_dir, exist_ok=True)
with open(os.path.join(out_dir, "summary.txt"), "w", encoding="utf-8") as f:
    f.write("chunks=%d\nresumed_from=%d\n" % (CHUNKS, resumed_from))
with open(os.path.join(out_dir, "_outputs.env"), "w", encoding="utf-8") as f:
    f.write("chunks_done=%d\n" % CHUNKS)
    f.write("resumed_from=%d\n" % resumed_from)
    f.write("resume_demo=complete\n")

print("全部 %d 块完成（本次从第 %d 块起跑）" % (CHUNKS, resumed_from + 1), flush=True)
