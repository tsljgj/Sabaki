#!/bin/sh
# Rebuild Sabaki and install it to /Applications, signed with a stable local
# certificate so macOS keeps the Screen Recording permission between builds.
set -e
cd "$(dirname "$0")/.."
npm run bundle
npx electron-builder --mac dir --arm64 --publish never
pkill -f "Applications/Sabaki.app" || true
codesign --force --deep --sign "Sabaki Local Signing" dist/mac-arm64/Sabaki.app
rm -rf /Applications/Sabaki.app
cp -R dist/mac-arm64/Sabaki.app /Applications/
xattr -cr /Applications/Sabaki.app
