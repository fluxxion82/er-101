"""Unit-image logic shared by tools/mkimage.py, tools/imagecheck.py and the flash
scripts (tools/backup.sh, tools/restore.sh, tools/upgrade.sh).

Vocabulary
  firmware hex   the build artifact (release/avr32/er-101-firmware-X.Y.Z.hex);
                 tools/hexcheck.py is the checker for it
  backup         a directory written by tools/backup.sh: two full flash reads,
                 two user-page reads, manifest.json
  unit image     the merged hex that tools/upgrade.sh programs: new code and
                 factory tables from the firmware, user tables and snapshots from
                 the backup, an explicitly migrated meta descriptor
  restore image  the backup's program and data flash, minus the bootloader region

Region ownership (docs/flash-map.md):
  0x80000000-0x80001FFF  bootloader       never written, never part of an image
  0x80002000-0x8001735F  code             firmware
  0x80017360-0x8001767F  tables 0-7       firmware (factory)
  0x80017680-0x80017FDF  tables 8-15      backup (USr1-USr8), factory copy if erased
  0x80017FE0-0x80017FFF  meta descriptor  migrated from the backup (mirrors meta.c)
  0x80018000-0x8007FFFF  snapshots        backup
  0x80800000-0x808001FF  user page        read only (DAC calibration, bootloader words)

dfu-programmer 1.1.0 is the only programmer this was written for. Whether its
`read` output uses absolute addresses (0x80000000-based) or addresses relative
to the start of the memory is UNVERIFIED until the first backup; normalize()
accepts both and the manifest records which one was seen.
"""

import datetime as _dt
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import er101_layout as L  # noqa: E402
from intelhex import HexError, Memory  # noqa: E402

ERASED_PAGE = b"\xff" * L.FLASH_PAGE_SIZE
ERASED_TABLE = b"\xff" * L.REF_TABLE_SIZE
MANIFEST_FORMAT = 1
TARGET = "at32uc3c1512"
BACKUP_FILES = ("flash.hex", "flash-read2.hex", "userpage.hex", "userpage-read2.hex")

# (name, start, end, owner); the bootloader is listed so reports can name it.
REGIONS = (
    ("bootloader", L.BOOTLOADER_START, L.BOOTLOADER_END, "never written"),
    ("code", L.PROGRAM_START, L.CODE_LIMIT, "firmware"),
    ("factory tables 0-7", L.REF_TABLES_ADDRESS, L.USER_TABLES_ADDRESS, "firmware"),
    ("user tables 8-15", L.USER_TABLES_ADDRESS, L.META_ADDRESS, "backup"),
    ("meta descriptor", L.META_ADDRESS, L.PROGRAM_END, "migrated"),
    ("snapshots", L.SNAPSHOT_BASE, L.FLASH_END, "backup"),
)
WRITTEN_REGIONS = REGIONS[1:]


class ImageError(ValueError):
    pass


# -- helpers ------------------------------------------------------------------

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_version(text):
    parts = text.strip().split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ImageError("version %r is not MAJOR.MINOR.PATCH" % text)
    major, minor, patch = (int(p) for p in parts)
    if not (0 <= major <= 255 and 0 <= minor <= 255 and 0 <= patch <= 255):
        raise ImageError("version %r does not fit the one-byte fields of meta_info_t" % text)
    return major, minor, patch


