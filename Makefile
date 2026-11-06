firmware:
	+$(MAKE) -f scripts/firmware.mk

# Same as `make firmware`, inside the pinned toolchain container (tools/docker).
docker-firmware:
	tools/docker/run.sh make firmware

# Host tests: the sequencer core under clang or gcc with sanitizers (scripts/host.mk)
# and the flash tooling against a fake dfu-programmer (tests/tools). See docs/build.md.
test: test-host test-tools

test-host:
	+$(MAKE) -f scripts/host.mk test

test-tools:
	python3 -I -m unittest discover -s tests/tools -p 'test_*.py'

# FIRMWARE_VERSION in scripts/env.mk must match CHANGELOG.md and the tag.
check-version:
	sh tools/check-version.sh

clean:
	+$(MAKE) -f scripts/firmware.mk clean
	+$(MAKE) -f scripts/host.mk clean

dist-clean:
	rm -rf release debug testing build

.PHONY: firmware docker-firmware test test-host test-tools check-version clean dist-clean
