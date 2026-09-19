package org.telegram.messenger;

import android.os.SystemClock;

import org.telegram.tgnet.ConnectionsManager;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Подбирает рабочий прокси, не дожидаясь пользователя.
 *
 * Штатная ротация ({@link ProxyRotationController}) переключается вслепую: она
 * берёт следующего кандидата по списку, потому что пинги меряются только когда
 * открыт экран прокси. Здесь кандидаты сначала проверяются — параллельно с
 * точки зрения вызывающего, фактически через общую очередь
 * {@link ProxyCheckScheduler} с её интервалами, — и клиент садится на первую
 * ответившую точку, приоритетные пробуются раньше остальных.
 *
 * Проверки идут только тогда, когда они могут что-то изменить: соединение не
 * поднялось, либо пинги устарели настолько, что следующий перебор снова был бы
 * вслепую. Точки в бэкоффе не трогаются — иначе это спам по эндпоинтам, ради
 * чего нативные ограничители и существуют.
 */
public final class ProxyAutoSelector implements NotificationCenter.NotificationCenterDelegate {

    private static final ProxyAutoSelector INSTANCE = new ProxyAutoSelector();

    // Сколько ждём обычного коннекта, прежде чем лезть проверять кандидатов.
    private static final long PROBE_AFTER_CONNECTING_MS = 4_000L;
    // Как часто освежать пинги, пока приложение живо.
    private static final long REFRESH_INTERVAL_MS = 20 * 60 * 1000L;
    // Сколько точек берём в один заход: очередь последовательная, длинный
    // список растянулся бы на минуты и потерял смысл.
    private static final int MAX_CANDIDATES_PER_SWEEP = 4;
    // Верхняя граница одного захода: очередь последовательная, с интервалами.
    private static final long SWEEP_TIMEOUT_MS = 90_000L;

    private final Object checkOwner = new Object();
    private Runnable scheduledProbe;
    private Runnable scheduledRefresh;
    private boolean sweepInProgress;
    private long lastSweepAtMs;

    public static void init() {
        INSTANCE.initInternal();
    }

    private void initInternal() {
        for (int i = 0; i < UserConfig.MAX_ACCOUNT_COUNT; i++) {
            NotificationCenter.getInstance(i).addObserver(this, NotificationCenter.didUpdateConnectionState);
        }
        scheduleRefresh();
    }

    @Override
    public void didReceivedNotification(int id, int account, Object... args) {
        if (id != NotificationCenter.didUpdateConnectionState || account != UserConfig.selectedAccount) {
            return;
        }
        int state = ConnectionsManager.getInstance(account).getConnectionState();
        if (state == ConnectionsManager.ConnectionStateConnected || state == ConnectionsManager.ConnectionStateUpdating) {
            cancelScheduledProbe();
            return;
        }
        if (state != ConnectionsManager.ConnectionStateConnectingToProxy) {
            return;
        }
        if (!SharedConfig.isProxyEnabled() || SharedConfig.proxyList.size() <= 1) {
            return;
        }
        if (scheduledProbe != null) {
            return;
        }
        scheduledProbe = () -> {
            scheduledProbe = null;
            startSweep(true);
        };
        AndroidUtilities.runOnUIThread(scheduledProbe, PROBE_AFTER_CONNECTING_MS);
        log("probe scheduled delay_ms=" + PROBE_AFTER_CONNECTING_MS);
    }

    private void cancelScheduledProbe() {
        if (scheduledProbe != null) {
            AndroidUtilities.cancelRunOnUIThread(scheduledProbe);
            scheduledProbe = null;
        }
    }

    private void scheduleRefresh() {
        if (scheduledRefresh != null) {
            AndroidUtilities.cancelRunOnUIThread(scheduledRefresh);
        }
        scheduledRefresh = () -> {
            scheduledRefresh = null;
            // Освежаем пинги без переключения: цель — чтобы следующий перебор
            // знал живые точки, а не гадал по порядку списка.
            startSweep(false);
            scheduleRefresh();
        };
        AndroidUtilities.runOnUIThread(scheduledRefresh, REFRESH_INTERVAL_MS);
    }

