"""Shared exclusions for preview-cache directories, never original photos."""

THUMBNAIL_DIRECTORIES=frozenset({'thumbnails','.thumbnails'})


def is_thumbnail_directory(name):return str(name).casefold() in THUMBNAIL_DIRECTORIES
