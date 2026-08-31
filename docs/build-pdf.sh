#!/usr/bin/env bash
# Перегенерувати docs/pamyatka.pdf з docs/pamyatka.html.
# Потрібен Chrome — інших залежностей немає.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
[ -x "$CHROME" ] || CHROME="$(command -v google-chrome || command -v chromium)"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# HTML у репозиторії — фрагмент: обгортку додає хостинг артефактів.
# Для друку дописуємо каркас, кодування і світлу тему: на папері
# темна дала б білий текст на білому.
{
  printf '%s\n' '<!doctype html>' '<html lang="uk" data-theme="light">' '<head>' \
    '<meta charset="utf-8">' \
    '<meta name="viewport" content="width=device-width, initial-scale=1">' \
    '<style>* { -webkit-print-color-adjust: exact; print-color-adjust: exact; }' \
    'html, body { margin: 0; padding: 0; }</style>'
  cat "$HERE/pamyatka.html"
  printf '%s\n' '</html>'
} > "$TMP/print.html"

"$CHROME" --headless --disable-gpu --no-sandbox \
  --virtual-time-budget=8000 --print-to-pdf-no-header \
  --print-to-pdf="$HERE/pamyatka.pdf" "file://$TMP/print.html" 2>/dev/null

echo "✔ $HERE/pamyatka.pdf"
