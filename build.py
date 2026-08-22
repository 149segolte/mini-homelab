#!/usr/bin/env -S uv run --script
# SPDX-License-Identifier: MPL-2.0
# pyright: reportAny = false
#
# /// script
# requires-python = ">=3.13"
# dependencies = []
#
# [tool.basedpyright]
# reportAny = false
# ///

import argparse
import inspect
import shlex
import subprocess
import sys
from types import FunctionType
from typing import Literal

IMAGE = "mini-homelab"
PLATFORM = "linux/arm64"
TAG = "latest"

# --- BEGIN: Project tasks ---


def build(tag: str = TAG, cache_registry: str = "") -> None:
    """Build the host image. `bootc container lint` runs inside the build.

    Args:
        tag: The image tag to use.
        cache_registry: The cache registry to use for the build. (pushed to `{IMAGE}-cache`)
    """
    cache = f"{cache_registry}/{IMAGE}-cache" if cache_registry else None
    _run(
        "podman",
        "build",
        "--platform",
        PLATFORM,
        "-t",
        f"{IMAGE}:{tag}",
        *(["--layers", "--cache-from", cache, "--cache-to", cache] if cache else []),
        "bootc/",
    )


def push(registry: str, tag: str = TAG) -> None:
    """Tag and push an already-built image to one registry."""
    ref = f"{registry}/{IMAGE}:{tag}"
    _run("podman", "tag", f"{IMAGE}:{tag}", ref)
    _run("podman", "push", ref)


# --- END: Project tasks ---


def _run(*cmd: str, quiet: Literal["off", "echo", "output", "full"] = "off") -> None:
    """Echo a command, then run it, failing the script if it fails."""
    if quiet not in ("echo", "full"):
        print("[CMD]>", shlex.join(cmd), file=sys.stderr)
    try:
        _ = subprocess.run(cmd, check=True, capture_output=quiet in ("output", "full"))
    except subprocess.CalledProcessError as err:
        print(f"Error: failed to run `{shlex.join(cmd)}`", file=sys.stderr)
        sys.exit(err.returncode)


def _tasks() -> dict[str, FunctionType]:
    """Public module-level functions, keyed by subcommand name.

    Underscore-prefixed helpers are not tasks.
    """
    current_module = sys.modules[__name__]
    return {
        name: obj
        for name, obj in inspect.getmembers(current_module, inspect.isfunction)
        if obj.__module__ == __name__ and not name.startswith("_")
    }


def _parser() -> argparse.ArgumentParser:
    """Build a subcommand per task, with arguments taken from its signature."""
    parser = argparse.ArgumentParser(
        prog="./build.py", description="Build tasks for mini-homelab."
    )
    subparsers = parser.add_subparsers(dest="task", metavar="task")

    for name, fn in _tasks().items():
        doc = (fn.__doc__ or "").strip()
        subparser = subparsers.add_parser(
            name, help=doc.splitlines()[0] if doc else None, description=doc
        )
        for param in inspect.signature(fn).parameters.values():
            if param.default is inspect.Parameter.empty:
                _ = subparser.add_argument(param.name)
            else:
                _ = subparser.add_argument(
                    f"--{param.name.replace('_', '-')}",
                    dest=param.name,
                    default=param.default,
                    help=f"default: {param.default}",
                )
        subparser.set_defaults(_fn=fn)

    return parser


def _main() -> None:
    parser = _parser()
    args = parser.parse_args()
    fn = getattr(args, "_fn", None)
    if fn is None:
        parser.print_help()
        return

    kwargs = {k: v for k, v in vars(args).items() if k not in ("task", "_fn")}
    fn(**kwargs)


if __name__ == "__main__":
    _main()
