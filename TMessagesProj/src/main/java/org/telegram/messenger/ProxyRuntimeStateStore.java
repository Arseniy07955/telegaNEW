package org.telegram.messenger;

import android.os.SystemClock;

import org.telegram.tgnet.ConnectionsManager;

import java.util.HashMap;

public final class ProxyRuntimeStateStore {

    private static final long PROXY_CHECK_STALE_MS = 2 * 60 * 1000L;
    private static final long PROXY_CHECK_FAILURE_BACKOFF_MS = 2 * 60 * 1000L;
    private static final long PROXY_CHECK_FAILURE_BACKOFF_MAX_MS = 8 * 60 * 1000L;
    private static final long PROXY_CHECK_LIVE_FAILURE_DEDUP_MS = 1500L;
    private static final long PROXY_CHECK_CONNECTED_GRACE_MS = 60 * 1000L;
    private static final long USABLE_SUCCESS_HOLD_MS = 45 * 1000L;

    private static final HashMap<String, EndpointState> endpointStates = new HashMap<>();

    private ProxyRuntimeStateStore() {
    }

    public static Decision onNativeStage(ProxyConnectionEvent event) {
        if (event == null) {
            return Decision.ignored("ignored_empty_event", ProxyCheckDiagnostics.UNKNOWN_FAIL, "");
        }
        SharedConfig.ProxyInfo currentProxy = SharedConfig.currentProxy;
        boolean concretePhase = ProxyPhasePolicy.isLivePhase(event.phase)
                || (ProxyPhasePolicy.isFailure(event.phase) && !ProxyCheckDiagnostics.UNKNOWN_FAIL.equals(event.phase));
        boolean selectedAccountStage = event.account == UserConfig.selectedAccount;
        boolean stageTargetsCurrentProxy = currentProxy != null && concretePhase && ProxyEndpointKey.matchesLiveStage(currentProxy, event.endpointKey);
        if (!stageTargetsCurrentProxy) {
            if (selectedAccountStage && currentProxy != null && concretePhase) {
                logControl("decision=ignored_stale_endpoint source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " current=" + ProxyEndpointKey.liveStage(currentProxy));
            }
            return Decision.ignored("ignored_stale_endpoint", event.phase, event.endpointKey);
        }
        if (ProxyPhasePolicy.isProxyUsableSuccessPhase(event.phase)) {
            markConnectionUsable(currentProxy, event.phase, event.timestamp);
            logControl("decision=visible_usable_success source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey);
            return new Decision("visible_usable_success", event.phase, event.endpointKey, false, true, false);
        }
        if (ProxyPhasePolicy.canBackoff(event.phase) && hasFreshUsableSuccess(currentProxy, event.timestamp)) {
            String heldBy = ProxyCheckDiagnostics.normalize(currentProxy.lastCheckDiagnostic);
            logControl("decision=held_by_usable_success source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " held_by=" + heldBy);
            return new Decision("held_by_usable_success", event.phase, event.endpointKey, false, false, true);
        }

        boolean visibleChanged = false;
        if (selectedAccountStage && ProxyPhasePolicy.canOverwriteVisible(event.phase)) {
            if (ProxyCheckDiagnostics.shouldKeepFreshFailure(currentProxy, event.phase)) {
                logControl("decision=held_by_fresh_failure source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " held_by=" + currentProxy.lastCheckDiagnostic);
            } else {
                mirrorVisiblePhase(currentProxy, event.phase, event.timestamp);
                visibleChanged = true;
            }
        }

        if (!ProxyPhasePolicy.canBackoff(event.phase)) {
            logControl("decision=visible_only source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey);
            return new Decision("visible_only", event.phase, event.endpointKey, false, visibleChanged, false);
        }

        rememberLiveFailure(currentProxy, event.phase, event.timestamp);
        if (ProxyPhasePolicy.canRotate(event.phase)) {
            logControl("decision=rotation_trigger source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey);
            return new Decision("rotation_trigger", event.phase, event.endpointKey, true, visibleChanged, false);
        }
        return new Decision("backoff", event.phase, event.endpointKey, false, visibleChanged, false);
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

    public static long nextAllowedCheckTime(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return 0;
        }
        EndpointState exactState = endpointStates.get(ProxyEndpointKey.exact(proxyInfo));
        EndpointState networkState = endpointStates.get(ProxyEndpointKey.network(proxyInfo));
        long exactTime = exactState == null ? 0 : exactState.nextCheckTime;
        long networkTime = networkState == null ? 0 : networkState.nextCheckTime;
        return Math.max(exactTime, networkTime);
    }

    public static String lastEndpointDiagnostic(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return ProxyCheckDiagnostics.UNKNOWN_FAIL;
        }
        EndpointState state = latestEndpointState(proxyInfo);
        if (state == null) {
            return ProxyCheckDiagnostics.normalize(proxyInfo.lastCheckDiagnostic);
        }
        return state.lastDiagnostic;
    }

    public static void markConnected(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        boolean changed = proxyInfo.checking || !proxyInfo.available || !isFresh(proxyInfo);
        boolean preserveFreshProxyPhase = ProxyCheckDiagnostics.hasFreshFailure(proxyInfo) || hasFreshUsableSuccess(proxyInfo, now);
        if (!preserveFreshProxyPhase) {
            proxyInfo.available = true;
            proxyInfo.availableCheckTime = now;
            proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.OK;
            proxyInfo.lastCheckDiagnosticTime = proxyInfo.availableCheckTime;
            rememberConnected(proxyInfo, now);
        }
        clearTransientState(proxyInfo);
        if (changed) {
            logControl("decision=generic_connected endpoint=" + ProxyEndpointKey.endpoint(proxyInfo) + " preserve=" + preserveFreshProxyPhase);
        }
    }

    public static void markConnectionStarting(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        clearUsableSuccessHold(proxyInfo);
        proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.CONNECT_START;
        proxyInfo.lastCheckDiagnosticTime = now;
        logControl("decision=visible_only source=" + ProxyConnectionEvent.SOURCE_CONNECT_START + " phase=" + ProxyCheckDiagnostics.CONNECT_START + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo));
    }

    public static void markConnectionUsable(SharedConfig.ProxyInfo proxyInfo, String diagnostic) {
        markConnectionUsable(proxyInfo, diagnostic, SystemClock.elapsedRealtime());
    }

    public static void markConnectionUsable(SharedConfig.ProxyInfo proxyInfo, String diagnostic, long now) {
        if (proxyInfo == null) {
            return;
        }
        String normalized = ProxyCheckDiagnostics.normalize(diagnostic);
        proxyInfo.available = true;
        proxyInfo.availableCheckTime = now;
        proxyInfo.lastCheckDiagnostic = normalized;
        proxyInfo.lastCheckDiagnosticTime = now;
        clearEndpointBackoff(proxyInfo, normalized, now);
        clearTransientState(proxyInfo);
    }

    public static void markEndpointFailure(SharedConfig.ProxyInfo proxyInfo, String diagnostic) {
        if (proxyInfo == null || !ProxyPhasePolicy.canBackoff(diagnostic)) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        String normalized = ProxyCheckDiagnostics.normalize(diagnostic);
        if (hasFreshUsableSuccess(proxyInfo, now)) {
            logControl("decision=held_by_usable_success source=live_failure phase=" + normalized + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo) + " held_by=" + proxyInfo.lastCheckDiagnostic);
            return;
        }
        rememberLiveFailure(proxyInfo, normalized, now);
        if (ProxyPhasePolicy.canRotate(normalized)) {
            logControl("decision=rotation_trigger source=live_failure phase=" + normalized + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo));
        }
    }

    public static void markEndpointCooldown(SharedConfig.ProxyInfo proxyInfo, long now) {
        if (hasFreshConcreteProxyPhase(proxyInfo)) {
            return;
        }
        proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.ENDPOINT_COOLDOWN;
        proxyInfo.lastCheckDiagnosticTime = now;
    }

    public static void markCheckingIfNoFreshConcretePhase(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null || hasFreshConcreteProxyPhase(proxyInfo)) {
            return;
        }
        proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.CHECKING;
        proxyInfo.lastCheckDiagnosticTime = SystemClock.elapsedRealtime();
    }

    public static void copyTransientState(SharedConfig.ProxyInfo target, SharedConfig.ProxyInfo source) {
        if (target == null || source == null) {
            return;
        }
        setChecking(target, source.checking);
        setProxyCheckPingId(target, source.proxyCheckPingId);
    }

    public static void setChecking(SharedConfig.ProxyInfo proxyInfo, boolean checking) {
        if (proxyInfo == null) {
            return;
        }
        proxyInfo.checking = checking;
        if (checking) {
            markCheckingIfNoFreshConcretePhase(proxyInfo);
        }
    }

    public static void setProxyCheckPingId(SharedConfig.ProxyInfo proxyInfo, long pingId) {
        if (proxyInfo == null) {
            return;
        }
        proxyInfo.proxyCheckPingId = pingId;
    }

    public static void clearTransientState(SharedConfig.ProxyInfo proxyInfo) {
        setChecking(proxyInfo, false);
        setProxyCheckPingId(proxyInfo, 0);
    }

    public static void applyMeasuredProxyCheckResult(SharedConfig.ProxyInfo proxyInfo, long time, String diagnostic) {
        if (proxyInfo == null) {
            return;
        }
        proxyInfo.availableCheckTime = SystemClock.elapsedRealtime();
        mirrorVisiblePhase(proxyInfo, diagnostic, proxyInfo.availableCheckTime);
        clearTransientState(proxyInfo);
        if (time == -1) {
            proxyInfo.available = false;
            proxyInfo.ping = 0;
        } else {
            proxyInfo.ping = time;
            proxyInfo.available = true;
        }
    }

    public static String displayDiagnosticForProxyCheck(SharedConfig.ProxyInfo proxyInfo, long time, String normalizedDiagnostic) {
        if (time != -1 || !ProxyCheckDiagnostics.TCP_NOT_CONNECTED.equals(normalizedDiagnostic)) {
            return normalizedDiagnostic;
        }
        String previousDiagnostic = lastEndpointDiagnostic(proxyInfo);
        if (ProxyCheckDiagnostics.TCP_NOT_CONNECTED.equals(previousDiagnostic) || ProxyCheckDiagnostics.NETWORK_BLOCK_SUSPECTED.equals(previousDiagnostic)) {
            return ProxyCheckDiagnostics.NETWORK_BLOCK_SUSPECTED;
        }
        return normalizedDiagnostic;
    }

    public static long appliedTimeForProxyCheck(int account, SharedConfig.ProxyInfo proxyInfo, long time) {
        if (shouldPreserveProxyCheckFailure(account, proxyInfo, time)) {
            logControl("decision=proxy_check_shadowed endpoint=" + ProxyEndpointKey.endpoint(proxyInfo));
            return 0;
        }
        return time;
    }

    public static long callbackTimeForProxyCheck(int account, SharedConfig.ProxyInfo proxyInfo, long time) {
        if (shouldPreserveProxyCheckFailure(account, proxyInfo, time)) {
            return -1;
        }
        return time;
    }

    public static String appliedDiagnosticForProxyCheck(int account, SharedConfig.ProxyInfo proxyInfo, long time, String displayDiagnostic) {
        if (!shouldPreserveProxyCheckFailure(account, proxyInfo, time)) {
            return displayDiagnostic;
        }
        if (hasFreshConcreteProxyPhase(proxyInfo)) {
            return ProxyCheckDiagnostics.normalize(proxyInfo.lastCheckDiagnostic);
        }
        return ProxyCheckDiagnostics.OK;
    }

    public static void rememberProxyCheckResult(int account, SharedConfig.ProxyInfo proxyInfo, long time, String displayDiagnostic) {
        String normalizedDiagnostic = ProxyCheckDiagnostics.normalize(displayDiagnostic);
        long now = SystemClock.elapsedRealtime();
        if (time != -1) {
            rememberConnected(proxyInfo, now);
            return;
        }
        if (shouldPreserveProxyCheckFailure(account, proxyInfo, time)) {
            logControl("decision=proxy_check_shadowed endpoint=" + ProxyEndpointKey.endpoint(proxyInfo) + " phase=" + normalizedDiagnostic);
            return;
        }
        if (hasFreshUsableSuccess(proxyInfo, now)) {
            logControl("decision=held_by_usable_success source=" + ProxyConnectionEvent.SOURCE_PROXY_CHECK + " phase=" + normalizedDiagnostic + " endpoint=" + ProxyEndpointKey.endpoint(proxyInfo) + " held_by=" + proxyInfo.lastCheckDiagnostic);
            return;
        }
        String key = ProxyEndpointKey.forPhase(proxyInfo, normalizedDiagnostic);
        if (key == null) {
            key = ProxyEndpointKey.exact(proxyInfo);
        }
        if (key == null) {
            return;
        }
        rememberEndpointFailure(endpointStateForKey(key), proxyInfo, normalizedDiagnostic, now, ProxyConnectionEvent.SOURCE_PROXY_CHECK);
    }

    public static boolean isSwitchableCandidate(SharedConfig.ProxyInfo info) {
        return info != null
                && info != SharedConfig.currentProxy
                && !info.checking
                && !ProxyCheckDiagnostics.hasFreshFailure(info)
                && !ProxyCheckDiagnostics.hasFreshEndpointCooldown(info)
                && !ProxyCheckDiagnostics.hasFreshUnresolvedLivePhase(info)
                && !isEndpointBackedOff(info);
    }

    public static boolean shouldScheduleFallback(int account, String diagnostic, String endpointKey) {
        SharedConfig.ProxyInfo currentProxy = SharedConfig.currentProxy;
        String normalized = ProxyCheckDiagnostics.normalize(diagnostic);
        boolean result = account == UserConfig.selectedAccount
                && currentProxy != null
                && ProxyEndpointKey.matchesLiveStage(currentProxy, endpointKey)
                && ProxyPhasePolicy.canRotate(normalized)
                && !hasFreshUsableSuccess(currentProxy, SystemClock.elapsedRealtime());
        if (!result) {
            logControl("decision=fallback_not_scheduled phase=" + normalized + " endpoint=" + endpointKey);
        }
        return result;
    }

    public static boolean hasFreshConcreteProxyPhase(SharedConfig.ProxyInfo proxyInfo) {
        return ProxyCheckDiagnostics.hasFreshFailure(proxyInfo)
                || ProxyCheckDiagnostics.hasFreshLivePhase(proxyInfo)
                || ProxyCheckDiagnostics.hasFreshEndpointCooldown(proxyInfo);
    }

    private static boolean shouldPreserveProxyCheckFailure(int account, SharedConfig.ProxyInfo proxyInfo, long time) {
        if (time != -1 || proxyInfo == null || !targetsCurrentProxyEndpoint(proxyInfo)) {
            return false;
        }
        return isConnectedCurrentProxy(account, proxyInfo) || hasFreshConcreteProxyPhase(proxyInfo);
    }

    private static boolean isConnectedCurrentProxy(int account, SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null || !targetsCurrentProxyEndpoint(proxyInfo)) {
            return false;
        }
        int state = ConnectionsManager.getInstance(account).getConnectionState();
        return state == ConnectionsManager.ConnectionStateConnected || state == ConnectionsManager.ConnectionStateUpdating;
    }

