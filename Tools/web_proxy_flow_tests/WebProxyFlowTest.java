package org.telegram.proxy;

/**
 * Plain-JVM tests of WebProxyFlow, compiled and run by
 * Tools/check_web_proxy_isolation.py (javac --release 8, no Android SDK).
 */
public final class WebProxyFlowTest {
    private static int checks;

    private static void check(boolean condition, String message) {
        checks++;
        if (!condition) {
            throw new AssertionError(message);
        }
    }

    private static final int I1 = 1;
    private static final int I2 = 2;
    private static final int D1 = 11;
    private static final int D2 = 12;
    private static final int U1 = 21;
    private static final int U2 = 22;

    public static void main(String[] args) {
        interactiveGoesFirst();
        bulkIsRoundRobin();
        uploadInFlightIsCapped();
        uploadIsNotStarvedForever();
        reclassifyKeepsAccounting();
        downlinkCredit();
        carrierStall();
        receiveWaitDecisions();
        verdictEncoding();
        adaptiveGrowsOnFreePath();
        adaptiveShrinksWhenQueueGrows();
        adaptiveIgnoresIdleFlow();
        adaptiveCongestionSpacing();
        adaptiveBaseRttExpires();
        baseRttHeldUnderOwnQueue();
        samplerSpansOneRoundTrip();
        samplerClaimsLimitOnlyAfterRoundTrip();
        System.out.println("WebProxyFlowTest passed " + checks + " checks.");
    }

    private static WebProxyFlow.UplinkScheduler scheduler() {
        WebProxyFlow.UplinkScheduler scheduler = new WebProxyFlow.UplinkScheduler();
        scheduler.add(I1, WebProxyFlow.CLASS_INTERACTIVE);
        scheduler.add(I2, WebProxyFlow.CLASS_INTERACTIVE);
        scheduler.add(D1, WebProxyFlow.CLASS_DOWNLOAD);
        scheduler.add(D2, WebProxyFlow.CLASS_DOWNLOAD);
        scheduler.add(U1, WebProxyFlow.CLASS_UPLOAD);
        scheduler.add(U2, WebProxyFlow.CLASS_UPLOAD);
        return scheduler;
    }

    private static void interactiveGoesFirst() {
        WebProxyFlow.UplinkScheduler scheduler = scheduler();
        scheduler.markReady(U1);
        scheduler.markReady(D1);
        scheduler.markReady(I1);
        check(scheduler.next().streamId == I1, "an interactive stream is served before bulk streams");
        check(scheduler.next().streamId == D1, "download requests come before upload data");
        WebProxyFlow.Grant upload = scheduler.next();
        check(upload.streamId == U1 && upload.maxBytes == WebProxyFlow.UPLINK_FRAME_SIZE, "upload gets one frame-sized grant");
        check(scheduler.next() == null, "nothing is granted once no stream is ready");
        scheduler.markReady(I1);
        scheduler.markReady(I1);
        check(scheduler.next().streamId == I1 && scheduler.next() == null, "markReady is idempotent");
    }

    private static void bulkIsRoundRobin() {
        WebProxyFlow.UplinkScheduler scheduler = scheduler();
        scheduler.markReady(U1);
        scheduler.markReady(U2);
        int[] order = new int[6];
        for (int i = 0; i < order.length; i++) {
            WebProxyFlow.Grant grant = scheduler.next();
            order[i] = grant.streamId;
            scheduler.sent(grant.streamId, 1000);
            scheduler.acknowledged(grant.streamId, 1000);
            scheduler.markReady(grant.streamId);
        }
        check(order[0] == U1 && order[1] == U2 && order[2] == U1 && order[3] == U2 && order[4] == U1 && order[5] == U2,
                "two upload streams alternate frame by frame");
    }

