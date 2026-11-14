# Photo Review v1.3

- Portable EXEs can now update in place. Choose "Update now" to download and verify the new release, save your review, and restart at the same EXE location.
- Downloads show progress and transfer speed, and can be cancelled before installation.
- Release size and SHA-256 checksums are verified before closing the app. Once the installed EXE is signed, updates also require a valid signature from the same publisher.
- A temporary helper waits for PhotoReview to exit before replacing its EXE. Replacement or launch failures restore the previous EXE; a previous copy is retained in the app’s update cache.
- Pictures, settings, and saved selections stay in place, and the current review reopens after updating.

Versions through v1.2 need one final manual download/replacement to install this updater. After that, use the in-app update button for future releases. A writable application folder is required; the updater does not request administrator access.

Update checks still happen only when opening the app. "Skip" dismisses the prompt until the next launch. No Python, Docker, or separate installed updater is required.
