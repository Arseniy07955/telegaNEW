/*
 * This is the source code of Telegram for Android v. 5.x.x.
 * It is licensed under GNU GPL v. 2 or later.
 * You should have received a copy of the license in this archive (see LICENSE).
 */

package org.telegram.messenger;

import android.os.SystemClock;
import android.util.Base64;

import org.telegram.tgnet.ConnectionsManager;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;

public class ProxyCheckScheduler {

    private static final long PROXY_CHECK_STALE_MS = 2 * 60 * 1000L;
    private static final long PROXY_CHECK_SPACING_MS = 700L;
    private static final long PROXY_CHECK_FAILURE_BACKOFF_MS = 2 * 60 * 1000L;
    private static final long PROXY_CHECK_FAILURE_BACKOFF_MAX_MS = 8 * 60 * 1000L;
    private static final long PROXY_CHECK_LIVE_FAILURE_DEDUP_MS = 1500L;
    private static final long PROXY_CHECK_CONNECTED_GRACE_MS = 60 * 1000L;

    private static final ArrayList<Request> queue = new ArrayList<>();
    private static final HashMap<String, EndpointState> endpointStates = new HashMap<>();
    private static final Runnable startNextRunnable = ProxyCheckScheduler::startNext;
    private static Request activeRequest;

    public interface Callback {
        void onProxyChecked(SharedConfig.ProxyInfo proxyInfo, long time, String diagnostic);
        void onProxyCheckQueueFinished();
    }

    public static boolean enqueueNow(int currentAccount, SharedConfig.ProxyInfo proxyInfo, Object owner, Callback callback) {
        if (proxyInfo == null || owner == null) {
            log("enqueue_now_rejected endpoint=" + endpoint(proxyInfo));
            return false;
        }
        if (attachPending(proxyInfo, owner, callback, true)) {
            return true;
        }
        clearDetachedCheckState(proxyInfo, "enqueue_now");
        if (proxyInfo.checking) {
            log("enqueue_now_rejected endpoint=" + endpoint(proxyInfo));
            return false;
        }
        queue.add(0, new Request(currentAccount, proxyInfo, owner, callback, true));
        log("enqueue_now endpoint=" + endpoint(proxyInfo) + " queued=" + queue.size());
        AndroidUtilities.runOnUIThread(startNextRunnable);
        return true;
    }

    public static int enqueueStale(int currentAccount, List<SharedConfig.ProxyInfo> proxyList, Object owner, Callback callback) {
        if (proxyList == null || owner == null) {
            log("enqueue_rejected owner=" + owner);
            return 0;
        }
        int added = 0;
        for (int i = 0, count = proxyList.size(); i < count; i++) {
            SharedConfig.ProxyInfo proxyInfo = proxyList.get(i);
            if (attachPending(proxyInfo, owner, callback, false)) {
                added++;
                continue;
            }
            clearDetachedCheckState(proxyInfo, "enqueue");
            if (!shouldCheck(proxyInfo, false)) {
                continue;
            }
            queue.add(new Request(currentAccount, proxyInfo, owner, callback));
            added++;
            log("enqueue endpoint=" + endpoint(proxyInfo) + " queued=" + queue.size());
        }
        if (added > 0) {
            AndroidUtilities.runOnUIThread(startNextRunnable);
        }
        return added;
    }

    public static void clearDetachedCheckStates(List<SharedConfig.ProxyInfo> proxyList, String reason) {
        if (proxyList == null) {
            return;
        }
        for (int i = 0, count = proxyList.size(); i < count; i++) {
            clearDetachedCheckState(proxyList.get(i), reason);
        }
    }

