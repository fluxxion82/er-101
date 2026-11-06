"""Host tests for the flash tooling (plan item A9), on synthetic data only.

    make test-tools
    python3 -I -m unittest discover -s tests/tools -p 'test_*.py' -v

Covers tools/intelhex.py, tools/er101_flash.py, tools/mkimage.py,
tools/imagecheck.py, tools/hexcheck.py and the three shell scripts driven
against tests/tools/fake_dfu_programmer.py. No real device is involved; the
dfu-programmer address base and the DFU entry procedure stay UNVERIFIED until
the first bench session.
"""

import json
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
TOOLS = os.path.join(REPO, "tools")
sys.path.insert(0, TOOLS)

import er101_flash as F  # noqa: E402
import er101_layout as L  # noqa: E402
import hexcheck  # noqa: E402
import intelhex  # noqa: E402
from intelhex import Memory  # noqa: E402

FAKE_DFU = os.path.join(HERE, "fake_dfu_programmer.py")
PYTHON = sys.executable
NEW_VERSION = (3, 0, 0)
FACTORY_TABLE = struct.pack(">%dH" % L.VOLTAGES_PER_TABLE, *L.SEMITONE_ROW)


def rnd(n, seed):
    r = random.Random(seed)
    return bytes(r.randrange(256) for _ in range(n))


def synthetic_firmware(seed=1, code_size=0x9000):
    """A hex that passes tools/hexcheck.py: trampoline, jump to _stext, code, 16 tables."""
    fw = Memory()
    fw.put(L.FLASH_BASE, hexcheck.TRAMPOLINE_BRANCH + b"\x00" * (L.BOOTLOADER_END - L.FLASH_BASE - 4))
    stext = L.PROGRAM_START + 0x10
    fw.put(L.PROGRAM_START, hexcheck.LOAD_PC_FROM_NEXT_WORD + struct.pack(">I", stext) + b"\x00" * 8)
    fw.put(stext, rnd(code_size, seed))
    tables = bytearray()
    for t in range(L.NUM_REF_TABLES):
        if t == 0 or t >= L.FIRST_USER_TABLE:
            tables += FACTORY_TABLE
        else:
            tables += struct.pack(">%dH" % L.VOLTAGES_PER_TABLE, *[min(65535, i * (500 + 37 * t)) for i in range(100)])
    fw.put(L.REF_TABLES_ADDRESS, bytes(tables))
    return fw


def meta_bytes(version=(2, 10, 0), slot=5, unpaused=0, magic=L.META_MAGIC, spare=None):
    head = struct.pack(">IBBBBB", magic, version[0], version[1], version[2], slot, unpaused)
    spare = spare if spare is not None else b"\xff" * (L.META_SIZE - len(head))
    return head + spare


def synthetic_device(seed=2, meta=None, user_tables=None, saved_slots=(1, 2, 15)):
    """(flash bytes, user page bytes) of a module running an old firmware."""
    flash = bytearray(b"\xff" * L.FLASH_SIZE)
    flash[0:0x2000] = rnd(0x2000, seed + 100)                      # some bootloader
    old = synthetic_firmware(seed=seed + 1, code_size=0x7000)
    for s, buf in old.slice(L.PROGRAM_START, L.META_ADDRESS).segments():
        flash[s - L.FLASH_BASE:s - L.FLASH_BASE + len(buf)] = buf
    if user_tables is None:
        user_tables = {8: rnd(200, 8), 9: rnd(200, 9), 10: b"\xff" * 200, 15: rnd(200, 15)}
    for t, data in user_tables.items():
        a = L.REF_TABLES_ADDRESS + t * L.REF_TABLE_SIZE - L.FLASH_BASE
        flash[a:a + 200] = data
    meta = meta if meta is not None else meta_bytes()
    flash[L.META_ADDRESS - L.FLASH_BASE:L.META_ADDRESS - L.FLASH_BASE + 32] = meta
    for slot in saved_slots:
        a = L.SNAPSHOT_BASE + slot * L.SNAPSHOT_SIZE - L.FLASH_BASE
        body = struct.pack(">I", L.SEQ_MAGIC) + rnd(L.SEQUENCER_DATA_SIZE - 4, 1000 + slot)
        flash[a:a + len(body)] = body
    userpage = bytearray(rnd(L.USER_PAGE_SIZE, seed + 7))
    off = L.DAC_DESCRIPTOR_ADDRESS - L.USER_PAGE_ADDRESS
    userpage[off:off + 8] = struct.pack(">II", L.DAC_DESCRIPTOR_MAGIC, 16)
    return bytes(flash), bytes(userpage)


