#!/usr/bin/env python3
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

SCHEDULER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckScheduler.java"
PROXY_LIST = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProxyListActivity.java"
PROXY_SETTINGS = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProxySettingsActivity.java"
ANDROID_UTILITIES = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/AndroidUtilities.java"
ROTATION = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyRotationController.java"
JAVA_MANAGER = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"
DIAGNOSTICS = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckDiagnostics.java"
README = ROOT / "README.md"

checks = [
    (SCHEDULER, "PROXY_CHECK_SPACING_MS", "scheduler must space background proxy checks"),
    (SCHEDULER, "PROXY_CHECK_FAILURE_BACKOFF_MS", "scheduler must back off repeated failed endpoint checks"),
    (SCHEDULER, "PROXY_CHECK_LIVE_FAILURE_DEDUP_MS", "scheduler must deduplicate repeated live terminal stages from the same native close"),
    (SCHEDULER, "PROXY_CHECK_CONNECTED_GRACE_MS", "scheduler must avoid rechecking a recently connected endpoint"),
    (SCHEDULER, "activeRequest", "scheduler must keep a single active background check"),
    (SCHEDULER, "EndpointState", "scheduler must keep per-endpoint check state outside mutable ProxyInfo rows"),
    (SCHEDULER, "endpointStates", "scheduler must remember endpoint cooldowns across UI/rotation sweeps"),
    (SCHEDULER, "enqueueStale", "scheduler must expose stale-check enqueueing"),
    (SCHEDULER, "enqueueNow", "scheduler must expose priority manual checks so GUI does not bypass the shared queue"),
    (SCHEDULER, "owner == null", "scheduler must reject ownerless checks because they cannot be cancelled or drained reliably"),
    (SCHEDULER, "isFresh", "scheduler must expose one freshness policy for UI and rotation"),
    (SCHEDULER, "markConnected", "scheduler must expose a single path for real connected-state observations"),
    (SCHEDULER, "markConnectionStarting", "scheduler must expose a single path for explicit current-proxy reconnect attempts"),
    (SCHEDULER, "markConnectionUsable", "scheduler must expose a concrete native-success path that clears stale endpoint backoff"),
    (SCHEDULER, "markEndpointFailure", "scheduler must expose a single path for real current-connection endpoint failures"),
    (SCHEDULER, "nextAllowedCheckTime", "scheduler must expose a single debounce policy for UI and rotation"),
    (SCHEDULER, "isEndpointBackedOff", "scheduler must expose endpoint backoff state so rotation cannot bypass it"),
    (SCHEDULER, "rememberCheckResult", "scheduler must update endpoint cooldowns from measured check results"),
    (SCHEDULER, "displayDiagnosticForResult", "scheduler must translate repeated TCP failures into a user-facing network-block phase"),
    (SCHEDULER, "skip_backoff", "scheduler must log when a repeated endpoint check is intentionally suppressed"),
    (SCHEDULER, "endpointKey", "scheduler must deduplicate checks by proxy endpoint, not ProxyInfo object identity"),
    (SCHEDULER, "endpointNetworkKey", "scheduler must keep host/port state for pre-TLS endpoint failures"),
    (SCHEDULER, "endpointStateKeyForDiagnostic", "scheduler must choose endpoint backoff key by failure phase"),
    (SCHEDULER, "toLowerCase(Locale.US)", "scheduler endpoint key must normalize host names without device-locale surprises"),
    (SCHEDULER, "normalizeKeyPart", "scheduler endpoint key must handle null endpoint fields before lowercasing"),
    (SCHEDULER, "appendKeyPart", "scheduler endpoint key must encode fields without delimiter collisions"),
    (SCHEDULER, "attachPending", "scheduler must attach GUI listeners to an existing endpoint check instead of starting duplicates"),
    (SCHEDULER, "attachPending(proxyInfo, owner, callback, true)", "manual checks must force-upgrade an existing queued endpoint check"),
    (SCHEDULER, "request.force = request.force || force", "attached manual listeners must upgrade pending requests to forced checks"),
    (SCHEDULER, "ArrayList<Listener>", "scheduler must support multiple owners/listeners for one endpoint check"),
    (SCHEDULER, "applyMeasuredResult", "scheduler must copy measured checked results to attached ProxyInfo instances"),
    (SCHEDULER, "appliedTimeForResult", "scheduler must normalize check results before applying them to UI state"),
    (SCHEDULER, "callbackTimeForResult", "scheduler must keep measured callback result separate from preserved connected state"),
    (SCHEDULER, "isConnectedCurrentProxy", "scheduler must not let background check failures overwrite the currently connected proxy"),
    (SCHEDULER, "nativePingId", "scheduler must keep native cancellation state outside mutable UI ProxyInfo objects"),
    (SCHEDULER, "notifyRequestFinishedIfDrained", "scheduler must notify every listener when a coalesced request is skipped or drained"),
    (SCHEDULER, "notifiedOwners", "scheduler must emit at most one drain callback per owner for a coalesced endpoint"),
    (SCHEDULER, "alreadyNotifiedOwner", "scheduler must deduplicate drain callbacks for owners with duplicate endpoint listeners"),
    (SCHEDULER, "hasActiveListenerForProxyInfo", "listener cancellation must not clear shared ProxyInfo state while another listener still owns it"),
    (SCHEDULER, "clearCancelledListenerState", "listener cancellation must clear detached UI ProxyInfo state only after checking remaining listeners"),
    (SCHEDULER, "clearDetachedCheckState", "scheduler must recover stale ProxyInfo.checking state when there is no queued or active request"),
    (SCHEDULER, "clearDetachedCheckStates", "scheduler must let passive UI screens clear stale checking state without starting checks"),
    (SCHEDULER, "clearTransientState", "scheduler must clear checking/native ping state without rewriting measured availability"),
    (SCHEDULER, "cancelOwner", "scheduler must let screens cancel queued checks"),
    (SCHEDULER, "cancelProxyCheck", "scheduler must cancel the native active check when owner is cancelled"),
    (SCHEDULER, "onProxyCheckQueueFinished", "scheduler must notify owners when their sweep is drained"),
    (SCHEDULER, "proxy_check_scheduler ", "scheduler must use a stable log prefix for UI diagnostics"),
    (SCHEDULER, "enqueue endpoint=", "scheduler must log enqueue decisions for UI diagnostics"),
    (SCHEDULER, "start endpoint=", "scheduler must log check start for UI diagnostics"),
    (SCHEDULER, "finish result=", "scheduler must log check finish for UI diagnostics"),
    (SCHEDULER, "finish_ignored", "scheduler must log late native callbacks that no longer match the active Java request"),
    (SCHEDULER, "cancel_owner", "scheduler must log owner cancellation for UI diagnostics"),
    (SCHEDULER, "proxyInfo.proxyCheckPingId == 0", "scheduler must fail fast if native checkProxy refuses to start"),
    (SCHEDULER, "force", "scheduler must support forced manual checks without abusing stale-cache state"),
    (PROXY_LIST, "ProxyCheckScheduler.clearDetachedCheckStates", "proxy list must clear stale check state without starting a full sweep"),
    (PROXY_LIST, "ProxyCheckScheduler.isFresh", "proxy list must use the shared freshness policy"),
    (PROXY_LIST, "markConnectedCurrentProxyIfNeeded", "proxy list must mark connected-state observations outside cell rendering"),
    (PROXY_LIST, "ProxyCheckScheduler.markConnectionStarting", "proxy list must clear stale visible failures when a real user-selected proxy reconnect starts"),
    (PROXY_LIST, "ProxyCheckScheduler.cancelOwner(this)", "proxy list must cancel queued checks on destroy"),
    (PROXY_SETTINGS, "ProxyCheckScheduler.markConnectionStarting(currentProxyInfo)", "proxy settings save must clear stale visible failures before applying enabled proxy settings"),
    (ANDROID_UTILITIES, "ProxyCheckScheduler.markConnectionStarting(SharedConfig.currentProxy)", "proxy link add/apply must clear stale visible failures before applying enabled proxy settings"),
    (ROTATION, "ProxyCheckScheduler.isFresh", "proxy rotation must not switch to stale availability results"),
    (ROTATION, "ProxyCheckScheduler.markConnected(SharedConfig.currentProxy)", "proxy rotation must share connected-state freshness with the scheduler"),
    (ROTATION, "ProxyCheckScheduler.markConnectionStarting(info)", "proxy rotation must publish a fresh starting phase before applying a fallback proxy"),
    (ROTATION, "selectFallbackCandidate", "proxy rotation must try one unchecked endpoint through a real connection instead of full-list proxy checks"),
    (ROTATION, "switch fallback endpoint=", "proxy rotation must log one-at-a-time fallback switches distinctly"),
    (ROTATION, "isCheckScheduled", "proxy rotation must not schedule duplicate delayed sweeps"),
    (ROTATION, "TERMINAL_STAGE_SWITCH_DELAY_MS", "proxy rotation must accelerate fallback after terminal MTProxy startup phases"),
    (ROTATION, "NotificationCenter.proxyConnectionStageChanged", "proxy rotation must observe concrete MTProxy startup stages"),
    (ROTATION, "ProxyCheckDiagnostics.shouldAccelerateProxyRotation", "proxy rotation must use the shared diagnostic map to decide terminal phases"),
    (JAVA_MANAGER, "ProxyCheckScheduler.markEndpointFailure(currentProxy, normalizedDiagnostic)", "current proxy live terminal stages must update scheduler endpoint backoff"),
    (ROTATION, "proxy_rotation ", "proxy rotation must emit stable diagnostics"),
    (ROTATION, "scheduled_check skipped background_disabled", "proxy rotation must not launch a full proxy-check sweep while connection is already trying"),
    (DIAGNOSTICS, "hasFreshEndpointCooldown", "proxy diagnostics must expose fresh endpoint cooldown as a rotation blocker"),
    (DIAGNOSTICS, "hasFreshUnresolvedLivePhase", "proxy diagnostics must expose unresolved live phases as rotation blockers"),
    (DIAGNOSTICS, "shouldAccelerateProxyRotation", "proxy diagnostics must expose terminal startup phases that should accelerate fallback rotation"),
    (ROTATION, "ProxyCheckDiagnostics.hasFreshEndpointCooldown(info)", "proxy rotation must not fallback-switch to an endpoint still in native cooldown"),
    (ROTATION, "ProxyCheckDiagnostics.hasFreshUnresolvedLivePhase(info)", "proxy rotation must not fallback-switch to an endpoint still proving its proxy data path"),
    (ROTATION, "ProxyCheckScheduler.isEndpointBackedOff(info)", "proxy rotation must not fallback-switch to an endpoint still in scheduler backoff"),
    (README, "Java backoff использует ту же фазовую идею ключей", "README must document Java scheduler phase-aware endpoint keys"),
    (README, "host:port:username:password:secret", "README must document exact-key proxy-check coalescing"),
    (README, "generic `Connected`", "README must document that generic connected-state observations do not erase fresh terminal proxy phases"),
    (README, "Явный новый старт подключения", "README must document that explicit reconnect attempts publish a fresh starting phase"),
]

