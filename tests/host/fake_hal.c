/*
 * Link stubs that let the sequencer core run on the host. Everything the core
 * calls outside itself lands here: display (src/display.h), DAC (src/extdac.h),
 * GPIO and flash (the fake asf.h). The display stubs record nothing but counts;
 * the flash is a byte array, so a write overwrites bytes (the real driver
 * erases and rewrites whole 512-byte pages, which the host tests do not model).
 */
#include <asf.h>
#include <wallclock.h>
#include <meta.h>
#include <display.h>
#include <extdac.h>
#include <sequencer.h>
#include "fake_hal.h"

uint8_t fake_flash[FAKE_FLASH_SIZE];
uint8_t fake_userpage[FAKE_USERPAGE_SIZE];
bool fake_pin_high[FAKE_NUM_PINS];
uint32_t fake_count;
int fake_flash_writes;
int fake_flash_write_errors;
fake_dac_write_t fake_dac_log[FAKE_DAC_LOG_SIZE];
int fake_dac_log_count;
int fake_dac_value[8];
int fake_error_count;
uint8_t fake_last_error;

void fake_hal_reset(void)
{
  memset(fake_flash, 0xFF, sizeof(fake_flash));
  memset(fake_userpage, 0xFF, sizeof(fake_userpage));
  memset(fake_pin_high, 0, sizeof(fake_pin_high));
  fake_count = 0;
  fake_flash_writes = 0;
  fake_flash_write_errors = 0;
  fake_dac_log_clear();
  for (int i = 0; i < 8; i++)
    fake_dac_value[i] = -1;
  fake_error_count = 0;
  fake_last_error = 0;
  memset(segdisp_display, 0, sizeof(segdisp_display));
  wallclock_base = 0;
}

void fake_dac_log_clear(void)
{
  fake_dac_log_count = 0;
  memset(fake_dac_log, 0, sizeof(fake_dac_log));
}

uint32_t fake_count_register(void)
{
  return fake_count;
}

/* ---- GPIO ---------------------------------------------------------------- */

void gpio_set_pin_high(uint32_t pin)
{
  if (pin < FAKE_NUM_PINS)
    fake_pin_high[pin] = true;
}

void gpio_set_pin_low(uint32_t pin)
{
  if (pin < FAKE_NUM_PINS)
    fake_pin_high[pin] = false;
}

/* ---- Flash --------------------------------------------------------------- */

static uint8_t *nvm_locate(mem_type_t mem, uint32_t address, uint32_t len)
{
  if (mem == INT_FLASH && address >= FAKE_FLASH_BASE && address - FAKE_FLASH_BASE + len <= FAKE_FLASH_SIZE)
    return fake_flash + (address - FAKE_FLASH_BASE);
  if (mem == INT_USERPAGE && address >= FAKE_USERPAGE_BASE && address - FAKE_USERPAGE_BASE + len <= FAKE_USERPAGE_SIZE)
    return fake_userpage + (address - FAKE_USERPAGE_BASE);
  return NULL;
}

status_code_t nvm_read(mem_type_t mem, uint32_t address, void *buffer, uint32_t len)
{
  uint8_t *p = nvm_locate(mem, address, len);
  if (p == NULL)
  {
    fake_flash_write_errors++;
    return ERR_INVALID_ARG;
  }
  memcpy(buffer, p, len);
  return STATUS_OK;
}

status_code_t nvm_write(mem_type_t mem, uint32_t address, void *buffer, uint32_t len)
{
  uint8_t *p = nvm_locate(mem, address, len);
  if (p == NULL)
  {
    fake_flash_write_errors++;
    return ERR_INVALID_ARG;
  }
  if (mem == INT_FLASH)
    fake_flash_writes++;
  memcpy(p, buffer, len);
  return STATUS_OK;
}

/* ---- DAC (src/extdac.h) -------------------------------------------------- */

