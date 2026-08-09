package org.telegram.ui.Components;

import android.app.Activity;
import android.content.Context;
import android.text.TextUtils;

import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.ApplicationLoader;
import org.telegram.messenger.BetaUpdate;
import org.telegram.messenger.ForgejoUpdaterController;
import org.telegram.messenger.LocaleController;
import org.telegram.messenger.R;
import org.telegram.ui.ActionBar.AlertDialog;

import java.io.File;

/** A compact release dialog shared by Forgejo dev and stable update channels. */
public final class ForgejoUpdateAlertDialog {

    private ForgejoUpdateAlertDialog() {
    }

    public static boolean show(Context context, BetaUpdate update) {
        Activity activity = AndroidUtilities.findActivity(context);
        if (activity == null || activity.isFinishing()) {
            return false;
        }
        ForgejoUpdaterController updater = ForgejoUpdaterController.getInstance();
        File downloadedFile = ApplicationLoader.applicationLoaderInstance.getDownloadedUpdateFile();
        String title = LocaleController.getString(ForgejoUpdaterController.isDevChannel()
                ? R.string.AppUpdateBeta
                : R.string.AppUpdate);
        String message = LocaleController.formatString(
                R.string.AppBetaUpdateVersion,
                update.version,
                String.valueOf(update.versionCode));
        if (!TextUtils.isEmpty(update.changelog)) {
            message += "\n\n" + update.changelog;
        }

        AlertDialog.Builder builder = new AlertDialog.Builder(activity)
                .setTitle(title)
                .setMessage(message)
                .setNegativeButton(LocaleController.getString(R.string.AppUpdateRemindMeLater), (dialog, which) ->
                        updater.remindAboutCurrentUpdateLater())
                .setNeutralButton(LocaleController.getString(R.string.AppUpdateSkipVersion), (dialog, which) ->
                        updater.skipCurrentUpdate())
                .forceVerticalButtons();
        if (downloadedFile != null) {
            builder.setPositiveButton(LocaleController.getString(R.string.AppUpdateNow), (dialog, which) -> {
                updater.remindAboutCurrentUpdateLater();
                File file = ApplicationLoader.applicationLoaderInstance.getDownloadedUpdateFile();
                if (file != null) {
                    AndroidUtilities.openForView(file, "ZaStoGram.apk", "application/vnd.android.package-archive", activity, null, false);
                }
            });
        } else {
            builder.setPositiveButton(LocaleController.getString(R.string.AppUpdateDownloadNow), (dialog, which) -> {
                updater.remindAboutCurrentUpdateLater();
                ApplicationLoader.applicationLoaderInstance.downloadUpdate();
            });
        }
        builder.show();
        return true;
    }
}
