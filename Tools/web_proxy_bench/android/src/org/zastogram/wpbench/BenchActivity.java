package org.zastogram.wpbench;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Base64;
import android.util.Log;
import android.webkit.WebView;

import androidx.webkit.ProxyConfig;
import androidx.webkit.ProxyController;
import androidx.webkit.WebViewCompat;

import org.telegram.messenger.ApplicationLoader;
import org.telegram.proxy.WebProxyTransport;

import java.io.FileOutputStream;
import java.io.InputStream;
import java.security.SecureRandom;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CountDownLatch;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * One bench run per launch (am start -S ... --es key value ...); the result
 * is one "WPBENCH RESULT {json}" logcat line.
 *
 * mode=java (default): the app's own org.telegram.proxy classes, compiled
 * into this APK, carry Bench's workload through a real WebView and the real
 * bridge page. mode=page: the same workload runs inside the bridge page
 * (assets/pagebench.js stands in for the app behind TelegramWebProxy), the
 * ceiling of page + relay + network without the Java side.
 *
 * The WebView reaches the bench relay through the host's CONNECT proxy
 * (proxy=, default 10.0.2.2:28444), so the APK needs no WebView debug flags
 * and is not debuggable: ART compiles it like a release build.
 */
public class BenchActivity extends Activity {
    private static final String HOST = "w.bench.test";
    private static final String SECRET = "000000000000000000000000000000a2";

    private volatile boolean uiLoadRunning;
    private WebView pageView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        ApplicationLoader.applicationContext = getApplicationContext();
        final Map<String, String> params = new HashMap<>();
        Bundle extras = getIntent().getExtras();
        if (extras != null) {
            for (String key : extras.keySet()) {
                params.put(key, String.valueOf(extras.get(key)));
            }
        }
        final CountDownLatch proxied = new CountDownLatch(1);
        ProxyController.getInstance().setProxyOverride(
                new ProxyConfig.Builder().addProxyRule(param(params, "proxy", "10.0.2.2:28444")).build(),
                Runnable::run, proxied::countDown);
        final int uiLoad = Integer.parseInt(param(params, "uiload", "0"));
        if (uiLoad > 0) {
            // Busy main thread: uiLoad ms of work in every 16 ms frame, like a
            // chat list being scrolled while the transfer runs.
            uiLoadRunning = true;
            final Handler handler = new Handler(Looper.getMainLooper());
            handler.post(new Runnable() {
                @Override
                public void run() {
                    if (!uiLoadRunning) {
                        return;
                    }
                    long end = SystemClock.uptimeMillis() + uiLoad;
                    while (SystemClock.uptimeMillis() < end) {
                    }
                    handler.postDelayed(this, Math.max(1, 16 - uiLoad));
                }
            });
        }
        new Thread(() -> {
            try {
                proxied.await();
            } catch (InterruptedException ignore) {
            }
            if ("page".equals(params.get("mode"))) {
                runOnUiThread(() -> runPage(params));
                return;
            }
            int port = WebProxyTransport.start(param(params, "host", HOST), param(params, "secret", SECRET));
            String result;
            if (port == 0) {
                result = "{\"ok\":false,\"error\":\"start failed\"}";
            } else {
                // Class announcement as tgnet does it; transports from before
                // 12f2a119f (upstream DrKLO) have none.
                java.lang.reflect.Method announce = null;
                try {
                    announce = WebProxyTransport.class.getMethod("registerLocalStream", int.class, int.class, int.class);
                } catch (NoSuchMethodException ignore) {
                }
                final java.lang.reflect.Method method = announce;
                result = Bench.run(port, (localPort, cls) -> {
                    if (method != null) {
                        try {
                            method.invoke(null, port, localPort, cls);
                        } catch (Exception e) {
                            throw new RuntimeException(e);
                        }
                    }
                }, params);
            }
            uiLoadRunning = false;
            report(result);
            try {
                Thread.sleep(1500);
            } catch (InterruptedException ignore) {
            }
            WebProxyTransport.stop();
        }, "bench-main").start();
    }

    private static String param(Map<String, String> params, String key, String def) {
        String value = params.get(key);
        return value != null ? value : def;
    }

    private void report(String result) {
        Log.i("WPBENCH", "RESULT " + result);
        try (FileOutputStream out = new FileOutputStream(getFilesDir() + "/result.json")) {
            out.write(result.getBytes("UTF-8"));
        } catch (Exception e) {
            Log.e("WPBENCH", "write", e);
        }
    }

    private void runPage(Map<String, String> params) {
        try {
            byte[] secret = new byte[16];
            secret[15] = (byte) 0xa2;
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(secret, "HmacSHA256"));
            int flags = Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP;
            String capability = Base64.encodeToString(mac.doFinal(("tdesktop-web-proxy-bridge-v1\n" + HOST).getBytes("UTF-8")), flags);
            byte[] nonce = new byte[32];
            new SecureRandom().nextBytes(nonce);
            String[][] defaults = {{"idle", "10"}, {"dc", "20"}, {"ping", "250"}, {"up", "0"}, {"down", "0"}, {"upPart", "131072"},
                    {"upParallel", "16"}, {"upConns", "2"}, {"downPart", "131072"}, {"downParallel", "4"}, {"downConns", "2"}};
            StringBuilder json = new StringBuilder("{");
            for (String[] d : defaults) {
                json.append('"').append(d[0]).append("\":").append(param(params, d[0], d[1])).append(',');
            }
            json.setLength(json.length() - 1);
            json.append('}');
            InputStream in = getAssets().open("pagebench.js");
            byte[] data = new byte[in.available()];
            int offset = 0;
            while (offset < data.length) {
                offset += in.read(data, offset, data.length - offset);
            }
            String js = new String(data, "UTF-8").replace("__PARAMS__", json.toString());
            WebView view = new WebView(this);
            view.getSettings().setJavaScriptEnabled(true);
            Set<String> rules = new HashSet<>();
            rules.add("https://" + HOST);
            final long[] cpuStart = Bench.cpu();
            final double mb = Double.parseDouble(param(params, "up", "0")) + Double.parseDouble(param(params, "down", "0"));
            WebViewCompat.addWebMessageListener(view, "BenchReport", rules, (source, message, origin, main, reply) -> {
                String result = message.getData();
                long[] cpuEnd = Bench.cpu();
                if (mb > 0 && result != null && result.endsWith("}")) {
                    result = result.substring(0, result.length() - 1) + String.format(Locale.US, ",\"cpu_ms_per_MB\":%.1f,\"main_ms_per_MB\":%.1f}",
                            (cpuEnd[0] - cpuStart[0]) / mb, (cpuEnd[1] - cpuStart[1]) / mb);
                }
                report(result);
            });
            WebViewCompat.addDocumentStartJavaScript(view, js, rules);
            pageView = view;
            view.loadUrl("https://" + HOST + "/?bridge=" + capability + "#android=" + Base64.encodeToString(nonce, flags));
        } catch (Exception e) {
            report("{\"ok\":false,\"error\":\"" + e + "\"}");
        }
    }
}
