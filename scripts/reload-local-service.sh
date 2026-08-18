#!/bin/zsh
set -euo pipefail

service_label="${INVESTORLAB_SERVICE_LABEL:-org.investorlab.server}"
service_domain="gui/$(id -u)"
plist_path="${INVESTORLAB_LAUNCH_AGENT_PLIST:-${HOME}/Library/LaunchAgents/org.investorlab.server.plist}"

if launchctl print "$service_domain/$service_label" >/dev/null 2>&1; then
  launchctl bootout "$service_domain/$service_label"
fi

launchctl bootstrap "$service_domain" "$plist_path"
launchctl print "$service_domain/$service_label" | head -24

for attempt in {1..15}; do
  if health_response="$(curl --fail --silent --show-error http://127.0.0.1:8000/api/health 2>/dev/null)"; then
    print -r -- "$health_response"
    exit 0
  fi
  sleep 1
done

print -u2 "Investor Lab did not become healthy within 15 seconds."
exit 1
