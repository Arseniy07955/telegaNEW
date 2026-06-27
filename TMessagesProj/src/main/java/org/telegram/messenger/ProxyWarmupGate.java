package org.telegram.messenger;

import android.os.SystemClock;

import java.util.Locale;

public final class ProxyWarmupGate {

    public enum ProxyWarmupState {
        COLD,
        WARMING,
        USABLE,
        DEGRADED
    }

    public enum NetworkRequestClass {
        INIT_CRITICAL,
        USER_VISIBLE,
        MEDIA_VISIBLE,
        MEDIA_PREFETCH,
        STORIES_PREFETCH,
        STICKER_PREFETCH,
        CONTACTS_SYNC,
        BACKGROUND_REFRESH;

        static NetworkRequestClass fromOrdinal(int requestClass) {
            NetworkRequestClass[] values = values();
            if (requestClass < 0 || requestClass >= values.length) {
                return BACKGROUND_REFRESH;
            }
            return values[requestClass];
        }
    }

    private static final int PRE_USABLE_MEDIA_LIMIT = 1;
    private static final int PRE_USABLE_PREFETCH_LIMIT = 0;
    private static final int USABLE_RAMP_STEP_MS = 400;
    private static final int USABLE_RAMP_INITIAL_LIMIT = 2;
    private static final int USABLE_RAMP_SECOND_LIMIT = 4;
    private static final int USABLE_RAMP_SECOND_AT_MS = 2000;
    private static final int USABLE_RAMP_STABLE_MS = 5000;
    private static final int PRE_USABLE_PREFETCH_DELAY_MS = 1500;
    private static final String DECISION_ALLOW = "decision=allow";
    private static final String DECISION_DELAY = "decision=delay";
    private static final String DECISION_RAMP = "decision=ramp";

    private static ProxyWarmupState state = ProxyWarmupState.COLD;
    private static String endpointKey;
    private static long usableAtMs = -1;
    private static long lastStateChangeAtMs = -1;
    private static int delayedRequests;

    private ProxyWarmupGate() {
    }

    public static boolean canStartNetworkHeavyOperation(int account, int dcId, int requestClass) {
        return canStartNetworkHeavyOperation(account, dcId, NetworkRequestClass.fromOrdinal(requestClass));
    }

    public static synchronized boolean canStartNetworkHeavyOperation(int account, int dcId, NetworkRequestClass requestClass) {
        if (!isMtProxyEnabledForWarmup()) {
            return true;
        }
        long now = SystemClock.elapsedRealtime();
        ProxyWarmupState currentState = currentStateLocked(now);
        NetworkRequestClass normalizedClass = normalizeClass(requestClass);
        if (normalizedClass == NetworkRequestClass.INIT_CRITICAL) {
            logDecision(currentState, DECISION_ALLOW, normalizedClass, account, dcId, 0, 0, 0);
            return true;
        }
        if (currentState != ProxyWarmupState.USABLE && isBlockedBeforeUsable(normalizedClass)) {
            delayedRequests++;
            logDecision(currentState, DECISION_DELAY, normalizedClass, account, dcId, PRE_USABLE_PREFETCH_DELAY_MS, 0, 0);
            return false;
        }
        if (currentState == ProxyWarmupState.USABLE && isRampLimitedLocked(now)) {
            logDecision(currentState, DECISION_RAMP, normalizedClass, account, dcId, 0, 0, maxForUsableAgeLocked(now, Integer.MAX_VALUE));
        } else if (currentState != ProxyWarmupState.USABLE) {
            logDecision(currentState, DECISION_ALLOW, normalizedClass, account, dcId, 0, 0, PRE_USABLE_MEDIA_LIMIT);
        }
        return true;
    }

    public static long delayForNetworkHeavyOperation(int account, int dcId, int requestClass) {
        return delayForNetworkHeavyOperation(account, dcId, NetworkRequestClass.fromOrdinal(requestClass));
    }

