#!/usr/bin/env python3
"""Build the unit image that tools/upgrade.sh programs, or the restore image for
tools/restore.sh.

    python3 tools/mkimage.py upgrade --backup BACKUP_DIR --firmware release/avr32/er-101-firmware-3.0.0.hex -o upgrade.hex
    python3 tools/mkimage.py restore --backup BACKUP_DIR -o restore.hex

upgrade: new code and factory tables 0-7 from the firmware hex, user tables 8-15
and all snapshot slots from the backup (an erased user table gets the factory
copy and is reported), and a meta descriptor migrated the way the firmware's
first boot would migrate it (src/meta.c update_meta_info), so the first boot
finds nothing to rewrite. The version comes from scripts/env.mk unless
--version is given; it must match the version in the firmware file name.

restore: the backup's program and data flash as one image.

Neither image carries bytes in the bootloader region or the user page. Every
image is checked by tools/imagecheck.py before it is written here, and the
scripts check it again. Exit 0 on success, 1 on any problem.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import er101_flash as F  # noqa: E402
from intelhex import HexError, Memory  # noqa: E402


def version_in_filename(path):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)\.hex$", os.path.basename(path))
    return tuple(int(x) for x in m.groups()) if m else None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    up = sub.add_parser("upgrade", help="merged unit image from firmware and backup")
    up.add_argument("--backup", required=True)
    up.add_argument("--firmware", required=True)
    up.add_argument("--version", help="MAJOR.MINOR.PATCH; default FIRMWARE_VERSION from scripts/env.mk")
    up.add_argument("-o", "--output", required=True)
    rs = sub.add_parser("restore", help="restore image from a backup")
    rs.add_argument("--backup", required=True)
    rs.add_argument("-o", "--output", required=True)
    args = ap.parse_args(argv[1:])

    try:
        backup = F.load_backup(args.backup)
        F.verify_manifest(backup)
        if args.command == "upgrade":
            fw = Memory.from_hex_file(args.firmware)
            version = F.parse_version(args.version) if args.version else F.version_from_env_mk()
            named = version_in_filename(args.firmware)
            if named is not None and named != version:
                raise F.ImageError("firmware file is named %d.%d.%d but the version to migrate to is %d.%d.%d"
                                   % (named + version))
            image, report = F.build_upgrade_image(fw, backup.flash, version)
            failures = F.check_image(image, backup.flash, fw, version)
        else:
            image, report = F.build_restore_image(backup.flash)
            failures = F.check_image(image, backup.flash)
    except (F.ImageError, HexError, OSError) as e:
        print("mkimage: %s" % e, file=sys.stderr)
        return 1

    print("mkimage: %s image for unit %s" % (args.command, backup.unit_id[:16]))
    for line in report:
        print("  " + line)
    for line in F.region_table(image):
        print(line)
    if failures:
        for f in failures:
            print("  FAIL  " + f)
        print("mkimage: FAILED, nothing written", file=sys.stderr)
        return 1
    image.write_hex_file(args.output)
    print("mkimage: wrote %s (%d bytes, sha256 %s)" % (args.output, len(image), F.sha256_file(args.output)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
