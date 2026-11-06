#!/usr/bin/env python3
"""Check the ER-101 firmware hex, the artifact that gets flashed, against the flash map.

    python3 tools/hexcheck.py release/avr32/er-101-firmware-3.0.0.hex

Runs after every build (scripts/firmware.mk) and before any flash. Exit status
0 when every check passes, 1 otherwise. This is a check on the *firmware
artifact*; the merged unit image that tools/upgrade.sh programs has its own
checker (tools/imagecheck.py) and these bounds are never loosened for it.

Checks (docs/flash-map.md has the numbers):
  - the file is strict Intel HEX (tools/intelhex.py), no overlaps, EOF present
  - 0x80000000 holds the trampoline's branch to 0x80002000, and the rest of the
    bootloader's 8 KiB is zero padding only (dfu-programmer drops it with
    --suppress-bootloader-mem; it is never flashed)
  - 0x80002000 holds the jump to _stext (not zeros): a word load into PC and a
    target inside the program area that the hex covers
  - 0x80017360 holds all 3200 bytes of the reference tables, table 0 is the
    semitone row and the factory copies of the user tables 8-15 match it
  - nothing is present at or beyond 0x80017FE0 (meta descriptor, snapshots,
    user page) and nothing outside flash
  - the headroom between the last code byte and 0x80017360 is reported
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import er101_layout as L  # noqa: E402
from intelhex import HexError, Memory  # noqa: E402

TRAMPOLINE_BRANCH = bytes.fromhex("E08F1000")   # bral +0x2000 (rjmp program_start, relaxed)
LOAD_PC_FROM_NEXT_WORD = bytes.fromhex("FEFF0004")  # ld.w pc, pc[4]  (lda.w pc, _stext)


def check(path, verbose=True):
    failures = []
    notes = []

    def ok(msg):
        notes.append("  ok    " + msg)

    def bad(msg):
        failures.append(msg)
        notes.append("  FAIL  " + msg)

    try:
        mem = Memory.from_hex_file(path)
    except (HexError, OSError, UnicodeDecodeError) as e:
        print("hexcheck: %s" % e)
        print("hexcheck: FAILED")
        return False
    ok("strict Intel HEX, %d bytes in %d segment(s)" % (len(mem), len(mem.segments())))

    # Trampoline and bootloader region.
    head = mem.get(L.FLASH_BASE, 4, fill=None) if mem.covers(L.FLASH_BASE, L.FLASH_BASE + 4) else None
    if head == TRAMPOLINE_BRANCH:
        ok("0x%08X: bral to 0x%08X (trampoline)" % (L.FLASH_BASE, L.PROGRAM_START))
    else:
        bad("0x%08X holds %s, expected the trampoline's branch %s to 0x%08X"
            % (L.FLASH_BASE, head.hex() if head else "nothing", TRAMPOLINE_BRANCH.hex(), L.PROGRAM_START))
    rest = mem.slice(L.FLASH_BASE + 4, L.BOOTLOADER_END)
    nonzero = [s for s, buf in rest.segments() if any(buf)]
    if nonzero:
        bad("bootloader region 0x%08X-0x%08X holds code other than the trampoline (from 0x%08X)"
            % (L.FLASH_BASE + 4, L.BOOTLOADER_END - 1, nonzero[0]))
    else:
        ok("bootloader region holds only the trampoline and zero padding")

    # Program entry.
    if mem.covers(L.PROGRAM_START, L.PROGRAM_START + 8):
        entry = mem.get(L.PROGRAM_START, 8)
        target = struct.unpack(">I", entry[4:8])[0]
        if entry[:4] == LOAD_PC_FROM_NEXT_WORD and L.PROGRAM_START <= target < L.CODE_LIMIT \
                and mem.covers(target, target + 2):
            ok("0x%08X: jump to _stext at 0x%08X" % (L.PROGRAM_START, target))
        else:
            bad("0x%08X holds %s, expected the jump to _stext (%s + address in the program area)"
                % (L.PROGRAM_START, entry.hex(), LOAD_PC_FROM_NEXT_WORD.hex()))
    else:
        bad("no data at 0x%08X, where the bootloader starts the firmware" % L.PROGRAM_START)

    # Reference tables.
    if mem.covers(L.REF_TABLES_ADDRESS, L.REF_TABLES_END):
        tables = mem.get(L.REF_TABLES_ADDRESS, L.REF_TABLES_SIZE)
        table0 = struct.unpack(">%dH" % L.VOLTAGES_PER_TABLE, tables[: L.REF_TABLE_SIZE])
        if table0 == L.SEMITONE_ROW:
            ok("table 0 at 0x%08X is the semitone row" % L.REF_TABLES_ADDRESS)
        else:
            bad("table 0 at 0x%08X is not the semitone row (starts %s)" % (L.REF_TABLES_ADDRESS, table0[:4]))
        bad_user = [t for t in range(L.FIRST_USER_TABLE, L.NUM_REF_TABLES)
                    if tables[t * L.REF_TABLE_SIZE:(t + 1) * L.REF_TABLE_SIZE] != tables[: L.REF_TABLE_SIZE]]
        if bad_user:
            bad("factory user tables %s do not match table 0" % bad_user)
        else:
            ok("all 3200 table bytes present; user tables 8-15 carry the factory semitone copy")
    else:
        present = mem.slice(L.REF_TABLES_ADDRESS, L.REF_TABLES_END)
        bad("reference tables are not at 0x%08X (%d of %d bytes present); user table saves would be lost"
            % (L.REF_TABLES_ADDRESS, len(present), L.REF_TABLES_SIZE))

    # Nothing beyond the tables.
    beyond = mem.slice(L.META_ADDRESS, 0x100000000)
    below = mem.slice(0, L.FLASH_BASE)
    if beyond.is_empty() and below.is_empty():
        ok("nothing at or beyond 0x%08X and nothing below flash" % L.META_ADDRESS)
    else:
        for s, buf in beyond.segments() + below.segments():
            bad("%d byte(s) at 0x%08X (%s) must not be in a firmware hex"
                % (len(buf), s, L.region_name(s)))

    # Headroom.
    code = mem.slice(L.PROGRAM_START, L.CODE_LIMIT)
    code_end = (code.max_address() + 1) if not code.is_empty() else L.PROGRAM_START
    headroom = L.CODE_LIMIT - code_end
    gap_low = mem.slice(L.CODE_LIMIT, L.REF_TABLES_ADDRESS)  # empty range, kept for symmetry
    del gap_low

    report = ["hexcheck: %s" % path]
    for s, buf in mem.segments():
        report.append("  0x%08X-0x%08X %7d bytes  %s" % (s, s + len(buf) - 1, len(buf), L.region_name(s)))
    report.append("  program bytes 0x%08X-0x%08X: %d; headroom to 0x%08X: %d bytes"
                  % (L.PROGRAM_START, code_end - 1, len(code), L.CODE_LIMIT, headroom))
    report.extend(notes)
    report.append("hexcheck: FAILED (%d problem%s)" % (len(failures), "" if len(failures) == 1 else "s")
                  if failures else "hexcheck: ok")
    if verbose:
        print("\n".join(report))
    return not failures


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    return 0 if check(argv[1]) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