void extdac_set_value(int i, int value)
{
  if (i >= 0 && i < 8)
    fake_dac_value[i] = value;
  if (fake_dac_log_count < FAKE_DAC_LOG_SIZE)
  {
    fake_dac_log[fake_dac_log_count].channel = i;
    fake_dac_log[fake_dac_log_count].value = value;
    fake_dac_log_count++;
  }
}

void extdac_set_smoother_timing(uint8_t track, wallclock_t t0, unsigned long period)
{
  (void)track;
  (void)t0;
  (void)period;
}

void extdac_set_smoother_output(int i, int v0, int v1)
{
  (void)v1;
  extdac_set_value(i, v0);
}

void extdac_disable_smoother(int i)
{
  (void)i;
}

/* ---- Display (src/display.h) --------------------------------------------- */

uint16_t segdisp_display[SEGDISP_NUM];
uint8_t focus_left_display = SEGDISP_STEP;
uint8_t focus_right_display = SEGDISP_CVA;
uint8_t saved_left_display = SEGDISP_STEP;
uint8_t saved_right_display = SEGDISP_CVA;

const uint16_t segdisp_right_digits[10] = {
    SEGDISP_RIGHT_0, SEGDISP_RIGHT_1, SEGDISP_RIGHT_2, SEGDISP_RIGHT_3, SEGDISP_RIGHT_4,
    SEGDISP_RIGHT_5, SEGDISP_RIGHT_6, SEGDISP_RIGHT_7, SEGDISP_RIGHT_8, SEGDISP_RIGHT_9};

/* Two-digit patterns: the tests only need distinct values per number. */
const uint16_t segdisp_both_digits[100] = {
#define D(n) (uint16_t)(0x0100 + (n))
    D(0), D(1), D(2), D(3), D(4), D(5), D(6), D(7), D(8), D(9),
    D(10), D(11), D(12), D(13), D(14), D(15), D(16), D(17), D(18), D(19),
    D(20), D(21), D(22), D(23), D(24), D(25), D(26), D(27), D(28), D(29),
    D(30), D(31), D(32), D(33), D(34), D(35), D(36), D(37), D(38), D(39),
    D(40), D(41), D(42), D(43), D(44), D(45), D(46), D(47), D(48), D(49),
    D(50), D(51), D(52), D(53), D(54), D(55), D(56), D(57), D(58), D(59),
    D(60), D(61), D(62), D(63), D(64), D(65), D(66), D(67), D(68), D(69),
    D(70), D(71), D(72), D(73), D(74), D(75), D(76), D(77), D(78), D(79),
    D(80), D(81), D(82), D(83), D(84), D(85), D(86), D(87), D(88), D(89),
    D(90), D(91), D(92), D(93), D(94), D(95), D(96), D(97), D(98), D(99)
#undef D
};

void segdisp_put_number(int display, uint8_t number) { (void)display; (void)number; }
void segdisp_put_dualdigit(int display, uint8_t number) { (void)display; (void)number; }
void segdisp_put_right_digit(int display, uint8_t digit) { (void)display; (void)digit; }
void segdisp_put_note(uint16_t number) { (void)number; }
void segdisp_put_voltage(uint16_t number) { (void)number; }
void segdisp_set(int display, uint16_t mask) { (void)display; (void)mask; }
void segdisp_clear(int display, uint16_t mask) { (void)display; (void)mask; }
void segdisp_toggle(int display, uint16_t mask) { (void)display; (void)mask; }
void select_left_display(int display) { focus_left_display = (uint8_t)display; }
void select_right_display(int display) { focus_right_display = (uint8_t)display; }
void highlight_left_display(int display) { (void)display; }
void highlight_right_display(int display) { (void)display; }
void restore_left_display(void) {}
void restore_right_display(void) {}
void orange_set(int display) { (void)display; }
void orange_clear(int display) { (void)display; }
void flash(uint8_t orange, uint8_t display, uint16_t value) { (void)orange; (void)display; (void)value; }
void wait_for_display_to_refresh(void) {}

void segdisp_on_error(uint8_t code)
{
  fake_error_count++;
  fake_last_error = code;
}
