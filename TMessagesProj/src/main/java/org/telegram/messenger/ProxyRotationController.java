package org.telegram.messenger;

import android.content.SharedPreferences;
import org.telegram.tgnet.ConnectionsManager;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

public class ProxyRotationController implements NotificationCenter.NotificationCenterDelegate {
    private final static ProxyRotationController INSTANCE = new ProxyRotationController();

    public final static int DEFAULT_TIMEOUT_INDEX = 1;
    private final static long TERMINAL_STAGE_SWITCH_DELAY_MS = 900L;
    public final static List<Integer> ROTATION_TIMEOUTS = Arrays.asList(
            5, 10, 15, 30, 60
    );

    private boolean isCheckScheduled;

    private Runnable checkProxyAndSwitchRunnable = () -> {
        isCheckScheduled = false;
        log("scheduled_check skipped background_disabled");
        switchToAvailable();
    };

    public static void init() {
        INSTANCE.initInternal();
    }

    @SuppressWarnings("ComparatorCombinators")
    private void switchToAvailable() {
        if (!SharedConfig.proxyRotationEnabled) {
            log("skip_switch rotation_disabled");
            return;
        }

        SharedConfig.ProxyInfo info = selectFreshAvailableCandidate();
        String switchReason = "fresh";
        if (info == null) {
            info = selectFallbackCandidate();
            switchReason = "fallback";
        }
        if (info == null) {
            log("no_candidate");
            return;
        }

        switchToProxy(info, switchReason);
    }

    private SharedConfig.ProxyInfo selectFreshAvailableCandidate() {
        List<SharedConfig.ProxyInfo> sortedList = new ArrayList<>(SharedConfig.proxyList);
        Collections.sort(sortedList, (o1, o2) -> Long.compare(o1.ping, o2.ping));
        for (SharedConfig.ProxyInfo info : sortedList) {
            if (!isSwitchableCandidate(info) || !info.available || !ProxyCheckScheduler.isFresh(info)) {
                continue;
            }
            return info;
        }
        return null;
    }

    private SharedConfig.ProxyInfo selectFallbackCandidate() {
        int count = SharedConfig.proxyList.size();
        int currentIndex = SharedConfig.currentProxy != null ? SharedConfig.proxyList.indexOf(SharedConfig.currentProxy) : -1;
        for (int offset = 1; offset <= count; offset++) {
            int index = (currentIndex + offset + count) % count;
            SharedConfig.ProxyInfo info = SharedConfig.proxyList.get(index);
            if (isSwitchableCandidate(info)) {
                return info;
            }
        }
        return null;
    }

    private boolean isSwitchableCandidate(SharedConfig.ProxyInfo info) {
        return info != null
                && info != SharedConfig.currentProxy
                && !info.checking
                && !ProxyCheckDiagnostics.hasFreshFailure(info)
                && !ProxyCheckDiagnostics.hasFreshEndpointCooldown(info)
                && !ProxyCheckDiagnostics.hasFreshUnresolvedLivePhase(info)
                && !ProxyCheckScheduler.isEndpointBackedOff(info);
    }

    private void switchToProxy(SharedConfig.ProxyInfo info, String reason) {
        SharedPreferences.Editor editor = MessagesController.getGlobalMainSettings().edit();
        editor.putString("proxy_ip", info.address);
        editor.putString("proxy_pass", info.password);
        editor.putString("proxy_user", info.username);
        editor.putInt("proxy_port", info.port);
        editor.putString("proxy_secret", info.secret);
        editor.putBoolean("proxy_enabled", true);

        if (!info.secret.isEmpty()) {
            editor.putBoolean("proxy_enabled_calls", false);
        }
        editor.apply();

        SharedConfig.currentProxy = info;
        ProxyCheckScheduler.markConnectionStarting(info);
        NotificationCenter.getGlobalInstance().postNotificationName(NotificationCenter.proxyChangedByRotation);
        NotificationCenter.getGlobalInstance().postNotificationName(NotificationCenter.proxySettingsChanged);
        ConnectionsManager.setProxySettings(true, SharedConfig.currentProxy.address, SharedConfig.currentProxy.port, SharedConfig.currentProxy.username, SharedConfig.currentProxy.password, SharedConfig.currentProxy.secret);
        if ("fallback".equals(reason)) {
            log("switch fallback endpoint=" + endpoint(info) + " ping=" + info.ping);
        } else {
            log("switch fresh endpoint=" + endpoint(info) + " ping=" + info.ping);
        }
    }

