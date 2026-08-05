/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#include "MtProxyClientHelloPolicy.h"

static uint16_t mtProxyRead16(const uint8_t *data) {
    return (uint16_t) (((uint16_t) data[0] << 8) | data[1]);
}

static uint32_t mtProxyRead24(const uint8_t *data) {
    return ((uint32_t) data[0] << 16) | ((uint32_t) data[1] << 8) | data[2];
}

// The reference relay skips a leading cipher when both bytes have 0xA in the
// low nibble. It does not require the two bytes to be equal as JA4 GREASE
// normalization does, so the relay contract deliberately uses this shape.
static bool mtProxyRelayGreaseCipher(uint16_t value) {
    return (value & 0x0f0fU) == 0x0a0aU;
}

static MtProxyClientHelloContractIssue mtProxyCheckSniExtension(
        const uint8_t *value,
        size_t length,
        const std::string &domainFromSecret) {
    if (length < 5) {
        return MtProxyClientHelloContractIssue::SniMissing;
    }
    size_t namesLength = mtProxyRead16(value);
    if (namesLength + 2 != length) {
        return MtProxyClientHelloContractIssue::InconsistentLength;
    }
    size_t position = 2;
    while (position < length) {
        if (position + 3 > length) {
            return MtProxyClientHelloContractIssue::InconsistentLength;
        }
        uint8_t type = value[position];
        size_t nameLength = mtProxyRead16(value + position + 1);
        position += 3;
        if (position + nameLength > length) {
            return MtProxyClientHelloContractIssue::InconsistentLength;
        }
        if (type == 0) {
            if (nameLength == 0) {
                return MtProxyClientHelloContractIssue::SniMissing;
            }
            if (nameLength != domainFromSecret.size()
                    || std::char_traits<char>::compare(
                            (const char *) value + position,
                            domainFromSecret.data(),
                            nameLength) != 0) {
                return MtProxyClientHelloContractIssue::SniMismatch;
            }
            return MtProxyClientHelloContractIssue::None;
        }
        position += nameLength;
    }
    return MtProxyClientHelloContractIssue::SniMissing;
}

