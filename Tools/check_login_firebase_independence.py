#!/usr/bin/env python3
"""Guard ZaStoGram phone login against accidental Firebase/GMS dependencies."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BUILD_VARS = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/BuildVars.java"
LOGIN_ACTIVITY = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/LoginActivity.java"
CONNECTIONS_MANAGER = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"
ANDROID_UTILITIES = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/AndroidUtilities.java"
ANDROID_MANIFEST = ROOT / "TMessagesProj/src/main/AndroidManifest.xml"
MODULE_GRADLE = ROOT / "TMessagesProj/build.gradle"
CAPTCHA_CONTROLLER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/CaptchaController.java"
LAUNCH_ACTIVITY = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/LaunchActivity.java"
PASSPORT_ACTIVITY = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/PassportActivity.java"


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"login Firebase independence check failed: {message}", file=sys.stderr)
        raise SystemExit(1)


def main() -> int:
    build_vars = BUILD_VARS.read_text(encoding="utf-8")
    login = LOGIN_ACTIVITY.read_text(encoding="utf-8")
    connections_manager = CONNECTIONS_MANAGER.read_text(encoding="utf-8")
    android_utilities = ANDROID_UTILITIES.read_text(encoding="utf-8")
    android_manifest = ANDROID_MANIFEST.read_text(encoding="utf-8")
    module_gradle = MODULE_GRADLE.read_text(encoding="utf-8")
    captcha_controller = CAPTCHA_CONTROLLER.read_text(encoding="utf-8")
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
        "public static final boolean USE_PLAY_INTEGRITY = false;" in build_vars
        and "com.google.android.play:integrity" not in module_gradle
        and "com.google.android.gms:play-services-safetynet" not in module_gradle
        and "com.google.android.recaptcha:recaptcha" not in module_gradle
        and "IntegrityManagerFactory" not in connections_manager
        and "PLAYINTEGRITY_FAILED_EXCEPTION_DISABLED" in connections_manager,
        "Play Integrity and SafetyNet must be absent while native auth requests always receive a fallback",
    )
    require(
        "com.google.android.recaptcha" not in captcha_controller
        and "RECAPTCHA_FAILED_DISABLED" in captcha_controller
        and "native_receivedCaptchaResult" in captcha_controller,
        "Google reCAPTCHA must resume native requests through the no-GMS fallback",
    )
    require(
        "public static final boolean USE_SMS_RETRIEVER = false;" in build_vars
        and "waitingForSms && BuildVars.USE_SMS_RETRIEVER" in android_utilities
        and 'android:name=".SmsReceiver"\n            android:enabled="false"' in android_manifest,
        "SMS Retriever must not be started or exposed as a broadcast receiver",
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
    require(
        "PHONE_CODE_REQUEST_TIMEOUT_MS" in login
        and "schedulePhoneCodeRequestTimeout(reqId);" in login
        and "cancelPhoneCodeRequestTimeout();\n                nextPressed = false;" in login,
        "auth.sendCode must have a bounded UI watchdog and cancel it on completion",
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
        'FileLog.d("{FIREBASE_SMS_AUTH_DISABLED}', firebase_response
    )
    firebase_block_end = login.find(
        'params.putString("phoneHash", res.phone_code_hash);', firebase_response
    )
    require(
        firebase_response >= 0
        and disabled_fallback > firebase_response
        and firebase_block_end > disabled_fallback,
        "an unexpected Firebase response must immediately fall back",
    )
    fallback_block = login[disabled_fallback:firebase_block_end]
    require(
        "isRequestingFirebaseSms = true;" in login[firebase_response:disabled_fallback]
        and 'resendCodeWithoutFirebase(params, res, "FIREBASE_SMS_AUTH_DISABLED");'
        in fallback_block,
        "the non-Firebase resend path must be active instead of returning as a no-op",
    )
    require(
        "IntegrityManagerFactory" not in fallback_block
        and "SafetyNet.getClient" not in fallback_block
        and "GooglePushListenerServiceProvider" not in fallback_block,
        "the Firebase response fallback must contain no Google Services path",
    )
    resend_start = login.find("private void resendCodeWithoutFirebase(")
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
