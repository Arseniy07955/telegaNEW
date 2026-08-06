package org.telegram.messenger;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.os.Build;
import android.text.TextUtils;

import org.json.JSONArray;
import org.json.JSONObject;
import org.telegram.messenger.web.BuildConfig;
import org.telegram.ui.LaunchActivity;
import org.telegram.ui.web.HttpGetFileTask;
import org.telegram.ui.web.HttpGetTask;

import java.io.File;
import java.util.ArrayList;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * ZaStoGram Standalone updater backed by public GitHub Releases.
 *
 * The update channel is immutable for a built APK: dev builds accept only
 * prereleases, while stable builds accept only regular releases. Drafts and
 * assets for a different CPU architecture are never offered.
 */
public final class GitHubUpdaterController {

    private static final long CHECK_INTERVAL_PAUSED = 24L * 60L * 60L * 1000L;
    private static final long CHECK_INTERVAL = 20L * 60L * 1000L;
    private static final long MAX_APK_BYTES = 512L * 1024L * 1024L;
    private static final Pattern DEV_RELEASE_TAG = Pattern.compile("^zastogram-apk-(\\d+)-(\\d+)$");

    private static GitHubUpdaterController instance;

    public static GitHubUpdaterController getInstance() {
        if (instance == null) {
            instance = new GitHubUpdaterController();
        }
        return instance;
    }

    private String version;
    private int displayVersionCode;
    private String changelog;
    private String releaseTag;
    private long releaseId;
    private long assetId;
    private long updateOrder;
    private String fileUrl;
    private long assetSize;
    private String path;
    private long lastCheck;
    private String installedReleaseTag;

    private boolean checkingForUpdate;
    private boolean showPopupAfterCheck;
    private final ArrayList<Runnable> completionCallbacks = new ArrayList<>();
    private final Runnable scheduledUpdateCheck = () -> checkForUpdate(false, null);

    private boolean downloading;
    private float downloadingProgress;
    private HttpGetFileTask downloadingTask;

    private GitHubUpdaterController() {
        load();
    }

    private SharedPreferences getSharedPreferences() {
        return ApplicationLoader.applicationContext.getSharedPreferences("github_updater", Activity.MODE_PRIVATE);
    }

    private static boolean isDevChannel() {
        return "dev".equalsIgnoreCase(BuildConfig.ZASTO_UPDATE_CHANNEL);
    }

    private static String getChannel() {
        return isDevChannel() ? "dev" : "stable";
    }

    private void load() {
        SharedPreferences prefs = getSharedPreferences();
        version = prefs.getString("version", null);
        displayVersionCode = prefs.getInt("displayVersionCode", 0);
        changelog = prefs.getString("changelog", null);
        releaseTag = prefs.getString("releaseTag", null);
        releaseId = prefs.getLong("releaseId", 0L);
        assetId = prefs.getLong("assetId", 0L);
        updateOrder = prefs.getLong("updateOrder", 0L);
        fileUrl = prefs.getString("fileUrl", null);
        assetSize = prefs.getLong("assetSize", 0L);
        path = prefs.getString("path", null);
        lastCheck = prefs.getLong("lastCheck", 0L);
        installedReleaseTag = prefs.getString("installedReleaseTag", null);

        int currentVersionCode = getCurrentVersionCode();
        int previousInstalledVersionCode = prefs.getInt("installedVersionCode", currentVersionCode);
        String embeddedReleaseTag = BuildConfig.ZASTO_RELEASE_TAG;
        if (!TextUtils.isEmpty(embeddedReleaseTag)) {
            installedReleaseTag = embeddedReleaseTag;
        } else if (currentVersionCode != previousInstalledVersionCode && !TextUtils.isEmpty(releaseTag)) {
            // App data survives an APK update. This also identifies a release installed
            // through the updater when an older build did not embed its GitHub tag.
            installedReleaseTag = releaseTag;
        }

        if (!getChannel().equals(prefs.getString("channel", getChannel()))) {
            clearPendingUpdate(true);
            lastCheck = 0L;
        }
        if (!TextUtils.isEmpty(path) && !new File(path).exists()) {
            path = null;
        }
        File partialFile = getPartialDownloadFile();
        if (partialFile != null && partialFile.isFile() && assetSize > 0L) {
            if (partialFile.length() > assetSize) {
                deleteFile(partialFile);
            } else if (TextUtils.isEmpty(path) && partialFile.length() == assetSize && isDownloadedApkValid(partialFile)) {
                // Recover a download if the process stopped after the last byte was
                // written but before the completed path reached SharedPreferences.
                path = partialFile.getAbsolutePath();
            }
        }
        if (!TextUtils.isEmpty(releaseTag) && releaseTag.equals(getInstalledReleaseTag())) {
            clearPendingUpdate(true);
        }
        save(currentVersionCode);
    }

