/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#ifndef MTPROXYENDPOINTPOLICY_H
#define MTPROXYENDPOINTPOLICY_H

#include <stdint.h>
#include <netinet/in.h>
#include <string>
#include "MtProxyOptions.h"

class MtProxyEndpointPolicy {
public:
    struct MtProxyEndpointContext {
        std::string endpointKey;
        std::string recipeCacheKey;
        std::string networkEndpointKey;
        std::string dnsCacheKey;
        bool fakeTls = false;
        bool recipeUsesGrease = false;
        bool recipeIsGreaseProbe = false;
        bool classicFallbackAllowed = false;
        int32_t connectionPatternMode = MT_PROXY_CONNECTION_PATTERN_OFF;
        int32_t priority = 0;
    };

    struct CooldownResult {
        bool active = false;
        std::string key;
        int64_t cooldownUntil = 0;
        int64_t remainingMs = 0;
    };

    struct TcpConnectGateResult {
        bool shouldDelay = false;
        int32_t activeTcpConnects = 0;
        uint32_t delayMs = 0;
    };

    struct DnsCoalesceResult {
        bool shouldDelay = false;
        uint32_t delayMs = 0;
    };

    struct FailureResult {
        bool recorded = false;
        bool shadowedByUsableSuccess = false;
        bool recipeExhausted = false;
        std::string stateKey;
        int64_t cooldownMs = 0;
        int64_t usableSuccessRemainingMs = 0;
        int32_t recipeLevel = 0;
        int32_t alternateProfileIndex = 0;
        int32_t cachedRecipeLevel = 0;
        int32_t cachedAlternateProfileIndex = 0;
    };

    struct DataPathSuccessResult {
        bool accepted = false;
        bool cachedRecipe = false;
        int32_t cachedRecipeLevel = 0;
    };

    struct GreaseProbeResult {
        bool useGrease = false;
        bool probe = false;
        bool supported = false;
        bool rejected = false;
    };

    static bool extractSslipIpv4Address(const std::string &host, struct in_addr *address, std::string *literalAddress);
    static std::string networkEndpointKeyFor(const std::string &host, uint16_t port);
    static std::string admissionKeyFor(const std::string &host, uint16_t port, const std::string &domain);
    static std::string endpointKeyFor(const std::string &host, uint16_t port, const char *secretKind, const std::string &domain);
    static std::string dnsCacheKeyFor(const std::string &host, uint16_t port);
    static std::string stateKeyForPhase(const std::string &phase, const std::string &networkEndpointKey, const std::string &endpointKey);
    static bool failureNeedsCooldown(const std::string &diagnostic);
    static bool failureNeedsRecipe(const std::string &diagnostic);
    static int64_t cooldownMs(const std::string &diagnostic, int32_t connectionPatternMode, int32_t priority);
    static CooldownResult readCooldown(const MtProxyEndpointContext &context, int64_t now);
    static TcpConnectGateResult beginTcpConnect(const std::string &networkEndpointKey, bool wasReady);
    static int32_t releaseTcpConnect(const std::string &networkEndpointKey);
    static DnsCoalesceResult beginDnsCoalesce(const std::string &dnsCacheKey, int64_t now);
    static bool useCachedHostAddress(const std::string &dnsCacheKey, int64_t now, std::string *cachedIpv4);
    static void storeResolvedAddress(const std::string &dnsCacheKey, const std::string &ip, int64_t now);
    static FailureResult recordFailure(const MtProxyEndpointContext &context, const std::string &phase, int64_t now);
    static void recordHandshakeOk(const MtProxyEndpointContext &context, const char *reason);
    static DataPathSuccessResult recordDataPathSuccess(const MtProxyEndpointContext &context, const char *reason, int64_t now);
    static bool recordSecretDomainSanitized(const std::string &endpointKey);
    static GreaseProbeResult readGreaseProbeState(const std::string &recipeCacheKey);
    static int32_t recipeLevelForEndpoint(const std::string &endpointKey);
    static int32_t recipeAlternateProfileIndexForEndpoint(const std::string &endpointKey);
    static std::string lastRecipeDiagnosticForEndpoint(const std::string &endpointKey);
    static void resetStateForKey(const std::string &key, int64_t now, bool resetRecipe);
};

#endif
