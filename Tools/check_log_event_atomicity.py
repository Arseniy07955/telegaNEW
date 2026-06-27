#!/usr/bin/env python3
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
NATIVE_FILE_LOG = ROOT / "TMessagesProj/jni/tgnet/FileLog.cpp"
JAVA_FILE_LOG = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/FileLog.java"
MTPROXY_ALL = ROOT / "Tools/check_mtproxy_all.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    native = read(NATIVE_FILE_LOG)
    java = read(JAVA_FILE_LOG)
    all_checks = read(MTPROXY_ALL)

    require(
        "writeNativeLogLine" in native
        and "vsnprintf" in native
        and "pthread_mutex_lock(&logger.mutex)" in native
        and "fprintf(logFile, \"%s\\n\", line.c_str())" in native,
        "native FileLog must format one event line and append it under one mutex-protected write",
        failures,
    )
    require(
        native.count("vfprintf(logFile") == 0
        and native.count("fprintf(logFile, \"\\n\")") == 0,
        "native FileLog must not split one log event across prefix/body/newline fprintf calls",
        failures,
    )
    require(
        "sanitizeLogMessage" in java
        and "writeLogLine(" in java
        and "writeExceptionLogLine(" in java
        and "private static synchronized void writeLogLineLocked" in java
        and "streamWriter.write(line)" in java
        and "streamWriter.write('\\n')" in java,
        "Java FileLog must centralize file append as one sanitized line per event",
        failures,
    )
    for marker in (
        "streamWriter.write(getInstance().dateFormat.format(System.currentTimeMillis()) + \" D/tmessages:",
        "streamWriter.write(getInstance().dateFormat.format(System.currentTimeMillis()) + \" W/tmessages:",
        "streamWriter.write(getInstance().dateFormat.format(System.currentTimeMillis()) + \" E/tmessages:",
        "streamWriter.write(getInstance().dateFormat.format(System.currentTimeMillis()) + \" FATAL/tmessages:",
    ):
        require(marker not in java, f"Java FileLog must route {marker} through writeLogLine", failures)
    require(
        re.search(r"sanitizeLogMessage\(.*replace\('\\n', '\\\\n'\)", java, re.S)
        and re.search(r"sanitizeLogMessage\(.*replace\('\\r', '\\\\r'\)", java, re.S),
        "Java FileLog must sanitize embedded newlines so one event remains one physical log line",
        failures,
    )
    require(
        '"check_log_event_atomicity.py"' in all_checks,
        "full MTProxy guard suite must include log event atomicity guard",
        failures,
    )

    if failures:
        print("Log event atomicity guard failed:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1

    print("Log event atomicity guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
