#!/bin/sh
# make check-version: the firmware version has one source, FIRMWARE_VERSION in
# scripts/env.mk. The first entry of CHANGELOG.md must carry the same version, and
# when HEAD is tagged the tag must be v<version>. With --release a tag is required.
set -eu
cd "$(dirname "$0")/.."
release=0
[ "${1:-}" = "--release" ] && release=1

env_version=$(sed -n 's/^FIRMWARE_VERSION[[:space:]]*=[[:space:]]*//p' scripts/env.mk | tr -d '[:space:]')
changelog_version=$(grep -m1 '^# v[0-9]' CHANGELOG.md | sed 's/^# v//; s/[^0-9.].*$//')
tag=$(git describe --tags --exact-match HEAD 2>/dev/null || true)

fail=0
echo "scripts/env.mk FIRMWARE_VERSION: ${env_version:-missing}"
echo "CHANGELOG.md first entry:        ${changelog_version:-missing}"
echo "git tag at HEAD:                 ${tag:-none}"

case $env_version in
  [0-9].[0-9].[0-9]|[0-9].[0-9][0-9].[0-9]) ;;
  *) echo "FAIL: FIRMWARE_VERSION must be MAJOR.MINOR.PATCH with one-digit MAJOR and PATCH (the splash shows one digit each)"; fail=1 ;;
esac
if [ "$env_version" != "$changelog_version" ]; then
  echo "FAIL: CHANGELOG.md's first entry does not match scripts/env.mk"; fail=1
fi
if [ -n "$tag" ] && [ "$tag" != "v$env_version" ]; then
  echo "FAIL: HEAD is tagged $tag but the firmware version is $env_version"; fail=1
fi
if [ $release -eq 1 ] && [ -z "$tag" ]; then
  echo "FAIL: --release requires HEAD to be tagged v$env_version"; fail=1
fi
if [ $fail -ne 0 ]; then exit 1; fi
echo "check-version: ok"
