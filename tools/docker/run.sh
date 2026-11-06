#!/bin/sh
# Run a command in the pinned AVR32 toolchain container.
#
#   tools/docker/run.sh                    # make firmware
#   tools/docker/run.sh make dist-clean    # any other command
#
# The image is built from tools/docker/Dockerfile on first use. The repository is
# mounted at the fixed path /src/er-101 so paths seen by the compiler do not
# depend on where the checkout lives. Files are written as the calling user.
# The toolchain is x86-64 only; the container always runs as linux/amd64
# (Rosetta or QEMU on Apple silicon).
#
# Environment:
#   ER101_TOOLCHAIN_IMAGE  image tag (default er101-avr32-toolchain:3.4.3.820)
#   ER101_REBUILD_IMAGE=1  rebuild the image even if it exists
set -eu

image=${ER101_TOOLCHAIN_IMAGE:-er101-avr32-toolchain:3.4.3.820}
repo=$(cd "$(dirname "$0")/../.." && pwd)

if ! docker info >/dev/null 2>&1; then
  echo "run.sh: the Docker daemon is not running (start Docker Desktop first)" >&2
  exit 1
fi

if [ "${ER101_REBUILD_IMAGE:-0}" = 1 ] || ! docker image inspect "$image" >/dev/null 2>&1; then
  docker build --platform linux/amd64 -f "$repo/tools/docker/Dockerfile" -t "$image" "$repo/tools/docker"
fi

[ $# -gt 0 ] || set -- make firmware

tty_flag=
[ -t 0 ] && [ -t 1 ] && tty_flag=-t

exec docker run --rm -i $tty_flag --platform linux/amd64 \
  --user "$(id -u):$(id -g)" \
  -v "$repo":/src/er-101 -w /src/er-101 \
  "$image" "$@"