    private void save() {
        save(getCurrentVersionCode());
    }

    private void save(int installedVersionCode) {
        SharedPreferences.Editor editor = getSharedPreferences().edit();
        putString(editor, "version", version);
        putString(editor, "changelog", changelog);
        putString(editor, "releaseTag", releaseTag);
        putString(editor, "fileUrl", fileUrl);
        putString(editor, "path", path);
        putString(editor, "installedReleaseTag", installedReleaseTag);
        putInt(editor, "displayVersionCode", displayVersionCode);
        putLong(editor, "releaseId", releaseId);
        putLong(editor, "assetId", assetId);
        putLong(editor, "updateOrder", updateOrder);
        putLong(editor, "assetSize", assetSize);
        putLong(editor, "lastCheck", lastCheck);
        editor.putString("channel", getChannel());
        editor.putInt("installedVersionCode", installedVersionCode);
        editor.apply();
    }

    private static void putString(SharedPreferences.Editor editor, String key, String value) {
        if (TextUtils.isEmpty(value)) {
            editor.remove(key);
        } else {
            editor.putString(key, value);
        }
    }

    private static void putInt(SharedPreferences.Editor editor, String key, int value) {
        if (value == 0) {
            editor.remove(key);
        } else {
            editor.putInt(key, value);
        }
    }

    private static void putLong(SharedPreferences.Editor editor, String key, long value) {
        if (value == 0L) {
            editor.remove(key);
        } else {
            editor.putLong(key, value);
        }
    }

    public void checkForUpdate(boolean force, Runnable whenDone) {
        if (whenDone != null) {
            completionCallbacks.add(whenDone);
        } else {
            showPopupAfterCheck = true;
        }
        if (checkingForUpdate) {
            return;
        }

        long interval = ApplicationLoader.mainInterfacePaused ? CHECK_INTERVAL_PAUSED : CHECK_INTERVAL;
        if (!force && System.currentTimeMillis() - lastCheck < interval) {
            finishCheck(false);
            scheduleNextCheck();
            return;
        }

        final String url = getReleasesUrl();
        checkingForUpdate = true;
        new HttpGetTask(result -> AndroidUtilities.runOnUIThread(() -> {
            boolean changed = false;
            try {
                if (result == null) {
                    throw new IllegalStateException("GitHub returned no response");
                }
                ReleaseCandidate candidate = isDevChannel()
                        ? parseNewestPrerelease(new JSONArray(result))
                        : parseStableRelease(new JSONObject(result));
                changed = applyCandidate(candidate);
                lastCheck = System.currentTimeMillis();
                save();
            } catch (Exception e) {
                FileLog.e("Failed to check ZaStoGram updates at " + url, e);
            } finally {
                checkingForUpdate = false;
                finishCheck(changed);
                scheduleNextCheck();
            }
        }))
                .setHeader("Accept", "application/vnd.github+json")
                .setHeader("X-GitHub-Api-Version", "2022-11-28")
                .setHeader("User-Agent", "ZaStoGram-Android-Updater")
                .execute(url);
    }

    private static String getReleasesUrl() {
        String base = "https://api.github.com/repos/" + BuildConfig.ZASTO_GITHUB_REPOSITORY + "/releases";
        return isDevChannel() ? base + "?per_page=100" : base + "/latest";
    }

    private ReleaseCandidate parseNewestPrerelease(JSONArray releases) throws Exception {
        ReleaseCandidate newest = null;
        for (int i = 0; i < releases.length(); i++) {
            JSONObject release = releases.optJSONObject(i);
            ReleaseCandidate candidate = parseRelease(release, true);
            if (candidate != null && (newest == null
                    || candidate.updateOrder > newest.updateOrder
                    || candidate.updateOrder == newest.updateOrder && candidate.releaseId > newest.releaseId)) {
                newest = candidate;
            }
        }
        return newest;
    }

    private ReleaseCandidate parseStableRelease(JSONObject release) throws Exception {
        if (release == null || !release.has("tag_name")) {
            if (release != null && "Not Found".equals(release.optString("message", ""))) {
                // GitHub returns 404 from /releases/latest when a repository has no
                // regular release yet. For a stable build that simply means no update.
                return null;
            }
            throw new IllegalStateException("GitHub latest-release response has no tag_name");
        }
        return parseRelease(release, false);
    }

