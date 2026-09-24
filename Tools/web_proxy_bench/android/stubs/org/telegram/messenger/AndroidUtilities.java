package org.telegram.messenger;
import android.os.Handler;
import android.os.Looper;
public class AndroidUtilities {
    private static final Handler handler = new Handler(Looper.getMainLooper());
    public static void runOnUIThread(Runnable r) { runOnUIThread(r, 0); }
    public static void runOnUIThread(Runnable r, long delay) {
        if (delay == 0) handler.post(r); else handler.postDelayed(r, delay);
    }
    public static void cancelRunOnUIThread(Runnable r) { handler.removeCallbacks(r); }
}
