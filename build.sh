#!/bin/sh
set -eu
# Publish only the reviewed public files. Admin/server files and backups stay out.
rm -rf dist
mkdir -p dist/assets
for page in index services polishing protection wrapping interior works studio contact prices faq; do
  cp "$page.html" dist/
done
cp style.css app.js content.js content-default.json dist/
cp assets/logo-transparent.png assets/noir.png assets/studio.jpg dist/assets/