    private static void uploadInFlightIsCapped() {
        WebProxyFlow.UplinkScheduler scheduler = scheduler();
        long limit = WebProxyFlow.UPLINK_UPLOAD_IN_FLIGHT;
        long sent = 0;
        while (true) {
            scheduler.markReady(U1);
            WebProxyFlow.Grant grant = scheduler.next();
            if (grant == null) {
                break;
            }
            check(grant.streamId == U1, "only the upload stream is ready");
            check(grant.maxBytes <= limit - sent, "an upload grant never exceeds the remaining in-flight room");
            scheduler.sent(U1, grant.maxBytes);
            sent += grant.maxBytes;
        }
        check(sent == limit && scheduler.uploadInFlight() == limit, "uploads stop exactly at the in-flight cap");
        check(scheduler.uploadBlocked(), "a ready upload over the cap is reported as blocked");
        scheduler.markReady(I1);
        check(scheduler.next().streamId == I1, "interactive frames still go out while uploads are capped");
        scheduler.acknowledged(U1, 10_000);
        WebProxyFlow.Grant resumed = scheduler.next();
        check(resumed != null && resumed.streamId == U1 && resumed.maxBytes == 10_000, "relay credit reopens exactly the credited room");
        scheduler.remove(U1);
        check(scheduler.uploadInFlight() == 0, "a removed upload stream frees its in-flight bytes");

        WebProxyFlow.UplinkScheduler moved = scheduler();
        moved.setUploadLimit(3L << 20);
        check(moved.uploadLimit() == 3L << 20, "the carrier moves the upload cap");
        moved.setUploadLimit(10);
        check(moved.uploadLimit() == WebProxyFlow.UPLINK_FRAME_SIZE, "the cap never drops below one frame");
    }

    private static void uploadIsNotStarvedForever() {
        WebProxyFlow.UplinkScheduler scheduler = scheduler();
        scheduler.markReady(U1);
        int beforeUpload = 0;
        while (true) {
            scheduler.markReady(I1);
            WebProxyFlow.Grant grant = scheduler.next();
            if (grant.streamId == U1) {
                break;
            }
            beforeUpload++;
            check(beforeUpload <= WebProxyFlow.UPLINK_PRIORITY_BURST, "an eligible upload is served within one priority burst");
        }
        check(beforeUpload == WebProxyFlow.UPLINK_PRIORITY_BURST, "uploads yield exactly one priority burst to interactive frames");
    }

    private static void reclassifyKeepsAccounting() {
        WebProxyFlow.UplinkScheduler scheduler = new WebProxyFlow.UplinkScheduler();
        scheduler.add(U1, WebProxyFlow.CLASS_INTERACTIVE);
        scheduler.markReady(U1);
        scheduler.sent(U1, 5000);
        check(scheduler.uploadInFlight() == 0, "interactive bytes are not upload bytes");
        scheduler.setClass(U1, WebProxyFlow.CLASS_UPLOAD);
        check(scheduler.uploadInFlight() == 5000, "reclassified in-flight bytes count against the upload cap");
        WebProxyFlow.Grant grant = scheduler.next();
        check(grant != null && grant.streamId == U1, "a ready stream stays ready in its new class");
        scheduler.setClass(U1, WebProxyFlow.CLASS_DOWNLOAD);
        check(scheduler.uploadInFlight() == 0, "leaving the upload class releases its in-flight bytes");
        int[] stats = scheduler.stats();
        check(stats[WebProxyFlow.CLASS_DOWNLOAD] == 1 && stats[WebProxyFlow.CLASS_UPLOAD] == 0, "stats follow the class");
        check(WebProxyFlow.normalizeClass(7) == WebProxyFlow.CLASS_INTERACTIVE, "unknown classes are interactive");
    }