failed = []
for path, needle, message in checks:
    if not path.exists():
        failed.append(f"{path.relative_to(ROOT)}: missing file")
        continue
    text = path.read_text(encoding="utf-8")
    if needle not in text:
        failed.append(f"{path.relative_to(ROOT)}: {message}")

if failed:
    print("Proxy check scheduler guard failed:")
    for item in failed:
        print(f" - {item}")
    sys.exit(1)

scheduler_text = SCHEDULER.read_text(encoding="utf-8")
if "request.proxyInfo == proxyInfo" in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: pending checks must be matched by endpoint key, not ProxyInfo object identity")
    sys.exit(1)
if "proxyInfo.address.toLowerCase(Locale.US)" in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: endpointKey must normalize null host values before lowercasing")
    sys.exit(1)
if "if (proxyInfo == null || owner == null)" not in scheduler_text or "if (proxyList == null || owner == null)" not in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: enqueueNow/enqueueStale must reject ownerless checks at the public API boundary")
    sys.exit(1)
if "long appliedTime = appliedTimeForResult(request, time);" not in scheduler_text or "long callbackTime = callbackTimeForResult(request, time);" not in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: finishRequest must separate applied state from callback result")
    sys.exit(1)
mark_connected_start = scheduler_text.find("public static void markConnected")
mark_connected_end = scheduler_text.find("public static void markEndpointFailure", mark_connected_start)
mark_connected_body = scheduler_text[mark_connected_start:mark_connected_end]
if (
    "boolean preserveFreshProxyPhase = ProxyCheckDiagnostics.hasFreshFailure(proxyInfo) || ProxyCheckDiagnostics.hasFreshLivePhase(proxyInfo);" not in mark_connected_body
    or "if (!preserveFreshProxyPhase)" not in mark_connected_body
    or mark_connected_body.find("if (!preserveFreshProxyPhase)") > mark_connected_body.find("proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.OK")
    or mark_connected_body.find("if (!preserveFreshProxyPhase)") > mark_connected_body.find("rememberConnected(proxyInfo)")
):
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: generic connected-state observations must not overwrite fresh concrete proxy phases")
    sys.exit(1)
