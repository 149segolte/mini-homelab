# Install-time overlay

The machine-local half of the host: files that cannot be baked into an image
because they are specific to this Pi, or secret.

```bash
./build.py add-templates <mounted_at> [KEY=VALUE ...]
bootc/install/templates/overlay.py --mounted-at /mnt --dry-run
```

Entries and their fields are in `bootc/install/templates/config.toml`, which
documents its own schema. `[vars]` supplies substitutions; trailing
`KEY=VALUE` arguments override them, which is how `ADMIN_AP_PSK`, `HOME_AP_PSK`
and `ADMIN_SSH_KEY` stay out of git. The SSH key is base64 — tmpfiles' `f~`
decodes its argument.

Anything not machine-local belongs in `bootc/files/` instead, where the image
build labels and versions it.

## dest mapping

A booted path is not a path under the mount point. `dest` is written as:

| Booted | On disk |
| --- | --- |
| `/boot/*` | the target directly |
| `/var/*` | `ostree/deploy/<stateroot>/var/*`, shared across deployments |
| `/etc/*` | `ostree/deploy/<stateroot>/deploy/<checksum>.<serial>/etc/*` |

Only those three roots are allowed. `/usr` belongs to the image; the rest are
symlinks into `/var`.

## Guarantees

Every entry is validated — source exists, dest legal, owner resolves, no
destination on a read-only mount — before anything is written. bootc finalises
root and boot read-only at the end of the install, so the alternative is
`[Errno 30]` mid-overlay.

Owners resolve against the target's `passwd`/`group`, and files are relabelled
with `setfiles -c <target policy> -r <root>`. Both because the installer host
is not the image: without `-c`, setfiles validates the spec against the running
kernel's policy and rejects contexts like `k3s_data_t` outright. Relabelling is
non-fatal; `--no-relabel` skips it.

## sysusers

`systemd-sysusers.service` ships with `ConditionNeedsUpdate=|/etc`, which never
fires in image mode — `/etc` is merged from `/usr/etc` at deploy time and
marked current, so the admin user is never created. Both fixes are kept, since
a `touch` costs nothing: a drop-in resets the conditions, and `add-templates`
touches the deployment's `/usr`.

`sysusers.conf` declares the group explicitly; the `uid:gid` form on the `u`
line fails with `please create GID 1000` without it.
