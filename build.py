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
from pathlib import Path
from types import FunctionType
from typing import Literal

FILE_LOCATION = Path(__file__).parent
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


def install(
    mounted_at: str,
    root_uuid: str,
    boot_uuid: str,
    target_registry: str,
    tag: str = TAG,
    *kargs: str,
) -> None:
    """Install the image onto an already-mounted filesystem. Needs root (privileged container).

    `mounted_at` is the host path the target root is mounted at. Disk layout and partitions are assumed to be already set up. Refer to README.md for details.
    `target_registry` becomes the ref the installed host pulls upgrades from.
    Any trailing arguments are passed through as `--karg`.
    """
    # bootc records the target ref and `bootc upgrade` pulls from it later, so
    # an empty one would leave a host that cannot pull.
    if not target_registry:
        print(
            "Error: target_registry must name a registry the installed host can pull from.",
            file=sys.stderr,
        )
        sys.exit(1)

    target = f"{target_registry}/{IMAGE}:{tag}"

    _run(
        *["podman", "run", "--rm", "--privileged", "--pid=host", "--ipc=host"],
        *["--security-opt", "label=type:unconfined_t"],
        *["-v", "/dev:/dev"],
        *["-v", "/var/lib/containers:/var/lib/containers"],
        *["-v", f"{mounted_at}:{mounted_at}:rslave"],
        f"{IMAGE}:{tag}",
        *["bootc", "install", "to-filesystem"],
        *[f"--karg=root=UUID={root_uuid}"],
        *[f"--karg={arg}" for arg in kargs],
        *["--boot-mount-spec", f"UUID={boot_uuid}"],
        *["--target-imgref", target],
        *[mounted_at],
    )


def add_templates(mounted_at: str, *variables: str) -> None:
    """Overlay the config.toml entries onto an installed target. Needs root.

    Trailing `KEY=VALUE` arguments become template substitutions, which is how secrets are supplied without committing them.
    Use `bootc/install/templates/overlay.py --dry-run` to preview without writing.
    """
    overlay_script = FILE_LOCATION / "bootc" / "install" / "templates" / "overlay.py"

    if not overlay_script.exists():
        print(f"Error: {overlay_script} not found", file=sys.stderr)
        sys.exit(1)

    _run(
        str(overlay_script),
        *["--mounted-at", mounted_at],
        *[arg for variable in variables for arg in ("--var", variable)],
    )


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
            name.replace("_", "-"),
            help=doc.splitlines()[0] if doc else None,
            description=doc,
        )
        for param in inspect.signature(fn).parameters.values():
            if param.kind is inspect.Parameter.VAR_POSITIONAL:
                _ = subparser.add_argument(
                    param.name, nargs="*", help=f"zero or more {param.name}"
                )
            elif param.default is inspect.Parameter.empty:
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

    # A VAR_POSITIONAL parameter cannot be passed by keyword, so bind in
    # declaration order and let the signature decide what goes where.
    values = vars(args)
    positional: list[str] = []
    kwargs: dict[str, object] = {}
    for param in inspect.signature(fn).parameters.values():
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            positional.extend(values.get(param.name) or [])
        elif param.kind is inspect.Parameter.KEYWORD_ONLY:
            kwargs[param.name] = values[param.name]
        else:
            positional.append(values[param.name])

    fn(*positional, **kwargs)


if __name__ == "__main__":
    _main()
