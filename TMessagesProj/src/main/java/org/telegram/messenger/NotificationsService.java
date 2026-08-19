/*
 * This is the source code of Telegram for Android v. 1.3.x.
 * It is licensed under GNU GPL v. 2 or later.
 * You should have received a copy of the license in this archive (see LICENSE).
 *
 * Copyright Nikolai Kudashov, 2013-2018.
 */

package org.telegram.messenger;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.IBinder;

import androidx.core.app.NotificationCompat;

import org.telegram.tgnet.ConnectionsManager;
import org.telegram.ui.LaunchActivity;

public class NotificationsService extends Service {

    private static final int NOTIFICATION_ID = 39;
    private static final String CHANNEL_ID = "background_connection";
    private boolean foregroundStarted;

    @Override
    public void onCreate() {
        super.onCreate();
        startInForeground();
        ApplicationLoader.postInitApplication();
        ConnectionsManager.applyBackgroundNetworkPolicyForAllAccounts();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        startInForeground();
        ConnectionsManager.applyBackgroundNetworkPolicyForAllAccounts();
        return START_STICKY;
    }

    private void startInForeground() {
        try {
            NotificationManager notificationManager = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && notificationManager != null) {
                NotificationChannel channel = new NotificationChannel(
                        CHANNEL_ID,
                        getString(R.string.NotificationsBackgroundConnectionChannel),
                        NotificationManager.IMPORTANCE_LOW
                );
                channel.setDescription(getString(R.string.NotificationsBackgroundConnectionChannelInfo));
                channel.setShowBadge(false);
                notificationManager.createNotificationChannel(channel);
            }

            Intent launchIntent = new Intent(this, LaunchActivity.class);
            launchIntent.setAction(Intent.ACTION_MAIN);
            launchIntent.addCategory(Intent.CATEGORY_LAUNCHER);
            PendingIntent contentIntent = PendingIntent.getActivity(
                    this,
                    0,
                    launchIntent,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
            );
            NotificationCompat.Builder builder = new NotificationCompat.Builder(this, CHANNEL_ID)
                    .setSmallIcon(R.drawable.notification)
                    .setContentTitle(getString(R.string.AppName))
                    .setContentText(getString(R.string.NotificationsBackgroundConnectionActive))
                    .setContentIntent(contentIntent)
                    .setCategory(NotificationCompat.CATEGORY_SERVICE)
                    .setPriority(NotificationCompat.PRIORITY_LOW)
                    .setOngoing(true)
                    .setOnlyAlertOnce(true)
                    .setShowWhen(false);
            startForeground(NOTIFICATION_ID, builder.build());
            foregroundStarted = true;
        } catch (Throwable error) {
            FileLog.e(error);
            stopSelf();
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    public void onDestroy() {
        super.onDestroy();
        stopForeground(true);
        SharedPreferences preferences = MessagesController.getGlobalNotificationsSettings();
        if (foregroundStarted && preferences.getBoolean("pushService", true)) {
            Intent intent = new Intent("org.telegram.start");
            intent.setPackage(getPackageName());
            sendBroadcast(intent);
        }
    }
}
