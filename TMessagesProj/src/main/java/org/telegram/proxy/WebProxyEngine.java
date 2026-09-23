package org.telegram.proxy;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.ServerSocketChannel;
import java.nio.channels.SocketChannel;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentLinkedQueue;

/**
 * Data path of the WEB proxy, between tgnet's loopback sockets and the
 * bridge page. Plain Java without Android classes (the host JVM runs it in
 * Tools/web_proxy_flow_tests); {@link WebProxyTransport} owns the WebView and
 * plugs it in as the {@link Host}.
 *
 * <p>One thread runs everything: a selector over the loopback listener and
 * every tgnet socket, the frames coming back from the page, the timers, and
 * the batches going to the page. There is no thread per socket, no carrier
 * executor and no Java-side queue of stream bytes:
 * <ul>
 * <li>uplink is pulled, not pushed: a socket is read only when
 * WebProxyFlow.UplinkScheduler grants its stream a frame, directly into the
 * batch for the page. Bytes tgnet has written and the carrier may not take
 * yet stay in the loopback socket, which pushes back on tgnet;</li>
 * <li>all frames produced in one pass (credit, OPEN/CLOSE, DATA) go to the
 * page as one binary message, so the UI thread posts one message per pass
 * instead of one per frame;</li>
 * <li>downlink is written to tgnet without blocking: a stream whose socket is
 * full keeps its bytes (bounded by the credit it holds) while every other
 * stream goes on;</li>
 * <li>one flow-control layer: the relay's per-stream WINDOW credit, plus one
 * adaptive carrier budget per direction (WebProxyFlow.AdaptiveWindow) that
 * bounds bulk bytes queued in the single relay pipe to about one
 * bandwidth-delay product plus a small queue allowance.</li>
 * </ul>
 * The UI thread never takes the engine lock; it only enqueues what the page
 * sent and wakes the selector.
 */
final class WebProxyEngine {
    static final int FRAME_OPEN = 0x01;
    static final int FRAME_DATA = 0x02;
    static final int FRAME_CLOSE = 0x03;
    static final int FRAME_WINDOW = 0x04;
    static final int FRAME_PING = 0x05;
    static final int FRAME_PONG = 0x06;
    static final int FRAME_HELLO = 0x10;
    static final int FRAME_WELCOME = 0x11;
    static final int FRAME_BYE = 0x1f;
    static final int FRAME_HEADER = 8;
    static final int FRAME_MAX_PAYLOAD = 1024 * 1024;
    static final long INITIAL_WINDOW = 4L * 1024 * 1024;

    static final int MAX_STREAMS = 64;
    // One message to the page: one full DATA frame plus control frames. The
    // page sends each message as one WebSocket message as soon as it can,
    // and the relay applies a message only once all of it arrived: a large
    // batch holds its first bytes (and their credit) back by batch/rate, and
    // at the start of a burst, while the browser's TCP is still in slow
    // start, by several round trips (1 MiB batches delayed the first byte at
    // the relay by ~0.8 s at 150 ms on the bench).
    static final int BATCH_LIMIT = 96 * 1024;
    // Classes tgnet announced for sockets the bridge has not accepted yet.
    static final int MAX_PENDING_CLASSES = 256;
    // Frames the page may have handed us and we have not parsed yet.
    static final int MAX_INBOUND_BYTES = 64 * 1024 * 1024;
    static final long HEALTH_CHECK_INTERVAL_MS = 2_000;
    static final long HEALTH_SUMMARY_INTERVAL_MS = 10_000;
    // How often the adaptive windows are fed.
    static final long SAMPLE_INTERVAL_MS = 200;

    /** What the engine needs from its surroundings. */
    interface Host {
        /** Hands one batch of complete frames to the page of {@code token}. */
        void postToPage(int token, byte[] batch);

        /**
         * The carrier failed and every stream is closed; the page must be
         * rebuilt. Called at most once per page, on the engine thread.
         */
        void carrierFailed(String reason);

        /** The relay welcomed the carrier. */
        void carrierReady();

        long now();

        boolean logsEnabled();

        void log(String line);

        void logError(Throwable error);
    }

    private static final class Stream {
        final int id;
        final SocketChannel channel;
        final int localPort;
        SelectionKey key;
        int streamClass = WebProxyFlow.CLASS_INTERACTIVE;
        boolean opened;

        // Uplink: relay credit, socket readiness.
        long sendWindow = INITIAL_WINDOW;
        boolean readable;
        // Handed to the page, not yet credited back by the relay.
        long unacked;
        long sentTotal;
        long ackedTotal;
        // (end offset, sent at) of DATA frames not yet credited: the credit
        // round trip samples of the adaptive windows.
        final ArrayDeque<long[]> sendMarks = new ArrayDeque<>();
        // Interactive: when the first byte of a request left without a reply.
        long awaitingSince;

