#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def main() -> int:
    errors: list[str] = []
    policy = read("TMessagesProj/src/main/java/org/telegram/messenger/DeviceResourcePolicy.java")
    exits = read("TMessagesProj/src/main/java/org/telegram/messenger/ProcessExitDiagnostics.java")
    app = read("TMessagesProj/src/main/java/org/telegram/messenger/ApplicationLoader.java")
    shared = read("TMessagesProj/src/main/java/org/telegram/messenger/SharedConfig.java")
    images = read("TMessagesProj/src/main/java/org/telegram/messenger/ImageLoader.java")
    stories = read("TMessagesProj/src/main/java/org/telegram/ui/Stories/StoriesController.java")
    plugins = read("TMessagesProj/src/main/java/org/telegram/plugins/PluginsController.java")
    logs = read("TMessagesProj/src/main/java/org/telegram/messenger/FileLog.java")
    filter_tabs = read("TMessagesProj/src/main/java/org/telegram/ui/Components/FilterTabsView.java")
    dialogs = read("TMessagesProj/src/main/java/org/telegram/ui/DialogsActivity.java")
    main_tabs = read("TMessagesProj/src/main/java/org/telegram/ui/MainTabsActivity.java")
    zasto_privacy = read("TMessagesProj/src/main/java/org/telegram/messenger/ZaStoPrivacy.java")
    lite_mode = read("TMessagesProj/src/main/java/org/telegram/messenger/LiteMode.java")
    media_data = read("TMessagesProj/src/main/java/org/telegram/messenger/MediaDataController.java")
    messages_controller = read("TMessagesProj/src/main/java/org/telegram/messenger/MessagesController.java")
    chat_activity = read("TMessagesProj/src/main/java/org/telegram/ui/ChatActivity.java")
    file_loader = read("TMessagesProj/src/main/java/org/telegram/messenger/FileLoader.java")
    file_operation = read("TMessagesProj/src/main/java/org/telegram/messenger/FileLoadOperation.java")

    require("CONSTRAINED_HEAP_MB = 128" in policy and "isLowRamDevice()" in policy,
            "resource policy must treat both a 128 MiB heap and Android low-RAM as constrained", errors)
    require("DeviceResourcePolicy.isConstrainedDevice()" in shared
            and "constrainedByHeap ||" in shared,
            "performance classification must not let CPU count override a constrained heap", errors)
    require("DeviceResourcePolicy.getImageCacheDivisor()" in images
            and "clearMemoryIfInitialized()" in images,
            "image cache must scale down and be releasable without constructing ImageLoader", errors)
    require(stories.count("if (!DeviceResourcePolicy.allowStoryPreload())") >= 2,
            "startup and update story prefetch paths must obey the central resource policy", errors)
    require("DeviceResourcePolicy.onTrimMemory(level);" in app
            and "DeviceResourcePolicy.onLowMemory();" in app,
            "Application callbacks must route memory pressure to the central policy", errors)

    require("getHistoricalProcessExitReasons" in exits
            and "REASON_LOW_MEMORY" in exits
            and "REASON_CRASH_NATIVE" in exits
            and "FileLog.persistDiagnostic" in exits,
            "next startup must persist Android's previous Java/native/OOM exit reason", errors)
    require("public static void cleanupLogs()" in logs
            and "pruneOldLogs(getMaxLogFiles());" in logs,
            "startup log retention must preserve the previous crash session", errors)
    require("writeExceptionLogLineSync(\"FATAL\"" in logs
            and '"FATAL".equals(level)' in logs,
            "fatal exceptions must be written synchronously and force-flushed", errors)
    require("e instanceof OutOfMemoryError && BuildVars.DEBUG_PRIVATE_VERSION" in logs
            and "!DeviceResourcePolicy.isConstrainedDevice()" in logs,
            "production or constrained OOM handling must not attempt an HPROF dump", errors)

    require("private void resetDefaultTabTitle()" in filter_tabs
            and "Tab defaultTab = findDefaultTab();" in filter_tabs
            and "if (defaultTab != null)" in filter_tabs
            and filter_tabs.count("resetDefaultTabTitle();") >= 2
            and "findDefaultTab().setTitle" not in filter_tabs,
            "tab-counter updates must tolerate a deliberately hidden All Chats tab", errors)

    require("public int getStableIdForTabId(int tabId)" in filter_tabs
            and "int position = idToPosition.get(tabId, -1);" in filter_tabs
            and "public boolean scrollToTabWithId(int id)" in filter_tabs
            and "positionToStableId.clear();" in filter_tabs
            and "filterTabsView.scrollToTabWithId(tabId);" in dialogs
            and "if (!filterTabsView.selectTabWithStableId(stableId))" in dialogs
            and "if (viewPages[0].selectedType != id)" in dialogs,
            "folder navigation must resolve logical tab IDs through the visible-tab mapping", errors)
    require("int filterIndex1 = tab1.id;" in filter_tabs
            and "int filterIndex2 = tab2.id;" in filter_tabs
            and "filters.set(filterIndex1, filter2);" in filter_tabs
            and "filters.set(filterIndex2, filter1);" in filter_tabs,
            "folder reordering must map visible tabs back to logical filter IDs", errors)
    require("public static boolean shouldHideAllChatsTab(int filterCount)" in zasto_privacy
            and "ZaStoPrivacy.shouldHideAllChatsTab(filters.size())" in dialogs
            and "ZaStoPrivacy.shouldHideAllChatsTab(filters.size())" in main_tabs,
            "all folder entry points must share the hidden All Chats visibility policy", errors)

    require("req.top_msg_id = (int) topicId;" in media_data
            and "PinnedMessagesRequestKey(dialogId, topicId, generation)" in media_data
            and "endReached = false;" in media_data
            and "loadedTopicId != getTopicId()" in chat_activity,
            "topic pinned-message loading must stay scoped, retryable, and stale-response safe", errors)
    require("public void unpinAllMessages(TLRPC.Chat chat, TLRPC.User user, int topicId)" in messages_controller
            and "req.top_msg_id = topicId;" in messages_controller
            and "unpinAllMessages(currentChat, currentUser, (int) getTopicId())" in chat_activity,
            "unpin-all must use Telegram's topic-scoped API so unloaded pins are removed too", errors)
    require("getLoadOperationDiagnostics" in file_loader
            and "recentLoadDiagnostics" in file_loader
            and "Thread.currentThread() == Utilities.stageQueue" in file_loader
            and "ready.await(750, TimeUnit.MILLISECONDS)" in file_loader
            and "getDiagnosticSnapshot(String event)" in file_operation
            and 'appendDiagnostic(result, "request[" + i + "]"' in file_operation
            and "lastDiagnosticError" in file_operation,
            "message diagnostics must retain active and failed media request identifiers", errors)
    require("MessageTechnicalDetailsRefresh" in chat_activity
            and "Utilities.globalQueue.postRunnable" in chat_activity
            and "getDatacenterConnectionDiagnostics" in chat_activity
            and 'appendTechnicalSection(result, "file_loader")' in chat_activity
            and 'appendTechnicalSection(result, "live_connections")' in chat_activity,
            "every message details dialog must refresh live loader and connection routes", errors)
    require('"document_access_hash"' not in chat_activity
            and '"photo_access_hash"' not in chat_activity
            and '"webpage_url"' not in chat_activity
            and '"embed_url"' not in chat_activity
            and '"local_path"' not in chat_activity
            and "getPathToMessage(message, false)" in chat_activity
            and "getFileDatabase().getPath(documentId, dcId, type, useFileDatabaseQueue)" in file_loader,
            "copied message diagnostics must fingerprint credentials and paths while resolving the current account without blocking", errors)

    flags_chat = next(
        (line for line in lite_mode.splitlines() if "int FLAGS_CHAT =" in line),
        "",
    )
    require("FLAG_CHAT_BLUR" not in flags_chat
            and lite_mode.count("& ~FLAG_CHAT_BLUR") >= 3
            and 'if (!preferences.contains("lite_mode7"))' in lite_mode
            and "defaultValue &= ~FLAG_CHAT_BLUR;" in lite_mode
            and '.putInt("lite_mode7", defaultValue)' in lite_mode
            and 'preferences.getInt("lite_mode7", defaultValue)' in lite_mode
            and '.putInt("lite_mode7", value)' in lite_mode,
            "chat blur must remain opt-in across presets and the lite-mode preference migration", errors)

    init_start = plugins.find("public void init(Context context)")
    staged_start = plugins.find("public void startEnabledPlugins()")
    python_start = plugins.find("ensurePythonStarted();", staged_start)
    require(init_start >= 0 and staged_start > init_start and python_start > staged_start,
            "plugin metadata discovery and CPython startup must remain separate phases", errors)
    require("if (!hasEnabledCompatiblePlugin)" in plugins
            and "python startup skipped, no enabled compatible plugins" in plugins,
            "CPython must stay unloaded when no compatible plugin is enabled", errors)
    require("startEnabledPlugins();" in app,
            "plugin runtime startup must happen only after Telegram initialization", errors)

    if errors:
        print("Runtime resilience guard failed:", file=sys.stderr)
        for error in errors:
            print(f" - {error}", file=sys.stderr)
        return 1
    print("Runtime resilience guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
