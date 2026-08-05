/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#ifndef TGNET_WSS_SOCKET_H
#define TGNET_WSS_SOCKET_H

#include "../transport/TransportSocket.h"

#include <openssl/ssl.h>
#include <memory>
#include <string>
#include <vector>

namespace tgnet {
namespace wss {

struct Route {
    std::string relayHost;
    std::string relayHostFallback;
    std::string connectHost;
    uint16_t relayPort = 443;
    std::string domain;
    std::string path = "/apiws";
    bool viaFallback = false;
};

// Telegram's public web relays cover production DC1-DC5. Media connections
// use the corresponding -1 relay, matching Telegram Web's transport catalog.
bool OfficialRoute(int32_t dcId, bool mediaConnection, bool testBackend, Route *route);

class Socket final : public transport::Socket {
public:
    explicit Socket(Route route);
    ~Socket() override;

    bool open(const struct sockaddr *address, socklen_t addressLength, std::string *diagnostic) override;
    int fd() const override;
    bool onEvent(uint32_t events, std::vector<std::vector<uint8_t>> &payloads, std::string *diagnostic) override;
    bool write(const uint8_t *data, uint32_t size, std::string *diagnostic) override;
    bool isReady() const override;
    bool wantsWrite() const override;
    bool isClosed() const override;
    transport::HandshakePhase handshakePhase() const override;
    const char *transportName() const override;
    void timedOut() override;
    void close() override;

    const Route &route() const;

private:
    enum class State : uint8_t {
        TcpConnecting,
        TlsHandshake,
        HttpWrite,
        HttpRead,
        Ready,
        Closed,
    };

    enum class IoWait : uint8_t {
        None,
        Read,
        Write,
    };

    bool finishTcpConnect(std::string *diagnostic);
    bool startTls(std::string *diagnostic);
    bool pumpTls(std::string *diagnostic);
    bool queueHttpUpgrade(std::string *diagnostic);
    bool flushPending(std::string *diagnostic);
    bool readIntoBuffer(std::string *diagnostic);
    bool parseHttpResponse(std::string *diagnostic);
    bool parseFrames(std::vector<std::vector<uint8_t>> &payloads, std::string *diagnostic);
    bool queueFrame(uint8_t opcode, const uint8_t *data, uint32_t size, std::string *diagnostic);
    void setIoWait(IoWait wait, const char *operation);
    void noteAttemptFailed();
    void noteUpgradeSucceeded();
    const char *stateName() const;
    const char *ioWaitName() const;
    Route routeConfig;
    SSL *ssl = nullptr;
    int socketFd = -1;
    State state = State::Closed;
    IoWait ioWait = IoWait::None;
    transport::HandshakePhase phase = transport::HandshakePhase::None;
    std::vector<uint8_t> pendingOutput;
    size_t pendingOutputOffset = 0;
    std::vector<uint8_t> inputBuffer;
    std::string secWebSocketKey;
    bool fragmentedMessage = false;
    bool failureRecorded = false;
};

std::unique_ptr<transport::Socket> CreateSocket(Route route);

} // namespace wss
} // namespace tgnet

#endif
