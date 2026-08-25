#!/usr/bin/env -S uv run --script
# SPDX-License-Identifier: MPL-2.0
# pyright: reportAny = false
#
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///

"""Overlay files onto a freshly installed target.

Entries come from config.toml beside this script. Each names a source in
files/, an absolute destination inside the target, and optional mode and
ownership.

Run as root on the installer host, after `./build.py install` and before
fix-var-mount.py, so files landing in /var are copied along with it:

  ./overlay.py --mounted-at /mnt --var WIFI_PSK=hunter2
"""

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from string import Template
from typing import NoReturn, override

import tomllib

FILE_LOCATION = Path(__file__).parent
CONFIG = FILE_LOCATION / "config.toml"
FILES = FILE_LOCATION / "files"

# Only machine-local trees. Image content belongs in bootc/files/, where the
# build labels it and ostree carries it, instead of being written in by hand.
ALLOWED_ROOTS = ("etc", "var", "boot")

# FAT32 holds no xattrs, so nothing under the ESP can carry an SELinux label.
UNLABELLED = PurePosixPath("/boot/efi")

DEFAULT_MODE = 0o600
DEFAULT_DIR_MODE = 0o755


def _fail(message: str) -> NoReturn:
    print(f"Error: {message}", file=sys.stderr)
    sys.exit(1)


def _warn(message: str) -> None:
    print(f"Warning: {message}", file=sys.stderr)


def _run(*cmd: str, fatal: bool = True) -> None:
    print("  [CMD]>", shlex.join(cmd), file=sys.stderr)
    try:
        _ = subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        if fatal:
            _fail(f"failed to run `{shlex.join(cmd)}`")
        _warn(f"`{cmd[0]}` failed; continuing.")


def _deployment_root(target: Path) -> Path:
    """The deployment holding the target's /etc, or the target if not ostree."""
    found = sorted(
        d for d in target.glob("ostree/deploy/*/deploy/*") if (d / "etc").is_dir()
    )
    if len(found) > 1:
        _fail(f"{target} holds {len(found)} deployments; cannot tell which is new")
    return found[0] if found else target


def _resolve_owner(owner: str, deployment: Path) -> tuple[int, int]:
    """Numeric ids from the target's own passwd and group, not the host's."""

    def one(value: str, table: Path) -> int:
        if value.isdigit():
            return int(value)
        lines = table.read_text().splitlines() if table.is_file() else []
        for fields in (line.split(":") for line in lines):
            if len(fields) > 2 and fields[0] == value and fields[2].isdigit():
                return int(fields[2])
        _fail(f"no {value!r} in the target's {table.name}; use a numeric id")

    user, _, group = owner.partition(":")
    etc = deployment / "etc"
    return one(user, etc / "passwd"), one(group, etc / "group") if group else -1


@dataclass(frozen=True)
class Plan:
    """One validated entry, ready to write."""

    source: Path
    booted: PurePosixPath  # dest as the booted system sees it
    dest: Path  # where that currently lives on disk
    root: Path  # prefix that maps dest back to booted, for `setfiles -r`
    where: str
    mode: int
    owner: tuple[int, int] | None
    template: bool
    recursive: bool

    @property
    def labelled(self) -> bool:
        """False under the ESP, which is FAT32 and holds no xattrs."""
        return not self.booted.is_relative_to(UNLABELLED)

    @property
    def contents(self) -> list[Path]:
        """The tree beneath a recursive source, or just the source itself."""
        return sorted(self.source.rglob("*")) if self.recursive else [self.source]

    @override
    def __str__(self) -> str:
        parts = [f"{self.booted}  <- {self.source.name}  [{self.where}]"]
        parts += [f"recursive({len(self.contents)})"] if self.recursive else []
        parts += ["templated"] if self.template else []
        parts += [] if self.labelled else ["no-label"]
        parts += [f"mode={self.mode:04o}"]
        parts += [f"owner={self.owner[0]}:{self.owner[1]}"] if self.owner else []
        return " ".join(parts)


