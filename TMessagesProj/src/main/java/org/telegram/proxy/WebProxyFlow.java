package org.telegram.proxy;

import java.util.ArrayDeque;
import java.util.HashMap;
import java.util.Iterator;
import java.util.Map;

/**
 * Flow policy of the WEB proxy carrier, kept free of Android classes and
 * threads so that it can be compiled and tested on a plain JVM
 * (Tools/web_proxy_flow_tests).
 *
 * <p>Every tgnet connection through a WEB proxy is one loopback socket and
 * one stream, and all of them share a single carrier: one bridge page that
 * forwards frames to the relay through one HTTPS or WebSocket pipe. The
 * relay keeps one FIFO per direction for the whole carrier, so whatever the
 * client hands over first is delivered first. Left alone, a media download
 * or a file upload puts megabytes in front of the one reply a chat is
 * waiting for, and every connection then declares itself dead after a few
 * seconds of silence - all at once, over a pipe that was working the whole
 * time.
 *
 * <p>The policy mirrors the desktop client (web_proxy_flow.cpp):
 * <ul>
 * <li>uplink: interactive frames go first, bulk streams share what is left
 * round-robin in frame-sized chunks, and upload bytes that the relay has not
 * yet written to its backend are capped by a window;</li>
 * <li>downlink: the relay may only read a backend while it holds our credit,
 * so download streams share a credit budget instead of the full protocol
 * window each;</li>
 * <li>both the upload window and the download budget adapt
 * ({@link AdaptiveWindow}): they follow the bandwidth-delay product the
 * carrier actually delivers plus a small queue allowance, and shrink when
 * chat replies start to wait in line behind bulk data;</li>
 * <li>liveness: a stream is declared dead by what the carrier knows about it,
 * not by a per-connection timer that cannot tell "queued" from "lost".</li>
 * </ul>
 */
public final class WebProxyFlow {
    public static final int CLASS_INTERACTIVE = 0;
    public static final int CLASS_DOWNLOAD = 1;
    public static final int CLASS_UPLOAD = 2;
    public static final int CLASS_COUNT = 3;

    // Uplink.
    public static final int UPLINK_FRAME_SIZE = 64 * 1024;
    // Upload bytes handed to the carrier and not yet credited back by the
    // relay: what can sit in front of an interactive frame in the shared
    // uplink FIFO. The starting value (desktop: 1 MiB; 2 MiB matched the
    // upstream transport's throughput on the bench from the first second);
    // the carrier moves it with an AdaptiveWindow (UPLINK_WINDOW_*).
    public static final long UPLINK_UPLOAD_IN_FLIGHT = 2L * 1024 * 1024;
    public static final long UPLINK_WINDOW_MIN = 256 * 1024;
    public static final long UPLINK_WINDOW_MAX = 8L * 1024 * 1024;
    // A steady stream of interactive/download frames must not stop uploads
    // forever: after this many of them in a row while an upload was
    // eligible, one upload frame goes out.
    public static final int UPLINK_PRIORITY_BURST = 16;

    // Downlink. The protocol window is fixed by the relay (4 MiB).
    public static final long DOWNLINK_STREAM_WINDOW = 4L * 1024 * 1024;
    // Credit shared by all download streams: the most media that can be
    // queued in the relay's downlink FIFO in front of an interactive reply.
    // The starting value; the carrier moves it with an AdaptiveWindow.
    public static final long DOWNLINK_DOWNLOAD_BUDGET = 2L * 1024 * 1024;
    public static final long DOWNLINK_BUDGET_MIN = 256 * 1024;
    public static final long DOWNLINK_BUDGET_MAX = 8L * 1024 * 1024;
    // Bounds of one download stream's share (desktop: downloadMin/Max).
    public static final long DOWNLINK_DOWNLOAD_MIN = 128 * 1024;
    public static final long DOWNLINK_DOWNLOAD_MAX = 4L * 1024 * 1024;

