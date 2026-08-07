#!/usr/bin/env python3
"""Guard release builds against Telegram's published test API identity."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BUILD_VARS = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/BuildVars.java"
MODULE_GRADLE = ROOT / "TMessagesProj/build.gradle"
LOGIN_ACTIVITY = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/LoginActivity.java"
STRINGS = ROOT / "TMessagesProj/src/main/res/values/strings.xml"
STRINGS_RU = ROOT / "TMessagesProj/src/main/res/values-ru/strings.xml"
WORKFLOW = ROOT / ".forgejo/workflows/build-apk.yml"


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"Telegram API identity check failed: {message}", file=sys.stderr)
        raise SystemExit(1)


def main() -> int:
    build_vars = BUILD_VARS.read_text(encoding="utf-8")
    gradle = MODULE_GRADLE.read_text(encoding="utf-8")
    login = LOGIN_ACTIVITY.read_text(encoding="utf-8")
    strings = STRINGS.read_text(encoding="utf-8")
    strings_ru = STRINGS_RU.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")

    require(
        "public static int APP_ID = BuildConfig.TELEGRAM_API_ID;" in build_vars
        and "public static String APP_HASH = BuildConfig.TELEGRAM_API_HASH;" in build_vars,
        "BuildVars must obtain the Telegram identity from generated BuildConfig fields",
    )
    require(
        "public static int APP_ID = 4;" not in build_vars
        and 'public static String APP_HASH = "014b35b6184100b085b0d0572f9b5103";' not in build_vars,
        "BuildVars must not hard-code Telegram's published test identity",
    )

    for literal in (
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "ZASTO_TELEGRAM_API_CREDENTIALS_FILE",
        ".config/zastogram-signing/telegram-api.properties",
        "ZASTO_REQUIRE_TELEGRAM_API_CREDENTIALS",
        "zastoTelegramApiIdConfigured == null || zastoTelegramApiHashConfigured == null",
        'zastoTelegramApiId == "4"',
        'zastoTelegramApiHash.equalsIgnoreCase("014b35b6184100b085b0d0572f9b5103")',
        'buildConfigField "int", "TELEGRAM_API_ID"',
        'buildConfigField "String", "TELEGRAM_API_HASH"',
    ):
        require(literal in gradle, f"Gradle credential contract is missing: {literal}")
    require(
        'zastoTelegramApiIdConfigured ?: "4"' not in gradle
        and 'zastoTelegramApiHashConfigured ?: "014b35b6184100b085b0d0572f9b5103"' not in gradle
        and "if (zastoUsesPublishedTestIdentity)" in gradle,
        "every Android build must fail instead of falling back to the published test identity",
    )

    require(
        'error.text.contains("API_ID_PUBLISHED_FLOOD")' in login
        and "R.string.LoginApiCredentialsError" in login
        and 'name="LoginApiCredentialsError"' in strings
        and 'name="LoginApiCredentialsError"' in strings_ru,
        "phone login must turn API_ID_PUBLISHED_FLOOD into a visible localized error",
    )

    for literal in (
        "TELEGRAM_API_ID: ${{ secrets.TELEGRAM_API_ID }}",
        "TELEGRAM_API_HASH: ${{ secrets.TELEGRAM_API_HASH }}",
        "ZASTO_REQUIRE_TELEGRAM_API_CREDENTIALS: '1'",
        '[[ "$TELEGRAM_API_ID" != 4 ]]',
        '[[ "${TELEGRAM_API_HASH,,}" != 014b35b6184100b085b0d0572f9b5103 ]]',
        "python3 Tools/check_telegram_api_identity.py",
    ):
        require(literal in workflow, f"Forgejo workflow credential guard is missing: {literal}")
    require(
        "Inject optional Telegram API credentials" not in workflow
        and "compile proof uses the public test identity" not in workflow,
        "Forgejo must fail instead of silently compiling with the test identity",
    )

    print("Telegram API identity check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
