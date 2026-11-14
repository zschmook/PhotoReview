# Photo Review v1.0

Windows photo comparison and review, with Keep checked by default and explicit final acceptance before recycling local files.

- Folder-scoped similar-photo matching and a separate review mode for pictures and videos.
- Keyboard navigation, thumbnail selection, full-size previews, and video playback.
- iPhone/Android photo imports and optional video imports, with checksum-based incremental copying.
- HEIC/HEIF decoding and optional JPEG copies that retain original files.
- Phone-original cleanup only after two confirmations and fresh checksum verification.
- USB-drive importing and local SQLite caches for repeat use.
- One background GitHub release check when the app starts, with a download prompt for newer stable versions.

Download **PhotoReview.exe**. No Python or Docker installation is required. Settings and caches remain under `%LOCALAPPDATA%\PhotoReview\data` when replacing the EXE. The release includes `SHA256SUMS.txt` for the executable.

Phone import/cleanup depends on the device's Windows driver, unlock/trust state and exposed media. Keep originals backed up while evaluating the workflow. JPEG copies are separate review entries. Video playback depends on supported codecs. GPU matching is optional and falls back to CPU.
