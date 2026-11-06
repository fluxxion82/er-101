"""Flash and RAM map of the ER-101 (AT32UC3C1512C), shared by the host tools.

Every number here mirrors a constant in the firmware: src/meta.h,
src/sequencer.h and src/voltages.h. docs/flash-map.md explains the layout.
Keep the three in step; tests/host/test_layout.py checks the arithmetic.
"""

FLASH_BASE = 0x80000000
FLASH_SIZE = 0x00080000          # 512 KiB
FLASH_END = FLASH_BASE + FLASH_SIZE
FLASH_PAGE_SIZE = 512

# The factory Atmel DFU bootloader owns the first 16 pages. The firmware hex
# contains a trampoline there (a branch to PROGRAM_START) that is never
# flashed; dfu-programmer's --suppress-bootloader-mem drops it.
BOOTLOADER_START = FLASH_BASE
BOOTLOADER_END = FLASH_BASE + 0x2000       # exclusive

PROGRAM_START = BOOTLOADER_END             # 0x80002000, meta.h PROGRAM_START_ADDRESS
MAX_PROGRAM_SIZE = 176 * FLASH_PAGE_SIZE   # meta.h MAX_PROGRAM_SIZE
PROGRAM_END = PROGRAM_START + MAX_PROGRAM_SIZE   # 0x80018000, exclusive

META_SIZE = 32
META_ADDRESS = PROGRAM_END - META_SIZE     # 0x80017FE0, meta.h META_DESCRIPTOR_ADDRESS
META_MAGIC = 0xABCDABCD
META_INFO_SIZE = 12                        # sizeof(meta_info_t) with padding

NUM_REF_TABLES = 16
VOLTAGES_PER_TABLE = 100
REF_TABLE_SIZE = 2 * VOLTAGES_PER_TABLE    # 200 bytes, big-endian uint16
REF_TABLES_SIZE = NUM_REF_TABLES * REF_TABLE_SIZE   # 3200
REF_TABLES_ADDRESS = META_ADDRESS - REF_TABLES_SIZE # 0x80017360, sequencer.h REFTABLE_BASE_ADDRESS
REF_TABLES_END = META_ADDRESS
FIRST_USER_TABLE = 8                       # tables 8..15 are USr1..USr8, editable in place
USER_TABLES_ADDRESS = REF_TABLES_ADDRESS + FIRST_USER_TABLE * REF_TABLE_SIZE

# Everything the compiler emits (code, rodata, data load image) must end here.
CODE_LIMIT = REF_TABLES_ADDRESS

SNAPSHOT_BASE = PROGRAM_END                # 0x80018000, sequencer.h SNAPSHOT_BASE_ADDRESS
SNAPSHOT_SIZE = 52 * FLASH_PAGE_SIZE       # 26624
NUM_SNAPSHOT_SLOTS = 16                    # NUM_SNAPSHOTS is 17 in firmware; slot 0 is the virtual blank
SNAPSHOT_END = SNAPSHOT_BASE + NUM_SNAPSHOT_SLOTS * SNAPSHOT_SIZE
SEQ_MAGIC = 0xABC01CBA
SEQUENCER_DATA_SIZE = 26572

USER_PAGE_ADDRESS = 0x80800000
USER_PAGE_SIZE = 512
DAC_DESCRIPTOR_ADDRESS = 0x808001F0        # meta.h, factory written, read only
DAC_DESCRIPTOR_MAGIC = 0xABCDDCBA
CALIBRATION_MAGIC = 0xBAAB

SRAM_BASE = 0x00000000
SRAM_SIZE = 0x00010000                     # 64 KiB
SRAM_END = SRAM_BASE + SRAM_SIZE

# Semitone table: 667 codes per semitone, 8000 per volt, clamped at the top.
SEMITONE_ROW = tuple(min(round(i * 8000 / 12), 64667) for i in range(VOLTAGES_PER_TABLE))

assert SNAPSHOT_END == FLASH_END, "snapshots must end exactly at the end of flash"
assert REF_TABLES_ADDRESS == 0x80017360
assert META_ADDRESS == 0x80017FE0
assert PROGRAM_START == 0x80002000
assert SNAPSHOT_BASE == 0x80018000


def region_name(address):
    """Name of the flash region holding address, for messages."""
    if BOOTLOADER_START <= address < BOOTLOADER_END:
        return "bootloader"
    if PROGRAM_START <= address < CODE_LIMIT:
        return "program"
    if REF_TABLES_ADDRESS <= address < USER_TABLES_ADDRESS:
        return "factory tables 0-7"
    if USER_TABLES_ADDRESS <= address < REF_TABLES_END:
        return "user tables 8-15"
    if META_ADDRESS <= address < PROGRAM_END:
        return "meta descriptor"
    if SNAPSHOT_BASE <= address < SNAPSHOT_END:
        slot = (address - SNAPSHOT_BASE) // SNAPSHOT_SIZE
        return "snapshot slot %d" % (slot + 1)
    if USER_PAGE_ADDRESS <= address < USER_PAGE_ADDRESS + USER_PAGE_SIZE:
        return "user page"
    return "outside the ER-101 memory map"
