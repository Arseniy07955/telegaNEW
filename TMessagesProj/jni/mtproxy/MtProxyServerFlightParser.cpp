#include "MtProxyServerFlightParser.h"

#include "MtProxyOptions.h"

#include <cstring>
#include <openssl/hmac.h>
#include <vector>

static constexpr size_t MT_PROXY_TLS_DIGEST_SIZE = 32;
static constexpr uint8_t MT_PROXY_TLS_RECORD_CHANGE_CIPHER_SPEC = 0x14;
static constexpr uint8_t MT_PROXY_TLS_RECORD_ALERT = 0x15;
static constexpr uint8_t MT_PROXY_TLS_RECORD_APPLICATION_DATA = 0x17;
static constexpr uint8_t MT_PROXY_TLS_RECORD_HANDSHAKE = 0x16;

static bool mtProxyTlsVersionLooksValid(const uint8_t *data, size_t size) {
    return size >= 3 && data[1] == 0x03 && data[2] <= 0x04;
}

const char *mtProxyServerHelloParserName(int32_t parserMode) {
    switch (normalizeMtProxyServerHelloParserOption(parserMode)) {
        case MT_PROXY_SERVER_HELLO_PARSER_TLS_ALERT_EXACT_DESC:
            return "tolerate_tls_alert_exact_desc";
        case MT_PROXY_SERVER_HELLO_PARSER_FRAGMENTED_SERVER_HELLO:
            return "tolerate_fragmented_server_hello";
        case MT_PROXY_SERVER_HELLO_PARSER_CCS_TICKET_ORDERING:
            return "tolerate_ccs_ticket_ordering";
        case MT_PROXY_SERVER_HELLO_PARSER_EXTRA_RECORDS:
            return "tolerate_extra_records_before_server_hello";
        case MT_PROXY_SERVER_HELLO_PARSER_LENIENT_RECORD:
            return "lenient_record_parser";
        case MT_PROXY_SERVER_HELLO_PARSER_RESERVED:
            return "reserved_hmac_parser";
        case MT_PROXY_SERVER_HELLO_PARSER_STANDARD:
        default:
            return "standard_hmac_parser";
    }
}

MtProxyServerFlightRecordInfo mtProxyServerFlightReadRecordInfo(const uint8_t *data, size_t size) {
    MtProxyServerFlightRecordInfo result;
    if (data == nullptr || size == 0) {
        return result;
    }
    result.recordType = data[0];
    if (size >= 2) {
        result.tlsMajor = data[1];
    }
    if (size >= 3) {
        result.tlsMinor = data[2];
    }
    if (size >= 5) {
        result.recordLength = (int32_t) (((uint32_t) data[3] << 8) + data[4]);
    }
    if (mtProxyServerFlightLooksLikeTlsAlert(data, size)) {
        result.alertLevel = data[5];
        result.alertDescription = data[6];
    }
    return result;
}

bool mtProxyServerFlightLooksLikeTlsAlert(const uint8_t *data, size_t size) {
    if (data == nullptr || size < 5 || data[0] != MT_PROXY_TLS_RECORD_ALERT || !mtProxyTlsVersionLooksValid(data, size)) {
        return false;
    }
    size_t recordLength = ((size_t) data[3] << 8) + data[4];
    return recordLength == 2 && size >= 7;
}

bool mtProxyServerFlightHandshakeRecordNeedsMoreBytes(const uint8_t *data, size_t size) {
    if (data == nullptr || size < 5 || data[0] != MT_PROXY_TLS_RECORD_HANDSHAKE || !mtProxyTlsVersionLooksValid(data, size)) {
        return false;
    }
    size_t recordLength = ((size_t) data[3] << 8) + data[4];
    return size < recordLength + 5;
}

