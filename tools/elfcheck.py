#!/usr/bin/env python3
"""Check the linked ER-101 firmware ELF against the flash and RAM map.

    python3 tools/elfcheck.py release/avr32/er-101-firmware-3.0.0.elf

Runs after every link (scripts/firmware.mk). Exit status 0 when every check
passes, 1 otherwise. Needs only the Python standard library: the ELF is parsed
here, so the check also runs on a machine without the AVR32 binutils.

Checks (see docs/flash-map.md for the numbers):
  - the entry point and _trampoline are at the start of flash
  - program_start, the address the DFU bootloader jumps to, is 0x80002000
  - .ref_tables is at 0x80017360 with exactly 3200 bytes and ref_tables points there
  - .exception exists and _evba is 512-byte aligned
  - every flash-loaded byte except the tables ends below 0x80017360
  - nothing but .reset lies in the bootloader's 8 KiB
  - .data, .bss and the stack fit in the 64 KiB of SRAM
It also prints every section's address, load address and size, the key
symbols, and the flash headroom, which docs/flash-map.md records.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import er101_layout as L  # noqa: E402

SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_NOBITS = 8
SHF_ALLOC = 0x2
PT_LOAD = 1


class ElfError(Exception):
    pass


class Elf32BE:
    """Just enough of a big-endian ELF32 reader for the checks above."""

    def __init__(self, data):
        if data[:4] != b"\x7fELF":
            raise ElfError("not an ELF file")
        if data[4] != 1 or data[5] != 2:
            raise ElfError("expected a 32-bit big-endian ELF (AVR32)")
        (self.e_type, self.e_machine, _ver, self.entry, phoff, shoff, _flags, _ehsize,
         phentsize, phnum, shentsize, shnum, shstrndx) = struct.unpack(">HHIIIIIHHHHHH", data[16:52])
        self.data = data
        self.sections = []
        for i in range(shnum):
            off = shoff + i * shentsize
            (name, stype, flags, addr, offset, size, link, info, align, entsize) = \
                struct.unpack(">IIIIIIIIII", data[off: off + 40])
            self.sections.append(dict(name_off=name, type=stype, flags=flags, addr=addr,
                                      offset=offset, size=size, link=link, info=info,
                                      align=align, entsize=entsize))
        shstr = self.sections[shstrndx]
        for s in self.sections:
            s["name"] = self._cstr(shstr["offset"] + s["name_off"])
        self.segments = []
        for i in range(phnum):
            off = phoff + i * phentsize
            (ptype, offset, vaddr, paddr, filesz, memsz, flags, align) = \
                struct.unpack(">IIIIIIII", data[off: off + 32])
            self.segments.append(dict(type=ptype, offset=offset, vaddr=vaddr, paddr=paddr,
                                      filesz=filesz, memsz=memsz, flags=flags, align=align))
        self.symbols = {}
        for s in self.sections:
            if s["type"] != SHT_SYMTAB:
                continue
            strtab = self.sections[s["link"]]
            for off in range(s["offset"], s["offset"] + s["size"], 16):
                (st_name, value, size, info, other, shndx) = struct.unpack(">IIIBBH", data[off: off + 16])
                name = self._cstr(strtab["offset"] + st_name)
                if name:
                    self.symbols[name] = value

    def _cstr(self, off):
        end = self.data.index(b"\x00", off)
        return self.data[off:end].decode("ascii", "replace")

    def section(self, name):
        for s in self.sections:
            if s["name"] == name:
                return s
        return None

    def lma(self, s):
        """Load address of an allocated section, from the PT_LOAD segment holding it."""
        for seg in self.segments:
            if seg["type"] != PT_LOAD:
                continue
            if seg["vaddr"] <= s["addr"] and s["addr"] + s["size"] <= seg["vaddr"] + seg["memsz"]:
                return seg["paddr"] + (s["addr"] - seg["vaddr"])
        return s["addr"]

    def loaded_sections(self):
        """Allocated sections that occupy bytes in the image (not NOBITS), with LMA."""
        out = []
        for s in self.sections:
            if s["flags"] & SHF_ALLOC and s["type"] != SHT_NOBITS and s["size"] > 0:
                out.append((s, self.lma(s)))
        return out


def check(path, verbose=True):
    with open(path, "rb") as f:
        elf = Elf32BE(f.read())
    failures = []
    notes = []

    def ok(msg):
        notes.append("  ok    " + msg)

    def bad(msg):
        failures.append(msg)
        notes.append("  FAIL  " + msg)

    sym = elf.symbols

    def need(name):
        if name not in sym:
            bad("symbol %s is missing" % name)
            return None
        return sym[name]

    # Entry and trampoline.
    tramp = need("_trampoline")
    if tramp is not None:
        if tramp == L.FLASH_BASE and elf.entry == tramp:
            ok("entry point and _trampoline at 0x%08X" % tramp)
        else:
            bad("_trampoline at 0x%08X, entry 0x%08X; both must be 0x%08X"
                % (tramp, elf.entry, L.FLASH_BASE))

    ps = need("program_start")
    if ps is not None:
        if ps == L.PROGRAM_START:
            ok("program_start == 0x%08X" % L.PROGRAM_START)
        else:
            bad("program_start is 0x%08X, the bootloader jumps to 0x%08X (%+d bytes)"
                % (ps, L.PROGRAM_START, ps - L.PROGRAM_START))

    # Reference tables.
    rt = need("ref_tables")
    rts = elf.section(".ref_tables")
    if rts is None:
        bad("no .ref_tables section")
    else:
        rt_lma = elf.lma(rts)
        if rt is not None and rt == L.REF_TABLES_ADDRESS and rts["addr"] == L.REF_TABLES_ADDRESS \
                and rt_lma == L.REF_TABLES_ADDRESS:
            ok("ref_tables and .ref_tables at 0x%08X" % L.REF_TABLES_ADDRESS)
        else:
            bad("ref_tables symbol 0x%08X, .ref_tables at 0x%08X (load 0x%08X); all must be 0x%08X. "
                "User table saves write to 0x%08X and would be read from the symbol."
                % (rt or 0, rts["addr"], rt_lma, L.REF_TABLES_ADDRESS, L.REF_TABLES_ADDRESS))
        if rts["size"] == L.REF_TABLES_SIZE:
            ok(".ref_tables is %d bytes" % L.REF_TABLES_SIZE)
        else:
            bad(".ref_tables is %d bytes, expected %d" % (rts["size"], L.REF_TABLES_SIZE))

    # Exception vectors.
    exc = elf.section(".exception")
    evba = need("_evba")
    if exc is None:
        bad("no .exception section (the interrupt vector table was dropped)")
    elif evba is not None:
        if evba % 0x200 == 0 and evba == exc["addr"]:
            ok("_evba 0x%08X is 512-byte aligned at the start of .exception" % evba)
        else:
            bad("_evba 0x%08X must be 512-byte aligned and start .exception (0x%08X)" % (evba, exc["addr"]))

    # Flash placement.
    max_code_end = 0
    for s, lma in elf.loaded_sections():
        if lma < L.FLASH_BASE or lma >= L.FLASH_END:
            if lma >= L.SRAM_END:  # ignore pure RAM sections only if they are in SRAM
                bad("section %s loads outside flash at 0x%08X" % (s["name"], lma))
            continue
        end = lma + s["size"]
        if s["name"] == ".ref_tables":
            continue
        if s["name"] == ".reset":
            if lma != L.FLASH_BASE:
                bad(".reset loads at 0x%08X, expected 0x%08X" % (lma, L.FLASH_BASE))
            if end > L.PROGRAM_START + 8:
                bad(".reset ends at 0x%08X; only the trampoline (ending by 0x%08X) belongs there"
                    % (end, L.PROGRAM_START + 8))
            max_code_end = max(max_code_end, end)
            continue
        if lma < L.PROGRAM_START:
            bad("section %s loads at 0x%08X, inside the bootloader's region" % (s["name"], lma))
        if end > L.CODE_LIMIT:
            bad("section %s loads up to 0x%08X, past the code limit 0x%08X (%s)"
                % (s["name"], end, L.CODE_LIMIT, L.region_name(end - 1)))
        max_code_end = max(max_code_end, end)
    if not failures or all("loads" not in f for f in failures):
        ok("flash image ends at 0x%08X, headroom to 0x%08X is %d bytes"
           % (max_code_end, L.CODE_LIMIT, L.CODE_LIMIT - max_code_end))
    headroom = L.CODE_LIMIT - max_code_end

    # RAM.
    end_ = need("_end")
    stack = need("_stack")
    estack = need("_estack")
    stack_size = need("_stack_size")
    if None not in (end_, stack, estack, stack_size):
        if estack == L.SRAM_END and stack + stack_size == estack:
            ok("stack 0x%08X-0x%08X (%d bytes) ends at the top of SRAM" % (stack, estack, stack_size))
        else:
            bad("stack 0x%08X-0x%08X (size %d) does not end at SRAM end 0x%08X"
                % (stack, estack, stack_size, L.SRAM_END))
        if end_ + stack_size <= L.SRAM_END and end_ <= stack:
            ok("_end 0x%08X plus stack fits in SRAM; %d bytes between _end and _stack"
               % (end_, stack - end_))
        else:
            bad("static data ends at 0x%08X, which collides with the stack at 0x%08X" % (end_, stack))

    for name in ("_stext", "main", "_data_lma", "_data", "_edata", "__bss_start"):
        need(name)

    report = []
    report.append("elfcheck: %s" % path)
    report.append("  entry 0x%08X" % elf.entry)
    report.append("  %-14s %10s %10s %8s" % ("section", "vma", "lma", "size"))
    for s in elf.sections:
        if s["flags"] & SHF_ALLOC and s["size"] > 0:
            report.append("  %-14s 0x%08X 0x%08X %8d%s" % (s["name"], s["addr"], elf.lma(s), s["size"],
                                                        "  (nobits)" if s["type"] == SHT_NOBITS else ""))
    report.append("  symbols:")
    for name in ("_trampoline", "program_start", "_stext", "_evba", "ref_tables", "_data_lma",
                 "_data", "_edata", "__bss_start", "_end", "_stack", "_estack", "_stack_size", "main"):
        if name in sym:
            report.append("    %-14s 0x%08X" % (name, sym[name]))
    if None not in (end_, stack_size):
        used = (end_ - L.SRAM_BASE) + stack_size
        report.append("  RAM: static data to 0x%08X (%d bytes) + stack %d = %d of %d bytes (%.1f%%)"
                      % (end_, end_ - L.SRAM_BASE, stack_size, used, L.SRAM_SIZE, 100.0 * used / L.SRAM_SIZE))
    report.append("  flash headroom below 0x%08X: %d bytes" % (L.CODE_LIMIT, headroom))
    report.extend(notes)
    if failures:
        report.append("elfcheck: FAILED (%d problem%s)" % (len(failures), "" if len(failures) == 1 else "s"))
    else:
        report.append("elfcheck: ok")
    if verbose:
        print("\n".join(report))
    return not failures


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    try:
        return 0 if check(argv[1]) else 1
    except (ElfError, OSError) as e:
        print("elfcheck: %s: %s" % (argv[1], e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
