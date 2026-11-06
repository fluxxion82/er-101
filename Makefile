firmware:
	+$(MAKE) -f scripts/firmware.mk

# Same as `make firmware`, inside the pinned toolchain container (tools/docker).
docker-firmware:
	tools/docker/run.sh make firmware

# FIRMWARE_VERSION in scripts/env.mk must match CHANGELOG.md and the tag.
check-version:
	sh tools/check-version.sh

clean:
	+$(MAKE) -f scripts/firmware.mk clean

dist-clean:
	rm -rf release debug testing build

.PHONY: firmware docker-firmware check-version clean dist-clean
