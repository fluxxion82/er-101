/*
 * Linker script for the ER-101 firmware on the AT32UC3C1512C.
 *
 * Derived from the toolchain's default avr32elf_uc3c1512c.x with four changes
 * that make the flash layout the firmware assumes (src/meta.h, src/sequencer.h)
 * the layout the linker produces:
 *
 *  1. Only trampoline_uc3.o contributes to .reset, so program_start, the
 *     address the DFU bootloader jumps to, is exactly 0x80002000. The unused
 *     _start in startup_uc3.o used to land first and push it 8 bytes late.
 *  2. .reset and .exception are KEEP'd, so a future --gc-sections cannot drop
 *     the trampoline or the interrupt vector table.
 *  3. .ref_tables is pinned at REF_TABLES_ADDRESS (0x80017360), where
 *     seq_copy_to_reftable writes user tables. The upstream Makefile passed
 *     --section-start to the gcc driver without -Wl, and the driver dropped it.
 *  4. ASSERTs fail the link when code, rodata or data reach the tables, when
 *     the tables or the meta descriptor move, or when static data meets the stack.
 *
 * The flash map is documented in docs/flash-map.md and mirrored in
 * tools/er101_layout.py; tools/elfcheck.py and tools/hexcheck.py verify the
 * output of every build against it.
 */

OUTPUT_FORMAT("elf32-avr32", "elf32-avr32", "elf32-avr32")
OUTPUT_ARCH(avr32:uc)
ENTRY(_trampoline)

MEMORY
{
    FLASH (rxai!w)   : ORIGIN = 0x80000000, LENGTH = 512K
    CPUSRAM (wxa!ri) : ORIGIN = 0x00000004, LENGTH = 0xFFFC
    USERPAGE         : ORIGIN = 0x80800000, LENGTH = 512
}

PROGRAM_START      = 0x80002000;   /* meta.h PROGRAM_START_ADDRESS                 */
REF_TABLES_ADDRESS = 0x80017360;   /* sequencer.h REFTABLE_BASE_ADDRESS             */
REF_TABLES_SIZE    = 3200;         /* 16 tables x 100 x uint16                      */
META_ADDRESS       = 0x80017FE0;   /* meta.h META_DESCRIPTOR_ADDRESS, 32 bytes      */
PROGRAM_END        = 0x80018000;   /* snapshots start here (SNAPSHOT_BASE_ADDRESS)  */

/* The AVR32 linker emulation defines _stack_size (default 0x1000); keep that default
   if it ever stops doing so. */
PROVIDE (_stack_size = 0x1000);

