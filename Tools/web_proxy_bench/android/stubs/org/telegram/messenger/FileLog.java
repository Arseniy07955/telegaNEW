package org.telegram.messenger;
public class FileLog {
    public static void d(String s) { android.util.Log.d("WPLOG", s); }
    public static void w(String s) { android.util.Log.w("WPLOG", s); }
    public static void e(String s) { android.util.Log.e("WPLOG", s); }
    public static void e(Throwable t) { android.util.Log.e("WPLOG", "exception", t); }
    public static void e(String s, Throwable t) { android.util.Log.e("WPLOG", s, t); }
}
