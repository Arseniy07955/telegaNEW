#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "TMessagesProj_AppStandalone/src/main/java/org/telegram/messenger/GitHubUpdaterController.java"
LOADER = ROOT / "TMessagesProj_AppStandalone/src/main/java/org/telegram/messenger/ApplicationLoaderImpl.java"
LAYOUT = ROOT / "TMessagesProj_AppStandalone/src/main/java/org/telegram/ui/Components/GitHubUpdateLayout.java"
ALERT = ROOT / "TMessagesProj_AppStandalone/src/main/java/org/telegram/ui/Components/GitHubUpdateAlertDialog.java"
LAUNCH = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/LaunchActivity.java"
BETA_UPDATE = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/BetaUpdate.java"
LIB_GRADLE = ROOT / "TMessagesProj/build.gradle"
APP_GRADLE = ROOT / "TMessagesProj_AppStandalone/build.gradle"
WORKFLOW = ROOT / ".github/workflows/build-apk.yml"


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"FAIL: missing {path.relative_to(ROOT)}", file=sys.stderr)
        raise SystemExit(1)


def require(text: str, literal: str, description: str, failures: list[str]) -> None:
    if literal not in text:
        failures.append(f"{description}: missing {literal!r}")


def main() -> int:
    controller = read(CONTROLLER)
    loader = read(LOADER)
    layout = read(LAYOUT)
    alert = read(ALERT)
    launch = read(LAUNCH)
    beta_update = read(BETA_UPDATE)
    lib_gradle = read(LIB_GRADLE)
    app_gradle = read(APP_GRADLE)
    workflow = read(WORKFLOW)
    failures: list[str] = []

    for literal in (
        "public boolean isCustomUpdate()",
        "return true;",
        "GitHubUpdaterController.getInstance().checkForUpdate(force, whenDone)",
        "GitHubUpdaterController.getInstance().downloadUpdate()",
        "new GitHubUpdateLayout(activity, sideMenuContainer)",
        "GitHubUpdateAlertDialog.show(context, update)",
    ):
        require(loader, literal, "Standalone loader must fully own app updates", failures)

    for literal in (
        'return isDevChannel() ? base + "?per_page=100" : base + "/latest";',
        'release.optBoolean("draft", false)',
        'release.optBoolean("prerelease", false) != expectPrerelease',
        'asset.optString("browser_download_url", "")',
        'for (String abi : Build.SUPPORTED_ABIS)',
        '"ZaStoGram-standalone-" + abi + ".apk"',
        'candidate.releaseTag.equals(getInstalledReleaseTag())',
        'ApplicationLoader.getApplicationId().equals(packageInfo.packageName)',
        'file.length() != assetSize',
        'setHeader("X-GitHub-Api-Version", "2022-11-28")',
        'setHeader("User-Agent", "ZaStoGram-Android-Updater")',
    ):
        require(controller, literal, "GitHub updater channel/asset safety contract", failures)

    if "github.token" in controller.lower() or "authorization" in controller.lower():
        failures.append("Android updater must use the public GitHub Releases API without embedding credentials")

    custom_branch = launch.find("if (ApplicationLoader.applicationLoaderInstance.isCustomUpdate())")
    telegram_request = launch.find("new TLRPC.TL_help_getAppUpdate()", custom_branch)
    custom_return = launch.find("return;", custom_branch)
    if custom_branch < 0 or custom_return < 0 or telegram_request < 0 or not custom_return < telegram_request:
        failures.append("LaunchActivity must return from the custom updater before Telegram TL_help_getAppUpdate")

    for literal in (
        'System.getenv("ZASTO_UPDATE_CHANNEL") ?: "stable"',
        'buildConfigField "String", "ZASTO_UPDATE_CHANNEL"',
        'buildConfigField "String", "ZASTO_RELEASE_TAG"',
        'buildConfigField "String", "ZASTO_GITHUB_REPOSITORY"',
        'buildConfigField "int", "ZASTO_BUILD_NUMBER"',
    ):
        require(lib_gradle, literal, "BuildConfig update-channel identity", failures)

    require(
        app_gradle,
        "output.versionCodeOverride = defaultConfig.versionCode * 100000 + zastoBuildNumber * 10 + abiVersionDigit",
        "Published APKs need monotonically increasing workflow version codes",
        failures,
    )

    for literal in (
        "python3 Tools/check_github_update_contract.py",
        "ZASTO_UPDATE_CHANNEL: dev",
        "ZASTO_RELEASE_TAG: zastogram-apk-${{ github.run_number }}-${{ github.run_attempt }}",
        "ZASTO_BUILD_NUMBER: ${{ github.run_number }}",
        "ZASTO_GITHUB_REPOSITORY: ${{ github.repository }}",
        "--prerelease",
    ):
        require(workflow, literal, "GitHub Actions dev-release identity", failures)

    for text, description in (
        (layout, "GitHub update drawer UI"),
        (alert, "GitHub update alert UI"),
    ):
        require(text, "BuildConfig.ZASTO_UPDATE_CHANNEL", description, failures)
        require(text, '"ZaStoGram.apk"', description, failures)

    for literal in (
        "public final long updateOrder;",
        "updateOrder > update.updateOrder",
        "new BetaUpdate(version, displayVersionCode, changelog, updateOrder)",
    ):
        target = controller if literal.startswith("new BetaUpdate") else beta_update
        require(target, literal, "Release ordering must not depend on Telegram APP_VERSION_CODE", failures)

    if failures:
        print("GitHub updater contract failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("GitHub updater contract passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
