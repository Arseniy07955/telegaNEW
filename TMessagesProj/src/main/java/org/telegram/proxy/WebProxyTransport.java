package org.telegram.proxy;

import android.annotation.SuppressLint;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.SystemClock;
import android.text.TextUtils;
import android.util.Base64;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.SslErrorHandler;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.net.http.SslError;

import androidx.webkit.JavaScriptReplyProxy;
import androidx.webkit.WebMessageCompat;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;

import org.json.JSONObject;
import org.telegram.messenger.AndroidUtilities;
import org.telegram.messenger.ApplicationLoader;
import org.telegram.messenger.BuildVars;
import org.telegram.messenger.FileLog;
import org.telegram.ui.Components.ForegroundDetector;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.IDN;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

public final class WebProxyTransport implements ForegroundDetector.Listener {
    private static final int FRAME_OPEN = 0x01;
    private static final int FRAME_DATA = 0x02;
    private static final int FRAME_CLOSE = 0x03;
    private static final int FRAME_WINDOW = 0x04;
    private static final int FRAME_PING = 0x05;
    private static final int FRAME_PONG = 0x06;
    private static final int FRAME_HELLO = 0x10;
    private static final int FRAME_WELCOME = 0x11;
    private static final int FRAME_BYE = 0x1f;
    private static final int FRAME_HEADER = 8;
    private static final int FRAME_MAX_PAYLOAD = 1024 * 1024;
    private static final int DATA_CHUNK = 64 * 1024;
    private static final long INITIAL_WINDOW = 4L * 1024 * 1024;
    private static final int MAX_STREAMS = 64;
    private static final int MAX_OUTBOUND_ITEMS = 8192;
    private static final int MAX_OUTBOUND_BYTES = 64 * 1024 * 1024;
    // Bytes read from one tgnet socket and not yet handed to the carrier.
    // Past this the reader stops, and the loopback socket pushes back on
    // tgnet instead of the bridge buffering a whole upload part.
    private static final int STREAM_QUEUE_LIMIT = 256 * 1024;
    // Classes tgnet announced for sockets the bridge has not accepted yet.
    private static final int MAX_PENDING_CLASSES = 256;
    private static final long HEALTH_CHECK_INTERVAL_MS = 2_000;
    private static final long HEALTH_SUMMARY_INTERVAL_MS = 10_000;
    private static final String BRIDGE_OBJECT = "TelegramWebProxy";

    private static final Object staticLock = new Object();
    private static volatile WebProxyTransport instance;
    private static WebProxyTransport connectionTestInstance;

    private final Object lock = new Object();
    private final String host;
    private final String secret;
    private final String origin;
    private final String bridgeUrl;
    private final String androidNonce;
    private final ServerSocket serverSocket;
    private final ExecutorService ioExecutor = Executors.newCachedThreadPool();
    private final ExecutorService carrierExecutor = Executors.newSingleThreadExecutor();
    private final ScheduledExecutorService healthExecutor = Executors.newSingleThreadScheduledExecutor();
    private final AtomicInteger nextStreamId = new AtomicInteger(1);
    private final Map<Integer, Stream> streams = new HashMap<>();
    private final ArrayDeque<byte[]> outbound = new ArrayDeque<>();
    private final WebProxyFlow.UplinkScheduler scheduler = new WebProxyFlow.UplinkScheduler();
    private final LinkedHashMap<Integer, Integer> pendingClasses = new LinkedHashMap<Integer, Integer>() {
        @Override
        protected boolean removeEldestEntry(Map.Entry<Integer, Integer> eldest) {
            return size() > MAX_PENDING_CLASSES;
        }
    };

    private WebView webView;
    private JavaScriptReplyProxy replyProxy;
    private boolean carrierConnected;
    private boolean stopped;
    private boolean restartScheduled;
    private boolean drainScheduled;
    private boolean healthCheckScheduled;
    private int outboundBytes;

    // Carrier health, all under lock, SystemClock.elapsedRealtime() based
    // (the same CLOCK_BOOTTIME tgnet uses for its own timers).
    private long webViewStartedAt;
    private long lastDownlinkAt;
    private long lastCreditAt;
    private long totalUnacked;
    private long outstandingSince;
    private int downloadStreams;

    // Diagnostics since the last health summary.
    private long lastSummaryAt;
    private long trafficUp;
    private long trafficDown;
    private long trafficWithheldPeak;
    private int trafficUploadBlocked;

    public static int start(String host, String secret) {
        String normalized = normalizeHost(host);
        byte[] secretBytes = decodeSecret(secret);
        if (TextUtils.isEmpty(normalized) || secretBytes == null || !isSupported()) {
            return 0;
        }
        synchronized (staticLock) {
            if (instance != null && instance.host.equals(normalized) && instance.secret.equals(secret)) {
                return instance.serverSocket.getLocalPort();
            }
            if (instance != null) {
                instance.stopInternal();
                instance = null;
            }
            try {
                instance = new WebProxyTransport(normalized, secret, secretBytes);
                instance.startInternal();
                return instance.serverSocket.getLocalPort();
            } catch (Exception e) {
                FileLog.e(e);
                if (instance != null) {
                    instance.stopInternal();
                    instance = null;
                }
                return 0;
            }
        }
    }

