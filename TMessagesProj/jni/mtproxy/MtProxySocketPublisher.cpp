#include "MtProxySocketPublisher.h"

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
    // The phase set is generated from Tools/mtproxy_phase_contract.py
    // (observation_facade=True) into MtProxyPhaseClassification.h.
    return MtProxyPhase::isObservationFacadePhase(phase);
}
