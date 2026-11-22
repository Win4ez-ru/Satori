#!/bin/sh
set -eu
places_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$places_dir/build" /private/tmp/satori-daily-places-swift-cache
xcrun swiftc -swift-version 6 -strict-concurrency=complete -warnings-as-errors \
  -target "$(uname -m)-apple-macos14.0" -module-cache-path /private/tmp/satori-daily-places-swift-cache \
  "$places_dir/Models.swift" "$places_dir/Protocol.swift" "$places_dir/Lease.swift" \
  "$places_dir/NativeProvider.swift" "$places_dir/MapWindow.swift" "$places_dir/Coordinator.swift" \
  "$places_dir/SelfTest.swift" "$places_dir/Main.swift" -o "$places_dir/build/satori-places-helper"
"$places_dir/build/satori-places-helper" --self-test
