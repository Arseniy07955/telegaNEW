/*
 * This is the source code of Telegram for Android v. 5.x.x.
 * It is licensed under GNU GPL v. 2 or later.
 * You should have received a copy of the license in this archive (see LICENSE).
 *
 * Copyright Nikolai Kudashov, 2013-2018.
 */

package org.telegram.messenger;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class AppStartReceiver extends BroadcastReceiver {

    public void onReceive(Context context, Intent intent) {
        if (intent != null && (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction()) || "org.telegram.start".equals(intent.getAction()))) {
            // A BOOT_COMPLETED receiver has a temporary exemption for starting
            // a foreground service. Start it before onReceive() returns instead
            // of posting work which may run after that exemption is gone.
            SharedConfig.loadConfig();
            if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction()) && SharedConfig.passcodeHash.length() > 0) {
                SharedConfig.appLocked = true;
                SharedConfig.saveConfig();
            }
            ApplicationLoader.startPushService();
        }
    }
}
