package org.telegram.messenger;

import android.os.SystemClock;

import org.telegram.tgnet.ConnectionsManager;

import java.util.HashMap;
import java.util.Locale;

public final class ProxyRuntimeStateStore {
    private static final long DNS_OUTAGE_WINDOW_MS = 60 * 1000L;
    private static final long DNS_PREVIOUS_FAILURE_WINDOW_MS = 60 * 1000L;
    private static final long RESUME_GRACE_MS = 8 * 1000L;
    private static final HashMap<String, DnsOutageState> dnsOutageStates = new HashMap<>();
    private static final int[] proxyActivationGenerationFloor = new int[UserConfig.MAX_ACCOUNT_COUNT];
    private static int visibleActivationGenerationFloor;
    private static int proxyActivationGeneration;
    private static long lastForegroundResumeElapsedMs;

    private ProxyRuntimeStateStore() {
    }

    public static Decision onRuntimeEvent(ProxyConnectionEvent event) {
        return ProxyEventReducer.reduce(event);
    }

    public static Decision onNativeStage(ProxyConnectionEvent event) {
        return onRuntimeEvent(event);
    }

    public static int noteProxySettingsActivation() {
        return noteProxySettingsActivation(ProxyConnectionEvent.Origin.SETTINGS_CHANGE);
    }

    public static int noteProxySettingsActivation(ProxyConnectionEvent.Origin origin) {
        return noteProxyActivation(origin == null ? ProxyConnectionEvent.Origin.SETTINGS_CHANGE : origin, -1, true);
    }

    public static int noteProxyStartupRestoreActivation(int account) {
        return noteProxyActivation(ProxyConnectionEvent.Origin.STARTUP_RESTORE, account, false);
    }

    public static int noteProxyLifecycleActivation(int account, ProxyConnectionEvent.Origin origin) {
        return noteProxyActivation(origin == null ? ProxyConnectionEvent.Origin.ACTIVE_SOCKET : origin, account, false);
    }

    private static int noteProxyActivation(ProxyConnectionEvent.Origin origin, int account, boolean allAccounts) {
        synchronized (ProxyRuntimeStateStore.class) {
            proxyActivationGeneration++;
            if (proxyActivationGeneration <= 0) {
                proxyActivationGeneration = 1;
            }
            if (allAccounts) {
                for (int a = 0; a < proxyActivationGenerationFloor.length; a++) {
                    proxyActivationGenerationFloor[a] = proxyActivationGeneration;
                }
            } else if (account >= 0 && account < proxyActivationGenerationFloor.length) {
                proxyActivationGenerationFloor[account] = proxyActivationGeneration;
            }
            if (ProxyConnectionEvent.isVisibleOwnerOrigin(origin)) {
                visibleActivationGenerationFloor = proxyActivationGeneration;
            }
            String originName = origin == null ? ProxyConnectionEvent.Origin.ACTIVE_SOCKET.wireName : origin.wireName;
            logControl("owner=ProxyRuntimeStateStore.noteProxyActivation decision=activation_generation origin=" + originName + " account=" + account + " all_accounts=" + (allAccounts ? 1 : 0) + " generation=" + proxyActivationGeneration + " visible_floor=" + visibleActivationGenerationFloor);
            return proxyActivationGeneration;
        }
    }

    static boolean shouldIgnoreStaleActivationGeneration(ProxyConnectionEvent event) {
        if (event == null
                || !ProxyConnectionEvent.isHealthOrigin(event.origin)
                || event.account < 0
                || event.account >= proxyActivationGenerationFloor.length) {
            return false;
        }
        int floor;
        int visibleFloor;
        synchronized (ProxyRuntimeStateStore.class) {
            floor = proxyActivationGenerationFloor[event.account];
            visibleFloor = visibleActivationGenerationFloor;
        }
        if (visibleFloor > 0
                && !ProxyConnectionEvent.canDriveVisible(event)
                && event.activationGeneration > 0
                && event.activationGeneration < visibleFloor) {
            return true;
        }
        return floor > 0 && event.activationGeneration != floor;
    }

