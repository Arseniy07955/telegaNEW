#include "MtProxySecretDomain.h"

#include "MtProxyAdaptivePolicy.h"
#include "MtProxyPhaseContract.h"

#include <arpa/inet.h>
#include <cctype>
#include <climits>
#include <cstring>
#include <netinet/in.h>
#include <vector>

static std::string trimMtProxySecretDomain(const std::string &domain) {
    size_t begin = 0;
    while (begin < domain.size() && std::isspace((unsigned char) domain[begin])) {
        begin++;
    }
    size_t end = domain.size();
    while (end > begin && std::isspace((unsigned char) domain[end - 1])) {
        end--;
    }
    return domain.substr(begin, end - begin);
}

static bool validateMtProxySecretDomain(const std::string &domain) {
    if (domain.empty() || domain.size() > 253 || domain.front() == '.' || domain.back() == '.') {
        return false;
    }
    size_t labelLength = 0;
    bool labelStartsWithHyphen = false;
    for (size_t i = 0; i < domain.size(); i++) {
        unsigned char c = (unsigned char) domain[i];
        if (c == '.') {
            if (labelLength == 0 || labelLength > 63 || labelStartsWithHyphen || domain[i - 1] == '-') {
                return false;
            }
            labelLength = 0;
            labelStartsWithHyphen = false;
            continue;
        }
        bool valid = std::isalnum(c) || c == '-';
        if (!valid) {
            return false;
        }
        if (labelLength == 0) {
            labelStartsWithHyphen = c == '-';
        }
        labelLength++;
    }
    return labelLength > 0 && labelLength <= 63 && !labelStartsWithHyphen && domain.back() != '-';
}

bool mtProxyIsBlockedZeroAddress(const std::string &ip) {
    struct in_addr parsedAddress;
    if (inet_pton(AF_INET, ip.c_str(), &parsedAddress.s_addr) == 1) {
        return parsedAddress.s_addr == 0;
    }
    struct in6_addr parsedIpv6Address;
    static const struct in6_addr anyIpv6Address = IN6ADDR_ANY_INIT;
    return inet_pton(AF_INET6, ip.c_str(), &parsedIpv6Address) == 1
            && memcmp(&parsedIpv6Address, &anyIpv6Address, sizeof(parsedIpv6Address)) == 0;
}

static const char *sanitizeMtProxySecretDomain(const std::string &rawDomain, std::string *sanitizedDomain, bool *secretDomainSanitized) {
    std::string trimmed = trimMtProxySecretDomain(rawDomain);
    bool hasControl = false;
    if (secretDomainSanitized != nullptr) {
        *secretDomainSanitized = false;
    }
    for (unsigned char c : trimmed) {
        if (std::iscntrl(c)) {
            hasControl = true;
            break;
        }
    }
    if (sanitizedDomain != nullptr) {
        sanitizedDomain->clear();
        sanitizedDomain->reserve(trimmed.size());
        for (unsigned char c : trimmed) {
            if (!std::iscntrl(c)) {
                sanitizedDomain->push_back((char) std::tolower(c));
            }
        }
    }
    const std::string &domain = sanitizedDomain != nullptr ? *sanitizedDomain : trimmed;
    if (!validateMtProxySecretDomain(domain)) {
        return hasControl ? MtProxyPhase::SecretParseInvalidDomainControlChar : MtProxyPhase::SecretParseInvalidDomain;
    }
    if (hasControl && secretDomainSanitized != nullptr) {
        *secretDomainSanitized = true;
    }
    return nullptr;
}

