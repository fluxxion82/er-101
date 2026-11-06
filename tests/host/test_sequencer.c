/*
 * Pure-core tests of src/sequencer.c running on the host (plan item A8).
 *
 * These exercise the sequencer's data structures and clock path through the
 * fakes in fake_hal.c. They prove the seam: the firmware's sequencer compiles
 * and runs unchanged on a 64-bit host under the sanitizers.
 *
 * TODO(A8, target representation): the serialised structures are stored
 * big-endian on the AVR32 and little-endian here, so the snapshot round trip
 * below only shows that save and load agree with each other on this host. Tests
 * against real byte layouts (sizeof/offsetof of every serialised struct, a
 * big-endian snapshot fixture decoded independently, 32-bit wrap of the period
 * arithmetic) wait for the first flash dump from tools/backup.sh (plan item A9).
 */
#include <asf.h>
#include <meta.h>
#include <wallclock.h>
#include <extdac.h>
#include <display.h>
#include <sequencer.h>
#include <clipboard.h>
#include "fake_hal.h"
#include "greatest.h"

static void fresh_sequencer(void)
{
  fake_hal_reset();
  sequencer_init();
  fake_dac_log_clear();
}

/* Append a step after the editor position of a track and set its fields. */
static step_t *append_step(int track, uint8_t cvA, uint8_t duration, uint8_t gate)
{
  position_t *p = &seq.editors[track];
  if (!seq_insert_step_after(p))
    return NULL;
  p->ps->cvA = cvA;
  p->ps->duration = duration;
  p->ps->gate = gate;
  return p->ps;
}

TEST layout_constants_match_the_flash_map(void)
{
  ASSERT_EQ(26572u, sizeof(sequencer_data_t));
  ASSERT_EQ(0x80017360u, (unsigned)REFTABLE_BASE_ADDRESS);
  ASSERT_EQ(0x80017FE0u, (unsigned)META_DESCRIPTOR_ADDRESS);
  ASSERT_EQ(0x80018000u, (unsigned)SNAPSHOT_BASE_ADDRESS);
  ASSERT_EQ(26624u, (unsigned)SNAPSHOT_SIZE);
  ASSERT_EQ(0x80080000u, (unsigned)(SNAPSHOT_BASE_ADDRESS + 16 * SNAPSHOT_SIZE));
  PASS();
}

TEST step_pool_exhausts_and_recycles(void)
{
  fresh_sequencer();
  sequencer_data_t *d = &seq.data;
  bool seen[MAX_STEPS];
  memset(seen, 0, sizeof(seen));
  for (int i = 0; i < MAX_STEPS; i++)
  {
    step_id_t s = seq_allocate_step(d);
    ASSERT(s != INVALID_STEP);
    ASSERT(s < MAX_STEPS);
    ASSERT_FALSE(seen[s]);
    seen[s] = true;
  }
  ASSERT_EQ(MAX_STEPS, d->steps.head);
  ASSERT_EQ(INVALID_STEP, seq_allocate_step(d));
  seq_free_step(d, 17);
  ASSERT_EQ(17, seq_allocate_step(d));
  ASSERT_EQ(INVALID_STEP, seq_allocate_step(d));
  PASS();
}

TEST insert_builds_a_doubly_linked_pattern(void)
{
  fresh_sequencer();
  ASSERT(append_step(0, 1, 1, 1) != NULL);
  ASSERT(append_step(0, 2, 1, 1) != NULL);
  ASSERT(append_step(0, 3, 1, 1) != NULL);
  track_t *t = &seq.data.tracks[0];
  ASSERT_EQ(1, t->nm);
  pattern_t *pm = &PATTERN((&seq.data), t->first);
  ASSERT_EQ(3, pm->ns);
  ASSERT(pm->first != INVALID_STEP);
  ASSERT(pm->last != INVALID_STEP);

  /* Walk forward: three steps, cvA 1, 2, 3, prev/next reciprocal. */
  step_id_t s = pm->first;
  step_id_t prev = INVALID_STEP;
  int count = 0;
  while (s != INVALID_STEP)
  {
    step_t *ps = &STEP((&seq.data), s);
    ASSERT_EQ(prev, ps->prev);
    ASSERT_EQ(count + 1, ps->cvA);
    count++;
    prev = s;
    s = ps->next;
  }
  ASSERT_EQ(3, count);
  ASSERT_EQ(prev, pm->last);
  ASSERT_EQ(3, seq.data.steps.head);
  ASSERT_EQ(1, seq.data.patterns.head);
  PASS();
}

TEST deleting_the_middle_step_relinks_its_neighbours(void)
{
  fresh_sequencer();
  append_step(0, 1, 1, 1);
  step_t *middle = append_step(0, 2, 1, 1);
  append_step(0, 3, 1, 1);
  position_t *p = &seq.editors[0];
  pattern_t *pm = p->pm;
  step_id_t first = pm->first, last = pm->last;

  seq_prev_step(p); /* editor now on the middle step */
  ASSERT_EQ(middle, p->ps);
  seq_delete_step(p);

  ASSERT_EQ(2, pm->ns);
  ASSERT_EQ(first, pm->first);
  ASSERT_EQ(last, pm->last);
  ASSERT_EQ(last, STEP((&seq.data), first).next);
  ASSERT_EQ(first, STEP((&seq.data), last).prev);
  ASSERT_EQ(2, seq.data.steps.head);
  ASSERT_EQ(first, p->s); /* the position moved to the previous step */
  PASS();
}

