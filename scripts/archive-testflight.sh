#!/bin/zsh
set -euo pipefail

project_root="${0:A:h:h}"
archive_root="$project_root/build/testflight"
archive_path="$archive_root/InvestorLab.xcarchive"
export_path="$archive_root/export"

mkdir -p "$archive_root"
zsh "$project_root/scripts/check-testflight-readiness.sh"
xcodebuild \
  -project "$project_root/ios/InvestorLab.xcodeproj" \
  -scheme InvestorLab \
  -configuration Release \
  -destination "generic/platform=iOS" \
  -archivePath "$archive_path" \
  -allowProvisioningUpdates \
  archive

if [[ "${INVESTORLAB_EXPORT_IPA:-0}" == "1" ]]; then
  xcodebuild \
    -exportArchive \
    -archivePath "$archive_path" \
    -exportPath "$export_path" \
    -exportOptionsPlist "$project_root/ios/ExportOptions.plist" \
    -allowProvisioningUpdates
fi

print "Archive ready: $archive_path"
if [[ "${INVESTORLAB_EXPORT_IPA:-0}" == "1" ]]; then
  print "Export ready: $export_path"
fi
