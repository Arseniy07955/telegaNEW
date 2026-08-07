# ABI-Split APK Forgejo Actions Design

## Goal

Build four installable ZaStoGram standalone APKs in Forgejo Actions instead of one fat `afat` APK:

- `arm64-v8a`
- `armeabi-v7a`
- `x86`
- `x86_64`

Each successful workflow run should expose architecture-specific APK outputs instead of a single universal APK.

## Current State

The existing workflow builds `:TMessagesProj_AppStandalone:assembleAfatStandalone` and uploads the resulting APK through `actions/upload-artifact`.

`TMessagesProj_AppStandalone/build.gradle` currently has a single `afat` flavor whose `ndk.abiFilters` include all four supported ABIs. That produces one universal APK containing native libraries for every architecture, which makes the output larger than needed for a single device.

## Architecture

Add explicit ABI product flavors in `TMessagesProj_AppStandalone/build.gradle`.

Each flavor keeps the standalone build type and manifest behavior, but restricts native output to one ABI:

- `arm64` -> `arm64-v8a`
- `armv7` -> `armeabi-v7a`
- `x86` -> `x86`
- `x64` -> `x86_64`

Keep the existing `afat` flavor as a fallback for local/manual universal builds unless it causes a Gradle conflict.

Each ABI flavor gets a distinct two-digit `abiVersionCode` above the old universal `afat` suffix `9`. The `applicationVariants` version-code rule remains the central place that derives the final version code from `APP_VERSION_CODE` and the flavor suffix, using `APP_VERSION_CODE * 100 + abiVersionCode` so ABI split APKs can update over previously installed universal APKs.

The existing `variantFilter` must also allow the new ABI flavors for the `standalone` build type; otherwise Gradle will not create the matrix task names.

## Forgejo Actions

Refactor `.forgejo/workflows/build-apk.yml` to use a matrix with one entry per ABI flavor.

Shared setup remains in one job definition:

- checkout
- disk cleanup
- JDK 17 setup
- Android SDK/NDK/CMake install
- legacy `dx` compatibility hack
- ccache setup
- optional Telegram API secret injection
- MTProxy diagnostic log enablement
- signing keystore setup
- MTProxy guard suite

The build step becomes matrix-driven:

- `:TMessagesProj_AppStandalone:assembleArm64Standalone`
- `:TMessagesProj_AppStandalone:assembleArmv7Standalone`
- `:TMessagesProj_AppStandalone:assembleX86Standalone`
- `:TMessagesProj_AppStandalone:assembleX64Standalone`

Each matrix leg stages exactly one APK into `dist/` with a stable architecture-specific name:

- `dist/ZaStoGram-standalone-arm64-v8a.apk`
- `dist/ZaStoGram-standalone-armeabi-v7a.apk`
- `dist/ZaStoGram-standalone-x86.apk`
- `dist/ZaStoGram-standalone-x86_64.apk`

## Outputs

At minimum, Forgejo Actions should upload four architecture-specific artifacts, each containing the matching APK.

The workflow can also publish a run-specific prerelease with four direct `.apk` assets if direct install links are desired. If enabled, the prerelease should use a run-specific tag based on `forgejo.run_number` and `forgejo.run_attempt`.

Forgejo Actions artifacts may remain for CI history. They are not the same as direct APK release assets because Forgejo downloads artifacts as ZIP files.

## Caching

Use ABI-aware ccache keys so architectures do not fight over the same native object cache.

Gradle cache setup can remain shared through `actions/setup-java`, because Gradle's own cache separates task inputs.

## Verification

Local verification should include:

- static inspection that the standalone Gradle file defines all four ABI flavors with the expected `abiFilters`
- static inspection that the workflow matrix contains all four ABI entries
- static inspection that APK staging names are architecture-specific
- static inspection that uploaded output names are ABI-specific
- if release publishing is enabled, static inspection that release upload publishes `.apk` files directly
- the existing MTProxy guard suite remains part of CI

If full Gradle assembly cannot run locally because of SDK/NDK configuration, that must be reported separately from the YAML/Gradle contract verification.

## Out Of Scope

- Changing app package names or signing policy.
- Removing the universal `afat` fallback unless Gradle requires it.
- Reworking MTProxy runtime logic.
- Creating four copy-pasted workflow files.
