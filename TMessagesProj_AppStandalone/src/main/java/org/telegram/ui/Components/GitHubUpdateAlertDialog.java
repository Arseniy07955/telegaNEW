package org.telegram.ui.Components;

import android.app.Activity;
import android.content.Context;
import android.text.TextUtils;

import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.ApplicationLoader;
import org.telegram.messenger.BetaUpdate;
import org.telegram.messenger.BuildConfig;
import org.telegram.messenger.LocaleController;
import org.telegram.messenger.R;
import org.telegram.ui.ActionBar.AlertDialog;

import java.io.File;

/** A compact release dialog shared by GitHub dev and stable update channels. */
public final class GitHubUpdateAlertDialog {

    private GitHubUpdateAlertDialog() {
    }

    public static void show(Context context, BetaUpdate update) {
        File downloadedFile = ApplicationLoader.applicationLoaderInstance.getDownloadedUpdateFile();
        String title = LocaleController.getString("dev".equalsIgnoreCase(BuildConfig.ZASTO_UPDATE_CHANNEL)
                ? R.string.AppUpdateBeta
                : R.string.AppUpdate);
        String message = LocaleController.formatString(
                R.string.AppBetaUpdateVersion,
                update.version,
                String.valueOf(update.versionCode));
        if (!TextUtils.isEmpty(update.changelog)) {
            message += "\n\n" + update.changelog;
        }

        AlertDialog.Builder builder = new AlertDialog.Builder(context)
                .setTitle(title)
                .setMessage(message)
                .setNegativeButton(LocaleController.getString(R.string.AppUpdateRemindMeLater), null);
        if (downloadedFile != null) {
            builder.setPositiveButton(LocaleController.getString(R.string.AppUpdateNow), (dialog, which) -> {
                File file = ApplicationLoader.applicationLoaderInstance.getDownloadedUpdateFile();
                Activity activity = AndroidUtilities.findActivity(context);
                if (file != null && activity != null) {
                    AndroidUtilities.openForView(file, "ZaStoGram.apk", "application/vnd.android.package-archive", activity, null, false);
                }
            });
        } else {
            builder.setPositiveButton(LocaleController.getString(R.string.AppUpdateDownloadNow), (dialog, which) ->
                    ApplicationLoader.applicationLoaderInstance.downloadUpdate());
        }
        builder.show();
    }
}