    private static void downlinkCredit() {
        long window = WebProxyFlow.DOWNLINK_STREAM_WINDOW;
        check(WebProxyFlow.downlinkCreditTarget(WebProxyFlow.CLASS_INTERACTIVE, 3) == window, "interactive streams keep the full window");
        check(WebProxyFlow.downlinkCreditTarget(WebProxyFlow.CLASS_UPLOAD, 3) == window, "upload replies keep the full window");
        check(WebProxyFlow.downlinkCreditTarget(WebProxyFlow.CLASS_DOWNLOAD, 1) == WebProxyFlow.DOWNLINK_DOWNLOAD_BUDGET, "a lone download gets the whole budget");
        check(WebProxyFlow.downlinkCreditTarget(WebProxyFlow.CLASS_DOWNLOAD, 0) == WebProxyFlow.DOWNLINK_DOWNLOAD_BUDGET, "zero streams does not divide by zero");
        check(WebProxyFlow.downlinkCreditTarget(WebProxyFlow.CLASS_DOWNLOAD, 1, 64L << 20) == WebProxyFlow.DOWNLINK_DOWNLOAD_MAX, "a share never exceeds the per-stream max");
        check(WebProxyFlow.downlinkCreditTarget(WebProxyFlow.CLASS_DOWNLOAD, 2, 3L << 20) == 3L << 19, "the adaptive budget is shared like the fixed one");
        check(WebProxyFlow.downlinkCreditTarget(WebProxyFlow.CLASS_INTERACTIVE, 2, 256 * 1024) == window, "the budget never touches interactive streams");
        check(WebProxyFlow.downlinkCreditTarget(WebProxyFlow.CLASS_DOWNLOAD, 4) == WebProxyFlow.DOWNLINK_DOWNLOAD_BUDGET / 4, "downloads share the budget");
        check(WebProxyFlow.downlinkCreditTarget(WebProxyFlow.CLASS_DOWNLOAD, 64) == WebProxyFlow.DOWNLINK_DOWNLOAD_MIN, "a download never drops below the per-stream minimum");
        check(WebProxyFlow.downlinkCreditRelease(window - 65536, 65536, window) == 65536, "full-window streams get every consumed byte back");
        check(WebProxyFlow.downlinkCreditRelease(window - 65536, 65536, 1024 * 1024) == 0, "credit above the target is withheld");
        check(WebProxyFlow.downlinkCreditRelease(900 * 1024, 3 * 1024 * 1024, 1024 * 1024) == 124 * 1024, "credit is topped up to the target");
        check(WebProxyFlow.downlinkCreditRelease(0, 10_000, 1024 * 1024) == 10_000, "never more than was withheld");
        check(WebProxyFlow.downlinkCreditRelease(0, -5, 1024 * 1024) == 0, "negative withheld releases nothing");
    }

    private static WebProxyFlow.CarrierHealth carrier(long lastDownlinkAt, long lastCreditAt, long unacked, long outstandingSince) {
        WebProxyFlow.CarrierHealth carrier = new WebProxyFlow.CarrierHealth();
        carrier.connected = true;
        carrier.lastDownlinkAt = lastDownlinkAt;
        carrier.lastCreditAt = lastCreditAt;
        carrier.unackedBytes = unacked;
        carrier.outstandingSince = outstandingSince;
        return carrier;
    }

    private static void carrierStall() {
        long now = 1_000_000;
        check(!WebProxyFlow.carrierStalled(now, carrier(0, 0, 0, 0)), "an idle carrier with nothing outstanding is never stalled");
        check(WebProxyFlow.carrierStalled(now, carrier(now - 25_000, now - 30_000, 4096, now - 40_000)), "outstanding bytes and 20 s without relay frames is a stall");
        check(!WebProxyFlow.carrierStalled(now, carrier(now - 1_000, now - 30_000, 4096, now - 40_000)), "any relay frame is progress");
        check(!WebProxyFlow.carrierStalled(now, carrier(now - 25_000, now - 30_000, 4096, now - 5_000)), "newly outstanding bytes get the full stall window");
        WebProxyFlow.CarrierHealth down = carrier(0, 0, 4096, 1);
        down.connected = false;
        check(!WebProxyFlow.carrierStalled(now, down), "a carrier that is down is not judged as stalled");
    }

    private static WebProxyFlow.StreamHealth stream(long queued, long unacked, long lastReceivedAt, long deliveredAt) {
        WebProxyFlow.StreamHealth stream = new WebProxyFlow.StreamHealth();
        stream.open = true;
        stream.queuedBytes = queued;
        stream.unackedBytes = unacked;
        stream.lastReceivedAt = lastReceivedAt;
        stream.deliveredAt = deliveredAt;
        return stream;
    }

    private static void expect(WebProxyFlow.Decision decision, boolean wait, int reason, String message) {
        check(decision.waitMore == wait && decision.reason == reason,
                message + " (got " + (decision.waitMore ? "wait " : "fail ") + WebProxyFlow.reasonName(decision.reason) + ")");
    }