    public static void cancelOwner(Object owner) {
        if (owner == null) {
            return;
        }
        for (int i = queue.size() - 1; i >= 0; i--) {
            Request request = queue.get(i);
            if (request.cancelOwner(owner)) {
                log("cancel_owner endpoint=" + endpoint(request.proxyInfo) + " queued=" + queue.size() + " listeners=" + request.activeListenerCount());
                if (!request.hasActiveListeners()) {
                    request.cancelled = true;
                    queue.remove(i);
                    log("cancel_owner request_removed endpoint=" + endpoint(request.proxyInfo) + " queued=" + queue.size());
                }
            }
        }
        if (activeRequest != null && activeRequest.cancelOwner(owner)) {
            Request request = activeRequest;
            if (!request.hasActiveListeners()) {
                request.cancelled = true;
                SharedConfig.ProxyInfo proxyInfo = request.proxyInfo;
                ConnectionsManager.getInstance(request.currentAccount).cancelProxyCheck(request.nativePingId);
                clearTransientState(proxyInfo);
                activeRequest = null;
                log("cancel_owner active endpoint=" + endpoint(proxyInfo));
                AndroidUtilities.runOnUIThread(startNextRunnable, PROXY_CHECK_SPACING_MS);
            } else {
                log("cancel_owner active_listener endpoint=" + endpoint(request.proxyInfo) + " listeners=" + request.activeListenerCount());
            }
        }
    }

    public static boolean hasOwnerPending(Object owner) {
        if (owner == null) {
            return false;
        }
        if (activeRequest != null && activeRequest.hasOwner(owner)) {
            return true;
        }
        for (int i = 0, count = queue.size(); i < count; i++) {
            Request request = queue.get(i);
            if (request.hasOwner(owner)) {
                return true;
            }
        }
        return false;
    }

    private static boolean shouldCheck(SharedConfig.ProxyInfo proxyInfo, boolean force) {
        if (proxyInfo == null || proxyInfo.checking) {
            return false;
        }
        if (force) {
            return true;
        }
        long now = SystemClock.elapsedRealtime();
        long nextAllowedTime = nextAllowedCheckTime(proxyInfo);
        if (nextAllowedTime > now) {
            markEndpointCooldown(proxyInfo, now);
            log("skip_backoff endpoint=" + endpoint(proxyInfo) + " wait_ms=" + (nextAllowedTime - now) + " phase=" + lastEndpointDiagnostic(proxyInfo));
            return false;
        }
        if (isFresh(proxyInfo)) {
            return false;
        }
        return true;
    }

    public static boolean isFresh(SharedConfig.ProxyInfo proxyInfo) {
        return proxyInfo != null
                && proxyInfo.availableCheckTime != 0
                && SystemClock.elapsedRealtime() - proxyInfo.availableCheckTime < PROXY_CHECK_STALE_MS;
    }

