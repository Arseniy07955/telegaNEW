/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#include "MtProxyTerminalDiagnostic.h"

#include <errno.h>

#include "MtProxyPhaseContract.h"

namespace MtProxyPhase {

TerminalDiagnosticResult deriveTerminalDiagnostic(const TerminalDiagnosticInput &input) {
    TerminalDiagnosticResult result;
    const std::string current = input.currentDiagnostic != nullptr ? input.currentDiagnostic : "";
    // Diagnostics decided pre-I/O (before the socket connects) must survive the close path.
    // Re-deriving them from the startup timeline would replace them with
    // "connection_not_started" (startupActive is true because the socket never connected),
    // which is on the local-scheduler-timeout skip list -> records neither an endpoint
    // cooldown nor a reconnect hold -> reconnect hot loop. The phase set is owned by the
    // shared contract: MtProxyPhase::isPreIoTerminalVerdict (generated).
    if (isPreIoTerminalVerdict(current.c_str())) {
        result.diagnostic = current;
        return result;
    }
    if (input.timeline == nullptr) {
        result.diagnostic = current.empty() ? std::string("unknown_fail") : current;
        return result;
    }
    bool startupActive = input.timeline->hasLocalWait()
            || input.timeline->dnsResolveAttemptStarted()
            || input.timeline->tcpConnectAttemptStarted()
            || !input.socketConnectedLogged;
    if (startupActive) {
        const char *timelineDiagnostic = input.timeline->terminalDiagnostic(input.socketConnectedLogged);
        if (!input.socketConnectedLogged && input.timeline->tcpConnectAttemptStarted()) {
            if (input.socketError == ECONNREFUSED) {
                result.diagnostic = MtProxyPhase::TcpConnectionRefused;
                return result;
            }
            if (input.socketError == ETIMEDOUT || input.closeReason == 2 || current == MtProxyPhase::TcpConnectTimeout) {
                result.diagnostic = MtProxyPhase::TcpConnectTimeout;
                return result;
            }
        }
        result.diagnostic = timelineDiagnostic != nullptr ? timelineDiagnostic : "";
        result.timelineDerived = true;
        return result;
    }
    result.diagnostic = current.empty() ? std::string("unknown_fail") : current;
    return result;
}

}