SECTIONS
{
  /* startup_uc3.o's .reset holds _start, which nothing uses: the bootloader enters
     through _trampoline. Discard it so the trampoline alone defines .reset.
     This statement must come before .reset: input sections go to the first match. */
  /DISCARD/ : { *startup_uc3.o(.reset) }

  PROVIDE (__executable_start = 0x80000000); . = 0x80000000;
  .reset : { KEEP(*(.reset)) } >FLASH AT>FLASH

  .text :
  {
    *(.text .stub .text.* .gnu.linkonce.t.*)
    KEEP (*(.text.*personality*))
    *(.gnu.warning)
  } >FLASH AT>FLASH =0xd703d703
  PROVIDE (__etext = .);
  PROVIDE (_etext = .);
  PROVIDE (etext = .);

  /* Interrupt and exception vectors; exception.S aligns _evba to 0x200 inside. */
  .exception : { KEEP(*(.exception)) } >FLASH AT>FLASH

  .rodata         : { *(.rodata .rodata.* .gnu.linkonce.r.*) } >FLASH AT>FLASH
  .rodata1        : { *(.rodata1) } >FLASH AT>FLASH
  .eh_frame_hdr   : { *(.eh_frame_hdr) } >FLASH AT>FLASH
  .eh_frame       : ONLY_IF_RO { KEEP (*(.eh_frame)) } >FLASH AT>FLASH
  .gcc_except_table : ONLY_IF_RO { KEEP (*(.gcc_except_table)) *(.gcc_except_table.*) } >FLASH AT>FLASH

  /* Initialised data: lives in SRAM, loaded from flash right after the read-only
     sections (startup_uc3.S copies _data_lma.._data_lma+size to _data). */
  .dalign : { . = ALIGN(8); PROVIDE(_data_lma = .); } >FLASH AT>FLASH
  PROVIDE (_data = ORIGIN(CPUSRAM));
  . = ORIGIN(CPUSRAM);
  .eh_frame       : ONLY_IF_RW { KEEP (*(.eh_frame)) } >CPUSRAM AT>FLASH
  .gcc_except_table : ONLY_IF_RW { KEEP (*(.gcc_except_table)) *(.gcc_except_table.*) } >CPUSRAM AT>FLASH
  .tdata          : { *(.tdata .tdata.* .gnu.linkonce.td.*) } >CPUSRAM AT>FLASH
  .tbss           : { *(.tbss .tbss.* .gnu.linkonce.tb.*) *(.tcommon) } >CPUSRAM
  PROVIDE (__preinit_array_start = ALIGN(32 / 8));
  .preinit_array  : { KEEP (*(.preinit_array)) } >CPUSRAM AT>FLASH
  PROVIDE (__preinit_array_end = .);
  PROVIDE (__init_array_start = .);
  .init_array     : { KEEP (*(.init_array)) } >CPUSRAM AT>FLASH
  PROVIDE (__init_array_end = .);
  PROVIDE (__fini_array_start = .);
  .fini_array     : { KEEP (*(.fini_array)) } >CPUSRAM AT>FLASH
  PROVIDE (__fini_array_end = .);
  .ctors :
  {
    KEEP (*crtbegin*.o(.ctors))
    KEEP (*(EXCLUDE_FILE (*crtend*.o ) .ctors))
    KEEP (*(SORT(.ctors.*)))
    KEEP (*(.ctors))
  } >CPUSRAM AT>FLASH
  .dtors :
  {
    KEEP (*crtbegin*.o(.dtors))
    KEEP (*(EXCLUDE_FILE (*crtend*.o ) .dtors))
    KEEP (*(SORT(.dtors.*)))
    KEEP (*(.dtors))
  } >CPUSRAM AT>FLASH
  .jcr            : { KEEP (*(.jcr)) } >CPUSRAM AT>FLASH
  .data.rel.ro    : { *(.data.rel.ro.local) *(.data.rel.ro*) } >CPUSRAM AT>FLASH
  .dynamic        : { *(.dynamic) } >CPUSRAM AT>FLASH
  .got            : { *(.got.plt) *(.got) } >CPUSRAM AT>FLASH
  .data :
  {
    *(.data .data.* .gnu.linkonce.d.*)
    KEEP (*(.gnu.linkonce.d.*personality*))
    SORT(CONSTRUCTORS)
  } >CPUSRAM AT>FLASH
  .data1          : { *(.data1) } >CPUSRAM AT>FLASH
  .balign         : { . = ALIGN(8); _edata = .; } >CPUSRAM AT>FLASH
  _edata = .;
  PROVIDE (edata = .);
  __bss_start = .;
  .bss :
  {
   *(.dynbss)
   *(.bss .bss.* .gnu.linkonce.b.*)
   *(COMMON)
   . = ALIGN(8);
  } >CPUSRAM
  . = ALIGN(8);
  _end = .;
  PROVIDE (end = .);
  __heap_start__ = ALIGN(8);
  . = ORIGIN(CPUSRAM) + LENGTH(CPUSRAM) - _stack_size;
  __heap_end__ = .;

  /* Reference voltage tables at their fixed address, after every other flash
     section so the data load image stays below them. Tables 8-15 are rewritten in
     place by the firmware (user tables). The 32 bytes after them are the meta
     descriptor, written by the firmware; nothing is linked there. */
  .ref_tables REF_TABLES_ADDRESS : AT(REF_TABLES_ADDRESS) { KEEP(*(.ref_tables)) } >FLASH

  .stack ORIGIN(CPUSRAM) + LENGTH(CPUSRAM) - _stack_size :
  {
    _stack = .;
    *(.stack)
    . = _stack_size;
    _estack = .;
  } >CPUSRAM

  .userpage : { *(.userpage .userpage.*) } >USERPAGE AT>USERPAGE

  /* Debugging sections, not loaded. */
  .stab          0 : { *(.stab) }
  .stabstr       0 : { *(.stabstr) }
  .stab.excl     0 : { *(.stab.excl) }
  .stab.exclstr  0 : { *(.stab.exclstr) }
  .stab.index    0 : { *(.stab.index) }
  .stab.indexstr 0 : { *(.stab.indexstr) }
  .comment       0 : { *(.comment) }
  .debug          0 : { *(.debug) }
  .line           0 : { *(.line) }
  .debug_srcinfo  0 : { *(.debug_srcinfo) }
  .debug_sfnames  0 : { *(.debug_sfnames) }
  .debug_aranges  0 : { *(.debug_aranges) }
  .debug_pubnames 0 : { *(.debug_pubnames) }
  .debug_info     0 : { *(.debug_info .gnu.linkonce.wi.*) }
  .debug_abbrev   0 : { *(.debug_abbrev) }
  .debug_line     0 : { *(.debug_line) }
  .debug_frame    0 : { *(.debug_frame) }
  .debug_str      0 : { *(.debug_str) }
  .debug_loc      0 : { *(.debug_loc) }
  .debug_macinfo  0 : { *(.debug_macinfo) }
  .debug_weaknames 0 : { *(.debug_weaknames) }
  .debug_funcnames 0 : { *(.debug_funcnames) }
  .debug_typenames 0 : { *(.debug_typenames) }
  .debug_varnames  0 : { *(.debug_varnames) }
  /DISCARD/ : { *(.note.GNU-stack) }
}

ASSERT(_trampoline == 0x80000000,
       "ER-101: _trampoline must be the first instruction in flash")
ASSERT(program_start == PROGRAM_START,
       "ER-101: program_start is not at 0x80002000; the DFU bootloader jumps there")
ASSERT(ADDR(.ref_tables) == REF_TABLES_ADDRESS && ref_tables == REF_TABLES_ADDRESS,
       "ER-101: .ref_tables is not at 0x80017360, user table saves would be lost")
ASSERT(SIZEOF(.ref_tables) == REF_TABLES_SIZE,
       "ER-101: .ref_tables must be exactly 3200 bytes")
ASSERT(ADDR(.ref_tables) + SIZEOF(.ref_tables) == META_ADDRESS,
       "ER-101: the reference tables must end at the meta descriptor")
ASSERT(LOADADDR(.data) + SIZEOF(.data) <= REF_TABLES_ADDRESS,
       "ER-101: code, rodata and data overflow into the reference tables")
ASSERT(_evba % 0x200 == 0,
       "ER-101: _evba must be aligned to 512 bytes")
ASSERT(_end <= ORIGIN(CPUSRAM) + LENGTH(CPUSRAM) - _stack_size,
       "ER-101: static data collides with the stack")
/* That the stack ends at the top of SRAM is checked by tools/elfcheck.py: ld 2.23
   evaluates SIZEOF(.stack) before the ". = _stack_size" inside it has taken effect. */