def flip_byte(mem, addr):
    """A copy of mem with one byte changed (Memory.put refuses overlaps)."""
    out = Memory.from_segments(mem.segments())
    value = out.get(addr, 1)[0]
    out.remove_range(addr, addr + 1)
    out.put(addr, bytes([value ^ 0x5A]))
    return out


def memory_from_bytes(data, base, skip_erased=False):
    mem = Memory()
    for off in range(0, len(data), L.FLASH_PAGE_SIZE):
        chunk = data[off:off + L.FLASH_PAGE_SIZE]
        if skip_erased and chunk == b"\xff" * len(chunk):
            continue
        mem.put(base + off, chunk)
    return mem


class ParserTests(unittest.TestCase):
    def test_selftest(self):
        intelhex.selftest()


class NormalizeTests(unittest.TestCase):
    def test_relative_flash_is_shifted(self):
        mem, kind = F.normalize_flash(memory_from_bytes(b"\x01" * 1024, 0))
        self.assertEqual(kind, "relative")
        self.assertEqual(mem.min_address(), L.FLASH_BASE)

    def test_absolute_flash_is_kept(self):
        mem, kind = F.normalize_flash(memory_from_bytes(b"\x01" * 1024, L.FLASH_BASE + 0x2000))
        self.assertEqual(kind, "absolute")
        self.assertEqual(mem.min_address(), L.FLASH_BASE + 0x2000)

    def test_mixed_or_foreign_addresses_refused(self):
        with self.assertRaises(F.ImageError):
            F.normalize_flash(memory_from_bytes(b"\x01" * 1024, 0x40000000))
        with self.assertRaises(F.ImageError):
            F.normalize_flash(Memory())

    def test_userpage(self):
        mem, kind = F.normalize_userpage(memory_from_bytes(b"\x02" * 512, 0))
        self.assertEqual((kind, mem.min_address()), ("relative", L.USER_PAGE_ADDRESS))
        mem, kind = F.normalize_userpage(memory_from_bytes(b"\x02" * 512, L.USER_PAGE_ADDRESS))
        self.assertEqual(kind, "absolute")
        with self.assertRaises(F.ImageError):
            F.normalize_userpage(memory_from_bytes(b"\x02" * 1024, L.USER_PAGE_ADDRESS))


class MetaMigrationTests(unittest.TestCase):
    def test_from_2_10_keeps_slot(self):
        out, notes = F.migrate_meta(meta_bytes((2, 10, 0), slot=5, unpaused=1), NEW_VERSION)
        d = F.decode_meta(out)
        self.assertEqual((d["version"], d["last_saved_snapshot"], d["start_unpaused"]), ("3.0.0", 5, 1))
        self.assertTrue(d["valid"])

    def test_from_2_09_resets_slot(self):
        out, notes = F.migrate_meta(meta_bytes((2, 9, 0), slot=5), NEW_VERSION)
        self.assertEqual(F.decode_meta(out)["last_saved_snapshot"], 0)
        self.assertTrue(any("P4" in n for n in notes))

    def test_same_version_unchanged(self):
        raw = meta_bytes((3, 0, 0), slot=7, unpaused=0, spare=rnd(23, 3))
        out, notes = F.migrate_meta(raw, NEW_VERSION)
        self.assertEqual(out, raw)
        self.assertEqual(notes, [])

    def test_erased_descriptor_initialised(self):
        out, notes = F.migrate_meta(b"\xff" * 32, NEW_VERSION)
        d = F.decode_meta(out)
        self.assertTrue(d["valid"])
        self.assertEqual((d["version"], d["last_saved_snapshot"]), ("3.0.0", 0))
        self.assertEqual(d["start_unpaused"], 0xFF)   # meta.c leaves it as read (P4)
        self.assertEqual(out[9:], b"\xff" * 23)

    def test_spare_bytes_preserved(self):
        spare = rnd(23, 4)
        out, _ = F.migrate_meta(meta_bytes((2, 10, 0), slot=3, spare=spare), NEW_VERSION)
        self.assertEqual(out[9:], spare)

    def test_slot_above_15_reset(self):
        out, _ = F.migrate_meta(meta_bytes((2, 10, 0), slot=200), NEW_VERSION)
        self.assertEqual(F.decode_meta(out)["last_saved_snapshot"], 0)