    private static void receiveWaitDecisions() {
        long now = 10_000_000;
        long start = now - 12_000;
        WebProxyFlow.CarrierHealth busy = carrier(now - 100, now - 200, 0, 0);
        WebProxyFlow.CarrierHealth quiet = carrier(now - 12_000, now - 12_000, 0, 0);

        WebProxyFlow.StreamHealth closed = new WebProxyFlow.StreamHealth();
        expect(WebProxyFlow.decideReceiveWait(now, start, busy, closed), false, WebProxyFlow.REASON_STREAM_CLOSED, "a closed stream fails");
        expect(WebProxyFlow.decideReceiveWait(now, start, busy, null), false, WebProxyFlow.REASON_STREAM_CLOSED, "an unknown stream fails");
        WebProxyFlow.CarrierHealth down = carrier(now, now, 0, 0);
        down.connected = false;
        expect(WebProxyFlow.decideReceiveWait(now, start, down, stream(0, 0, 0, 0)), false, WebProxyFlow.REASON_CARRIER_DOWN, "a stream on a carrier that is down fails");

        expect(WebProxyFlow.decideReceiveWait(now, start, busy, stream(4096, 0, 0, 0)), true, WebProxyFlow.REASON_QUEUED, "a request still queued in the client waits");
        expect(WebProxyFlow.decideReceiveWait(now, start, busy, stream(0, 4096, 0, 0)), true, WebProxyFlow.REASON_QUEUED, "a request not yet delivered by the relay waits");
        expect(WebProxyFlow.decideReceiveWait(now, now - 70_000, busy, stream(4096, 0, 0, 0)), false, WebProxyFlow.REASON_MAX_WAIT, "no silence outlives the absolute bound");

        WebProxyFlow.CarrierHealth stalled = carrier(now - 25_000, now - 25_000, 4096, now - 40_000);
        expect(WebProxyFlow.decideReceiveWait(now, start, stalled, stream(0, 4096, 0, 0)), true, WebProxyFlow.REASON_CARRIER_STALLED, "a stalled carrier is left to its own watchdog");

        expect(WebProxyFlow.decideReceiveWait(now, start, busy, stream(0, 0, 0, now - 10_000)), true, WebProxyFlow.REASON_REPLY_QUEUED, "a delivered request waits while the downlink is busy");
        expect(WebProxyFlow.decideReceiveWait(now, now - 31_000, busy, stream(0, 0, 0, now - 31_000)), false, WebProxyFlow.REASON_REPLY_TIMEOUT, "a busy downlink excuses at most BUSY_REPLY_MS");

        expect(WebProxyFlow.decideReceiveWait(now, start, quiet, stream(0, 0, 0, now - 12_000)), false, WebProxyFlow.REASON_REPLY_MISSING, "a delivered request on a quiet carrier fails after QUIET_REPLY_MS");
        WebProxyFlow.CarrierHealth recent = carrier(now - 5_000, now - 5_000, 0, 0);
        WebProxyFlow.Decision pending = WebProxyFlow.decideReceiveWait(now, now - 5_000, recent, stream(0, 0, 0, now - 5_000));
        expect(pending, true, WebProxyFlow.REASON_REPLY_PENDING, "a reply may still come within QUIET_REPLY_MS");
        check(pending.waitMs == WebProxyFlow.QUIET_REPLY_MS - 5_000, "the wait ends exactly when the quiet period would");

        WebProxyFlow.Decision nearBound = WebProxyFlow.decideReceiveWait(now, now - (WebProxyFlow.MAX_WAIT_MS - 500), busy, stream(1, 0, 0, 0));
        check(nearBound.waitMore && nearBound.waitMs == 500, "a wait never extends past the absolute bound");
    }

