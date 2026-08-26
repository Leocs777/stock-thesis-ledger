#!/usr/bin/env sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
site_root="$project_root/_site"

rm -rf "$site_root"
mkdir -p "$site_root/assets"
cp -R "$project_root/site/." "$site_root/"
cp "$project_root/web/investor-lab-logo.png" "$site_root/assets/investor-lab-logo.png"
cp "$project_root/artifacts/github-hero-v1.png" "$site_root/assets/github-hero-v1.png"
cp "$project_root/artifacts/web-overview-preview.jpg" "$site_root/assets/web-overview-preview.jpg"
cp "$project_root/artifacts/web-day-trade-preview.jpg" "$site_root/assets/web-day-trade-preview.jpg"
cp "$project_root/artifacts/web-options-preview.jpg" "$site_root/assets/web-options-preview.jpg"
touch "$site_root/.nojekyll"

printf '%s\n' "GitHub Pages artifact ready: $site_root"