    private void initInternal() {
        for (int i = 0; i < UserConfig.MAX_ACCOUNT_COUNT; i++) {
            NotificationCenter.getInstance(i).addObserver(this, NotificationCenter.didUpdateConnectionState);
            NotificationCenter.getInstance(i).addObserver(this, NotificationCenter.proxyConnectionStageChanged);
        }
        NotificationCenter.getGlobalInstance().addObserver(this, NotificationCenter.proxySettingsChanged);
    }

    @Override
    public void didReceivedNotification(int id, int account, Object... args) {
        if (id == NotificationCenter.proxySettingsChanged) {
            AndroidUtilities.cancelRunOnUIThread(checkProxyAndSwitchRunnable);
            isCheckScheduled = false;
            log("cancel settings_changed");
        } else if (id == NotificationCenter.didUpdateConnectionState && account == UserConfig.selectedAccount) {
            if (!SharedConfig.isProxyEnabled() || !SharedConfig.proxyRotationEnabled || SharedConfig.proxyList.size() <= 1) {
                return;
            }

            int state = ConnectionsManager.getInstance(account).getConnectionState();

            if (state == ConnectionsManager.ConnectionStateConnectingToProxy) {
                if (!isCheckScheduled) {
                    log("schedule_after_connecting timeout_s=" + ROTATION_TIMEOUTS.get(SharedConfig.proxyRotationTimeout));
                    scheduleSwitch(ROTATION_TIMEOUTS.get(SharedConfig.proxyRotationTimeout) * 1000L, "connecting_timeout");
                }
            } else {
                if ((state == ConnectionsManager.ConnectionStateConnected || state == ConnectionsManager.ConnectionStateUpdating) && SharedConfig.currentProxy != null) {
                    ProxyCheckScheduler.markConnected(SharedConfig.currentProxy);
                }
                AndroidUtilities.cancelRunOnUIThread(checkProxyAndSwitchRunnable);
                isCheckScheduled = false;
                log("cancel state=" + state);
            }
        } else if (id == NotificationCenter.proxyConnectionStageChanged && account == UserConfig.selectedAccount) {
            if (!SharedConfig.isProxyEnabled() || !SharedConfig.proxyRotationEnabled || SharedConfig.proxyList.size() <= 1) {
                return;
            }
            if (args == null || args.length == 0 || !(args[0] instanceof String)) {
                return;
            }
            String diagnostic = (String) args[0];
            if (args.length < 2 || !(args[1] instanceof String) || !ProxyCheckScheduler.matchesEndpointStageKey(SharedConfig.currentProxy, (String) args[1])) {
                return;
            }
            if (!ProxyCheckDiagnostics.shouldAccelerateProxyRotation(diagnostic)) {
                return;
            }
            int state = ConnectionsManager.getInstance(account).getConnectionState();
            if (state != ConnectionsManager.ConnectionStateConnectingToProxy) {
                return;
            }
            log("schedule_after_stage phase=" + ProxyCheckDiagnostics.normalize(diagnostic) + " delay_ms=" + TERMINAL_STAGE_SWITCH_DELAY_MS);
            scheduleSwitch(TERMINAL_STAGE_SWITCH_DELAY_MS, "terminal_stage");
        }
    }

    private void scheduleSwitch(long delayMs, String reason) {
        AndroidUtilities.cancelRunOnUIThread(checkProxyAndSwitchRunnable);
        isCheckScheduled = true;
        AndroidUtilities.runOnUIThread(checkProxyAndSwitchRunnable, delayMs);
        log("scheduled_switch reason=" + reason + " delay_ms=" + delayMs);
    }

    private void log(String message) {
        if (BuildVars.LOGS_ENABLED) {
            FileLog.d("proxy_rotation " + message);
        }
    }

    private String endpoint(SharedConfig.ProxyInfo proxyInfo) {
        if (proxyInfo == null) {
            return "null";
        }
        return proxyInfo.address + ":" + proxyInfo.port;
    }
}
