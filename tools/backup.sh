#!/usr/bin/env bash
# Full backup of an ER-101 in DFU mode: program and data flash plus the user page.
#
#   tools/backup.sh [--out DIR] [--dry-run]
#
# Writes DIR (default backups/er101-<UTC timestamp>/):
#   flash.hex, flash-read2.hex        two `read --force` of the flash, identical
#   userpage.hex, userpage-read2.hex  two `read --force --user`, identical
#   dfu-info.txt                      programmer version, bootloader version
#   manifest.json                     sha256 of every file, unit id (sha256 of the
#                                     user page), decoded meta descriptor, slot and
#                                     table classes, which address base was seen
#   backup.log                        everything this script printed
#
# Reads only. Nothing is written to the module. The backup is complete only when
# manifest.json exists; a directory with INCOMPLETE in it must not be used.
# Address base of dfu-programmer's output (absolute or flash-relative) is
# UNVERIFIED before the first run; the checker accepts both and records it.

. "$(dirname "$0")/dfu-common.sh"

OUT=
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT=$2; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) die "unknown argument $1" ;;
  esac
done
[ -n "$OUT" ] || OUT="$REPO_DIR/backups/er101-$(timestamp)"

if [ "$DRY_RUN" = 1 ]; then
  log "dry run: nothing is read, nothing is written; the commands below would run"
else
  [ -e "$OUT" ] && die "$OUT exists; choose another --out"
  mkdir -p "$OUT"
  LOG_FILE=$OUT/backup.log
  : > "$OUT/INCOMPLETE"
fi
log "backup directory: $OUT"

check_tools
parser_selftest

log "step 1: identify the programmer and bootloader (compatibility record, not the unit id)"
if [ "$DRY_RUN" = 1 ]; then
  log "+ $DFU --version > $OUT/dfu-info.txt"
  log "+ $DFU $TARGET get bootloader-version >> $OUT/dfu-info.txt"
else
  { "$DFU" --version 2>&1 | head -n 1; } > "$OUT/dfu-info.txt" || true
  if ! "$DFU" "$TARGET" get bootloader-version >> "$OUT/dfu-info.txt" 2>&1; then
    die "cannot talk to the bootloader; put the ER-101 in DFU mode (docs/flash-and-backup.md) and check the USB cable"
  fi
  log "$(cat "$OUT/dfu-info.txt")"
fi

log "step 2: read the whole flash twice (--force includes blank pages)"
double_read "flash" "$OUT/flash.hex" "$OUT/flash-read2.hex" --force

log "step 3: read the user page twice (never written by this project)"
double_read "user page" "$OUT/userpage.hex" "$OUT/userpage-read2.hex" --force --user

log "step 4: parse, check coverage, write the manifest"
if [ "$DRY_RUN" = 1 ]; then
  log "+ $PYTHON -I $TOOLS_DIR/imagecheck.py backup $OUT --write-manifest --dfu-info $OUT/dfu-info.txt"
  log "dry run complete; no device was touched"
  exit 0
fi
host "$PYTHON" -I "$TOOLS_DIR/imagecheck.py" backup "$OUT" --write-manifest --dfu-info "$OUT/dfu-info.txt" \
  || die "backup incomplete: the reads did not pass the checker; $OUT is marked INCOMPLETE"
rm -f "$OUT/INCOMPLETE"
log "backup complete: $OUT"
log "keep it outside git (backups/ is ignored) and copy it somewhere safe before any erase"
