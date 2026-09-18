#!/usr/bin/env python3
"""hn-browse 的浏览主体：Playwright 驱动 Chromium 完成一次真实的多步浏览。"""

import json
import os
import sys

# 同模板里的防护：Windows runner 的 stdout 是 cp1252（模板已 export PYTHONIOENCODING，
# 这里再兜一层，保证任务单独拎出来跑也不崩）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from playwright.sync_api import sync_playwright

OUT = os.environ["GHA_OUTPUT_DIR"]
TOP_N = 5

print("[1/3] 打开 Hacker News 首页 ...", flush=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto("https://news.ycombinator.com", timeout=30000)
    page.wait_for_selector(".athing", timeout=15000)

    stories = []
    rows = page.query_selector_all(".athing")[:TOP_N]
    for row in rows:
        title_el = row.query_selector(".titleline a")
        item_id = row.get_attribute("id")
        sub = page.query_selector(f"#score_{item_id}")
        stories.append({
            "id": item_id,
            "title": title_el.inner_text() if title_el else "",
            "url": title_el.get_attribute("href") if title_el else "",
            "score": sub.inner_text().split()[0] if sub else "0",
        })
    page.screenshot(path=os.path.join(OUT, "hn-frontpage.png"))
    print(f"      首页榜单解析出 {len(stories)} 条，已截图", flush=True)

    print("[2/3] 逐条点进评论区 ...", flush=True)
    for s in stories:
        cpage = browser.new_page()
        cpage.goto(f"https://news.ycombinator.com/item?id={s['id']}", timeout=30000)
        try:
            cpage.wait_for_selector(".comment-tree", timeout=10000)
            comments = cpage.query_selector_all(".comment-tree .comment")
            s["comment_count"] = len(comments)
            first = cpage.query_selector(".comment-tree .commtext")
            s["first_comment"] = (first.inner_text()[:200] + "...") if first else ""
        except Exception:
            s["comment_count"] = 0
            s["first_comment"] = ""
        print(f"      #{s['id']} 「{s['title'][:40]}」 {s['score']} 分 / {s['comment_count']} 条评论", flush=True)
        cpage.close()

    browser.close()

print("[3/3] 写报告 ...", flush=True)
install_s = os.environ.get("INSTALL_S", "?")
skipped = os.environ.get("SKIPPED_INSTALL", "?")

with open(os.path.join(OUT, "digest.json"), "w", encoding="utf-8") as f:
    json.dump({"source": "news.ycombinator.com", "stories": stories}, f,
              ensure_ascii=False, indent=2)
    f.write("\n")

lines = ["# Hacker News 头条摘要", "",
         f"来源：https://news.ycombinator.com ｜ 前 {len(stories)} 条 ｜ "
         f"安装阶段 {install_s}s（{'缓存命中跳过' if skipped == '1' else '全新安装'}）", ""]
for i, s in enumerate(stories, 1):
    lines.append(f"{i}. **[{s['title']}]({s['url']})** — {s['score']} 分 / {s['comment_count']} 条评论")
    if s["first_comment"]:
        lines.append(f"   > 首条评论：{s['first_comment'][:150]}")
    lines.append("")
with open(os.path.join(OUT, "digest.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

top = stories[0] if stories else {}
with open(os.path.join(OUT, "_outputs.env"), "w", encoding="utf-8") as f:
    f.write(f"stories={len(stories)}\n")
    f.write(f"top_score={top.get('score', '0')}\n")
    f.write(f"top_comments={top.get('comment_count', 0)}\n")
    f.write(f"install_seconds={install_s}\n")
    f.write(f"install_skipped={skipped}\n")
    f.write("browse=ok\n")

print(f"完成：榜首「{top.get('title', '')[:50]}」（{top.get('score', '?')} 分）", flush=True)