def _source(spec: str, recursive: bool) -> Path:
    """A readable file, or a directory when recursive, under files/."""
    if spec.startswith("/"):
        _fail(f"source {spec!r} must be relative to {FILES.name}/")
    source = (FILES / spec).resolve()
    if not source.is_relative_to(FILES.resolve()):
        _fail(f"source {spec!r} resolves outside {FILES.name}/")
    if recursive and not source.is_dir():
        _fail(f"source {source} is not a directory, but the entry is recursive")
    if not recursive and not source.is_file():
        _fail(f"source {source} does not exist")
    return source


def _plan(entry: dict[str, object], target: Path, deployment: Path) -> Plan:
    """Validate one entry, resolving both ends. Writes nothing."""
    for key in ("source", "dest"):
        if key not in entry:
            _fail(f"entry {entry!r} is missing {key!r}")

    recursive = bool(entry.get("recursive", False))
    template = bool(entry.get("template", False))
    if recursive and template:
        _fail(f"entry {entry!r} cannot be both recursive and templated")

    source = _source(str(entry["source"]), recursive)
    dest_spec = str(entry["dest"])
    if not dest_spec.startswith("/"):
        _fail(f"dest {dest_spec!r} must be an absolute path inside the target")

    # Normalise first, or a ".." picks one root and writes under another.
    booted = PurePosixPath(os.path.normpath(dest_spec))
    top = booted.parts[1] if len(booted.parts) > 1 else ""
    if top not in ALLOWED_ROOTS:
        allowed = ", ".join("/" + root for root in ALLOWED_ROOTS)
        _fail(f"dest {dest_spec!r} must be under one of {allowed}")

    # /boot and /var are separate mounts; the deployment's own boot/ and var/
    # are empty mount points, so writing there is hidden once they mount.
    if deployment == target:
        root, where = target, "target"
    elif top == "boot":
        root, where = target, "boot"
    elif top == "var":
        root, where = deployment.parents[1], "stateroot"
    else:
        root, where = deployment, "deployment"

    dest = (root / booted.relative_to("/")).resolve()
    # Belt and braces: a mapping slip must not reach the installer's own files.
    if not dest.is_relative_to(target):
        _fail(f"dest {dest_spec!r} resolves outside {target}")

    mode = int(str(entry["mode"]), 8) if "mode" in entry else DEFAULT_MODE
    owner = (
        _resolve_owner(str(entry["owner"]), deployment) if "owner" in entry else None
    )
    return Plan(source, booted, dest, root, where, mode, owner, template, recursive)


def _render(plan: Plan, variables: dict[str, str]) -> bytes:
    """Read a source, substituting ${...} only when the entry asks for it."""
    if not plan.template:
        return plan.source.read_bytes()
    try:
        return Template(plan.source.read_text()).substitute(variables).encode()
    except KeyError as err:
        _fail(f"{plan.source}: no value for ${{{err.args[0]}}}")
    except ValueError as err:
        _fail(f"{plan.source}: bad template syntax ({err})")


def _readonly_mounts(plans: list[Plan], target: Path) -> list[Path]:
    """Mounts these plans need that are not writable.

    `bootc install` finalises root and boot read-only, so this is the normal
    state right after one.
    """
    mounts: set[Path] = set()
    for plan in plans:
        node = next(p for p in [plan.dest, *plan.dest.parents] if p.exists())
        if os.access(node, os.W_OK):
            continue
        while node != target and node != node.parent and not os.path.ismount(node):
            node = node.parent
        mounts.add(node)
    return sorted(mounts)


def _put(dest: Path, content: bytes, mode: int, owner: tuple[int, int] | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # O_CREAT's mode applies at creation, unlike a later chmod, so a secret is
    # never briefly world-readable.
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, DEFAULT_MODE)
    with os.fdopen(fd, "wb") as handle:
        _ = handle.write(content)
    os.chmod(dest, mode)
    if owner is not None:
        os.chown(dest, *owner)


def _write(plan: Plan, variables: dict[str, str]) -> None:
    if not plan.recursive:
        return _put(plan.dest, _render(plan, variables), plan.mode, plan.owner)

    # `mode` is for the files; directories need their execute bit to be usable.
    for source in [plan.source, *plan.contents]:
        dest = plan.dest / source.relative_to(plan.source)
        if source.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            os.chmod(dest, DEFAULT_DIR_MODE)
            if plan.owner:
                os.chown(dest, *plan.owner)
        else:
            _put(dest, source.read_bytes(), plan.mode, plan.owner)


