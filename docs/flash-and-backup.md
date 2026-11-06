# Backup, restore and upgrade over USB

Everything here talks to the Atmel DFU bootloader in the first 8 KiB of flash
through `dfu-programmer` 1.1.0, target `at32uc3c1512`. The scripts under
`tools/` are the only supported way to write the module. They never write the
bootloader region or the user page (factory DAC calibration and the bootloader
configuration words; corrupting those makes the module restart into the
bootloader until valid data is written).

**Status: nothing in this document has been exercised on a module yet.** The
scripts are tested on the host against a fake programmer
(`tests/tools/test_flash_tools.py`). The first real session follows the
sequence at the end, in that order.

## Entering DFU mode (UNVERIFIED)

The bootloader must be running for `dfu-programmer` to see the module. The
procedure is a hardware one, independent of whether the application firmware
works. What is known: the ER-101 has a USB connector on the board for firmware
updates, and Atmel's UC3 DFU bootloader is entered either by the ISP condition
on a pin at power-up or from the application. The exact ER-101 procedure (which
button or jumper, at power-up or with the module already powered) is to be
confirmed from Orthogonal Devices' firmware-update instructions before the first
session and written here, with a photo if useful. Until then:

```bash
dfu-programmer at32uc3c1512 get bootloader-version
```

is the harmless test: it prints a version when the bootloader is talking and an
error otherwise. The scripts run it first.

## Backup

```bash
tools/backup.sh                 # writes backups/er101-<UTC timestamp>/
tools/backup.sh --dry-run       # prints what it would run, touches nothing
```

Reads only. The flash is read twice with `read --force` (whole memory, blank
pages included) and the user page twice with `read --force --user`; both pairs
must be byte-identical or the backup is refused. `tools/imagecheck.py backup`
then parses both reads strictly, requires full coverage of `0x80002000`–`0x8007FFFF`
and of the 512-byte user page, and writes `manifest.json`: sha256 of every
file, the **unit id** (sha256 of the user page, which identifies this module;
bootloader ids are not unique), the decoded meta descriptor (version, last
saved slot, start-unpaused), the class of each user table and snapshot slot
(erased, factory or user; erased, saved or unrecognised), the DAC bit depth,
and whether the programmer's addresses were absolute or relative. A directory
with an `INCOMPLETE` marker was not finished and must not be used.

`backups/` is ignored by git. Copy a finished backup somewhere safe before any
erase.

## Restore (and the rehearsal)

```bash
tools/restore.sh --backup backups/er101-<timestamp>
```

Puts the backup's program and data flash back on the same module: one erase,
one programming pass with `flash --suppress-bootloader-mem`, two read-backs
that must agree and equal the image, then `launch`. Before erasing it requires:
the parser self-test, a complete backup whose files match their manifest
hashes, a restore image that passes `imagecheck`, and a live read of the
connected module's user page whose hash equals the backup's unit id. It then
asks you to type `ERASE`. If the read-back differs from the image, the firmware
is not started and the module stays in the bootloader for another attempt.

The plan's rehearsal is this script run against the backup of the shipped
firmware before any fork build is flashed: the module ends up holding the same
bytes and must boot, show its splash and play as before. That is the proof that
recovery works.

## Upgrade

```bash
tools/upgrade.sh --backup backups/er101-<timestamp> --firmware release/avr32/er-101-firmware-3.0.0.hex
```

Same erase, program, verify, launch, with a **unit image** built by
`tools/mkimage.py`: new code and factory tables 0–7 from the firmware hex,
user tables 8–15 and all sixteen snapshot slots from the backup (an erased user
table gets the factory copy and is reported), and a meta descriptor migrated
exactly as the firmware's first boot would migrate it (`src/meta.c`), so the
first boot finds nothing to rewrite. `tools/imagecheck.py image` checks the
image by region ownership; `tools/hexcheck.py` checks the firmware hex and is
never applied to the image. Two more gates than restore: the firmware hex must
pass `hexcheck`, and a live read of the module's flash must still equal the
backup, otherwise the backup is stale (a snapshot saved after it would be
lost) and you take a new one first.

Upgrading from 2.09 or earlier resets the last-saved snapshot slot once
(the `minor < 10` rule); from 2.10 it is kept. Snapshots and user tables are
preserved either way.

## If something goes wrong

- A failed erase or flash leaves the module in the bootloader. Do not power it
  off; run `tools/restore.sh` with the last backup.
- A failed read-back compare does the same: the script refuses to launch.
- The bootloader itself is never written, so the module can always be
  reprogrammed as long as DFU mode can be entered.

## The first bench session, in order

1. Module in DFU mode, `dfu-programmer at32uc3c1512 get bootloader-version` answers.
2. `tools/backup.sh`; keep a copy of the directory off the machine.
3. Read the manifest: version, slot, which tables and slots are in use; note
   the address base so the UNVERIFIED items in `docs/flash-map.md` can be closed.
4. `tools/restore.sh --backup <that backup>` (the rehearsal); power-cycle, film
   the splash (version), check that a sequence plays.
5. Baseline captures on the shipped firmware (`tools/bench/bench.py --dry-run`
   lists them).
6. Only then `tools/upgrade.sh --backup <fresh backup> --firmware release/avr32/er-101-firmware-3.0.0.hex`.
