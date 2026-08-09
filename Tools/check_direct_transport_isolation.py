#!/usr/bin/env python3
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CONNECTION_CPP = ROOT / "TMessagesProj/jni/tgnet/Connection.cpp"
CONNECTION_H = ROOT / "TMessagesProj/jni/tgnet/Connection.h"
SOCKET_CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.cpp"
SOCKET_H = ROOT / "TMessagesProj/jni/tgnet/ConnectionSocket.h"
MANAGER_CPP = ROOT / "TMessagesProj/jni/tgnet/ConnectionsManager.cpp"
MANAGER_JAVA = ROOT / "TMessagesProj/src/main/java/org/telegram/tgnet/ConnectionsManager.java"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def method_body(text: str, signature: str) -> str:
    start = text.find(signature)
    if start < 0:
        return ""
    brace = text.find("{", start)
    if brace < 0:
        return ""
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    connection = read(CONNECTION_CPP)
    connection_h = read(CONNECTION_H)
    socket = read(SOCKET_CPP)
    socket_h = read(SOCKET_H)
    manager = read(MANAGER_CPP)
    java = read(MANAGER_JAVA)

    route = method_body(connection, "bool Connection::isMtProxyRouteActive() const")
    write_gate = method_body(connection, "bool Connection::canSendRequestData")
    disconnect = method_body(connection, "void Connection::onDisconnectedInternal")
    send_messages = method_body(manager, "bool ConnectionsManager::sendMessagesToConnection(")
    resume_maybe = method_body(java, "public void resumeNetworkMaybe()")
    background = method_body(java, "public void applyBackgroundNetworkPolicy()")
    proxy_settings = method_body(java, "public static void setProxySettings(boolean enabled, String address, int port, String username, String password, String secret, ProxyConnectionEvent.Origin origin)")
    native_stage = method_body(java, "public static void onProxyConnectionStageChanged(final int currentAccount, final String diagnostic, final String endpointKey, final String probeKey, final String origin, final String socketRole, final int activationGeneration, final int suggestedHoldMs)")
    socket_policy = method_body(socket, "bool ConnectionSocket::checkTransportActionRequirements")
    socket_queue = method_body(socket, "bool ConnectionSocket::canQueueOutboundBuffer")
    socket_timeout = method_body(socket, "bool ConnectionSocket::checkTimeout")

    require("bool isMtProxyRouteActive() const;" in connection_h, "Connection must expose the selected MTProxy route boundary", failures)
    require(
        "return hasMtProxyOverride();" in route
        and "!manager.proxyAddress.empty() && !manager.proxySecret.empty()" in route
        and "bool hasMtProxyOverride() const;" in socket_h
        and "!overrideProxyAddress.empty() && !overrideProxySecret.empty()" in socket,
        "ordinary routes and proxy-check sockets must enter MTProxy policy only with an MTProxy secret",
        failures,
    )
    direct_idx = write_gate.find("if (!isMtProxyRouteActive())")
    closing_idx = write_gate.find("isClosingOrClosedForWrites()")
    require(
        direct_idx >= 0 and closing_idx > direct_idx,
        "direct send must return through the stock path before MTProxy closing/write gates",
        failures,
    )
    require(
        "const bool mtProxyRouteActive = isMtProxyRouteActive();" in disconnect
        and "if (mtProxyRouteActive &&" in disconnect,
        "MTProxy reconnect diagnostics/backoff must be gated by the selected route",
        failures,
    )
    require(
        "const bool mtProxyRouteActive = connection->isMtProxyRouteActive();" in send_messages
        and "if (mtProxyRouteActive && !connection->canSendRequestData" in send_messages
        and "accepted || !mtProxyRouteActive" in send_messages,
        "direct request sends must bypass proxy write gating and proxy requeue",
        failures,
    )
    require(
        "connection->isMtProxyRouteActive() && !connection->canSendRequestData(\"process_running_request\")" in manager
        and "connection->isMtProxyRouteActive() && !connection->canSendRequestData(\"process_queued_request\")" in manager,
        "request queue write gates must be MTProxy-only",
        failures,
    )
    require(
        "native_resumeNetwork(currentAccount, true);" in resume_maybe
        and "ProxyRuntimeStateStore" not in resume_maybe
        and "native_setProxyActivationContext" not in resume_maybe,
        "ordinary partial resume must not publish proxy lifecycle state",
        failures,
    )
    require(
        "native_resumeNetwork(currentAccount, false);" in background
        and "ProxyRuntimeStateStore" not in background
        and "native_setProxyActivationContext" not in background,
        "background network policy must not mutate proxy control state",
        failures,
    )
    require(
        "boolean hasSelectedProxy = enabled && !TextUtils.isEmpty(address);" in proxy_settings
        and "hasSelectedProxy ? ProxyRuntimeStateStore.noteProxySettingsActivation(activationOrigin) : 0" in proxy_settings,
        "disabled proxy settings must clear native routing without creating proxy activation state",
        failures,
    )
    require(
        "publishProxyActivationContext" not in java,
        "foreground/background lifecycle must not create proxy generations",
        failures,
    )
    require(
        "if (!SharedConfig.isProxyEnabled())" in native_stage
        and native_stage.find("if (!SharedConfig.isProxyEnabled())") < native_stage.find("ProxyConnectionEvent.nativeStage"),
        "late native proxy callbacks must be ignored after proxy disable",
        failures,
    )
    require(
        "bool isCurrentDirectConnection() const;" in socket_h
        and "stateMachine.diagnostics.transportMode == TransportMode::Direct" in socket,
        "ConnectionSocket must have an explicit direct transport boundary",
        failures,
    )
    require(
        "if (isCurrentDirectConnection())" in socket_policy
        and "return true;" in socket_policy,
        "direct sockets must bypass MTProxy transport action policy",
        failures,
    )
    require(
        "if (isCurrentDirectConnection())" in socket_queue
        and socket_queue.find("if (isCurrentDirectConnection())") < socket_queue.find("isClosingOrClosedForWrites()"),
        "direct outbound buffers must bypass MTProxy dead-for-writes gating",
        failures,
    )
    require(
        "if (isCurrentDirectConnection())" in socket_timeout
        and socket_timeout.find("if (isCurrentDirectConnection())") < socket_timeout.find("isCurrentMtProxyConnection()"),
        "direct timeout handling must return through the stock path before MTProxy diagnostics",
        failures,
    )

    if failures:
        print("Direct transport isolation check failed:", file=sys.stderr)
        for failure in failures:
            print(f" - {failure}", file=sys.stderr)
        return 1
    print("Direct transport isolation check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
