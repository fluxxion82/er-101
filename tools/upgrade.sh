#!/usr/bin/env bash
# Upgrade an ER-101 to a firmware build while keeping its user tables, snapshots
# and settings: one erase, one program pass with the merged unit image, read back
# and compare, then start the firmware.
#
#   tools/upgrade.sh --backup DIR --firmware release/avr32/er-101-firmware-3.0.0.hex [--dry-run]
#
# Refuses unless: the backup is complete and matches its manifest; the parser
# passes its self-test; the firmware hex passes tools/hexcheck.py; the merged
# image passes tools/imagecheck.py; the module on USB is the unit the backup came
# from; and the module's flash still equals the backup (otherwise the backup is
# stale and the user data it would carry forward is not current: take a new one).
# The bootloader region and the user page are never written.

. "$(dirname "$0")/dfu-common.sh"

BACKUP=
FIRMWARE=
while [ $# -gt 0 ]; do
  case "$1" in
    --backup) BACKUP=$2; shift 2 ;;
    --firmware) FIRMWARE=$2; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) die "unknown argument $1" ;;
  esac
done
[ -n "$BACKUP" ] || die "--backup DIR is required (a backup made by tools/backup.sh, taken just before this upgrade)"
[ -n "$FIRMWARE" ] || die "--firmware HEX is required"
[ -d "$BACKUP" ] || die "$BACKUP is not a directory"
[ -e "$BACKUP/INCOMPLETE" ] && die "$BACKUP is marked INCOMPLETE"
[ -f "$BACKUP/manifest.json" ] || die "$BACKUP has no manifest.json; not a completed backup"
[ -f "$FIRMWARE" ] || die "$FIRMWARE not found"

WORK=$BACKUP/upgrade-$(timestamp)
if [ "$DRY_RUN" = 1 ]; then
  log "dry run: nothing is written; the commands below would run (work dir would be $WORK)"
  WORK=$(mktemp -d "${TMPDIR:-/tmp}/er101-upgrade-dry.XXXXXX")
else
  mkdir "$WORK"
  LOG_FILE=$WORK/upgrade.log
fi

check_tools
parser_selftest

log "step 1: verify the backup against its manifest"
host "$PYTHON" -I "$TOOLS_DIR/imagecheck.py" backup "$BACKUP" || die "backup verification failed"
UNIT_ID=$("$PYTHON" -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["unit_id"])' "$BACKUP/manifest.json")
log "backup unit id: $UNIT_ID"

log "step 2: check the firmware artifact"
host "$PYTHON" -I "$TOOLS_DIR/hexcheck.py" "$FIRMWARE" || die "the firmware hex failed hexcheck; it must not be flashed"
cp "$FIRMWARE" "$WORK/firmware.hex"
log "firmware sha256: $(shasum -a 256 "$FIRMWARE" | cut -d' ' -f1)"

log "step 3: build and check the merged unit image"
host "$PYTHON" -I "$TOOLS_DIR/mkimage.py" upgrade --backup "$BACKUP" --firmware "$FIRMWARE" -o "$WORK/upgrade.hex" \
  || die "could not build the unit image"
host "$PYTHON" -I "$TOOLS_DIR/imagecheck.py" image "$WORK/upgrade.hex" --backup "$BACKUP" --firmware "$FIRMWARE" \
  || die "the unit image failed its check"

log "step 4: the module on USB must be the unit this backup came from"
double_read "user page" "$WORK/userpage.hex" "$WORK/userpage-read2.hex" --force --user
if [ "$DRY_RUN" = 1 ]; then
  log "+ $PYTHON -I $TOOLS_DIR/imagecheck.py unit-id $WORK/userpage.hex --expect $UNIT_ID"
else
  host "$PYTHON" -I "$TOOLS_DIR/imagecheck.py" unit-id "$WORK/userpage.hex" --expect "$UNIT_ID" \
    || die "the connected module is not the unit in this backup (or its user page did not read back); refusing to erase"
fi

log "step 5: the module's flash must still match the backup (the backup must be current)"
double_read "flash" "$WORK/live.hex" "$WORK/live-read2.hex" --force
if [ "$DRY_RUN" = 1 ]; then
  log "+ $PYTHON -I $TOOLS_DIR/imagecheck.py live $WORK/live.hex --backup $BACKUP"
else
  host "$PYTHON" -I "$TOOLS_DIR/imagecheck.py" live "$WORK/live.hex" --backup "$BACKUP" \
    || die "the module no longer matches the backup; run tools/backup.sh again and upgrade from the new backup"
fi

log "step 6: erase, program, verify, launch"
confirm_erase "the unit image $WORK/upgrade.hex (firmware $FIRMWARE)"
program_and_verify "$WORK/upgrade.hex" "$WORK"
if [ "$DRY_RUN" = 1 ]; then
  rm -rf "$WORK"
  log "dry run complete; no device was touched"
  exit 0
fi
log "upgrade complete: the module holds $FIRMWARE with its user tables, snapshots and meta descriptor carried over"
log "work files: $WORK"