    private static void verdictEncoding() {
        WebProxyFlow.Decision wait = new WebProxyFlow.Decision(true, WebProxyFlow.REASON_REPLY_QUEUED, 2000);
        long encoded = wait.encode();
        check(encoded > 0 && WebProxyFlow.decodeWaitMs(encoded) == 2000 && WebProxyFlow.decodeReason(encoded) == WebProxyFlow.REASON_REPLY_QUEUED,
                "a wait verdict carries its delay and reason");
        WebProxyFlow.Decision fail = new WebProxyFlow.Decision(false, WebProxyFlow.REASON_REPLY_MISSING, 0);
        check(fail.encode() == -WebProxyFlow.REASON_REPLY_MISSING && WebProxyFlow.decodeReason(fail.encode()) == WebProxyFlow.REASON_REPLY_MISSING,
                "a fail verdict is the negative reason");
        check(WebProxyFlow.REASON_REPLY_TIMEOUT < 16, "reasons fit the 4-bit JNI field");
        for (int reason = WebProxyFlow.REASON_STREAM_CLOSED; reason <= WebProxyFlow.REASON_REPLY_TIMEOUT; reason++) {
            check(!"unknown".equals(WebProxyFlow.reasonName(reason)), "every reason has a name");
        }
        check("unknown".equals(WebProxyFlow.reasonName(0)) && "unknown".equals(WebProxyFlow.reasonName(99)), "out-of-range reasons are unknown");
    }

    // A path with a bottleneck of `capacity` bytes per second and a base
    // round trip of `baseRtt` ms, fed as the carrier would feed it:
    // everything above one bandwidth-delay product waits in the queue, and
    // interactive frames wait behind it. Same model as the desktop tests.
    private static final class Path {
        final long capacity;
        final long baseRtt;

        Path(long capacity, long baseRtt) {
            this.capacity = capacity;
            this.baseRtt = baseRtt;
        }

        long queueDelay(long window) {
            long bdp = capacity * baseRtt / 1000;
            return window > bdp ? (window - bdp) * 1000 / capacity : 0;
        }

        void feed(WebProxyFlow.AdaptiveWindow window, long now, long interval) {
            long rtt = baseRtt + queueDelay(window.window());
            long rate = Math.min(window.window() * 1000 / rtt, capacity);
            window.update(now, interval, rate * interval / 1000, true, rtt, rtt);
        }
    }

    private static WebProxyFlow.AdaptiveWindow adaptive(long initial) {
        return new WebProxyFlow.AdaptiveWindow(initial, WebProxyFlow.UPLINK_WINDOW_MIN, WebProxyFlow.UPLINK_WINDOW_MAX);
    }

    private static void adaptiveGrowsOnFreePath() {
        WebProxyFlow.AdaptiveWindow window = adaptive(WebProxyFlow.UPLINK_UPLOAD_IN_FLIGHT);
        long start = window.window();
        Path path = new Path(20L << 20, 100);
        long now = 1000;
        boolean neverBelowStart = true;
        for (int i = 0; i < 50; i++) {
            now += 200;
            path.feed(window, now, 200);
            neverBelowStart = neverBelowStart && window.window() >= start;
        }
        // 20 MiB/s * (100 ms + budget) at least, the budget's ceiling at most.
        long budget = Math.max(WebProxyFlow.AdaptiveWindow.QUEUE_BUDGET_MIN,
                Math.min(WebProxyFlow.AdaptiveWindow.QUEUE_BUDGET_MAX, 100 * WebProxyFlow.AdaptiveWindow.QUEUE_BUDGET_PERCENT / 100));
        long ceiling = budget * WebProxyFlow.AdaptiveWindow.QUEUE_CEILING_PERCENT / 100;
        long expected = Math.min((20L << 20) * (100 + budget) / 1000, WebProxyFlow.UPLINK_WINDOW_MAX);
        check(window.window() > start, "a free path grows the window");
        check(neverBelowStart, "a free path never shrinks the window below where it started");
        check(window.window() >= expected * 8 / 10,
                "the window settles at least near rate * (rtt + budget) (got " + window.window() + ", expected " + expected + ")");
        check(path.queueDelay(window.window()) <= ceiling,
                "what waits in front of a chat stays within the queue ceiling");

        WebProxyFlow.AdaptiveWindow fast = adaptive(WebProxyFlow.UPLINK_UPLOAD_IN_FLIGHT);
        Path wide = new Path(100L << 20, 100);
        now = 1000;
        int steps = 0;
        while (fast.window() < WebProxyFlow.UPLINK_WINDOW_MAX && steps < 40) {
            now += 200;
            wide.feed(fast, now, 200);
            steps++;
        }
        check(fast.window() == WebProxyFlow.UPLINK_WINDOW_MAX, "a wide path reaches the upper bound");
        check(steps <= 4, "growth doubles per interval, not per second (took " + steps + " intervals)");
    }