class ImageBuildTests(unittest.TestCase):
    def setUp(self):
        self.fw = synthetic_firmware()
        self.assertTrue(hexcheck_passes(self.fw))
        flash, self.userpage = synthetic_device()
        self.backup_flash = memory_from_bytes(flash, L.FLASH_BASE)

    def test_upgrade_image_region_ownership(self):
        image, report = F.build_upgrade_image(self.fw, self.backup_flash, NEW_VERSION)
        self.assertEqual(F.check_image(image, self.backup_flash, self.fw, NEW_VERSION), [])
        self.assertFalse(image.has_any(0, L.PROGRAM_START))
        self.assertFalse(image.has_any(L.FLASH_END, 1 << 33))
        code = L.CODE_LIMIT - L.PROGRAM_START
        self.assertEqual(image.get(L.PROGRAM_START, code, fill=0xFF), self.fw.get(L.PROGRAM_START, code, fill=0xFF))
        self.assertEqual(image.get(L.REF_TABLES_ADDRESS, 1600), self.fw.get(L.REF_TABLES_ADDRESS, 1600))
        for t, expect_backup in ((8, True), (9, True), (10, False), (11, True), (15, True)):
            a = L.REF_TABLES_ADDRESS + t * 200
            want = self.backup_flash.get(a, 200) if expect_backup else self.fw.get(a, 200)
            self.assertEqual(image.get(a, 200), want, "table %d" % t)
        self.assertEqual(image.get(a, 200), self.backup_flash.get(a, 200))
        self.assertTrue(any("USr3" in r and "erased" in r for r in report))
        meta = F.decode_meta(image.get(L.META_ADDRESS, 32))
        self.assertEqual((meta["version"], meta["last_saved_snapshot"]), ("3.0.0", 5))
        snaps = L.FLASH_END - L.SNAPSHOT_BASE
        self.assertEqual(image.get(L.SNAPSHOT_BASE, snaps, fill=0xFF), self.backup_flash.get(L.SNAPSHOT_BASE, snaps, fill=0xFF))
        self.assertLess(len(image), 0x9000 + 0x1000 + 3200 + 32 + 3 * L.SNAPSHOT_SIZE)   # erased pages left out

    def test_check_image_catches_each_region(self):
        image, _ = F.build_upgrade_image(self.fw, self.backup_flash, NEW_VERSION)
        cases = {
            "code": L.PROGRAM_START + 0x100,
            "factory tables 0-7": L.REF_TABLES_ADDRESS + 10,
            "user table 8": L.REF_TABLES_ADDRESS + 8 * 200 + 3,
            "meta descriptor": L.META_ADDRESS + 5,
            "snapshots": L.SNAPSHOT_BASE + L.SNAPSHOT_SIZE + 40,
        }
        for name, addr in cases.items():
            bad = flip_byte(image, addr)
            failures = F.check_image(bad, self.backup_flash, self.fw, NEW_VERSION)
            self.assertTrue(any(name in f for f in failures), "%s: %r" % (name, failures))
        bad = Memory.from_segments(image.segments())
        bad.put(L.FLASH_BASE, b"\x01\x02")
        self.assertTrue(any("bootloader" in f for f in F.check_image(bad, self.backup_flash, self.fw, NEW_VERSION)))
        bad = Memory.from_segments(image.segments())
        bad.put(L.USER_PAGE_ADDRESS, b"\x01\x02")
        self.assertTrue(any("user page" in f for f in F.check_image(bad, self.backup_flash, self.fw, NEW_VERSION)))

    def test_wrong_version_in_meta_is_caught(self):
        image, _ = F.build_upgrade_image(self.fw, self.backup_flash, (3, 1, 0))
        self.assertTrue(F.check_image(image, self.backup_flash, self.fw, NEW_VERSION))

    def test_restore_image(self):
        image, _ = F.build_restore_image(self.backup_flash)
        self.assertEqual(F.check_image(image, self.backup_flash), [])
        self.assertFalse(image.has_any(0, L.PROGRAM_START))
        span = L.FLASH_END - L.PROGRAM_START
        self.assertEqual(image.get(L.PROGRAM_START, span, fill=0xFF), self.backup_flash.get(L.PROGRAM_START, span))

    def test_readback_compare(self):
        image, _ = F.build_upgrade_image(self.fw, self.backup_flash, NEW_VERSION)
        device = Memory.from_segments(image.segments())
        device.put(L.FLASH_BASE, rnd(0x2000, 9))      # a different bootloader is fine
        full = memory_from_bytes(device.get(L.FLASH_BASE, L.FLASH_SIZE, fill=0xFF), L.FLASH_BASE)
        self.assertEqual(F.compare_readback(full, image), [])
        broken = bytearray(full.get(L.FLASH_BASE, L.FLASH_SIZE))
        broken[L.SNAPSHOT_BASE + 100 - L.FLASH_BASE] ^= 1
        self.assertTrue(F.compare_readback(memory_from_bytes(bytes(broken), L.FLASH_BASE), image))
        partial = full.slice(L.PROGRAM_START, L.SNAPSHOT_BASE)
        self.assertTrue(any("cover" in f for f in F.compare_readback(partial, image)))

    def test_firmware_without_tables_refused(self):
        fw = self.fw.slice(0, L.REF_TABLES_ADDRESS + 100)
        with self.assertRaises(F.ImageError):
            F.build_upgrade_image(fw, self.backup_flash, NEW_VERSION)


