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