    private static void adaptiveShrinksWhenQueueGrows() {
        WebProxyFlow.AdaptiveWindow window = adaptive(8L << 20);
        // A slow uplink: 400 KB/s at 60 ms. 8 MiB in flight means 20 s of
        // queue in front of every chat request.
        Path path = new Path(400_000, 60);
        long now = 1000;
        window.update(now, 200, 80_000, false, 60, -1);
        for (int i = 0; i < 40; i++) {
            now += 200;
            path.feed(window, now, 200);
        }
        long floor = Math.max(WebProxyFlow.UPLINK_WINDOW_MIN,
                Math.min(WebProxyFlow.AdaptiveWindow.FLOOR_BYTES, 400_000 * WebProxyFlow.AdaptiveWindow.FLOOR_MS / 1000));
        check(window.window() <= floor * 11 / 10 && window.window() >= WebProxyFlow.UPLINK_WINDOW_MIN,
                "a queue far beyond the budget shrinks the window to the floor (got " + window.window() + ", floor " + floor + ")");
        check(path.queueDelay(window.window()) <= WebProxyFlow.AdaptiveWindow.FLOOR_MS,
                "on a slow path the floor is time, not bytes: at most FLOOR_MS of queue");
        check(window.decreases() > 0, "slow interactive replies cut the window multiplicatively");
    }

    private static void adaptiveIgnoresIdleFlow() {
        WebProxyFlow.AdaptiveWindow window = adaptive(WebProxyFlow.UPLINK_UPLOAD_IN_FLIGHT);
        long start = window.window();
        long now = 1000;
        for (int i = 0; i < 30; i++) {
            now += 200;
            window.update(now, 200, 1000, false, 80, 90);
        }
        check(window.window() == start, "an application-limited flow neither grows nor drains the window");
        WebProxyFlow.AdaptiveWindow unknown = adaptive(WebProxyFlow.UPLINK_UPLOAD_IN_FLIGHT);
        unknown.update(1000, 200, 10L << 20, true, -1, -1);
        check(unknown.window() == start, "without a round trip sample there is nothing to size by");
    }

    private static void adaptiveCongestionSpacing() {
        WebProxyFlow.AdaptiveWindow window = adaptive(WebProxyFlow.UPLINK_UPLOAD_IN_FLIGHT);
        long start = window.window();
        window.update(1000, 200, 1, false, 300, -1);
        window.update(1050, 50, 1, false, 300, 1500);
        check(window.window() == start, "a slow chat reply whose queue already drained is no reason to cut");
        window.update(1100, 50, 1, false, -1, 1500);
        check(window.window() == start * 70 / 100, "congestion cuts to 70%");
        window.update(1150, 50, 1, false, -1, 1500);
        check(window.window() == start * 70 / 100, "at most one cut per round trip");
        window.update(1500, 350, 1, false, -1, 1500);
        check(window.window() == start * 70 / 100 * 70 / 100, "a later round trip may cut again");
        for (int i = 0; i < 20; i++) {
            window.update(2000 + i * 400, 400, 1, false, -1, 5000);
        }
        check(window.window() == WebProxyFlow.UPLINK_WINDOW_MIN, "the window never drops below its floor");
    }

    private static void adaptiveBaseRttExpires() {
        WebProxyFlow.AdaptiveWindow window = adaptive(WebProxyFlow.UPLINK_UPLOAD_IN_FLIGHT);
        window.update(1000, 200, 1, false, 50, -1);
        window.update(2000, 200, 1, false, 200, -1);
        check(window.baseRtt() == 50, "the lowest recent round trip is the base");
        window.update(12_500, 200, 1, false, 200, -1);
        check(window.baseRtt() == 200, "an old minimum is forgotten");
        window.update(40_000, 200, 1, false, -1, -1);
        check(window.baseRtt() == 200, "without fresh samples the last base is kept");
    }

