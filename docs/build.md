# Building the ER-101 firmware

## In the pinned container (the reference build)

Prerequisites: Docker Desktop (any recent version) and GNU make.

```bash
make docker-firmware
```

The first run builds the image `er101-avr32-toolchain:3.4.3.820` from
`tools/docker/Dockerfile`: `debian:bookworm-slim` pinned by digest, the Microchip
AVR32 GNU toolchain 3.4.3.820 and `avr-headers.zip` downloaded from
`ww1.microchip.com` with their sha256 checked by BuildKit, unpacked under
`/opt/microchip`. The toolchain is x86-64 only, so the image is
`linux/amd64`. Later runs reuse the image; `ER101_REBUILD_IMAGE=1` forces a
rebuild. `tools/docker/run.sh` mounts the repository at `/src/er-101` and runs
as your user, so the output lands in `release/avr32/` owned by you:

- `er-101-firmware-3.0.0.hex`, the artifact to flash through `tools/upgrade.sh`
- `er-101-firmware-3.0.0.elf` and `.map`, kept for `avr32-addr2line` and review

Every build prints the tool versions first (`avr32-gcc`, `ld`, `make`,
`toolchain_dir`), then compiles, links with `arch/avr32/er-101.ld`, runs
`tools/elfcheck.py` on the ELF and `tools/hexcheck.py` on the hex. A failed
check deletes the artifact (`.DELETE_ON_ERROR`) and fails the build. Two clean
builds (`make dist-clean` between them) must give the same hex sha256; the
measured numbers are in `docs/flash-map.md`.

### Apple Silicon

Docker Desktop runs the amd64 image under QEMU unless Rosetta is enabled; a
full build takes about 45 s under QEMU on an M-series Mac and works. To use
Rosetta instead: Docker Desktop → Settings → General → tick "Use Rosetta for
x86_64/amd64 emulation on Apple Silicon" (needs the Virtualization framework,
which is the default). Nothing in the build depends on which one is used.

## Natively

The toolchain is a static x86-64 Linux binary set, so a native build needs an
x86-64 Linux host (or a VM). Unpack the two archives the Dockerfile downloads
into `~/microchip` (or set `toolchain_dir`) exactly as `tools/docker/Dockerfile`
does, then:

```bash
make firmware
```

`python3` (3.8 or later, standard library only) must be on the path for the
checkers. Mirror copies of the toolchain tarball, the headers and the
toolchain's source archives are kept outside the repository with sha256 sums;
`tools/docker/Dockerfile` records the checksums the build trusts.

## Host tests

```bash
make test
```

runs two suites and needs no toolchain, container or module:

- `make test-host`: the sequencer core (`sequencer.c`, `clipboard.c`,
  `voltages.c`, `wallclock.c`, `meta.c`) compiled on the host with
  `tests/host/asf.h`, a fake ASF, and `tests/host/fake_hal.c`, linked against
  the vendored `greatest.h`, built with `-fsanitize=address,undefined` and
  `-fno-strict-aliasing`. Tested on macOS clang; Linux gcc should work with
  `HOSTCC=gcc`. Target-representation tests (snapshot fixtures from a real
  dump, `sizeof`/`offsetof` of every serialised struct) wait for the first
  backup; `tests/host/test_sequencer.c` carries the TODO.
- `make test-tools`: the Intel HEX parser, the unit-image builder and checkers
  and the three flash scripts, the scripts driven against
  `tests/tools/fake_dfu_programmer.py` with synthetic data.

## Version

`FIRMWARE_VERSION` in `scripts/env.mk` is the single source. `make firmware`
generates `release/avr32/generated/version.h` from it and `src/meta.c` reads
the three numbers from there; the artifact names carry it. `make check-version`
confirms that `scripts/env.mk`, the first entry of `CHANGELOG.md` and the tag
on HEAD (if any) agree. Keep `minor` below 10 in mind: `update_meta_info`
resets the last-saved snapshot slot when the previous firmware's minor was
below 10 (see `CHANGELOG.md` and finding P4 in the review notes).

## Flashing tools

`dfu-programmer` 1.1.0 is the only programmer the scripts are written for
(`brew install dfu-programmer` installs 1.1.0 on macOS as of 2026-11; the
scripts refuse other versions unless `ER101_DFU_VERSION_OK=1`). Nothing else is
needed. See `docs/flash-and-backup.md` before connecting the module.
