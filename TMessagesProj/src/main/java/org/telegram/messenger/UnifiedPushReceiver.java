package org.telegram.messenger;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Receives endpoints and wake-ups from the UnifiedPush distributor. */
public class UnifiedPushReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null) {
            return;
        }
        try {
            UnifiedPushController.onReceive(context, intent);
        } catch (Throwable e) {
            FileLog.e(e);
        }
    }
}
