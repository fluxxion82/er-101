/*
 * src/meta.c on the host: the meta descriptor in flash and the version rule.
 * The version constants come from the generated version.h, the same header the
 * firmware build uses, so FIRMWARE_VERSION in scripts/env.mk is what is tested.
 */
#include <asf.h>
#include <meta.h>
#include <version.h>
#include "fake_hal.h"
#include "greatest.h"

static uint8_t *meta_bytes(void)
{
  return fake_flash + (META_DESCRIPTOR_ADDRESS - FAKE_FLASH_BASE);
}

static void write_meta(uint8_t major, uint8_t minor, uint8_t patch, uint8_t last_saved, uint8_t unpaused)
{
  meta_info_t m;
  memset(&m, 0, sizeof(m));
  m.magic = META_DESCRIPTOR_MAGIC;
  m.major = major;
  m.minor = minor;
  m.patch = patch;
  m.last_saved_snapshot = last_saved;
  m.start_unpaused = unpaused;
  memcpy(meta_bytes(), &m, sizeof(m));
}

static meta_info_t read_meta(void)
{
  meta_info_t m;
  memcpy(&m, meta_bytes(), sizeof(m));
  return m;
}

TEST erased_descriptor_is_initialised_with_the_build_version(void)
{
  fake_hal_reset();
  update_meta_info();
  meta_info_t m = read_meta();
  ASSERT_EQ(META_DESCRIPTOR_MAGIC, m.magic);
  ASSERT_EQ(FIRMWARE_VERSION_MAJOR, m.major);
  ASSERT_EQ(FIRMWARE_VERSION_MINOR, m.minor);
  ASSERT_EQ(FIRMWARE_VERSION_PATCH, m.patch);
  ASSERT_EQ(0, m.last_saved_snapshot);
  ASSERT_EQ(1, fake_flash_writes);
  PASS();
}

TEST matching_descriptor_is_not_rewritten(void)
{
  fake_hal_reset();
  write_meta(FIRMWARE_VERSION_MAJOR, FIRMWARE_VERSION_MINOR, FIRMWARE_VERSION_PATCH, 7, 0);
  update_meta_info();
  ASSERT_EQ(0, fake_flash_writes);
  ASSERT_EQ(7, meta_info.last_saved_snapshot);
  PASS();
}

/* The upstream upgrade rule: an old minor below 10 resets the last-saved slot,
 * whatever the major was. 2.09 -> 3.0.0 resets, 2.10 -> 3.0.0 keeps it (P4). */
TEST upgrade_from_2_09_resets_last_saved_slot(void)
{
  fake_hal_reset();
  write_meta(2, 9, 0, 5, 0);
  update_meta_info();
  meta_info_t m = read_meta();
  ASSERT_EQ(FIRMWARE_VERSION_MAJOR, m.major);
  ASSERT_EQ(FIRMWARE_VERSION_MINOR, m.minor);
  ASSERT_EQ(0, m.last_saved_snapshot);
  PASS();
}

TEST upgrade_from_2_10_keeps_last_saved_slot(void)
{
  fake_hal_reset();
  write_meta(2, 10, 0, 5, 1);
  update_meta_info();
  meta_info_t m = read_meta();
  ASSERT_EQ(FIRMWARE_VERSION_MAJOR, m.major);
  ASSERT_EQ(FIRMWARE_VERSION_MINOR, m.minor);
  ASSERT_EQ(5, m.last_saved_snapshot);
  ASSERT_EQ(1, m.start_unpaused);
  PASS();
}

TEST out_of_range_last_saved_slot_is_reset(void)
{
  fake_hal_reset();
  write_meta(FIRMWARE_VERSION_MAJOR, FIRMWARE_VERSION_MINOR, FIRMWARE_VERSION_PATCH, 16, 0);
  update_meta_info();
  ASSERT_EQ(0, read_meta().last_saved_snapshot);
  PASS();
}

TEST dac_bits_default_to_12_without_a_descriptor(void)
{
  fake_hal_reset();
  ASSERT_EQ(12, number_of_dac_bits());
  dac_info_t d = {DAC_DESCRIPTOR_MAGIC, 16};
  memcpy(fake_userpage + (DAC_DESCRIPTOR_ADDRESS - FAKE_USERPAGE_BASE), &d, sizeof(d));
  ASSERT_EQ(16, number_of_dac_bits());
  PASS();
}

SUITE(meta)
{
  RUN_TEST(erased_descriptor_is_initialised_with_the_build_version);
  RUN_TEST(matching_descriptor_is_not_rewritten);
  RUN_TEST(upgrade_from_2_09_resets_last_saved_slot);
  RUN_TEST(upgrade_from_2_10_keeps_last_saved_slot);
  RUN_TEST(out_of_range_last_saved_slot_is_reset);
  RUN_TEST(dac_bits_default_to_12_without_a_descriptor);
}