    private ReleaseCandidate parseRelease(JSONObject release, boolean expectPrerelease) throws Exception {
        if (release == null || release.optBoolean("draft", false)
                || release.optBoolean("prerelease", false) != expectPrerelease) {
            return null;
        }

        String tag = release.optString("tag_name", "").trim();
        long id = release.optLong("id", 0L);
        if (TextUtils.isEmpty(tag) || id == 0L) {
            return null;
        }

        JSONObject asset = findAssetForDevice(release.optJSONArray("assets"));
        if (asset == null) {
            return null;
        }
        String downloadUrl = asset.optString("browser_download_url", "");
        long assetId = asset.optLong("id", 0L);
        long size = asset.optLong("size", 0L);
        if (!downloadUrl.startsWith("https://") || assetId == 0L || size <= 0L || size > MAX_APK_BYTES) {
            return null;
        }

        String publishedAt = release.optString("published_at", "");
        long order = parsePublishedOrder(publishedAt);
        if (order == 0L) {
            order = id;
        }
        String releaseName = release.optString("name", "").trim();
        if (TextUtils.isEmpty(releaseName)) {
            releaseName = tag;
        }
        String body = release.optString("body", "").trim();

        ReleaseCandidate candidate = new ReleaseCandidate();
        candidate.version = releaseName;
        candidate.displayVersionCode = parseDisplayVersionCode(tag);
        candidate.changelog = TextUtils.isEmpty(body) ? null : body;
        candidate.releaseTag = tag;
        candidate.releaseId = id;
        candidate.assetId = assetId;
        candidate.updateOrder = order;
        candidate.fileUrl = downloadUrl;
        candidate.assetSize = size;
        return candidate;
    }

    private static JSONObject findAssetForDevice(JSONArray assets) {
        if (assets == null) {
            return null;
        }
        for (String abi : Build.SUPPORTED_ABIS) {
            String expectedName = "ZaStoGram-standalone-" + abi + ".apk";
            for (int i = 0; i < assets.length(); i++) {
                JSONObject asset = assets.optJSONObject(i);
                if (asset == null || !expectedName.equals(asset.optString("name", ""))) {
                    continue;
                }
                String state = asset.optString("state", "uploaded");
                if (TextUtils.isEmpty(state) || "uploaded".equals(state)) {
                    return asset;
                }
            }
        }
        return null;
    }

    private static long parsePublishedOrder(String publishedAt) {
        if (TextUtils.isEmpty(publishedAt)) {
            return 0L;
        }
        String digits = publishedAt.replaceAll("[^0-9]", "");
        if (digits.length() < 14) {
            return 0L;
        }
        try {
            return Long.parseLong(digits.substring(0, 14));
        } catch (NumberFormatException ignore) {
            return 0L;
        }
    }

    private static int parseDisplayVersionCode(String tag) {
        Matcher matcher = DEV_RELEASE_TAG.matcher(tag);
        if (matcher.matches()) {
            try {
                return Integer.parseInt(matcher.group(1));
            } catch (NumberFormatException ignore) {
                // Fall through to a compact, non-zero display value.
            }
        }
        return Math.max(1, BuildConfig.ZASTO_BUILD_NUMBER + 1);
    }

    private boolean applyCandidate(ReleaseCandidate candidate) {
        boolean hadPendingUpdate = !TextUtils.isEmpty(releaseTag);
        if (candidate == null || candidate.releaseTag.equals(getInstalledReleaseTag())) {
            clearPendingUpdate(true);
            return hadPendingUpdate;
        }

        if (candidate.releaseId == releaseId && candidate.releaseTag.equals(releaseTag)) {
            if ((assetId != 0L && candidate.assetId != assetId)
                    || candidate.assetSize != assetSize
                    || !TextUtils.equals(candidate.fileUrl, fileUrl)) {
                clearDownloadedUpdateFiles();
            }
            version = candidate.version;
            displayVersionCode = candidate.displayVersionCode;
            changelog = candidate.changelog;
            assetId = candidate.assetId;
            updateOrder = candidate.updateOrder;
            fileUrl = candidate.fileUrl;
            assetSize = candidate.assetSize;
            return false;
        }

        clearPendingUpdate(true);
        version = candidate.version;
        displayVersionCode = candidate.displayVersionCode;
        changelog = candidate.changelog;
        releaseTag = candidate.releaseTag;
        releaseId = candidate.releaseId;
        assetId = candidate.assetId;
        updateOrder = candidate.updateOrder;
        fileUrl = candidate.fileUrl;
        assetSize = candidate.assetSize;
        return true;
    }

