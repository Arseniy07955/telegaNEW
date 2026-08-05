/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#ifndef TGNET_TRANSPORT_SOCKET_H
#define TGNET_TRANSPORT_SOCKET_H

#include <stdint.h>
#include <sys/socket.h>
#include <string>
#include <vector>

namespace tgnet {
namespace transport {

enum class HandshakePhase : uint8_t {
    None,
    TcpConnected,
    TlsReady,
    WebSocketReady,
    FirstDataReceived,
};

// Transport boundary used by the connection layer. Implementations own their
// native descriptor and expose a byte stream; MTProto does not know how that
// stream is carried on the wire.
class Socket {
public:
    virtual ~Socket() = default;

    virtual bool open(const struct sockaddr *address, socklen_t addressLength, std::string *diagnostic) = 0;
    virtual int fd() const = 0;
    virtual bool onEvent(uint32_t events, std::vector<std::vector<uint8_t>> &payloads, std::string *diagnostic) = 0;
    virtual bool write(const uint8_t *data, uint32_t size, std::string *diagnostic) = 0;
    virtual bool isReady() const = 0;
    virtual bool wantsWrite() const = 0;
    virtual bool isClosed() const = 0;
    virtual HandshakePhase handshakePhase() const = 0;
    virtual const char *transportName() const = 0;
    virtual void timedOut() = 0;
    virtual void close() = 0;
};

} // namespace transport
} // namespace tgnet

#endif