mark_starting_start = scheduler_text.find("public static void markConnectionStarting")
mark_starting_end = scheduler_text.find("public static void markConnectionUsable", mark_starting_start)
mark_starting_body = scheduler_text[mark_starting_start:mark_starting_end]
if (
    "proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.CONNECT_START;" not in mark_starting_body
    or "proxyInfo.lastCheckDiagnosticTime = SystemClock.elapsedRealtime();" not in mark_starting_body
):
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: explicit current-proxy reconnect attempts must publish connect_start with a fresh timestamp")
    sys.exit(1)
if "finish result=\" + (effectiveTime == -1" in scheduler_text or "onProxyChecked(listener.proxyInfo, effectiveTime)" in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: callback result must not reuse preserved connected-state time")
    sys.exit(1)
if "String appliedDiagnostic = shouldPreserveConnectedState(request, time) ? ProxyCheckDiagnostics.OK : displayDiagnostic;" in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: preserved connected-state proxy checks must not overwrite fresh concrete proxy phases with ok")
    sys.exit(1)
if (
    "private static String appliedDiagnosticForResult" not in scheduler_text
    or "private static boolean hasFreshConcreteProxyPhase" not in scheduler_text
    or "ProxyCheckDiagnostics.hasFreshFailure(proxyInfo) || ProxyCheckDiagnostics.hasFreshLivePhase(proxyInfo) || ProxyCheckDiagnostics.hasFreshEndpointCooldown(proxyInfo)" not in scheduler_text
    or "appliedDiagnosticForResult(listener.proxyInfo, request, time, displayDiagnostic)" not in scheduler_text
):
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: finishRequest must preserve each listener's fresh concrete proxy phase while suppressing false current-proxy check failures")
    sys.exit(1)