def _policy_files(deployment: Path) -> tuple[Path, Path] | None:
    """The target's file_contexts and compiled policy, or None if absent.

    setfiles validates every context in the spec against a policy, and the
    running kernel's lacks types only the image defines — k3s_data_t, say.
    """
    policy = "targeted"
    config = deployment / "etc" / "selinux" / "config"
    if config.is_file():
        for line in config.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if key == "SELINUXTYPE" and sep and value:
                policy = value

    base = deployment / "etc" / "selinux" / policy
    contexts = base / "contexts" / "files" / "file_contexts"
    versions = sorted(
        base.glob("policy/policy.*"),
        key=lambda path: int(digits) if (digits := path.suffix[1:]).isdigit() else -1,
    )
    return (contexts, versions[-1]) if contexts.is_file() and versions else None


def _relabel(plans: list[Plan], deployment: Path, dry_run: bool) -> None:
    """Label against the target's policy, one setfiles call per root.

    `-r` strips the on-disk prefix so paths match file_contexts as the booted
    system sees them; restorecon would match the ostree paths and mislabel.
    """
    policy = _policy_files(deployment)
    if policy is None:
        return _warn("no SELinux policy in the target; leaving labels alone.")
    if shutil.which("setfiles") is None:
        return _warn("setfiles not on PATH; leaving labels alone.")
    contexts, binary = policy

    roots = {plan.root for plan in plans}
    print("Labelling:")
    for root in sorted(roots):
        paths = [str(plan.dest) for plan in plans if plan.root == root]
        if dry_run:
            print(f"  [dry-run] setfiles -r {root} ({len(paths)} path(s))")
        else:
            # -c validates against the target's policy, not the running kernel's.
            _run(
                *["setfiles", "-c", str(binary), "-r", str(root)],
                *[str(contexts), *paths],
                fatal=False,
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./overlay.py",
        description=(__doc__ or "").strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _ = parser.add_argument(
        "--mounted-at", required=True, help="path the installed root is mounted at"
    )
    _ = parser.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="substitution value; repeatable, overrides [vars] in the toml",
    )
    _ = parser.add_argument(
        "--relabel",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="set SELinux labels with setfiles (default: enabled)",
    )
    _ = parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be written, without writing it",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()

    if not args.dry_run and os.geteuid() != 0:
        _fail("must run as root; writing into the target needs it.")

    target = Path(args.mounted_at).resolve()
    if not target.is_dir():
        _fail(f"{target} is not a directory. Is the target root mounted?")
    if not CONFIG.is_file():
        _fail(f"{CONFIG} not found")

    config = tomllib.loads(CONFIG.read_text())
    entries = config.get("entry", [])
    if not entries:
        print(f"No entries in {CONFIG.name}; nothing to do.")
        return

    # Command line wins over the toml, so secrets need never be committed.
    overrides: dict[str, str] = {}
    for item in args.var:
        key, sep, value = str(item).partition("=")
        if not sep or not key:
            _fail(f"--var {item!r} is not KEY=VALUE")
        overrides[key] = value
    variables = {**config.get("vars", {}), **overrides}

    deployment = _deployment_root(target)
    if deployment != target:
        print(f"Deployment: {deployment.relative_to(target)}")

    # Validate everything first, so a bad last entry cannot half-overlay.
    plans = [_plan(entry, target, deployment) for entry in entries]

    if not args.dry_run and (readonly := _readonly_mounts(plans, target)):
        for mount in readonly:
            print(f"  {mount}", file=sys.stderr)
        _fail(f"read-only; try `mount -o remount,rw {readonly[0]}`")

    print(f"Overlaying {len(plans)} into {target}:")
    for plan in plans:
        print(f"  {'[dry-run] ' if args.dry_run else ''}{plan}")
        if not args.dry_run:
            _write(plan, variables)
        elif plan.template:
            _ = _render(plan, variables)  # surface template errors anyway

    if args.relabel and (labelled := [p for p in plans if p.labelled]):
        _relabel(labelled, deployment, args.dry_run)


if __name__ == "__main__":
    main()