static bool mtProxyVerifyServerHelloHmac(const std::string &secret, const uint8_t *clientRandom, const uint8_t *responseBytes, size_t responseSize) {
    if (responseSize < 43 || responseSize > MT_PROXY_SERVER_FLIGHT_MAX_BYTES) {
        return false;
    }
    std::vector<uint8_t> hmacInput(MT_PROXY_TLS_DIGEST_SIZE + responseSize);
    memcpy(hmacInput.data(), clientRandom, MT_PROXY_TLS_DIGEST_SIZE);
    memcpy(hmacInput.data() + MT_PROXY_TLS_DIGEST_SIZE, responseBytes, responseSize);
    memset(hmacInput.data() + MT_PROXY_TLS_DIGEST_SIZE + 11, 0, MT_PROXY_TLS_DIGEST_SIZE);

    uint8_t digest[MT_PROXY_TLS_DIGEST_SIZE];
    uint32_t outLength = 0;
    HMAC(EVP_sha256(), secret.data(), secret.size(), hmacInput.data(), hmacInput.size(), digest, &outLength);
    return outLength == MT_PROXY_TLS_DIGEST_SIZE && memcmp(digest, responseBytes + 11, MT_PROXY_TLS_DIGEST_SIZE) == 0;
}

MtProxyServerFlightParseResult mtProxyParseServerHelloFlight(const std::string &secret, const uint8_t *clientRandom, const uint8_t *data, size_t size, int32_t parserMode) {
    MtProxyServerFlightParseResult result;
    if (data == nullptr || size < 5) {
        result.waitMore = true;
        result.reason = "server_hello_header_wait";
        return result;
    }
    int32_t normalizedParser = normalizeMtProxyServerHelloParserOption(parserMode);
    bool allowLeadingRecords = normalizedParser == MT_PROXY_SERVER_HELLO_PARSER_LENIENT_RECORD
            || normalizedParser == MT_PROXY_SERVER_HELLO_PARSER_EXTRA_RECORDS
            || normalizedParser == MT_PROXY_SERVER_HELLO_PARSER_CCS_TICKET_ORDERING
            || normalizedParser == MT_PROXY_SERVER_HELLO_PARSER_FRAGMENTED_SERVER_HELLO
            || normalizedParser == MT_PROXY_SERVER_HELLO_PARSER_TLS_ALERT_EXACT_DESC;
    size_t skippedBytes = 0;
    if (allowLeadingRecords) {
        while (skippedBytes + 5 <= size && data[skippedBytes] != MT_PROXY_TLS_RECORD_HANDSHAKE) {
            uint8_t recordType = data[skippedBytes];
            if (data[skippedBytes + 1] != 0x03) {
                break;
            }
            size_t recordLen = ((size_t) data[skippedBytes + 3] << 8) + data[skippedBytes + 4];
            size_t recordEnd = skippedBytes + 5 + recordLen;
            if (recordEnd > MT_PROXY_SERVER_FLIGHT_MAX_BYTES) {
                result.invalid = true;
                result.candidateBytes = skippedBytes;
                result.reason = "leading_record_len_invalid";
                return result;
            }
            if (size < recordEnd) {
                result.waitMore = true;
                result.candidateBytes = recordEnd;
                result.reason = "leading_record_wait";
                return result;
            }
            if (recordType == MT_PROXY_TLS_RECORD_ALERT) {
                result.invalid = true;
                result.candidateBytes = recordEnd;
                result.reason = normalizedParser == MT_PROXY_SERVER_HELLO_PARSER_TLS_ALERT_EXACT_DESC
                        ? "tls_alert_exact_desc"
                        : "tls_alert_before_server_hello";
                return result;
            }
            bool skipRecord = recordType == MT_PROXY_TLS_RECORD_CHANGE_CIPHER_SPEC
                    || (recordType == MT_PROXY_TLS_RECORD_APPLICATION_DATA
                            && normalizedParser != MT_PROXY_SERVER_HELLO_PARSER_CCS_TICKET_ORDERING);
            if (!skipRecord) {
                break;
            }
            skippedBytes = recordEnd;
        }
        if (skippedBytes > 0) {
            if (size - skippedBytes < 5) {
                result.waitMore = true;
                result.candidateBytes = skippedBytes + 5;
                result.reason = "server_hello_header_wait";
                return result;
            }
            data += skippedBytes;
            size -= skippedBytes;
        }
    }
    if (data[0] != MT_PROXY_TLS_RECORD_HANDSHAKE || data[1] != 0x03 || data[2] != 0x03) {
        result.invalid = true;
        result.candidateBytes = skippedBytes;
        result.reason = normalizedParser == MT_PROXY_SERVER_HELLO_PARSER_LENIENT_RECORD
                ? "lenient_hello1_mismatch"
                : "hello1_mismatch";
        return result;
    }
    size_t len1 = ((size_t) data[3] << 8) + data[4];
    if (len1 > MT_PROXY_SERVER_FLIGHT_MAX_BYTES - 16) {
        result.invalid = true;
        result.reason = "len1_invalid";
        return result;
    }
    size_t firstRecordEnd = len1 + 5;
    if (size < firstRecordEnd) {
        result.waitMore = true;
        result.candidateBytes = skippedBytes + firstRecordEnd;
        result.reason = normalizedParser == MT_PROXY_SERVER_HELLO_PARSER_FRAGMENTED_SERVER_HELLO
                ? "fragmented_server_hello_wait"
                : "server_hello_wait_first_record";
        return result;
    }
    if (size < firstRecordEnd + 11) {
        result.waitMore = true;
        result.candidateBytes = skippedBytes + firstRecordEnd + 11;
        result.reason = "server_hello_wait_standard_header";
        return result;
    }
    static const uint8_t hello2[] = {0x14, 0x03, 0x03, 0x00, 0x01, 0x01, 0x17, 0x03, 0x03};
    if (std::memcmp(hello2, data + firstRecordEnd, sizeof(hello2)) != 0) {
        result.invalid = true;
        result.candidateBytes = skippedBytes + firstRecordEnd;
        result.reason = normalizedParser == MT_PROXY_SERVER_HELLO_PARSER_CCS_TICKET_ORDERING
                ? "ccs_ticket_ordering_mismatch"
                : "hello2_mismatch";
        return result;
    }
    size_t len2 = ((size_t) data[firstRecordEnd + 9] << 8) + data[firstRecordEnd + 10];
    if (len2 > MT_PROXY_SERVER_FLIGHT_MAX_BYTES - len1 - 5 - 11) {
        result.invalid = true;
        result.reason = "len2_invalid";
        return result;
    }
    size_t candidateBytes = len2 + len1 + 5 + 11;
    result.candidateBytes = skippedBytes + candidateBytes;
    if (size < candidateBytes) {
        result.waitMore = true;
        result.reason = normalizedParser == MT_PROXY_SERVER_HELLO_PARSER_FRAGMENTED_SERVER_HELLO
                ? "fragmented_server_hello_data_wait"
                : "server_hello_wait_standard_data";
        return result;
    }
    size_t appFlightBytes = len2;
    while (candidateBytes <= size) {
        if (mtProxyVerifyServerHelloHmac(secret, clientRandom, data, candidateBytes)) {
            result.matched = true;
            result.matchedBytes = skippedBytes + candidateBytes;
            result.appFlightBytes = appFlightBytes;
            result.candidateBytes = skippedBytes + candidateBytes;
            result.reason = skippedBytes > 0 ? "lenient_hmac_ok" : "standard_hmac_ok";
            return result;
        }
        if (candidateBytes == size) {
            break;
        }
        if (size - candidateBytes < 5) {
            result.waitMore = true;
            result.candidateBytes = skippedBytes + candidateBytes;
            result.reason = "server_hello_tail_header_wait";
            return result;
        }
        static const uint8_t appHeader[] = {0x17, 0x03, 0x03};
        if (std::memcmp(appHeader, data + candidateBytes, sizeof(appHeader)) != 0) {
            result.invalid = true;
            result.candidateBytes = skippedBytes + candidateBytes;
            result.reason = "server_hello_tail_mismatch";
            return result;
        }
        size_t nextLen = ((size_t) data[candidateBytes + 3] << 8) + data[candidateBytes + 4];
        if (nextLen > MT_PROXY_SERVER_FLIGHT_MAX_BYTES - candidateBytes - 5) {
            result.invalid = true;
            result.candidateBytes = skippedBytes + candidateBytes;
            result.reason = "server_hello_tail_len_invalid";
            return result;
        }
        if (size < candidateBytes + 5 + nextLen) {
            result.waitMore = true;
            result.candidateBytes = skippedBytes + candidateBytes;
            result.reason = "server_hello_tail_data_wait";
            return result;
        }
        candidateBytes += 5 + nextLen;
        appFlightBytes += 5 + nextLen;
        result.candidateBytes = candidateBytes;
    }
    result.reason = MtProxyPhase::ServerHelloHmacMismatch;
    result.candidateBytes = skippedBytes + candidateBytes;
    result.appFlightBytes = appFlightBytes;
    return result;
}
