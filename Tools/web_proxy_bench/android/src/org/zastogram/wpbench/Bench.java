package org.zastogram.wpbench;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.BufferedOutputStream;
import java.net.Socket;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * tgnet stand-in: opens loopback connections to the WEB bridge, announces
 * their class like ConnectionSocket::announceWebProxyStream and runs
 * pipelined RPCs against backend2.py (u32 reqLen | u32 respLen | u32 tag |
 * u32 delayMs | payload -> u32 respLen | u32 tag | payload).
 *
 * Upload mirrors FileUploadOperation (128 KiB parts, 16 in flight = 2 MiB,
 * round-robin over the upload connections); download mirrors
 * FileLoadOperation (128 KiB x 4 by default, 512 KiB x 8 with experimental
 * params, alternating Download/Download2).
 */
public final class Bench {
    public interface Announcer {
        void announce(int localPort, int streamClass);
    }

    public static final int CLASS_INTERACTIVE = 0;
    public static final int CLASS_DOWNLOAD = 1;
    public static final int CLASS_UPLOAD = 2;

    private static final byte[] ZERO = new byte[64 * 1024];

    private static final class Req {
        final int reqLen;
        final int respLen;
        final int delay;
        long sentAt;
        long doneAt;
        final CountDownLatch done = new CountDownLatch(1);
        Runnable onDone;

        Req(int reqLen, int respLen, int delay) {
            this.reqLen = reqLen;
            this.respLen = respLen;
            this.delay = delay;
        }
    }

    private static final class Conn {
        final Socket socket;
        final DataOutputStream out;
        final DataInputStream in;
        final ArrayDeque<Req> inflight = new ArrayDeque<>();
        final LinkedBlockingQueue<Req> sendQueue = new LinkedBlockingQueue<>();
        volatile boolean closed;
        volatile String error;

        Conn(int port, int streamClass, Announcer announcer) throws Exception {
            socket = new Socket("127.0.0.1", port);
            socket.setTcpNoDelay(true);
            announcer.announce(socket.getLocalPort(), streamClass);
            out = new DataOutputStream(new BufferedOutputStream(socket.getOutputStream(), 64 * 1024));
            in = new DataInputStream(socket.getInputStream());
            Thread writer = new Thread(this::writeLoop, "bench-w");
            Thread reader = new Thread(this::readLoop, "bench-r");
            writer.setDaemon(true);
            reader.setDaemon(true);
            writer.start();
            reader.start();
        }

        void send(Req req) {
            sendQueue.add(req);
        }

        private void writeLoop() {
            try {
                while (!closed) {
                    Req req = sendQueue.poll(200, TimeUnit.MILLISECONDS);
                    if (req == null) {
                        continue;
                    }
                    synchronized (this) {
                        inflight.addLast(req);
                    }
                    req.sentAt = System.nanoTime();
                    out.writeInt(req.reqLen);
                    out.writeInt(req.respLen);
                    out.writeInt(0);
                    out.writeInt(req.delay);
                    int left = req.reqLen;
                    while (left > 0) {
                        int k = Math.min(left, ZERO.length);
                        out.write(ZERO, 0, k);
                        left -= k;
                    }
                    if (sendQueue.isEmpty()) {
                        out.flush();
                    }
                }
            } catch (Exception e) {
                fail("write " + e);
            }
        }

        private void readLoop() {
            byte[] buffer = new byte[64 * 1024];
            try {
                while (!closed) {
                    int len = in.readInt();
                    in.readInt();
                    int left = len;
                    while (left > 0) {
                        int k = in.read(buffer, 0, Math.min(left, buffer.length));
                        if (k < 0) {
                            throw new java.io.EOFException();
                        }
                        left -= k;
                    }
                    Req req;
                    synchronized (this) {
                        req = inflight.pollFirst();
                    }
                    if (req == null) {
                        throw new IllegalStateException("unexpected response");
                    }
                    req.doneAt = System.nanoTime();
                    req.done.countDown();
                    if (req.onDone != null) {
                        req.onDone.run();
                    }
                }
            } catch (Exception e) {
                fail("read " + e);
            }
        }