def hexcheck_passes(fw):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "fw.hex")
        fw.write_hex_file(p)
        return hexcheck.check(p, verbose=False)


class HexcheckOnSyntheticTests(unittest.TestCase):
    def test_synthetic_firmware_passes(self):
        self.assertTrue(hexcheck_passes(synthetic_firmware()))

    def test_tables_moved_fails(self):
        fw = synthetic_firmware()
        tables = fw.get(L.REF_TABLES_ADDRESS, 3200)
        fw.remove_range(L.REF_TABLES_ADDRESS, L.META_ADDRESS)
        fw.put(L.REF_TABLES_ADDRESS - 0x4000, tables)
        self.assertFalse(hexcheck_passes(fw))


class ScriptFlowTests(unittest.TestCase):
    """The three scripts against the fake programmer."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="er101-tools-test.")
        self.state = os.path.join(self.tmp, "device")
        os.mkdir(self.state)
        self.flash, self.userpage = synthetic_device()
        self.write_device(self.flash, self.userpage)
        self.fw = synthetic_firmware()
        self.fw_path = os.path.join(self.tmp, "er-101-firmware-3.0.0.hex")
        self.fw.write_hex_file(self.fw_path)
        self.env = dict(os.environ, DFU_PROGRAMMER=FAKE_DFU, FAKE_DFU_STATE=self.state, PYTHON=PYTHON)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write_device(self, flash, userpage, config=None):
        with open(os.path.join(self.state, "flash.bin"), "wb") as f:
            f.write(flash)
        with open(os.path.join(self.state, "userpage.bin"), "wb") as f:
            f.write(userpage)
        if config is not None:
            with open(os.path.join(self.state, "config.json"), "w") as f:
                json.dump(config, f)

    def device_flash(self):
        with open(os.path.join(self.state, "flash.bin"), "rb") as f:
            return f.read()

    def log(self):
        p = os.path.join(self.state, "log")
        if not os.path.isfile(p):
            return ""
        with open(p) as f:
            return f.read()

    def run_script(self, name, *args, stdin="ERASE\n"):
        proc = subprocess.run([os.path.join(TOOLS, name)] + list(args), input=stdin, text=True,
                              capture_output=True, env=self.env, cwd=self.tmp)
        return proc

    def backup(self):
        out = os.path.join(self.tmp, "backup")
        proc = self.run_script("backup.sh", "--out", out)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return out

    def test_backup_writes_manifest_and_unit_id(self):
        out = self.backup()
        with open(os.path.join(out, "manifest.json")) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["unit_id"], F.sha256_bytes(self.userpage))
        self.assertEqual(manifest["address_base"], {"flash": "relative", "userpage": "relative"})
        self.assertEqual(manifest["meta"]["version"], "2.10.0")
        self.assertEqual(manifest["snapshots"]["1"], "saved")
        self.assertEqual(manifest["snapshots"]["3"], "erased")
        self.assertEqual(manifest["user_tables"]["10"], "erased")
        self.assertEqual(manifest["user_tables"]["8"], "user")
        self.assertEqual(manifest["dac_bits"], 16)
        self.assertFalse(os.path.exists(os.path.join(out, "INCOMPLETE")))
        self.assertNotIn("## ERASED", self.log())
        self.assertNotIn("## FLASHED", self.log())
        for name in F.BACKUP_FILES:
            self.assertTrue(os.path.isfile(os.path.join(out, name)))
        with open(os.path.join(out, "flash.hex")) as f:
            self.assertTrue(f.readline().startswith(":"))

    def test_backup_with_absolute_addresses(self):
        self.write_device(self.flash, self.userpage, {"address_base": "absolute"})
        out = self.backup()
        with open(os.path.join(out, "manifest.json")) as f:
            manifest = json.load(f)
        self.assertEqual(manifest["address_base"], {"flash": "absolute", "userpage": "absolute"})
        self.assertEqual(manifest["unit_id"], F.sha256_bytes(self.userpage))

    def test_backup_refuses_unstable_reads(self):
        self.write_device(self.flash, self.userpage, {"glitch_read": 2})
        out = os.path.join(self.tmp, "backup")
        proc = self.run_script("backup.sh", "--out", out)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("two reads differ", proc.stdout)
        self.assertTrue(os.path.exists(os.path.join(out, "INCOMPLETE")))
        self.assertFalse(os.path.exists(os.path.join(out, "manifest.json")))

    def test_backup_refuses_other_programmer_version(self):
        self.write_device(self.flash, self.userpage, {"version_string": "dfu-programmer 0.7.2"})
        proc = self.run_script("backup.sh", "--out", os.path.join(self.tmp, "b"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("1.1.0", proc.stdout)
        self.assertNotIn("read", self.log())

    def test_restore_refuses_without_manifest(self):
        out = self.backup()
        os.remove(os.path.join(out, "manifest.json"))
        proc = self.run_script("restore.sh", "--backup", out)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("## ERASED", self.log())

    def test_restore_refuses_tampered_backup(self):
        out = self.backup()
        with open(os.path.join(out, "flash.hex"), "a") as f:
            f.write(":00000001FF\n")
        proc = self.run_script("restore.sh", "--backup", out)
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("## ERASED", self.log())

    def test_restore_refuses_other_unit(self):
        out = self.backup()
        other = bytearray(self.userpage)
        other[0] ^= 0xFF
        self.write_device(self.flash, bytes(other))
        proc = self.run_script("restore.sh", "--backup", out)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not the unit", proc.stdout)
        self.assertNotIn("## ERASED", self.log())

    def test_restore_refuses_without_confirmation(self):
        out = self.backup()
        proc = self.run_script("restore.sh", "--backup", out, stdin="no\n")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not confirmed", proc.stdout)
        self.assertNotIn("## ERASED", self.log())

    def test_restore_rehearsal_leaves_device_identical(self):
        out = self.backup()
        before = self.device_flash()
        proc = self.run_script("restore.sh", "--backup", out)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        log = self.log()
        self.assertEqual(log.count("## ERASED"), 1)
        self.assertEqual(log.count("## FLASHED"), 1)
        self.assertLess(log.index("## ERASED"), log.index("## FLASHED"))
        self.assertLess(log.index("## FLASHED"), log.index("## LAUNCHED"))
        self.assertIn("flash --suppress-bootloader-mem", log)
        self.assertEqual(self.device_flash(), before)
        work = [d for d in os.listdir(out) if d.startswith("restore-")]
        self.assertEqual(len(work), 1)
        self.assertTrue(os.path.isfile(os.path.join(out, work[0], "readback.hex")))

    def test_upgrade_preserves_user_data(self):
        out = self.backup()
        proc = self.run_script("upgrade.sh", "--backup", out, "--firmware", self.fw_path)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        after = memory_from_bytes(self.device_flash(), L.FLASH_BASE)
        before = memory_from_bytes(self.flash, L.FLASH_BASE)
        self.assertEqual(after.get(L.FLASH_BASE, 0x2000), self.flash[:0x2000])           # bootloader untouched
        code = L.CODE_LIMIT - L.PROGRAM_START
        self.assertEqual(after.get(L.PROGRAM_START, code), self.fw.get(L.PROGRAM_START, code, fill=0xFF))
        self.assertEqual(after.get(L.REF_TABLES_ADDRESS, 1600), self.fw.get(L.REF_TABLES_ADDRESS, 1600))
        self.assertEqual(after.get(L.REF_TABLES_ADDRESS + 8 * 200, 200), before.get(L.REF_TABLES_ADDRESS + 8 * 200, 200))
        self.assertEqual(after.get(L.REF_TABLES_ADDRESS + 10 * 200, 200), FACTORY_TABLE)   # erased -> factory
        meta = F.decode_meta(after.get(L.META_ADDRESS, 32))
        self.assertEqual((meta["version"], meta["last_saved_snapshot"]), ("3.0.0", 5))
        snaps = L.FLASH_END - L.SNAPSHOT_BASE
        self.assertEqual(after.get(L.SNAPSHOT_BASE, snaps), before.get(L.SNAPSHOT_BASE, snaps))
        log = self.log()
        self.assertEqual(log.count("## ERASED"), 1)
        self.assertEqual(log.count("## FLASHED"), 1)
        self.assertLess(log.index("## FLASHED"), log.index("## LAUNCHED"))

    def test_upgrade_refuses_stale_backup(self):
        out = self.backup()
        changed = bytearray(self.flash)
        a = L.SNAPSHOT_BASE + 4 * L.SNAPSHOT_SIZE - L.FLASH_BASE
        changed[a:a + 4] = struct.pack(">I", L.SEQ_MAGIC)          # the user saved a snapshot after the backup
        self.write_device(bytes(changed), self.userpage)
        proc = self.run_script("upgrade.sh", "--backup", out, "--firmware", self.fw_path)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no longer matches the backup", proc.stdout)
        self.assertNotIn("## ERASED", self.log())

    def test_upgrade_refuses_bad_firmware(self):
        out = self.backup()
        bad = synthetic_firmware()
        tables = bad.get(L.REF_TABLES_ADDRESS, 3200)
        bad.remove_range(L.REF_TABLES_ADDRESS, L.META_ADDRESS)
        bad.put(L.REF_TABLES_ADDRESS - 0x4000, tables)             # the upstream Makefile defect B1
        bad_path = os.path.join(self.tmp, "bad-3.0.0.hex")
        bad.write_hex_file(bad_path)
        proc = self.run_script("upgrade.sh", "--backup", out, "--firmware", bad_path)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("hexcheck", proc.stdout)
        self.assertNotIn("## ERASED", self.log())

    def test_readback_mismatch_does_not_launch(self):
        out = self.backup()
        self.write_device(self.flash, self.userpage, {"glitch_read": 9})   # a read after programming
        proc = self.run_script("upgrade.sh", "--backup", out, "--firmware", self.fw_path)
        self.assertNotEqual(proc.returncode, 0)
        log = self.log()
        self.assertIn("## FLASHED", log)
        self.assertNotIn("## LAUNCHED", log)

    def test_dry_runs_touch_nothing(self):
        out = self.backup()
        before = self.device_flash()
        log_before = self.log()
        for args in (("backup.sh", "--dry-run", "--out", os.path.join(self.tmp, "dry")),
                     ("restore.sh", "--dry-run", "--backup", out),
                     ("upgrade.sh", "--dry-run", "--backup", out, "--firmware", self.fw_path)):
            proc = self.run_script(*args)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("dry run complete", proc.stdout)
        self.assertEqual(self.device_flash(), before)
        self.assertEqual(self.log().replace(log_before, "", 1).strip(), "--version\n--version\n--version".strip())
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "dry")))
        self.assertEqual([d for d in os.listdir(out) if d.startswith(("restore-", "upgrade-"))], [])


if __name__ == "__main__":
    unittest.main()