    static int currentActivationGenerationForEvent(int account, ProxyConnectionEvent.Origin origin) {
        ProxyConnectionEvent.Origin safeOrigin = origin == null ? ProxyConnectionEvent.Origin.ACTIVE_SOCKET : origin;
        synchronized (ProxyRuntimeStateStore.class) {
            if (account >= 0 && account < proxyActivationGenerationFloor.length && proxyActivationGenerationFloor[account] > 0) {
                return proxyActivationGenerationFloor[account];
            }
            if (ProxyConnectionEvent.isVisibleOwnerOrigin(safeOrigin) && visibleActivationGenerationFloor > 0) {
                return visibleActivationGenerationFloor;
            }
            return Math.max(0, proxyActivationGeneration);
        }
    }

    public static void noteResumeForeground() {
        synchronized (ProxyRuntimeStateStore.class) {
            lastForegroundResumeElapsedMs = SystemClock.elapsedRealtime();
        }
        logControl("owner=ProxyRuntimeStateStore.noteResumeForeground decision=resume_foreground grace_ms=" + RESUME_GRACE_MS);
    }

    static boolean isResumeGrace(long now) {
        long lastResume;
        synchronized (ProxyRuntimeStateStore.class) {
            lastResume = lastForegroundResumeElapsedMs;
        }
        return lastResume > 0 && now - lastResume >= 0 && now - lastResume <= RESUME_GRACE_MS;
    }

    static boolean shouldShowResumeRestoringStatus(SharedConfig.ProxyInfo proxyInfo, long now) {
        if (proxyInfo == null || now <= 0) {
            return false;
        }
        long lastResume;
        synchronized (ProxyRuntimeStateStore.class) {
            lastResume = lastForegroundResumeElapsedMs;
        }
        if (lastResume <= 0 || now < lastResume) {
            return false;
        }
        String diagnostic = ProxyCheckDiagnostics.normalize(proxyInfo.lastCheckDiagnostic);
        if (ProxyPhasePolicy.isProxyUsableSuccessPhase(diagnostic)
                || ProxyCheckDiagnostics.OK.equals(diagnostic)) {
            return false;
        }
        boolean freshUnresolvedPhase = ProxyCheckDiagnostics.hasFreshFailure(proxyInfo)
                || ProxyCheckDiagnostics.hasFreshUnresolvedLivePhase(proxyInfo)
                || ProxyCheckDiagnostics.hasFreshEndpointCooldown(proxyInfo);
        if (!freshUnresolvedPhase) {
            return false;
        }
        if (isUserInitiatedVisibleDiagnostic(proxyInfo) && proxyInfo.lastCheckDiagnosticTime >= lastResume) {
            return false;
        }
        long graceDeadline = lastResume + RESUME_GRACE_MS;
        return now <= graceDeadline
                || (proxyInfo.lastCheckDiagnosticTime > 0 && proxyInfo.lastCheckDiagnosticTime <= graceDeadline);
    }