static bool mtProxyUtf8ToCodepoints(const std::string &value, std::vector<uint32_t> *codepoints) {
    if (codepoints == nullptr) {
        return false;
    }
    codepoints->clear();
    for (size_t i = 0; i < value.size();) {
        uint8_t c = (uint8_t) value[i];
        if (c < 0x80) {
            codepoints->push_back(c);
            i++;
        } else if ((c & 0xe0) == 0xc0 && i + 1 < value.size()) {
            uint8_t c1 = (uint8_t) value[i + 1];
            if ((c1 & 0xc0) != 0x80) {
                return false;
            }
            uint32_t cp = ((uint32_t) (c & 0x1f) << 6) | (uint32_t) (c1 & 0x3f);
            if (cp < 0x80) {
                return false;
            }
            codepoints->push_back(cp);
            i += 2;
        } else if ((c & 0xf0) == 0xe0 && i + 2 < value.size()) {
            uint8_t c1 = (uint8_t) value[i + 1];
            uint8_t c2 = (uint8_t) value[i + 2];
            if ((c1 & 0xc0) != 0x80 || (c2 & 0xc0) != 0x80) {
                return false;
            }
            uint32_t cp = ((uint32_t) (c & 0x0f) << 12) | ((uint32_t) (c1 & 0x3f) << 6) | (uint32_t) (c2 & 0x3f);
            if (cp < 0x800 || (cp >= 0xd800 && cp <= 0xdfff)) {
                return false;
            }
            codepoints->push_back(cp);
            i += 3;
        } else if ((c & 0xf8) == 0xf0 && i + 3 < value.size()) {
            uint8_t c1 = (uint8_t) value[i + 1];
            uint8_t c2 = (uint8_t) value[i + 2];
            uint8_t c3 = (uint8_t) value[i + 3];
            if ((c1 & 0xc0) != 0x80 || (c2 & 0xc0) != 0x80 || (c3 & 0xc0) != 0x80) {
                return false;
            }
            uint32_t cp = ((uint32_t) (c & 0x07) << 18) | ((uint32_t) (c1 & 0x3f) << 12) | ((uint32_t) (c2 & 0x3f) << 6) | (uint32_t) (c3 & 0x3f);
            if (cp < 0x10000 || cp > 0x10ffff) {
                return false;
            }
            codepoints->push_back(cp);
            i += 4;
        } else {
            return false;
        }
    }
    return true;
}

static char mtProxyPunycodeDigit(uint32_t value) {
    return (char) (value < 26 ? ('a' + value) : ('0' + (value - 26)));
}

static uint32_t mtProxyPunycodeAdapt(uint32_t delta, uint32_t numpoints, bool firstTime) {
    static constexpr uint32_t base = 36;
    static constexpr uint32_t tmin = 1;
    static constexpr uint32_t tmax = 26;
    static constexpr uint32_t skew = 38;
    static constexpr uint32_t damp = 700;
    delta = firstTime ? delta / damp : delta / 2;
    delta += delta / numpoints;
    uint32_t k = 0;
    while (delta > ((base - tmin) * tmax) / 2) {
        delta /= base - tmin;
        k += base;
    }
    return k + (((base - tmin + 1) * delta) / (delta + skew));
}

static bool mtProxyPunycodeLabel(const std::string &label, std::string *encoded) {
    std::vector<uint32_t> codepoints;
    if (encoded == nullptr || !mtProxyUtf8ToCodepoints(label, &codepoints) || codepoints.empty()) {
        return false;
    }
    bool hasNonBasic = false;
    encoded->clear();
    for (uint32_t cp : codepoints) {
        if (cp < 0x80) {
            unsigned char c = (unsigned char) cp;
            if (!(std::isalnum(c) || c == '-')) {
                return false;
            }
            encoded->push_back((char) std::tolower(c));
        } else {
            hasNonBasic = true;
        }
    }
    if (!hasNonBasic) {
        return validateMtProxySecretDomain(*encoded);
    }
    std::string output = "xn--";
    size_t basicCount = encoded->size();
    output += *encoded;
    if (basicCount > 0) {
        output.push_back('-');
    }
    uint32_t n = 128;
    uint32_t delta = 0;
    uint32_t bias = 72;
    size_t handled = basicCount;
    while (handled < codepoints.size()) {
        uint32_t m = UINT32_MAX;
        for (uint32_t cp : codepoints) {
            if (cp >= n && cp < m) {
                m = cp;
            }
        }
        if (m == UINT32_MAX) {
            return false;
        }
        delta += (m - n) * (uint32_t) (handled + 1);
        n = m;
        for (uint32_t cp : codepoints) {
            if (cp < n) {
                delta++;
            } else if (cp == n) {
                uint32_t q = delta;
                for (uint32_t k = 36;; k += 36) {
                    uint32_t t;
                    if (k <= bias) {
                        t = 1;
                    } else if (k >= bias + 26) {
                        t = 26;
                    } else {
                        t = k - bias;
                    }
                    if (q < t) {
                        break;
                    }
                    output.push_back(mtProxyPunycodeDigit(t + ((q - t) % (36 - t))));
                    q = (q - t) / (36 - t);
                }
                output.push_back(mtProxyPunycodeDigit(q));
                bias = mtProxyPunycodeAdapt(delta, (uint32_t) handled + 1, handled == basicCount);
                delta = 0;
                handled++;
            }
        }
        delta++;
        n++;
    }
    if (output.size() > 63) {
        return false;
    }
    *encoded = output;
    return validateMtProxySecretDomain(*encoded);
}

