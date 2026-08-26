#!/bin/zsh
set -euo pipefail

project_root="${0:A:h:h}"
info_plist="$project_root/ios/InvestorLab/Info.plist"
privacy_manifest="$project_root/ios/InvestorLab/PrivacyInfo.xcprivacy"
project_file="$project_root/ios/InvestorLab.xcodeproj/project.pbxproj"

python3 "$project_root/scripts/check_app_store_metadata.py"
plutil -lint "$info_plist" "$privacy_manifest" "$project_root/ios/ExportOptions.plist"

if ! grep -q "PrivacyInfo.xcprivacy in Resources" "$project_file"; then
  print -u2 "PrivacyInfo.xcprivacy is not included in the iOS Resources phase."
  exit 1
fi

encryption=$(/usr/libexec/PlistBuddy -c "Print :ITSAppUsesNonExemptEncryption" "$info_plist")
if [[ "$encryption" != "false" ]]; then
  print -u2 "Review export-compliance settings: ITSAppUsesNonExemptEncryption is not false."
  exit 1
fi

marketing_version=$(sed -n 's/.*MARKETING_VERSION = \([^;]*\);/\1/p' "$project_file" | head -n 1)
build_number=$(sed -n 's/.*CURRENT_PROJECT_VERSION = \([^;]*\);/\1/p' "$project_file" | head -n 1)
if [[ -z "$marketing_version" || -z "$build_number" ]]; then
  print -u2 "The marketing version or build number is missing."
  exit 1
fi

print "TestFlight package checks: OK"
print "Version: $marketing_version ($build_number)"
print "Archive remains local until you explicitly approve an App Store Connect upload."
