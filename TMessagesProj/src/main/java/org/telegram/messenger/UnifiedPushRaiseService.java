package org.telegram.messenger;

import android.app.Service;
import android.content.Intent;
import android.os.Binder;
import android.os.IBinder;

/**
 * A distributor binds here before delivering a message: the binding lifts the app to the
 * foreground importance, which lets the wake-up start the push service on Android 12+.
 */
public class UnifiedPushRaiseService extends Service {

    private final Binder binder = new Binder();

    @Override
    public IBinder onBind(Intent intent) {
        return binder;
    }
}