def version_from_env_mk(path=None):
    """FIRMWARE_VERSION from scripts/env.mk, the single source of the version."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "env.mk")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line.startswith("FIRMWARE_VERSION") and "=" in line:
                return parse_version(line.split("=", 1)[1])
    raise ImageError("FIRMWARE_VERSION not found in %s" % path)


def normalize(mem, base, size, what):
    """Map a dfu-programmer read onto absolute addresses.

    Returns (Memory, kind) where kind is "relative" (addresses started at 0 and
    were shifted by base) or "absolute" (addresses already inside the memory).
    Anything else is refused: a read must not be guessed at.
    """
    if mem.is_empty():
        raise ImageError("%s read holds no data" % what)
    lo, hi = mem.min_address(), mem.max_address() + 1
    if hi <= size:
        return mem.shifted(base), "relative"
    if base <= lo and hi <= base + size:
        return mem, "absolute"
    raise ImageError("%s read spans 0x%08X-0x%08X, which is neither 0-0x%X (relative) nor "
                     "0x%08X-0x%08X (absolute)" % (what, lo, hi - 1, size - 1, base, base + size - 1))


def normalize_flash(mem):
    return normalize(mem, L.FLASH_BASE, L.FLASH_SIZE, "flash")


def normalize_userpage(mem):
    return normalize(mem, L.USER_PAGE_ADDRESS, L.USER_PAGE_SIZE, "user page")


def first_difference(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return None


def compare_regions(actual, expected, regions, actual_name, expected_name):
    """Byte-equal over each region, absent bytes reading as erased (0xFF)."""
    failures = []
    for name, start, end, _owner in regions:
        a = actual.get(start, end - start, fill=0xFF)
        b = expected.get(start, end - start, fill=0xFF)
        if a != b:
            i = first_difference(a, b)
            n = sum(1 for x, y in zip(a, b) if x != y)
            failures.append("%s: %s differs from %s at 0x%08X (%d byte%s differ; 0x%02X vs 0x%02X)"
                            % (name, actual_name, expected_name, start + i, n, "" if n == 1 else "s", a[i], b[i]))
    return failures


# -- meta descriptor ----------------------------------------------------------

META_STRUCT = struct.Struct(">IBBBBB")   # magic, major, minor, patch, last_saved_snapshot, start_unpaused


def decode_meta(raw):
    """Decode the 32-byte meta descriptor region (meta_info_t is its first 9 bytes)."""
    if len(raw) != L.META_SIZE:
        raise ImageError("meta descriptor must be %d bytes, got %d" % (L.META_SIZE, len(raw)))
    magic, major, minor, patch, slot, unpaused = META_STRUCT.unpack(raw[:META_STRUCT.size])
    return {
        "erased": raw == b"\xff" * L.META_SIZE,
        "valid": magic == L.META_MAGIC,
        "magic": "0x%08X" % magic,
        "version": "%d.%d.%d" % (major, minor, patch),
        "last_saved_snapshot": slot,
        "start_unpaused": unpaused,
        "spare_bytes": raw[META_STRUCT.size:].hex(),
    }


def migrate_meta(raw, version):
    """What update_meta_info() in src/meta.c would leave in flash on the first boot
    of `version`, computed from the backup's 32 descriptor bytes.

    Mirrors meta.c exactly, including the `minor < 10` rule that resets the last
    saved snapshot slot (finding P4: it ignores the major number, so an upgrade
    from any x.0-x.9 release resets the slot). Bytes the firmware never writes
    (struct padding 9-11 as read, spare bytes 12-31) are preserved. Returns
    (32 bytes, notes).
    """
    major, minor, patch = version
    buf = bytearray(raw)
    magic, cur_major, cur_minor, cur_patch, slot, unpaused = META_STRUCT.unpack(bytes(buf[:META_STRUCT.size]))
    notes = []
    if magic != L.META_MAGIC:
        magic = L.META_MAGIC
        slot = 0
        notes.append("meta descriptor had no valid magic (0x%08X): initialised, last saved snapshot 0"
                     % struct.unpack(">I", raw[:4])[0])
    if cur_major != major:
        notes.append("major %d -> %d" % (cur_major, major))
    if cur_minor != minor:
        if cur_minor < 10:
            if slot != 0:
                notes.append("minor %d < 10: last saved snapshot %d reset to 0 (meta.c rule, P4)" % (cur_minor, slot))
            slot = 0
        notes.append("minor %d -> %d" % (cur_minor, minor))
    if cur_patch != patch:
        notes.append("patch %d -> %d" % (cur_patch, patch))
    if slot > L.NUM_SNAPSHOT_SLOTS - 1:
        notes.append("last saved snapshot %d > 15 reset to 0" % slot)
        slot = 0
    buf[:META_STRUCT.size] = META_STRUCT.pack(magic, major, minor, patch, slot, unpaused)
    return bytes(buf), notes


# -- backups ------------------------------------------------------------------

class Backup:
    """A verified backup directory, parsed and normalized."""

    def __init__(self, directory):
        self.directory = directory
        self.flash = None            # Memory, absolute addresses
        self.userpage = None         # 512 bytes
        self.flash_base = None       # "relative" | "absolute"
        self.userpage_base = None
        self.unit_id = None
        self.problems = []

    @property
    def manifest_path(self):
        return os.path.join(self.directory, "manifest.json")


def load_backup(directory):
    """Parse and check a backup directory. Raises ImageError on anything that
    makes the backup unusable; the returned Backup is complete."""
    b = Backup(directory)
    for name in BACKUP_FILES:
        if not os.path.isfile(os.path.join(directory, name)):
            raise ImageError("backup %s is missing %s" % (directory, name))
    for first, second in (("flash.hex", "flash-read2.hex"), ("userpage.hex", "userpage-read2.hex")):
        if sha256_file(os.path.join(directory, first)) != sha256_file(os.path.join(directory, second)):
            raise ImageError("%s and %s differ: the two reads did not agree, backup unusable" % (first, second))
    try:
        flash_raw = Memory.from_hex_file(os.path.join(directory, "flash.hex"))
        user_raw = Memory.from_hex_file(os.path.join(directory, "userpage.hex"))
    except HexError as e:
        raise ImageError("backup hex rejected: %s" % e)
    b.flash, b.flash_base = normalize_flash(flash_raw)
    userpage, b.userpage_base = normalize_userpage(user_raw)
    if not b.flash.covers(L.PROGRAM_START, L.FLASH_END):
        gaps = []
        a = L.PROGRAM_START
        for s, buf in b.flash.slice(L.PROGRAM_START, L.FLASH_END).segments():
            if s > a:
                gaps.append("0x%08X-0x%08X" % (a, s - 1))
            a = s + len(buf)
        if a < L.FLASH_END:
            gaps.append("0x%08X-0x%08X" % (a, L.FLASH_END - 1))
        raise ImageError("flash read does not cover the program and data flash 0x%08X-0x%08X (missing %s); "
                         "was it taken with `read --force`?" % (L.PROGRAM_START, L.FLASH_END - 1, ", ".join(gaps)))
    if not userpage.covers(L.USER_PAGE_ADDRESS, L.USER_PAGE_ADDRESS + L.USER_PAGE_SIZE):
        raise ImageError("user page read does not cover all %d bytes" % L.USER_PAGE_SIZE)
    b.userpage = userpage.get(L.USER_PAGE_ADDRESS, L.USER_PAGE_SIZE)
    b.unit_id = sha256_bytes(b.userpage)
    return b


def unit_id_from_userpage_hex(path):
    """The unit id (sha256 of the 512 user-page bytes) from one read."""
    mem, kind = normalize_userpage(Memory.from_hex_file(path))
    if not mem.covers(L.USER_PAGE_ADDRESS, L.USER_PAGE_ADDRESS + L.USER_PAGE_SIZE):
        raise ImageError("user page read does not cover all %d bytes" % L.USER_PAGE_SIZE)
    return sha256_bytes(mem.get(L.USER_PAGE_ADDRESS, L.USER_PAGE_SIZE)), kind


def snapshot_classes(flash):
    out = {}
    for slot in range(L.NUM_SNAPSHOT_SLOTS):
        start = L.SNAPSHOT_BASE + slot * L.SNAPSHOT_SIZE
        data = flash.get(start, L.SNAPSHOT_SIZE, fill=0xFF)
        if data == b"\xff" * L.SNAPSHOT_SIZE:
            out[slot] = "erased"
        elif struct.unpack(">I", data[:4])[0] == L.SEQ_MAGIC:
            out[slot] = "saved"
        else:
            out[slot] = "unrecognised"
    return out


def user_table_classes(flash):
    out = {}
    factory = struct.pack(">%dH" % L.VOLTAGES_PER_TABLE, *L.SEMITONE_ROW)
    for t in range(L.FIRST_USER_TABLE, L.NUM_REF_TABLES):
        data = flash.get(L.REF_TABLES_ADDRESS + t * L.REF_TABLE_SIZE, L.REF_TABLE_SIZE, fill=0xFF)
        out[t] = "erased" if data == ERASED_TABLE else ("factory" if data == factory else "user")
    return out


def dac_bits(userpage):
    off = L.DAC_DESCRIPTOR_ADDRESS - L.USER_PAGE_ADDRESS
    magic, nbits = struct.unpack(">II", userpage[off:off + 8])
    return nbits if magic == L.DAC_DESCRIPTOR_MAGIC else None


def describe_backup(b, dfu_info=None):
    """The manifest dictionary for a loaded backup."""
    return {
        "format": MANIFEST_FORMAT,
        "created_utc": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "target": TARGET,
        "dfu_programmer": dfu_info,
        "unit_id": b.unit_id,
        "unit_id_is": "sha256 of the 512 user-page bytes (per-unit DAC calibration)",
        "address_base": {"flash": b.flash_base, "userpage": b.userpage_base},
        "bootloader_region_in_read": b.flash.has_any(L.BOOTLOADER_START, L.BOOTLOADER_END),
        "files": {name: sha256_file(os.path.join(b.directory, name)) for name in BACKUP_FILES},
        "meta": decode_meta(b.flash.get(L.META_ADDRESS, L.META_SIZE, fill=0xFF)),
        "user_tables": {str(k): v for k, v in user_table_classes(b.flash).items()},
        "snapshots": {str(k): v for k, v in snapshot_classes(b.flash).items()},
        "dac_bits": dac_bits(b.userpage),
    }


def write_manifest(b, dfu_info=None):
    manifest = describe_backup(b, dfu_info)
    with open(b.manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")
    return manifest


def verify_manifest(b):
    """The manifest must match the files and the unit id. Returns the manifest."""
    if not os.path.isfile(b.manifest_path):
        raise ImageError("backup %s has no manifest.json; it was not completed by tools/backup.sh" % b.directory)
    with open(b.manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    if manifest.get("format") != MANIFEST_FORMAT:
        raise ImageError("manifest format %r is not %d" % (manifest.get("format"), MANIFEST_FORMAT))
    for name in BACKUP_FILES:
        want = manifest.get("files", {}).get(name)
        have = sha256_file(os.path.join(b.directory, name))
        if want != have:
            raise ImageError("%s does not match its manifest hash (file changed since the backup?)" % name)
    if manifest.get("unit_id") != b.unit_id:
        raise ImageError("manifest unit id %s does not match the user page in the backup (%s)"
                         % (manifest.get("unit_id"), b.unit_id))
    return manifest


# -- images -------------------------------------------------------------------

def put_pages(image, source, start, end):
    """Copy [start, end) page by page, leaving erased pages out of the image."""
    for a in range(start, end, L.FLASH_PAGE_SIZE):
        page = source.get(a, L.FLASH_PAGE_SIZE, fill=0xFF)
        if page != ERASED_PAGE:
            image.put(a, page)


def check_firmware_for_image(fw):
    """The little hexcheck does not do: the firmware must bring everything the
    image takes from it. tools/hexcheck.py remains the real check."""
    if not fw.covers(L.PROGRAM_START, L.PROGRAM_START + 8):
        raise ImageError("firmware hex has no code at 0x%08X" % L.PROGRAM_START)
    if not fw.covers(L.REF_TABLES_ADDRESS, L.META_ADDRESS):
        raise ImageError("firmware hex does not carry all 16 reference tables at 0x%08X" % L.REF_TABLES_ADDRESS)
    if fw.has_any(L.META_ADDRESS, 1 << 33):
        raise ImageError("firmware hex has bytes at or beyond the meta descriptor 0x%08X" % L.META_ADDRESS)
    if fw.has_any(0, L.FLASH_BASE):
        raise ImageError("firmware hex has bytes below flash")


def expected_user_table(backup_flash, fw, table):
    """Backup copy, or the firmware's factory copy when the backup's is erased."""
    a = L.REF_TABLES_ADDRESS + table * L.REF_TABLE_SIZE
    saved = backup_flash.get(a, L.REF_TABLE_SIZE, fill=0xFF)
    if saved == ERASED_TABLE:
        return fw.get(a, L.REF_TABLE_SIZE), "erased"
    return saved, "preserved"


def build_upgrade_image(fw, backup_flash, version):
    """The unit image: (Memory, report lines)."""
    check_firmware_for_image(fw)
    image = Memory()
    report = []
    dropped = fw.slice(L.BOOTLOADER_START, L.BOOTLOADER_END)
    if not dropped.is_empty():
        report.append("firmware: %d byte(s) in the bootloader region left out (never written)" % len(dropped))
    code = fw.slice(L.PROGRAM_START, L.CODE_LIMIT)
    for s, buf in code.segments():
        image.put(s, buf)
    report.append("code: %d bytes from the firmware, 0x%08X-0x%08X" % (len(code), code.min_address(), code.max_address()))
    image.put(L.REF_TABLES_ADDRESS, fw.get(L.REF_TABLES_ADDRESS, L.FIRST_USER_TABLE * L.REF_TABLE_SIZE))
    report.append("tables 0-7: factory copies from the firmware")
    for t in range(L.FIRST_USER_TABLE, L.NUM_REF_TABLES):
        data, how = expected_user_table(backup_flash, fw, t)
        image.put(L.REF_TABLES_ADDRESS + t * L.REF_TABLE_SIZE, data)
        if how == "erased":
            report.append("table %d (USr%d): erased in the backup, factory semitone copy used" % (t, t - L.FIRST_USER_TABLE + 1))
        else:
            report.append("table %d (USr%d): preserved from the backup" % (t, t - L.FIRST_USER_TABLE + 1))
    raw = backup_flash.get(L.META_ADDRESS, L.META_SIZE, fill=0xFF)
    meta, notes = migrate_meta(raw, version)
    image.put(L.META_ADDRESS, meta)
    before, after = decode_meta(raw), decode_meta(meta)
    report.append("meta: %s slot %d -> %s slot %d%s" % (
        before["version"] if before["valid"] else "no magic", before["last_saved_snapshot"],
        after["version"], after["last_saved_snapshot"], ("; " + "; ".join(notes)) if notes else ""))
    put_pages(image, backup_flash, L.SNAPSHOT_BASE, L.FLASH_END)
    classes = snapshot_classes(backup_flash)
    report.append("snapshots: %d saved, %d erased, %d unrecognised, all preserved from the backup" % (
        sum(1 for v in classes.values() if v == "saved"), sum(1 for v in classes.values() if v == "erased"),
        sum(1 for v in classes.values() if v == "unrecognised")))
    return image, report


def build_restore_image(backup_flash):
    """The backup's program and data flash as one image; the bootloader region is left out."""
    image = Memory()
    put_pages(image, backup_flash, L.PROGRAM_START, L.FLASH_END)
    report = ["restore image: %d bytes from the backup over 0x%08X-0x%08X (erased pages left out)"
              % (len(image), L.PROGRAM_START, L.FLASH_END - 1)]
    return image, report


