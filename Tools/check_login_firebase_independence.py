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
        "public static final boolean USE_PHONE_LOGIN_TELEPHONY = false;" in build_vars,
        "phone login must remain independent from SIM and call APIs",
    )
    require(
        "public static boolean SUPPORTS_PASSKEYS = false;" in build_vars,
        "automatic platform passkey login must remain disabled on the fork",
    )
    require(
        'public static String SAFETYNET_KEY = "";' in build_vars,
        "the fork must not ship the official SafetyNet key",
    )
    require(
        'public static String getSmsHash() {\n        return "";' in build_vars,
        "the fork must not advertise an SMS Retriever hash from another package/signature",
    )
    phone_send_start = login.find("TLRPC.TL_codeSettings settings = new TLRPC.TL_codeSettings();")
    phone_send_end = login.find("TLObject req;", phone_send_start)
    phone_send = login[phone_send_start:phone_send_end]
    require(
        phone_send_start >= 0
        and phone_send_end > phone_send_start
        and "settings.allow_app_hash = false;" in phone_send
        and "settings.allow_firebase = false;" in phone_send
        and "PushListenerController.GooglePushListenerServiceProvider.INSTANCE.hasServices()"
        not in phone_send,
        "auth.sendCode must not consult or advertise Firebase services",
    )
    require(
        "settings.allow_app_hash = settings.allow_firebase =" not in login,
        "Google Services availability alone must never enable Firebase phone auth",
    )
    confirm_start = login.find("private void onConfirm(PhoneNumberConfirmView confirmView)")
    confirm_end = login.find("confirmView.animateProgress", confirm_start)
    confirm_block = login[confirm_start:confirm_end]
    require(
        confirm_start >= 0
        and confirm_end > confirm_start
        and "if (BuildVars.USE_PHONE_LOGIN_TELEPHONY" in confirm_block,
        "phone confirmation must not enter the runtime-permission flow",
    )
    require(
        "boolean simcardAvailable = BuildVars.USE_PHONE_LOGIN_TELEPHONY" in login,
        "call and missed-call verification must remain disabled",
    )
    fill_number_start = login.find("public void fillNumber()")
    fill_number_end = login.find("try {", fill_number_start)
    fill_number_prefix = login[fill_number_start:fill_number_end]
    require(
        fill_number_start >= 0
        and fill_number_end > fill_number_start
        and "if (!BuildVars.USE_PHONE_LOGIN_TELEPHONY)" in fill_number_prefix
        and "numberFilled = true;" in fill_number_prefix,
        "login must skip SIM-number autofill and its permission prompt",
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