    private String getInstalledReleaseTag() {
        return !TextUtils.isEmpty(BuildConfig.ZASTO_RELEASE_TAG)
                ? BuildConfig.ZASTO_RELEASE_TAG
                : installedReleaseTag;
    }

    private void clearPendingUpdate(boolean deleteFile) {
        if (deleteFile) {
            clearDownloadedUpdateFiles();
        }
        version = null;
        displayVersionCode = 0;
        changelog = null;
        releaseTag = null;
        releaseId = 0L;
        assetId = 0L;
        updateOrder = 0L;
        fileUrl = null;
        assetSize = 0L;
        path = null;
    }

    private void clearDownloadedUpdateFiles() {
        File partialFile = getPartialDownloadFile();
        if (!TextUtils.isEmpty(path)) {
            File downloadedFile = new File(path);
            deleteFile(downloadedFile);
            if (partialFile != null && downloadedFile.equals(partialFile)) {
                partialFile = null;
            }
        }
        deleteFile(partialFile);
        path = null;
    }

    private File getPartialDownloadFile() {
        if (releaseId == 0L || assetId == 0L) {
            return null;
        }
        return new File(ApplicationLoader.applicationContext.getCacheDir(),
                "zastogram-update-" + releaseId + "-" + assetId + ".apk.part");
    }

    private static void deleteFile(File file) {
        if (file == null || !file.exists()) {
            return;
        }
        try {
            file.delete();
        } catch (Exception e) {
            FileLog.e(e);
        }
    }

    private void finishCheck(boolean changed) {
        boolean shouldShowPopup = showPopupAfterCheck && changed && completionCallbacks.isEmpty();
        showPopupAfterCheck = false;
        ArrayList<Runnable> callbacks = new ArrayList<>(completionCallbacks);
        completionCallbacks.clear();

        if (changed) {
            NotificationCenter.getGlobalInstance().postNotificationName(NotificationCenter.appUpdateAvailable);
        }
        for (Runnable callback : callbacks) {
            callback.run();
        }
        if (shouldShowPopup && !ApplicationLoader.mainInterfacePaused) {
            Context context = LaunchActivity.instance != null ? LaunchActivity.instance : ApplicationLoader.applicationContext;
            BetaUpdate pendingUpdate = getUpdate();
            if (context != null && pendingUpdate != null) {
                ApplicationLoader.applicationLoaderInstance.showCustomUpdateAppPopup(context, pendingUpdate, UserConfig.selectedAccount);
            }
        }
    }

    private void scheduleNextCheck() {
        AndroidUtilities.cancelRunOnUIThread(scheduledUpdateCheck);
        AndroidUtilities.runOnUIThread(scheduledUpdateCheck,
                ApplicationLoader.mainInterfacePaused ? CHECK_INTERVAL_PAUSED : CHECK_INTERVAL);
    }

    public BetaUpdate getUpdate() {
        if (TextUtils.isEmpty(version) || TextUtils.isEmpty(releaseTag) || releaseId == 0L) {
            return null;
        }
        return new BetaUpdate(version, displayVersionCode, changelog, updateOrder);
    }

    public void downloadUpdate() {
        downloadUpdate(false);
    }

    private void downloadUpdate(boolean refreshedRelease) {
        if (downloading || !TextUtils.isEmpty(path) || getUpdate() == null) {
            return;
        }
        if (TextUtils.isEmpty(fileUrl)) {
            if (!refreshedRelease) {
                checkForUpdate(true, () -> downloadUpdate(true));
            }
            return;
        }

        File partialFile = getPartialDownloadFile();
        if (partialFile == null) {
            if (!refreshedRelease) {
                checkForUpdate(true, () -> downloadUpdate(true));
            }
            return;
        }
        if (partialFile.isFile() && assetSize > 0L && partialFile.length() >= assetSize) {
            if (partialFile.length() == assetSize && isDownloadedApkValid(partialFile)) {
                path = partialFile.getAbsolutePath();
                downloadingProgress = 1.0f;
                save();
                NotificationCenter.getGlobalInstance().postNotificationName(NotificationCenter.appUpdateAvailable);
                return;
            }
            deleteFile(partialFile);
        }

        downloading = true;
        downloadingProgress = getCachedDownloadProgress();
        NotificationCenter.getGlobalInstance().postNotificationName(NotificationCenter.appUpdateLoading);
        final long requestedReleaseId = releaseId;
        final long requestedAssetId = assetId;
        downloadingTask = new HttpGetFileTask(
                downloadedFile -> AndroidUtilities.runOnUIThread(() ->
                        onDownloadFinished(downloadedFile, requestedReleaseId, requestedAssetId)),
                progress -> {
                    if (!downloading || releaseId != requestedReleaseId || assetId != requestedAssetId) {
                        return;
                    }
                    downloadingProgress = progress;
                    NotificationCenter.getGlobalInstance().postNotificationName(NotificationCenter.appUpdateLoading);
                }
        ).setOverrideExtension("apk")
                .setDestFile(partialFile)
                .setResumeExistingFile(true)
                .setKeepPartialFileOnCancel(true)
                .setMaxSize(Math.min(MAX_APK_BYTES, assetSize));
        downloadingTask.execute(fileUrl);
    }

