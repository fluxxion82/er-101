#!/usr/bin/env python3
"""A stand-in for dfu-programmer 1.1.0 so the flash scripts can be exercised on
the host with synthetic data (tests/tools/test_flash_tools.py).

Point DFU_PROGRAMMER at this file and FAKE_DFU_STATE at a directory holding:
  flash.bin      524288 bytes, the device flash (bootloader region included)
  userpage.bin   512 bytes
  config.json    optional: {"address_base": "relative"|"absolute",
                            "glitch_read": N (the N-th read returns one wrong byte),
                            "fail": ["erase", ...] (commands that fail),
                            "bootloader_version": "1.0.3"}
  log            appended: one line per invocation (argv), plus a marker for
                 each erase and flash so tests can assert what ran and in what order

Implements: --version; TARGET get bootloader-version; TARGET read [--force]
[--user]; TARGET erase [--force]; TARGET flash [--suppress-bootloader-mem]
[--force] FILE; TARGET launch; TARGET reset. The real tool's behaviour that
matters to the scripts is mimicked: `read` without --force skips blank pages,
`flash` refuses data in the bootloader region unless --suppress-bootloader-mem is
given and refuses to program non-blank bytes without --force.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
import er101_layout as L  # noqa: E402
from intelhex import HexError, Memory  # noqa: E402

TARGET = "at32uc3c1512"


def main(argv):
    state = os.environ.get("FAKE_DFU_STATE")
    if not state or not os.path.isdir(state):
        print("fake dfu-programmer: FAKE_DFU_STATE must name a directory", file=sys.stderr)
        return 3
    cfg_path = os.path.join(state, "config.json")
    cfg = {}
    if os.path.isfile(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    with open(os.path.join(state, "log"), "a", encoding="utf-8") as f:
        f.write(" ".join(argv[1:]) + "\n")

    if argv[1:] == ["--version"]:
        print(cfg.get("version_string", "dfu-programmer 1.1.0"))
        return 0
    if len(argv) < 3:
        print("fake dfu-programmer: usage: TARGET COMMAND ...", file=sys.stderr)
        return 1
    if argv[1] != TARGET:
        print("fake dfu-programmer: unknown target %s" % argv[1], file=sys.stderr)
        return 1
    cmd, opts = argv[2], argv[3:]
    if cmd in cfg.get("fail", []):
        print("fake dfu-programmer: %s failed (configured)" % cmd, file=sys.stderr)
        return 1

    flash_path = os.path.join(state, "flash.bin")
    user_path = os.path.join(state, "userpage.bin")
    with open(flash_path, "rb") as f:
        flash = bytearray(f.read())
    with open(user_path, "rb") as f:
        userpage = bytearray(f.read())
    assert len(flash) == L.FLASH_SIZE and len(userpage) == L.USER_PAGE_SIZE

    if cmd == "get":
        print("Bootloader Version: %s" % cfg.get("bootloader_version", "0x03 (3)"))
        return 0

    if cmd == "read":
        user = "--user" in opts
        force = "--force" in opts
        data = userpage if user else flash
        base = (L.USER_PAGE_ADDRESS if user else L.FLASH_BASE) if cfg.get("address_base", "relative") == "absolute" else 0
        counter_path = os.path.join(state, "reads")
        n = 1
        if os.path.isfile(counter_path):
            with open(counter_path) as f:
                n = int(f.read() or 0) + 1
        with open(counter_path, "w") as f:
            f.write(str(n))
        data = bytearray(data)
        if cfg.get("glitch_read") == n:
            data[len(data) // 2] ^= 0x01
        mem = Memory()
        page = L.FLASH_PAGE_SIZE
        for off in range(0, len(data), page):
            chunk = bytes(data[off:off + page])
            if force or chunk != b"\xff" * len(chunk):
                mem.put(base + off, chunk)
        sys.stdout.write(mem.to_hex())
        return 0

    if cmd == "erase":
        flash[L.BOOTLOADER_END - L.FLASH_BASE:] = b"\xff" * (L.FLASH_SIZE - (L.BOOTLOADER_END - L.FLASH_BASE))
        with open(flash_path, "wb") as f:
            f.write(flash)
        with open(os.path.join(state, "log"), "a") as f:
            f.write("## ERASED\n")
        print("Erasing flash...  Success", file=sys.stderr)
        return 0

    if cmd == "flash":
        if "--user" in opts or "--eeprom" in opts:
            print("fake dfu-programmer: refusing to write the user page", file=sys.stderr)
            return 1
        path = opts[-1]
        try:
            mem = Memory.from_hex_file(path)
        except (HexError, OSError) as e:
            print("fake dfu-programmer: %s" % e, file=sys.stderr)
            return 1
        if not mem.is_empty() and mem.max_address() < L.FLASH_SIZE:
            mem = mem.shifted(L.FLASH_BASE)
        if mem.has_any(0, L.FLASH_BASE) or mem.has_any(L.FLASH_END, 1 << 33):
            print("fake dfu-programmer: Hex file has data outside of the flash region", file=sys.stderr)
            return 1
        if mem.has_any(L.BOOTLOADER_START, L.BOOTLOADER_END):
            if "--suppress-bootloader-mem" not in opts:
                print("fake dfu-programmer: Bootloader and code overlap. Use --suppress-bootloader-mem to ignore",
                      file=sys.stderr)
                return 1
            mem = mem.slice(L.BOOTLOADER_END, L.FLASH_END)
        for s, buf in mem.segments():
            off = s - L.FLASH_BASE
            if "--force" not in opts and any(b != 0xFF for b in flash[off:off + len(buf)]):
                print("fake dfu-programmer: The target memory for the program is not blank. Use --force",
                      file=sys.stderr)
                return 1
            flash[off:off + len(buf)] = buf
        with open(flash_path, "wb") as f:
            f.write(flash)
        with open(os.path.join(state, "log"), "a") as f:
            f.write("## FLASHED %d bytes\n" % len(mem))
        print("Validating...  Success", file=sys.stderr)
        return 0

    if cmd in ("launch", "reset"):
        with open(os.path.join(state, "log"), "a") as f:
            f.write("## LAUNCHED\n")
        return 0

    print("fake dfu-programmer: unknown command %s" % cmd, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
