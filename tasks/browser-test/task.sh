#!/usr/bin/env bash
# browser-test —— 验证 runner 上的浏览器自动化能力。
# 用预装的 headless Chrome 访问真实网页：截图 + 提取标题 + DOM 大小。
set -uo pipefail

echo "== browser-test =="

# 探测可用浏览器（ubuntu runner 预装 chrome / firefox）
CHROME=""
for c in google-chrome google-chrome-stable chromium chromium-browser; do
  if command -v "$c" >/dev/null 2>&1; then CHROME=$c; break; fi
done
if [ -z "$CHROME" ]; then echo "::error::没有可用浏览器"; exit 1; fi
ver=$("$CHROME" --version 2>/dev/null || echo unknown)
echo "浏览器: $CHROME ($ver)"

mkdir -p "$GHA_OUTPUT_DIR"

# 访问真实网页：截图 + dump DOM
"$CHROME" --headless=new --disable-gpu --no-first-run \
  --window-size=1280,800 \
  --screenshot="$GHA_OUTPUT_DIR/page.png" \
  "https://example.com" 2>&1 | tail -2

"$CHROME" --headless=new --disable-gpu --no-first-run \
  --dump-dom "https://example.com" > "$GHA_OUTPUT_DIR/page.html" 2>/dev/null

title=$(grep -oE '<title>[^<]*</title>' "$GHA_OUTPUT_DIR/page.html" | head -1 | sed 's/<[^>]*>//g')
dom_bytes=$(wc -c < "$GHA_OUTPUT_DIR/page.html" | tr -d ' ')
png_bytes=$(wc -c < "$GHA_OUTPUT_DIR/page.png" 2>/dev/null | tr -d ' ' || echo 0)

echo "页面标题: $title"
echo "DOM: ${dom_bytes} 字节, 截图: ${png_bytes} 字节"

{
  echo "browser=$ver"
  echo "page_title=$title"
  echo "dom_bytes=$dom_bytes"
  echo "screenshot_bytes=$png_bytes"
  echo "browser_test=ok"
} > "$GHA_OUTPUT_DIR/_outputs.env"

[ "$png_bytes" -gt 1000 ] || { echo "截图异常（太小）" >&2; exit 1; }
echo "== 完成 =="