    private static void baseRttHeldUnderOwnQueue() {
        // A long upload keeps a standing queue: every sample is base + queue.
        // The old minimum must survive its filter window, or the base, the
        // budget and the window ratchet each other up.
        WebProxyFlow.AdaptiveWindow window = adaptive(WebProxyFlow.UPLINK_UPLOAD_IN_FLIGHT);
        window.update(1000, 200, 100_000, false, 100, -1);
        long now = 1000;
        for (int i = 0; i < 100; i++) {
            now += 200;
            window.update(now, 200, 100_000, true, 330, -1);
        }
        check(window.baseRtt() == 100, "the base survives a long transfer that keeps its own queue (got " + window.baseRtt() + ")");
        for (int i = 0; i < 60; i++) {
            now += 200;
            window.update(now, 200, 1000, false, 300, -1);
        }
        check(window.baseRtt() == 300, "after a quiet filter window the base follows the path again");
        window.resetRtt();
        check(window.baseRtt() == -1, "a new carrier learns the path anew");
    }

    private static void samplerSpansOneRoundTrip() {
        // Credit arrives in bursts: 1 MB every 500 ms on a 2 MB/s path with
        // a 500 ms round trip. Per 200 ms tick that reads as 5 MB/s.
        WebProxyFlow.AdaptiveWindow window = adaptive(WebProxyFlow.UPLINK_UPLOAD_IN_FLIGHT);
        WebProxyFlow.DeliverySampler sampler = new WebProxyFlow.DeliverySampler();
        long now = 0;
        for (int tick = 0; tick < 60; tick++) {
            now += 200;
            if (tick % 5 == 0) {
                sampler.delivered(1_000_000);
            }
            sampler.tick(window, now, false, 500, -1);
        }
        check(window.rate() <= 2_600_000, "the rate is measured over a round trip, not over one burst (got " + window.rate() + ")");
        check(window.rate() >= 1_500_000, "bursty credit still shows the path rate (got " + window.rate() + ")");
        check(sampler.total() == 12_000_000, "the sampler counts every delivered byte");
    }

    private static void samplerClaimsLimitOnlyAfterRoundTrip() {
        // An idle carrier (a few chat bytes) starts a burst: during its first
        // round trip nothing can have been credited yet, so the burst must
        // neither grow nor shrink the window before a round trip passed.
        WebProxyFlow.AdaptiveWindow window = adaptive(WebProxyFlow.UPLINK_UPLOAD_IN_FLIGHT);
        WebProxyFlow.DeliverySampler sampler = new WebProxyFlow.DeliverySampler();
        long now = 0;
        for (int tick = 0; tick < 10; tick++) {
            now += 200;
            sampler.delivered(200);
            sampler.tick(window, now, false, 600, -1);
        }
        long before = window.window();
        for (int tick = 0; tick < 3; tick++) {
            now += 200;
            sampler.delivered(1_000_000);
            sampler.tick(window, now, true, 600, -1);
        }
        check(window.window() == before, "a window limited for less than a round trip is left alone");
        for (int tick = 0; tick < 4; tick++) {
            now += 200;
            sampler.delivered(1_000_000);
            sampler.tick(window, now, true, 600, -1);
        }
        check(window.window() > before, "a window that held a burst back for a whole round trip grows");

        // A ramping burst delivers far less than the window: without a queue
        // (round trip at its base) that is no reason to shrink.
        WebProxyFlow.AdaptiveWindow ramp = adaptive(4L << 20);
        ramp.update(1000, 200, 1000, false, 100, -1);
        for (int i = 0; i < 10; i++) {
            ramp.update(1200 + i * 200L, 200, 20_000, true, 100, -1);
        }
        check(ramp.window() >= 4L << 20, "a window in use but without a queue is not drained");
        long grown = ramp.window();
        ramp.update(4000, 200, 20_000, true, 900, -1);
        check(ramp.window() == grown, "one interval over the budget may be a burst starting");
        ramp.update(4200, 200, 20_000, true, 900, -1);
        check(ramp.window() < grown && ramp.window() >= grown / 2, "a queue beyond the budget twice in a row drains it, at most by half");
    }
}
