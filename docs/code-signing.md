# Code signing policy and SignPath setup

## Status

Photo Review applied to SignPath Foundation’s free open-source signing program on November 14, 2026. The application form confirmed submission with “Form submitted. Thank you, we'll be in touch soon.” The application is awaiting review; approval has not been granted and no SignPath credentials have been configured. Existing releases are unsigned unless their Windows signature verifies successfully.

The project’s original code and assets use GPL-3.0-only; see [LICENSE](../LICENSE). Dependencies retain their own licenses. Public source and releases are available at [zschmook/PhotoReview](https://github.com/zschmook/PhotoReview).

## Project policy

- Author, contribution reviewer, and release/signing approver: [Zachary Schmook / zschmook](https://github.com/zschmook).
- Contributions from other authors are reviewed by the maintainer before inclusion in a release.
- Signing requests cover only the project’s `PhotoReview.exe`, built from its matching public version tag on a GitHub-hosted Windows runner.
- Product name must be `Photo Review`; product and file versions must match the version being built.
- Third-party binaries are not separately re-signed with the project’s signing identity.
- Before enabling production signing, the maintainer must confirm multi-factor authentication on GitHub and SignPath and configure manual approval for release-signing requests.
- Once signing is enabled, a rejected, failed, or timed-out request prevents publication. The workflow must not publish the unsigned fallback for that run.

The proposed provider attribution, **to be activated only after acceptance**, is: Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by [SignPath Foundation](https://signpath.org).

## Privacy

The application processes media locally and does not upload pictures, library paths, thumbnails, or import records. Its startup check sends a request to GitHub’s release API; the service receives ordinary connection information and the app version in the user agent. Choosing Update now downloads the manifest and EXE from GitHub release storage; Python/manual fallback opens the download in the browser. Once the installed app is signed, in-place updates require a valid signature from the same publisher. Windows uses its standard trust services for certificate validation. See the [README privacy section](../README.md#privacy) and [GitHub privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement).

If signing is enabled, the release workflow sends the public build artifact and source/build identifiers to SignPath for signing. User photo libraries and local development samples are excluded from the repository and release artifacts. See [SignPath’s privacy policy](https://signpath.io/privacy-policy).

## Submitted application

The application was submitted through the [SignPath Foundation application page](https://signpath.org/apply). The project information below describes the application; the submitted form and contact details are recorded privately in the local development notes. Optional marketing communications were left unchecked.

| Field | Value |
| --- | --- |
| Project | Photo Review / PhotoReview |
| Maintainer | Zachary Schmook, GitHub `zschmook` |
| Contact email | Supplied privately to SignPath by the maintainer; omitted from this public page. |
| Project homepage and source | https://github.com/zschmook/PhotoReview |
| Releases | https://github.com/zschmook/PhotoReview/releases |
| License | GNU GPL version 3 only, GPL-3.0-only |
| Build system | GitHub Actions, GitHub-hosted Windows x64 runner, Python/PySide6, Nuitka one-file EXE |
| Artifact requested for signing | `PhotoReview.exe` |
| Signing policy | This document and the README’s Code signing policy section |

Description:

> I originally built Photo Review for my family, especially relatives who find modern photo applications overwhelming because they have too many options. The interface is deliberately bare bones by default, focused on importing pictures, comparing similar shots, and choosing what to keep. Keeping that workflow approachable for people without technical experience is a long-term design goal. Printing, ordering prints through commercial third-party services, and similar integrations are outside the project's scope.
>
> Photo Review is a Windows desktop application for user-controlled photo culling and media import. It groups similar pictures within a folder, provides individual picture/video review, and imports photos and optional videos from Android phones, iPhones, and USB drives. HEIC files can be previewed and compared or converted to JPEG copies while retaining originals. Local removals use the Windows Recycle Bin after a final review. Optional phone-original removal requires two confirmations and fresh checksum verification. Media processing and caches stay on the PC. The app is distributed as a single EXE with public tagged source and automated generated-data tests.

The project is new. Do not claim an established user base, independent reviews, verified account security, or SignPath acceptance. SignPath considers public reputation as well as the technical requirements; acceptance is discretionary.

## Release integration

The Windows release workflow contains an opt-in signing stage. **It is disabled while `SIGNPATH_ENABLED` is unset.** No account settings or repository secrets are created by committing the workflow.

After provider approval and account setup, the repository owner can configure these GitHub Actions values using the actual values issued/configured in SignPath:

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `SIGNPATH_API_TOKEN` | Token with submission permission for this project and release policy |
| Variable | `SIGNPATH_ORGANIZATION_ID` | SignPath organization ID |
| Variable | `SIGNPATH_PROJECT_SLUG` | Project slug configured for Photo Review |
| Variable | `SIGNPATH_SIGNING_POLICY_SLUG` | Production policy requiring manual approval |
| Variable | `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | Artifact configuration matching `signpath-artifact.xml` |
| Variable | `SIGNPATH_ENABLED` | Set to `true` last, after the other setup is complete |

The SignPath account must link this repository and its trusted GitHub build system. Review any requested GitHub App access before granting it. Use the [artifact configuration template](signpath-artifact.xml) to restrict signing to the EXE’s name, product, and version. The workflow uploads a ZIP artifact containing only that EXE, so the configuration’s root file type is ZIP.

With signing enabled, the release sequence is:

1. Validate the version and signing settings, install dependencies, and run tests.
2. Compile the EXE and run the packaged smoke test.
3. Upload the unsigned EXE as a GitHub workflow artifact and submit its artifact ID to SignPath.
4. Wait for the maintainer’s manual approval in SignPath, for up to one hour. This approval is in SignPath, **not** GitHub’s Publish release button.
5. Download the signed EXE, validate its Windows signature and product/version metadata, and replace the unsigned staging copy.
6. Compute release checksums from the signed bytes and publish the completed GitHub release.

Published version tags/releases are never overwritten. After setup, use a new version tag for the first signed release. Existing tag builds use the workflow committed at that tag and do not acquire these changes retroactively.

Before the first signed release, review bundled dependency notices and source availability, confirm the account security and signing policy, replace the pending attribution/status on the README and this page with the approved status, and perform a production-signature test. Signing identifies the publisher and artifact origin; it does not guarantee that every Windows reputation warning disappears immediately.

## Removing the application

Close Photo Review and delete `PhotoReview.exe`. Imported media remains in the chosen library. Settings and caches can be removed separately from `%LOCALAPPDATA%\PhotoReview\data`; doing so resets app state and import history, not the media library. Nuitka’s extracted runtime cache is separate from user media and may also remain under the Windows application cache directory.

## References

- [SignPath Foundation eligibility and requirements](https://signpath.org/terms)
- [SignPath GitHub integration](https://docs.signpath.io/trusted-build-systems/github)
- [Artifact metadata restrictions](https://docs.signpath.io/artifact-configuration/reference)
- [Microsoft SmartScreen reputation](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation)
