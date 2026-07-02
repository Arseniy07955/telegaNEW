/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#ifndef MTPROXYTERMINALDIAGNOSTIC_H
#define MTPROXYTERMINALDIAGNOSTIC_H

#include <stdint.h>
#include <string>

#include "MtProxyStartupTimeline.h"

// Pure derivation of the terminal close diagnostic from module-visible
// facts. ConnectionSocket::deriveMtProxyTerminalDiagnostic is a thin
// wrapper: it gates on isCurrentMtProxyConnection()/reason and performs
// the logging side effects; the classification decision lives here so it
// is host-compiled and cannot silently diverge from the phase contract.
namespace MtProxyPhase {

struct TerminalDiagnosticInput {
    // Current proxyCheckDiagnostic value at close time.
    const char *currentDiagnostic = "";
    const MtProxyStartupTimeline *timeline = nullptr;
    bool socketConnectedLogged = false;
    int32_t closeReason = 0;
    // errno captured on the socket (0 when none).
    int32_t socketError = 0;
};

struct TerminalDiagnosticResult {
    std::string diagnostic;
    // True when the diagnostic was re-derived from the local startup
    // timeline; the caller re-runs its pre-TCP timeout classification for
    // logging in that case (matching the historical side-effect order).
    bool timelineDerived = false;
};

TerminalDiagnosticResult deriveTerminalDiagnostic(const TerminalDiagnosticInput &input);

}

#endif
