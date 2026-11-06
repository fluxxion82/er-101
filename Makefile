firmware:
	+$(MAKE) -f scripts/firmware.mk

# Same as `make firmware`, inside the pinned toolchain container (tools/docker).
docker-firmware:
	tools/docker/run.sh make firmware

clean:
	+$(MAKE) -f scripts/firmware.mk clean

dist-clean:
	rm -rf release debug testing build

.PHONY: firmware docker-firmware clean dist-clean