MtProxyClientHelloContractIssue mtProxyCheckClientHelloContract(
        const uint8_t *data,
        size_t size,
        const std::string &domainFromSecret) {
    if (data == nullptr || size < 9) {
        return MtProxyClientHelloContractIssue::BadRecordHeader;
    }
    if (size < MT_PROXY_CANONICAL_CLIENT_HELLO_BYTES) {
        return MtProxyClientHelloContractIssue::TooShort;
    }
    if (size > MT_PROXY_MAX_RELAY_CLIENT_HELLO_BYTES) {
        return MtProxyClientHelloContractIssue::TooLong;
    }
    if (data[0] != 0x16
            || data[1] != 0x03
            || data[2] != 0x01
            || data[5] != 0x01) {
        return MtProxyClientHelloContractIssue::BadRecordHeader;
    }
    if ((size_t) mtProxyRead16(data + 3) + 5 != size
            || (size_t) mtProxyRead24(data + 6) + 9 != size) {
        return MtProxyClientHelloContractIssue::InconsistentLength;
    }

    size_t position = 43;
    if (position >= size) {
        return MtProxyClientHelloContractIssue::InconsistentLength;
    }
    size_t sessionIdLength = data[position++];
    if (sessionIdLength != 32) {
        return MtProxyClientHelloContractIssue::SessionIdNot32Bytes;
    }
    if (position + sessionIdLength + 2 > size) {
        return MtProxyClientHelloContractIssue::InconsistentLength;
    }
    position += sessionIdLength;

    size_t cipherSuitesLength = mtProxyRead16(data + position);
    position += 2;
    if (cipherSuitesLength < 2
            || (cipherSuitesLength % 2) != 0
            || position + cipherSuitesLength > size) {
        return MtProxyClientHelloContractIssue::InconsistentLength;
    }
    size_t cipherSuitesEnd = position + cipherSuitesLength;
    while (position + 1 < cipherSuitesEnd
            && mtProxyRelayGreaseCipher(mtProxyRead16(data + position))) {
        position += 2;
    }
    if (position + 1 >= cipherSuitesEnd) {
        return MtProxyClientHelloContractIssue::FirstCipherNotTls13;
    }
    uint16_t firstCipher = mtProxyRead16(data + position);
    if (firstCipher < 0x1301 || firstCipher > 0x1303) {
        return MtProxyClientHelloContractIssue::FirstCipherNotTls13;
    }
    position = cipherSuitesEnd;

    if (position >= size) {
        return MtProxyClientHelloContractIssue::InconsistentLength;
    }
    size_t compressionMethodsLength = data[position++];
    if (position + compressionMethodsLength + 2 > size) {
        return MtProxyClientHelloContractIssue::InconsistentLength;
    }
    position += compressionMethodsLength;
    size_t extensionsLength = mtProxyRead16(data + position);
    position += 2;
    if (position + extensionsLength != size) {
        return MtProxyClientHelloContractIssue::InconsistentLength;
    }

    bool sawSni = false;
    size_t extensionsEnd = position + extensionsLength;
    while (position < extensionsEnd) {
        if (position + 4 > extensionsEnd) {
            return MtProxyClientHelloContractIssue::InconsistentLength;
        }
        uint16_t extensionType = mtProxyRead16(data + position);
        size_t extensionLength = mtProxyRead16(data + position + 2);
        position += 4;
        if (position + extensionLength > extensionsEnd) {
            return MtProxyClientHelloContractIssue::InconsistentLength;
        }
        if (extensionType == 0x0000) {
            sawSni = true;
            MtProxyClientHelloContractIssue issue = mtProxyCheckSniExtension(
                    data + position,
                    extensionLength,
                    domainFromSecret);
            if (issue != MtProxyClientHelloContractIssue::None) {
                return issue;
            }
        }
        position += extensionLength;
    }
    return sawSni
            ? MtProxyClientHelloContractIssue::None
            : MtProxyClientHelloContractIssue::SniMissing;
}

const char *mtProxyClientHelloContractIssueName(
        MtProxyClientHelloContractIssue issue) {
    switch (issue) {
        case MtProxyClientHelloContractIssue::None:
            return "none";
        case MtProxyClientHelloContractIssue::BadRecordHeader:
            return "bad_record_header";
        case MtProxyClientHelloContractIssue::TooShort:
            return "under_canonical_length";
        case MtProxyClientHelloContractIssue::TooLong:
            return "over_relay_read_limit";
        case MtProxyClientHelloContractIssue::InconsistentLength:
            return "declared_lengths_disagree";
        case MtProxyClientHelloContractIssue::SessionIdNot32Bytes:
            return "session_id_not_32_bytes";
        case MtProxyClientHelloContractIssue::FirstCipherNotTls13:
            return "first_cipher_not_tls13";
        case MtProxyClientHelloContractIssue::SniMissing:
            return "sni_missing";
        case MtProxyClientHelloContractIssue::SniMismatch:
            return "sni_not_from_secret";
    }
    return "unknown";
}

bool mtProxyTlsProfileWithheld(int32_t profile) {
    profile = normalizeMtProxyTlsProfileOption(profile);
    return profile == MT_PROXY_TLS_PROFILE_CHROME_MODERN
            || profile == MT_PROXY_TLS_PROFILE_ANDROID_CHROME;
}

int32_t mtProxyDefaultTlsProfile() {
    // Matches the working tdesktop profile. Its final GREASE extension stays
    // deliberately empty: a byte-perfect browser copy was measured as refused.
    return MT_PROXY_TLS_PROFILE_YANDEX;
}

int32_t mtProxyEffectiveWireTlsProfile(int32_t profile) {
    profile = normalizeMtProxyTlsProfileOption(profile);
    if (profile == MT_PROXY_TLS_PROFILE_AUTO
            || profile == MT_PROXY_TLS_PROFILE_AUTO_ROTATE
            || mtProxyTlsProfileWithheld(profile)) {
        return mtProxyDefaultTlsProfile();
    }
    return profile;
}
