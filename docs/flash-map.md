# ER-101 flash map and measured build numbers

The module is an Atmel AVR32 UC3C1512C: 512 KiB of flash in 512-byte pages at
`0x80000000`, 64 KiB of SRAM at `0x00000000`, big-endian. These numbers come
from `src/meta.h`, `src/sequencer.h` and `src/voltages.h`, and were confirmed
by the first container build of this fork (2026-11-06). The constants live in
`tools/er101_layout.py`; every checker imports them from there.

## Regions

| Region | Address | Size | Owner on upgrade |
|---|---|---|---|
| Bootloader (Atmel DFU) | `0x80000000`–`0x80001FFF` | 8192 B | never written; `dfu-programmer flash --suppress-bootloader-mem` drops anything here |
| Program code and read-only data | `0x80002000`–`0x8001735F` | 86880 B | firmware |
| Reference tables 0–7 (factory) | `0x80017360`–`0x8001767F` | 8 × 200 B | firmware |
| Reference tables 8–15 (USr1–USr8) | `0x80017680`–`0x80017FDF` | 8 × 200 B | backup (factory copy if erased) |
| Meta descriptor (`meta_info_t`, magic `0xABCDABCD`) | `0x80017FE0`–`0x80017FFF` | 32 B (9 used) | migrated from the backup the way `update_meta_info` would |
| Snapshot slots 1–16 | `0x80018000`–`0x8007FFFF` | 16 × 26624 B | backup |
| User page | `0x80800000`–`0x808001FF` | 512 B | read only: DAC descriptor at `0x808001F0` (magic `0xABCDDCBA`, bits), bootloader configuration words in the last bytes |

Flash is allocated to the byte: the sixteen snapshot slots end exactly at the
end of flash. Each reference table is 100 big-endian `uint16` codes, 8000 codes
per volt. Table 0 is the semitone row; the firmware ships tables 8–15 as copies
of it and the user overwrites them in place from the panel.

The program area is placed by `arch/avr32/er-101.ld`, which asserts at link
time that the trampoline sits at `0x80000000`, `program_start` at
`0x80002000`, `.ref_tables` at `0x80017360` (VMA and LMA) with exactly 3200
bytes ending at the meta descriptor, and that `.data`'s load image stays below
the tables.

## Measured, 3.0.0 container build (2026-11-06)

Toolchain: Microchip AVR32 GNU toolchain 3.4.3.820 (avr32-gcc 4.4.7, GNU ld
2.23.1), GNU Make 4.3, inside `tools/docker`. Two clean builds gave identical
ELF and hex; hex sha256 `f457d2ea48f6346f502df0dfa89f63c26b788d59fcfef770ce15527ff85bbe44`.

| Item | Value |
|---|---|
| `avr32-size` text / data / bss | 83028 / 1292 / 60096 |
| Program bytes `0x80002000`–`0x80013CF3` | 72928 |
| Flash headroom below `0x80017360` | **13932 B** (10732 B with the upstream link line) |
| `_data_lma` (load image of `.data`) | ends at `0x80013CF4` |
| `program_start` | `0x80002000` (upstream build: `0x80002008`, defect B2) |
| `ref_tables` | `0x80017360` VMA = LMA (upstream build: `0x800137E8`, defect B1) |
| `_evba` | 0x200-aligned, `.exception` kept |
| `_stack_size` | 0x1000 (4096 B), injected by the avr32 ld emulation and `PROVIDE`d |
| `_end` | `0x0000DFD0`; 4144 B between `_end` and `_stack` |
| RAM: static data + stack | 61392 of 65536 B (93.7 %) |
| libdsp (`-ldsp-at32ucr3fp-dspspeed_opt`) | linked in, 0 symbols used |
| Compiler stderr | empty (gcc gives no warning for the dropped `--section-start`) |

For information only: the unfixed build of the same sources has the same
84328-byte layout as the machinehistories CI hex (`er-101-firmware-v210.hex`)
with 717 bytes differing in 550 runs, so that hex shows the same two defects.

## Unverified until the first bench session

- Whether `dfu-programmer read` emits absolute (`0x80000000`-based) or
  flash-relative addresses, and whether the read includes the bootloader
  region. `tools/er101_flash.py` accepts both and the backup manifest records
  what was seen.
- The DFU entry procedure (see `docs/flash-and-backup.md`).
- The firmware version on the module's splash and the ER-102's version.
