# Photo Review v1.2

A Windows desktop app for sorting a camera roll, comparing similar shots, and choosing which pictures and videos to keep. Import from an Android phone, iPhone, or USB drive, then review the files in your own library.

**You choose what goes. Checked always means Keep; unchecked means Recycle.** Matching suggests similar pictures without automatically choosing a winner. Keep one, several, all, or none of the pictures in a set.

## Built for family

Photo Review began as a tool for my family, especially relatives who find modern photo applications overwhelming because they have too many options. The interface is deliberately bare bones by default: import your pictures, compare similar shots, and choose what to keep. Keeping that workflow approachable for people without technical experience is a long-term design goal.

Printing, ordering prints through commercial third-party services, and similar integrations are outside the project's scope.

## Features

| Feature | What it does |
| --- | --- |
| Compare similar pictures | Finds similar shots within the same folder using perceptual hashing and structural similarity. Adjust similarity from 60–100% and the capture-time window from 1 minute to 30 days. |
| Review pictures and videos | Browse individual files without running comparisons. Videos play in the preview area and are never compared as still images. |
| Fast, familiar navigation | Windows menus, a folder tree, thumbnails, image previews, and keyboard shortcuts. Selections stay saved as you move between folders. |
| Final review before recycling | Expand a row to preview a proposed removal, change its Keep checkbox, and see the space to recover before accepting. Local removals use the Windows Recycle Bin. |
| Phone imports | Detect connected Android phones and iPhones, wait for unlock/USB access, and optionally include videos. Progress separates discovery, files already on the PC, and new downloads, with transfer speed. |
| Incremental importing | Compare file content with checksums and retain local import records so repeat imports copy missing files instead of creating another copy of everything. |
| Organized destinations | Keep imports inside the selected library, with separate phone folders and simple Year/Month/Day, Year/Month, or date-folder layouts. Videos go into the phone’s Videos folder. |
| USB-drive imports | Detect a thumb drive and search all ordinary folders for supported media. USB originals are copied and left in place. |
| iPhone photo formats | Preview and compare HEIC/HEIF files directly. Create JPEG copies while retaining originals, including a conversion command for existing folders. |
| Optional phone cleanup | After importing, use a separate two-confirmation flow with fresh checksum verification before removing phone originals. |
| Repeat-use performance | Local SQLite caches and thumbnails speed up later reviews. Optional cross-vendor GPU comparison falls back to CPU when unavailable. |
| Startup update checks | Check GitHub once when opening the app, display release notes, and offer Update now or Skip. |

Photo Review runs locally, requires no account for reviewing files, and is available as a single Windows x64 EXE. It is written in Python with PySide6 and packaged with Nuitka.

## Download