if (
    "private static boolean shouldRememberConnectedCheckResult" not in scheduler_text
    or "time != -1 || shouldRememberConnectedCheckResult(request, time)" not in scheduler_text
    or "finish_keep_proxy_phase endpoint=" not in scheduler_text
):
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: preserved connected-state proxy checks must not clear endpoint backoff while a concrete proxy phase is still fresh")
    sys.exit(1)
if "applyMeasuredResult(request.proxyInfo, appliedTime);" in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: finishRequest must publish measured results only through listener fan-out")
    sys.exit(1)
if "cancelProxyCheck(proxyInfo.proxyCheckPingId)" in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: active native cancellation must use Request.nativePingId, not mutable ProxyInfo.proxyCheckPingId")
    sys.exit(1)
allowed_direct_check_callers = {SCHEDULER.resolve(), JAVA_MANAGER.resolve()}
direct_check_callers = []
direct_check_pattern = re.compile(r"\.checkProxy\(|native_checkProxy")
for path in (ROOT / "TMessagesProj/src/main/java/org/telegram").rglob("*.java"):
    text = path.read_text(encoding="utf-8", errors="replace")
    if not direct_check_pattern.search(text):
        continue
    path = path.resolve()
    if path not in allowed_direct_check_callers:
        direct_check_callers.append(str(path.relative_to(ROOT)))
if direct_check_callers:
    print("Proxy check scheduler guard failed:")
    print(" - direct proxy checks must go through ProxyCheckScheduler:")
    for path in direct_check_callers[:20]:
        print(f"   {path}")
    sys.exit(1)
if "currentInfo.availableCheckTime = 0" in PROXY_LIST.read_text(encoding="utf-8"):
    print("Proxy check scheduler guard failed:")
    print(f" - {PROXY_LIST.relative_to(ROOT)}: connected current proxy must not be marked stale by the UI")
    sys.exit(1)
proxy_list_text = PROXY_LIST.read_text(encoding="utf-8")
if "ProxyCheckScheduler.enqueueStale(currentAccount, proxyList" in proxy_list_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {PROXY_LIST.relative_to(ROOT)}: opening the proxy list must not start a full proxy-check sweep")
    sys.exit(1)
