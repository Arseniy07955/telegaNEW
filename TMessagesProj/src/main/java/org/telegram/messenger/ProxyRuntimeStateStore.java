package org.telegram.messenger;

import android.os.SystemClock;

import org.telegram.tgnet.ConnectionsManager;

import java.util.HashMap;
import java.util.Locale;

public final class ProxyRuntimeStateStore {
    private static final long DNS_OUTAGE_WINDOW_MS = 60 * 1000L;
    private static final long DNS_PREVIOUS_FAILURE_WINDOW_MS = 60 * 1000L;
    private static final long DNS_VISIBLE_DELAY_MS = 800L;
    private static final HashMap<String, DnsOutageState> dnsOutageStates = new HashMap<>();
    private static long pendingDnsVisibleGeneration;
    private static String pendingDnsVisibleEndpointKey = "";
    private static String pendingDnsVisiblePhase = "";
    private static int pendingDnsVisibleAccount = -1;
    private static long pendingDnsVisibleStartedAtMs;

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
        if (concretePhase && ProxyHealthStore.shouldIgnoreEndpointTelemetry(event.endpointKey, event.timestamp)) {
            clearPendingDnsVisiblePhase(event.endpointKey, event.timestamp);
            logControl("decision=ignored_rotated_away source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey);
            return Decision.ignored("ignored_rotated_away", event.phase, event.endpointKey);
        }
        boolean stageTargetsCurrentProxy = currentProxy != null && concretePhase && ProxyEndpointKey.matchesLiveStage(currentProxy, event.endpointKey);
        if (!stageTargetsCurrentProxy) {
            if (selectedAccountStage && currentProxy != null && concretePhase) {
                logControl("decision=ignored_stale_endpoint source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " current=" + ProxyEndpointKey.liveStage(currentProxy));
            }
            return Decision.ignored("ignored_stale_endpoint", event.phase, event.endpointKey);
        }
        if (!shouldDelayDnsVisiblePhase(event.phase)) {
            clearPendingDnsVisiblePhase(event.endpointKey, event.timestamp);
        }
        ProxyWarmupGate.onProxyLivePhase(event.endpointKey, event.phase, event.timestamp);
        if (ProxyPhasePolicy.isProxyUsableSuccessPhase(event.phase)) {
            markConnectionUsable(currentProxy, event.phase, event.timestamp);
            logControl("decision=visible_usable_success source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey);
            return new Decision("visible_usable_success", event.phase, event.endpointKey, false, true, false);
        }
        if (shouldHoldLivePhaseByUsableSuccess(currentProxy, event)) {
            String heldBy = heldByUsablePhase(currentProxy, event.timestamp);
            logControl("decision=held_live_by_usable_success source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " held_by=" + heldBy);
            return new Decision("held_live_by_usable_success", event.phase, event.endpointKey, false, false, true);
        }
        if (shouldShadowFailureByUsableSuccess(currentProxy, event)) {
            String heldBy = heldByUsablePhase(currentProxy, event.timestamp);
            logControl("decision=shadowed_by_usable_success source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " held_by=" + heldBy);
            return new Decision("shadowed_by_usable_success", event.phase, event.endpointKey, false, false, true);
        }
        boolean freshUsableSuccess = ProxyHealthStore.hasFreshUsableSuccess(currentProxy, event.timestamp);
        if (!freshUsableSuccess
                && isCurrentProxyUsable(currentProxy, event.timestamp)
                && ProxyPhasePolicy.isLivePhase(event.phase)
                && !ProxyPhasePolicy.isProxyUsableSuccessPhase(event.phase)) {
            String heldBy = heldByCurrentProxyPhase(currentProxy, event.timestamp);
            logControl("decision=held_live_by_current_proxy_usable source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " held_by=" + heldBy);
            return new Decision("held_live_by_current_proxy_usable", event.phase, event.endpointKey, false, false, true);
        }
        if (ProxyPhasePolicy.canBackoff(event.phase) && freshUsableSuccess) {
            String heldBy = heldByUsablePhase(currentProxy, event.timestamp);
            logControl("decision=held_by_usable_success source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " held_by=" + heldBy);
            return new Decision("held_by_usable_success", event.phase, event.endpointKey, false, false, true);
        }
        if (ProxyPhasePolicy.canBackoff(event.phase) && isCurrentProxyUsable(currentProxy, event.timestamp)) {
            String heldBy = heldByCurrentProxyPhase(currentProxy, event.timestamp);
            logControl("decision=held_by_current_proxy_usable source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " held_by=" + heldBy);
            return new Decision("held_by_current_proxy_usable", event.phase, event.endpointKey, false, false, true);
        }
        rememberDnsResolveFailurePhase(currentProxy, event.phase, event.timestamp);
        if (shouldHoldHostResolveFailureByDnsOutage(currentProxy, event.phase, event.timestamp)) {
            logControl("decision=dns_outage_hold source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " host=" + dnsHost(currentProxy) + " failures=" + dnsOutageFailures(currentProxy, event.timestamp));
            return new Decision("dns_outage_hold", event.phase, event.endpointKey, false, false, true);
        }
        if (shouldKeepConnectionNotStartedTelemetryOnlyByDnsOutage(currentProxy, event.phase, event.timestamp)) {
            logControl("decision=telemetry_only reason=previous_dns_outage source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " host=" + dnsHost(currentProxy) + " failures=" + dnsOutageFailures(currentProxy, event.timestamp));
            return new Decision("telemetry_only", event.phase, event.endpointKey, false, false, false);
        }

        if (shouldDelayDnsVisiblePhase(event.phase)) {
            if (selectedAccountStage) {
                scheduleDnsVisiblePhase(currentProxy, event);
            }
            logControl("decision=telemetry_only source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " delay_ms=" + DNS_VISIBLE_DELAY_MS);
            return new Decision("telemetry_only", event.phase, event.endpointKey, false, false, false);
        }

        if (shouldKeepLifecycleFailureTelemetryOnly(event.phase)) {
            logControl("decision=telemetry_only source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey);
            return new Decision("telemetry_only", event.phase, event.endpointKey, false, false, false);
        }

        boolean visibleChanged = false;
        if (selectedAccountStage && ProxyPhasePolicy.canOverwriteVisible(event.phase)) {
            if (ProxyCheckDiagnostics.shouldKeepFreshFailure(currentProxy, event.phase)) {
                logControl("decision=held_by_fresh_failure source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " held_by=" + ProxyStatusMirror.diagnostic(currentProxy));
            } else {
                ProxyStatusMirror.mirrorVisiblePhase(currentProxy, event.phase, event.timestamp);
                visibleChanged = true;
            }
        }

        if (!ProxyPhasePolicy.canBackoff(event.phase)) {
            logControl("decision=visible_only source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey);
            return new Decision("visible_only", event.phase, event.endpointKey, false, visibleChanged, false);
        }

        if (ProxyPhasePolicy.isPunitiveFailure(event.phase)) {
            ProxyWarmupGate.onProxyFailure(event.endpointKey, event.phase, event.timestamp);
        }
        ProxyHealthStore.EndpointFailureResult failure = ProxyHealthStore.rememberLiveFailure(currentProxy, event.phase, event.timestamp);
        if (ProxyPhasePolicy.canRotate(event.phase) && failure.rotationAllowed) {
            ProxyHealthStore.quarantineExactEndpoint(currentProxy, event.phase, event.timestamp);
            ProxyHealthStore.ignoreEndpointTelemetry(event.endpointKey, event.timestamp, event.phase);
            ProxyCheckScheduler.cancelEndpointAttempts(event.endpointKey);
            logControl("decision=rotation_trigger source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey);
            return new Decision("rotation_trigger", event.phase, event.endpointKey, true, visibleChanged, false);
        }
        if (ProxyPhasePolicy.canRotate(event.phase)) {
            logControl("decision=held_by_failure_hysteresis source=" + event.source + " account=" + event.account + " phase=" + event.phase + " endpoint=" + event.endpointKey + " failures=" + failure.rotationFailures);
        }
        return new Decision("backoff", event.phase, event.endpointKey, false, visibleChanged, false);
    }

    private static boolean shouldHoldLivePhaseByUsableSuccess(SharedConfig.ProxyInfo proxyInfo, ProxyConnectionEvent event) {
        if (proxyInfo == null || event == null) {
            return false;
        }

        String phase = ProxyCheckDiagnostics.normalize(event.phase);
        if (!ProxyHealthStore.hasFreshUsableSuccess(proxyInfo, event.timestamp)) {
            return false;
        }
        if (!ProxyPhasePolicy.isLivePhase(phase)) {
            return false;
        }
        if (ProxyPhasePolicy.isProxyUsableSuccessPhase(phase)) {
            return false;
        }
        return true;
    }

    private static boolean shouldShadowFailureByUsableSuccess(SharedConfig.ProxyInfo proxyInfo, ProxyConnectionEvent event) {
        if (proxyInfo == null || event == null) {
            return false;
        }
        String phase = ProxyCheckDiagnostics.normalize(event.phase);
        return ProxyPhasePolicy.isFailure(phase)
                && ProxyHealthStore.hasFreshUsableSuccess(proxyInfo, event.timestamp);
    }

    private static String heldByUsablePhase(SharedConfig.ProxyInfo proxyInfo, long now) {
        String heldBy = ProxyHealthStore.lastUsablePhase(proxyInfo, now);
        if (ProxyPhasePolicy.isProxyUsableSuccessPhase(heldBy)) {
            return heldBy;
        }
        heldBy = ProxyStatusMirror.diagnostic(proxyInfo);
        if (ProxyPhasePolicy.isProxyUsableSuccessPhase(heldBy)) {
            return heldBy;
        }
        return ProxyCheckDiagnostics.FIRST_TLS_APP_RECV;
    }

    private static String heldByCurrentProxyPhase(SharedConfig.ProxyInfo proxyInfo, long now) {
        String heldBy = ProxyHealthStore.lastUsablePhase(proxyInfo, now);
        if (ProxyPhasePolicy.isProxyUsableSuccessPhase(heldBy)) {
            return heldBy;
        }
        heldBy = ProxyStatusMirror.diagnostic(proxyInfo);
        if (ProxyPhasePolicy.isFailure(heldBy)) {
            return ProxyCheckDiagnostics.OK;
        }
        return heldBy;
    }

    private static boolean shouldDelayDnsVisiblePhase(String phase) {
        switch (ProxyCheckDiagnostics.normalize(phase)) {
            case ProxyCheckDiagnostics.HOST_RESOLVE_START:
            case ProxyCheckDiagnostics.DNS_COALESCE_WAIT:
                return true;
            default:
                return false;
        }
    }

    private static boolean shouldKeepLifecycleFailureTelemetryOnly(String phase) {
        String normalized = ProxyCheckDiagnostics.normalize(phase);
        return ProxyCheckDiagnostics.BACKGROUND_HANDSHAKE_ABORTED.equals(normalized)
                || ProxyCheckDiagnostics.DNS_NEGATIVE_CACHE_HIT.equals(normalized);
    }

    private static boolean shouldKeepConnectionNotStartedTelemetryOnlyByDnsOutage(SharedConfig.ProxyInfo proxyInfo, String phase, long now) {
        return proxyInfo != null
                && ProxyCheckDiagnostics.CONNECTION_NOT_STARTED.equals(ProxyCheckDiagnostics.normalize(phase))
                && previousPhaseWasDnsOutageOrResolveFailed(proxyInfo.address, now);
    }

    private static void scheduleDnsVisiblePhase(SharedConfig.ProxyInfo proxyInfo, ProxyConnectionEvent event) {
        if (proxyInfo == null || event == null || event.endpointKey.length() == 0) {
            return;
        }
        long generation = ++pendingDnsVisibleGeneration;
        pendingDnsVisibleEndpointKey = event.endpointKey;
        pendingDnsVisiblePhase = event.phase;
        pendingDnsVisibleAccount = event.account;
        pendingDnsVisibleStartedAtMs = event.timestamp;
        AndroidUtilities.runOnUIThread(() -> promotePendingDnsVisiblePhase(generation), DNS_VISIBLE_DELAY_MS);
    }

    private static void promotePendingDnsVisiblePhase(long generation) {
        if (generation != pendingDnsVisibleGeneration || pendingDnsVisibleEndpointKey.length() == 0) {
            return;
        }
        SharedConfig.ProxyInfo currentProxy = SharedConfig.currentProxy;
        String endpointKey = pendingDnsVisibleEndpointKey;
        String phase = pendingDnsVisiblePhase;
        int account = pendingDnsVisibleAccount;
        long startedAtMs = pendingDnsVisibleStartedAtMs;
        long now = SystemClock.elapsedRealtime();
        if (currentProxy == null
                || !ProxyEndpointKey.matchesTelemetryEndpointKey(currentProxy, endpointKey)
                || !shouldDelayDnsVisiblePhase(phase)
                || now - startedAtMs < DNS_VISIBLE_DELAY_MS
                || currentProxy.lastCheckDiagnosticTime > startedAtMs
                || ProxyHealthStore.hasFreshUsableSuccess(currentProxy, now)
                || isCurrentProxyUsable(currentProxy, now)
                || ProxyCheckDiagnostics.hasFreshFailure(currentProxy)) {
            clearPendingDnsVisiblePhase(endpointKey, now);
            return;
        }
        ProxyStatusMirror.mirrorVisiblePhase(currentProxy, phase, now);
        logControl("decision=visible_delayed_dns source=" + ProxyConnectionEvent.SOURCE_NATIVE_STAGE + " account=" + account + " phase=" + phase + " endpoint=" + endpointKey + " delay_ms=" + (now - startedAtMs));
        clearPendingDnsVisiblePhase(endpointKey, now);
        NotificationCenter.getGlobalInstance().postNotificationName(NotificationCenter.proxyConnectionStageChanged, phase, endpointKey);
        AccountInstance.getInstance(account).getNotificationCenter().postNotificationName(NotificationCenter.proxyConnectionStageChanged, phase, endpointKey);
    }

    private static void clearPendingDnsVisiblePhase(String endpointKey, long now) {
        if (pendingDnsVisibleEndpointKey.length() == 0 || !ProxyEndpointKey.sameTelemetryEndpointKey(pendingDnsVisibleEndpointKey, endpointKey)) {
            return;
        }
        pendingDnsVisibleGeneration++;
        pendingDnsVisibleEndpointKey = "";
        pendingDnsVisiblePhase = "";
        pendingDnsVisibleAccount = -1;
        pendingDnsVisibleStartedAtMs = 0;
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

    private static boolean isCurrentProxyUsable(SharedConfig.ProxyInfo proxyInfo, long now) {
        if (ProxyHealthStore.isEndpointRotatedAway(proxyInfo, now)) {
            return false;
        }
        return ProxyHealthStore.hasFreshUsableSuccess(proxyInfo, now)
                || isConnectedCurrentProxy(UserConfig.selectedAccount, proxyInfo);
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
        long now = SystemClock.elapsedRealtime();
        clearPendingDnsVisiblePhase(ProxyEndpointKey.liveStage(proxyInfo), now);
        if (ProxyHealthStore.isEndpointRotatedAway(proxyInfo, now)) {
            logControl("decision=ignored_rotated_away source=" + ProxyConnectionEvent.SOURCE_CONNECTED + " phase=" + ProxyCheckDiagnostics.OK + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo));
            return;
        }
        boolean changed = ProxyStatusMirror.isChecking(proxyInfo) || !ProxyStatusMirror.isAvailable(proxyInfo) || !ProxyStatusMirror.isFresh(proxyInfo);
        boolean preserveFreshProxyPhase = ProxyCheckDiagnostics.hasFreshFailure(proxyInfo) || ProxyHealthStore.hasFreshUsableSuccess(proxyInfo, now);
        if (!preserveFreshProxyPhase) {
            ProxyStatusMirror.markConnected(proxyInfo, now);
            ProxyHealthStore.rememberConnected(proxyInfo, now);
        }
        ProxyStatusMirror.clearTransientState(proxyInfo);
        if (changed) {
            logControl("decision=generic_connected endpoint=" + ProxyEndpointKey.endpoint(proxyInfo) + " preserve=" + preserveFreshProxyPhase);
        }
    }

    public static void markConnectionStarting(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return;
        }
        long now = SystemClock.elapsedRealtime();
        clearPendingDnsVisiblePhase(ProxyEndpointKey.liveStage(proxyInfo), now);
        if (ProxyHealthStore.isEndpointRotatedAway(proxyInfo, now)) {
            logControl("decision=ignored_rotated_away source=" + ProxyConnectionEvent.SOURCE_CONNECT_START + " phase=" + ProxyCheckDiagnostics.CONNECT_START + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo));
            return;
        }
        if (ProxyHealthStore.hasFreshUsableSuccess(proxyInfo, now)) {
            logControl("decision=held_live_by_usable_success source=" + ProxyConnectionEvent.SOURCE_CONNECT_START + " phase=" + ProxyCheckDiagnostics.CONNECT_START + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo) + " held_by=" + heldByUsablePhase(proxyInfo, now));
            return;
        }
        if (isCurrentProxyUsable(proxyInfo, now)) {
            logControl("decision=held_live_by_current_proxy_usable source=" + ProxyConnectionEvent.SOURCE_CONNECT_START + " phase=" + ProxyCheckDiagnostics.CONNECT_START + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo) + " held_by=" + heldByCurrentProxyPhase(proxyInfo, now));
            return;
        }
        ProxyStatusMirror.markConnectionStarting(proxyInfo, now);
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
        clearPendingDnsVisiblePhase(ProxyEndpointKey.liveStage(proxyInfo), now);
        if (ProxyHealthStore.isEndpointRotatedAway(proxyInfo, now)) {
            logControl("decision=ignored_rotated_away source=usable_success phase=" + normalized + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo));
            return;
        }
        ProxyStatusMirror.markConnectionUsable(proxyInfo, normalized, now);
        ProxyHealthStore.clearEndpointBackoff(proxyInfo, normalized, now);
        ProxyStatusMirror.clearTransientState(proxyInfo);
        ProxyWarmupGate.onProxyUsable(ProxyEndpointKey.liveStage(proxyInfo), now);
    }

    public static ProxyHealthStore.EndpointFailureResult markEndpointFailure(SharedConfig.ProxyInfo proxyInfo, String diagnostic) {
        if (proxyInfo == null) {
            return ProxyHealthStore.EndpointFailureResult.noop(diagnostic);
        }
        long now = SystemClock.elapsedRealtime();
        String normalized = ProxyCheckDiagnostics.normalize(diagnostic);
        if (shouldKeepConnectionNotStartedTelemetryOnlyByDnsOutage(proxyInfo, normalized, now)) {
            logControl("decision=telemetry_only reason=previous_dns_outage source=live_failure phase=" + normalized + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo) + " host=" + dnsHost(proxyInfo) + " failures=" + dnsOutageFailures(proxyInfo, now));
            return ProxyHealthStore.EndpointFailureResult.noop(normalized);
        }
        if (!ProxyPhasePolicy.canBackoff(diagnostic)) {
            return ProxyHealthStore.EndpointFailureResult.noop(normalized);
        }
        clearPendingDnsVisiblePhase(ProxyEndpointKey.liveStage(proxyInfo), now);
        if (ProxyHealthStore.isEndpointRotatedAway(proxyInfo, now)) {
            logControl("decision=ignored_rotated_away source=live_failure phase=" + normalized + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo));
            return ProxyHealthStore.EndpointFailureResult.noop(normalized);
        }
        if (ProxyHealthStore.hasFreshUsableSuccess(proxyInfo, now)) {
            logControl("decision=held_by_usable_success source=live_failure phase=" + normalized + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo) + " held_by=" + heldByUsablePhase(proxyInfo, now));
            return ProxyHealthStore.EndpointFailureResult.noop(normalized);
        }
        if (isCurrentProxyUsable(proxyInfo, now)) {
            logControl("decision=held_by_current_proxy_usable source=live_failure phase=" + normalized + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo) + " held_by=" + heldByCurrentProxyPhase(proxyInfo, now));
            return ProxyHealthStore.EndpointFailureResult.noop(normalized);
        }
        rememberDnsResolveFailurePhase(proxyInfo, normalized, now);
        if (shouldHoldHostResolveFailureByDnsOutage(proxyInfo, normalized, now)) {
            logControl("decision=dns_outage_hold source=live_failure phase=" + normalized + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo) + " host=" + dnsHost(proxyInfo) + " failures=" + dnsOutageFailures(proxyInfo, now));
            return ProxyHealthStore.EndpointFailureResult.noop(normalized);
        }
        if (ProxyPhasePolicy.isPunitiveFailure(normalized)) {
            ProxyWarmupGate.onProxyFailure(ProxyEndpointKey.liveStage(proxyInfo), normalized, now);
        }
        ProxyHealthStore.EndpointFailureResult failure = ProxyHealthStore.rememberLiveFailure(proxyInfo, normalized, now);
        if (ProxyPhasePolicy.canRotate(normalized) && failure.rotationAllowed) {
            ProxyHealthStore.quarantineExactEndpoint(proxyInfo, normalized, now);
            ProxyHealthStore.ignoreEndpointTelemetry(ProxyEndpointKey.liveStage(proxyInfo), now);
            ProxyCheckScheduler.cancelEndpointAttempts(ProxyEndpointKey.liveStage(proxyInfo));
            logControl("decision=rotation_trigger source=live_failure phase=" + normalized + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo));
        } else if (ProxyPhasePolicy.canRotate(normalized)) {
            logControl("decision=held_by_failure_hysteresis source=live_failure phase=" + normalized + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo) + " failures=" + failure.rotationFailures);
        }
        return failure;
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
        if (ProxyHealthStore.isEndpointRotatedAway(proxyInfo, now)) {
            logControl("decision=ignored_rotated_away source=" + ProxyConnectionEvent.SOURCE_PROXY_CHECK + " phase=" + normalizedDiagnostic + " endpoint=" + ProxyEndpointKey.liveStage(proxyInfo));
            return;
        }
        if (time != -1) {
            ProxyHealthStore.rememberConnected(proxyInfo, now);
            return;
        }
        if (shouldPreserveProxyCheckFailure(account, proxyInfo, time)) {
            logControl("decision=proxy_check_shadowed endpoint=" + ProxyEndpointKey.endpoint(proxyInfo) + " phase=" + normalizedDiagnostic);
            return;
        }
        if (ProxyHealthStore.hasFreshUsableSuccess(proxyInfo, now)) {
            logControl("decision=held_by_usable_success source=" + ProxyConnectionEvent.SOURCE_PROXY_CHECK + " phase=" + normalizedDiagnostic + " endpoint=" + ProxyEndpointKey.endpoint(proxyInfo) + " held_by=" + heldByUsablePhase(proxyInfo, now));
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

    public static boolean shouldScheduleFallback(int account, String diagnostic, String endpointKey) {
        SharedConfig.ProxyInfo currentProxy = SharedConfig.currentProxy;
        String normalized = ProxyCheckDiagnostics.normalize(diagnostic);
        long now = SystemClock.elapsedRealtime();
        boolean candidate = account == UserConfig.selectedAccount
                && currentProxy != null
                && ProxyEndpointKey.matchesLiveStage(currentProxy, endpointKey)
                && !isCurrentProxyUsable(currentProxy, now);
        if (candidate && shouldKeepConnectionNotStartedTelemetryOnlyByDnsOutage(currentProxy, normalized, now)) {
            logRotation("decision=telemetry_only reason=previous_dns_outage phase=" + normalized + " endpoint=" + endpointKey + " host=" + dnsHost(currentProxy) + " failures=" + dnsOutageFailures(currentProxy, now));
            logControl("decision=telemetry_only reason=previous_dns_outage phase=" + normalized + " endpoint=" + endpointKey + " host=" + dnsHost(currentProxy) + " failures=" + dnsOutageFailures(currentProxy, now));
            return false;
        }
        if (!ProxyPhasePolicy.isPunitiveFailure(normalized)) {
            logRotation("decision=ignored_non_punitive phase=" + normalized + " endpoint=" + endpointKey);
            return false;
        }
        if (currentProxy != null && ProxyEndpointKey.matchesLiveStage(currentProxy, endpointKey) && isCurrentProxyUsable(currentProxy, now)) {
            if (ProxyHealthStore.hasFreshUsableSuccess(currentProxy, now)) {
                logRotation("decision=held_by_usable_success phase=" + normalized + " endpoint=" + endpointKey + " held_by=" + heldByUsablePhase(currentProxy, now));
            } else {
                logRotation("decision=held_by_current_proxy_usable phase=" + normalized + " endpoint=" + endpointKey + " held_by=" + heldByCurrentProxyPhase(currentProxy, now));
            }
            return false;
        }
        if (candidate && shouldHoldHostResolveFailureByDnsOutage(currentProxy, normalized, now)) {
            logRotation("decision=dns_outage_hold phase=" + normalized + " endpoint=" + endpointKey + " host=" + dnsHost(currentProxy) + " failures=" + dnsOutageFailures(currentProxy, now));
            logControl("decision=dns_outage_hold phase=" + normalized + " endpoint=" + endpointKey + " host=" + dnsHost(currentProxy) + " failures=" + dnsOutageFailures(currentProxy, now));
            return false;
        }
        ProxyHealthStore.EndpointFailureResult failure = candidate
                ? ProxyHealthStore.lastFailureResult(currentProxy, normalized, now)
                : ProxyHealthStore.EndpointFailureResult.noop(normalized);
        boolean result = candidate && failure.rotationAllowed;
        if (result) {
            ProxyHealthStore.quarantineExactEndpoint(currentProxy, normalized, now);
            ProxyHealthStore.ignoreEndpointTelemetry(endpointKey, now);
            ProxyCheckScheduler.cancelEndpointAttempts(endpointKey);
            logRotation("decision=trigger phase=" + normalized + " endpoint=" + endpointKey + " count=" + failure.rotationFailures + " required=" + ProxyHealthStore.punitiveFailuresToRotate());
        } else if (candidate) {
            logRotation("decision=waiting_hysteresis phase=" + normalized + " endpoint=" + endpointKey + " count=" + failure.rotationFailures + " required=" + ProxyHealthStore.punitiveFailuresToRotate());
            logControl("decision=held_by_failure_hysteresis phase=" + normalized + " endpoint=" + endpointKey + " failures=" + failure.rotationFailures);
        } else {
            logRotation("decision=fallback_not_scheduled phase=" + normalized + " endpoint=" + endpointKey);
            logControl("decision=fallback_not_scheduled phase=" + normalized + " endpoint=" + endpointKey + " failures=" + failure.rotationFailures);
        }
        return result;
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

    private static void rememberDnsResolveFailurePhase(SharedConfig.ProxyInfo proxyInfo, String phase, long now) {
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
        String key = normalizeDnsHost(proxyInfo.address);
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

    private static boolean shouldHoldHostResolveFailureByDnsOutage(SharedConfig.ProxyInfo proxyInfo, String phase, long now) {
        return proxyInfo != null
                && ProxyCheckDiagnostics.HOST_RESOLVE_FAILED.equals(ProxyCheckDiagnostics.normalize(phase))
                && isDnsGlobalOutage(proxyInfo.address, now);
    }

    private static int dnsOutageFailures(SharedConfig.ProxyInfo proxyInfo, long now) {
        if (proxyInfo == null) {
            return 0;
        }
        String key = normalizeDnsHost(proxyInfo.address);
        synchronized (dnsOutageStates) {
            DnsOutageState state = dnsOutageStates.get(key);
            if (state == null || now - state.windowStartedAtMs > DNS_OUTAGE_WINDOW_MS) {
                return 0;
            }
            return state.failures;
        }
    }

    private static String dnsHost(SharedConfig.ProxyInfo proxyInfo) {
        return proxyInfo == null ? "" : normalizeDnsHost(proxyInfo.address);
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

    private static void logControl(String message) {
        if (BuildVars.LOGS_ENABLED) {
            FileLog.d("proxy_control " + message);
        }
    }

    private static void logRotation(String message) {
        if (BuildVars.LOGS_ENABLED) {
            FileLog.d("proxy_rotation " + message);
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

}