    private static boolean targetsCurrentProxyEndpoint(SharedConfig.ProxyInfo proxyInfo) {
        SharedConfig.ProxyInfo currentProxy = SharedConfig.currentProxy;
        String key = ProxyEndpointKey.exact(proxyInfo);
        return currentProxy != null && key != null && key.equals(ProxyEndpointKey.exact(currentProxy));
    }

    private static boolean hasFreshUsableSuccess(SharedConfig.ProxyInfo proxyInfo, long now) {
        if (proxyInfo == null) {
            return false;
        }
        if (proxyInfo.lastCheckDiagnosticTime != 0
                && now - proxyInfo.lastCheckDiagnosticTime < USABLE_SUCCESS_HOLD_MS
                && ProxyPhasePolicy.isProxyUsableSuccessPhase(proxyInfo.lastCheckDiagnostic)) {
            return true;
        }
        EndpointState exactState = endpointStates.get(ProxyEndpointKey.exact(proxyInfo));
        EndpointState networkState = endpointStates.get(ProxyEndpointKey.network(proxyInfo));
        return (exactState != null && exactState.usableSuccessUntil > now)
                || (networkState != null && networkState.usableSuccessUntil > now);
    }

    private static void mirrorVisiblePhase(SharedConfig.ProxyInfo proxyInfo, String phase, long now) {
        proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.normalize(phase);
        proxyInfo.lastCheckDiagnosticTime = now;
    }

