package org.telegram.messenger;

import android.app.BroadcastOptions;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.os.Build;
import android.text.TextUtils;

import org.telegram.tgnet.ConnectionsManager;

import java.util.List;
import java.util.UUID;

/**
 * UnifiedPush without the connector library.
 *
 * FCM cannot reach ZaStoGram (Telegram's server pushes only to Telegram's own Firebase
 * project), so a killed process never learns about new messages. With a UnifiedPush
 * distributor installed (ntfy and others), the endpoint URL it hands out is registered as a
 * Simple Push token (token_type 4): Telegram then sends a payload-free wake-up to that URL,
 * the distributor starts us, and the regular connection fetches and shows the updates.
 *
 * Protocol constants follow org.unifiedpush.android:connector.
 */
public final class UnifiedPushController {

    private static final String PREFS = "zasto_unifiedpush";
    private static final String KEY_TOKEN = "token";
    private static final String KEY_DISTRIBUTOR = "distributor";
    private static final String KEY_ENDPOINT = "endpoint";

    private static final String DISTRIBUTOR_REGISTER = "org.unifiedpush.android.distributor.REGISTER";
    private static final String DISTRIBUTOR_MESSAGE_ACK = "org.unifiedpush.android.distributor.MESSAGE_ACK";
    private static final String FEATURE_BYTES_MESSAGE = "org.unifiedpush.android.distributor.feature.BYTES_MESSAGE";

    static final String CONNECTOR_NEW_ENDPOINT = "org.unifiedpush.android.connector.NEW_ENDPOINT";
    static final String CONNECTOR_MESSAGE = "org.unifiedpush.android.connector.MESSAGE";
    static final String CONNECTOR_UNREGISTERED = "org.unifiedpush.android.connector.UNREGISTERED";
    static final String CONNECTOR_REGISTRATION_FAILED = "org.unifiedpush.android.connector.REGISTRATION_FAILED";

    private UnifiedPushController() {
    }

    private static SharedPreferences prefs() {
        return ApplicationLoader.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private static String token() {
        final SharedPreferences prefs = prefs();
        String token = prefs.getString(KEY_TOKEN, null);
        if (TextUtils.isEmpty(token)) {
            token = UUID.randomUUID().toString();
            prefs.edit().putString(KEY_TOKEN, token).apply();
        }
        return token;
    }

    /** The distributor to use: the saved one while it is still installed, else the first found. */
    public static String findDistributor() {
        final Context context = ApplicationLoader.applicationContext;
        final PackageManager pm = context.getPackageManager();
        final List<ResolveInfo> receivers;
        try {
            receivers = pm.queryBroadcastReceivers(new Intent(DISTRIBUTOR_REGISTER), 0);
        } catch (Exception e) {
            FileLog.e(e);
            return null;
        }
        final String saved = prefs().getString(KEY_DISTRIBUTOR, null);
        String first = null;
        for (ResolveInfo info : receivers) {
            if (info.activityInfo == null) {
                continue;
            }
            final String pkg = info.activityInfo.packageName;
            if (pkg == null || pkg.equals(context.getPackageName())) {
                continue;
            }
            if (pkg.equals(saved)) {
                return pkg;
            }
            if (first == null) {
                first = pkg;
            }
        }
        return first;
    }

    public static String savedEndpoint() {
        return prefs().getString(KEY_ENDPOINT, null);
    }

    public static void register() {
        final String distributor = findDistributor();
        if (distributor == null) {
            return;
        }
        final Context context = ApplicationLoader.applicationContext;
        prefs().edit().putString(KEY_DISTRIBUTOR, distributor).apply();
        final Intent intent = new Intent(DISTRIBUTOR_REGISTER);
        intent.setPackage(distributor);
        intent.putExtra("token", token());
        intent.putExtra("application", context.getPackageName());
        intent.putExtra("message", "ZaStoGram");
        intent.putExtra("features", new String[] { FEATURE_BYTES_MESSAGE });
        intent.putExtra("pi", PendingIntent.getBroadcast(context, 0, new Intent("org.unifiedpush.dummy_app"), PendingIntent.FLAG_IMMUTABLE));
        send(context, intent);
        if (BuildVars.LOGS_ENABLED) {
            FileLog.d("UnifiedPush register distributor=" + distributor);
        }
    }

    private static void send(Context context, Intent intent) {
        if (Build.VERSION.SDK_INT >= 34) {
            final BroadcastOptions options = BroadcastOptions.makeBasic();
            options.setShareIdentityEnabled(true);
            context.sendBroadcast(intent, null, options.toBundle());
        } else {
            context.sendBroadcast(intent);
        }
    }

    static void onReceive(Context context, Intent intent) {
        final String action = intent.getAction();
        if (action == null || !TextUtils.equals(intent.getStringExtra("token"), token())) {
            return;
        }
        switch (action) {
            case CONNECTOR_NEW_ENDPOINT: {
                final String endpoint = intent.getStringExtra("endpoint");
                if (TextUtils.isEmpty(endpoint)) {
                    return;
                }
                if (BuildVars.LOGS_ENABLED) {
                    FileLog.d("UnifiedPush new endpoint");
                }
                prefs().edit().putString(KEY_ENDPOINT, endpoint).apply();
                AndroidUtilities.runOnUIThread(() -> {
                    ApplicationLoader.postInitApplication();
                    PushListenerController.sendRegistrationToServer(PushListenerController.PUSH_TYPE_SIMPLE, endpoint);
                });
                break;
            }
            case CONNECTOR_MESSAGE: {
                if (BuildVars.LOGS_ENABLED) {
                    FileLog.d("UnifiedPush wake-up");
                }
                acknowledge(context, intent.getStringExtra("id"));
                AndroidUtilities.runOnUIThread(() -> {
                    ApplicationLoader.postInitApplication();
                    for (int a = 0; a < UserConfig.MAX_ACCOUNT_COUNT; a++) {
                        if (UserConfig.getInstance(a).isClientActivated()) {
                            ConnectionsManager.onInternalPushReceived(a);
                            ConnectionsManager.getInstance(a).resumeNetworkMaybe();
                        }
                    }
                });
                break;
            }
            case CONNECTOR_UNREGISTERED:
            case CONNECTOR_REGISTRATION_FAILED: {
                if (BuildVars.LOGS_ENABLED) {
                    FileLog.d("UnifiedPush " + action + " reason=" + intent.getStringExtra("reason"));
                }
                prefs().edit().remove(KEY_ENDPOINT).apply();
                break;
            }
        }
    }

    private static void acknowledge(Context context, String id) {
        final String distributor = prefs().getString(KEY_DISTRIBUTOR, null);
        if (TextUtils.isEmpty(id) || TextUtils.isEmpty(distributor)) {
            return;
        }
        final Intent intent = new Intent(DISTRIBUTOR_MESSAGE_ACK);
        intent.setPackage(distributor);
        intent.putExtra("token", token());
        intent.putExtra("id", id);
        try {
            send(context, intent);
        } catch (Exception e) {
            FileLog.e(e);
        }
    }
}