    public static synchronized long delayForNetworkHeavyOperation(int account, int dcId, NetworkRequestClass requestClass) {
        if (!isMtProxyEnabledForWarmup()) {
            return 0;
        }
        long now = SystemClock.elapsedRealtime();
        ProxyWarmupState currentState = currentStateLocked(now);
        NetworkRequestClass normalizedClass = normalizeClass(requestClass);
        if (currentState != ProxyWarmupState.USABLE && isBlockedBeforeUsable(normalizedClass)) {
            logDecision(currentState, DECISION_DELAY, normalizedClass, account, dcId, PRE_USABLE_PREFETCH_DELAY_MS, 0, 0);
            return PRE_USABLE_PREFETCH_DELAY_MS;
        }
        if (currentState != ProxyWarmupState.USABLE || isRampLimitedLocked(now)) {
            return USABLE_RAMP_STEP_MS;
        }
        return 0;
    }

    public static synchronized int maxActiveMediaRequestsPerEndpoint(int account, int normalLimit, NetworkRequestClass requestClass) {
        return fanoutLimitLocked(account, 0, normalLimit, normalizeClass(requestClass), false);
    }

    public static synchronized int maxUploadGetFileOffsetsPerFile(int account, int normalLimit, NetworkRequestClass requestClass) {
        return fanoutLimitLocked(account, 0, normalLimit, normalizeClass(requestClass), true);
    }

    public static synchronized boolean isMtProxyStartupFanoutLimited(int account) {
        if (!isMtProxyEnabledForWarmup()) {
            return false;
        }
        long now = SystemClock.elapsedRealtime();
        ProxyWarmupState currentState = currentStateLocked(now);
        return currentState != ProxyWarmupState.USABLE || isRampLimitedLocked(now);
    }

    public static synchronized void onProxyLivePhase(String endpoint, String phase, long now) {
        if (!isMtProxyEnabledForWarmup() || ProxyPhasePolicy.isProxyUsableSuccessPhase(phase)) {
            return;
        }
        if (!ProxyPhasePolicy.isLivePhase(phase) || currentStateLocked(now) == ProxyWarmupState.USABLE) {
            return;
        }
        endpointKey = endpoint;
        state = ProxyWarmupState.WARMING;
        lastStateChangeAtMs = now;
    }

    public static void onProxyUsable(String endpoint, long now) {
        synchronized (ProxyWarmupGate.class) {
            endpointKey = endpoint;
            state = ProxyWarmupState.USABLE;
            usableAtMs = now;
            lastStateChangeAtMs = now;
            logReleaseDelayedLocked();
        }
    }

    public static void onProxyFailure(String endpoint, String phase, long now) {
        String normalized = ProxyCheckDiagnostics.normalize(phase);
        if (!ProxyPhasePolicy.isPunitiveFailure(normalized)) {
            return;
        }
        synchronized (ProxyWarmupGate.class) {
            if (!isMtProxyEnabledForWarmup() || ProxyHealthStore.hasFreshUsableSuccess(SharedConfig.currentProxy, now)) {
                return;
            }
            endpointKey = endpoint;
            state = ProxyWarmupState.DEGRADED;
            lastStateChangeAtMs = now;
            logDecision(state, DECISION_DELAY, NetworkRequestClass.BACKGROUND_REFRESH, UserConfig.selectedAccount, 0, USABLE_RAMP_STEP_MS, 0, 0);
        }
    }

    public static synchronized String stateName() {
        return currentStateLocked(SystemClock.elapsedRealtime()).name().toLowerCase(Locale.US);
    }

    private static int fanoutLimitLocked(int account, int dcId, int normalLimit, NetworkRequestClass requestClass, boolean uploadGetFile) {
        if (normalLimit <= 0 || !isMtProxyEnabledForWarmup()) {
            return normalLimit;
        }
        long now = SystemClock.elapsedRealtime();
        ProxyWarmupState currentState = currentStateLocked(now);
        if (currentState != ProxyWarmupState.USABLE) {
            int limit = isBlockedBeforeUsable(requestClass) ? PRE_USABLE_PREFETCH_LIMIT : PRE_USABLE_MEDIA_LIMIT;
            if (limit <= 0) {
                delayedRequests++;
                logDecision(currentState, DECISION_DELAY, requestClass, account, dcId, PRE_USABLE_PREFETCH_DELAY_MS, 0, 0);
            } else {
                logDecision(currentState, DECISION_ALLOW, requestClass, account, dcId, 0, 0, limit);
            }
            return Math.min(normalLimit, limit);
        }
        int limit = maxForUsableAgeLocked(now, normalLimit);
        if (limit < normalLimit) {
            logDecision(currentState, DECISION_RAMP, requestClass, account, dcId, 0, uploadGetFile ? 0 : -1, limit);
        }
        return limit;
    }