    public static boolean isEndpointBackedOff(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return false;
        }
        EndpointState state = endpointFailureState(proxyInfo);
        return state != null
                && state.consecutiveFailures > 0
                && nextAllowedCheckTime(proxyInfo) > SystemClock.elapsedRealtime();
    }

    public static void markConnected(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return;
        }
        boolean changed = proxyInfo.checking || !proxyInfo.available || !isFresh(proxyInfo);
        boolean preserveFreshProxyPhase = ProxyCheckDiagnostics.hasFreshFailure(proxyInfo) || ProxyCheckDiagnostics.hasFreshLivePhase(proxyInfo);
        if (!preserveFreshProxyPhase) {
            proxyInfo.available = true;
            proxyInfo.availableCheckTime = SystemClock.elapsedRealtime();
            proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.OK;
            proxyInfo.lastCheckDiagnosticTime = proxyInfo.availableCheckTime;
            rememberConnected(proxyInfo);
        }
        clearTransientState(proxyInfo);
        if (changed) {
            log("mark_connected endpoint=" + endpoint(proxyInfo));
        }
    }

    public static void markConnectionStarting(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return;
        }
        proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.CONNECT_START;
        proxyInfo.lastCheckDiagnosticTime = SystemClock.elapsedRealtime();
        log("mark_connection_starting endpoint=" + endpoint(proxyInfo));
    }

    public static void markConnectionUsable(SharedConfig.ProxyInfo proxyInfo, String diagnostic) {
        if (proxyInfo == null) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        proxyInfo.available = true;
        proxyInfo.availableCheckTime = now;
        proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.normalize(diagnostic);
        proxyInfo.lastCheckDiagnosticTime = now;
        rememberConnected(proxyInfo);
        clearTransientState(proxyInfo);
        log("mark_connection_usable endpoint=" + endpoint(proxyInfo) + " phase=" + proxyInfo.lastCheckDiagnostic);
    }

    public static void markEndpointFailure(SharedConfig.ProxyInfo proxyInfo, String diagnostic) {
        if (proxyInfo == null || !ProxyCheckDiagnostics.shouldAccelerateProxyRotation(diagnostic)) {
            return;
        }
        String normalizedDiagnostic = ProxyCheckDiagnostics.normalize(diagnostic);
        String key = endpointStateKeyForDiagnostic(proxyInfo, normalizedDiagnostic);
        if (key == null) {
            return;
        }
        EndpointState state = endpointStateForKey(key);
        long now = SystemClock.elapsedRealtime();
        if (normalizedDiagnostic.equals(state.lastDiagnostic) && now - state.lastCheckTime < PROXY_CHECK_LIVE_FAILURE_DEDUP_MS) {
            log("live_failure_dedup endpoint=" + endpoint(proxyInfo) + " phase=" + normalizedDiagnostic);
            return;
        }
        rememberEndpointFailure(state, proxyInfo, normalizedDiagnostic, now, "live_failure");
    }

    public static boolean matchesEndpointStageKey(SharedConfig.ProxyInfo proxyInfo, String endpointKey) {
        if (proxyInfo == null || endpointKey == null || endpointKey.length() == 0) {
            return false;
        }
        return endpointStageKeyForLiveStage(proxyInfo).equals(endpointKey);
    }

    public static String endpointStageKeyForLiveStage(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return "";
        }
        byte[] secret = decodedSecretForLiveStage(proxyInfo.secret);
        String kind = secretKindForLiveStage(secret);
        if ("none".equals(kind)) {
            return networkEndpointKeyForLiveStage(proxyInfo);
        }
        StringBuilder builder = new StringBuilder();
        builder.append(normalizeKeyPart(proxyInfo.address, false)).append(":").append(proxyInfo.port).append(":").append(kind);
        if ("ee".equals(kind)) {
            String domain = secretDomainForLiveStage(secret);
            if (domain.length() > 0) {
                builder.append(":").append(domain);
            }
        }
        return builder.toString();
    }

    public static String networkEndpointKeyForLiveStage(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return "";
        }
        return normalizeKeyPart(proxyInfo.address, true) + ":" + proxyInfo.port;
    }

    private static byte[] decodedSecretForLiveStage(String secret) {
        if (secret == null || secret.length() == 0) {
            return new byte[0];
        }
        boolean allHex = true;
        for (int i = 0, count = secret.length(); i < count; i++) {
            char c = secret.charAt(i);
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'))) {
                allHex = false;
                break;
            }
        }
        if (allHex) {
            int size = secret.length() / 2;
            byte[] result = new byte[size];
            for (int i = 0; i < size; i++) {
                int high = Character.digit(secret.charAt(i * 2), 16);
                int low = Character.digit(secret.charAt(i * 2 + 1), 16);
                result[i] = (byte) (high * 16 + low);
            }
            return result;
        }
        int remainder = secret.length() & 3;
        if (remainder == 1) {
            return new byte[0];
        }
        String padded = secret;
        if (remainder != 0) {
            padded += remainder == 2 ? "==" : "=";
        }
        try {
            return Base64.decode(padded, Base64.URL_SAFE | Base64.NO_WRAP);
        } catch (IllegalArgumentException e) {
            return new byte[0];
        }
    }

    private static String secretKindForLiveStage(byte[] secret) {
        if (secret == null || secret.length == 0) {
            return "none";
        }
        int first = secret[0] & 0xff;
        if (secret.length >= 17 && first == 0xdd) {
            return "dd";
        }
        if (secret.length > 17 && first == 0xee) {
            return "ee";
        }
        return "legacy";
    }

    private static String secretDomainForLiveStage(byte[] secret) {
        if (secret == null || secret.length <= 17 || (secret[0] & 0xff) != 0xee) {
            return "";
        }
        return new String(secret, 17, secret.length - 17, StandardCharsets.UTF_8);
    }

    private static boolean hasPending(SharedConfig.ProxyInfo proxyInfo) {
        return findPending(proxyInfo) != null;
    }

    private static boolean attachPending(SharedConfig.ProxyInfo proxyInfo, Object owner, Callback callback, boolean force) {
        Request request = findPending(proxyInfo);
        if (request == null) {
            return false;
        }
        request.force = request.force || force;
        if (request.addListener(proxyInfo, owner, callback)) {
            proxyInfo.checking = request.proxyInfo.checking;
            proxyInfo.proxyCheckPingId = request.proxyInfo.proxyCheckPingId;
            log("attach_pending endpoint=" + endpoint(proxyInfo) + " listeners=" + request.activeListenerCount() + " force=" + request.force);
        }
        return true;
    }

    private static Request findPending(SharedConfig.ProxyInfo proxyInfo) {
        String key = endpointKey(proxyInfo);
        if (key == null) {
            return null;
        }
        if (activeRequest != null && !activeRequest.cancelled && key.equals(endpointKey(activeRequest.proxyInfo))) {
            return activeRequest;
        }
        for (int i = 0, count = queue.size(); i < count; i++) {
            Request request = queue.get(i);
            if (!request.cancelled && key.equals(endpointKey(request.proxyInfo))) {
                return request;
            }
        }
        return null;
    }

    private static void startNext() {
        if (activeRequest != null) {
            return;
        }
        while (!queue.isEmpty()) {
            Request request = queue.remove(0);
            if (request.cancelled) {
                continue;
            }
            if (!shouldCheck(request.proxyInfo, request.force)) {
                log("skip_fresh endpoint=" + endpoint(request.proxyInfo) + " queued=" + queue.size());
                notifyRequestFinishedIfDrained(request);
                continue;
            }
            startRequest(request);
            return;
        }
    }

    private static void startRequest(Request request) {
        activeRequest = request;
        SharedConfig.ProxyInfo proxyInfo = request.proxyInfo;
        request.setChecking(true);
        log("start endpoint=" + endpoint(proxyInfo) + " queued=" + queue.size());
        proxyInfo.proxyCheckPingId = ConnectionsManager.getInstance(request.currentAccount).checkProxy(proxyInfo.address, proxyInfo.port, proxyInfo.username, proxyInfo.password, proxyInfo.secret, (time, diagnostic) -> AndroidUtilities.runOnUIThread(() -> finishRequest(request, time, diagnostic)));
        request.nativePingId = proxyInfo.proxyCheckPingId;
        request.setProxyCheckPingId(proxyInfo.proxyCheckPingId);
        if (proxyInfo.proxyCheckPingId == 0) {
            log("start_failed endpoint=" + endpoint(proxyInfo) + " reason=native_refused");
            finishRequest(request, -1, ProxyCheckDiagnostics.START_FAILED);
        }
    }

    private static void finishRequest(Request request, long time, String diagnostic) {
        if (activeRequest != request) {
            log("finish_ignored endpoint=" + endpoint(request.proxyInfo) + " time=" + time + " diagnostic=" + ProxyCheckDiagnostics.normalize(diagnostic) + " active=" + (activeRequest == null ? "null" : endpoint(activeRequest.proxyInfo)));
            return;
        }
        activeRequest = null;
        String normalizedDiagnostic = normalizedDiagnosticForResult(time, diagnostic);
        String displayDiagnostic = displayDiagnosticForResult(request, time, normalizedDiagnostic);
        long appliedTime = appliedTimeForResult(request, time);
        long callbackTime = callbackTimeForResult(request, time);
        log("finish result=" + (callbackTime == -1 ? "fail" : "ok") + " phase=" + normalizedDiagnostic + " diagnostic=" + displayDiagnostic + " time=" + callbackTime + " applied_time=" + appliedTime + " raw_time=" + time + " endpoint=" + endpoint(request.proxyInfo) + " queued=" + queue.size() + " cancelled=" + request.cancelled + " listeners=" + request.activeListenerCount());
        rememberCheckResult(request, callbackTime, displayDiagnostic);
        for (int i = 0, count = request.listeners.size(); i < count; i++) {
            Listener listener = request.listeners.get(i);
            if (listener.cancelled) {
                continue;
            }
            String appliedDiagnostic = appliedDiagnosticForResult(listener.proxyInfo, request, time, displayDiagnostic);
            applyMeasuredResult(listener.proxyInfo, appliedTime, appliedDiagnostic);
            NotificationCenter.getGlobalInstance().postNotificationName(NotificationCenter.proxyCheckDone, listener.proxyInfo);
            if (listener.callback != null) {
                listener.callback.onProxyChecked(listener.proxyInfo, callbackTime, displayDiagnostic);
            }
        }
        notifyRequestFinishedIfDrained(request);
        AndroidUtilities.runOnUIThread(startNextRunnable, PROXY_CHECK_SPACING_MS);
    }

    private static String normalizedDiagnosticForResult(long time, String diagnostic) {
        if (time >= 0) {
            return ProxyCheckDiagnostics.OK;
        }
        return ProxyCheckDiagnostics.normalize(diagnostic);
    }

    private static String displayDiagnosticForResult(Request request, long time, String normalizedDiagnostic) {
        if (time != -1 || !ProxyCheckDiagnostics.TCP_NOT_CONNECTED.equals(normalizedDiagnostic)) {
            return normalizedDiagnostic;
        }
        String previousDiagnostic = lastEndpointDiagnostic(request.proxyInfo);
        if (ProxyCheckDiagnostics.TCP_NOT_CONNECTED.equals(previousDiagnostic) || ProxyCheckDiagnostics.NETWORK_BLOCK_SUSPECTED.equals(previousDiagnostic)) {
            return ProxyCheckDiagnostics.NETWORK_BLOCK_SUSPECTED;
        }
        return normalizedDiagnostic;
    }

    private static long appliedTimeForResult(Request request, long time) {
        if (shouldPreserveConnectedState(request, time)) {
            log("finish_keep_connected endpoint=" + endpoint(request.proxyInfo));
            return 0;
        }
        return time;
    }

    private static String appliedDiagnosticForResult(SharedConfig.ProxyInfo proxyInfo, Request request, long time, String displayDiagnostic) {
        if (!shouldPreserveConnectedState(request, time)) {
            return displayDiagnostic;
        }
        if (hasFreshConcreteProxyPhase(proxyInfo)) {
            return ProxyCheckDiagnostics.normalize(proxyInfo.lastCheckDiagnostic);
        }
        return ProxyCheckDiagnostics.OK;
    }

    private static boolean hasFreshConcreteProxyPhase(SharedConfig.ProxyInfo proxyInfo) {
        return ProxyCheckDiagnostics.hasFreshFailure(proxyInfo) || ProxyCheckDiagnostics.hasFreshLivePhase(proxyInfo) || ProxyCheckDiagnostics.hasFreshEndpointCooldown(proxyInfo);
    }

    private static void markCheckingIfNoFreshConcretePhase(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null || hasFreshConcreteProxyPhase(proxyInfo)) {
            return;
        }
        proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.CHECKING;
        proxyInfo.lastCheckDiagnosticTime = SystemClock.elapsedRealtime();
    }

    private static long callbackTimeForResult(Request request, long time) {
        if (shouldPreserveConnectedState(request, time)) {
            return -1;
        }
        return time;
    }

    private static boolean shouldPreserveConnectedState(Request request, long time) {
        return time == -1 && isConnectedCurrentProxy(request.currentAccount, request.proxyInfo);
    }

    private static boolean isConnectedCurrentProxy(int currentAccount, SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null || proxyInfo != SharedConfig.currentProxy) {
            return false;
        }
        int state = ConnectionsManager.getInstance(currentAccount).getConnectionState();
        return state == ConnectionsManager.ConnectionStateConnected || state == ConnectionsManager.ConnectionStateUpdating;
    }

    private static long nextAllowedCheckTime(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return 0;
        }
        EndpointState exactState = endpointStates.get(endpointKey(proxyInfo));
        EndpointState networkState = endpointStates.get(endpointNetworkKey(proxyInfo));
        long exactTime = exactState == null ? 0 : exactState.nextCheckTime;
        long networkTime = networkState == null ? 0 : networkState.nextCheckTime;
        return Math.max(exactTime, networkTime);
    }

    private static String lastEndpointDiagnostic(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return ProxyCheckDiagnostics.UNKNOWN_FAIL;
        }
        EndpointState state = latestEndpointState(proxyInfo);
        if (state == null) {
            return ProxyCheckDiagnostics.normalize(proxyInfo.lastCheckDiagnostic);
        }
        return state.lastDiagnostic;
    }

    private static void rememberConnected(SharedConfig.ProxyInfo proxyInfo) {
        String key = endpointKey(proxyInfo);
        if (key == null) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        rememberEndpointConnected(endpointStateForKey(key), now);
        String networkKey = endpointNetworkKey(proxyInfo);
        if (networkKey != null && !networkKey.equals(key)) {
            rememberEndpointConnected(endpointStateForKey(networkKey), now);
        }
    }

    private static void rememberCheckResult(Request request, long time, String diagnostic) {
        String normalizedDiagnostic = ProxyCheckDiagnostics.normalize(diagnostic);
        String key = time == -1 && !isConnectedCurrentProxy(request.currentAccount, request.proxyInfo)
                ? endpointStateKeyForDiagnostic(request.proxyInfo, normalizedDiagnostic)
                : endpointKey(request.proxyInfo);
        if (key == null) {
            return;
        }
        EndpointState state = endpointStateForKey(key);
        long now = SystemClock.elapsedRealtime();
        state.lastDiagnostic = normalizedDiagnostic;
        state.lastCheckTime = now;
        if (time != -1 || shouldRememberConnectedCheckResult(request, time)) {
            rememberEndpointConnected(state, now);
            String networkKey = endpointNetworkKey(request.proxyInfo);
            if (networkKey != null && !networkKey.equals(key)) {
                rememberEndpointConnected(endpointStateForKey(networkKey), now);
            }
            return;
        }
        if (shouldPreserveConnectedState(request, time)) {
            log("finish_keep_proxy_phase endpoint=" + endpoint(request.proxyInfo));
            return;
        }

        rememberEndpointFailure(state, request.proxyInfo, state.lastDiagnostic, now, "proxy_check");
    }

    private static boolean shouldRememberConnectedCheckResult(Request request, long time) {
        return shouldPreserveConnectedState(request, time) && !hasFreshConcreteProxyPhase(request.proxyInfo);
    }

    private static void rememberEndpointConnected(EndpointState state, long now) {
        state.consecutiveFailures = 0;
        state.lastDiagnostic = ProxyCheckDiagnostics.OK;
        state.lastCheckTime = now;
        state.nextCheckTime = now + PROXY_CHECK_CONNECTED_GRACE_MS;
    }

    private static void rememberEndpointFailure(EndpointState state, SharedConfig.ProxyInfo proxyInfo, String diagnostic, long now, String source) {
        state.lastDiagnostic = ProxyCheckDiagnostics.normalize(diagnostic);
        state.lastCheckTime = now;
        state.consecutiveFailures++;
        long multiplier = 1L << Math.min(2, Math.max(0, state.consecutiveFailures - 1));
        long backoff = Math.min(PROXY_CHECK_FAILURE_BACKOFF_MAX_MS, PROXY_CHECK_FAILURE_BACKOFF_MS * multiplier);
        state.nextCheckTime = now + backoff;
        log("backoff endpoint=" + endpoint(proxyInfo) + " wait_ms=" + backoff + " failures=" + state.consecutiveFailures + " phase=" + state.lastDiagnostic + " source=" + source);
    }

    private static void markEndpointCooldown(SharedConfig.ProxyInfo proxyInfo, long now) {
        if (hasFreshConcreteProxyPhase(proxyInfo)) {
            return;
        }
        proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.ENDPOINT_COOLDOWN;
        proxyInfo.lastCheckDiagnosticTime = now;
    }

    private static EndpointState endpointStateForKey(String key) {
        EndpointState state = endpointStates.get(key);
        if (state == null) {
            state = new EndpointState();
            endpointStates.put(key, state);
        }
        return state;
    }

    private static void notifyRequestFinishedIfDrained(Request request) {
        ArrayList<Object> notifiedOwners = new ArrayList<>();
        for (int i = 0, count = request.listeners.size(); i < count; i++) {
            notifyListenerFinishedIfDrained(request.listeners.get(i), notifiedOwners);
        }
    }

    private static void notifyListenerFinishedIfDrained(Listener listener, ArrayList<Object> notifiedOwners) {
        if (listener.cancelled || listener.callback == null || hasOwnerPending(listener.owner) || alreadyNotifiedOwner(listener.owner, notifiedOwners)) {
            return;
        }
        if (listener.owner != null) {
            notifiedOwners.add(listener.owner);
        }
        log("drained owner=" + listener.owner);
        listener.callback.onProxyCheckQueueFinished();
    }

    private static boolean alreadyNotifiedOwner(Object owner, ArrayList<Object> notifiedOwners) {
        return owner != null && notifiedOwners.contains(owner);
    }

    private static void clearDetachedCheckState(SharedConfig.ProxyInfo proxyInfo, String reason) {
        if (proxyInfo != null && proxyInfo.checking && !hasPending(proxyInfo)) {
            clearTransientState(proxyInfo);
            log("clear_detached endpoint=" + endpoint(proxyInfo) + " reason=" + reason);
        }
    }

    private static void clearTransientState(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return;
        }
        proxyInfo.checking = false;
        proxyInfo.proxyCheckPingId = 0;
    }

    private static void applyMeasuredResult(SharedConfig.ProxyInfo proxyInfo, long time, String diagnostic) {
        proxyInfo.availableCheckTime = SystemClock.elapsedRealtime();
        proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.normalize(diagnostic);
        proxyInfo.lastCheckDiagnosticTime = proxyInfo.availableCheckTime;
        clearTransientState(proxyInfo);
        if (time == -1) {
            proxyInfo.available = false;
            proxyInfo.ping = 0;
        } else {
            proxyInfo.ping = time;
            proxyInfo.available = true;
        }
        proxyInfo.proxyCheckPingId = 0;
    }

    private static void log(String message) {
        if (BuildVars.LOGS_ENABLED) {
            FileLog.d("proxy_check_scheduler " + message);
        }
    }

    private static String endpoint(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return "null";
        }
        return proxyInfo.address + ":" + proxyInfo.port;
    }

    private static String endpointKey(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return null;
        }
        StringBuilder builder = new StringBuilder();
        appendKeyPart(builder, normalizeKeyPart(proxyInfo.address, true));
        appendKeyPart(builder, String.valueOf(proxyInfo.port));
        appendKeyPart(builder, normalizeKeyPart(proxyInfo.username, false));
        appendKeyPart(builder, normalizeKeyPart(proxyInfo.password, false));
        appendKeyPart(builder, normalizeKeyPart(proxyInfo.secret, false));
        return builder.toString();
    }

    private static String endpointNetworkKey(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return null;
        }
        StringBuilder builder = new StringBuilder();
        appendKeyPart(builder, normalizeKeyPart(proxyInfo.address, true));
        appendKeyPart(builder, String.valueOf(proxyInfo.port));
        return builder.toString();
    }

    private static String endpointStateKeyForDiagnostic(SharedConfig.ProxyInfo proxyInfo, String diagnostic) {
        switch (ProxyCheckDiagnostics.normalize(diagnostic)) {
            case ProxyCheckDiagnostics.HOST_RESOLVE_FAILED:
            case ProxyCheckDiagnostics.TCP_NOT_CONNECTED:
            case ProxyCheckDiagnostics.NETWORK_BLOCK_SUSPECTED:
            case ProxyCheckDiagnostics.TCP_CONNECTED_NO_PONG:
            case ProxyCheckDiagnostics.MTPROXY_PACKET_SENT_NO_RESPONSE:
            case ProxyCheckDiagnostics.DROPPED_EARLY_AFTER_APPDATA:
                return endpointNetworkKey(proxyInfo);
            default:
                return endpointKey(proxyInfo);
        }
    }

    private static EndpointState latestEndpointState(SharedConfig.ProxyInfo proxyInfo) {
        EndpointState exactState = endpointStates.get(endpointKey(proxyInfo));
        EndpointState networkState = endpointStates.get(endpointNetworkKey(proxyInfo));
        if (exactState == null) {
            return networkState;
        }
        if (networkState == null) {
            return exactState;
        }
        return networkState.lastCheckTime > exactState.lastCheckTime ? networkState : exactState;
    }

    private static EndpointState endpointFailureState(SharedConfig.ProxyInfo proxyInfo) {
        EndpointState exactState = endpointStates.get(endpointKey(proxyInfo));
        EndpointState networkState = endpointStates.get(endpointNetworkKey(proxyInfo));
        boolean exactBackoff = exactState != null && exactState.consecutiveFailures > 0;
        boolean networkBackoff = networkState != null && networkState.consecutiveFailures > 0;
        if (!exactBackoff) {
            return networkBackoff ? networkState : null;
        }
        if (!networkBackoff) {
            return exactState;
        }
        return networkState.nextCheckTime > exactState.nextCheckTime ? networkState : exactState;
    }

    private static String normalizeKeyPart(String value, boolean lowerCase) {
        if (value == null) {
            return "";
        }
        return lowerCase ? value.toLowerCase(Locale.US) : value;
    }

    private static void appendKeyPart(StringBuilder builder, String value) {
        builder.append(value.length()).append(':').append(value);
    }

    private static class Request {
        final int currentAccount;
        final SharedConfig.ProxyInfo proxyInfo;
        final ArrayList<Listener> listeners = new ArrayList<>();
        long nativePingId;
        boolean force;
        boolean cancelled;

        Request(int currentAccount, SharedConfig.ProxyInfo proxyInfo, Object owner, Callback callback) {
            this(currentAccount, proxyInfo, owner, callback, false);
        }

        Request(int currentAccount, SharedConfig.ProxyInfo proxyInfo, Object owner, Callback callback, boolean force) {
            this.currentAccount = currentAccount;
            this.proxyInfo = proxyInfo;
            this.force = force;
            addListener(proxyInfo, owner, callback);
        }

        boolean addListener(SharedConfig.ProxyInfo proxyInfo, Object owner, Callback callback) {
            for (int i = 0, count = listeners.size(); i < count; i++) {
                Listener listener = listeners.get(i);
                if (!listener.cancelled && listener.owner == owner && listener.proxyInfo == proxyInfo) {
                    return false;
                }
            }
            listeners.add(new Listener(proxyInfo, owner, callback));
            return true;
        }

        boolean cancelOwner(Object owner) {
            boolean changed = false;
            for (int i = 0, count = listeners.size(); i < count; i++) {
                Listener listener = listeners.get(i);
                if (!listener.cancelled && listener.owner == owner) {
                    listener.cancelled = true;
                    listener.callback = null;
                    changed = true;
                }
            }
            if (changed) {
                clearCancelledListenerState();
            }
            return changed;
        }

        void clearCancelledListenerState() {
            for (int i = 0, count = listeners.size(); i < count; i++) {
                Listener listener = listeners.get(i);
                if (listener.cancelled && !hasActiveListenerForProxyInfo(listener.proxyInfo)) {
                    listener.proxyInfo.checking = false;
                    listener.proxyInfo.proxyCheckPingId = 0;
                }
            }
        }

        boolean hasActiveListenerForProxyInfo(SharedConfig.ProxyInfo proxyInfo) {
            for (int i = 0, count = listeners.size(); i < count; i++) {
                Listener listener = listeners.get(i);
                if (!listener.cancelled && listener.proxyInfo == proxyInfo) {
                    return true;
                }
            }
            return false;
        }

        boolean hasOwner(Object owner) {
            for (int i = 0, count = listeners.size(); i < count; i++) {
                Listener listener = listeners.get(i);
                if (!listener.cancelled && listener.owner == owner) {
                    return true;
                }
            }
            return false;
        }

        boolean hasActiveListeners() {
            return activeListenerCount() > 0;
        }

        int activeListenerCount() {
            int count = 0;
            for (int i = 0, listenersCount = listeners.size(); i < listenersCount; i++) {
                if (!listeners.get(i).cancelled) {
                    count++;
                }
            }
            return count;
        }

        void setChecking(boolean checking) {
            proxyInfo.checking = checking;
            if (checking) {
                markCheckingIfNoFreshConcretePhase(proxyInfo);
            }
            for (int i = 0, count = listeners.size(); i < count; i++) {
                Listener listener = listeners.get(i);
                if (!listener.cancelled) {
                    listener.proxyInfo.checking = checking;
                    if (checking) {
                        markCheckingIfNoFreshConcretePhase(listener.proxyInfo);
                    }
                }
            }
        }

        void setProxyCheckPingId(long pingId) {
            proxyInfo.proxyCheckPingId = pingId;
            for (int i = 0, count = listeners.size(); i < count; i++) {
                Listener listener = listeners.get(i);
                if (!listener.cancelled) {
                    listener.proxyInfo.proxyCheckPingId = pingId;
                }
            }
        }
    }

    private static class EndpointState {
        long nextCheckTime;
        long lastCheckTime;
        int consecutiveFailures;
        String lastDiagnostic = ProxyCheckDiagnostics.UNKNOWN_FAIL;
    }

    private static class Listener {
        final SharedConfig.ProxyInfo proxyInfo;
        final Object owner;
        Callback callback;
        boolean cancelled;

        Listener(SharedConfig.ProxyInfo proxyInfo, Object owner, Callback callback) {
            this.proxyInfo = proxyInfo;
            this.owner = owner;
            this.callback = callback;
        }
    }
}
