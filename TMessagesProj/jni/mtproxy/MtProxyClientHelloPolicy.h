/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#ifndef MTPROXYCLIENTHELLOPOLICY_H
#define MTPROXYCLIENTHELLOPOLICY_H

#include <stddef.h>
#include <stdint.h>
#include <string>

#include "MtProxyOptions.h"

static constexpr size_t MT_PROXY_CANONICAL_CLIENT_HELLO_BYTES = 517;
static constexpr size_t MT_PROXY_MAX_RELAY_CLIENT_HELLO_BYTES = 4096;

enum class MtProxyClientHelloContractIssue : uint8_t {
    None = 0,
    BadRecordHeader,
    TooShort,
    TooLong,
    InconsistentLength,
    SessionIdNot32Bytes,
    FirstCipherNotTls13,
    SniMissing,
    SniMismatch,
};

// Validates the parts of ClientHello that MTProxy checks before accepting the
// connection as FakeTLS. Digest freshness and replay protection are applied
// after template construction, when the HMAC/random is written by the socket.
MtProxyClientHelloContractIssue mtProxyCheckClientHelloContract(
        const uint8_t *data,
        size_t size,
        const std::string &domainFromSecret);

const char *mtProxyClientHelloContractIssueName(
        MtProxyClientHelloContractIssue issue);

// Measured 26 July 2026: ChromeModern and AndroidChrome were answered only
// 3/12 times on a filtered network, while Yandex, Firefox, FirefoxAndroid and
// AndroidOkHttp were answered 12/12 on the same relay in the same minutes.
bool mtProxyTlsProfileWithheld(int32_t profile);
int32_t mtProxyDefaultTlsProfile();
int32_t mtProxyEffectiveWireTlsProfile(int32_t profile);

#endif