    private void onDownloadFinished(File downloadedFile, long requestedReleaseId, long requestedAssetId) {
        downloading = false;
        downloadingTask = null;
        if (releaseId != requestedReleaseId || assetId != requestedAssetId) {
            deleteFile(downloadedFile);
            downloadingProgress = getCachedDownloadProgress();
            NotificationCenter.getGlobalInstance().postNotificationName(NotificationCenter.appUpdateAvailable);
            return;
        }
        if (downloadedFile != null && isDownloadedApkValid(downloadedFile)) {
            path = downloadedFile.getAbsolutePath();
            downloadingProgress = 1.0f;
            save();
        } else if (downloadedFile != null && downloadedFile.isFile()
                && assetSize > 0L && downloadedFile.length() < assetSize) {
            // Some HTTP stacks report a clean EOF instead of throwing when the
            // connection disappears. This is still a resumable partial file.
            downloadingProgress = getCachedDownloadProgress();
            FileLog.d("GitHub update download paused at " + (int) (downloadingProgress * 100) + "%");
        } else if (downloadedFile != null) {
            downloadingProgress = 0.0f;
            deleteFile(downloadedFile);
            FileLog.e("Downloaded GitHub release asset is not a valid ZaStoGram APK");
        } else {
            // Network failures leave the deterministic .part file in cache. A
            // later attempt, including after app restart, resumes that file.
            downloadingProgress = getCachedDownloadProgress();
            if (downloadingProgress > 0.0f) {
                FileLog.d("GitHub update download paused at " + (int) (downloadingProgress * 100) + "%");
            } else {
                FileLog.e("Failed to download GitHub release asset");
            }
        }
        NotificationCenter.getGlobalInstance().postNotificationName(NotificationCenter.appUpdateAvailable);
    }

    private boolean isDownloadedApkValid(File file) {
        if (!file.isFile() || assetSize > 0L && file.length() != assetSize) {
            return false;
        }
        try {
            PackageInfo packageInfo = ApplicationLoader.applicationContext.getPackageManager()
                    .getPackageArchiveInfo(file.getAbsolutePath(), 0);
            return packageInfo != null && ApplicationLoader.getApplicationId().equals(packageInfo.packageName);
        } catch (Exception e) {
            FileLog.e(e);
            return false;
        }
    }

    public void cancelDownloadingUpdate() {
        if (!downloading) {
            return;
        }
        if (downloadingTask != null) {
            downloadingTask.cancel(true);
            downloadingTask = null;
        }
        downloading = false;
        downloadingProgress = getCachedDownloadProgress();
        NotificationCenter.getGlobalInstance().postNotificationName(NotificationCenter.appUpdateAvailable);
    }

    public boolean isDownloading() {
        return downloading;
    }

    public float getDownloadingProgress() {
        return downloadingProgress;
    }

    private float getCachedDownloadProgress() {
        File partialFile = getPartialDownloadFile();
        if (partialFile == null || !partialFile.isFile() || assetSize <= 0L) {
            return 0.0f;
        }
        return Math.max(0.0f, Math.min(1.0f, (float) partialFile.length() / assetSize));
    }

    public File getDownloadedFile() {
        if (TextUtils.isEmpty(path)) {
            return null;
        }
        File file = new File(path);
        if (!file.exists()) {
            path = null;
            save();
            return null;
        }
        return file;
    }

    private int getCurrentVersionCode() {
        try {
            return ApplicationLoader.applicationContext.getPackageManager()
                    .getPackageInfo(ApplicationLoader.applicationContext.getPackageName(), 0).versionCode;
        } catch (Exception e) {
            FileLog.e(e);
            return 0;
        }
    }

    private static final class ReleaseCandidate {
        private String version;
        private int displayVersionCode;
        private String changelog;
        private String releaseTag;
        private long releaseId;
        private long assetId;
        private long updateOrder;
        private String fileUrl;
        private long assetSize;
    }
}
