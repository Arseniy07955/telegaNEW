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
        check(WebProxyFlow.downlinkCreditTarget(WebProxyFlow.CLASS_DOWNLOAD, 1) == WebProxyFlow.DOWNLINK_DOWNLOAD_MAX, "one download is capped at the per-stream maximum");
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
}
