#!/usr/bin/env python3
"""Guard ZaStoGram phone login against accidental Firebase/GMS dependencies."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BUILD_VARS = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/BuildVars.java"
LOGIN_ACTIVITY = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/LoginActivity.java"
LAUNCH_ACTIVITY = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/LaunchActivity.java"
PASSPORT_ACTIVITY = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/PassportActivity.java"


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"login Firebase independence check failed: {message}", file=sys.stderr)
        raise SystemExit(1)


def main() -> int:
    build_vars = BUILD_VARS.read_text(encoding="utf-8")
    login = LOGIN_ACTIVITY.read_text(encoding="utf-8")
    launch = LAUNCH_ACTIVITY.read_text(encoding="utf-8")
    passport = PASSPORT_ACTIVITY.read_text(encoding="utf-8")

    require(
        "public static final boolean USE_FIREBASE_SMS_AUTH = false;" in build_vars,
        "Firebase SMS auth must remain compile-time disabled",
    )
    require(
        'public static String SAFETYNET_KEY = "";' in build_vars,
        "the fork must not ship the official SafetyNet key",
    )
    require(
        'public static String getSmsHash() {\n        return "";' in build_vars,
        "the fork must not advertise an SMS Retriever hash from another package/signature",
    )
    require(
        "boolean firebaseSmsAvailable = BuildVars.USE_FIREBASE_SMS_AUTH" in login
        and "settings.allow_app_hash = firebaseSmsAvailable;" in login
        and "settings.allow_firebase = firebaseSmsAvailable;" in login,
        "auth.sendCode flags must be gated by the disabled fork capability",
    )
    require(
        "settings.allow_app_hash = settings.allow_firebase =" not in login,
        "Google Services availability alone must never enable Firebase phone auth",
    )
    launch_auth_start = launch.find("TL_account.sendConfirmPhoneCode req")
    launch_auth_end = launch.find("Bundle params = new Bundle();", launch_auth_start)
    launch_auth = launch[launch_auth_start:launch_auth_end]
    require(
        launch_auth_start >= 0
        and launch_auth_end > launch_auth_start
        and "req.settings.allow_app_hash = false;" in launch_auth
        and "req.settings.allow_firebase = false;" in launch_auth
        and "PushListenerController.GooglePushListenerServiceProvider.INSTANCE.hasServices()"
        not in launch_auth,
        "account-deletion phone confirmation must not advertise Firebase or app-hash support",
    )
    passport_auth_start = passport.find("final TL_account.sendVerifyPhoneCode req")
    passport_auth_end = passport.find(
        "if (req.settings.allow_flashcall)", passport_auth_start
    )
    passport_auth = passport[passport_auth_start:passport_auth_end]
    require(
        passport_auth_start >= 0
        and passport_auth_end > passport_auth_start
        and "req.settings.allow_app_hash = false;" in passport_auth
        and "req.settings.allow_firebase = false;" in passport_auth
        and "PushListenerController.GooglePushListenerServiceProvider.INSTANCE.hasServices()"
        not in passport_auth,
        "Passport phone verification must not advertise SMS Retriever support",
    )

    firebase_response = login.find(
        "if (res.type instanceof TLRPC.TL_auth_sentCodeTypeFirebaseSms"
    )
    disabled_fallback = login.find(
        "if (!BuildVars.USE_FIREBASE_SMS_AUTH)", firebase_response
    )
    services_check = login.find(
        "if (PushListenerController.GooglePushListenerServiceProvider.INSTANCE.hasServices())",
        firebase_response,
    )
    require(
        firebase_response >= 0
        and disabled_fallback > firebase_response
        and services_check > disabled_fallback,
        "an unexpected Firebase response must fall back before touching Google Services",
    )
    fallback_block = login[disabled_fallback:services_check]
    require(
        "isRequestingFirebaseSms = true;" in login[firebase_response:disabled_fallback]
        and 'resendCodeFromSafetyNet(params, res, "FIREBASE_SMS_AUTH_DISABLED");'
        in fallback_block,
        "the non-Firebase resend path must be active instead of returning as a no-op",
    )
    resend_start = login.find("private void resendCodeFromSafetyNet(")
    resend_end = login.find("public static String errorString(", resend_start)
    resend_body = login[resend_start:resend_end]
    require(
        resend_start >= 0
        and resend_end > resend_start
        and "ConnectionsManager.RequestFlagTryDifferentDc" in resend_body
        and "ConnectionsManager.RequestFlagEnableUnauthorized" in resend_body,
        "the pre-login fallback request must work on an unauthorized or migrated DC",
    )

    print("login Firebase independence check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
