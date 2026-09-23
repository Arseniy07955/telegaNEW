package org.telegram.proxy;

import java.util.ArrayDeque;
import java.util.HashMap;
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
 * yet written to its backend are capped;</li>
 * <li>downlink: the relay may only read a backend while it holds our credit,
 * so download streams get a small credit instead of the full protocol
 * window;</li>
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
    // uplink FIFO.
    public static final long UPLINK_UPLOAD_IN_FLIGHT = 1024 * 1024;
    // A steady stream of interactive/download frames must not stop uploads
    // forever: after this many of them in a row while an upload was
    // eligible, one upload frame goes out.
    public static final int UPLINK_PRIORITY_BURST = 16;

    // Downlink. The protocol window is fixed by the relay (4 MiB).
    public static final long DOWNLINK_STREAM_WINDOW = 4L * 1024 * 1024;
    // Credit shared by all download streams: the most media that can be
    // queued in the relay's downlink FIFO in front of an interactive reply.
    public static final long DOWNLINK_DOWNLOAD_BUDGET = 2L * 1024 * 1024;
    public static final long DOWNLINK_DOWNLOAD_MIN = 256 * 1024;
    public static final long DOWNLINK_DOWNLOAD_MAX = 1024 * 1024;

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
        private final long uploadLimit;
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
        if (streamClass != CLASS_DOWNLOAD) {
            return DOWNLINK_STREAM_WINDOW;
        }
        long share = DOWNLINK_DOWNLOAD_BUDGET / Math.max(downloadStreams, 1);
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
