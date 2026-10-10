import re
from typing import Optional


INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_filename(value: Optional[str], fallback: str = "video.bin") -> str:
    """Return a cross-platform-safe basename while preserving useful Unicode."""
    basename = (value or "").replace("\\", "/").rsplit("/", 1)[-1]
    basename = INVALID_FILENAME_CHARS.sub("_", basename).strip(" .")
    if not basename:
        return fallback

    basename = basename[:200].rstrip(" .") or fallback
    stem = basename.split(".", 1)[0].upper()
    if stem in WINDOWS_RESERVED_NAMES:
        basename = f"_{basename}"
    return basename
