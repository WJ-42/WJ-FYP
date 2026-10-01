#!/usr/bin/env bash
# Render the office sim headlessly and save a PNG.
#
# Usage: tools/capture.sh <output.png> [query-string]
#   tools/capture.sh out.png
#   tools/capture.sh pod.png "focus=-8,3&zoom=3"
#
# Two things here are deliberate and worth not "simplifying" away:
#
#  1. --user-data-dir points at a throwaway profile. Without it Chromium
#     attaches to the user's already-running browser instead of starting a
#     headless instance, and the capture hangs forever.
#  2. The image comes from the page's own canvas.toDataURL() (the ?still=1
#     path), not from --screenshot. A one-shot WebGL render is not reliably
#     present in the compositor when --screenshot fires, so that route
#     silently produces a blank frame.

set -euo pipefail

OUT="${1:?usage: capture.sh <output.png> [query-string]}"
QUERY="${2:-}"
PORT="${PORT:-8731}"
SIZE="${SIZE:-1600,1000}"

URL="http://localhost:${PORT}/index.html?still=1"
[ -n "$QUERY" ] && URL="${URL}&${QUERY}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if ! curl -sf -o /dev/null "http://localhost:${PORT}/index.html"; then
  echo "No server on port ${PORT}. Start one with:" >&2
  echo "  python3 -m http.server ${PORT} --directory office-sim" >&2
  exit 1
fi

timeout 180 chromium \
  --headless=new \
  --user-data-dir="${WORK}/profile" \
  --no-first-run \
  --disable-gpu \
  --use-gl=angle \
  --use-angle=swiftshader \
  --enable-unsafe-swiftshader \
  --hide-scrollbars \
  --window-size="${SIZE}" \
  --virtual-time-budget=30000 \
  --dump-dom "$URL" > "${WORK}/dom.html" 2>"${WORK}/console.log"

python3 - "$WORK/dom.html" "$OUT" <<'PY'
import base64, html, re, sys
dom_path, out_path = sys.argv[1], sys.argv[2]
dom = open(dom_path, encoding='utf-8', errors='replace').read()

# The element carries a style attribute too, so the id cannot be assumed to be
# the last thing before the '>'.
def diagnostics():
    m = re.search(r'id="diag"[^>]*>(.*?)</pre>', dom, re.S)
    return html.unescape(m.group(1)) if m else None

m = re.search(r'data-png="data:image/png;base64,([^"]+)"', dom)
if not m:
    diag = diagnostics()
    print("No frame captured.", "Diagnostics:" if diag else "", diag or "", file=sys.stderr)
    sys.exit(1)

open(out_path, 'wb').write(base64.b64decode(m.group(1)))

print(f"wrote {out_path}")
diag = diagnostics()
if diag:
    print(diag)
PY

grep -iE "CONSOLE.*(error|uncaught)" "${WORK}/console.log" >&2 || true
