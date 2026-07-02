#ifndef MTPROXYRECOVERYPOLICY_H
#define MTPROXYRECOVERYPOLICY_H

#include <cstddef>
#include <cstdint>
#include <string>

#include "MtProxyFailureEvidence.h"

enum class MtProxyRecoveryActionKind : uint8_t {
    IgnoreLocal,
    KeepSameRecipeBackoff,
    AdvanceClientHelloOnly,
    AdvanceParserAllowed,
    PostHandshakeShapingBackoff,
    ProfilesExhaustedBackoff,
    TerminalConfigFailure,
};

struct MtProxyRecoveryAction {
    MtProxyRecoveryActionKind kind = MtProxyRecoveryActionKind::IgnoreLocal;
    bool canRotate = false;
    bool canBackoff = false;
    bool terminalConfig = false;
};

MtProxyRecoveryAction mtProxyRecoveryActionForEvidence(MtProxyFailureEvidenceKind evidence);
MtProxyRecoveryAction mtProxyRecoveryActionForPhase(const std::string &phase, size_t responseBytes);
const char *mtProxyRecoveryActionName(MtProxyRecoveryActionKind kind);
bool mtProxyRecoveryActionAdvancesRecipe(MtProxyRecoveryAction action);
bool mtProxyRecoveryActionAllowsParserVariants(MtProxyRecoveryAction action);

#endif