    // Liveness.
    // Outstanding uplink bytes and not a single frame from the relay for this
    // long: the carrier itself is stuck and is recovered once, for all streams.
    public static final long CARRIER_STALL_MS = 20_000;
    // The request reached the backend and the downlink is empty, yet no
    // reply: nothing is queued ahead of it, the stream is dead.
    public static final long QUIET_REPLY_MS = 8_000;
    // The request reached the backend and the downlink is busy with other
    // streams, yet no reply for this long: dead as well.
    public static final long BUSY_REPLY_MS = 30_000;
    // Absolute bound for one silence, whatever the carrier reports.
    public static final long MAX_WAIT_MS = 64_000;
    // Frames from the relay within this long mean the downlink is busy.
    public static final long BUSY_WINDOW_MS = 2_000;
    public static final long RECHECK_MS = 2_000;
    // A bridge page that never welcomes us while streams wait.
    public static final long WELCOME_TIMEOUT_MS = 30_000;

    // Receive-wait reasons. The numeric values cross JNI and must match
    // kWebProxyReceiveReasonNames in tgnet/ConnectionSocket.cpp.
    public static final int REASON_STREAM_CLOSED = 1;
    public static final int REASON_CARRIER_DOWN = 2;
    public static final int REASON_MAX_WAIT = 3;
    public static final int REASON_CARRIER_STALLED = 4;
    public static final int REASON_QUEUED = 5;
    public static final int REASON_REPLY_QUEUED = 6;
    public static final int REASON_REPLY_PENDING = 7;
    public static final int REASON_REPLY_MISSING = 8;
    public static final int REASON_REPLY_TIMEOUT = 9;

    private static final String[] REASON_NAMES = {
            "unknown",
            "stream_closed",
            "carrier_down",
            "max_wait",
            "carrier_stalled",
            "request_queued",
            "reply_queued",
            "reply_pending",
            "reply_missing",
            "reply_timeout",
    };

    private WebProxyFlow() {
    }

    public static String className(int streamClass) {
        switch (streamClass) {
            case CLASS_DOWNLOAD:
                return "download";
            case CLASS_UPLOAD:
                return "upload";
            default:
                return "interactive";
        }
    }

    public static int normalizeClass(int streamClass) {
        return streamClass == CLASS_DOWNLOAD || streamClass == CLASS_UPLOAD ? streamClass : CLASS_INTERACTIVE;
    }

    public static String reasonName(int reason) {
        return reason > 0 && reason < REASON_NAMES.length ? REASON_NAMES[reason] : REASON_NAMES[0];
    }

    public static final class Grant {
        public final int streamId;
        public final int maxBytes;

        Grant(int streamId, int maxBytes) {
            this.streamId = streamId;
            this.maxBytes = maxBytes;
        }
    }

    /** Not thread-safe: the transport calls it under its own lock. */
    public static final class UplinkScheduler {
        private static final class Entry {
            int streamClass;
            long inFlight;
            boolean ready;
        }

        private final int frameSize;
        private long uploadLimit;
        private final int priorityBurst;
        private final Map<Integer, Entry> streams = new HashMap<>();
        @SuppressWarnings({"unchecked", "rawtypes"})
        private final ArrayDeque<Integer>[] ready = new ArrayDeque[CLASS_COUNT];
        private long uploadInFlight;
        private int uploadSkipped;

        public UplinkScheduler() {
            this(UPLINK_FRAME_SIZE, UPLINK_UPLOAD_IN_FLIGHT, UPLINK_PRIORITY_BURST);
        }

        public UplinkScheduler(int frameSize, long uploadLimit, int priorityBurst) {
            this.frameSize = frameSize;
            this.uploadLimit = uploadLimit;
            this.priorityBurst = priorityBurst;
            for (int i = 0; i < CLASS_COUNT; i++) {
                ready[i] = new ArrayDeque<>();
            }
        }