        // Downlink: credit the relay holds, bytes written to tgnet and not
        // yet returned as credit, bytes waiting for tgnet's socket.
        long receiveWindow = INITIAL_WINDOW;
        long withheld;
        long creditToSend;
        final ArrayDeque<ByteBuffer> down = new ArrayDeque<>();
        long downBytes;

        long lastReceivedAt;
        long deliveredAt;

        Stream(int id, SocketChannel channel, int localPort) {
            this.id = id;
            this.channel = channel;
            this.localPort = localPort;
        }
    }

    private static final class Inbound {
        static final int INIT = 1;
        static final int BYTES = 2;
        static final int FAILED = 3;
        static final int STARTING = 4;

        final int kind;
        final int token;
        final byte[] data;
        final String reason;

        Inbound(int kind, int token, byte[] data, String reason) {
            this.kind = kind;
            this.token = token;
            this.data = data;
            this.reason = reason;
        }
    }

    private final Host host;
    private final Object lock = new Object();
    private final Selector selector;
    private final ServerSocketChannel server;
    private final int port;
    private final Thread thread;
    private final ConcurrentLinkedQueue<Inbound> inbound = new ConcurrentLinkedQueue<>();
    private final java.util.concurrent.atomic.AtomicLong inboundBytes = new java.util.concurrent.atomic.AtomicLong();
    private volatile boolean stopped;

    // Everything below is guarded by lock; the engine thread holds it while
    // it works and never while it waits in select().
    private final Map<Integer, Stream> streams = new HashMap<>();
    private final Map<Integer, Stream> streamsByPort = new HashMap<>();
    private final LinkedHashMap<Integer, Integer> pendingClasses = new LinkedHashMap<Integer, Integer>() {
        @Override
        protected boolean removeEldestEntry(Map.Entry<Integer, Integer> eldest) {
            return size() > MAX_PENDING_CLASSES;
        }
    };
    private final WebProxyFlow.UplinkScheduler scheduler = new WebProxyFlow.UplinkScheduler();
    private final WebProxyFlow.AdaptiveWindow uplinkWindow = new WebProxyFlow.AdaptiveWindow(
            WebProxyFlow.UPLINK_UPLOAD_IN_FLIGHT, WebProxyFlow.UPLINK_WINDOW_MIN, WebProxyFlow.UPLINK_WINDOW_MAX);
    private final WebProxyFlow.AdaptiveWindow downlinkWindow = new WebProxyFlow.AdaptiveWindow(
            WebProxyFlow.DOWNLINK_DOWNLOAD_BUDGET, WebProxyFlow.DOWNLINK_BUDGET_MIN, WebProxyFlow.DOWNLINK_BUDGET_MAX);
    // Control frames waiting for the next batch (OPEN, CLOSE, PONG, HELLO).
    private final ArrayList<byte[]> control = new ArrayList<>();
    private final ByteBuffer batch = ByteBuffer.allocate(BATCH_LIMIT);
    private int nextStreamId = 1;
    private int downloadStreams;

    // Carrier: token of the page we talk to (0 = none), connected once the
    // relay welcomed it, restartPending while the host rebuilds the page.
    private int pageToken;
    private int lastToken;
    private boolean connected;
    private boolean restartPending;
    private long pageStartedAt;

    // Carrier health, host.now() based.
    private long lastDownlinkAt;
    private long lastCreditAt;
    private long totalUnacked;
    private long outstandingSince;
    private long nextHealthAt;

    // Adaptive window samples of the current interval.
    private final WebProxyFlow.DeliverySampler uplinkSampler = new WebProxyFlow.DeliverySampler();
    private final WebProxyFlow.DeliverySampler downlinkSampler = new WebProxyFlow.DeliverySampler();
    private long sampleStartedAt;
    private long sampleRtt = -1;
    private long sampleInteractive = -1;
    private boolean sampleUploadLimited;
    private boolean sampleDownloadLimited;
    private boolean sampleUploadActive;
    private boolean sampleDownloadActive;

    // Diagnostics since the last summary.
    private long lastSummaryAt;
    private long trafficUp;
    private long trafficDown;
    private long trafficBatches;
    private long trafficInbound;
    private long trafficStalledWrites;

