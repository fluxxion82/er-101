#!/usr/bin/env python3
"""Strict Intel HEX reader and writer shared by the ER-101 host tools.

Reading rejects anything that is not a clean, complete image: bad characters,
bad checksums, wrong record lengths, unknown record types, data after the
end-of-file record, a missing end-of-file record, and overlapping data. The
flash tools (backup, restore, upgrade) rely on these rejections, so
`python3 tools/intelhex.py --selftest` exercises them and the shell scripts
run that before touching the module.

    from intelhex import Memory, HexError
    mem = Memory.from_hex_file(path)
    mem.segments()            -> [(start, bytes), ...] sorted, non-overlapping, coalesced
    mem.get(start, size)      -> bytes, raises HexError on any gap
    mem.get(start, size, fill=0xFF)
    mem.covers(start, end)    -> True if every byte in [start, end) is present
    mem.put(start, data)      -> add bytes, raises on overlap
    mem.to_hex()              -> text with 16-byte records and type 04 extended addresses
"""

import sys

RECORD_DATA = 0x00
RECORD_EOF = 0x01
RECORD_EXT_SEGMENT = 0x02
RECORD_START_SEGMENT = 0x03
RECORD_EXT_LINEAR = 0x04
RECORD_START_LINEAR = 0x05


class HexError(ValueError):
    """A file or operation did not satisfy the strict rules above."""


