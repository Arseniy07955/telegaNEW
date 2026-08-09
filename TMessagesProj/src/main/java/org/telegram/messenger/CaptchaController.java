package org.telegram.messenger;

import org.telegram.tgnet.ConnectionsManager;

public class CaptchaController {

    public static void request(int currentAccount, int requestToken, String action, String keyId) {
        // Google's reCAPTCHA SDK depends on Play Integrity. Its upstream failure paths
        // already resume MTProto with a RECAPTCHA_FAILED_* marker, so do that immediately
        // without loading Google code on firmware without Google Play Services.
        FileLog.d("CaptchaController: Google reCAPTCHA disabled, returning fallback for action=" + action);
        ConnectionsManager.native_receivedCaptchaResult(
                currentAccount,
                new int[]{requestToken},
                "RECAPTCHA_FAILED_DISABLED");
    }
}
