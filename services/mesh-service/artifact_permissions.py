"""Permissions for immutable artifacts written through the shared Docker volume."""

from __future__ import annotations

import os
from pathlib import Path


def publish_shared_artifact_permissions(root: Path) -> None:
    """Make a container-owned artifact readable and removable by the host group."""
    raw_gid = os.getenv(
        "ARTIFACT_OUTPUT_GID",
        os.getenv("C2M_OUTPUT_GID", str(os.getgid())),
    ).strip()
    try:
        output_gid = int(raw_gid)
    except ValueError as exc:
        raise RuntimeError("ARTIFACT_OUTPUT_GID must be a non-negative integer") from exc
    if output_gid < 0:
        raise RuntimeError("ARTIFACT_OUTPUT_GID must be a non-negative integer")

    for artifact in (root, *root.rglob("*")):
        os.chown(artifact, -1, output_gid, follow_symlinks=False)
        artifact.chmod(0o2770 if artifact.is_dir() else 0o660)