def check_image(image, backup_flash, fw=None, version=None):
    """Region ownership. With fw None the image is a restore image (all from the
    backup). Returns a list of failures; empty means the image is right."""
    failures = []
    if image.has_any(0, L.PROGRAM_START):
        failures.append("image holds %d byte(s) below 0x%08X (bootloader region, never written)"
                        % (len(image.slice(0, L.PROGRAM_START)), L.PROGRAM_START))
    if image.has_any(L.FLASH_END, 1 << 33):
        failures.append("image holds %d byte(s) at or beyond 0x%08X (user page or outside flash)"
                        % (len(image.slice(L.FLASH_END, 1 << 33)), L.FLASH_END))
    if fw is None:
        failures += compare_regions(image, backup_flash, WRITTEN_REGIONS, "image", "backup")
        return failures
    if version is None:
        raise ImageError("check_image needs the firmware version to recompute the meta migration")
    try:
        check_firmware_for_image(fw)
    except ImageError as e:
        failures.append(str(e))
        return failures
    failures += compare_regions(image, fw, REGIONS[1:3], "image", "firmware")
    for t in range(L.FIRST_USER_TABLE, L.NUM_REF_TABLES):
        a = L.REF_TABLES_ADDRESS + t * L.REF_TABLE_SIZE
        want, how = expected_user_table(backup_flash, fw, t)
        have = image.get(a, L.REF_TABLE_SIZE, fill=0xFF)
        if have != want:
            failures.append("user table %d at 0x%08X is not the %s copy" % (t, a, "backup" if how == "preserved" else "factory"))
    want_meta, _notes = migrate_meta(backup_flash.get(L.META_ADDRESS, L.META_SIZE, fill=0xFF), version)
    have_meta = image.get(L.META_ADDRESS, L.META_SIZE, fill=0xFF)
    if have_meta != want_meta:
        failures.append("meta descriptor is not the migration's output (%s vs %s)" % (have_meta.hex(), want_meta.hex()))
    failures += compare_regions(image, backup_flash, REGIONS[5:], "image", "backup")
    return failures


def compare_readback(readback, image):
    """After programming: the device must hold exactly the image over every written
    region (absent bytes erased on both sides). The bootloader region is ignored."""
    failures = []
    if not readback.covers(L.PROGRAM_START, L.FLASH_END):
        failures.append("read-back does not cover 0x%08X-0x%08X; was it taken with `read --force`?"
                        % (L.PROGRAM_START, L.FLASH_END - 1))
    failures += compare_regions(readback, image, WRITTEN_REGIONS, "device", "image")
    return failures


def compare_live_with_backup(live, backup_flash):
    """Before an upgrade: the device must still hold what the backup holds, or the
    backup is stale and the user data it would carry forward is not current."""
    return compare_regions(live, backup_flash, WRITTEN_REGIONS, "device", "backup")


def region_table(mem):
    lines = []
    for s, buf in mem.segments():
        lines.append("  0x%08X-0x%08X %7d bytes  %s" % (s, s + len(buf) - 1, len(buf), L.region_name(s)))
    return lines
