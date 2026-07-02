#ifndef MTPROXYFAILUREEVIDENCE_H
#define MTPROXYFAILUREEVIDENCE_H

#include <cstddef>
#include <cstdint>
#include <string>

enum class MtProxyFailureEvidenceKind : uint8_t {
    None,
    PreTcpLocalWait,
    DnsFailure,
    TcpFailure,
    NoBytesAfterClientHello,
    ServerBytesParserFailure,
    ServerHelloHmacMismatch,
    PostHandshakeNoAppData,
    ConfigInvalidSecret,
    CancelledOrShadowed,
};

MtProxyFailureEvidenceKind mtProxyEvidenceForPhase(const std::string &phase, size_t responseBytes);
const char *mtProxyFailureEvidenceName(MtProxyFailureEvidenceKind kind);

#endif