        void fail(String reason) {
            if (!closed) {
                error = reason;
                close();
            }
        }

        void close() {
            closed = true;
            try {
                socket.close();
            } catch (Exception ignore) {
            }
        }
    }

    /** utime+stime in ms of the process and of its main thread (Linux /proc). */
    static long[] cpu() {
        long[] result = {-1, -1};
        try {
            String self = new String(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get("/proc/self/stat")), "UTF-8");
            String pid = self.substring(0, self.indexOf(' '));
            result[0] = ticks(self);
            result[1] = ticks(new String(java.nio.file.Files.readAllBytes(java.nio.file.Paths.get("/proc/self/task/" + pid + "/stat")), "UTF-8"));
        } catch (Throwable ignore) {
        }
        return result;
    }

    private static long ticks(String stat) {
        String[] f = stat.substring(stat.lastIndexOf(')') + 2).split(" ");
        return (Long.parseLong(f[11]) + Long.parseLong(f[12])) * 10;
    }

    private static int intParam(Map<String, String> p, String key, int def) {
        String v = p.get(key);
        return v == null ? def : Integer.parseInt(v);
    }

    private static double ms(long nanos) {
        return nanos / 1e6;
    }

    private static String percentiles(List<Double> values) {
        if (values.isEmpty()) {
            return "null";
        }
        List<Double> sorted = new ArrayList<>(values);
        Collections.sort(sorted);
        double p50 = sorted.get(sorted.size() / 2);
        double p90 = sorted.get(Math.min(sorted.size() - 1, (int) Math.ceil(sorted.size() * 0.9) - 1));
        double max = sorted.get(sorted.size() - 1);
        return String.format(Locale.US, "{\"p50\":%.1f,\"p90\":%.1f,\"max\":%.1f,\"n\":%d}", p50, p90, max, sorted.size());
    }

    /** One pipelined file transfer; returns bytes per second of payload. */
    private static final class Transfer {
        final List<Conn> conns = new ArrayList<>();
        final boolean upload;
        final int parts;
        final int part;
        final int parallel;
        final int delay;
        final AtomicLong doneBytes = new AtomicLong();
        long startedAt;
        long finishedAt;
        final List<Double> partMs = Collections.synchronizedList(new ArrayList<>());

        Transfer(int port, Announcer announcer, boolean upload, int totalBytes, int part, int parallel, int conns, int delay) throws Exception {
            this.upload = upload;
            this.part = part;
            this.parallel = parallel;
            this.delay = delay;
            this.parts = Math.max(1, totalBytes / part);
            for (int i = 0; i < conns; i++) {
                this.conns.add(new Conn(port, upload ? CLASS_UPLOAD : CLASS_DOWNLOAD, announcer));
            }
        }

        void run(long deadline) throws Exception {
            Semaphore window = new Semaphore(parallel);
            CountDownLatch all = new CountDownLatch(parts);
            startedAt = System.nanoTime();
            for (int i = 0; i < parts; i++) {
                while (!window.tryAcquire(100, TimeUnit.MILLISECONDS)) {
                    check(deadline);
                }
                Req req = upload ? new Req(part, 16, delay) : new Req(64, part, delay);
                req.onDone = () -> {
                    partMs.add(ms(req.doneAt - req.sentAt));
                    doneBytes.addAndGet(part);
                    window.release();
                    all.countDown();
                };
                conns.get(i % conns.size()).send(req);
            }
            while (!all.await(100, TimeUnit.MILLISECONDS)) {
                check(deadline);
            }
            finishedAt = System.nanoTime();
        }

        void check(long deadline) {
            for (Conn conn : conns) {
                if (conn.error != null) {
                    throw new IllegalStateException(conn.error);
                }
            }
            if (System.nanoTime() > deadline) {
                throw new IllegalStateException("timeout done=" + doneBytes.get());
            }
        }

        double mbps() {
            return (double) parts * part / 1048576.0 / ((finishedAt - startedAt) / 1e9);
        }

        void close() {
            for (Conn conn : conns) {
                conn.close();
            }
        }
    }

    public static String run(int port, Announcer announcer, Map<String, String> p) {
        long started = System.nanoTime();
        StringBuilder out = new StringBuilder("{");
        Conn chat = null;
        List<Transfer> transfers = new ArrayList<>();
        Thread pinger = null;
        try {
            int delay = intParam(p, "dc", 20);
            long deadline = started + intParam(p, "timeout", 180) * 1_000_000_000L;
            chat = new Conn(port, CLASS_INTERACTIVE, announcer);
            Req first = new Req(128, 256, delay);
            chat.send(first);
            if (!first.done.await(60, TimeUnit.SECONDS)) {
                throw new IllegalStateException("carrier never came up");
            }
            out.append(String.format(Locale.US, "\"first_ms\":%.0f,", ms(first.doneAt - started)));
            List<Double> idle = new ArrayList<>();
            for (int i = 0; i < intParam(p, "idle", 10); i++) {
                Req req = new Req(128, 256, delay);
                chat.send(req);
                if (!req.done.await(30, TimeUnit.SECONDS)) {
                    throw new IllegalStateException("idle ping lost");
                }
                idle.add(ms(req.doneAt - req.sentAt));
            }
            out.append("\"idle_ms\":").append(percentiles(idle)).append(',');

            int recover = intParam(p, "recover", 0);
            if (recover > 0) {
                // tgnet-like: a connection that fails is replaced by a new one.
                // Pings every 200 ms for `recover` seconds while the host
                // restarts the relay; reports the longest gap between replies.
                long end = System.nanoTime() + recover * 1_000_000_000L;
                long lastOk = System.nanoTime();
                double maxGap = 0;
                int reconnects = 0;
                Conn conn = chat;
                while (System.nanoTime() < end) {
                    Req req = new Req(128, 256, delay);
                    conn.send(req);
                    boolean ok = req.done.await(3, TimeUnit.SECONDS);
                    if (ok && conn.error == null) {
                        long now = System.nanoTime();
                        maxGap = Math.max(maxGap, ms(now - lastOk));
                        lastOk = now;
                        Thread.sleep(200);
                    } else {
                        conn.close();
                        reconnects++;
                        Thread.sleep(100);
                        try {
                            conn = new Conn(port, CLASS_INTERACTIVE, announcer);
                        } catch (Exception e) {
                            Thread.sleep(500);
                        }
                    }
                }
                out.append(String.format(Locale.US, "\"recover_max_gap_ms\":%.0f,\"reconnects\":%d,", maxGap, reconnects));
                chat = conn;
            }

            int stuckMb = intParam(p, "stuck", 0);
            if (stuckMb > 0) {
                // A connection whose owner stops reading (a download that was
                // paused, a busy tgnet thread): the bridge must not let it
                // hold up everyone else. Pings are timed while it is stuck.
                Socket stuck = new Socket();
                stuck.setReceiveBufferSize(16 * 1024);
                stuck.connect(new java.net.InetSocketAddress("127.0.0.1", port));
                announcer.announce(stuck.getLocalPort(), CLASS_INTERACTIVE);
                java.io.DataOutputStream so = new java.io.DataOutputStream(stuck.getOutputStream());
                so.writeInt(64);
                so.writeInt(stuckMb << 20);
                so.writeInt(0);
                so.writeInt(0);
                so.write(new byte[64]);
                so.flush();
                Thread.sleep(1000);
                List<Double> during = new ArrayList<>();
                int lost = 0;
                for (int i = 0; i < intParam(p, "stuckPings", 10); i++) {
                    Req req = new Req(128, 256, delay);
                    chat.send(req);
                    if (!req.done.await(10, TimeUnit.SECONDS)) {
                        lost++;
                        break;
                    }
                    during.add(ms(req.doneAt - req.sentAt));
                    Thread.sleep(200);
                }
                out.append("\"stuck_ms\":").append(percentiles(during)).append(",\"stuck_lost\":").append(lost).append(',');
                stuck.close();
            }

            int upMb = intParam(p, "up", 0);
            int downMb = intParam(p, "down", 0);
            if (upMb > 0) {
                transfers.add(new Transfer(port, announcer, true, upMb << 20,
                        intParam(p, "upPart", 128 * 1024), intParam(p, "upParallel", 16), intParam(p, "upConns", 2), delay));
            }
            if (downMb > 0) {
                transfers.add(new Transfer(port, announcer, false, downMb << 20,
                        intParam(p, "downPart", 128 * 1024), intParam(p, "downParallel", 4), intParam(p, "downConns", 2), delay));
            }
            List<Double> loaded = Collections.synchronizedList(new ArrayList<>());
            if (!transfers.isEmpty()) {
                final Conn chatConn = chat;
                final int interval = intParam(p, "ping", 250);
                final boolean[] stop = {false};
                pinger = new Thread(() -> {
                    try {
                        while (!stop[0]) {
                            Req req = new Req(128, 256, delay);
                            chatConn.send(req);
                            if (!req.done.await(60, TimeUnit.SECONDS)) {
                                loaded.add(60000.0);
                                return;
                            }
                            loaded.add(ms(req.doneAt - req.sentAt));
                            Thread.sleep(interval);
                        }
                    } catch (InterruptedException ignore) {
                    }
                }, "bench-ping");
                pinger.setDaemon(true);
                pinger.start();
                List<Thread> runners = new ArrayList<>();
                final String[] failure = {null};
                long[] cpuStart = cpu();
                long transferStart = System.nanoTime();
                out.append(String.format(Locale.US, "\"wall_start\":%.3f,", System.currentTimeMillis() / 1000.0));
                for (Transfer transfer : transfers) {
                    Thread t = new Thread(() -> {
                        try {
                            transfer.run(deadline);
                        } catch (Exception e) {
                            failure[0] = String.valueOf(e.getMessage());
                        }
                    }, "bench-transfer");
                    runners.add(t);
                    t.start();
                }
                for (Thread t : runners) {
                    t.join();
                }
                long transferEnd = System.nanoTime();
                long[] cpuEnd = cpu();
                out.append(String.format(Locale.US, "\"wall_end\":%.3f,", System.currentTimeMillis() / 1000.0));
                stop[0] = true;
                pinger.interrupt();
                if (failure[0] != null) {
                    throw new IllegalStateException(failure[0]);
                }
                long total = 0;
                for (Transfer transfer : transfers) {
                    String key = transfer.upload ? "up" : "down";
                    total += (long) transfer.parts * transfer.part;
                    out.append(String.format(Locale.US, "\"%s_MBps\":%.2f,\"%s_part_ms\":%s,", key, transfer.mbps(), key, percentiles(transfer.partMs)));
                }
                out.append(String.format(Locale.US, "\"total_MBps\":%.2f,", total / 1048576.0 / ((transferEnd - transferStart) / 1e9)));
                out.append(String.format(Locale.US, "\"cpu_ms_per_MB\":%.1f,\"main_ms_per_MB\":%.1f,", (cpuEnd[0] - cpuStart[0]) / (total / 1048576.0), (cpuEnd[1] - cpuStart[1]) / (total / 1048576.0)));
                out.append("\"loaded_ms\":").append(percentiles(loaded)).append(',');
            }
            out.append("\"ok\":true");
        } catch (Exception e) {
            out.append("\"ok\":false,\"error\":\"").append(String.valueOf(e.getMessage()).replace('"', '\'')).append('"');
        } finally {
            if (pinger != null) {
                pinger.interrupt();
            }
            for (Transfer transfer : transfers) {
                transfer.close();
            }
            if (chat != null) {
                chat.close();
            }
        }
        out.append(String.format(Locale.US, ",\"seconds\":%.1f}", (System.nanoTime() - started) / 1e9));
        return out.toString();
    }
}
