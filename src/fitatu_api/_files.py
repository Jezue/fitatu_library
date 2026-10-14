"""Private file writes: the diary export of ``fitatu-api export`` and of the facade."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_private_file(path: str | Path, content: bytes, *, overwrite: bool = False) -> None:
    """Write ``content`` to ``path`` as a new file readable by its owner only (0600).

    An existing file (or symlink) at ``path`` raises ``FileExistsError`` unless ``overwrite``.
    """
    target = Path(path)
    # Swapping in a fresh 0600 file keeps no old file's mode, follows no symlink
    # and never leaves half an export behind.
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        if overwrite:
            os.replace(tmp, target)
        else:
            os.link(tmp, target)  # fails when target exists, like O_EXCL
    finally:
        Path(tmp).unlink(missing_ok=True)
