# Installing to disk

Fresh disk only; afterwards it is `bootc upgrade` + reboot. Partition and mount
the target yourself.

| # | Mount | Size | FS | Holds |
| - | --- | --- | --- | --- |
| 1 | ESP | 1 GiB | FAT32 | Pi firmware and U-Boot; FAT32 is mandatory |
| 2 | `/boot` | 2 GiB | ext4 | kernel and initramfs per deployment |
| 3 | `/` | 8 GiB+ | ext4 | deployments; two are kept |
| 4 | `/var` | remainder | ext4 | container storage, k3s data, logs |

Mount root at `mounted_at`, `/boot` beneath it, the ESP at `boot/efi`. Leave
`/var` **unmounted** — `bootc install to-filesystem` errors if anything is
mounted there ([bootc#997](https://github.com/bootc-dev/bootc/issues/997)).

```bash
sudo ./build.py install /mnt "$ROOT_UUID" "$BOOT_UUID" quay.io/149segolte \
  "systemd.mount-extra=UUID=$VAR_UUID:/var:ext4"
sudo ./build.py add-templates /mnt ADMIN_AP_PSK=... HOME_AP_PSK=... ADMIN_SSH_KEY=...
sudo bootc/install/fix-var-mount.py /mnt /dev/sda4
```

Order matters: `add-templates` runs in the middle so what it writes under
`/var` is carried onto the real partition by the last step.

- The registry argument becomes `--target-imgref` — where the installed host
  pulls upgrades from. Trailing arguments become `--karg`.
- The target is bind-mounted at `/target` inside the install container, not at
  its real path: ostree makes `/mnt` a symlink to `var/mnt`, dangling because
  `/var` is empty in the image.
- `fix-var-mount.py` copies bootc's seed at `ostree/deploy/<stateroot>/var` —
  not `<target>/var`, and not the deployment's own empty `var/`. `rsync -aHAX`
  carries labels, so `--relabel` defaults off.

## ESP

bootc writes the EFI bootloader but not the Broadcom firmware or U-Boot, and
never revisits the ESP. Those are overlaid once from
`bootc/install/templates/files/rpi/`, which is gitignored apart from
`config.txt` and must be populated by hand per the
[FCOS Pi 4 guide](https://docs.fedoraproject.org/en-US/fedora-coreos/provisioning-raspberry-pi4/#_installing_fcos_and_booting_via_u_boot).

Four packages supply it: `uboot-images-armv8`, `bcm2711-firmware`,
`bcm283x-firmware`, `bcm283x-overlays`. The guide's `dnf download` command
lists only three — it omits `bcm2711-firmware`, which carries `start4.elf` and
`fixup4.dat`. Without them the Pi never reaches a kernel.