    public static int getActiveLocalPort(String host, String secret) {
        String normalized = normalizeHost(host);
        synchronized (staticLock) {
            if (instance != null && instance.host.equals(normalized) && instance.secret.equals(secret)) {
                return instance.serverSocket.getLocalPort();
            }
        }
        return 0;
    }

    public static void stop() {
        synchronized (staticLock) {
            if (instance != null) {
                instance.stopInternal();
                instance = null;
            }
        }
    }

    /**
     * A WEB proxy is the active proxy: every tgnet connection shares one
     * carrier, so file transfers should not fan out over many connections.
     */
    public static boolean isActive() {
        return instance != null;
    }

    private static WebProxyTransport findByBridgePort(int bridgePort) {
        synchronized (staticLock) {
            if (instance != null && instance.serverSocket.getLocalPort() == bridgePort) {
                return instance;
            }
            if (connectionTestInstance != null && connectionTestInstance.serverSocket.getLocalPort() == bridgePort) {
                return connectionTestInstance;
            }
        }
        return null;
    }

    /**
     * tgnet announces the class of a bridge connection right after its
     * connect(), keyed by the socket's local port, which is the remote port
     * of the socket the bridge accepts. Called on the tgnet network thread.
     */
    public static void registerLocalStream(int bridgePort, int localPort, int streamClass) {
        WebProxyTransport target = findByBridgePort(bridgePort);
        if (target != null) {
            target.registerLocalStreamInternal(localPort, streamClass);
        }
    }

    /**
     * tgnet asks whether a bridge connection silent for its receive timeout
     * may keep waiting. Returns the WebProxyFlow.Decision JNI encoding:
     * positive to wait, negative to fail. Called on the tgnet network thread.
     */
    public static long receiveWait(int bridgePort, int localPort, long waitStartedAt) {
        WebProxyTransport target = findByBridgePort(bridgePort);
        if (target == null) {
            return -WebProxyFlow.REASON_CARRIER_DOWN;
        }
        return target.receiveWaitInternal(localPort, waitStartedAt);
    }

    public static int startConnectionCheck(String host, String secret, ReadyCallback readyCallback) {
        String normalized = normalizeHost(host);
        byte[] secretBytes = decodeSecret(secret);
        if (TextUtils.isEmpty(normalized) || secretBytes == null || !isSupported()) {
            return 0;
        }
        synchronized (staticLock) {
            if (connectionTestInstance != null) {
                connectionTestInstance.stopInternal();
                connectionTestInstance = null;
            }
            try {
                connectionTestInstance = new WebProxyTransport(normalized, secret, secretBytes);
                connectionTestInstance.readyCallback = readyCallback;
                connectionTestInstance.startInternal();
                return connectionTestInstance.serverSocket.getLocalPort();
            } catch (Exception e) {
                FileLog.e(e);
                if (connectionTestInstance != null) {
                    connectionTestInstance.stopInternal();
                    connectionTestInstance = null;
                }
                return 0;
            }
        }
    }

    public static void stopConnectionCheck() {
        synchronized (staticLock) {
            if (connectionTestInstance != null) {
                connectionTestInstance.stopInternal();
                connectionTestInstance = null;
            }
        }
    }

    private ReadyCallback readyCallback;

    public interface ReadyCallback {
        void onReady();
    }