    private static void rememberLiveFailure(SharedConfig.ProxyInfo proxyInfo, String diagnostic, long now) {
        String normalized = ProxyCheckDiagnostics.normalize(diagnostic);
        String key = ProxyEndpointKey.forPhase(proxyInfo, normalized);
        if (key == null) {
            return;
        }
        EndpointState state = endpointStateForKey(key);
        if (normalized.equals(state.lastDiagnostic) && now - state.lastCheckTime < PROXY_CHECK_LIVE_FAILURE_DEDUP_MS) {
            logControl("decision=live_failure_dedup endpoint=" + ProxyEndpointKey.endpoint(proxyInfo) + " phase=" + normalized);
            return;
        }
        rememberEndpointFailure(state, proxyInfo, normalized, now, "live_failure");
    }

    private static void rememberConnected(SharedConfig.ProxyInfo proxyInfo, long now) {
        String key = ProxyEndpointKey.exact(proxyInfo);
        if (key == null) {
            return;
        }
        rememberEndpointConnected(endpointStateForKey(key), ProxyCheckDiagnostics.OK, now, false);
        String networkKey = ProxyEndpointKey.network(proxyInfo);
        if (networkKey != null && !networkKey.equals(key)) {
            rememberEndpointConnected(endpointStateForKey(networkKey), ProxyCheckDiagnostics.OK, now, false);
        }
    }