    WebProxyEngine(Host host) throws IOException {
        this.host = host;
        selector = Selector.open();
        server = ServerSocketChannel.open();
        try {
            server.socket().bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), MAX_STREAMS);
            server.configureBlocking(false);
            server.register(selector, SelectionKey.OP_ACCEPT);
        } catch (IOException e) {
            closeQuietly(server);
            selector.close();
            throw e;
        }
        port = server.socket().getLocalPort();
        thread = new Thread(this::run, "WebProxyEngine");
        thread.setDaemon(true);
    }

    void start() {
        thread.start();
    }

    int port() {
        return port;
    }

    void stop() {
        stopped = true;
        selector.wakeup();
    }

    // ---- Calls from the page side (UI thread): enqueue and wake up. ----

    /** A new page is being loaded; its welcome timeout starts now. */
    void pageStarting() {
        post(new Inbound(Inbound.STARTING, 0, null, null));
    }

    /** The page authenticated itself; returns the token of its carrier. */
    int pageInit() {
        int token;
        synchronized (tokenLock) {
            token = ++tokenCounter;
            if (tokenCounter == Integer.MAX_VALUE) {
                tokenCounter = 0;
            }
        }
        post(new Inbound(Inbound.INIT, token, null, null));
        return token;
    }

    private final Object tokenLock = new Object();
    private int tokenCounter;

    /** One binary message from the page (one or more complete frames). */
    void pageBytes(int token, byte[] data) {
        if (inboundBytes.addAndGet(data.length) > MAX_INBOUND_BYTES) {
            inboundBytes.addAndGet(-data.length);
            post(new Inbound(Inbound.FAILED, token, null, "inbound_overflow"));
            return;
        }
        post(new Inbound(Inbound.BYTES, token, data, null));
    }

    /** The page or its WebView failed. */
    void pageFailed(int token, String reason) {
        post(new Inbound(Inbound.FAILED, token, null, reason));
    }

    private void post(Inbound item) {
        inbound.add(item);
        selector.wakeup();
    }

    // ---- Calls from tgnet (network thread). ----

    void registerLocalStream(int localPort, int streamClass) {
        synchronized (lock) {
            if (stopped || localPort <= 0) {
                return;
            }
            Stream stream = streamsByPort.get(localPort);
            if (stream != null) {
                setStreamClassLocked(stream, streamClass);
            } else {
                pendingClasses.put(localPort, WebProxyFlow.normalizeClass(streamClass));
            }
        }
        selector.wakeup();
    }

    long receiveWait(int localPort, long waitStartedAt) {
        synchronized (lock) {
            long now = host.now();
            Stream stream = streamsByPort.get(localPort);
            WebProxyFlow.StreamHealth health = new WebProxyFlow.StreamHealth();
            if (stream != null) {
                health.open = true;
                // Pulled uplink: bytes tgnet wrote wait in the socket until
                // the stream is granted a frame.
                health.queuedBytes = stream.readable || stream.sendWindow <= 0 ? 1 : 0;
                health.unackedBytes = stream.unacked;
                health.lastReceivedAt = stream.lastReceivedAt;
                health.deliveredAt = stream.deliveredAt;
            }
            WebProxyFlow.Decision decision = WebProxyFlow.decideReceiveWait(now, waitStartedAt, carrierHealthLocked(), health);
            if (host.logsEnabled()) {
                host.log("web_proxy_receive_wait stream=" + (stream != null ? stream.id : 0)
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

    // ---- Engine thread. ----

    private void run() {
        try {
            while (!stopped) {
                long timeout;
                synchronized (lock) {
                    timeout = nextTimerDelayLocked();
                }
                if (timeout == 0) {
                    selector.selectNow();
                } else {
                    selector.select(timeout);
                }
                synchronized (lock) {
                    if (stopped) {
                        break;
                    }
                    try {
                        loopOnceLocked();
                    } catch (Exception e) {
                        // A bug or an odd socket state must not kill the only
                        // thread of the WEB proxy: drop the carrier and its
                        // streams, which tgnet reopens on a fresh one.
                        host.logError(e);
                        for (Stream stream : new ArrayList<>(streams.values())) {
                            closeStreamLocked(stream, false);
                        }
                        failCarrierLocked("engine_error " + e.getClass().getSimpleName());
                    }
                }
            }
        } catch (Throwable e) {
            if (!stopped) {
                host.logError(e);
            }
        } finally {
            synchronized (lock) {
                stopped = true;
                for (Stream stream : new ArrayList<>(streams.values())) {
                    closeQuietly(stream.channel);
                }
                streams.clear();
                streamsByPort.clear();
            }
            closeQuietly(server);
            try {
                selector.close();
            } catch (IOException ignore) {
            }
        }
    }

    private void loopOnceLocked() throws IOException {
        long now = host.now();
        Iterator<SelectionKey> keys = selector.selectedKeys().iterator();
        while (keys.hasNext()) {
            SelectionKey key = keys.next();
            keys.remove();
            if (!key.isValid()) {
                continue;
            }
            if (key.channel() == server) {
                acceptLocked();
                continue;
            }
            Stream stream = (Stream) key.attachment();
            if (streams.get(stream.id) != stream) {
                continue;
            }
            if (key.isWritable()) {
                flushDownLocked(stream, now);
            }
            if (key.isValid() && key.isReadable()) {
                stream.readable = true;
                markReadyLocked(stream);
            }
            if (streams.get(stream.id) == stream) {
                updateInterestLocked(stream);
            }
        }
        Inbound item;
        while ((item = inbound.poll()) != null) {
            if (item.data != null) {
                inboundBytes.addAndGet(-item.data.length);
            }
            handleInboundLocked(item, now);
            if (stopped) {
                return;
            }
        }
        timersLocked(now);
        pumpLocked(now);
    }

    private long nextTimerDelayLocked() {
        if (!inbound.isEmpty()) {
            return 0;
        }
        long now = host.now();
        long next = Long.MAX_VALUE;
        if (nextHealthAt != 0) {
            next = Math.min(next, nextHealthAt);
        }
        if (connected && sampleStartedAt != 0) {
            next = Math.min(next, sampleStartedAt + SAMPLE_INTERVAL_MS);
        }
        if (next == Long.MAX_VALUE) {
            return 0x7fffffffL;
        }
        return Math.max(1, next - now);
    }

    private void acceptLocked() throws IOException {
        while (true) {
            SocketChannel channel;
            try {
                channel = server.accept();
            } catch (IOException e) {
                // Out of descriptors and the like: tgnet retries the connect.
                host.logError(e);
                return;
            }
            if (channel == null) {
                return;
            }
            if (streams.size() >= MAX_STREAMS) {
                closeQuietly(channel);
                continue;
            }
            int localPort;
            try {
                channel.configureBlocking(false);
                channel.socket().setTcpNoDelay(true);
                localPort = channel.socket().getPort();
            } catch (IOException e) {
                closeQuietly(channel);
                continue;
            }
            Stream stream = new Stream(allocateStreamIdLocked(), channel, localPort);
            Integer announced = pendingClasses.remove(localPort);
            stream.streamClass = announced != null ? announced : WebProxyFlow.CLASS_INTERACTIVE;
            stream.key = channel.register(selector, 0, stream);
            streams.put(stream.id, stream);
            streamsByPort.put(localPort, stream);
            scheduler.add(stream.id, stream.streamClass);
            if (stream.streamClass == WebProxyFlow.CLASS_DOWNLOAD) {
                downloadStreams++;
                rebalanceDownlinkCreditLocked();
            }
            if (connected) {
                openStreamLocked(stream);
            }
            scheduleHealthLocked(host.now());
        }
    }

    private int allocateStreamIdLocked() {
        while (true) {
            int result = nextStreamId;
            nextStreamId = nextStreamId >= 0x00ffffff ? 1 : nextStreamId + 1;
            if (!streams.containsKey(result)) {
                return result;
            }
        }
    }

    private void openStreamLocked(Stream stream) {
        stream.opened = true;
        control.add(frame(FRAME_OPEN, stream.id, null, 0, 0));
        updateInterestLocked(stream);
    }

    // OP_READ only while the stream could send: reading is what grants it a
    // frame, and a socket we do not read pushes back on tgnet.
    private void updateInterestLocked(Stream stream) {
        if (stream.key == null || !stream.key.isValid()) {
            return;
        }
        int ops = 0;
        if (stream.opened && !stream.readable && stream.sendWindow > 0) {
            ops |= SelectionKey.OP_READ;
        }
        if (stream.downBytes > 0) {
            ops |= SelectionKey.OP_WRITE;
        }
        if (stream.key.interestOps() != ops) {
            stream.key.interestOps(ops);
        }
    }

    private void markReadyLocked(Stream stream) {
        if (stream.opened && stream.readable && stream.sendWindow > 0) {
            scheduler.markReady(stream.id);
        }
    }

    // ---- Frames from the page. ----

    private void handleInboundLocked(Inbound item, long now) {
        switch (item.kind) {
            case Inbound.STARTING:
                restartPending = false;
                pageStartedAt = now;
                scheduleHealthLocked(now);
                return;
            case Inbound.INIT:
                if (pageToken != 0 || restartPending) {
                    return;
                }
                pageToken = item.token;
                lastToken = item.token;
                // The page turns its first binary message into the session
                // request, which must be one HELLO: nothing else goes to the
                // page before WELCOME.
                host.postToPage(pageToken, frame(FRAME_HELLO, 0, new byte[]{1}, 0, 1));
                return;
            case Inbound.FAILED:
                if (item.token == 0 || item.token == pageToken || (pageToken == 0 && item.token == lastToken)) {
                    failCarrierLocked(item.reason);
                }
                return;
            case Inbound.BYTES:
                if (item.token != pageToken || pageToken == 0) {
                    return;
                }
                lastDownlinkAt = now;
                trafficInbound++;
                String error = processFramesLocked(item.data, now);
                if (error != null) {
                    failCarrierLocked(error);
                }
                return;
            default:
        }
    }

    private String processFramesLocked(byte[] input, long now) {
        int offset = 0;
        while (offset < input.length) {
            if (input.length - offset < FRAME_HEADER) {
                return "protocol_error";
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
                return "protocol_error";
            }
            if (!processFrameLocked(type, streamId, input, offset + FRAME_HEADER, (int) length, now)) {
                return "protocol_error type=" + type;
            }
            offset = (int) end;
        }
        return null;
    }

    private boolean processFrameLocked(int type, int streamId, byte[] data, int offset, int length, long now) {
        if (streamId == 0) {
            if (type == FRAME_WELCOME && length == 0) {
                if (connected) {
                    return false;
                }
                connected = true;
                pageStartedAt = 0;
                lastDownlinkAt = now;
                sampleStartedAt = now;
                for (Stream stream : streams.values()) {
                    openStreamLocked(stream);
                    markReadyLocked(stream);
                }
                scheduleHealthLocked(now);
                if (host.logsEnabled()) {
                    host.log("web_proxy_carrier event=connected streams=" + streams.size());
                }
                host.carrierReady();
                return true;
            } else if (type == FRAME_PING && length <= 64 && connected) {
                control.add(frame(FRAME_PONG, 0, data, offset, length));
                return true;
            }
            return false;
        }
        if (!connected) {
            return false;
        }
        Stream stream = streams.get(streamId);
        if (stream == null) {
            // Late frames of a stream we closed.
            return type == FRAME_DATA || type == FRAME_WINDOW || type == FRAME_CLOSE;
        }
        if (type == FRAME_DATA) {
            if (length == 0 || stream.receiveWindow < length) {
                return false;
            }
            stream.receiveWindow -= length;
            stream.lastReceivedAt = now;
            trafficDown += length;
            downlinkSampler.delivered(length);
            if (stream.streamClass == WebProxyFlow.CLASS_DOWNLOAD) {
                sampleDownloadActive = true;
            }
            if (stream.awaitingSince != 0) {
                if (stream.streamClass == WebProxyFlow.CLASS_INTERACTIVE) {
                    long delay = now - stream.awaitingSince;
                    sampleInteractive = sampleInteractive < 0 ? delay : Math.min(sampleInteractive, delay);
                }
                stream.awaitingSince = 0;
            }
            // The page's message array is ours: queue a view of it, no copy.
            stream.down.addLast(ByteBuffer.wrap(data, offset, length));
            stream.downBytes += length;
            flushDownLocked(stream, now);
            if (streams.get(stream.id) == stream) {
                updateInterestLocked(stream);
            }
            return true;
        } else if (type == FRAME_WINDOW && length == 4) {
            long amount = (data[offset] & 0xffL) << 24
                    | (data[offset + 1] & 0xffL) << 16
                    | (data[offset + 2] & 0xffL) << 8
                    | (data[offset + 3] & 0xffL);
            if (amount == 0 || stream.sendWindow > 0xffffffffL - amount) {
                return false;
            }
            stream.sendWindow += amount;
            creditLocked(stream, amount, now);
            markReadyLocked(stream);
            updateInterestLocked(stream);
            return true;
        } else if (type == FRAME_CLOSE && length == 0) {
            closeStreamLocked(stream, false);
            return true;
        }
        return false;
    }

    private void creditLocked(Stream stream, long amount, long now) {
        long credited = Math.min(stream.unacked, amount);
        stream.unacked -= credited;
        totalUnacked = Math.max(0, totalUnacked - credited);
        if (totalUnacked == 0) {
            outstandingSince = 0;
        }
        lastCreditAt = now;
        scheduler.acknowledged(stream.id, amount);
        uplinkSampler.delivered(credited);
        stream.ackedTotal += amount;
        long sentAt = -1;
        while (!stream.sendMarks.isEmpty() && stream.sendMarks.peekFirst()[0] <= stream.ackedTotal) {
            sentAt = stream.sendMarks.pollFirst()[1];
        }
        if (sentAt >= 0) {
            long rtt = now - sentAt;
            sampleRtt = sampleRtt < 0 ? rtt : Math.min(sampleRtt, rtt);
        }
        if (stream.unacked == 0) {
            stream.deliveredAt = now;
        }
    }

    // Writes queued downlink bytes to tgnet without blocking, then returns
    // the credit the stream's class allows.
    private void flushDownLocked(Stream stream, long now) {
        long written = 0;
        try {
            while (!stream.down.isEmpty()) {
                ByteBuffer head = stream.down.peekFirst();
                int count = stream.channel.write(head);
                written += count;
                if (head.hasRemaining()) {
                    trafficStalledWrites++;
                    break;
                }
                stream.down.pollFirst();
            }
        } catch (IOException e) {
            closeStreamLocked(stream, true);
            return;
        }
        if (written == 0) {
            return;
        }
        stream.downBytes -= written;
        stream.withheld += written;
        releaseDownlinkCreditLocked(stream);
    }

    // Returns the credit a download stream consumed only while the relay
    // holds less than the stream's share of the adaptive download budget,
    // so undelivered media waits in the relay's backend socket instead of in
    // the shared downlink FIFO in front of interactive replies.
    private void releaseDownlinkCreditLocked(Stream stream) {
        long target = WebProxyFlow.downlinkCreditTarget(stream.streamClass, downloadStreams, downlinkWindow.window());
        long release = WebProxyFlow.downlinkCreditRelease(stream.receiveWindow, stream.withheld, target);
        if (release <= 0) {
            if (stream.streamClass == WebProxyFlow.CLASS_DOWNLOAD && stream.withheld > 0) {
                sampleDownloadLimited = true;
            }
            return;
        }
        stream.withheld -= release;
        stream.receiveWindow += release;
        stream.creditToSend += release;
    }

    private void rebalanceDownlinkCreditLocked() {
        for (Stream stream : streams.values()) {
            if (stream.opened && stream.withheld > 0) {
                releaseDownlinkCreditLocked(stream);
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

    // ---- Frames to the page. ----

    // One pass: credit and control frames first, then DATA in scheduler
    // order read straight from the sockets, all into one message.
    private void pumpLocked(long now) {
        if (stopped || pageToken == 0) {
            control.clear();
            return;
        }
        batch.clear();
        for (Stream stream : streams.values()) {
            if (stream.creditToSend > 0 && connected) {
                long amount = Math.min(stream.creditToSend, 0xffffffffL);
                stream.creditToSend -= amount;
                putHeader(FRAME_WINDOW, stream.id, 4);
                batch.putInt((int) amount);
            }
        }
        for (byte[] value : control) {
            if (batch.remaining() < value.length) {
                flushBatchLocked();
            }
            batch.put(value);
        }
        control.clear();
        if (connected) {
            pumpDataLocked(now);
        }
        flushBatchLocked();
    }

    private void pumpDataLocked(long now) {
        boolean uploadLimited = false;
        while (true) {
            if (batch.remaining() < FRAME_HEADER + 1024) {
                flushBatchLocked();
            }
            WebProxyFlow.Grant grant = scheduler.next();
            if (grant == null) {
                break;
            }
            Stream stream = streams.get(grant.streamId);
            if (stream == null) {
                scheduler.remove(grant.streamId);
                continue;
            }
            if (!stream.opened || !stream.readable || stream.sendWindow <= 0) {
                continue;
            }
            int room = batch.remaining() - FRAME_HEADER;
            int take = (int) Math.min(Math.min(grant.maxBytes, room), Math.min(stream.sendWindow, WebProxyFlow.UPLINK_FRAME_SIZE));
            int start = batch.position();
            batch.position(start + FRAME_HEADER);
            int limit = batch.limit();
            batch.limit(start + FRAME_HEADER + take);
            int count;
            try {
                count = stream.channel.read(batch);
            } catch (IOException e) {
                count = -1;
            }
            batch.limit(limit);
            if (count <= 0) {
                batch.position(start);
                stream.readable = false;
                if (count < 0) {
                    closeStreamLocked(stream, true);
                } else {
                    updateInterestLocked(stream);
                }
                continue;
            }
            batch.put(start, (byte) FRAME_DATA);
            batch.put(start + 1, (byte) (stream.id >> 16));
            batch.put(start + 2, (byte) (stream.id >> 8));
            batch.put(start + 3, (byte) stream.id);
            batch.putInt(start + 4, count);
            stream.sendWindow -= count;
            scheduler.sent(stream.id, count);
            if (totalUnacked == 0) {
                outstandingSince = now;
            }
            stream.unacked += count;
            totalUnacked += count;
            stream.sentTotal += count;
            stream.sendMarks.addLast(new long[]{stream.sentTotal, now});
            if (stream.streamClass == WebProxyFlow.CLASS_UPLOAD) {
                sampleUploadActive = true;
            }
            if (stream.streamClass == WebProxyFlow.CLASS_INTERACTIVE && stream.awaitingSince == 0) {
                stream.awaitingSince = now;
            }
            trafficUp += count;
            if (count < take) {
                // Drained what tgnet had written; wait for more.
                stream.readable = false;
                updateInterestLocked(stream);
            } else {
                markReadyLocked(stream);
                if (stream.sendWindow <= 0) {
                    updateInterestLocked(stream);
                }
            }
        }
        if (scheduler.uploadBlocked()) {
            uploadLimited = true;
        }
        if (uploadLimited) {
            sampleUploadLimited = true;
        }
    }

    private void putHeader(int type, int streamId, int length) {
        batch.put((byte) type)
                .put((byte) (streamId >> 16))
                .put((byte) (streamId >> 8))
                .put((byte) streamId)
                .putInt(length);
    }

    private void flushBatchLocked() {
        if (batch.position() == 0) {
            return;
        }
        byte[] value = new byte[batch.position()];
        System.arraycopy(batch.array(), 0, value, 0, value.length);
        batch.clear();
        trafficBatches++;
        host.postToPage(pageToken, value);
    }

    static byte[] frame(int type, int streamId, byte[] payload, int offset, int length) {
        byte[] result = new byte[FRAME_HEADER + length];
        result[0] = (byte) type;
        result[1] = (byte) (streamId >> 16);
        result[2] = (byte) (streamId >> 8);
        result[3] = (byte) streamId;
        result[4] = (byte) (length >>> 24);
        result[5] = (byte) (length >>> 16);
        result[6] = (byte) (length >>> 8);
        result[7] = (byte) length;
        if (length > 0) {
            System.arraycopy(payload, offset, result, FRAME_HEADER, length);
        }
        return result;
    }

    // ---- Streams and carrier lifecycle. ----

    private void closeStreamLocked(Stream stream, boolean notifyRelay) {
        if (streams.get(stream.id) != stream) {
            return;
        }
        streams.remove(stream.id);
        streamsByPort.remove(stream.localPort);
        scheduler.remove(stream.id);
        totalUnacked = Math.max(0, totalUnacked - stream.unacked);
        stream.unacked = 0;
        if (totalUnacked == 0) {
            outstandingSince = 0;
        }
        stream.down.clear();
        stream.downBytes = 0;
        if (stream.key != null) {
            stream.key.cancel();
        }
        closeQuietly(stream.channel);
        if (stream.streamClass == WebProxyFlow.CLASS_DOWNLOAD) {
            downloadStreams--;
            rebalanceDownlinkCreditLocked();
        }
        if (notifyRelay && connected && stream.opened) {
            control.add(frame(FRAME_CLOSE, stream.id, null, 0, 0));
        }
    }

    // Tears the carrier down once for all of its streams; the host rebuilds
    // the page, which opens a fresh relay session.
    private void failCarrierLocked(String reason) {
        if (stopped || restartPending) {
            return;
        }
        if (host.logsEnabled()) {
            host.log("web_proxy_carrier event=lost reason=" + reason
                    + " connected=" + (connected ? 1 : 0)
                    + " streams=" + streams.size()
                    + " unacked=" + totalUnacked);
        }
        restartPending = true;
        connected = false;
        pageToken = 0;
        pageStartedAt = 0;
        control.clear();
        for (Stream stream : new ArrayList<>(streams.values())) {
            if (stream.key != null) {
                stream.key.cancel();
            }
            closeQuietly(stream.channel);
        }
        streams.clear();
        streamsByPort.clear();
        scheduler.clear();
        downloadStreams = 0;
        totalUnacked = 0;
        outstandingSince = 0;
        sampleStartedAt = 0;
        // The next carrier may take another network path.
        uplinkWindow.resetRtt();
        downlinkWindow.resetRtt();
        host.carrierFailed(reason);
    }

    private WebProxyFlow.CarrierHealth carrierHealthLocked() {
        WebProxyFlow.CarrierHealth health = new WebProxyFlow.CarrierHealth();
        health.connected = connected && pageToken != 0 && !stopped;
        health.lastDownlinkAt = lastDownlinkAt;
        health.lastCreditAt = lastCreditAt;
        health.unackedBytes = totalUnacked;
        health.outstandingSince = outstandingSince;
        return health;
    }

    private void scheduleHealthLocked(long now) {
        if (nextHealthAt == 0) {
            nextHealthAt = now + HEALTH_CHECK_INTERVAL_MS;
        }
    }

    private void timersLocked(long now) {
        if (connected && sampleStartedAt != 0 && now - sampleStartedAt >= SAMPLE_INTERVAL_MS) {
            sampleLocked(now);
        }
        if (nextHealthAt != 0 && now >= nextHealthAt) {
            nextHealthAt = 0;
            checkHealthLocked(now);
        }
    }

    // Feeds both adaptive windows with the interval that just ended. The
    // credit round trip (a DATA frame to its relay WINDOW) crosses both the
    // uplink and the downlink FIFO, so it measures the queue in either.
    private void sampleLocked(long now) {
        long interactive = sampleInteractive;
        uplinkSampler.tick(uplinkWindow, now, sampleUploadLimited, sampleRtt, sampleUploadActive ? interactive : -1);
        downlinkSampler.tick(downlinkWindow, now, sampleDownloadLimited, sampleRtt, sampleDownloadActive ? interactive : -1);
        scheduler.setUploadLimit(uplinkWindow.window());
        sampleStartedAt = now;
        sampleRtt = -1;
        sampleInteractive = -1;
        sampleUploadLimited = false;
        sampleDownloadLimited = false;
        sampleUploadActive = false;
        sampleDownloadActive = false;
        // A larger budget lets withheld download credit go now.
        rebalanceDownlinkCreditLocked();
        for (Stream stream : streams.values()) {
            markReadyLocked(stream);
        }
    }

    // Carrier watchdog: a carrier that holds uplink bytes and hears nothing
    // from the relay for WebProxyFlow.CARRIER_STALL_MS, or a bridge page that
    // never welcomes waiting streams, is recovered once for all streams,
    // instead of every tgnet connection timing out and reconnecting on its own.
    private void checkHealthLocked(long now) {
        String failure = null;
        WebProxyFlow.CarrierHealth health = carrierHealthLocked();
        if (WebProxyFlow.carrierStalled(now, health)) {
            failure = "stalled silence_ms=" + (now - WebProxyFlow.carrierProgressAt(health)) + " unacked=" + totalUnacked;
        } else if (!connected && !restartPending && pageStartedAt != 0
                && now - pageStartedAt >= WebProxyFlow.WELCOME_TIMEOUT_MS && !streams.isEmpty()) {
            failure = "welcome_timeout waited_ms=" + (now - pageStartedAt);
        } else if (host.logsEnabled() && now - lastSummaryAt >= HEALTH_SUMMARY_INTERVAL_MS) {
            logSummaryLocked(now);
        }
        if (failure != null) {
            failCarrierLocked(failure);
        }
        if (!streams.isEmpty() || totalUnacked > 0 || (pageStartedAt != 0 && !connected)) {
            scheduleHealthLocked(now);
        }
    }

    private void logSummaryLocked(long now) {
        long interval = lastSummaryAt != 0 ? now - lastSummaryAt : HEALTH_SUMMARY_INTERVAL_MS;
        lastSummaryAt = now;
        long queuedDown = 0;
        long withheld = 0;
        for (Stream stream : streams.values()) {
            queuedDown += stream.downBytes;
            withheld += stream.withheld;
        }
        long up = trafficUp;
        long down = trafficDown;
        long batches = trafficBatches;
        long messages = trafficInbound;
        long stalledWrites = trafficStalledWrites;
        trafficUp = 0;
        trafficDown = 0;
        trafficBatches = 0;
        trafficInbound = 0;
        trafficStalledWrites = 0;
        if (up == 0 && down == 0 && queuedDown == 0 && totalUnacked == 0) {
            return;
        }
        int[] stats = scheduler.stats();
        host.log("web_proxy_carrier event=health connected=" + (connected ? 1 : 0)
                + " streams(i/d/u)=" + stats[0] + "/" + stats[1] + "/" + stats[2]
                + " ready=" + stats[3] + "/" + stats[4] + "/" + stats[5]
                + " unacked=" + totalUnacked
                + " upload_inflight=" + scheduler.uploadInFlight() + "/" + scheduler.uploadLimit()
                + " down_budget=" + downlinkWindow.window()
                + " base_rtt_ms=" + uplinkWindow.baseRtt()
                + " rate_up=" + uplinkWindow.rate()
                + " rate_down=" + downlinkWindow.rate()
                + " cuts=" + uplinkWindow.decreases() + "/" + downlinkWindow.decreases()
                + " queued_down=" + queuedDown
                + " stalled_writes=" + stalledWrites
                + " withheld=" + withheld
                + " up=" + up
                + " down=" + down
                + " batches=" + batches
                + " inbound=" + messages
                + " interval_ms=" + interval
                + " last_down_ms=" + sinceMs(now, lastDownlinkAt)
                + " last_credit_ms=" + sinceMs(now, lastCreditAt));
    }

    private static String sinceMs(long now, long at) {
        return at != 0 ? Long.toString(now - at) : "never";
    }

    private static void closeQuietly(java.io.Closeable value) {
        try {
            value.close();
        } catch (Exception ignore) {
        }
    }
}