    public static boolean isSupported() {
        try {
            return WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)
                    && WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_ARRAY_BUFFER);
        } catch (Throwable e) {
            FileLog.e(e);
            return false;
        }
    }

    public static boolean isValidSecret(String value) {
        return decodeSecret(value) != null;
    }

    public static String normalizeHost(String value) {
        if (value == null) {
            return "";
        }
        value = value.trim();
        if (value.endsWith(".")) {
            value = value.substring(0, value.length() - 1);
        }
        try {
            value = IDN.toASCII(value, IDN.USE_STD3_ASCII_RULES).toLowerCase(Locale.US);
        } catch (Exception e) {
            return "";
        }
        if (value.length() > 253 || value.indexOf('.') <= 0 || value.contains(":") || value.matches("[0-9.]+")) {
            return "";
        }
        String[] labels = value.split("\\.", -1);
        for (String label : labels) {
            if (label.isEmpty() || label.length() > 63 || label.startsWith("-") || label.endsWith("-")) {
                return "";
            }
        }
        return value;
    }

    private WebProxyTransport(String host, String secret, byte[] secretBytes) throws Exception {
        this.host = host;
        this.secret = secret;
        origin = "https://" + host;
        androidNonce = randomToken(32);
        String context = "tdesktop-web-proxy-bridge-v1\n" + host;
        Mac hmac = Mac.getInstance("HmacSHA256");
        hmac.init(new SecretKeySpec(secretBytes, "HmacSHA256"));
        String capability = Base64.encodeToString(
                hmac.doFinal(context.getBytes(StandardCharsets.UTF_8)),
                Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP);
        bridgeUrl = origin + "/?bridge=" + capability + "#android=" + androidNonce;
        serverSocket = new ServerSocket(0, MAX_STREAMS, InetAddress.getByName("127.0.0.1"));
    }

    private void startInternal() {
        ForegroundDetector detector = ForegroundDetector.getInstance();
        if (detector != null) {
            detector.addListener(this);
        }
        ioExecutor.execute(this::acceptLoop);
        AndroidUtilities.runOnUIThread(this::createWebView);
    }

    private void stopInternal() {
        ArrayList<Stream> close;
        synchronized (lock) {
            if (stopped) {
                return;
            }
            stopped = true;
            carrierConnected = false;
            restartScheduled = false;
            close = new ArrayList<>(streams.values());
            streams.clear();
            resetFlowLocked();
            pendingClasses.clear();
            outbound.clear();
            outboundBytes = 0;
            lock.notifyAll();
        }
        try {
            serverSocket.close();
        } catch (Exception ignore) {
        }
        ForegroundDetector detector = ForegroundDetector.getInstance();
        if (detector != null) {
            detector.removeListener(this);
        }
        for (Stream stream : close) {
            closeSocket(stream.socket);
        }
        AndroidUtilities.runOnUIThread(() -> {
            if (replyProxy != null) {
                try {
                    replyProxy.postMessage("{\"t\":\"close\"}");
                } catch (Exception ignore) {
                }
            }
            destroyWebView();
        });
        ioExecutor.shutdownNow();
        carrierExecutor.shutdownNow();
        healthExecutor.shutdownNow();
    }

    private void acceptLoop() {
        while (true) {
            try {
                Socket socket = serverSocket.accept();
                socket.setTcpNoDelay(true);
                Stream stream;
                synchronized (lock) {
                    if (stopped || streams.size() >= MAX_STREAMS) {
                        closeSocket(socket);
                        continue;
                    }
                    int streamId = allocateStreamId();
                    stream = new Stream(streamId, socket);
                    Integer announced = pendingClasses.remove(stream.localPort);
                    stream.streamClass = announced != null ? announced : WebProxyFlow.CLASS_INTERACTIVE;
                    streams.put(streamId, stream);
                    scheduler.add(streamId, stream.streamClass);
                    if (stream.streamClass == WebProxyFlow.CLASS_DOWNLOAD) {
                        downloadStreams++;
                    }
                    if (carrierConnected) {
                        stream.opened = true;
                        sendFrame(FRAME_OPEN, streamId, null);
                    }
                    scheduleHealthCheckLocked();
                }
                Stream value = stream;
                ioExecutor.execute(() -> readLoop(value));
            } catch (Exception e) {
                synchronized (lock) {
                    if (stopped) {
                        return;
                    }
                }
                FileLog.e(e);
                failCarrier("accept_failed");
                return;
            }
        }
    }

    private int allocateStreamId() {
        while (true) {
            int result = nextStreamId.getAndUpdate(value -> value >= 0x00ffffff ? 1 : value + 1);
            if (result != 0 && !streams.containsKey(result)) {
                return result;
            }
        }
    }

    // Reads one tgnet socket into its stream queue. Frames are cut from the
    // queues by pumpUplinkLocked(), in the order WebProxyFlow's scheduler
    // picks, never by the reader: a reader that wrote frames itself would put
    // an upload in front of every chat request.
    private void readLoop(Stream stream) {
        byte[] buffer = new byte[DATA_CHUNK];
        try {
            InputStream input = stream.socket.getInputStream();
            while (true) {
                synchronized (lock) {
                    while (!stopped && streams.get(stream.id) == stream
                            && stream.pendingBytes >= STREAM_QUEUE_LIMIT) {
                        lock.wait();
                    }
                    if (stopped || streams.get(stream.id) != stream) {
                        return;
                    }
                }
                int count = input.read(buffer, 0, buffer.length);
                if (count < 0) {
                    closeStream(stream, true);
                    return;
                }
                if (count == 0) {
                    continue;
                }
                byte[] data = new byte[count];
                System.arraycopy(buffer, 0, data, 0, count);
                synchronized (lock) {
                    if (stopped || streams.get(stream.id) != stream) {
                        return;
                    }
                    stream.pending.addLast(data);
                    stream.pendingBytes += count;
                    markReadyLocked(stream);
                    pumpUplinkLocked();
                }
            }
        } catch (Exception e) {
            closeStream(stream, true);
        }
    }

    private void markReadyLocked(Stream stream) {
        if (stream.opened && stream.sendWindow > 0 && stream.pendingBytes > 0) {
            scheduler.markReady(stream.id);
        }
    }

    // Hands queued stream bytes to the carrier, one frame per scheduler
    // grant: interactive first, bulk round-robin, uploads capped by the bytes
    // the relay has not yet credited back.
    private void pumpUplinkLocked() {
        if (stopped || !carrierConnected || replyProxy == null) {
            return;
        }
        boolean took = false;
        long now = SystemClock.elapsedRealtime();
        while (true) {
            WebProxyFlow.Grant grant = scheduler.next();
            if (grant == null) {
                break;
            }
            Stream stream = streams.get(grant.streamId);
            if (stream == null) {
                scheduler.remove(grant.streamId);
                continue;
            }
            int take = (int) Math.min(Math.min(grant.maxBytes, DATA_CHUNK), Math.min(stream.sendWindow, stream.pendingBytes));
            if (!stream.opened || take <= 0) {
                continue;
            }
            byte[] payload = takePending(stream, take);
            stream.sendWindow -= take;
            scheduler.sent(stream.id, take);
            addUnackedLocked(stream, take, now);
            trafficUp += take;
            took = true;
            sendFrame(FRAME_DATA, stream.id, payload);
            if (stopped || !carrierConnected) {
                // sendFrame() failed the carrier.
                return;
            }
            markReadyLocked(stream);
        }
        if (scheduler.uploadBlocked()) {
            trafficUploadBlocked++;
        }
        if (took) {
            lock.notifyAll();
        }
    }

    private static byte[] takePending(Stream stream, int count) {
        byte[] result = new byte[count];
        int offset = 0;
        while (offset < count) {
            byte[] head = stream.pending.peekFirst();
            int chunk = Math.min(count - offset, head.length - stream.pendingOffset);
            System.arraycopy(head, stream.pendingOffset, result, offset, chunk);
            offset += chunk;
            stream.pendingOffset += chunk;
            if (stream.pendingOffset == head.length) {
                stream.pending.pollFirst();
                stream.pendingOffset = 0;
            }
        }
        stream.pendingBytes -= count;
        return result;
    }

    private void addUnackedLocked(Stream stream, long bytes, long now) {
        if (bytes <= 0) {
            return;
        }
        if (totalUnacked == 0) {
            outstandingSince = now;
        }
        stream.unacked += bytes;
        totalUnacked += bytes;
    }

    private void creditUnackedLocked(Stream stream, long bytes, long now) {
        long credited = Math.min(stream.unacked, bytes);
        stream.unacked -= credited;
        totalUnacked = Math.max(0, totalUnacked - credited);
        if (totalUnacked == 0) {
            outstandingSince = 0;
        }
        lastCreditAt = now;
        scheduler.acknowledged(stream.id, bytes);
        if (stream.unacked == 0) {
            stream.deliveredAt = now;
        }
    }

    // Returns the credit a download stream consumed only while the relay
    // holds less than the stream's share (WebProxyFlow.downlinkCreditTarget),
    // so undelivered media waits in the relay's backend socket instead of in
    // the shared downlink FIFO in front of interactive replies.
    private long releaseDownlinkCreditLocked(Stream stream) {
        long target = WebProxyFlow.downlinkCreditTarget(stream.streamClass, downloadStreams);
        long release = WebProxyFlow.downlinkCreditRelease(stream.receiveWindow, stream.withheld, target);
        if (release <= 0) {
            trafficWithheldPeak = Math.max(trafficWithheldPeak, stream.withheld);
            return 0;
        }
        stream.withheld -= release;
        stream.receiveWindow += release;
        return release;
    }

    private void rebalanceDownlinkCreditLocked() {
        if (!carrierConnected) {
            return;
        }
        ArrayList<long[]> releases = null;
        for (Stream stream : streams.values()) {
            if (stream.opened && stream.withheld > 0) {
                long release = releaseDownlinkCreditLocked(stream);
                if (release > 0) {
                    if (releases == null) {
                        releases = new ArrayList<>();
                    }
                    releases.add(new long[]{stream.id, release});
                }
            }
        }
        if (releases != null) {
            // Sent after the loop: a failing sendFrame() clears the stream map.
            for (long[] release : releases) {
                sendFrame(FRAME_WINDOW, (int) release[0], uint32((int) release[1]));
            }
        }
    }

    private void setStreamClassLocked(Stream stream, int streamClass) {
        streamClass = WebProxyFlow.normalizeClass(streamClass);
        if (stream.streamClass == streamClass) {
            return;
        }
        if (stream.streamClass == WebProxyFlow.CLASS_DOWNLOAD) {
            downloadStreams--;
        }
        if (streamClass == WebProxyFlow.CLASS_DOWNLOAD) {
            downloadStreams++;
        }
        stream.streamClass = streamClass;
        scheduler.setClass(stream.id, streamClass);
        rebalanceDownlinkCreditLocked();
    }

    // Drops the flow state of a stream that left the map.
    private void forgetStreamLocked(Stream stream) {
        scheduler.remove(stream.id);
        totalUnacked = Math.max(0, totalUnacked - stream.unacked);
        stream.unacked = 0;
        if (totalUnacked == 0) {
            outstandingSince = 0;
        }
        stream.pending.clear();
        stream.pendingOffset = 0;
        stream.pendingBytes = 0;
        if (stream.streamClass == WebProxyFlow.CLASS_DOWNLOAD) {
            downloadStreams--;
            rebalanceDownlinkCreditLocked();
        }
    }

    private void resetFlowLocked() {
        scheduler.clear();
        downloadStreams = 0;
        totalUnacked = 0;
        outstandingSince = 0;
    }

    private Stream findStreamByLocalPortLocked(int localPort) {
        for (Stream stream : streams.values()) {
            if (stream.localPort == localPort) {
                return stream;
            }
        }
        return null;
    }

    private void registerLocalStreamInternal(int localPort, int streamClass) {
        synchronized (lock) {
            if (stopped || localPort <= 0) {
                return;
            }
            Stream stream = findStreamByLocalPortLocked(localPort);
            if (stream != null) {
                setStreamClassLocked(stream, streamClass);
            } else {
                pendingClasses.put(localPort, WebProxyFlow.normalizeClass(streamClass));
            }
        }
    }

    private WebProxyFlow.CarrierHealth carrierHealthLocked() {
        WebProxyFlow.CarrierHealth health = new WebProxyFlow.CarrierHealth();
        health.connected = carrierConnected && replyProxy != null && !stopped;
        health.lastDownlinkAt = lastDownlinkAt;
        health.lastCreditAt = lastCreditAt;
        health.unackedBytes = totalUnacked;
        health.outstandingSince = outstandingSince;
        return health;
    }

    private long receiveWaitInternal(int localPort, long waitStartedAt) {
        synchronized (lock) {
            long now = SystemClock.elapsedRealtime();
            Stream stream = findStreamByLocalPortLocked(localPort);
            WebProxyFlow.StreamHealth health = new WebProxyFlow.StreamHealth();
            if (stream != null) {
                health.open = true;
                health.queuedBytes = stream.pendingBytes;
                health.unackedBytes = stream.unacked;
                health.lastReceivedAt = stream.lastReceivedAt;
                health.deliveredAt = stream.deliveredAt;
            }
            WebProxyFlow.Decision decision = WebProxyFlow.decideReceiveWait(now, waitStartedAt, carrierHealthLocked(), health);
            if (BuildVars.LOGS_ENABLED) {
                FileLog.d("web_proxy_receive_wait stream=" + (stream != null ? stream.id : 0)
                        + " class=" + WebProxyFlow.className(stream != null ? stream.streamClass : WebProxyFlow.CLASS_INTERACTIVE)
                        + " verdict=" + (decision.waitMore ? "wait" : "fail")
                        + " reason=" + WebProxyFlow.reasonName(decision.reason)
                        + " wait_ms=" + decision.waitMs
                        + " silence_ms=" + (now - waitStartedAt)
                        + " queued=" + health.queuedBytes
                        + " unacked=" + health.unackedBytes
                        + " carrier_unacked=" + totalUnacked
                        + " last_down_ms=" + sinceMs(now, lastDownlinkAt)
                        + " last_credit_ms=" + sinceMs(now, lastCreditAt));
            }
            return decision.encode();
        }
    }

    private static String sinceMs(long now, long at) {
        return at != 0 ? Long.toString(now - at) : "never";
    }

    private void scheduleHealthCheckLocked() {
        if (healthCheckScheduled || stopped) {
            return;
        }
        healthCheckScheduled = true;
        try {
            healthExecutor.schedule(this::checkHealth, HEALTH_CHECK_INTERVAL_MS, TimeUnit.MILLISECONDS);
        } catch (Exception e) {
            healthCheckScheduled = false;
        }
    }

    // Carrier watchdog: a carrier that holds uplink bytes and hears nothing
    // from the relay for WebProxyFlow.CARRIER_STALL_MS, or a bridge page that
    // never welcomes waiting streams, is recovered once for all streams,
    // instead of every tgnet connection timing out and reconnecting on its own.
    private void checkHealth() {
        String failure = null;
        synchronized (lock) {
            healthCheckScheduled = false;
            if (stopped) {
                return;
            }
            long now = SystemClock.elapsedRealtime();
            WebProxyFlow.CarrierHealth health = carrierHealthLocked();
            if (WebProxyFlow.carrierStalled(now, health)) {
                failure = "stalled silence_ms=" + (now - WebProxyFlow.carrierProgressAt(health)) + " unacked=" + totalUnacked;
            } else if (!carrierConnected && !restartScheduled && webViewStartedAt != 0
                    && now - webViewStartedAt >= WebProxyFlow.WELCOME_TIMEOUT_MS && !streams.isEmpty()) {
                failure = "welcome_timeout waited_ms=" + (now - webViewStartedAt);
            } else if (BuildVars.LOGS_ENABLED && now - lastSummaryAt >= HEALTH_SUMMARY_INTERVAL_MS) {
                logSummaryLocked(now);
            }
            if (failure == null && (!streams.isEmpty() || totalUnacked > 0)) {
                scheduleHealthCheckLocked();
            }
        }
        if (failure != null) {
            failCarrier(failure);
        }
    }

    private void logSummaryLocked(long now) {
        long interval = lastSummaryAt != 0 ? now - lastSummaryAt : HEALTH_SUMMARY_INTERVAL_MS;
        lastSummaryAt = now;
        long queued = 0;
        long withheld = 0;
        for (Stream stream : streams.values()) {
            queued += stream.pendingBytes;
            withheld += stream.withheld;
        }
        long up = trafficUp;
        long down = trafficDown;
        long withheldPeak = trafficWithheldPeak;
        int uploadBlocked = trafficUploadBlocked;
        trafficUp = 0;
        trafficDown = 0;
        trafficWithheldPeak = 0;
        trafficUploadBlocked = 0;
        if (up == 0 && down == 0 && queued == 0 && totalUnacked == 0) {
            return;
        }
        int[] stats = scheduler.stats();
        FileLog.d("web_proxy_carrier event=health connected=" + (carrierConnected ? 1 : 0)
                + " streams(i/d/u)=" + stats[0] + "/" + stats[1] + "/" + stats[2]
                + " ready=" + stats[3] + "/" + stats[4] + "/" + stats[5]
                + " queued=" + queued
                + " unacked=" + totalUnacked
                + " upload_inflight=" + scheduler.uploadInFlight() + "/" + scheduler.uploadLimit()
                + " upload_blocked=" + uploadBlocked
                + " withheld=" + withheld
                + " withheld_peak=" + withheldPeak
                + " up=" + up
                + " down=" + down
                + " interval_ms=" + interval
                + " last_down_ms=" + sinceMs(now, lastDownlinkAt)
                + " last_credit_ms=" + sinceMs(now, lastCreditAt)
                + " outbound=" + outboundBytes);
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void createWebView() {
        synchronized (lock) {
            if (stopped || webView != null) {
                return;
            }
            ForegroundDetector detector = ForegroundDetector.getInstance();
            if (detector != null && detector.isBackground()) {
                restartScheduled = true;
                return;
            }
            restartScheduled = false;
        }
        destroyWebView();
        WebView view;
        try {
            view = new WebView(ApplicationLoader.applicationContext);
        } catch (Exception e) {
            FileLog.e(e);
            failCarrier("webview_create_failed");
            return;
        }
        webView = view;
        synchronized (lock) {
            webViewStartedAt = SystemClock.elapsedRealtime();
        }
        view.setBackgroundColor(Color.TRANSPARENT);
        WebSettings settings = view.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(false);
        settings.setDatabaseEnabled(false);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setCacheMode(WebSettings.LOAD_NO_CACHE);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(false);
        settings.setGeolocationEnabled(false);
        settings.setMediaPlaybackRequiresUserGesture(true);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            settings.setSafeBrowsingEnabled(true);
        }
        view.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView current, WebResourceRequest request) {
                return request.isForMainFrame() && !isBridgeNavigation(request.getUrl());
            }

            @Override
            public void onReceivedSslError(WebView current, SslErrorHandler handler, SslError error) {
                handler.cancel();
                failWebView(current);
            }

            @Override
            public void onReceivedError(WebView current, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    failWebView(current);
                }
            }

            @Override
            public void onReceivedHttpError(WebView current, WebResourceRequest request, WebResourceResponse response) {
                if (request.isForMainFrame()) {
                    failWebView(current);
                }
            }

            @Override
            public boolean onRenderProcessGone(WebView current, RenderProcessGoneDetail detail) {
                failWebView(current);
                return true;
            }
        });
        Set<String> rules = new HashSet<>();
        rules.add(origin);
        WebViewCompat.addWebMessageListener(view, BRIDGE_OBJECT, rules, this::onWebMessage);
        view.loadUrl(bridgeUrl);
    }

    private void failWebView(WebView source) {
        if (source == webView) {
            failCarrier("webview_error");
        }
    }

    private boolean isBridgeNavigation(Uri value) {
        return value != null
                && "https".equals(value.getScheme())
                && host.equals(value.getHost())
                && value.getPort() == -1
                && "/".equals(value.getPath())
                && value.getQueryParameterNames().size() == 1
                && value.getQueryParameterNames().contains("bridge");
    }

    private void onWebMessage(
            WebView sourceView,
            WebMessageCompat message,
            Uri sourceOrigin,
            boolean isMainFrame,
            JavaScriptReplyProxy sourceReplyProxy) {
        if (sourceView != webView || !isMainFrame || !origin.equals(sourceOrigin.toString())) {
            return;
        }
        if (message.getType() == WebMessageCompat.TYPE_STRING) {
            handleControl(message.getData(), sourceReplyProxy);
        } else if (message.getType() == WebMessageCompat.TYPE_ARRAY_BUFFER) {
            synchronized (lock) {
                if (stopped || replyProxy == null || replyProxy != sourceReplyProxy) {
                    return;
                }
            }
            byte[] data = message.getArrayBuffer();
            carrierExecutor.execute(() -> processFrames(data));
        }
    }

    private void handleControl(String data, JavaScriptReplyProxy sourceReplyProxy) {
        try {
            JSONObject object = new JSONObject(data);
            String type = object.optString("t");
            if ("tproxy-android-init".equals(type)
                    && object.optInt("v") == 1
                    && androidNonce.equals(object.optString("nonce"))) {
                synchronized (lock) {
                    if (stopped || replyProxy != null) {
                        return;
                    }
                    replyProxy = sourceReplyProxy;
                }
                sendFrame(FRAME_HELLO, 0, new byte[]{1});
                return;
            }
            if ("close".equals(type) || "failed".equals(object.optString("state"))) {
                failCarrier("page_" + ("close".equals(type) ? "close" : "failed"));
            }
        } catch (Exception ignore) {
        }
    }

    private void processFrames(byte[] input) {
        synchronized (lock) {
            lastDownlinkAt = SystemClock.elapsedRealtime();
        }
        int offset = 0;
        while (offset < input.length) {
            if (input.length - offset < FRAME_HEADER) {
                failCarrier("protocol_error");
                return;
            }
            int type = input[offset] & 0xff;
            int streamId = (input[offset + 1] & 0xff) << 16
                    | (input[offset + 2] & 0xff) << 8
                    | (input[offset + 3] & 0xff);
            long length = (input[offset + 4] & 0xffL) << 24
                    | (input[offset + 5] & 0xffL) << 16
                    | (input[offset + 6] & 0xffL) << 8
                    | (input[offset + 7] & 0xffL);
            long end = offset + FRAME_HEADER + length;
            if (length > FRAME_MAX_PAYLOAD || end > input.length) {
                failCarrier("protocol_error");
                return;
            }
            byte[] payload = new byte[(int) length];
            System.arraycopy(input, offset + FRAME_HEADER, payload, 0, payload.length);
            if (!processFrame(type, streamId, payload)) {
                failCarrier("protocol_error type=" + type);
                return;
            }
            offset = (int) end;
        }
    }

    private boolean processFrame(int type, int streamId, byte[] payload) {
        if (streamId == 0) {
            if (type == FRAME_WELCOME && payload.length == 0) {
                ArrayList<Stream> open;
                ReadyCallback callback;
                synchronized (lock) {
                    if (stopped || carrierConnected) {
                        return false;
                    }
                    carrierConnected = true;
                    webViewStartedAt = 0;
                    lastDownlinkAt = SystemClock.elapsedRealtime();
                    open = new ArrayList<>(streams.values());
                    for (Stream stream : open) {
                        stream.opened = true;
                        sendFrame(FRAME_OPEN, stream.id, null);
                        markReadyLocked(stream);
                    }
                    pumpUplinkLocked();
                    scheduleHealthCheckLocked();
                    if (BuildVars.LOGS_ENABLED) {
                        FileLog.d("web_proxy_carrier event=connected streams=" + open.size());
                    }
                    callback = readyCallback;
                    lock.notifyAll();
                }
                if (callback != null) {
                    AndroidUtilities.runOnUIThread(callback::onReady);
                }
                return true;
            } else if (type == FRAME_PING && payload.length <= 64) {
                sendFrame(FRAME_PONG, 0, payload);
                return true;
            } else if (type == FRAME_BYE && payload.length <= FRAME_MAX_PAYLOAD) {
                return false;
            }
            return false;
        }
        Stream stream;
        synchronized (lock) {
            stream = streams.get(streamId);
            if (stream == null) {
                return type == FRAME_DATA || type == FRAME_WINDOW || type == FRAME_CLOSE;
            }
        }
        if (type == FRAME_DATA) {
            if (payload.length == 0) {
                return false;
            }
            synchronized (lock) {
                if (stream.receiveWindow < payload.length) {
                    return false;
                }
                stream.receiveWindow -= payload.length;
                stream.lastReceivedAt = SystemClock.elapsedRealtime();
                trafficDown += payload.length;
            }
            try {
                OutputStream output = stream.socket.getOutputStream();
                output.write(payload);
                synchronized (lock) {
                    if (streams.get(stream.id) == stream) {
                        stream.withheld += payload.length;
                        long release = releaseDownlinkCreditLocked(stream);
                        if (release > 0) {
                            sendFrame(FRAME_WINDOW, stream.id, uint32((int) release));
                        }
                    }
                }
            } catch (Exception e) {
                closeStream(stream, true);
            }
            return true;
        } else if (type == FRAME_WINDOW && payload.length == 4) {
            long amount = ByteBuffer.wrap(payload).getInt() & 0xffffffffL;
            if (amount == 0) {
                return false;
            }
            synchronized (lock) {
                if (stream.sendWindow > 0xffffffffL - amount) {
                    return false;
                }
                stream.sendWindow += amount;
                if (streams.get(stream.id) == stream) {
                    creditUnackedLocked(stream, amount, SystemClock.elapsedRealtime());
                    markReadyLocked(stream);
                    pumpUplinkLocked();
                }
            }
            return true;
        } else if (type == FRAME_CLOSE && payload.length == 0) {
            closeStream(stream, false);
            return true;
        }
        return false;
    }

    private void closeStream(Stream stream, boolean notifyRelay) {
        boolean sendClose = false;
        synchronized (lock) {
            if (streams.get(stream.id) != stream) {
                return;
            }
            streams.remove(stream.id);
            forgetStreamLocked(stream);
            sendClose = notifyRelay && carrierConnected && stream.opened;
            lock.notifyAll();
        }
        closeSocket(stream.socket);
        if (sendClose) {
            sendFrame(FRAME_CLOSE, stream.id, null);
        }
        synchronized (lock) {
            // Upload room freed by the stream may unblock another one.
            pumpUplinkLocked();
        }
    }

    private void sendFrame(int type, int streamId, byte[] payload) {
        if (payload == null) {
            payload = new byte[0];
        }
        byte[] frame = ByteBuffer.allocate(FRAME_HEADER + payload.length)
                .put((byte) type)
                .put((byte) (streamId >> 16))
                .put((byte) (streamId >> 8))
                .put((byte) streamId)
                .putInt(payload.length)
                .put(payload)
                .array();
        boolean schedule;
        synchronized (lock) {
            if (stopped) {
                return;
            }
            if (!carrierConnected && type != FRAME_HELLO && type != FRAME_PONG) {
                // Stream frames for a carrier that is gone must not reach the
                // next bridge page ahead of its HELLO.
                return;
            }
            if (outbound.size() >= MAX_OUTBOUND_ITEMS
                    || outboundBytes > MAX_OUTBOUND_BYTES - frame.length) {
                failCarrier("outbound_overflow bytes=" + outboundBytes);
                return;
            }
            outbound.add(frame);
            outboundBytes += frame.length;
            schedule = !drainScheduled;
            drainScheduled = true;
        }
        if (schedule) {
            AndroidUtilities.runOnUIThread(this::drainOutbound);
        }
    }

    private void drainOutbound() {
        while (true) {
            byte[] frame;
            JavaScriptReplyProxy target;
            synchronized (lock) {
                target = replyProxy;
                if (stopped || target == null || outbound.isEmpty()) {
                    drainScheduled = false;
                    return;
                }
                frame = outbound.removeFirst();
                outboundBytes -= frame.length;
            }
            try {
                target.postMessage(frame);
            } catch (Exception e) {
                FileLog.e(e);
                synchronized (lock) {
                    drainScheduled = false;
                }
                failCarrier("post_failed");
                return;
            }
        }
    }

    // Tears the carrier down once for all of its streams and recreates the
    // bridge page, which opens a fresh relay session.
    private void failCarrier(String reason) {
        ArrayList<Stream> close;
        synchronized (lock) {
            if (stopped || restartScheduled) {
                return;
            }
            if (BuildVars.LOGS_ENABLED) {
                FileLog.d("web_proxy_carrier event=lost reason=" + reason
                        + " connected=" + (carrierConnected ? 1 : 0)
                        + " streams=" + streams.size()
                        + " unacked=" + totalUnacked
                        + " outbound=" + outboundBytes);
            }
            restartScheduled = true;
            carrierConnected = false;
            replyProxy = null;
            webViewStartedAt = 0;
            outbound.clear();
            outboundBytes = 0;
            close = new ArrayList<>(streams.values());
            streams.clear();
            resetFlowLocked();
            lock.notifyAll();
        }
        for (Stream stream : close) {
            closeSocket(stream.socket);
        }
        AndroidUtilities.runOnUIThread(() -> {
            destroyWebView();
            synchronized (lock) {
                if (stopped) {
                    return;
                }
            }
            AndroidUtilities.runOnUIThread(this::createWebView, 1000);
        });
    }

    @Override
    public void onBecameForeground() {
        AndroidUtilities.runOnUIThread(this::createWebView);
    }

    @Override
    public void onBecameBackground() {
    }

    private void destroyWebView() {
        WebView value = webView;
        webView = null;
        replyProxy = null;
        if (value != null) {
            try {
                value.stopLoading();
                value.loadUrl("about:blank");
                value.removeAllViews();
                value.destroy();
            } catch (Exception e) {
                FileLog.e(e);
            }
        }
    }

    private static byte[] decodeSecret(String value) {
        if (value == null) {
            return null;
        }
        value = value.trim();
        byte[] result;
        if ((value.length() == 32 || value.length() == 34) && value.matches("[0-9a-fA-F]+")) {
            result = new byte[value.length() / 2];
            for (int i = 0; i < result.length; i++) {
                result[i] = (byte) Integer.parseInt(value.substring(i * 2, i * 2 + 2), 16);
            }
        } else {
            try {
                result = Base64.decode(value, Base64.URL_SAFE | Base64.NO_WRAP);
            } catch (Exception e) {
                return null;
            }
        }
        if (result.length == 16 || result.length == 17 && (result[0] & 0xff) == 0xdd) {
            return result;
        }
        return null;
    }

    private static String randomToken(int size) {
        byte[] bytes = new byte[size];
        new SecureRandom().nextBytes(bytes);
        return Base64.encodeToString(bytes, Base64.URL_SAFE | Base64.NO_PADDING | Base64.NO_WRAP);
    }

    private static byte[] uint32(int value) {
        return ByteBuffer.allocate(4).putInt(value).array();
    }

    private static void closeSocket(Socket socket) {
        try {
            socket.close();
        } catch (Exception ignore) {
        }
    }

    private static final class Stream {
        private final int id;
        private final Socket socket;
        // tgnet's local port of this connection: the key tgnet uses for its
        // class announcement and its receive-wait question.
        private final int localPort;
        private int streamClass = WebProxyFlow.CLASS_INTERACTIVE;
        private long sendWindow = INITIAL_WINDOW;
        private long receiveWindow = INITIAL_WINDOW;
        // Consumed by tgnet but not yet returned to the relay, see
        // releaseDownlinkCreditLocked().
        private long withheld;
        private boolean opened;

        // Read from tgnet, not yet handed to the carrier.
        private final ArrayDeque<byte[]> pending = new ArrayDeque<>();
        private int pendingOffset;
        private int pendingBytes;

        // Handed to the carrier, not yet credited back by the relay.
        private long unacked;
        private long lastReceivedAt;
        private long deliveredAt;

        private Stream(int id, Socket socket) {
            this.id = id;
            this.socket = socket;
            this.localPort = socket.getPort();
        }
    }
}
