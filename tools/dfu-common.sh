# Shared by tools/backup.sh, tools/restore.sh and tools/upgrade.sh. Sourced, not run.
#
# Programmer: dfu-programmer 1.1.0, target at32uc3c1512. Override the binary with
# DFU_PROGRAMMER=/path/to/dfu-programmer (the host tests point it at a fake).
# Another version is refused unless ER101_DFU_VERSION_OK=1.
#
# Every script accepts --dry-run: it prints each command it would run, runs
# nothing that talks to the device and writes nothing.

set -eu

TOOLS_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_DIR=$(cd "$TOOLS_DIR/.." && pwd)
DFU=${DFU_PROGRAMMER:-dfu-programmer}
TARGET=at32uc3c1512
PYTHON=${PYTHON:-python3}
DRY_RUN=0
SCRIPT_NAME=$(basename "$0")
LOG_FILE=

log() {
  printf '%s\n' "$*"
  if [ -n "$LOG_FILE" ]; then printf '%s\n' "$*" >> "$LOG_FILE"; fi
}

die() {
  log "$SCRIPT_NAME: $*"
  log "$SCRIPT_NAME: STOPPED, nothing further was done"
  exit 1
}

# Run a host-side command (python checkers and the like). Runs in dry-run too:
# these touch files, not the device.
host() {
  log "+ $*"
  "$@"
}

# Run dfu-programmer with stdout to the terminal and the log.
dfu() {
  log "+ $DFU $TARGET $*"
  if [ "$DRY_RUN" = 1 ]; then return 0; fi
  "$DFU" "$TARGET" "$@"
}

# Run a dfu-programmer read into a file. Usage: dfu_read FILE [read options].
dfu_read() {
  out=$1
  shift
  log "+ $DFU $TARGET read $* > $out"
  if [ "$DRY_RUN" = 1 ]; then return 0; fi
  "$DFU" "$TARGET" read "$@" > "$out"
}

check_tools() {
  command -v "$PYTHON" > /dev/null 2>&1 || die "$PYTHON not found"
  command -v "$DFU" > /dev/null 2>&1 || die "$DFU not found; install dfu-programmer 1.1.0 (brew install dfu-programmer) or set DFU_PROGRAMMER"
  dfu_version=$("$DFU" --version 2>&1 | head -n 1 || true)
  log "programmer: $dfu_version"
  case "$dfu_version" in
    *"dfu-programmer 1.1.0"*) ;;
    *)
      if [ "${ER101_DFU_VERSION_OK:-0}" != 1 ]; then
        die "this tooling is written for dfu-programmer 1.1.0, found '$dfu_version'; set ER101_DFU_VERSION_OK=1 to proceed anyway"
      fi
      log "warning: proceeding with an untested dfu-programmer version"
      ;;
  esac
}

# The parser must reject malformed input before any read is trusted or any
# erase is issued.
parser_selftest() {
  host "$PYTHON" -I "$TOOLS_DIR/intelhex.py" --selftest || die "Intel HEX parser self-test failed"
}

# Read something twice and require both reads to be identical. Usage:
# double_read NAME FILE1 FILE2 [read options]
double_read() {
  name=$1 first=$2 second=$3
  shift 3
  dfu_read "$first" "$@" || die "$name read 1 failed (is the ER-101 in DFU mode and on USB?)"
  dfu_read "$second" "$@" || die "$name read 2 failed"
  if [ "$DRY_RUN" = 1 ]; then return 0; fi
  if ! cmp -s "$first" "$second"; then
    die "$name: the two reads differ ($first vs $second); unstable USB or device, backup not trusted"
  fi
  log "$name: both reads identical, $(wc -c < "$first" | tr -d ' ') bytes of Intel HEX"
}

timestamp() {
  date -u +%Y%m%d-%H%M%S
}

# Interactive gate in front of erase. Reads one line from stdin.
confirm_erase() {
  what=$1
  if [ "$DRY_RUN" = 1 ]; then
    log "(dry run) would ask for confirmation here, then erase and program $what"
    return 0
  fi
  log ""
  log "About to ERASE the program and data flash of the ER-101 (0x80002000-0x8007FFFF)"
  log "and program $what."
  log "The bootloader (0x80000000-0x80001FFF) and the user page are not touched."
  log "Everything on the module is replaced by the image; the backup stays on disk."
  printf 'Type ERASE to continue: '
  answer=
  read -r answer || true
  log "(answer: $answer)"
  [ "$answer" = ERASE ] || die "not confirmed"
}

# Erase, program, read back, compare; the firmware is only started when the
# device holds exactly the image. Usage: program_and_verify IMAGE WORKDIR
program_and_verify() {
  image=$1 work=$2
  dfu erase || die "erase failed; the device is in an unknown state, do not power it off, re-run or restore"
  dfu flash --suppress-bootloader-mem "$image" || die "flash failed; the device holds a partial image, re-run or restore"
  double_read "read-back" "$work/readback.hex" "$work/readback-read2.hex" --force
  if [ "$DRY_RUN" = 1 ]; then
    log "(dry run) would compare the read-back with $image and then launch the firmware"
    return 0
  fi
  host "$PYTHON" -I "$TOOLS_DIR/imagecheck.py" readback "$work/readback.hex" --image "$image" \
    || die "the device does not hold the image; the firmware was NOT started, re-run or restore"
  dfu launch || die "launch failed (the image verified; power-cycle the module to start it)"
}