        public void add(int streamId, int streamClass) {
            remove(streamId);
            Entry entry = new Entry();
            entry.streamClass = normalizeClass(streamClass);
            streams.put(streamId, entry);
        }

        /** Reclassifies a stream, keeping its in-flight bytes and readiness. */
        public void setClass(int streamId, int streamClass) {
            Entry entry = streams.get(streamId);
            streamClass = normalizeClass(streamClass);
            if (entry == null || entry.streamClass == streamClass) {
                return;
            }
            if (entry.streamClass == CLASS_UPLOAD) {
                uploadInFlight -= entry.inFlight;
            }
            entry.streamClass = streamClass;
            if (streamClass == CLASS_UPLOAD) {
                uploadInFlight += entry.inFlight;
            }
            if (entry.ready) {
                // The stale id in the old queue is skipped by hasReady().
                ready[streamClass].addLast(streamId);
            }
        }

        public void remove(int streamId) {
            Entry entry = streams.remove(streamId);
            if (entry != null && entry.streamClass == CLASS_UPLOAD) {
                uploadInFlight -= entry.inFlight;
            }
            // A stale id may stay in its ready queue; popReady() skips it.
        }

        public void clear() {
            streams.clear();
            for (ArrayDeque<Integer> queue : ready) {
                queue.clear();
            }
            uploadInFlight = 0;
            uploadSkipped = 0;
        }

        /** The stream has data and send credit. Idempotent. */
        public void markReady(int streamId) {
            Entry entry = streams.get(streamId);
            if (entry == null || entry.ready) {
                return;
            }
            entry.ready = true;
            ready[entry.streamClass].addLast(streamId);
        }

        private boolean hasReady(int streamClass) {
            ArrayDeque<Integer> queue = ready[streamClass];
            while (!queue.isEmpty()) {
                Entry entry = streams.get(queue.peekFirst());
                if (entry != null && entry.ready && entry.streamClass == streamClass) {
                    return true;
                }
                queue.pollFirst();
            }
            return false;
        }

        private int popReady(int streamClass) {
            if (!hasReady(streamClass)) {
                return 0;
            }
            int streamId = ready[streamClass].pollFirst();
            streams.get(streamId).ready = false;
            return streamId;
        }

        /**
         * Picks the next stream to write one frame for and removes it from
         * the ready set; mark it ready again if it still has data after the
         * write. Returns null when nothing may be sent now.
         */
        public Grant next() {
            long uploadRoom = uploadLimit - uploadInFlight;
            boolean uploadEligible = uploadRoom > 0 && hasReady(CLASS_UPLOAD);
            boolean preferUpload = uploadEligible && uploadSkipped >= priorityBurst;
            int[] order = preferUpload
                    ? new int[]{CLASS_UPLOAD, CLASS_INTERACTIVE, CLASS_DOWNLOAD}
                    : new int[]{CLASS_INTERACTIVE, CLASS_DOWNLOAD, CLASS_UPLOAD};
            for (int streamClass : order) {
                boolean upload = streamClass == CLASS_UPLOAD;
                if (upload && !uploadEligible) {
                    continue;
                }
                int streamId = popReady(streamClass);
                if (streamId == 0) {
                    continue;
                }
                if (upload) {
                    uploadSkipped = 0;
                } else if (uploadEligible) {
                    uploadSkipped++;
                }
                int maxBytes = upload ? (int) Math.min(frameSize, uploadRoom) : frameSize;
                return new Grant(streamId, maxBytes);
            }
            return null;
        }

        public void sent(int streamId, int bytes) {
            Entry entry = streams.get(streamId);
            if (entry == null || bytes <= 0) {
                return;
            }
            entry.inFlight += bytes;
            if (entry.streamClass == CLASS_UPLOAD) {
                uploadInFlight += bytes;
            }
        }

