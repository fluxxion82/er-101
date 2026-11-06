# Host unit tests for the sequencer core: `make test` (from the top-level Makefile).
#
# Compiles src/sequencer.c, clipboard.c, voltages.c, wallclock.c and meta.c with the
# host compiler against the fake ASF header and link stubs in tests/host/, with
# AddressSanitizer and UndefinedBehaviorSanitizer on. The firmware is built with
# -O2 and strict aliasing; the tests use -fno-strict-aliasing like the firmware
# author intended (the dead CFLAGS.only in env.mk), see docs/build.md.
#
# Works with Apple clang and with gcc on Linux. HOSTCC overrides the compiler.
include scripts/env.mk

HOSTCC ?= cc
host_build_dir := build/host
version_h := $(host_build_dir)/generated/version.h

core_sources := src/sequencer.c src/clipboard.c src/voltages.c src/wallclock.c src/meta.c
test_sources := $(sort $(wildcard tests/host/*.c))
host_objects := $(addprefix $(host_build_dir)/,$(core_sources:%.c=%.o) $(test_sources:%.c=%.o))

# The fake asf.h must shadow src/asf.h: tests/host comes first on the include path.
# voltages.c puts its tables in a named ELF section, which Mach-O rejects; on the
# host the attribute is turned into "unused".
HOSTCFLAGS := -std=gnu99 -g -O1 -Wall -Wextra -Wno-unused-parameter \
	-fno-strict-aliasing -fno-omit-frame-pointer \
	-fsanitize=address,undefined -fno-sanitize-recover=undefined \
	-D'__section__(x)=unused' \
	-Itests/host -Isrc -I$(host_build_dir)/generated -Itests/host/vendor

test_binary := $(host_build_dir)/er101-tests

test: $(test_binary)
	@echo "[host.mk] RUN $(test_binary)"
	@$(test_binary)

$(test_binary): $(host_objects)
	@echo "[host.mk] LINK $@"
	@$(HOSTCC) $(HOSTCFLAGS) -o $@ $(host_objects)

$(host_build_dir)/%.o: %.c $(version_h)
	@echo "[host.mk] CC $<"
	@mkdir -p $(@D)
	@$(HOSTCC) $(HOSTCFLAGS) -MMD -MP -MF $@.d -c $< -o $@

$(version_h): scripts/env.mk scripts/gen-version.sh
	@sh scripts/gen-version.sh $(FIRMWARE_VERSION) $@

clean:
	rm -rf $(host_build_dir)

-include $(host_objects:%.o=%.o.d)

.PHONY: test clean