class Memory:
    """A sparse byte image with 32-bit addresses."""

    def __init__(self):
        self._segs = []   # list of [start, bytearray], sorted, non-adjacent after coalescing

    # -- construction -----------------------------------------------------

    @classmethod
    def from_hex_file(cls, path):
        with open(path, "r", encoding="ascii", errors="strict") as f:
            return cls.from_hex_text(f.read(), name=path)

    @classmethod
    def from_hex_text(cls, text, name="<hex>"):
        mem = cls()
        upper = 0
        seen_eof = False
        lineno = 0
        for raw in text.splitlines():
            lineno += 1
            line = raw.strip()
            if not line:
                continue
            where = "%s:%d" % (name, lineno)
            if seen_eof:
                raise HexError("%s: data after the end-of-file record" % where)
            if line[0] != ":":
                raise HexError("%s: record does not start with ':'" % where)
            body = line[1:]
            if len(body) % 2 or len(body) < 10:
                raise HexError("%s: record has an odd or too short hex length" % where)
            try:
                data = bytes.fromhex(body)
            except ValueError:
                raise HexError("%s: record contains non-hex characters" % where)
            count = data[0]
            if len(data) != count + 5:
                raise HexError("%s: byte count %d does not match record length %d"
                               % (where, count, len(data) - 5))
            if sum(data) & 0xFF:
                raise HexError("%s: checksum mismatch" % where)
            offset = (data[1] << 8) | data[2]
            rtype = data[3]
            payload = data[4:-1]
            if rtype == RECORD_DATA:
                address = upper + offset
                if address + count > 0x100000000:
                    raise HexError("%s: data record crosses the 32-bit address space" % where)
                try:
                    mem.put(address, payload)
                except HexError as e:
                    raise HexError("%s: %s" % (where, e))
            elif rtype == RECORD_EOF:
                if count != 0:
                    raise HexError("%s: end-of-file record with data" % where)
                seen_eof = True
            elif rtype == RECORD_EXT_LINEAR:
                if count != 2:
                    raise HexError("%s: extended linear address record must carry 2 bytes" % where)
                upper = ((payload[0] << 8) | payload[1]) << 16
            elif rtype == RECORD_EXT_SEGMENT:
                if count != 2:
                    raise HexError("%s: extended segment address record must carry 2 bytes" % where)
                upper = ((payload[0] << 8) | payload[1]) << 4
            elif rtype in (RECORD_START_SEGMENT, RECORD_START_LINEAR):
                if count != 4:
                    raise HexError("%s: start address record must carry 4 bytes" % where)
                # The entry point is irrelevant to a flash image; accepted and ignored.
            else:
                raise HexError("%s: unknown record type 0x%02X" % (where, rtype))
        if not seen_eof:
            raise HexError("%s: missing end-of-file record" % name)
        return mem

    @classmethod
    def from_segments(cls, segments):
        mem = cls()
        for start, data in segments:
            mem.put(start, data)
        return mem

    # -- editing ------------------------------------------------------------

    def put(self, start, data):
        """Add bytes at start. Overlap with existing bytes is an error."""
        data = bytes(data)
        if not data:
            return
        end = start + len(data)
        if start < 0 or end > 0x100000000:
            raise HexError("address 0x%X out of 32-bit range" % start)
        for s, buf in self._segs:
            if s < end and start < s + len(buf):
                raise HexError("overlapping data at 0x%08X" % max(s, start))
        self._segs.append([start, bytearray(data)])
        self._segs.sort(key=lambda seg: seg[0])
        self._coalesce()

    def _coalesce(self):
        merged = []
        for s, buf in self._segs:
            if merged and merged[-1][0] + len(merged[-1][1]) == s:
                merged[-1][1].extend(buf)
            else:
                merged.append([s, buf])
        self._segs = merged

    def remove_range(self, start, end):
        """Drop every byte in [start, end)."""
        out = []
        for s, buf in self._segs:
            e = s + len(buf)
            if e <= start or s >= end:
                out.append([s, buf])
                continue
            if s < start:
                out.append([s, buf[: start - s]])
            if e > end:
                out.append([end, buf[end - s:]])
        self._segs = out

    # -- queries ------------------------------------------------------------

    def segments(self):
        return [(s, bytes(buf)) for s, buf in self._segs]

    def __len__(self):
        return sum(len(buf) for _, buf in self._segs)

    def is_empty(self):
        return not self._segs

    def min_address(self):
        return self._segs[0][0] if self._segs else None

    def max_address(self):
        """Address of the last byte present, or None."""
        if not self._segs:
            return None
        s, buf = self._segs[-1]
        return s + len(buf) - 1

    def covers(self, start, end):
        """True when every byte in [start, end) is present."""
        if end <= start:
            return True
        for s, buf in self._segs:
            if s <= start and end <= s + len(buf):
                return True
        return False

    def has_any(self, start, end):
        """True when any byte in [start, end) is present."""
        for s, buf in self._segs:
            if s < end and start < s + len(buf):
                return True
        return False

    def get(self, start, size, fill=None):
        """Bytes in [start, start+size). Missing bytes raise unless fill is given."""
        end = start + size
        out = bytearray([fill if fill is not None else 0] * size)
        have = bytearray(size)
        for s, buf in self._segs:
            e = s + len(buf)
            lo, hi = max(s, start), min(e, end)
            if lo < hi:
                out[lo - start: hi - start] = buf[lo - s: hi - s]
                have[lo - start: hi - start] = b"\x01" * (hi - lo)
        if fill is None and not all(have):
            first_gap = have.index(0)
            raise HexError("no data at 0x%08X" % (start + first_gap))
        return bytes(out)

    def slice(self, start, end):
        """A new Memory holding only the bytes present in [start, end)."""
        out = Memory()
        for s, buf in self._segs:
            e = s + len(buf)
            lo, hi = max(s, start), min(e, end)
            if lo < hi:
                out.put(lo, buf[lo - s: hi - s])
        return out

    def shifted(self, delta):
        """A new Memory with every address moved by delta."""
        return Memory.from_segments((s + delta, buf) for s, buf in self._segs)

    # -- output -------------------------------------------------------------

    def to_hex(self, record_size=16):
        lines = []
        upper = None
        for start, buf in self._segs:
            address = start
            pos = 0
            while pos < len(buf):
                hi = address >> 16
                if hi != upper:
                    lines.append(_record(RECORD_EXT_LINEAR, 0, bytes([hi >> 8, hi & 0xFF])))
                    upper = hi
                room = min(record_size, len(buf) - pos, 0x10000 - (address & 0xFFFF))
                lines.append(_record(RECORD_DATA, address & 0xFFFF, buf[pos: pos + room]))
                address += room
                pos += room
        lines.append(_record(RECORD_EOF, 0, b""))
        return "\n".join(lines) + "\n"

    def write_hex_file(self, path):
        with open(path, "w", encoding="ascii", newline="\n") as f:
            f.write(self.to_hex())