        public void acknowledged(int streamId, long bytes) {
            Entry entry = streams.get(streamId);
            if (entry == null || bytes <= 0) {
                return;
            }
            long acked = Math.min(entry.inFlight, bytes);
            entry.inFlight -= acked;
            if (entry.streamClass == CLASS_UPLOAD) {
                uploadInFlight -= acked;
            }
        }

        public long uploadInFlight() {
            return uploadInFlight;
        }

        /** Moves the upload cap; never below one frame, so uploads progress. */
        public void setUploadLimit(long bytes) {
            uploadLimit = Math.max(bytes, frameSize);
        }

        public long uploadLimit() {
            return uploadLimit;
        }

        public boolean uploadBlocked() {
            if (uploadInFlight < uploadLimit) {
                return false;
            }
            for (Entry entry : streams.values()) {
                if (entry.ready && entry.streamClass == CLASS_UPLOAD) {
                    return true;
                }
            }
            return false;
        }

        /** Streams per class, then ready streams per class. */
        public int[] stats() {
            int[] result = new int[CLASS_COUNT * 2];
            for (Entry entry : streams.values()) {
                result[entry.streamClass]++;
                if (entry.ready) {
                    result[CLASS_COUNT + entry.streamClass]++;
                }
            }
            return result;
        }
    }

    /** How much credit the relay should hold for a stream of this class. */
    public static long downlinkCreditTarget(int streamClass, int downloadStreams) {
        return downlinkCreditTarget(streamClass, downloadStreams, DOWNLINK_DOWNLOAD_BUDGET);
    }

    /** The same with the download budget the carrier currently allows. */
    public static long downlinkCreditTarget(int streamClass, int downloadStreams, long budget) {
        if (streamClass != CLASS_DOWNLOAD) {
            return DOWNLINK_STREAM_WINDOW;
        }
        long share = budget / Math.max(downloadStreams, 1);
        share = Math.max(DOWNLINK_DOWNLOAD_MIN, Math.min(DOWNLINK_DOWNLOAD_MAX, share));
        return Math.min(share, DOWNLINK_STREAM_WINDOW);
    }

    /**
     * How much of the credit consumed by the reader and not yet returned may
     * be returned now, given how much the relay already holds.
     */
    public static long downlinkCreditRelease(long relayCredit, long withheld, long target) {
        long release = target - relayCredit;
        if (release <= 0) {
            return 0;
        }
        return Math.min(release, Math.max(withheld, 0));
    }

