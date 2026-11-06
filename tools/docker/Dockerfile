# syntax=docker/dockerfile:1.7
#
# Pinned firmware toolchain for the ER-101 (Atmel AVR32 UC3C1512C).
#
# The only compiler for this part is Microchip's "AVR 32-bit GNU Toolchain"
# 3.4.3.820 (GCC 4.4.7, binutils 2.23.1, newlib 1.16.0), shipped as statically
# linked x86-64 Linux binaries. This image installs it, plus the AVR device
# headers, under /opt/microchip, at the layout scripts/avr32.mk expects:
#
#   /opt/microchip/avr32-gnu-toolchain-linux_x86_64/bin/avr32-gcc
#   /opt/microchip/avr-headers/avr32/uc3c1512c.h
#
# Both downloads are verified against the SHA-256 recorded here (computed from
# the files fetched on 2026-11-05). The sources Microchip publishes are
# x86-64 only, so on Apple silicon this image runs under Docker Desktop's
# x86-64 emulation: Rosetta when enabled, QEMU otherwise (slower, same output).
#
# Build and run through tools/docker/run.sh (or `make docker-firmware`).

ARG BASE_IMAGE=debian:bookworm-slim@sha256:c67e92c8099ba22853ab4cfda7cfb494d253d6012ca82dee98ba8976772af018

# ---------------------------------------------------------------------------
# Downloads, each verified by SHA-256 before anything is unpacked.
FROM --platform=linux/amd64 ${BASE_IMAGE} AS downloads
ADD --checksum=sha256:66eb4dddce43b413912d9f9af920b265f2074c0c34d47aed2e90fde311bbd4ea \
    https://ww1.microchip.com/downloads/archive/avr32-gnu-toolchain-3.4.3.820-linux.any.x86_64.tar.gz \
    /dl/avr32-gnu-toolchain-3.4.3.820-linux.any.x86_64.tar.gz
ADD --checksum=sha256:f6e389278b12d06682776a28be0bb6356ded670bea605000f50bd453d753f0b9 \
    https://ww1.microchip.com/downloads/archive/avr-headers.zip \
    /dl/avr-headers.zip

# ---------------------------------------------------------------------------
# Final image: build tools plus /opt/microchip.
FROM --platform=linux/amd64 ${BASE_IMAGE}
RUN apt-get update \
 && apt-get install -y --no-install-recommends make unzip python3 \
 && rm -rf /var/lib/apt/lists/*
RUN --mount=type=bind,from=downloads,source=/dl,target=/dl \
    mkdir -p /opt/microchip \
 && tar -xzf /dl/avr32-gnu-toolchain-3.4.3.820-linux.any.x86_64.tar.gz -C /opt/microchip \
 && unzip -q /dl/avr-headers.zip -d /opt/microchip \
 && test -x /opt/microchip/avr32-gnu-toolchain-linux_x86_64/bin/avr32-gcc \
 && test -f /opt/microchip/avr-headers/avr32/uc3c1512c.h
ENV toolchain_dir=/opt/microchip \
    LC_ALL=C \
    HOME=/tmp
WORKDIR /src/er-101
