#!/usr/bin/env python3
"""Checks for backups, unit images and device read-backs (plan item A9).

    python3 tools/imagecheck.py backup DIR [--write-manifest [--dfu-info FILE]]
    python3 tools/imagecheck.py unit-id USERPAGE.hex [--expect SHA256]
    python3 tools/imagecheck.py image IMAGE.hex --backup DIR [--firmware FW.hex [--version X.Y.Z]]
    python3 tools/imagecheck.py readback READ.hex --image IMAGE.hex
    python3 tools/imagecheck.py live LIVE.hex --backup DIR

backup    the two flash reads agree, the two user-page reads agree, both parse as
          strict Intel HEX, the flash read covers 0x80002000-0x8007FFFF and the
          user page all 512 bytes; with --write-manifest the manifest is written,
          otherwise it must exist and match (file hashes, unit id)
unit-id   prints the unit id of one user-page read (sha256 of its 512 bytes);
          with --expect, fails unless it matches
image     region ownership: code and tables 0-7 byte-equal to the firmware,
          tables 8-15 and snapshots byte-equal to the backup (an erased backup
          table may carry the factory copy), the meta descriptor equal to the
          migration's output, nothing in the bootloader region or the user page;
          without --firmware the image is a restore image and every region must
          equal the backup. tools/hexcheck.py is NOT applied to images.
readback  after programming: the device read equals the image over every written
          region, erased bytes on both sides reading 0xFF; bootloader ignored
live      before an upgrade: the device still holds what the backup holds

Exit 0 when every check passes, 1 otherwise.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import er101_flash as F  # noqa: E402
import er101_layout as L  # noqa: E402
from intelhex import HexError, Memory  # noqa: E402


def report(failures, what):
    for f in failures:
        print("  FAIL  " + f)
    if failures:
        print("imagecheck: %s FAILED (%d problem%s)" % (what, len(failures), "" if len(failures) == 1 else "s"))
        return 1
    print("imagecheck: %s ok" % what)
    return 0


def cmd_backup(args):
    b = F.load_backup(args.directory)
    if args.write_manifest:
        dfu_info = None
        if args.dfu_info and os.path.isfile(args.dfu_info):
            with open(args.dfu_info, "r", encoding="utf-8", errors="replace") as f:
                dfu_info = f.read().strip()
        manifest = F.write_manifest(b, dfu_info)
        print("imagecheck: wrote %s" % b.manifest_path)
    else:
        manifest = F.verify_manifest(b)
    print("  unit id            %s" % b.unit_id)
    print("  flash addresses    %s; bootloader region in read: %s"
          % (b.flash_base, "yes" if manifest["bootloader_region_in_read"] else "no"))
    print("  user page          %s addresses, DAC bits %s" % (b.userpage_base, manifest["dac_bits"]))
    m = manifest["meta"]
    print("  meta descriptor    %s" % ("erased" if m["erased"] else "%s, slot %d, start_unpaused %d%s"
          % (m["version"], m["last_saved_snapshot"], m["start_unpaused"], "" if m["valid"] else " (no magic)")))
    tables = manifest["user_tables"]
    print("  user tables 8-15   %s" % ", ".join("%s:%s" % (k, v) for k, v in sorted(tables.items(), key=lambda kv: int(kv[0]))))
    snaps = manifest["snapshots"]
    print("  snapshots          %s" % ", ".join("%s:%s" % (k, v) for k, v in sorted(snaps.items(), key=lambda kv: int(kv[0]))))
    return report([], "backup %s" % args.directory)


def cmd_unit_id(args):
    unit_id, kind = F.unit_id_from_userpage_hex(args.userpage)
    print("%s  (%s addresses)" % (unit_id, kind))
    if args.expect and args.expect != unit_id:
        return report(["user page hash %s does not match the expected unit id %s: wrong unit, or the user "
                       "page did not read back as recorded" % (unit_id, args.expect)], "unit id")
    return report([], "unit id")


def cmd_image(args):
    b = F.load_backup(args.backup)
    F.verify_manifest(b)
    image = Memory.from_hex_file(args.image)
    if args.firmware:
        fw = Memory.from_hex_file(args.firmware)
        version = F.parse_version(args.version) if args.version else F.version_from_env_mk()
        failures = F.check_image(image, b.flash, fw, version)
        what = "unit image %s (firmware %s, version %d.%d.%d)" % ((args.image, args.firmware) + version)
    else:
        failures = F.check_image(image, b.flash)
        what = "restore image %s" % args.image
    for line in F.region_table(image):
        print(line)
    return report(failures, what)


def cmd_readback(args):
    read, kind = F.normalize_flash(Memory.from_hex_file(args.readback))
    image = Memory.from_hex_file(args.image)
    print("  read-back uses %s addresses, %d bytes" % (kind, len(read)))
    return report(F.compare_readback(read, image), "read-back %s against %s" % (args.readback, args.image))


def cmd_live(args):
    b = F.load_backup(args.backup)
    F.verify_manifest(b)
    live, kind = F.normalize_flash(Memory.from_hex_file(args.live))
    print("  live read uses %s addresses, %d bytes" % (kind, len(live)))
    failures = []
    if not live.covers(L.PROGRAM_START, L.FLASH_END):
        failures.append("live read does not cover 0x%08X-0x%08X" % (L.PROGRAM_START, L.FLASH_END - 1))
    failures += F.compare_live_with_backup(live, b.flash)
    if failures:
        failures.append("the backup is not what the device holds now: take a new backup before upgrading")
    return report(failures, "device matches backup %s" % args.backup)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("backup")
    p.add_argument("directory")
    p.add_argument("--write-manifest", action="store_true")
    p.add_argument("--dfu-info", help="text file with the dfu-programmer version and `get` output to record")
    p.set_defaults(func=cmd_backup)
    p = sub.add_parser("unit-id")
    p.add_argument("userpage")
    p.add_argument("--expect")
    p.set_defaults(func=cmd_unit_id)
    p = sub.add_parser("image")
    p.add_argument("image")
    p.add_argument("--backup", required=True)
    p.add_argument("--firmware")
    p.add_argument("--version")
    p.set_defaults(func=cmd_image)
    p = sub.add_parser("readback")
    p.add_argument("readback")
    p.add_argument("--image", required=True)
    p.set_defaults(func=cmd_readback)
    p = sub.add_parser("live")
    p.add_argument("live")
    p.add_argument("--backup", required=True)
    p.set_defaults(func=cmd_live)
    args = ap.parse_args(argv[1:])
    try:
        return args.func(args)
    except (F.ImageError, HexError, OSError, ValueError) as e:
        print("imagecheck: %s" % e)
        print("imagecheck: FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