static std::string mtProxyLowercaseAsciiNoControl(const std::string &value, bool *removedControl) {
    std::string result;
    result.reserve(value.size());
    if (removedControl != nullptr) {
        *removedControl = false;
    }
    for (unsigned char c : value) {
        if (std::iscntrl(c)) {
            if (removedControl != nullptr) {
                *removedControl = true;
            }
            continue;
        }
        result.push_back((char) std::tolower(c));
    }
    return result;
}

static std::string mtProxyNoTrailingDot(std::string value) {
    while (!value.empty() && value.back() == '.') {
        value.pop_back();
    }
    return value;
}

static bool mtProxyPunycodeDomain(const std::string &domain, std::string *punycodeDomain) {
    if (punycodeDomain == nullptr) {
        return false;
    }
    punycodeDomain->clear();
    std::string trimmed = mtProxyNoTrailingDot(domain);
    if (trimmed.empty()) {
        return false;
    }
    size_t start = 0;
    while (start <= trimmed.size()) {
        size_t dot = trimmed.find('.', start);
        std::string label = trimmed.substr(start, dot == std::string::npos ? std::string::npos : dot - start);
        std::string encodedLabel;
        if (!mtProxyPunycodeLabel(label, &encodedLabel)) {
            return false;
        }
        if (!punycodeDomain->empty()) {
            punycodeDomain->push_back('.');
        }
        *punycodeDomain += encodedLabel;
        if (dot == std::string::npos) {
            break;
        }
        start = dot + 1;
    }
    return validateMtProxySecretDomain(*punycodeDomain);
}

MtProxySecretDomainPlan buildMtProxySecretDomainPlan(const std::string &rawDomain) {
    MtProxySecretDomainPlan plan;
    std::string trimmed = trimMtProxySecretDomain(rawDomain);
    bool removedControl = false;
    plan.originalDomain = trimmed;
    plan.sanitizedDomain = mtProxyLowercaseAsciiNoControl(trimmed, &removedControl);
    plan.lowercaseAsciiDomain = mtProxyLowercaseAsciiNoControl(trimmed, nullptr);
    plan.noTrailingDotDomain = mtProxyNoTrailingDot(plan.lowercaseAsciiDomain);
    mtProxyPunycodeDomain(plan.sanitizedDomain, &plan.punycodeDomain);
    plan.sanitized = removedControl && validateMtProxySecretDomain(plan.sanitizedDomain);

    auto addVariant = [&](int32_t variant, const std::string &value) {
        if (validateMtProxySecretDomain(value)) {
            plan.allowedSniVariants |= MtProxyAdaptivePolicy::sniVariantMask(variant);
            if (plan.canonicalDomain.empty()) {
                plan.canonicalDomain = value;
            }
        }
    };

    if (!removedControl) {
        addVariant(MtProxyAdaptivePolicy::SNI_ORIGINAL, plan.originalDomain);
    }
    addVariant(MtProxyAdaptivePolicy::SNI_SANITIZED, plan.sanitizedDomain);
    addVariant(MtProxyAdaptivePolicy::SNI_LOWERCASE_ASCII, plan.lowercaseAsciiDomain);
    addVariant(MtProxyAdaptivePolicy::SNI_NO_TRAILING_DOT, plan.noTrailingDotDomain);
    addVariant(MtProxyAdaptivePolicy::SNI_PUNYCODE, plan.punycodeDomain);

    if (plan.canonicalDomain.empty()) {
        plan.terminalDiagnostic = removedControl ? MtProxyPhase::SecretParseInvalidDomainControlChar : MtProxyPhase::SecretParseInvalidDomain;
    } else {
        plan.allowedSniVariants |= MtProxyAdaptivePolicy::sniVariantMask(MtProxyAdaptivePolicy::SNI_OPTIONAL_NO_SNI);
    }
    return plan;
}

const char *mtProxySecretKindName(const std::string &secret) {
    if (secret.empty()) {
        return "none";
    }
    if (secret.size() >= 17 && secret[0] == '\xdd') {
        return "dd";
    }
    if (secret.size() > 17 && secret[0] == '\xee') {
        return "ee";
    }
    return "legacy";
}
