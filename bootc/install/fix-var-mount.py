#!/usr/bin/env -S uv run --script
# SPDX-License-Identifier: MPL-2.0
# pyright: reportAny = false
#
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///

"""Seed a dedicated /var partition after a bootc install.

`bootc install to-filesystem` writes /var into the root filesystem and errors
out if a partition mounted there, so a separate /var has to be copied across
afterwards. See https://github.com/bootc-dev/bootc/issues/997.

Run as root on the installer host, with the installed root still mounted:

  ./fix-var-mount.py /mnt /dev/sda4

Mounting it at boot is a separate concern, handled by a karg set at install
time: `systemd.mount-extra=UUID=<var-uuid>:/var:ext4`.
"""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _run(*cmd: str) -> None:
    """Echo a command, then run it, failing the script if it fails."""
    print("[CMD]>", shlex.join(cmd), file=sys.stderr)
    try:
        _ = subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as err:
        print(f"Error: failed to run `{shlex.join(cmd)}`", file=sys.stderr)
        sys.exit(err.returncode)


def _selinux_enabled() -> bool:
    """Whether the installer host has SELinux active."""
    return Path("/sys/fs/selinux/enforce").exists()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./fix-var-mount.py",
        description=(__doc__ or "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _ = parser.add_argument(
        "mounted_at", help="path the freshly installed root is mounted at"
    )
    _ = parser.add_argument(
        "var_device", help="partition to become /var, e.g. /dev/sda4"
    )
    _ = parser.add_argument(
        "--relabel",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="restorecon the copy; skipped when SELinux is off (default: enabled)",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()

    if os.geteuid() != 0:
        sys.exit("Error: must run as root; mounting and copying /var needs it.")

    source = Path(args.mounted_at).resolve() / "var"
    if not source.is_dir():
        sys.exit(f"Error: {source} is not a directory. Is the target root mounted?")

    device = Path(args.var_device)
    if not device.is_block_device():
        sys.exit(f"Error: {device} is not a block device.")

    for tool in ("mount", "umount", "rsync"):
        if shutil.which(tool) is None:
            sys.exit(f"Error: {tool} not found on PATH.")

    # rmdir at the end rather than a recursive delete: it refuses to run unless
    # the directory is empty, so a failed umount cannot cost us the partition.
    staged = Path(tempfile.mkdtemp(prefix="fix-var-mount-"))
    try:
        _run("mount", str(device), str(staged))
        try:
            if any(staged.iterdir()):
                print(
                    f"Warning: {device} is not empty; rsync will merge into what is already there.",
                    file=sys.stderr,
                )

            # -X carries security.selinux across, so the copy arrives labelled.
            _run("rsync", "-aHAX", f"{source}/", f"{staged}/")

            if args.relabel and _selinux_enabled():
                if shutil.which("restorecon") is None:
                    print(
                        "Warning: SELinux is on but restorecon is missing; keeping the labels rsync copied.",
                        file=sys.stderr,
                    )
                else:
                    _run("restorecon", "-R", str(staged))
        finally:
            _run("umount", str(staged))
    finally:
        try:
            staged.rmdir()
        except OSError as err:
            print(f"Warning: leaving {staged} in place ({err}).", file=sys.stderr)

    print(f"Copied {source}/ onto {device}.")


if __name__ == "__main__":
    main()
