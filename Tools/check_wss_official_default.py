#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SHARED_CONFIG = ROOT / "TMessagesProj/src/main/java/org/telegram/messenger/SharedConfig.java"
PROXY_LIST = ROOT / "TMessagesProj/src/main/java/org/telegram/ui/ProxyListActivity.java"
WSS_CPP = ROOT / "TMessagesProj/jni/tgnet/wss/WssSocket.cpp"


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    shared_config = SHARED_CONFIG.read_text(encoding="utf-8", errors="replace")
    proxy_list = PROXY_LIST.read_text(encoding="utf-8", errors="replace")
    wss_cpp = WSS_CPP.read_text(encoding="utf-8", errors="replace")

    require('preferences.getBoolean("wssTransportEnabled", false)' in shared_config,
            "WSS must be opt-in by default")
    require('preferences.getInt("wssTransportMode"' in shared_config,
            "legacy WSS mode must migrate to the checkbox")
    require('.remove("wssHost")' in shared_config and '.remove("wssPath")' in shared_config,
            "custom gateway keys must be removed during migration")
    require("UseWssTransport" in proxy_list and "wssTransportEnabled" in proxy_list,
            "the official transport must be exposed as a normal checkbox")
    require('prefix = dcId == 4 ? "kws4" : "kws2"' in wss_cpp,
            "official WSS must target Telegram DC2/DC4 relay names")
    require('mediaConnection ? "-1.web.telegram.org"' in wss_cpp,
            "official WSS must select the media relay variant")
    require("dcId != 2 && dcId != 4" in wss_cpp and "testBackend" in wss_cpp,
            "unsupported/test DCs must stay on their normal transport")

    print("WSS official route guard passed.")


if __name__ == "__main__":
    main()
