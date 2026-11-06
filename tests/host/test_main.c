/* Runner for the host unit tests. Each suite lives in its own file. */
#include "greatest.h"

extern SUITE(sequencer);
extern SUITE(meta);

GREATEST_MAIN_DEFS();

int main(int argc, char **argv)
{
  GREATEST_MAIN_BEGIN();
  RUN_SUITE(sequencer);
  RUN_SUITE(meta);
  GREATEST_MAIN_END();
}