    private void startSweep(boolean switchWhenFound) {
        if (sweepInProgress) {
            return;
        }
        if (SharedConfig.proxyList.isEmpty()) {
            return;
        }
        if (!switchWhenFound && !SharedConfig.isProxyEnabled()) {
            // Фоновое освежение нужно только тем, кто прокси реально пользуется.
            return;
        }
        List<SharedConfig.ProxyInfo> candidates = collectCandidates();
        if (candidates.isEmpty()) {
            log("sweep skipped no_candidates switch=" + switchWhenFound);
            return;
        }

        sweepInProgress = true;
        lastSweepAtMs = SystemClock.elapsedRealtime();
        int enqueued = 0;
        // Очередь сообщает о завершении один раз на владельца, поэтому здесь
        // не счётчик, а один общий колбэк на весь заход.
        ProxyCheckScheduler.Callback callback = new ProxyCheckScheduler.Callback() {
            @Override
            public void onProxyChecked(SharedConfig.ProxyInfo proxyInfo, long time, String diagnostic) {
                if (!proxyInfo.available) {
                    log("probe failed endpoint=" + endpoint(proxyInfo) + " diagnostic=" + diagnostic);
                    return;
                }
                log("probe ok endpoint=" + endpoint(proxyInfo) + " ping=" + time);
                if (switchWhenFound) {
                    switchIfStillNeeded(proxyInfo);
                }
            }

            @Override
            public void onProxyCheckQueueFinished() {
                sweepInProgress = false;
                log("sweep finished");
            }
        };
        for (SharedConfig.ProxyInfo candidate : candidates) {
            if (ProxyCheckScheduler.enqueueNow(UserConfig.selectedAccount, candidate, checkOwner, callback)) {
                enqueued++;
            }
        }
        if (enqueued == 0) {
            sweepInProgress = false;
            log("sweep skipped nothing_enqueued switch=" + switchWhenFound);
            return;
        }
        log("sweep start candidates=" + enqueued + " switch=" + switchWhenFound);
        // Страховка: если колбэк завершения по какой-то причине не придёт,
        // следующий заход не должен быть заблокирован навсегда.
        AndroidUtilities.runOnUIThread(() -> {
            if (sweepInProgress && SystemClock.elapsedRealtime() - lastSweepAtMs >= SWEEP_TIMEOUT_MS) {
                sweepInProgress = false;
                log("sweep timeout");
            }
        }, SWEEP_TIMEOUT_MS);
    }

    /**
     * Кандидаты в порядке, в котором их стоит пробовать: приоритетные точки
     * первыми, затем по последнему известному пингу, в конце — вовсе не
     * проверявшиеся. Текущая точка и точки в бэкоффе пропускаются.
     */
    private List<SharedConfig.ProxyInfo> collectCandidates() {
        long now = SystemClock.elapsedRealtime();
        List<SharedConfig.ProxyInfo> candidates = new ArrayList<>();
        for (SharedConfig.ProxyInfo info : SharedConfig.proxyList) {
            if (info == null || info == SharedConfig.currentProxy || info.checking) {
                continue;
            }
            if (ProxyRuntimeStateStore.isEndpointBackedOff(info)
                    || ProxyRuntimeStateStore.nextAllowedCheckTime(info) > now
                    || ProxyCheckScheduler.isFresh(info)) {
                continue;
            }
            candidates.add(info);
        }
        Collections.sort(candidates, (o1, o2) -> {
            int byPriority = Boolean.compare(SharedConfig.isPriorityProxy(o2), SharedConfig.isPriorityProxy(o1));
            if (byPriority != 0) {
                return byPriority;
            }
            // Непроверенные точки не должны выигрывать у быстрых из-за ping == 0.
            long ping1 = o1.ping > 0 ? o1.ping : Long.MAX_VALUE;
            long ping2 = o2.ping > 0 ? o2.ping : Long.MAX_VALUE;
            return Long.compare(ping1, ping2);
        });
        if (candidates.size() > MAX_CANDIDATES_PER_SWEEP) {
            return new ArrayList<>(candidates.subList(0, MAX_CANDIDATES_PER_SWEEP));
        }
        return candidates;
    }

    private void switchIfStillNeeded(SharedConfig.ProxyInfo proxyInfo) {
        int state = ConnectionsManager.getInstance(UserConfig.selectedAccount).getConnectionState();
        if (state == ConnectionsManager.ConnectionStateConnected || state == ConnectionsManager.ConnectionStateUpdating) {
            log("switch skipped already_connected endpoint=" + endpoint(proxyInfo));
            return;
        }
        if (proxyInfo == SharedConfig.currentProxy) {
            return;
        }
        if (ProxyRuntimeStateStore.isCurrentProxyUsable(SharedConfig.currentProxy)) {
            log("switch skipped current_usable endpoint=" + endpoint(proxyInfo));
            return;
        }
        log("switch endpoint=" + endpoint(proxyInfo) + " ping=" + proxyInfo.ping);
        ProxyRotationController.applyProxy(proxyInfo, "auto_selector");
    }

    private static String endpoint(SharedConfig.ProxyInfo proxyInfo) {
        return proxyInfo == null ? "null" : proxyInfo.address + ":" + proxyInfo.port;
    }

    private static void log(String message) {
        if (BuildVars.LOGS_ENABLED) {
            FileLog.d("proxy_auto " + message);
        }
    }
}