TEST step_limit_per_pattern_tilts_and_refuses(void)
{
  fresh_sequencer();
  for (int i = 0; i < 99; i++)
    ASSERT(append_step(0, 0, 1, 1) != NULL);
  ASSERT_FALSE(seq_insert_step_after(&seq.editors[0]));
  ASSERT_EQ(99, seq.editors[0].pm->ns);
  ASSERT_EQ(99, seq.data.steps.head);
  PASS();
}

/* Four external clocks drive a three-step track through its steps and round to
 * the start, writing the semitone-table voltage of each step's cvA index to DAC
 * channel 0 (track 1 A) and raising gate 1 at each step. The other tracks hold
 * one step each: an empty track's gate update reads through a NULL step on the
 * host (finding F14; harmless on the target only because SRAM starts at 0). */
TEST clocks_advance_steps_and_write_the_dac(void)
{
  fresh_sequencer();
  append_step(0, 10, 1, 1);
  append_step(0, 20, 1, 1);
  append_step(0, 30, 1, 1);
  for (int t = 1; t < NUM_TRACKS; t++)
    append_step(t, 0, 1, 1);
  seq.latched_period = 48000; /* 1 ms at 48 MHz */
  const uint16_t *semitones = ref_tables[REF_TABLE_SEMITONES];
  fake_dac_log_clear();

  const int expected_index[4] = {10, 20, 30, 10};
  wallclock_t now = 1000000;
  for (int clock = 0; clock < 4; clock++)
  {
    fake_count = (uint32_t)now;
    seq_on_clock_rising(now, false, NULL);
    ASSERT_EQ_FMT((int)semitones[expected_index[clock]], fake_dac_value[0], "%d");
    ASSERT(fake_pin_high[PIN_TRACK1_GATE]);
    now += seq.latched_period;
  }
  ASSERT_EQ(0, seq.players[0].ticks - 1); /* one tick into the current step */
  PASS();
}

TEST pause_blocks_the_clock(void)
{
  fresh_sequencer();
  append_step(0, 10, 1, 1);
  for (int t = 1; t < NUM_TRACKS; t++)
    append_step(t, 0, 1, 1);
  seq.pause = true;
  seq_on_clock_rising(5000, false, NULL);
  ASSERT_EQ(0, fake_dac_log_count);
  ASSERT_FALSE(fake_pin_high[PIN_TRACK1_GATE]);
  PASS();
}

TEST snapshot_save_and_load_round_trip(void)
{
  fresh_sequencer();
  append_step(0, 42, 7, 3);
  append_step(1, 5, 1, 1);
  ASSERT_FALSE(seq_snapshot_exists(1));
  ASSERT_FALSE(seq_snapshot_exists(2));

  seq_save_snapshot(1);
  ASSERT(seq_snapshot_exists(1));
  ASSERT_FALSE(seq_snapshot_exists(2));
  ASSERT_EQ(1, meta_info.last_saved_snapshot);
  uint32_t slot1 = SNAPSHOT_BASE_ADDRESS + 1 * SNAPSHOT_SIZE;
  ASSERT_EQ(0, memcmp(fake_flash + (slot1 - FAKE_FLASH_BASE), &seq.data, sizeof(sequencer_data_t)));
  /* Nothing past the data in the slot was touched. */
  ASSERT_EQ(0xFF, fake_flash[slot1 - FAKE_FLASH_BASE + sizeof(sequencer_data_t)]);

  sequencer_data_init(&seq.data);
  for (int i = 0; i < NUM_TRACKS; i++)
    position_init(&seq.editors[i], &seq.data, &seq.data.tracks[i]);
  ASSERT_EQ(0, seq.data.tracks[0].nm);

  seq_load_snapshot(1);
  ASSERT_EQ(1, seq.data.tracks[0].nm);
  ASSERT_EQ(1, seq.data.tracks[1].nm);
  ASSERT_EQ(0, seq.data.tracks[2].nm);
  ASSERT_EQ(2, seq.data.steps.head);
  step_t *s = &STEP((&seq.data), PATTERN((&seq.data), seq.data.tracks[0].first).first);
  ASSERT_EQ(42, s->cvA);
  ASSERT_EQ(7, s->duration);
  ASSERT_EQ(3, s->gate);
  ASSERT_EQ(0, fake_error_count);
  PASS();
}

TEST user_table_copy_writes_to_the_pinned_address(void)
{
  fresh_sequencer();
  uint16_t table[NUM_VOLTAGES_PER_TABLE];
  for (int i = 0; i < NUM_VOLTAGES_PER_TABLE; i++)
    table[i] = (uint16_t)(i * 100);
  seq_copy_to_reftable(8, table); /* USr1 */
  uint32_t usr1 = REFTABLE_BASE_ADDRESS + 8 * REFTABLE_SIZE;
  ASSERT_EQ(0x80017360u + 1600u, usr1);
  ASSERT_EQ(0, memcmp(fake_flash + (usr1 - FAKE_FLASH_BASE), table, sizeof(table)));
  ASSERT_EQ(0xFF, fake_flash[usr1 - FAKE_FLASH_BASE + sizeof(table)]);
  ASSERT_EQ(0, fake_error_count);
  PASS();
}

SUITE(sequencer)
{
  RUN_TEST(layout_constants_match_the_flash_map);
  RUN_TEST(step_pool_exhausts_and_recycles);
  RUN_TEST(insert_builds_a_doubly_linked_pattern);
  RUN_TEST(deleting_the_middle_step_relinks_its_neighbours);
  RUN_TEST(step_limit_per_pattern_tilts_and_refuses);
  RUN_TEST(clocks_advance_steps_and_write_the_dac);
  RUN_TEST(pause_blocks_the_clock);
  RUN_TEST(snapshot_save_and_load_round_trip);
  RUN_TEST(user_table_copy_writes_to_the_pinned_address);
}