def _record(rtype, offset, payload):
    data = bytes([len(payload), (offset >> 8) & 0xFF, offset & 0xFF, rtype]) + bytes(payload)
    checksum = (-sum(data)) & 0xFF
    return ":" + (data + bytes([checksum])).hex().upper()


# -- self test -----------------------------------------------------------------

def selftest():
    """Rejection paths the flash scripts depend on. Raises AssertionError on failure."""
    ext = _record(RECORD_EXT_LINEAR, 0, b"\x80\x00")
    data = _record(RECORD_DATA, 0x2000, bytes.fromhex("00112233"))
    eof = _record(RECORD_EOF, 0, b"")
    mem = Memory.from_hex_text("\n".join([ext, data, eof]))
    assert mem.get(0x80002000, 4) == bytes.fromhex("00112233")
    assert mem.max_address() == 0x80002003
    assert mem.covers(0x80002000, 0x80002004) and not mem.covers(0x80002000, 0x80002005)

    def rejects(lines, why):
        try:
            Memory.from_hex_text("\n".join(lines))
        except HexError:
            return
        raise AssertionError("accepted a hex file that should have been rejected: " + why)

    def flip_last(rec):
        return rec[:-1] + ("0" if rec[-1] != "0" else "1")

    rejects([ext, flip_last(data), eof], "bad checksum")
    rejects([ext, ":03" + data[3:], eof], "byte count shorter than data")
    rejects([ext, data], "missing EOF")
    rejects([ext, data, eof, data], "data after EOF")
    rejects([ext, data, _record(RECORD_DATA, 0x2001, b"\x44"), eof], "overlap")
    rejects([ext, data[:-3] + "GG" + data[-1], eof], "non-hex characters")
    rejects([ext, data[1:], eof], "missing colon")
    rejects([ext, _record(0x07, 0x2000, b"\x00"), eof], "unknown record type")
    rejects([ext, _record(RECORD_EOF, 0, b"\x00"), ], "EOF with data")
    rejects([_record(RECORD_EXT_LINEAR, 0, b"\x80"), data, eof], "extended address with 1 byte")
    rejects([ext, "", "  :garbage", eof], "garbage line")

    # Round trip through the writer keeps bytes and addresses.
    mem2 = Memory()
    mem2.put(0x80017360, bytes(range(200)))
    mem2.put(0x8007FFF0, b"\xFF" * 16)
    back = Memory.from_hex_text(mem2.to_hex())
    assert back.segments() == mem2.segments()
    assert back.get(0x80017360, 200) == bytes(range(200))
    assert back.get(0x80017360 - 4, 8, fill=0xFF) == b"\xFF" * 4 + bytes(range(4))
    try:
        mem2.put(0x80017361, b"\x00")
        raise AssertionError("put accepted overlapping bytes")
    except HexError:
        pass
    sliced = mem2.slice(0x80017364, 0x80017368)
    assert sliced.segments() == [(0x80017364, bytes(range(4, 8)))]
    mem2.remove_range(0x80017360, 0x80017428)
    assert mem2.segments() == [(0x8007FFF0, b"\xFF" * 16)]
    return True


def main(argv):
    if argv[1:] == ["--selftest"]:
        selftest()
        print("intelhex: selftest ok")
        return 0
    if len(argv) == 2 and argv[1] not in ("-h", "--help"):
        mem = Memory.from_hex_file(argv[1])
        print("%s: %d bytes in %d segment(s)" % (argv[1], len(mem), len(mem.segments())))
        for s, buf in mem.segments():
            print("  0x%08X-0x%08X  %7d bytes" % (s, s + len(buf) - 1, len(buf)))
        return 0
    print("usage: intelhex.py FILE.hex | --selftest", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except HexError as e:
        print("intelhex: %s" % e, file=sys.stderr)
        sys.exit(1)