    /**
     * A window that follows the bandwidth-delay product of the carrier; a
     * port of the desktop client's AdaptiveWindow (web_proxy_flow.cpp) with
     * the same bounds and filters and the measured differences listed
     * below.
     *
     * <p>It is fed once per interval with what the carrier delivered in it:
     * bytes credited back by the relay (uplink) or received from it
     * (downlink), the fastest round trip of a frame in the interval, the
     * fastest interactive reply, and whether demand was held back by the
     * window. It keeps
     * <pre>    window = rate * (baseRtt + queueBudget)</pre>
     * where rate is the best recent delivery rate and baseRtt the lowest
     * recent round trip: one bandwidth-delay product keeps the pipe full and
     * the queue budget is what may wait in the shared FIFOs in front of a
     * chat request. It differs from the desktop policy where the Android
     * bench (emulator, real WebView, real relay, netem; upstream DrKLO
     * transport as the throughput floor) showed a cost:
     * <ul>
     * <li>the budget is one round trip but at least 200 ms (desktop:
     * 50..250 ms): with less, the upload fell 3% behind the upstream
     * transport at 50 ms, whose queue is seconds long;</li>
     * <li>growth does not wait for the rate estimate: while the window holds
     * the transfer back and the round trip shows no queue (within half a
     * budget), it doubles per interval like a slow start, so a burst reaches
     * the link rate as fast as without any window; with a small queue it
     * still grows up to the target;</li>
     * <li>between one and one and a half budgets the window holds
     * (round-trip jitter of the browser and the radio); only a queue beyond
     * that for two intervals in a row brings it back to the target, at most
     * halving it, and not while the delivered rate still climbs (the queue
     * of a slow start sits in the browser's own socket).
     * A window merely in use is never drained: while the browser's TCP ramps
     * up the delivered rate, and the target with it, is below the path
     * rate;</li>
     * <li>a slow chat reply cuts the window multiplicatively (at most once per
     * round trip), unless the credit round trip of the same interval shows
     * the queue already drained;</li>
     * <li>neither a drain nor a cut goes below min(2 MiB, 0.8 s of the
     * measured rate): with less the upstream transport kept the link busier
     * (see FLOOR_BYTES).</li>
     * </ul>
     *
     * <p>Times are in milliseconds, -1 means "no sample". Not thread-safe.
     */
    public static final class AdaptiveWindow {
        // Queue allowance: one base round trip, but never less than 200 ms
        // (desktop: 50..250 ms). Below that the bench lost throughput to the
        // upstream transport, which has no carrier window at all: a window
        // a few tens of ms above the bandwidth-delay product leaves the
        // browser's WebSocket TCP idle between bursts of relay credit.
        public static final long QUEUE_BUDGET_PERCENT = 100;
        public static final long QUEUE_BUDGET_MIN = 200;
        public static final long QUEUE_BUDGET_MAX = 300;
        // The window holds while the queue stays under this share of the
        // budget and drains beyond it.
        public static final long QUEUE_CEILING_PERCENT = 150;
        public static final long CONGESTION_DELAY = 500;
        public static final long RATE_WINDOW = 5_000;
        public static final long RTT_WINDOW = 10_000;
        public static final long DECREASE_PERCENT = 70;
        public static final long MAX_GROWTH_PERCENT = 200;
        public static final long MIN_DECREASE_SPACING = 200;
        // Never below this much in flight, up to FLOOR_BYTES: on the bench a
        // window only a round trip or so above the bandwidth-delay product
        // left the link idle 2-3% of the time (credit comes back in bursts),
        // while the upstream transport, with no carrier window at all, kept
        // it busy. 0.8 s of the measured rate keeps the floor short on slow
        // links (a 2 MiB floor would be 3 s of queue at 5 Mbit/s).
        public static final long FLOOR_BYTES = 2L * 1024 * 1024;
        public static final long FLOOR_MS = 800;

        private final long min;
        private final long max;
        // (at, value) pairs, oldest first.
        private final ArrayDeque<long[]> rates = new ArrayDeque<>();
        private final ArrayDeque<long[]> rtts = new ArrayDeque<>();
        private long lastBaseRtt = -1;
        private long window;
        private long target;
        private long lastDecreaseAt;
        private int decreases;
        private int overIntervals;
        private long lastRampAt;
        // The minimum kept past its filter window while our own queue
        // inflates every newer sample (-1 = none).
        private long heldBaseRtt = -1;
        private long lastLimitedAt;

        public AdaptiveWindow(long initial, long min, long max) {
            this.min = min;
            this.max = max;
            window = clamp(initial);
            target = window;
        }

        private long clamp(long value) {
            return Math.max(min, Math.min(max, value));
        }

        public long window() {
            return window;
        }

        public long target() {
            return target;
        }

        public int decreases() {
            return decreases;
        }

        /** Best delivery rate of the recent past, bytes per second. */
        public long rate() {
            long result = 0;
            for (long[] sample : rates) {
                result = Math.max(result, sample[1]);
            }
            return result;
        }

        /** Lowest recent round trip, or -1 before the first sample. */
        public long baseRtt() {
            if (rtts.isEmpty()) {
                return heldBaseRtt >= 0 ? heldBaseRtt : lastBaseRtt;
            }
            long result = heldBaseRtt >= 0 ? heldBaseRtt : Long.MAX_VALUE;
            for (long[] sample : rtts) {
                result = Math.min(result, sample[1]);
            }
            return result;
        }

