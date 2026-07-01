#include "MtProxyRecoveryPolicy.h"

#include "MtProxyPhaseContract.h"

static MtProxyRecoveryAction mtProxyRecoveryAction(MtProxyRecoveryActionKind kind, bool canRotate, bool canBackoff, bool terminalConfig) {
    MtProxyRecoveryAction action;
    action.kind = kind;
    action.canRotate = canRotate;
    action.canBackoff = canBackoff;
    action.terminalConfig = terminalConfig;
    return action;
}

MtProxyRecoveryAction mtProxyRecoveryActionForEvidence(MtProxyFailureEvidenceKind evidence) {
    switch (evidence) {
        case MtProxyFailureEvidenceKind::PreTcpLocalWait:
            return mtProxyRecoveryAction(MtProxyRecoveryActionKind::IgnoreLocal, false, false, false);
        case MtProxyFailureEvidenceKind::DnsFailure:
        case MtProxyFailureEvidenceKind::TcpFailure:
            return mtProxyRecoveryAction(MtProxyRecoveryActionKind::KeepSameRecipeBackoff, true, true, false);
        case MtProxyFailureEvidenceKind::NoBytesAfterClientHello:
            return mtProxyRecoveryAction(MtProxyRecoveryActionKind::AdvanceClientHelloOnly, false, false, false);
        case MtProxyFailureEvidenceKind::ServerBytesParserFailure:
        case MtProxyFailureEvidenceKind::ServerHelloHmacMismatch:
            return mtProxyRecoveryAction(MtProxyRecoveryActionKind::AdvanceParserAllowed, false, false, false);
        case MtProxyFailureEvidenceKind::PostHandshakeNoAppData:
            return mtProxyRecoveryAction(MtProxyRecoveryActionKind::PostHandshakeShapingBackoff, true, true, false);
        case MtProxyFailureEvidenceKind::ConfigInvalidSecret:
            return mtProxyRecoveryAction(MtProxyRecoveryActionKind::TerminalConfigFailure, false, false, true);
        case MtProxyFailureEvidenceKind::CancelledOrShadowed:
        case MtProxyFailureEvidenceKind::None:
        default:
            return mtProxyRecoveryAction(MtProxyRecoveryActionKind::IgnoreLocal, false, false, false);
    }
}

MtProxyRecoveryAction mtProxyRecoveryActionForPhase(const std::string &phase, size_t responseBytes) {
    if (phase == MtProxyPhase::HandshakeProfilesExhausted
            || phase == MtProxyPhase::FaketlsNotMtproxyResponse
            || phase == MtProxyPhase::FaketlsNoServerHelloTerminal
            || phase == MtProxyPhase::FaketlsServerClosedTerminal) {
        return mtProxyRecoveryAction(MtProxyRecoveryActionKind::ProfilesExhaustedBackoff, true, true, false);
    }
    return mtProxyRecoveryActionForEvidence(mtProxyEvidenceForPhase(phase, responseBytes));
}

const char *mtProxyRecoveryActionName(MtProxyRecoveryActionKind kind) {
    switch (kind) {
        case MtProxyRecoveryActionKind::KeepSameRecipeBackoff:
            return "keep_same_recipe_backoff";
        case MtProxyRecoveryActionKind::AdvanceClientHelloOnly:
            return "advance_client_hello_only";
        case MtProxyRecoveryActionKind::AdvanceParserAllowed:
            return "advance_parser_allowed";
        case MtProxyRecoveryActionKind::PostHandshakeShapingBackoff:
            return "post_handshake_shaping_backoff";
        case MtProxyRecoveryActionKind::ProfilesExhaustedBackoff:
            return "profiles_exhausted_backoff";
        case MtProxyRecoveryActionKind::TerminalConfigFailure:
            return "terminal_config_failure";
        case MtProxyRecoveryActionKind::IgnoreLocal:
        default:
            return "ignore_local";
    }
}

bool mtProxyRecoveryActionAdvancesRecipe(MtProxyRecoveryAction action) {
    return action.kind == MtProxyRecoveryActionKind::AdvanceClientHelloOnly
            || action.kind == MtProxyRecoveryActionKind::AdvanceParserAllowed;
}

bool mtProxyRecoveryActionAllowsParserVariants(MtProxyRecoveryAction action) {
    return action.kind == MtProxyRecoveryActionKind::AdvanceParserAllowed;
}