    private static ProxyWarmupState currentStateLocked(long now) {
        long age = lastUsableSuccessAgeLocked(now);
        if (age >= 0) {
            if (state != ProxyWarmupState.USABLE) {
                state = ProxyWarmupState.USABLE;
                usableAtMs = now - age;
                lastStateChangeAtMs = usableAtMs;
            }
            return ProxyWarmupState.USABLE;
        }
        if (state == ProxyWarmupState.USABLE && usableAtMs >= 0) {
            return ProxyWarmupState.USABLE;
        }
        return state;
    }

    private static boolean isRampLimitedLocked(long now) {
        if (usableAtMs < 0) {
            long age = lastUsableSuccessAgeLocked(now);
            if (age < 0) {
                return true;
            }
            usableAtMs = now - age;
        }
        return now - usableAtMs < USABLE_RAMP_STABLE_MS;
    }

    private static int maxForUsableAgeLocked(long now, int normalLimit) {
        if (normalLimit <= 0) {
            return normalLimit;
        }
        if (usableAtMs < 0) {
            long age = lastUsableSuccessAgeLocked(now);
            if (age < 0) {
                return Math.min(normalLimit, PRE_USABLE_MEDIA_LIMIT);
            }
            usableAtMs = now - age;
        }
        long age = Math.max(0, now - usableAtMs);
        if (age >= USABLE_RAMP_STABLE_MS) {
            return normalLimit;
        }
        if (age >= USABLE_RAMP_SECOND_AT_MS) {
            return Math.min(normalLimit, USABLE_RAMP_SECOND_LIMIT);
        }
        return Math.min(normalLimit, USABLE_RAMP_INITIAL_LIMIT);
    }

    private static long lastUsableSuccessAgeLocked(long now) {
        return ProxyHealthStore.lastUsableSuccessAgeMs(SharedConfig.currentProxy, now);
    }

    private static boolean isBlockedBeforeUsable(NetworkRequestClass requestClass) {
        switch (requestClass) {
            case MEDIA_PREFETCH:
            case STORIES_PREFETCH:
            case STICKER_PREFETCH:
            case CONTACTS_SYNC:
            case BACKGROUND_REFRESH:
                return true;
            default:
                return false;
        }
    }

    private static NetworkRequestClass normalizeClass(NetworkRequestClass requestClass) {
        return requestClass == null ? NetworkRequestClass.BACKGROUND_REFRESH : requestClass;
    }

    private static boolean isMtProxyEnabledForWarmup() {
        SharedConfig.ProxyInfo currentProxy = SharedConfig.currentProxy;
        return SharedConfig.isProxyEnabled()
                && currentProxy != null
                && currentProxy.secret != null
                && currentProxy.secret.length() > 0
                && !currentProxy.isWssTransport();
    }

    private static void logReleaseDelayedLocked() {
        if (BuildVars.LOGS_ENABLED) {
            FileLog.d("proxy_warmup state=usable decision=release_delayed count=" + delayedRequests + " endpoint=" + endpointKey);
        }
        delayedRequests = 0;
    }

    private static void logDecision(ProxyWarmupState warmupState, String decision, NetworkRequestClass requestClass, int account, int dcId, long delay, int active, int max) {
        if (!BuildVars.LOGS_ENABLED) {
            return;
        }
        StringBuilder builder = new StringBuilder("proxy_warmup state=");
        builder.append(warmupState.name().toLowerCase(Locale.US));
        builder.append(" ").append(decision);
        builder.append(" class=").append(requestClass.name().toLowerCase(Locale.US));
        builder.append(" account=").append(account);
        if (dcId != 0) {
            builder.append(" dc=").append(dcId);
        }
        if (delay > 0) {
            builder.append(" delay=").append(delay);
        }
        if (active >= 0) {
            builder.append(" active=").append(active);
        }
        if (max >= 0 && max != Integer.MAX_VALUE) {
            builder.append(" max=").append(max);
        }
        if (endpointKey != null) {
            builder.append(" endpoint=").append(endpointKey);
        }
        if (lastStateChangeAtMs > 0) {
            builder.append(" state_age=").append(Math.max(0, SystemClock.elapsedRealtime() - lastStateChangeAtMs));
        }
        FileLog.d(builder.toString());
    }
}
