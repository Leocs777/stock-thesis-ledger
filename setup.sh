#!/usr/bin/env sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$project_dir"

if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "Error: Python 3.10 or newer is required." >&2
    exit 1
fi

python3 -c 'import sys; sys.exit("Error: Python 3.10 or newer is required.") if sys.version_info < (3, 10) else None'

mkdir -p data

python3 -m py_compile app.py test_app.py test_paper_validation.py scripts/check-local-links.py scripts/check_app_store_metadata.py scripts/paper_validation.py
python3 scripts/check-local-links.py

if command -v zsh >/dev/null 2>&1; then
    zsh -n setup.sh scripts/archive-testflight.sh scripts/check-testflight-readiness.sh scripts/reload-local-service.sh
fi

if command -v plutil >/dev/null 2>&1; then
    plutil -lint ios/InvestorLab/Info.plist ios/InvestorLab/PrivacyInfo.xcprivacy ios/ExportOptions.plist scripts/org.investorlab.server.plist scripts/org.investorlab.tunnel.plist
fi

printf '\n%s\n' "Investor Lab bootstrap checks passed."
printf '%s\n' "No external dependency was installed."
printf '%s\n' "Start the local server with:"
printf '  %s\n' "python3 app.py"
printf '%s\n' "Then open http://127.0.0.1:8000"
printf '\n%s\n' "The app reads process environment variables directly."
printf '%s\n' "See .env.example and README.md; setup.sh does not load .env."
