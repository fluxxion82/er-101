/*
 * Test-facing side of the host fakes (tests/host/fake_hal.c): a 512 KiB flash
 * image, the user page, GPIO pin levels, the CPU cycle counter and a log of DAC
 * writes. Tests reset it with fake_hal_reset() before each case.
 */
#ifndef FAKE_HAL_H
#define FAKE_HAL_H

#include <stdbool.h>
#include <stdint.h>

#define FAKE_FLASH_BASE 0x80000000u
#define FAKE_FLASH_SIZE 0x00080000u
#define FAKE_USERPAGE_BASE 0x80800000u
#define FAKE_USERPAGE_SIZE 512u

extern uint8_t fake_flash[FAKE_FLASH_SIZE];
extern uint8_t fake_userpage[FAKE_USERPAGE_SIZE];
extern bool fake_pin_high[128];
extern uint32_t fake_count;          /* value Get_system_register(AVR32_COUNT) returns */
extern int fake_flash_writes;        /* nvm_write calls on INT_FLASH */
extern int fake_flash_write_errors;  /* out-of-range nvm accesses, which also return an error */

/* DAC writes in call order: channel 0..7 and the 16-bit code (extdac_set_value). */
#define FAKE_DAC_LOG_SIZE 256
typedef struct
{
  int channel;
  int value;
} fake_dac_write_t;
extern fake_dac_write_t fake_dac_log[FAKE_DAC_LOG_SIZE];
extern int fake_dac_log_count;
extern int fake_dac_value[8];        /* last value per channel, -1 when never written */

extern int fake_error_count;         /* segdisp_on_error calls */
extern uint8_t fake_last_error;

void fake_hal_reset(void);
void fake_dac_log_clear(void);

#endif /* FAKE_HAL_H */