    private static boolean isUserInitiatedVisibleDiagnostic(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null || proxyInfo.lastCheckOrigin == null) {
            return false;
        }
        return ProxyConnectionEvent.Origin.USER_SELECT.wireName.equals(proxyInfo.lastCheckOrigin)
                || ProxyConnectionEvent.Origin.SETTINGS_CHANGE.wireName.equals(proxyInfo.lastCheckOrigin);
    }

    static Decision quarantineAndCancelEndpoint(SharedConfig.ProxyInfo proxyInfo, String phase, String endpointKey, String probeKey, long now, String source, ProxyConnectionEvent.Origin origin, int account, boolean visibleChanged) {
        return quarantineAndCancelEndpoint(proxyInfo, phase, endpointKey, probeKey, now, source, origin, account, 0, visibleChanged);
    }

    static Decision quarantineAndCancelEndpoint(SharedConfig.ProxyInfo proxyInfo, String phase, String endpointKey, String probeKey, long now, String source, ProxyConnectionEvent.Origin origin, int account, int activationGeneration, boolean visibleChanged) {
        String normalized = ProxyCheckDiagnostics.normalize(phase);
        String targetEndpointKey = endpointKey == null || endpointKey.length() == 0 ? ProxyEndpointKey.liveStage(proxyInfo) : endpointKey;
        String targetNetworkKey = ProxyEndpointKey.networkFromLiveStage(targetEndpointKey);
        String targetProbeKey = probeKey == null ? "" : probeKey;
        ProxyHealthStore.quarantineExactEndpoint(proxyInfo, normalized, now);
        ProxyHealthStore.ignoreEndpointTelemetry(targetEndpointKey, now, normalized);
        int proxyCheckCancelled = ProxyCheckScheduler.cancelEndpointAttempts(targetEndpointKey);
        String originName = origin == null ? ProxyConnectionEvent.Origin.ACTIVE_SOCKET.wireName : origin.wireName;
        boolean oneShotTerminal = ProxyPhasePolicy.isOneShotTerminal(normalized);
        String decision = oneShotTerminal ? "terminal_quarantine" : "rotation_trigger";
        int nativeCancelled = ConnectionsManager.cancelProxyEndpointAttempts(targetEndpointKey, targetProbeKey, decision);
        logControl("decision=cancel_endpoint_attempts source=" + source + " origin=" + originName + " account=" + account + " phase=" + normalized + " endpoint=" + targetEndpointKey + " probe=" + targetProbeKey + " proxy_check_cancelled=" + proxyCheckCancelled + " native_cancelled=" + nativeCancelled);
        if (oneShotTerminal) {
            logControl("decision=terminal_quarantine source=" + source + " origin=" + originName + " account=" + account + " phase=" + normalized + " endpoint=" + targetEndpointKey + " probe=" + targetProbeKey);
        } else {
            logControl("decision=rotation_trigger source=" + source + " origin=" + originName + " account=" + account + " phase=" + normalized + " endpoint=" + targetEndpointKey + " probe=" + targetProbeKey);
        }
        ProxyEndpointVerdict verdict = ProxyPhasePolicy.verdictForPhase(normalized, now)
                .withIdentity(targetEndpointKey, targetNetworkKey, activationGeneration, origin);
        return new Decision(decision, normalized, targetEndpointKey, verdict, true, visibleChanged, false);
    }

    static boolean shouldKeepConnectionNotStartedTelemetryOnlyByDnsOutage(SharedConfig.ProxyInfo proxyInfo, String phase, long now) {
        return proxyInfo != null
                && ProxyCheckDiagnostics.CONNECTION_NOT_STARTED.equals(ProxyCheckDiagnostics.normalize(phase))
                && previousPhaseWasDnsOutageOrResolveFailed(proxyInfo.settings.getAddress(), now);
    }

    public static boolean isFresh(SharedConfig.ProxyInfo proxyInfo) {
        return ProxyStatusMirror.isFresh(proxyInfo);
    }

    public static boolean isEndpointBackedOff(SharedConfig.ProxyInfo proxyInfo) {
        return ProxyHealthStore.isEndpointBackedOff(proxyInfo);
    }

    public static long nextAllowedCheckTime(SharedConfig.ProxyInfo proxyInfo) {
        return ProxyHealthStore.nextAllowedCheckTime(proxyInfo);
    }

    public static boolean hasFreshUsableSuccess(SharedConfig.ProxyInfo proxyInfo) {
        return ProxyHealthStore.hasFreshUsableSuccess(proxyInfo, SystemClock.elapsedRealtime());
    }

    public static boolean isCurrentProxyUsable(SharedConfig.ProxyInfo proxyInfo) {
        return isCurrentProxyUsable(proxyInfo, SystemClock.elapsedRealtime());
    }

    static boolean isCurrentProxyUsable(SharedConfig.ProxyInfo proxyInfo, long now) {
        return ProxyVisibleStateStore.isCurrentProxyUsable(proxyInfo, now);
    }

    public static boolean isEndpointRotatedAway(SharedConfig.ProxyInfo proxyInfo) {
        return ProxyHealthStore.isEndpointRotatedAway(proxyInfo, SystemClock.elapsedRealtime());
    }

    public static void clearRotatedAwayTelemetry() {
        ProxyHealthStore.clearRotatedAwayTelemetry();
    }

    public static long usableSuccessRemainingMs(SharedConfig.ProxyInfo proxyInfo) {
        return ProxyHealthStore.usableSuccessRemainingMs(proxyInfo, SystemClock.elapsedRealtime());
    }

    public static long lastUsableSuccessAgeMs(SharedConfig.ProxyInfo proxyInfo) {
        return lastUsableSuccessAgeMs(proxyInfo, SystemClock.elapsedRealtime());
    }

    public static long lastUsableSuccessAgeMs(SharedConfig.ProxyInfo proxyInfo, long now) {
        return ProxyHealthStore.lastUsableSuccessAgeMs(proxyInfo, now);
    }

    public static boolean isMtProxyStartupFanoutLimited(int account) {
        return ProxyWarmupGate.isMtProxyStartupFanoutLimited(account);
    }

    public static int fileLoaderStartupOperationLimit(int account, int normalLimit) {
        return ProxyWarmupGate.maxActiveMediaRequestsPerEndpoint(account, normalLimit, ProxyWarmupGate.NetworkRequestClass.MEDIA_VISIBLE);
    }

    public static int fileLoaderStartupRequestLimit(int account, int normalLimit, boolean delayedPreload) {
        ProxyWarmupGate.NetworkRequestClass requestClass = delayedPreload
                ? ProxyWarmupGate.NetworkRequestClass.MEDIA_PREFETCH
                : ProxyWarmupGate.NetworkRequestClass.MEDIA_VISIBLE;
        return ProxyWarmupGate.maxUploadGetFileOffsetsPerFile(account, normalLimit, requestClass);
    }

    public static int fileLoaderStartupFanoutRecheckDelayMs(int account) {
        return (int) ProxyWarmupGate.delayForNetworkHeavyOperation(account, 0, ProxyWarmupGate.NetworkRequestClass.MEDIA_VISIBLE);
    }

    public static String lastEndpointDiagnostic(SharedConfig.ProxyInfo proxyInfo) {
        return ProxyHealthStore.lastEndpointDiagnostic(proxyInfo, ProxyStatusMirror.diagnostic(proxyInfo));
    }

    public static void markConnected(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return;
        }
        int activationGeneration = currentActivationGenerationForEvent(UserConfig.selectedAccount, ProxyConnectionEvent.Origin.ACTIVE_SOCKET);
        onRuntimeEvent(ProxyConnectionEvent.connected(UserConfig.selectedAccount, proxyInfo, ProxyConnectionEvent.Origin.ACTIVE_SOCKET, activationGeneration, SystemClock.elapsedRealtime()));
    }

    public static void markConnectionStarting(SharedConfig.ProxyInfo proxyInfo) {
        markConnectionStarting(proxyInfo, ProxyConnectionEvent.Origin.ACTIVE_SOCKET);
    }

    public static void markConnectionStarting(SharedConfig.ProxyInfo proxyInfo, ProxyConnectionEvent.Origin origin) {
        if (proxyInfo == null) {
            return;
        }
        ProxyConnectionEvent.Origin safeOrigin = origin == null ? ProxyConnectionEvent.Origin.ACTIVE_SOCKET : origin;
        int activationGeneration = currentActivationGenerationForEvent(UserConfig.selectedAccount, safeOrigin);
        onRuntimeEvent(ProxyConnectionEvent.connectStart(UserConfig.selectedAccount, proxyInfo, safeOrigin, activationGeneration, SystemClock.elapsedRealtime()));
    }

    public static void markConnectionUsable(SharedConfig.ProxyInfo proxyInfo, String diagnostic) {
        markConnectionUsable(proxyInfo, diagnostic, SystemClock.elapsedRealtime());
    }

    public static void markConnectionUsable(SharedConfig.ProxyInfo proxyInfo, String diagnostic, long now) {
        markConnectionUsable(proxyInfo, diagnostic, now, currentActivationGenerationForEvent(UserConfig.selectedAccount, ProxyConnectionEvent.Origin.ACTIVE_SOCKET));
    }

    public static void markConnectionUsable(SharedConfig.ProxyInfo proxyInfo, String diagnostic, long now, int activationGeneration) {
        if (proxyInfo == null) {
            return;
        }
        String normalized = ProxyCheckDiagnostics.normalize(diagnostic);
        int eventGeneration = activationGeneration > 0
                ? activationGeneration
                : currentActivationGenerationForEvent(UserConfig.selectedAccount, ProxyConnectionEvent.Origin.ACTIVE_SOCKET);
        onRuntimeEvent(ProxyConnectionEvent.usableSuccess(UserConfig.selectedAccount, proxyInfo, normalized, ProxyConnectionEvent.Origin.ACTIVE_SOCKET, eventGeneration, now));
    }

    static boolean applyConnectionUsable(SharedConfig.ProxyInfo proxyInfo, String diagnostic, long now, int activationGeneration) {
        if (proxyInfo == null) {
            return false;
        }
        String normalized = ProxyCheckDiagnostics.normalize(diagnostic);
        if (!ProxyVisibleStateStore.markConnectionUsable(proxyInfo, normalized, now, activationGeneration)) {
            return false;
        }
        ProxyHealthStore.clearEndpointBackoff(proxyInfo, normalized, now);
        ProxyWarmupGate.onProxyUsable(ProxyEndpointKey.liveStage(proxyInfo), now);
        return true;
    }

    public static void markEndpointCooldown(SharedConfig.ProxyInfo proxyInfo, long now) {
        if (hasFreshConcreteProxyPhase(proxyInfo)) {
            return;
        }
        ProxyStatusMirror.markEndpointCooldown(proxyInfo, now);
    }

    public static void markCheckingIfNoFreshConcretePhase(SharedConfig.ProxyInfo proxyInfo) {
        ProxyStatusMirror.markCheckingIfNoFreshConcretePhase(proxyInfo);
    }

    public static void copyTransientState(SharedConfig.ProxyInfo target, SharedConfig.ProxyInfo source) {
        ProxyStatusMirror.copyTransientState(target, source);
    }

    public static void setChecking(SharedConfig.ProxyInfo proxyInfo, boolean checking) {
        ProxyStatusMirror.setChecking(proxyInfo, checking);
    }

    public static void setProxyCheckPingId(SharedConfig.ProxyInfo proxyInfo, long pingId) {
        ProxyStatusMirror.setProxyCheckPingId(proxyInfo, pingId);
    }

    public static void clearTransientState(SharedConfig.ProxyInfo proxyInfo) {
        ProxyStatusMirror.clearTransientState(proxyInfo);
    }

    public static void applyMeasuredProxyCheckResult(SharedConfig.ProxyInfo proxyInfo, long time, String diagnostic) {
        ProxyStatusMirror.applyMeasuredProxyCheckResult(proxyInfo, time, diagnostic);
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
            return ProxyStatusMirror.diagnostic(proxyInfo);
        }
        return ProxyCheckDiagnostics.OK;
    }

    public static void rememberProxyCheckResult(int account, SharedConfig.ProxyInfo proxyInfo, long time, String displayDiagnostic) {
        String normalizedDiagnostic = ProxyCheckDiagnostics.normalize(displayDiagnostic);
        long now = SystemClock.elapsedRealtime();
        boolean targetsActiveProxy = targetsCurrentProxyEndpoint(proxyInfo);
        if (ProxyHealthStore.isEndpointRotatedAway(proxyInfo, now)) {
            logControl("decision=ignored_rotated_away source=" + ProxyConnectionEvent.SOURCE_PROXY_CHECK + " phase=" + normalizedDiagnostic + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo));
            return;
        }
        if (time != -1) {
            if (targetsActiveProxy) {
                logControl("decision=proxy_list_only source=" + ProxyConnectionEvent.SOURCE_PROXY_CHECK + " origin=" + ProxyConnectionEvent.Origin.PROXY_CHECK.wireName + " phase=" + ProxyCheckDiagnostics.OK + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo) + " reason=active_origin_required");
                return;
            }
            ProxyHealthStore.rememberConnected(proxyInfo, now);
            return;
        }
        if (ProxyPhasePolicy.terminalExactConfig(normalizedDiagnostic)) {
            String endpointKey = ProxyEndpointKey.liveStage(proxyInfo);
            int proxyCheckCancelled = ProxyCheckScheduler.cancelEndpointAttempts(endpointKey);
            int nativeCancelled = 0;
            if (!targetsActiveProxy) {
                ProxyHealthStore.quarantineExactEndpoint(proxyInfo, normalizedDiagnostic, now);
                ProxyHealthStore.ignoreEndpointTelemetry(endpointKey, now, normalizedDiagnostic);
                nativeCancelled = ConnectionsManager.cancelProxyEndpointAttempts(endpointKey, "", "terminal_proxy_config_unsupported");
            }
            logControl("decision=terminal_proxy_config_unsupported source=" + ProxyConnectionEvent.SOURCE_PROXY_CHECK + " origin=" + ProxyConnectionEvent.Origin.PROXY_CHECK.wireName + " phase=" + normalizedDiagnostic + " endpoint=" + endpointKey + " row_only=1 active_match=" + (targetsActiveProxy ? 1 : 0));
            logControl("decision=cancel_endpoint_attempts source=" + ProxyConnectionEvent.SOURCE_PROXY_CHECK + " origin=" + ProxyConnectionEvent.Origin.PROXY_CHECK.wireName + " phase=" + normalizedDiagnostic + " endpoint=" + endpointKey + " probe= proxy_check_cancelled=" + proxyCheckCancelled + " native_cancelled=" + nativeCancelled + " row_only=1");
            logControl("decision=terminal_quarantine source=" + ProxyConnectionEvent.SOURCE_PROXY_CHECK + " origin=" + ProxyConnectionEvent.Origin.PROXY_CHECK.wireName + " phase=" + normalizedDiagnostic + " endpoint=" + endpointKey + " probe= row_only=1");
            return;
        }
        if (targetsActiveProxy) {
            logControl("decision=proxy_list_only source=" + ProxyConnectionEvent.SOURCE_PROXY_CHECK + " origin=" + ProxyConnectionEvent.Origin.PROXY_CHECK.wireName + " endpoint=" + ProxyEndpointKey.endpoint(proxyInfo) + " phase=" + normalizedDiagnostic + " reason=active_origin_required");
            return;
        }
        if (shouldPreserveProxyCheckFailure(account, proxyInfo, time)) {
            logControl("decision=proxy_list_only source=" + ProxyConnectionEvent.SOURCE_PROXY_CHECK + " origin=" + ProxyConnectionEvent.Origin.PROXY_CHECK.wireName + " endpoint=" + ProxyEndpointKey.endpoint(proxyInfo) + " phase=" + normalizedDiagnostic + " held_by=" + ProxyVisibleStateStore.heldByCurrentProxyPhase(SharedConfig.currentProxy, now));
            return;
        }
        if (ProxyHealthStore.hasFreshUsableSuccess(proxyInfo, now)) {
            logControl("decision=proxy_list_only source=" + ProxyConnectionEvent.SOURCE_PROXY_CHECK + " origin=" + ProxyConnectionEvent.Origin.PROXY_CHECK.wireName + " phase=" + normalizedDiagnostic + " endpoint=" + ProxyEndpointKey.endpoint(proxyInfo) + " held_by=" + ProxyVisibleStateStore.heldByUsablePhase(proxyInfo, now));
            return;
        }
        ProxyHealthStore.rememberProxyCheckFailure(proxyInfo, normalizedDiagnostic, now);
    }

    public static boolean isSwitchableCandidate(SharedConfig.ProxyInfo info) {
        return info != null
                && info != SharedConfig.currentProxy
                && !ProxyStatusMirror.isChecking(info)
                && !ProxyCheckDiagnostics.hasFreshFailure(info)
                && !ProxyCheckDiagnostics.hasFreshEndpointCooldown(info)
                && !ProxyCheckDiagnostics.hasFreshUnresolvedLivePhase(info)
                && !ProxyHealthStore.isEndpointRotatedAway(info, SystemClock.elapsedRealtime())
                && !isEndpointBackedOff(info);
    }

    public static boolean hasFreshConcreteProxyPhase(SharedConfig.ProxyInfo proxyInfo) {
        return ProxyStatusMirror.hasFreshConcreteProxyPhase(proxyInfo);
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

    static void rememberDnsResolveFailurePhase(SharedConfig.ProxyInfo proxyInfo, String phase, long now) {
        if (proxyInfo == null) {
            return;
        }
        String normalized = ProxyCheckDiagnostics.normalize(phase);
        if (!ProxyCheckDiagnostics.HOST_RESOLVE_FAILED.equals(normalized)
                && !ProxyCheckDiagnostics.HOST_RESOLVE_TIMEOUT.equals(normalized)
                && !ProxyCheckDiagnostics.DNS_NEGATIVE_CACHE_HIT.equals(normalized)
                && !ProxyCheckDiagnostics.DNS_BLOCKED_ZERO_ADDRESS.equals(normalized)) {
            return;
        }
        String key = normalizeDnsHost(proxyInfo.settings.getAddress());
        if (key.length() == 0) {
            return;
        }
        synchronized (dnsOutageStates) {
            DnsOutageState state = dnsOutageStateForHostLocked(key, now);
            state.lastResolveFailureAtMs = now;
        }
    }

    private static boolean previousPhaseWasDnsOutageOrResolveFailed(String host, long now) {
        String key = normalizeDnsHost(host);
        if (key.length() == 0) {
            return false;
        }
        synchronized (dnsOutageStates) {
            DnsOutageState state = dnsOutageStates.get(key);
            if (state == null) {
                return false;
            }
            boolean previousDnsOutage = state.failures > 0
                    && now - state.windowStartedAtMs <= DNS_OUTAGE_WINDOW_MS
                    && state.hasAllProvidersFailed();
            boolean previousResolveFailed = state.lastResolveFailureAtMs > 0
                    && now - state.lastResolveFailureAtMs <= DNS_PREVIOUS_FAILURE_WINDOW_MS;
            return previousDnsOutage || previousResolveFailed;
        }
    }

    public static void recordDnsResolverProviderFailure(String host, String provider, String reason) {
        String key = normalizeDnsHost(host);
        if (key.length() == 0 || !isDnsOutageProvider(provider)) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        synchronized (dnsOutageStates) {
            DnsOutageState state = dnsOutageStateForHostLocked(key, now);
            state.markProviderFailed(provider, now);
        }
    }

    public static void recordDnsResolveChainFailure(String host, boolean systemFailed, boolean googleFailed, boolean cloudflareFailed) {
        String key = normalizeDnsHost(host);
        if (key.length() == 0) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        synchronized (dnsOutageStates) {
            DnsOutageState state = dnsOutageStateForHostLocked(key, now);
            state.systemFailed = state.systemFailed || systemFailed;
            state.googleFailed = state.googleFailed || googleFailed;
            state.cloudflareFailed = state.cloudflareFailed || cloudflareFailed;
            if (state.hasAllProvidersFailed()) {
                state.failures++;
                state.lastFailureAtMs = now;
                state.lastResolveFailureAtMs = now;
                logControl("decision=dns_outage_record host=" + key + " failures=" + state.failures + " providers=system,google_json_doh,cloudflare_json_doh");
            }
        }
    }

    public static void recordDnsNegativeCacheHit(String host, String reason) {
        String key = normalizeDnsHost(host);
        if (key.length() == 0) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        synchronized (dnsOutageStates) {
            DnsOutageState state = dnsOutageStateForHostLocked(key, now);
            state.lastResolveFailureAtMs = now;
        }
        logControl("decision=dns_negative_cache_hit host=" + key + " reason=" + reason);
    }

    public static void recordDnsResolveSuccess(String host, String provider) {
        String key = normalizeDnsHost(host);
        if (key.length() == 0) {
            return;
        }
        boolean removed;
        synchronized (dnsOutageStates) {
            removed = dnsOutageStates.remove(key) != null;
        }
        if (removed) {
            logControl("decision=dns_outage_clear host=" + key + " provider=" + provider);
        }
    }

    public static boolean isDnsGlobalOutage(String host, long now) {
        String key = normalizeDnsHost(host);
        if (key.length() == 0) {
            return false;
        }
        synchronized (dnsOutageStates) {
            DnsOutageState state = dnsOutageStates.get(key);
            return state != null
                    && state.failures > 0
                    && now - state.windowStartedAtMs <= DNS_OUTAGE_WINDOW_MS
                    && state.hasAllProvidersFailed();
        }
    }

    static boolean shouldHoldHostResolveFailureByDnsOutage(SharedConfig.ProxyInfo proxyInfo, String phase, long now) {
        return proxyInfo != null
                && ProxyCheckDiagnostics.HOST_RESOLVE_FAILED.equals(ProxyCheckDiagnostics.normalize(phase))
                && isDnsGlobalOutage(proxyInfo.settings.getAddress(), now);
    }

    static int dnsOutageFailures(SharedConfig.ProxyInfo proxyInfo, long now) {
        if (proxyInfo == null) {
            return 0;
        }
        String key = normalizeDnsHost(proxyInfo.settings.getAddress());
        synchronized (dnsOutageStates) {
            DnsOutageState state = dnsOutageStates.get(key);
            if (state == null || now - state.windowStartedAtMs > DNS_OUTAGE_WINDOW_MS) {
                return 0;
            }
            return state.failures;
        }
    }

    static String dnsHost(SharedConfig.ProxyInfo proxyInfo) {
        return proxyInfo == null ? "" : normalizeDnsHost(proxyInfo.settings.getAddress());
    }

    private static DnsOutageState dnsOutageStateForHostLocked(String host, long now) {
        DnsOutageState state = dnsOutageStates.get(host);
        if (state == null || now - state.windowStartedAtMs > DNS_OUTAGE_WINDOW_MS) {
            state = new DnsOutageState(host, now);
            dnsOutageStates.put(host, state);
        }
        return state;
    }

    private static boolean isDnsOutageProvider(String provider) {
        return "system".equals(provider)
                || "google_json_doh".equals(provider)
                || "cloudflare_json_doh".equals(provider);
    }

    private static String normalizeDnsHost(String host) {
        if (host == null) {
            return "";
        }
        return host.trim().toLowerCase(Locale.US);
    }

    private static final class DnsOutageState {
        long windowStartedAtMs;
        int failures;
        String host;
        boolean cloudflareFailed;
        boolean googleFailed;
        boolean systemFailed;
        long lastFailureAtMs;
        long lastResolveFailureAtMs;

        DnsOutageState(String host, long now) {
            this.host = host;
            this.windowStartedAtMs = now;
        }

        void markProviderFailed(String provider, long now) {
            if ("system".equals(provider)) {
                systemFailed = true;
            } else if ("google_json_doh".equals(provider)) {
                googleFailed = true;
            } else if ("cloudflare_json_doh".equals(provider)) {
                cloudflareFailed = true;
            }
            lastFailureAtMs = now;
        }

        boolean hasAllProvidersFailed() {
            return systemFailed && googleFailed && cloudflareFailed;
        }
    }

    private static String lastControlMessage;

    static void logControl(String message) {
        if (!BuildVars.LOGS_ENABLED) {
            return;
        }
        // Collapse runs of identical control decisions. A churning/rotated-away proxy can re-emit the
        // same decision (e.g. ignored_rotated_away) thousands of times per second; only the transition
        // carries diagnostic value, so suppress consecutive duplicates. This was ~200k of the main-log
        // lines in a 4-minute capture.
        synchronized (ProxyRuntimeStateStore.class) {
            if (message.equals(lastControlMessage)) {
                return;
            }
            lastControlMessage = message;
        }
        FileLog.d("proxy_control " + (message.contains("owner=") ? message : "owner=ProxyRuntimeStateStore " + message));
    }

    static void logRotation(String message) {
        if (BuildVars.LOGS_ENABLED) {
            FileLog.d("proxy_rotation " + (message.contains("owner=") ? message : "owner=ProxyRuntimeStateStore " + message));
        }
    }

    public static final class Decision {
        public final String decision;
        public final String phase;
        public final String endpointKey;
        public final ProxyEndpointVerdict verdict;
        public final boolean rotationTrigger;
        public final boolean visibleChanged;
        public final boolean shadowed;

        Decision(String decision, String phase, String endpointKey, boolean rotationTrigger, boolean visibleChanged, boolean shadowed) {
            this(decision, phase, endpointKey, ProxyPhasePolicy.verdictForPhase(phase, 0).withIdentity(endpointKey, ProxyEndpointKey.networkFromLiveStage(endpointKey), 0, ProxyConnectionEvent.Origin.ACTIVE_SOCKET), rotationTrigger, visibleChanged, shadowed);
        }

        Decision(String decision, String phase, String endpointKey, ProxyEndpointVerdict verdict, boolean rotationTrigger, boolean visibleChanged, boolean shadowed) {
            this.decision = decision;
            this.phase = ProxyCheckDiagnostics.normalize(phase);
            this.endpointKey = endpointKey;
            this.verdict = verdict == null ? ProxyPhasePolicy.verdictForPhase(phase, 0) : verdict;
            this.rotationTrigger = rotationTrigger;
            this.visibleChanged = visibleChanged;
            this.shadowed = shadowed;
        }

        static Decision ignored(String decision, String phase, String endpointKey) {
            return new Decision(decision, phase, endpointKey, false, false, false);
        }

        static Decision ignored(String decision, String phase, String endpointKey, ProxyEndpointVerdict verdict) {
            return new Decision(decision, phase, endpointKey, verdict, false, false, false);
        }
    }

}
