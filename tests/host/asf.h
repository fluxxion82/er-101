/*
 * Host stand-in for the Atmel Software Framework header the firmware includes.
 *
 * The sequencer core (src/sequencer.c, clipboard.c, voltages.c, wallclock.c,
 * meta.c) only touches the identifiers below. Written from scratch: names and
 * semantics match the ASF, the text does not (the ASF license ties its code to
 * Atmel parts). Where the ASF semantics matter for test results they are
 * reproduced, see min() and cpu_us_2_cy().
 */
#ifndef FAKE_ASF_H
#define FAKE_ASF_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define __always_inline inline __attribute__((__always_inline__))

/* Clock: 16 MHz crystal, PLL to 96 MHz, CPU divider 2 (arch/avr32/config). */
#define CPU_HZ 48000000UL

/* Memory map of the AT32UC3C1512C (avr-headers uc3c1512c.h). */
#define AVR32_FLASH_ADDRESS 0x80000000
#define AVR32_FLASH_SIZE 0x00080000
#define AVR32_FLASH_PAGE_SIZE 0x00000200
#define AVR32_SRAM_ADDRESS 0x00000000
#define AVR32_SRAM_SIZE 0x00010000
#define AVR32_FLASHC_USER_PAGE_ADDRESS 0x80800000
#define AVR32_FLASHC_USER_PAGE_SIZE 512

/* The CPU cycle counter, read through Get_system_register(AVR32_COUNT). The fake
 * is a variable the tests advance (fake_hal.h). */
#define AVR32_COUNT 0x108
uint32_t fake_count_register(void);
#define Get_system_register(reg) fake_count_register()

/* ASF min() is the AVR32 `min` instruction on two int operands: both arguments are
 * converted to int first. The firmware calls it on unsigned long cycle counts. */
#define min(a, b)                              \
  ({                                           \
    int __arg_a = (a), __arg_b = (b);          \
    __arg_a < __arg_b ? __arg_a : __arg_b;     \
  })

/* cycle_counter.h: microseconds to cycles, rounded up. */
__always_inline static uint32_t cpu_us_2_cy(unsigned long us, unsigned long fcpu_hz)
{
  return (uint32_t)(((unsigned long long)us * fcpu_hz + 999999) / 1000000);
}

/* GPIO pin numbers from arch/avr32/config/conf_board.h: PA = 0.., PB = 32.., PC = 64.., PD = 96.. */
#define PIN_TRACK1_GATE 14
#define PIN_TRACK2_GATE 12
#define PIN_TRACK3_GATE 13
#define PIN_TRACK4_GATE 11
#define PIN_LSTART_LED 64
#define PIN_LEND_LED 65
#define PIN_COMMIT_LED 67
#define PIN_PAUSE_LED 69
#define PIN_COPY_LED 95
#define PIN_SMOOTH_LED 97
#define FAKE_NUM_PINS 128

void gpio_set_pin_high(uint32_t pin);
void gpio_set_pin_low(uint32_t pin);

/* common_nvm.h */
typedef enum
{
  INT_FLASH,
  INT_USERPAGE
} mem_type_t;

typedef enum
{
  STATUS_OK = 0,
  ERR_INVALID_ARG = -1
} status_code_t;

status_code_t nvm_read(mem_type_t mem, uint32_t address, void *buffer, uint32_t len);
status_code_t nvm_write(mem_type_t mem, uint32_t address, void *buffer, uint32_t len);

#endif /* FAKE_ASF_H */