    private static void clearEndpointBackoff(SharedConfig.ProxyInfo proxyInfo, String phase, long now) {
        String exactKey = ProxyEndpointKey.exact(proxyInfo);
        if (exactKey == null) {
            return;
        }
        rememberEndpointConnected(endpointStateForKey(exactKey), phase, now, true);
        String networkKey = ProxyEndpointKey.network(proxyInfo);
        if (networkKey != null && !networkKey.equals(exactKey)) {
            rememberEndpointConnected(endpointStateForKey(networkKey), phase, now, true);
        }
        logControl("decision=clear_backoff phase=" + phase + " endpoint=" + ProxyEndpointKey.endpoint(proxyInfo));
    }

    private static void clearUsableSuccessHold(SharedConfig.ProxyInfo proxyInfo) {
        clearUsableSuccessHold(ProxyEndpointKey.exact(proxyInfo));
        clearUsableSuccessHold(ProxyEndpointKey.network(proxyInfo));
    }

    private static void clearUsableSuccessHold(String key) {
        EndpointState state = endpointStates.get(key);
        if (state != null) {
            state.usableSuccessUntil = 0;
        }
    }

    private static void rememberEndpointConnected(EndpointState state, String diagnostic, long now, boolean usableSuccess) {
        state.consecutiveFailures = 0;
        state.lastDiagnostic = ProxyCheckDiagnostics.normalize(diagnostic);
        state.lastCheckTime = now;
        state.nextCheckTime = now + PROXY_CHECK_CONNECTED_GRACE_MS;
        state.usableSuccessUntil = usableSuccess ? now + USABLE_SUCCESS_HOLD_MS : 0;
    }

