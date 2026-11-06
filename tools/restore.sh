#!/usr/bin/env bash
# Restore a backup made by tools/backup.sh to the same ER-101: one erase, one
# program pass, read back and compare, then start the firmware.
#
#   tools/restore.sh --backup DIR [--dry-run]
#
# Refuses unless: the backup is complete and its manifest matches its files; the
# Intel HEX parser passes its self-test; the module on USB has the user page the
# backup was taken from (same unit id); the restore image passes imagecheck.
# The bootloader region and the user page are never written.
#
# The rehearsal the plan asks for is this script run against the backup of the
# shipped firmware, before any fork build is flashed: the module ends up holding
# the same bytes and must boot and play as before.

. "$(dirname "$0")/dfu-common.sh"

BACKUP=
while [ $# -gt 0 ]; do
  case "$1" in
    --backup) BACKUP=$2; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) die "unknown argument $1" ;;
  esac
done
[ -n "$BACKUP" ] || die "--backup DIR is required (a backup made by tools/backup.sh)"
[ -d "$BACKUP" ] || die "$BACKUP is not a directory"
[ -e "$BACKUP/INCOMPLETE" ] && die "$BACKUP is marked INCOMPLETE; it cannot be restored"
[ -f "$BACKUP/manifest.json" ] || die "$BACKUP has no manifest.json; not a completed backup"

WORK=$BACKUP/restore-$(timestamp)
if [ "$DRY_RUN" = 1 ]; then
  log "dry run: nothing is written; the commands below would run (work dir would be $WORK)"
  WORK=$(mktemp -d "${TMPDIR:-/tmp}/er101-restore-dry.XXXXXX")
else
  mkdir "$WORK"
  LOG_FILE=$WORK/restore.log
fi

check_tools
parser_selftest

log "step 1: verify the backup against its manifest"
host "$PYTHON" -I "$TOOLS_DIR/imagecheck.py" backup "$BACKUP" || die "backup verification failed"
UNIT_ID=$("$PYTHON" -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["unit_id"])' "$BACKUP/manifest.json")
log "backup unit id: $UNIT_ID"

log "step 2: build and check the restore image"
host "$PYTHON" -I "$TOOLS_DIR/mkimage.py" restore --backup "$BACKUP" -o "$WORK/restore.hex" || die "could not build the restore image"
host "$PYTHON" -I "$TOOLS_DIR/imagecheck.py" image "$WORK/restore.hex" --backup "$BACKUP" || die "restore image failed its check"

log "step 3: the module on USB must be the unit this backup came from"
double_read "user page" "$WORK/userpage.hex" "$WORK/userpage-read2.hex" --force --user
if [ "$DRY_RUN" = 1 ]; then
  log "+ $PYTHON -I $TOOLS_DIR/imagecheck.py unit-id $WORK/userpage.hex --expect $UNIT_ID"
else
  host "$PYTHON" -I "$TOOLS_DIR/imagecheck.py" unit-id "$WORK/userpage.hex" --expect "$UNIT_ID" \
    || die "the connected module is not the unit in this backup (or its user page did not read back); refusing to erase"
fi

log "step 4: erase, program, verify, launch"
confirm_erase "the restore image $WORK/restore.hex"
program_and_verify "$WORK/restore.hex" "$WORK"
if [ "$DRY_RUN" = 1 ]; then
  rm -rf "$WORK"
  log "dry run complete; no device was touched"
  exit 0
fi
log "restore complete: the module holds the backup $BACKUP and the firmware was launched"
