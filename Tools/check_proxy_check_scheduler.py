#!/usr/bin/env python3
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

SCHEDULER = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyCheckScheduler.java"
PROXY_LIST = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProxyListActivity.java"
ROTATION = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/ProxyRotationController.java"
JAVA_MANAGER = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"

checks = [
    (SCHEDULER, "PROXY_CHECK_SPACING_MS", "scheduler must space background proxy checks"),
    (SCHEDULER, "PROXY_CHECK_FAILURE_BACKOFF_MS", "scheduler must back off repeated failed endpoint checks"),
    (SCHEDULER, "PROXY_CHECK_CONNECTED_GRACE_MS", "scheduler must avoid rechecking a recently connected endpoint"),
    (SCHEDULER, "activeRequest", "scheduler must keep a single active background check"),
    (SCHEDULER, "EndpointState", "scheduler must keep per-endpoint check state outside mutable ProxyInfo rows"),
    (SCHEDULER, "endpointStates", "scheduler must remember endpoint cooldowns across UI/rotation sweeps"),
    (SCHEDULER, "enqueueStale", "scheduler must expose stale-check enqueueing"),
    (SCHEDULER, "enqueueNow", "scheduler must expose priority manual checks so GUI does not bypass the shared queue"),
    (SCHEDULER, "owner == null", "scheduler must reject ownerless checks because they cannot be cancelled or drained reliably"),
    (SCHEDULER, "isFresh", "scheduler must expose one freshness policy for UI and rotation"),
    (SCHEDULER, "markConnected", "scheduler must expose a single path for real connected-state observations"),
    (SCHEDULER, "nextAllowedCheckTime", "scheduler must expose a single debounce policy for UI and rotation"),
    (SCHEDULER, "rememberCheckResult", "scheduler must update endpoint cooldowns from measured check results"),
    (SCHEDULER, "displayDiagnosticForResult", "scheduler must translate repeated TCP failures into a user-facing network-block phase"),
    (SCHEDULER, "skip_backoff", "scheduler must log when a repeated endpoint check is intentionally suppressed"),
    (SCHEDULER, "endpointKey", "scheduler must deduplicate checks by proxy endpoint, not ProxyInfo object identity"),
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
    (PROXY_LIST, "ProxyCheckScheduler.cancelOwner(this)", "proxy list must cancel queued checks on destroy"),
    (ROTATION, "ProxyCheckScheduler.isFresh", "proxy rotation must not switch to stale availability results"),
    (ROTATION, "ProxyCheckScheduler.markConnected(SharedConfig.currentProxy)", "proxy rotation must share connected-state freshness with the scheduler"),
    (ROTATION, "selectFallbackCandidate", "proxy rotation must try one unchecked endpoint through a real connection instead of full-list proxy checks"),
    (ROTATION, "switch fallback endpoint=", "proxy rotation must log one-at-a-time fallback switches distinctly"),
    (ROTATION, "isCheckScheduled", "proxy rotation must not schedule duplicate delayed sweeps"),
    (ROTATION, "proxy_rotation ", "proxy rotation must emit stable diagnostics"),
    (ROTATION, "scheduled_check skipped background_disabled", "proxy rotation must not launch a full proxy-check sweep while connection is already trying"),
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
if "finish result=\" + (effectiveTime == -1" in scheduler_text or "onProxyChecked(listener.proxyInfo, effectiveTime)" in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: callback result must not reuse preserved connected-state time")
    sys.exit(1)
if "applyMeasuredResult(request.proxyInfo, appliedTime);" in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: finishRequest must publish measured results only through listener fan-out")
    sys.exit(1)
if "cancelProxyCheck(proxyInfo.proxyCheckPingId)" in scheduler_text:
    print("Proxy check scheduler guard failed:")
    print(f" - {SCHEDULER.relative_to(ROOT)}: active native cancellation must use Request.nativePingId, not mutable ProxyInfo.proxyCheckPingId")
    sys.exit(1)
direct_check_result = subprocess.run(
    ["rg", "-l", r"\.checkProxy\(|native_checkProxy", str(ROOT / "TMessagesProj/src/main/java/org/telegram")],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    check=False,
)
if direct_check_result.returncode not in (0, 1):
    print("Proxy check scheduler guard failed:")
    print(f" - rg failed while checking direct proxy calls: {direct_check_result.stderr.strip()}")
    sys.exit(1)
allowed_direct_check_callers = {SCHEDULER.resolve(), JAVA_MANAGER.resolve()}
direct_check_callers = []
for item in direct_check_result.stdout.splitlines():
    path = Path(item).resolve()
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
update_status_start = proxy_list_text.find("public void updateStatus()")
update_status_end = proxy_list_text.find("public void setSelectionEnabled", update_status_start)
update_status_body = proxy_list_text[update_status_start:update_status_end]
if "ProxyCheckScheduler.markConnected" in update_status_body:
    print("Proxy check scheduler guard failed:")
    print(f" - {PROXY_LIST.relative_to(ROOT)}: proxy list cell rendering must not mutate scheduler freshness state")
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