    private static void rememberEndpointFailure(EndpointState state, SharedConfig.ProxyInfo proxyInfo, String diagnostic, long now, String source) {
        state.usableSuccessUntil = 0;
        state.lastDiagnostic = ProxyCheckDiagnostics.normalize(diagnostic);
        state.lastCheckTime = now;
        state.consecutiveFailures++;
        long multiplier = 1L << Math.min(2, Math.max(0, state.consecutiveFailures - 1));
        long backoff = Math.min(PROXY_CHECK_FAILURE_BACKOFF_MAX_MS, PROXY_CHECK_FAILURE_BACKOFF_MS * multiplier);
        state.nextCheckTime = now + backoff;
        logControl("decision=backoff endpoint=" + ProxyEndpointKey.endpoint(proxyInfo) + " wait_ms=" + backoff + " failures=" + state.consecutiveFailures + " phase=" + state.lastDiagnostic + " source=" + source);
    }

    private static EndpointState endpointStateForKey(String key) {
        EndpointState state = endpointStates.get(key);
        if (state == null) {
            state = new EndpointState();
            endpointStates.put(key, state);
        }
        return state;
    }

    private static EndpointState latestEndpointState(SharedConfig.ProxyInfo proxyInfo) {
        EndpointState exactState = endpointStates.get(ProxyEndpointKey.exact(proxyInfo));
        EndpointState networkState = endpointStates.get(ProxyEndpointKey.network(proxyInfo));
        if (exactState == null) {
            return networkState;
        }
        if (networkState == null) {
            return exactState;
        }
        return networkState.lastCheckTime > exactState.lastCheckTime ? networkState : exactState;
    }

    private static EndpointState endpointFailureState(SharedConfig.ProxyInfo proxyInfo) {
        EndpointState exactState = endpointStates.get(ProxyEndpointKey.exact(proxyInfo));
        EndpointState networkState = endpointStates.get(ProxyEndpointKey.network(proxyInfo));
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

    private static void logControl(String message) {
        if (BuildVars.LOGS_ENABLED) {
            FileLog.d("proxy_control " + message);
        }
    }

    public static final class Decision {
        public final String decision;
        public final String phase;
        public final String endpointKey;
        public final boolean rotationTrigger;
        public final boolean visibleChanged;
        public final boolean shadowed;

        private Decision(String decision, String phase, String endpointKey, boolean rotationTrigger, boolean visibleChanged, boolean shadowed) {
            this.decision = decision;
            this.phase = phase;
            this.endpointKey = endpointKey;
            this.rotationTrigger = rotationTrigger;
            this.visibleChanged = visibleChanged;
            this.shadowed = shadowed;
        }

        private static Decision ignored(String decision, String phase, String endpointKey) {
            return new Decision(decision, phase, endpointKey, false, false, false);
        }
    }

    private static class EndpointState {
        int consecutiveFailures;
        String lastDiagnostic = ProxyCheckDiagnostics.UNKNOWN_FAIL;
        long lastCheckTime;
        long nextCheckTime;
        long usableSuccessUntil;
    }
}
