#!/usr/bin/env python3
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionsManager.cpp"
HDR = ROOT / "TMessagesProj/jni/tgnet/ConnectionsManager.h"


def require(condition, message):
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


def main():
    cpp = CPP.read_text(encoding="utf-8")
    hdr = HDR.read_text(encoding="utf-8")

    require(
        "finishProxyCheck(" in hdr and "scheduleNextProxyCheck(" in hdr,
        "ConnectionsManager must declare a single proxy-check finish path and next-queue helper",
    )
    require(
        "void ConnectionsManager::finishProxyCheck(" in cpp
        and "void ConnectionsManager::scheduleNextProxyCheck(" in cpp,
        "ConnectionsManager.cpp must define proxy-check lifecycle helpers",
    )
    require(
        'proxy_check_finish result=' in cpp and 'proxy_check_next queued=' in cpp,
        "proxy-check lifecycle must be visible in native logs",
    )
    require(
        "request_missing" in cpp,
        "closed proxy-check must log when the original running request is already gone",
    )
    require(
        re.search(r"finishProxyCheck\(iter,\s*-1,\s*\"connection_closed\"", cpp),
        "ConnectionTypeProxy close must finish active check as -1 even if request lookup fails",
    )
    require(
        "proxyCheckInfo->onRequestTime(-1)" not in cpp,
        "failure callback must go through finishProxyCheck instead of ad-hoc close-only handling",
    )
    require(
        cpp.count("proxyActiveChecks.erase(iter)") == 1,
        "active proxy-check erase must be centralized to avoid divergent lifecycle paths",
    )
    require(
        "finishProxyCheck(iter, ping," in cpp,
        "TL_pong success path must use the same lifecycle helper",
    )

    print("proxy check native lifecycle guard OK")


if __name__ == "__main__":
    main()
