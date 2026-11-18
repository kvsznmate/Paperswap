#!/usr/bin/env bash
# Publish a signed Android release to GitHub Releases (#65).
#
# The release key stays on the machine that signs: this script builds and signs
# locally, then tags the commit and uploads the APK. Nothing secret reaches
# GitHub. See android/README.md, "Releasing".
#
#   cd android && ./release.sh            # build, sign, verify, tag, publish
#   ./release.sh --dry-run                # build, sign and verify only
#
# Needs: JDK 17-22 in JAVA_HOME, the paperswap.signing.* properties in
# ~/.gradle/gradle.properties, the Android SDK (ANDROID_HOME or sdk.dir in
# local.properties) and an authenticated gh. PAPERSWAP_BASE_URL sets the
# backend the APK talks to, and must be https:// (#32): release builds allow no
# cleartext, and Gradle refuses to package a signed APK with an http:// URL.
#   PAPERSWAP_BASE_URL=https://<domain>/ ./release.sh
set -euo pipefail
cd "$(dirname "$0")"

dry_run=false
[ "${1:-}" = "--dry-run" ] && dry_run=true

die() { echo "release: $*" >&2; exit 1; }

version=$(sed -n 's/^ *versionName = "\(.*\)"/\1/p' app/build.gradle.kts)
[ -n "$version" ] || die "versionName not found in app/build.gradle.kts"
tag="v$version"
echo "Releasing $tag"

if ! $dry_run; then
  [ -z "$(git status --porcelain)" ] || die "working tree is not clean"
  git fetch -q origin main --tags
  head=$(git rev-parse HEAD)
  [ "$head" = "$(git rev-parse origin/main)" ] \
    || die "HEAD is not origin/main. Releases are cut from main: git switch main && git pull"
  git rev-parse -q --verify "refs/tags/$tag" >/dev/null \
    && die "tag $tag already exists. Bump versionName and versionCode first"
  gh auth status >/dev/null 2>&1 || die "gh is not authenticated (gh auth login)"
fi

# Build. -Ppaperswap.baseUrl only when PAPERSWAP_BASE_URL is set.
gradle_args=(:app:assembleRelease)
[ -n "${PAPERSWAP_BASE_URL:-}" ] && gradle_args+=("-Ppaperswap.baseUrl=$PAPERSWAP_BASE_URL")
./gradlew "${gradle_args[@]}"

apk=app/build/outputs/apk/release/app-release.apk
[ -f "$apk" ] || die "no signed APK (only app-release-unsigned.apk?). Set the paperswap.signing.* properties"

# Verify with the newest build-tools apksigner.
sdk="${ANDROID_HOME:-$(sed -n 's/^sdk\.dir=//p' local.properties 2>/dev/null)}"
[ -n "$sdk" ] || die "Android SDK not found: set ANDROID_HOME or sdk.dir in local.properties"
apksigner=$(find "$sdk/build-tools" -maxdepth 2 \( -name apksigner -o -name apksigner.bat \) -type f | sort -V | tail -1)
[ -n "$apksigner" ] || die "apksigner not found under $sdk/build-tools"
"$apksigner" verify --print-certs "$apk" | grep -E "Signer #1 certificate (DN|SHA-256)"

out="app/build/outputs/apk/release/paperswap-$version.apk"
cp "$apk" "$out"
echo "Signed: $out"

if $dry_run; then
  echo "Dry run: not tagged or published."
  exit 0
fi

git tag -a "$tag" -m "Paperswap $version"
git push origin "$tag"
gh release create "$tag" "$out" --title "Paperswap $version" --generate-notes --verify-tag
