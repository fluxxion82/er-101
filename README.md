# ER-101 Indexed Quad Sequencer — firmware fork

Firmware for the Orthogonal Devices ER-101 Eurorack sequencer, forked from
[odevices/er-101](https://github.com/odevices/er-101) (Brian Clarkson, Orthogonal
Devices), the published source of the shipped v2.10 firmware. All sequencer
behaviour in this repository is theirs; this fork exists to keep the module
buildable and maintainable now that it is discontinued, fix what is broken,
and later add features without breaking the ER-102 pairing.

Fork releases are numbered 3.x so they cannot be mistaken for Orthogonal
Devices releases. The first is 3.0.0 (`CHANGELOG.md`).

## What this fork changes first

Two defects in the upstream build make a hex built from `main` worse than the
firmware that shipped (both confirmed with the real toolchain, see
`docs/flash-map.md`):

1. The reference-table pin `--section-start=.ref_tables=0x80017360` is passed
   bare to the gcc driver, which drops it silently. The tables land wherever
   the linker puts them (`0x800137E8` here), while the firmware saves user
   tables to `0x80017360`. A user-table save then writes into the middle of
   the code.
2. The link order puts the ASF `_start` before the trampoline's `.reset`, so
   `program_start` lands at `0x80002008`, eight bytes after the address the
   bootloader jumps to.

This fork links with `arch/avr32/er-101.ld`, which places and asserts the
layout, and every build is checked by `tools/elfcheck.py` and
`tools/hexcheck.py`. The toolchain is pinned in a container so anyone can
reproduce the hex byte for byte.

## Building

```bash
make docker-firmware   # reference build in the pinned container
make test              # host tests, no toolchain or module needed
```

`docs/build.md` has the details, including the native build and the Rosetta
note for Apple Silicon.

## Flashing

Read `docs/flash-and-backup.md` first. `tools/backup.sh` makes a verified
backup of the module, `tools/restore.sh` puts it back, `tools/upgrade.sh`
programs a build while keeping your user tables, snapshots and settings. The
bootloader and the factory calibration page are never written.

## Layout

- `src/` firmware (upstream, with small fixes); `arch/avr32/` ASF and the linker script
- `scripts/` build (`firmware.mk`, `env.mk` holds `FIRMWARE_VERSION`), host tests (`host.mk`)
- `tools/` checkers, flash scripts, container, bench scaffold
- `tests/host/` sequencer-core tests; `tests/tools/` tooling tests
- `docs/` build guide, flash and backup guide, flash map

## License and credit

The upstream license applies (`LICENSE`). Orthogonal Devices built the ER-101
and wrote this firmware; please do not report problems with fork builds to them.
