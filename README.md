# Photo Review v1.0

A Windows desktop app for comparing similar photos and choosing what to keep. Python + PySide6, packaged as a single EXE with Nuitka. Photo processing happens locally.

## Download

Get **PhotoReview.exe** from [GitHub Releases](https://github.com/zschmook/PhotoReview/releases). No Python or Docker installation is required. Keep your existing settings by replacing the EXE; persistent data lives under `%LOCALAPPDATA%/PhotoReview/data`.

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

The app makes one background request to [GitHub's latest-release API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release) when it opens. It offers **Download update** only for a newer stable version with a published PhotoReview.exe asset. That button opens GitHub's executable download. Close the app and replace the old EXE after downloading; settings stay in place.

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
git tag v1.0
git push origin main
git push origin v1.0
```

The **Windows release** GitHub Actions workflow tests generated-data operations, builds a Windows x64 single EXE, runs the packaged smoke test, and publishes PhotoReview.exe, SHA256SUMS.txt and release-manifest.json. It uses a draft until all assets are uploaded. A published version is never overwritten; use a new version/tag. Failed builds leave no published update. The workflow can also be run manually for an existing matching tag.

GPU execution is optional in the smoke test because hosted runners may lack a hardware GPU. CPU comparison, HEIC decoding/conversion, Qt startup, icon loading and Windows Shell interfaces remain checked. Generated CI diagnostics are stored as workflow artifacts.

## Tests

Focused release checks use disposable generated images and mocked phone operations:

```bash
.venv/Scripts/python.exe -m unittest tests.test_updates tests.test_release tests.test_core tests.test_heic tests.test_phones tests.test_phone_import tests.test_phone_download tests.test_phone_cleanup tests.test_phone_folders tests.test_usb_import tests.test_packaging tests.test_gpu.FallbackTests -q
```

Additional UI and video tests are available in tests/. Some older UI scenarios require optional local demo fixtures. Development test selection does not access physical phones or personal photo libraries. The packaged smoke check enumerates device identities and creates Shell interfaces without copying or deleting phone files.
