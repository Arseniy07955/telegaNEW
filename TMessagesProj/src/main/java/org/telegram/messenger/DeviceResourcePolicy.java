/*
 * This is the source code of Telegram for Android.
 * It is licensed under GNU GPL v. 2 or later.
 */

package org.telegram.messenger;

import android.app.ActivityManager;
import android.content.ComponentCallbacks2;
import android.content.Context;
import android.os.SystemClock;

/**
 * Central policy for optional memory-heavy work.
 *
 * Android's device RAM and the per-process heap cap are different constraints. A modern
 * low-end phone can expose eight CPU cores while still giving this process only a 128 MiB
 * heap. CPU-only performance classification therefore must not authorize eager media work.
 */
public final class DeviceResourcePolicy {

    private static final int CONSTRAINED_HEAP_MB = 128;
    private static final int DEFAULT_IMAGE_CACHE_DIVISOR = 7;
    private static final int CONSTRAINED_IMAGE_CACHE_DIVISOR = 10;
    private static final long MEMORY_PRESSURE_HOLD_MS = 2 * 60 * 1000L;

    private static volatile Snapshot snapshot;
    private static volatile long memoryPressureUntilMs;

    private DeviceResourcePolicy() {
    }

    public static boolean isConstrainedDevice() {
        Snapshot value = getSnapshot();
        return value.lowRamDevice || value.memoryClassMb <= CONSTRAINED_HEAP_MB;
    }

    public static int getImageCacheDivisor() {
        return isConstrainedDevice() ? CONSTRAINED_IMAGE_CACHE_DIVISOR : DEFAULT_IMAGE_CACHE_DIVISOR;
    }

    public static boolean allowStoryPreload() {
        return !isConstrainedDevice() && SystemClock.elapsedRealtime() >= memoryPressureUntilMs;
    }

    public static void logConfiguration() {
        if (!BuildVars.LOGS_ENABLED) {
            return;
        }
        Snapshot value = getSnapshot();
        FileLog.d("runtime_resource_policy constrained=" + isConstrainedDevice()
                + " low_ram=" + value.lowRamDevice
                + " memory_class_mb=" + value.memoryClassMb
                + " total_ram_mb=" + value.totalRamMb
                + " image_cache_divisor=" + getImageCacheDivisor()
                + " story_preload=" + allowStoryPreload());
    }

    public static void onTrimMemory(int level) {
        boolean pressureSignal = level == ComponentCallbacks2.TRIM_MEMORY_RUNNING_LOW
                || level == ComponentCallbacks2.TRIM_MEMORY_RUNNING_CRITICAL
                || level >= ComponentCallbacks2.TRIM_MEMORY_BACKGROUND;
        if (pressureSignal) {
            memoryPressureUntilMs = Math.max(memoryPressureUntilMs,
                    SystemClock.elapsedRealtime() + MEMORY_PRESSURE_HOLD_MS);
        }

        boolean evictImages = level == ComponentCallbacks2.TRIM_MEMORY_RUNNING_CRITICAL
                || level >= ComponentCallbacks2.TRIM_MEMORY_MODERATE
                || isConstrainedDevice() && (level == ComponentCallbacks2.TRIM_MEMORY_RUNNING_LOW
                || level >= ComponentCallbacks2.TRIM_MEMORY_UI_HIDDEN);
        if (evictImages) {
            ImageLoader.clearMemoryIfInitialized();
        }
        if (BuildVars.LOGS_ENABLED && (pressureSignal || evictImages)) {
            FileLog.d("runtime_memory_trim level=" + level
                    + " constrained=" + isConstrainedDevice()
                    + " evicted_images=" + evictImages
                    + " preload_suppressed=" + !allowStoryPreload());
        }
    }

    public static void onLowMemory() {
        memoryPressureUntilMs = Math.max(memoryPressureUntilMs,
                SystemClock.elapsedRealtime() + MEMORY_PRESSURE_HOLD_MS);
        ImageLoader.clearMemoryIfInitialized();
        if (BuildVars.LOGS_ENABLED) {
            FileLog.d("runtime_memory_trim level=low_memory constrained=" + isConstrainedDevice()
                    + " evicted_images=true preload_suppressed=true");
        }
    }

    private static Snapshot getSnapshot() {
        Snapshot value = snapshot;
        if (value != null) {
            return value;
        }
        synchronized (DeviceResourcePolicy.class) {
            value = snapshot;
            if (value == null) {
                value = readSnapshot(ApplicationLoader.applicationContext);
                snapshot = value;
            }
        }
        return value;
    }

    private static Snapshot readSnapshot(Context context) {
        int memoryClassMb = Integer.MAX_VALUE;
        boolean lowRamDevice = false;
        long totalRamMb = -1;
        if (context != null) {
            try {
                ActivityManager activityManager = (ActivityManager) context.getSystemService(Context.ACTIVITY_SERVICE);
                if (activityManager != null) {
                    memoryClassMb = activityManager.getMemoryClass();
                    lowRamDevice = activityManager.isLowRamDevice();
                    ActivityManager.MemoryInfo memoryInfo = new ActivityManager.MemoryInfo();
                    activityManager.getMemoryInfo(memoryInfo);
                    totalRamMb = memoryInfo.totalMem / (1024L * 1024L);
                }
            } catch (Throwable ignore) {
            }
        }
        return new Snapshot(memoryClassMb, lowRamDevice, totalRamMb);
    }

    private static final class Snapshot {
        final int memoryClassMb;
        final boolean lowRamDevice;
        final long totalRamMb;

        Snapshot(int memoryClassMb, boolean lowRamDevice, long totalRamMb) {
            this.memoryClassMb = memoryClassMb;
            this.lowRamDevice = lowRamDevice;
            this.totalRamMb = totalRamMb;
        }
    }
}