        private long floor() {
            return Math.min(FLOOR_BYTES, rate() * FLOOR_MS / 1000);
        }

        /** A new carrier, maybe over another network: learn the path anew. */
        public void resetRtt() {
            rtts.clear();
            lastBaseRtt = -1;
            heldBaseRtt = -1;
            lastLimitedAt = 0;
        }

        public void update(long now, long interval, long bytes, boolean windowLimited, long rtt, long interactiveDelay) {
            if (interval <= 0) {
                return;
            }
            long sampleRate = bytes * 1000 / interval;
            if (sampleRate > rate() * 5 / 4) {
                // Still ramping up: the browser's TCP (or the backend) has
                // not reached the path rate yet.
                lastRampAt = now;
            }
            rates.addLast(new long[]{now, sampleRate});
            while (!rates.isEmpty() && now - rates.peekFirst()[0] > RATE_WINDOW) {
                rates.pollFirst();
            }
            if (rtt >= 0) {
                rtts.addLast(new long[]{now, rtt});
            }
            if (windowLimited) {
                lastLimitedAt = now;
            } else if (heldBaseRtt >= 0 && now - lastLimitedAt > RTT_WINDOW) {
                // Our window has not queued anything for a whole filter
                // window: fresh samples are honest again.
                heldBaseRtt = -1;
            }
            while (!rtts.isEmpty() && now - rtts.peekFirst()[0] > RTT_WINDOW) {
                long[] expired = rtts.pollFirst();
                if (windowLimited || heldBaseRtt >= 0) {
                    // While our own window keeps a queue, every fresh sample
                    // includes it: forgetting the old minimum would let the
                    // base, the budget and the window ratchet each other up.
                    heldBaseRtt = heldBaseRtt >= 0 ? Math.min(heldBaseRtt, expired[1]) : expired[1];
                } else if (rtts.isEmpty()) {
                    // An old minimum is forgotten, but never the last one we
                    // have: with no fresh sample it still is the best guess.
                    lastBaseRtt = expired[1];
                }
            }
            long base = baseRtt();
            if (base < 0) {
                return;
            }
            long budget = Math.max(QUEUE_BUDGET_MIN, Math.min(QUEUE_BUDGET_MAX, base * QUEUE_BUDGET_PERCENT / 100));
            // A slow chat reply means the queue hurts chats, unless the credit
            // round trip of the same interval shows the queue already drained:
            // then the reply was held by a transient that is over (typically
            // the browser's TCP ramping up at the start of a burst), and a cut
            // would only slow the transfer down.
            long ceiling = base + budget * QUEUE_CEILING_PERCENT / 100;
            boolean drained = rtt >= 0 && rtt <= ceiling;
            if (interactiveDelay >= 0 && interactiveDelay - base > CONGESTION_DELAY && !drained) {
                long spacing = Math.max(base, MIN_DECREASE_SPACING);
                if (lastDecreaseAt == 0 || now - lastDecreaseAt >= spacing) {
                    lastDecreaseAt = now;
                    window = Math.max(clamp(window * DECREASE_PERCENT / 100), Math.min(window, floor()));
                    target = window;
                    decreases++;
                }
                return;
            }
            long bestRate = rate();
            if (bestRate > 0) {
                target = clamp(Math.max(bestRate * (base + budget) / 1000, floor()));
            }
            boolean over = rtt >= 0 && rtt > ceiling;
            if (over) {
                overIntervals++;
            } else if (rtt >= 0) {
                overIntervals = 0;
            }
            if (over) {
                // A queue beyond the ceiling, two intervals in a row (one
                // alone may be the browser's TCP starting a burst): back to
                // the target, which is exact now that the path is full, but
                // at most by half at a time.
                // Not while the delivered rate still climbs (as a slow start
                // does, by a quarter or more within the last three
                // intervals): that queue sits in the browser's own socket
                // while its TCP opens up, and a smaller window would only
                // keep it from opening.
                boolean ramping = lastRampAt != 0 && now - lastRampAt < 3 * interval;
                if (overIntervals >= 2 && !ramping && bestRate > 0 && target < window) {
                    window = Math.max(target, clamp(window / 2));
                }
            } else if (windowLimited && rtt >= 0) {
                if (rtt <= base + budget / 2) {
                    // The window holds the transfer back and nothing queues:
                    // probe up like a slow start, 2x per interval, until the
                    // round trip shows a queue. Throughput comes first; the
                    // queue is bounded by the drain above.
                    window = clamp(window * MAX_GROWTH_PERCENT / 100);
                } else if (rtt <= base + budget && window < target) {
                    window = Math.min(target, clamp(window * MAX_GROWTH_PERCENT / 100));
                }
            }
            // Between the budget and the ceiling the window holds: round-trip
            // jitter of the browser and the radio does not move it.
            if (windowLimited) {
                window = Math.max(window, clamp(floor()));
            }
        }
    }