Get **PhotoReview.exe** from [GitHub Releases](https://github.com/zschmook/PhotoReview/releases). No Python or Docker installation is required. Keep your existing settings by replacing the EXE; persistent data lives under `%LOCALAPPDATA%/PhotoReview/data`.

1. Open the app and choose your picture library. It remembers your choice.
2. Select **Compare** to find similar pictures, or **Review** to browse pictures and videos individually.
3. Leave files checked to keep them. Uncheck files you want to recycle.
4. Choose **Review changes**, check the previews and totals, and explicitly accept the removals.

The app does not reorganize existing library files. Import folder layouts apply to newly imported files.

## Review pictures

Choose a library folder at first launch; the app remembers it. Compare mode groups similar photos within each immediate folder, using capture time (or modified time as fallback), perceptual hashing and structural similarity. The time window and minimum similarity are adjustable. Similarity scores suggest candidates; they do not decide what to delete.

Every photo starts checked. **Checked means Keep; unchecked means Recycle.** The final review shows unchecked files with expandable previews and Keep controls. Only explicit final acceptance moves local files to the Windows Recycle Bin. Files are validated again before recycling. Network and removable sources are review-only for local recycling.

Review mode browses individual pictures and videos without matching. JPEG, PNG, WebP, BMP, TIFF and HEIC/HEIF pictures are supported. Videos have an embedded player and are not compared as images.

- Left/Right: images within a matching set.
- Up/Down: previous/next matching set.
- Space: toggle Keep.
- Thumbnails and preview arrows select images; lower navigation moves between sets.

## Phone and USB imports

Enable phone detection, unlock the phone, and confirm readiness before scanning. Android uses DCIM, Pictures, Download, Documents and Movies; iPhones scan all folders exposed by Windows photo access, including date folders directly under Internal Storage. Thumbnail folders are skipped. USB drives search all ordinary folders, skipping links and system metadata.

Imports compare checksums and copy only missing content. Pictures go inside the selected library; phone imports use a phone folder and configurable date layout. Videos are optional and go into a Videos folder. Files on a USB drive are copy-only.

HEIC originals can be reviewed directly. New imports also create JPEG copies beside HEIC files while retaining the originals. For existing albums, choose **File → Convert HEIC to JPEG copies…**. Both formats remain separate review entries.

Phone-original deletion is a separate action requiring two confirmations. After the first confirmation, every downloaded file is checksum-verified against its phone original. A final confirmation precedes removal, and the backend rechecks copies and originals before each removal. Phone deletion may be permanent and does not use the Windows Recycle Bin. Originals with uncopied movie/edit companions stay protected. Device-driver behavior varies; keep backups while evaluating this workflow.

## Startup updates

The app makes one background request to [GitHub's latest-release API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release) when it opens. For a newer stable version with a published PhotoReview.exe asset, it shows the installed/new versions and the GitHub release notes. **Update now** opens GitHub's executable download. Close the app and replace the old EXE after downloading; settings stay in place. **Skip** dismisses the prompt until the next launch.

There is no periodic network polling. A pending notice waits for the first-run folder choice and active modal work. Offline failures, rate limits and absent releases remain quiet. No photos or file paths are sent with the update request. The app does not silently install or replace itself.

## Run from Python

Python 3.12+, from Git Bash:

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
./run.sh
```

Optional cross-vendor GPU matching: install `wgpu==0.33.0`. CPU fallback remains available. `./run.sh --reset` archives app settings/cache and starts the folder wizard again; original photos are untouched. Private sample folders and diagnostic captures are not included in this repository.

## Build locally

```bash
.venv/Scripts/python.exe -m pip install -r requirements-build.txt
./build.sh
```

The single EXE is published to `dist/PhotoReview.exe`. Intermediate output stays under `.build/`; extraction caches use a fresh identity per build. The script optionally accepts a comma-separated list of folders to copy the finished EXE into. Local builds do not publish GitHub releases.

## Publish a release

The version lives in **photo_review/version.py**. Package metadata and Windows EXE resources derive from it. Update that version and RELEASE_NOTES.md for the next release, commit, then push a matching version tag:

```bash
git tag v1.2
git push origin main
git push origin v1.2
```

The **Windows release** GitHub Actions workflow tests generated-data operations, builds a Windows x64 single EXE, runs the packaged smoke test, and publishes PhotoReview.exe, SHA256SUMS.txt and release-manifest.json. It uses a draft until all assets are uploaded. A published version is never overwritten; use a new version/tag. Failed builds leave no published update. The workflow can also be run manually for an existing matching tag.

The optional SignPath stage is prepared but disabled pending approval/account setup. When enabled, it waits for signing approval, validates the signed EXE, and computes release checksums from that signed copy before publication. See [Code signing policy](docs/code-signing.md).

The tested EXE is also saved as a GitHub workflow artifact for seven days before publication, so a publishing failure can be recovered without compiling again. Publishing verifies that the remote version tag still matches the tested checkout; it does not create or move version tags.

GPU execution is optional in the smoke test because hosted runners may lack a hardware GPU. CPU comparison, HEIC decoding/conversion, Qt startup, icon loading and Windows Shell interfaces remain checked. Generated CI diagnostics are stored as workflow artifacts.

## Tests

Focused release checks use disposable generated images and mocked phone operations:

```bash
.venv/Scripts/python.exe -m unittest tests.test_updates tests.test_release tests.test_core tests.test_heic tests.test_phones tests.test_phone_import tests.test_phone_download tests.test_phone_cleanup tests.test_phone_folders tests.test_usb_import tests.test_packaging tests.test_gpu.FallbackTests -q
```

Additional UI and video tests are available in tests/. Some older UI scenarios require optional local demo fixtures. Development test selection does not access physical phones or personal photo libraries. The packaged smoke check enumerates device identities and creates Shell interfaces without copying or deleting phone files.

## Privacy

Photo comparison, thumbnails, import history, and checksums are processed and stored on the PC. Photo Review does not upload your media or collect analytics. A startup update check connects to GitHub; GitHub receives the connection’s IP address and the app version in its user agent, but no pictures or library paths. Choosing **Update now** opens a GitHub download in your browser. See [GitHub’s privacy statement](https://docs.github.com/en/site-policy/privacy-policies/github-general-privacy-statement) for that service.

## Code signing policy

SignPath Foundation signing is being prepared. The project has not yet been approved, and releases must be treated as unsigned unless their Windows signature verifies successfully. No signing sponsorship is claimed at this stage.

The maintainer, [zschmook](https://github.com/zschmook), is responsible for authoring, reviewing contributions, and approving releases. Signing is intended to cover only Photo Review’s own EXE built from the matching public source tag on GitHub-hosted runners. Third-party components retain their own licenses and publisher identities.

See the [SignPath setup and application notes](docs/code-signing.md) for the proposed signing flow and remaining setup.

## License

Photo Review’s original source code and project-created assets are Copyright (C) 2026 Zachary Schmook and licensed under the **GNU General Public License, version 3 only (GPL-3.0-only)**. See [LICENSE](LICENSE). The program is provided without warranty. Dependencies remain under their respective licenses.