did_update_start = proxy_list_text.find("id == NotificationCenter.didUpdateConnectionState")
proxy_done_start = proxy_list_text.find("id == NotificationCenter.proxyCheckDone")
did_update_end = proxy_done_start if proxy_done_start > did_update_start else len(proxy_list_text)
proxy_done_end = proxy_list_text.find("private class ListAdapter", proxy_done_start)
if did_update_start == -1 or proxy_done_start == -1 or "updateRows(true)" in proxy_list_text[did_update_start:did_update_end]:
    print("Proxy check scheduler guard failed:")
    print(f" - {PROXY_LIST.relative_to(ROOT)}: connection-state updates must not re-sort the proxy list while the user is selecting a proxy")
    sys.exit(1)
did_update_body = proxy_list_text[did_update_start:did_update_end]
if "cell.updateStatus();" in did_update_body and "updateCurrentProxyStatusCell();" in did_update_body:
    print("Proxy check scheduler guard failed:")
    print(f" - {PROXY_LIST.relative_to(ROOT)}: connection-state updates must repaint the current proxy row once, not through duplicate direct and helper calls")
    sys.exit(1)
if "updateProxyActionBarStatus();" in did_update_body and "updateCurrentProxyStatusCell();" in did_update_body:
    print("Proxy check scheduler guard failed:")
    print(f" - {PROXY_LIST.relative_to(ROOT)}: connection-state updates must not repaint the action bar twice")
    sys.exit(1)
if proxy_done_start == -1 or proxy_done_end == -1 or "updateRows(true)" in proxy_list_text[proxy_done_start:proxy_done_end]:
    print("Proxy check scheduler guard failed:")
    print(f" - {PROXY_LIST.relative_to(ROOT)}: proxy-check result events must update visible rows without full list reordering")
    sys.exit(1)
proxy_done_body = proxy_list_text[proxy_done_start:proxy_done_end]
if (
    "updateProxyActionBarStatus();" in proxy_done_body
    and "proxyInfo == selectedProxy" not in proxy_done_body
):
    print("Proxy check scheduler guard failed:")
    print(f" - {PROXY_LIST.relative_to(ROOT)}: proxy-check result events must not repaint the action bar for unrelated proxy rows")
    sys.exit(1)
update_status_start = proxy_list_text.find("public void updateStatus()")
update_status_end = proxy_list_text.find("public void setSelectionEnabled", update_status_start)
update_status_body = proxy_list_text[update_status_start:update_status_end]
if "ProxyCheckScheduler.markConnected" in update_status_body:
    print("Proxy check scheduler guard failed:")
    print(f" - {PROXY_LIST.relative_to(ROOT)}: proxy list cell rendering must not mutate scheduler freshness state")
    sys.exit(1)
manual_select_start = proxy_list_text.find("SharedConfig.currentProxy = info;")
manual_select_update = proxy_list_text.find("for (int a = proxyStartRow; a < proxyEndRow; a++)", manual_select_start)
manual_select_mark_starting = proxy_list_text.find("ProxyCheckScheduler.markConnectionStarting(SharedConfig.currentProxy);", manual_select_start)
if manual_select_start == -1 or manual_select_update == -1 or manual_select_mark_starting == -1 or manual_select_mark_starting > manual_select_update:
    print("Proxy check scheduler guard failed:")
    print(f" - {PROXY_LIST.relative_to(ROOT)}: manual proxy selection must publish connect_start before repainting visible proxy rows")
    sys.exit(1)
reapply_start = proxy_list_text.find("private void reapplyCurrentProxySettings()")
reapply_end = proxy_list_text.find("private void reapplyWssTransportSettings()", reapply_start)
reapply_body = proxy_list_text[reapply_start:reapply_end]
if (
    "ProxyCheckScheduler.markConnectionStarting(SharedConfig.currentProxy);" not in reapply_body
    or "updateCurrentProxyStatusCell();" not in reapply_body
):
    print("Proxy check scheduler guard failed:")
    print(f" - {PROXY_LIST.relative_to(ROOT)}: MTProxy option reapply must immediately repaint the current row with the new connect_start phase")
    sys.exit(1)
if "notifyOwnerFinishedIfDrained(request)" in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: coalesced request drain must notify listeners, not the old request-shaped callback")
    sys.exit(1)
if "copyResult(proxyInfo, -1);" in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: owner cancellation must clear transient state without marking the proxy unavailable")
    sys.exit(1)
cancel_start = scheduler_text.find("if (activeRequest != null && activeRequest.cancelOwner(owner))")
cancel_log = scheduler_text.find('log("cancel_owner active endpoint="', cancel_start)
cancel_branch = scheduler_text[cancel_start:cancel_log]
if "postNotificationName(NotificationCenter.proxyCheckDone" in cancel_branch:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: owner cancellation must not emit proxyCheckDone without a measured proxy-check result")
    sys.exit(1)
