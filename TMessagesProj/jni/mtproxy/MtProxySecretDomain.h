#ifndef MTPROXYSECRETDOMAIN_H
#define MTPROXYSECRETDOMAIN_H

#include <cstdint>
#include <string>

struct MtProxySecretDomainPlan {
    std::string originalDomain;
    std::string sanitizedDomain;
    std::string lowercaseAsciiDomain;
    std::string noTrailingDotDomain;
    std::string punycodeDomain;
    std::string canonicalDomain;
    uint32_t allowedSniVariants = 0;
    const char *terminalDiagnostic = nullptr;
    bool sanitized = false;
};

MtProxySecretDomainPlan buildMtProxySecretDomainPlan(const std::string &rawDomain);
const char *mtProxySecretKindName(const std::string &secret);
bool mtProxyIsBlockedZeroAddress(const std::string &ip);

#endif
