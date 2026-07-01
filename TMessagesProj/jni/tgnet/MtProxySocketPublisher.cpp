#include "MtProxySocketPublisher.h"

#include <cstring>

MtProxySocketObservation mtProxyNormalizeSocketObservation(const MtProxySocketObservation &observation) {
    MtProxySocketObservation normalized = observation;
    if (normalized.phase == nullptr || normalized.phase[0] == '\0') {
        normalized.phase = MtProxyPhase::ConnectionNotStarted;
    }
    if (normalized.reason == nullptr || normalized.reason[0] == '\0') {
        normalized.reason = "unknown";
    }
    return normalized;
}

void mtProxyPublishSocketObservation(const MtProxySocketObservation &observation, const MtProxySocketPublisherCallbacks &callbacks) {
    MtProxySocketObservation normalized = mtProxyNormalizeSocketObservation(observation);
    if (normalized.publishVisibleStage && callbacks.publishVisibleStage) {
        callbacks.publishVisibleStage(normalized);
    }
    if (normalized.recordEndpointFailure && callbacks.recordEndpointFailure) {
        callbacks.recordEndpointFailure(normalized);
    }
}

bool mtProxySocketObservationIsHighRiskPhase(const char *phase) {
    if (phase == nullptr || phase[0] == '\0') {
        return false;
    }
    return strcmp(phase, "recipe_failed") == 0
           || strcmp(phase, MtProxyPhase::HandshakeProfilesExhausted) == 0
           || strcmp(phase, MtProxyPhase::FaketlsNotMtproxyResponse) == 0
           || strcmp(phase, MtProxyPhase::FaketlsNoServerHelloTerminal) == 0
           || strcmp(phase, MtProxyPhase::FaketlsServerClosedTerminal) == 0
           || strcmp(phase, MtProxyPhase::SecretParseInvalidDomainControlChar) == 0
           || strcmp(phase, MtProxyPhase::SecretParseInvalidDomain) == 0
           || strcmp(phase, MtProxyPhase::DnsBlockedZeroAddress) == 0
           || strcmp(phase, MtProxyPhase::PostHandshakeNoAppdata) == 0
           || strcmp(phase, MtProxyPhase::FirstTlsAppRecv) == 0
           || strcmp(phase, MtProxyPhase::FirstMtproxyPacketRecv) == 0;
}