if "listener.proxyInfo.checking = false;" in scheduler_text and "clearCancelledListenerState" not in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: listener cancel must not blindly clear shared ProxyInfo checking state")
    sys.exit(1)
if ' + ":" + proxyInfo.port + ":" +' in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: endpointKey must not use delimiter-only concatenation")
    sys.exit(1)
network_key_method = scheduler_text[scheduler_text.find("private static String endpointNetworkKey"):]
network_key_method = network_key_method[:network_key_method.find("\n    private static", 1)]
if (
    "normalizeKeyPart(proxyInfo.address, true)" not in network_key_method
    or "String.valueOf(proxyInfo.port)" not in network_key_method
    or "proxyInfo.secret" in network_key_method
    or "proxyInfo.username" in network_key_method
    or "proxyInfo.password" in network_key_method
):
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: endpointNetworkKey must be host/port only, without secret or auth fields")
    sys.exit(1)
state_key_method = scheduler_text[scheduler_text.find("private static String endpointStateKeyForDiagnostic"):]
state_key_method = state_key_method[:state_key_method.find("\n    private static", 1)]
for phase in (
    "HOST_RESOLVE_FAILED",
    "TCP_NOT_CONNECTED",
    "NETWORK_BLOCK_SUSPECTED",
    "TCP_CONNECTED_NO_PONG",
    "MTPROXY_PACKET_SENT_NO_RESPONSE",
    "DROPPED_EARLY_AFTER_APPDATA",
):
    if phase not in state_key_method:
        print("Proxy check scheduler guard failed:")
        print(f" - {SCHEDULER.relative_to(ROOT)}: endpointStateKeyForDiagnostic must use host/port state for {phase}")
        sys.exit(1)
if "endpointNetworkKey(proxyInfo)" not in state_key_method or "endpointKey(proxyInfo)" not in state_key_method:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: phase-aware endpoint state must choose between host/port and exact proxy keys")
    sys.exit(1)

enqueue_stale_start = scheduler_text.find("public static int enqueueStale(")
enqueue_stale_end = scheduler_text.find("public static void cancelOwner(", enqueue_stale_start)
enqueue_stale_body = scheduler_text[enqueue_stale_start:enqueue_stale_end]
ordered_needles = [
    "attachPending(proxyInfo, owner, callback, false)",
    "clearDetachedCheckState(proxyInfo, \"enqueue\")",
    "shouldCheck(proxyInfo, false)",
]
last_index = -1
for needle in ordered_needles:
    needle_index = enqueue_stale_body.find(needle)
    if needle_index == -1 or needle_index <= last_index:
        print("Proxy check scheduler guard failed:")
        print(f" - {SCHEDULER.relative_to(ROOT)}: enqueueStale must attach to active endpoint checks before deciding a ProxyInfo is already checking")
        sys.exit(1)
    last_index = needle_index

if "shouldCheck(proxyInfo, false)" not in enqueue_stale_body:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: background sweeps must use cooldown-aware non-forced checks")
    sys.exit(1)
should_check_method = scheduler_text[scheduler_text.find("private static boolean shouldCheck"):]
should_check_method = should_check_method[:should_check_method.find("\n    public static", 1)]
if "markEndpointCooldown(proxyInfo, now);" not in should_check_method:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: skipped endpoint backoff must publish endpoint_cooldown so GUI rows show the wait instead of looking unchecked")
    sys.exit(1)
if "proxyInfo.lastCheckDiagnostic = ProxyCheckDiagnostics.ENDPOINT_COOLDOWN;" not in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: scheduler endpoint cooldown must use the shared diagnostic string")
    sys.exit(1)
if "shouldCheck(request.proxyInfo, request.force)" not in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: queued starts must re-check cooldown before opening native sockets")
    sys.exit(1)
if "rememberCheckResult(request, callbackTime, displayDiagnostic);" not in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: finishRequest must update endpoint backoff before listener fan-out")
    sys.exit(1)
if "rememberCheckResult(request, callbackTime, displayDiagnostic);" in scheduler_text:
    finish_start = scheduler_text.find("private static void finishRequest(")
    remember_index = scheduler_text.find("rememberCheckResult(request, callbackTime, displayDiagnostic);", finish_start)
    fanout_index = scheduler_text.find("for (int i = 0, count = request.listeners.size();", finish_start)
    if remember_index == -1 or fanout_index == -1 or remember_index > fanout_index:
        print("Proxy check scheduler guard failed:")
        print(f" - {SCHEDULER.relative_to(ROOT)}: endpoint backoff must be updated before notifying GUI listeners")
        sys.exit(1)