    /**
     * Feeds an {@link AdaptiveWindow} from per-tick carrier counters so that
     * its samples mean what the window assumes.
     *
     * <p>Relay credit and relay data arrive in bursts (one WebSocket message
     * carries whatever the relay had queued), so bytes per short tick
     * overstate the path rate, and the window then settles well above the
     * bandwidth-delay product. The rate handed to the window is therefore
     * measured over the most recent span of at least one base round trip.
     * Likewise "the window held the transfer back" is only claimed once it
     * did so for a whole round trip: during the first round trip of a burst
     * no credit can have come back yet, which says nothing about the path.
     */
    public static final class DeliverySampler {
        public static final long MIN_SPAN_MS = 200;
        private static final int MAX_HISTORY = 64;

        // (at, delivered so far), oldest first.
        private final ArrayDeque<long[]> history = new ArrayDeque<>();
        private long delivered;
        private long limitedSince = -1;

        public void delivered(long bytes) {
            if (bytes > 0) {
                delivered += bytes;
            }
        }

        public long total() {
            return delivered;
        }

        /**
         * Called once per tick with what the carrier saw since the previous
         * tick: whether the window was the limit, the fastest credit round
         * trip and the fastest interactive reply (-1 when none).
         */
        public void tick(AdaptiveWindow window, long now, boolean limited, long rtt, long interactiveDelay) {
            long base = window.baseRtt();
            if (base < 0) {
                base = rtt;
            }
            long span = Math.max(MIN_SPAN_MS, base);
            if (limited) {
                if (limitedSince < 0) {
                    // It may have started anywhere in the interval.
                    limitedSince = now;
                }
            } else {
                limitedSince = -1;
            }
            history.addLast(new long[]{now, delivered});
            while (history.size() > MAX_HISTORY) {
                history.pollFirst();
            }
            // Keep the newest entry at least one span old, drop what is older.
            while (history.size() > 1) {
                Iterator<long[]> it = history.iterator();
                it.next();
                long[] second = it.next();
                if (now - second[0] >= span) {
                    history.pollFirst();
                } else {
                    break;
                }
            }
            long[] from = history.peekFirst();
            long interval = now - from[0];
            long bytes = delivered - from[1];
            boolean heldBack = limited && now - limitedSince >= span;
            window.update(now, interval, bytes, heldBack, rtt, interactiveDelay);
        }
    }

    public static final class CarrierHealth {
        public boolean connected;
        // Any frame from the relay.
        public long lastDownlinkAt;
        // Any credit from the relay: our uplink bytes reached its backends.
        public long lastCreditAt;
        // Bytes written to the carrier and not yet credited, all streams.
        public long unackedBytes;
        // When unackedBytes last became non-zero.
        public long outstandingSince;
    }

