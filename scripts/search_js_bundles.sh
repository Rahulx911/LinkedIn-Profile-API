#!/bin/bash
# Downloads the public (no-login, no-cookie) JS bundle files referenced by the
# profile page HTML, plus one level of their dynamically-imported sub-chunks,
# and greps the whole set for strings related to the Experience lazy-load
# mechanism. These are static assets from static.licdn.com — no LinkedIn
# account/cookie involved, just public frontend code.
#
# Usage: ./scripts/search_js_bundles.sh <path-to-saved-profile-html>
# e.g.:  ./scripts/search_js_bundles.sh debug_output/rahul911.html

set -e

HTML_FILE="${1:?Usage: $0 <path-to-saved-profile-html>}"
OUT_DIR="debug_output/js_bundles"
mkdir -p "$OUT_DIR"

echo "Extracting script URLs from $HTML_FILE..."
grep -o '<script[^>]*src="[^"]*"' "$HTML_FILE" | grep -o 'src="[^"]*"' | sed 's/src="//;s/"$//' | sort -u > "$OUT_DIR/urls.txt"

echo "Downloading $(wc -l < "$OUT_DIR/urls.txt") top-level bundle files..."
while read -r url; do
  fname=$(basename "$url")
  [ -f "$OUT_DIR/$fname" ] || curl -s -A "Mozilla/5.0" -o "$OUT_DIR/$fname" "$url"
done < "$OUT_DIR/urls.txt"

echo "Finding dynamically-imported sub-chunks referenced from those bundles..."
grep -oh 'import("\.\/[A-Za-z0-9_-]*\.js")' "$OUT_DIR"/*.js 2>/dev/null | grep -o '\./[A-Za-z0-9_-]*\.js' | sed 's/^\.\///' | sort -u > "$OUT_DIR/subchunks.txt"
echo "Found $(wc -l < "$OUT_DIR/subchunks.txt") sub-chunk references. Downloading..."
while read -r fname; do
  [ -f "$OUT_DIR/$fname" ] && continue
  curl -s -A "Mozilla/5.0" -o "$OUT_DIR/$fname" "https://static.licdn.com/aero-v1/sc/h/assets/$fname"
done < "$OUT_DIR/subchunks.txt"

echo ""
echo "Total files now in $OUT_DIR: $(ls "$OUT_DIR"/*.js 2>/dev/null | wc -l)"
echo ""
echo "--- all voyager*Dash* query names ---"
grep -oh 'voyager[A-Za-z]*Dash[A-Za-z]*' "$OUT_DIR"/*.js 2>/dev/null | sort -u

echo ""
echo "--- any /sdui or sdui/ path-like strings ---"
grep -oh 'sdui[A-Za-z/_-]\{3,40\}' "$OUT_DIR"/*.js 2>/dev/null | sort -u | head -30

echo ""
echo "--- any voyager/api path strings ---"
grep -ohr 'voyager/api/[a-zA-Z0-9/_-]*' "$OUT_DIR"/*.js 2>/dev/null | sort -u | head -30

echo ""
echo "--- literal 'lazy_anchor' or 'lazy-load' string occurrences (connects to the HTML componentkey) ---"
grep -oh '.\{60\}lazy[_-]\(anchor\|load\).\{60\}' "$OUT_DIR"/*.js 2>/dev/null | head -10

echo ""
echo "Done."