rotation_text = ROTATION.read_text(encoding="utf-8")
diagnostics_text = DIAGNOSTICS.read_text(encoding="utf-8")
endpoint_backoff_method = scheduler_text[scheduler_text.find("public static boolean isEndpointBackedOff"):]
endpoint_backoff_method = endpoint_backoff_method[:endpoint_backoff_method.find("\n    public static", 1)]
if (
    "nextAllowedCheckTime(proxyInfo)" not in endpoint_backoff_method
    or "SystemClock.elapsedRealtime()" not in endpoint_backoff_method
    or "state.consecutiveFailures > 0" not in endpoint_backoff_method
):
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: rotation-visible endpoint backoff must use scheduler nextAllowedCheckTime, current elapsed time, and failure count without treating connected grace as failure backoff")
    sys.exit(1)
mark_failure_method = scheduler_text[scheduler_text.find("public static void markEndpointFailure"):]
mark_failure_method = mark_failure_method[:mark_failure_method.find("\n    private static", 1)]
if (
    "ProxyCheckDiagnostics.shouldAccelerateProxyRotation(diagnostic)" not in mark_failure_method
    or "endpointStateForKey(key)" not in mark_failure_method
    or "rememberEndpointFailure" not in mark_failure_method
    or "PROXY_CHECK_LIVE_FAILURE_DEDUP_MS" not in mark_failure_method
    or "state.lastDiagnostic" not in mark_failure_method
    or "state.lastCheckTime" not in mark_failure_method
):
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: live current-connection failures must update shared endpoint backoff through the same failure helper without double-counting duplicate terminal stages")
    sys.exit(1)
endpoint_cooldown_method = diagnostics_text[diagnostics_text.find("public static boolean hasFreshEndpointCooldown"):]
endpoint_cooldown_method = endpoint_cooldown_method[:endpoint_cooldown_method.find("\n    public static", 1)]
if (
    "ENDPOINT_COOLDOWN.equals(normalize(proxyInfo.lastCheckDiagnostic))" not in endpoint_cooldown_method
    or "LIVE_PHASE_STALE_MS" not in endpoint_cooldown_method
):
    print("Proxy check scheduler guard failed:")
    print(f" - {DIAGNOSTICS.relative_to(ROOT)}: fresh endpoint cooldown must be detected by the shared diagnostic string and live-phase TTL")
    sys.exit(1)
status_text_method = diagnostics_text[diagnostics_text.find("public static String statusText"):]
status_text_method = status_text_method[:status_text_method.find("\n    public static", 1)]
passive_checking_index = status_text_method.find("if (proxyInfo.checking)")
passive_cooldown_index = status_text_method.find("hasFreshEndpointCooldown(proxyInfo)", passive_checking_index)
passive_unchecked_index = status_text_method.find("ProxyStatusUnchecked", passive_checking_index)
if passive_cooldown_index == -1 or passive_unchecked_index == -1 or passive_cooldown_index > passive_unchecked_index:
    print("Proxy check scheduler guard failed:")
    print(f" - {DIAGNOSTICS.relative_to(ROOT)}: passive proxy rows must show fresh endpoint_cooldown before falling back to unchecked")
    sys.exit(1)
accelerate_method = diagnostics_text[diagnostics_text.find("public static boolean shouldAccelerateProxyRotation"):]
accelerate_method = accelerate_method[:accelerate_method.find("\n    public static", 1)]
for phase in (
    "HOST_RESOLVE_FAILED",
    "TCP_NOT_CONNECTED",
    "TCP_CONNECTED_NO_PONG",
    "NETWORK_BLOCK_SUSPECTED",
    "CLIENT_HELLO_SENT_NO_SERVER_HELLO",
    "SERVER_HELLO_HMAC_MISMATCH",
    "MTPROXY_PACKET_SENT_NO_RESPONSE",
    "POST_HANDSHAKE_NO_APPDATA",
    "DROPPED_EARLY_AFTER_APPDATA",
):
    if phase not in accelerate_method:
        print("Proxy check scheduler guard failed:")
        print(f" - {DIAGNOSTICS.relative_to(ROOT)}: terminal phase {phase} must accelerate fallback rotation")
        sys.exit(1)
