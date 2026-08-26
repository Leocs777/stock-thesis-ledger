# App Store privacy and TestFlight metadata

Status: v0.1.6 internal-test preparation. This is an engineering checklist, not
legal advice. Re-evaluate the answers whenever deployment ownership or data flow
changes.

## Reference-build data boundary

The public iOS binary connects only to the Server URL chosen by the user. The
open-source maintainer does not operate a hosted service and cannot access that
server's account, research, portfolio, Paper, device, or sync records. The app
stores its server URL, language, and notification preferences in UserDefaults;
the bearer token is stored in the iOS Keychain.

`PrivacyInfo.xcprivacy` declares no tracking, no tracking domains, and the
`CA92.1` required-reason declaration for app-only UserDefaults access. The
Info.plist declares that the build does not use non-exempt encryption; the app
uses Apple's networking and Keychain APIs rather than implementing its own
cryptography.

## App Store Connect decision table

| Deployment | Operator access | App privacy answer to review |
| --- | --- | --- |
| Personal internal TestFlight using a server controlled only by the same user | The developer/maintainer does not receive tester data | The current no-developer-collection reference boundary may apply |
| Internal team test on a server the developer administers | The developer can access account and research records | Disclose the data actually retained, including contact info, user content, identifiers, and financial/research records as applicable |
| Public or external beta | Depends on hosting, analytics, crash reporting, email, and support tooling | Perform a fresh data-flow inventory and update both labels and policy before adding testers |

Do not copy the first row to a hosted beta. Apple's definition depends on who
can access transmitted data, how long it is retained, and why it is used—not on
whether the product is called local-first.

## URLs and in-app access

- English privacy: `https://leocs777.github.io/stock-thesis-ledger/privacy/`
- Chinese privacy: `https://leocs777.github.io/stock-thesis-ledger/zh-CN/privacy/`
- English support: `https://leocs777.github.io/stock-thesis-ledger/support/`
- Chinese support: `https://leocs777.github.io/stock-thesis-ledger/zh-CN/support/`

Settings links to the English privacy and support pages. Both pages link to the
localized versions. Verify the URLs after GitHub Pages deployment and before
uploading a build.

## Phase-1 checklist

1. Run `scripts/check-testflight-readiness.sh`.
2. Confirm the selected Apple team, bundle ID, version, and build number.
3. Archive locally with `scripts/archive-testflight.sh`.
4. Create the App Store Connect record and paste the checked-in `ios/metadata/`
   copy into both localizations.
5. Complete App Privacy from the actual test-server data flow.
6. Put temporary review Server URL and test credentials only in App Store
   Connect review fields, never in Git.
7. Upload only after a separate explicit approval.

## Change triggers

Repeat the privacy inventory before adding analytics, crash reporting, APNs
provider delivery, a hosted server, customer support ingestion, advertising,
third-party SDKs, or real brokerage connectivity. Update the manifest, labels,
policy, retention plan, deletion behavior, and reviewer notes together.
