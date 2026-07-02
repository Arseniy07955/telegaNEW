#ifndef MTPROXYSERVERFLIGHTPARSER_H
#define MTPROXYSERVERFLIGHTPARSER_H

#include "MtProxyPhaseContract.h"

#include <cstddef>
#include <cstdint>
#include <string>

static constexpr size_t MT_PROXY_SERVER_FLIGHT_MAX_BYTES = 64 * 1024;

struct MtProxyServerFlightRecordInfo {
    int32_t recordType = -1;
    int32_t tlsMajor = -1;
    int32_t tlsMinor = -1;
    int32_t recordLength = -1;
    int32_t alertLevel = -1;
    int32_t alertDescription = -1;
};

struct MtProxyServerFlightParseResult {
    bool waitMore = false;
    bool matched = false;
    bool invalid = false;
    size_t matchedBytes = 0;
    size_t appFlightBytes = 0;
    size_t candidateBytes = 0;
    const char *reason = MtProxyPhase::ServerHelloHmacMismatch;
};

const char *mtProxyServerHelloParserName(int32_t parserMode);
MtProxyServerFlightRecordInfo mtProxyServerFlightReadRecordInfo(const uint8_t *data, size_t size);
bool mtProxyServerFlightLooksLikeTlsAlert(const uint8_t *data, size_t size);
bool mtProxyServerFlightHandshakeRecordNeedsMoreBytes(const uint8_t *data, size_t size);
MtProxyServerFlightParseResult mtProxyParseServerHelloFlight(const std::string &secret, const uint8_t *clientRandom, const uint8_t *data, size_t size, int32_t parserMode);

#endif