    public static final class StreamHealth {
        public boolean open;
        // Waiting in the client, not yet handed to the carrier.
        public long queuedBytes;
        // Handed to the carrier, not yet written to the backend by the relay.
        public long unackedBytes;
        public long lastReceivedAt;
        // When everything this stream sent last reached the backend.
        public long deliveredAt;
    }

    public static final class Decision {
        public final boolean waitMore;
        public final int reason;
        public final long waitMs;

        Decision(boolean waitMore, int reason, long waitMs) {
            this.waitMore = waitMore;
            this.reason = reason;
            this.waitMs = waitMs;
        }

        /**
         * JNI encoding: a positive value is "wait", (waitMs << 4) | reason;
         * a negative value is "fail", -reason.
         */
        public long encode() {
            return waitMore ? (Math.max(waitMs, 1) << 4) | reason : -reason;
        }
    }

    public static long decodeWaitMs(long verdict) {
        return verdict > 0 ? verdict >> 4 : 0;
    }

    public static int decodeReason(long verdict) {
        return verdict > 0 ? (int) (verdict & 0xf) : (int) -verdict;
    }

    public static long carrierProgressAt(CarrierHealth carrier) {
        return Math.max(carrier.outstandingSince, Math.max(carrier.lastCreditAt, carrier.lastDownlinkAt));
    }

    public static boolean carrierStalled(long now, CarrierHealth carrier) {
        if (!carrier.connected || carrier.unackedBytes <= 0) {
            return false;
        }
        return now - carrierProgressAt(carrier) >= CARRIER_STALL_MS;
    }

    /**
     * Called when a connection saw no data for its usual receive timeout.
     * Wait means the reply is plausibly still queued in a carrier that makes
     * progress; a stalled carrier is recovered by the carrier itself, once
     * for all of its streams, so the stream waits for that as well.
     */
    public static Decision decideReceiveWait(long now, long waitStartedAt, CarrierHealth carrier, StreamHealth stream) {
        if (stream == null || !stream.open) {
            return new Decision(false, REASON_STREAM_CLOSED, 0);
        } else if (!carrier.connected) {
            return new Decision(false, REASON_CARRIER_DOWN, 0);
        }
        long waited = now - waitStartedAt;
        if (waited >= MAX_WAIT_MS) {
            return new Decision(false, REASON_MAX_WAIT, 0);
        }
        long left = MAX_WAIT_MS - waited;
        if (carrierStalled(now, carrier)) {
            // The carrier watchdog tears every stream down at once; failing
            // here first would only reopen streams on the stuck carrier.
            return waitDecision(REASON_CARRIER_STALLED, RECHECK_MS, left);
        } else if (stream.queuedBytes > 0 || stream.unackedBytes > 0) {
            // The request itself has not reached the backend yet, and the
            // carrier is moving (otherwise it would be stalled above).
            return waitDecision(REASON_QUEUED, RECHECK_MS, left);
        }
        long replyFrom = Math.max(stream.deliveredAt, Math.max(stream.lastReceivedAt, waitStartedAt));
        long silent = now - replyFrom;
        if (silent >= BUSY_REPLY_MS) {
            return new Decision(false, REASON_REPLY_TIMEOUT, 0);
        }
        long downlinkIdle = now - carrier.lastDownlinkAt;
        if (downlinkIdle < BUSY_WINDOW_MS) {
            return waitDecision(REASON_REPLY_QUEUED, RECHECK_MS, left);
        }
        // Nothing is arriving at all, so nothing can be queued in front of
        // the reply: count the time both the request and the downlink were
        // idle.
        long quiet = Math.min(silent, downlinkIdle);
        if (quiet >= QUIET_REPLY_MS) {
            return new Decision(false, REASON_REPLY_MISSING, 0);
        }
        return waitDecision(REASON_REPLY_PENDING, QUIET_REPLY_MS - quiet, left);
    }

    private static Decision waitDecision(int reason, long delay, long left) {
        return new Decision(true, reason, Math.max(1, Math.min(delay, left)));
    }
}