if "ProxyCheckScheduler.enqueueStale(currentAccount, SharedConfig.proxyList" in rotation_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {ROTATION.relative_to(ROOT)}: proxy rotation must not start a full proxy-check sweep")
    sys.exit(1)
for old_rotation_proxy_check_hook in (
    "rotationCheckCallback",
    "onProxyCheckQueueFinished",
    "NotificationCenter.proxyCheckDone",
    "isCurrentlyChecking",
):
    if old_rotation_proxy_check_hook in rotation_text:
        print("Proxy check scheduler guard failed:")
        print(f" - {ROTATION.relative_to(ROOT)}: proxy rotation must not keep old proxy-check hook {old_rotation_proxy_check_hook}")
        sys.exit(1)

switch_to_proxy_method = rotation_text[rotation_text.find("private void switchToProxy"):]
switch_to_proxy_method = switch_to_proxy_method[:switch_to_proxy_method.find("\n    private void", 1)]
rotation_changed_index = switch_to_proxy_method.find("NotificationCenter.proxyChangedByRotation")
settings_changed_index = switch_to_proxy_method.find("NotificationCenter.proxySettingsChanged")
if rotation_changed_index == -1 or settings_changed_index == -1 or rotation_changed_index > settings_changed_index:
    print("Proxy check scheduler guard failed:")
    print(f" - {ROTATION.relative_to(ROOT)}: rotation must notify proxyChangedByRotation before proxySettingsChanged so open proxy list screens can avoid full resort")
    sys.exit(1)

if "skipNextProxySettingsChangedLayout" not in proxy_list_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {PROXY_LIST.relative_to(ROOT)}: proxy list must suppress the full proxySettingsChanged rebuild that belongs to rotation")
    sys.exit(1)
rotation_branch_start = proxy_list_text.find("id == NotificationCenter.proxyChangedByRotation")
settings_branch_start = proxy_list_text.find("id == NotificationCenter.proxySettingsChanged")
rotation_branch = proxy_list_text[rotation_branch_start:settings_branch_start]
settings_branch_end = proxy_list_text.find("} else if (id == NotificationCenter.proxyConnectionStageChanged)", settings_branch_start)
settings_branch = proxy_list_text[settings_branch_start:settings_branch_end]
if (
    "skipNextProxySettingsChangedLayout = true;" not in rotation_branch
    or "updateRows(false)" not in rotation_branch
    or "updateCurrentProxyStatusCell();" not in rotation_branch
    or "updateProxyActionBarStatus();" in rotation_branch
    or "if (skipNextProxySettingsChangedLayout)" not in settings_branch
    or "skipNextProxySettingsChangedLayout = false;" not in settings_branch
    or "updateCurrentProxyStatusCell();" not in settings_branch
    or settings_branch.find("if (skipNextProxySettingsChangedLayout)") > settings_branch.find("updateRows(true)")
):
    print("Proxy check scheduler guard failed:")
    print(f" - {PROXY_LIST.relative_to(ROOT)}: rotation-linked proxySettingsChanged must repaint status softly instead of resorting the proxy list")
    sys.exit(1)


def require_cancel_order(marker, label):
    marker_index = rotation_text.find(marker)
    if marker_index == -1:
        print("Proxy check scheduler guard failed:")
        print(f" - {ROTATION.relative_to(ROOT)}: proxy rotation must log cancellation on {label}")
        sys.exit(1)

    branch_start = max(
        rotation_text.rfind("} else if", 0, marker_index),
        rotation_text.rfind("} else {", 0, marker_index),
    )
    branch_text = rotation_text[branch_start:marker_index]
    ordered_needles = [
        "AndroidUtilities.cancelRunOnUIThread(checkProxyAndSwitchRunnable);",
        "isCheckScheduled = false;",
    ]
    last_index = -1
    for needle in ordered_needles:
        needle_index = branch_text.find(needle)
        if needle_index == -1 or needle_index <= last_index:
            print("Proxy check scheduler guard failed:")
            print(f" - {ROTATION.relative_to(ROOT)}: proxy rotation must cancel timer, clear flags, then cancel native check on {label}")
            sys.exit(1)
        last_index = needle_index


require_cancel_order('log("cancel settings_changed");', "settings_changed")
require_cancel_order('log("cancel state=" + state);', "state change")

print("Proxy check scheduler guard passed.")
