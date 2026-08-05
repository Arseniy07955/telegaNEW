/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#include "WssSocket.h"
#include "../FileLog.h"
#include "rtc_base/ssl_roots.h"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <openssl/err.h>
#include <openssl/rand.h>
#include <openssl/sha.h>
#include <openssl/x509.h>
#include <sys/epoll.h>
#include <sys/socket.h>
#include <unistd.h>

#include <algorithm>
#include <chrono>
#include <cctype>
#include <cerrno>
#include <cstring>
#include <map>
#include <mutex>

namespace tgnet {
namespace wss {
namespace {

constexpr const char *kOfficialRelayIp = "149.154.167.220";
constexpr const char *kOfficialPath = "/apiws";
constexpr uint32_t kMaxFrame = 2 * 1024 * 1024;
constexpr size_t kMaxHttpHeader = 32 * 1024;
constexpr size_t kMaxPendingOutput = 4 * 1024 * 1024;
constexpr size_t kMaxPendingInput = 4 * 1024 * 1024;
constexpr int64_t kFallbackPreferenceTtlMs = 30 * 60 * 1000;

struct RelayPreference {
    bool preferFallback = false;
    int64_t until = 0;
};

std::mutex relayPreferencesMutex;
std::map<std::string, RelayPreference> relayPreferences;

int64_t monotonicMillis() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
}

std::string relayPreferenceKey(const Route &route) {
    return route.relayHost + ":" + std::to_string(route.relayPort) + ":" + route.domain;
}

bool hasFallback(const Route &route) {
    return !route.relayHostFallback.empty() && route.relayHostFallback != route.relayHost;
}

bool preferFallback(const Route &route) {
    if (!hasFallback(route)) {
        return false;
    }
    std::lock_guard<std::mutex> lock(relayPreferencesMutex);
    auto it = relayPreferences.find(relayPreferenceKey(route));
    if (it == relayPreferences.end()) {
        return false;
    }
    if (it->second.until <= monotonicMillis()) {
        relayPreferences.erase(it);
        return false;
    }
    return it->second.preferFallback;
}

void recordAttemptFailed(const Route &route) {
    if (!hasFallback(route)) {
        return;
    }
    std::lock_guard<std::mutex> lock(relayPreferencesMutex);
    if (route.viaFallback) {
        relayPreferences.erase(relayPreferenceKey(route));
    } else {
        relayPreferences[relayPreferenceKey(route)] = {
                true,
                monotonicMillis() + kFallbackPreferenceTtlMs,
        };
    }
}

void recordUpgradeSucceeded(const Route &route) {
    if (!hasFallback(route)) {
        return;
    }
    std::lock_guard<std::mutex> lock(relayPreferencesMutex);
    if (route.viaFallback) {
        relayPreferences[relayPreferenceKey(route)] = {
                true,
                monotonicMillis() + kFallbackPreferenceTtlMs,
        };
    } else {
        relayPreferences.erase(relayPreferenceKey(route));
    }
}

std::string base64Encode(const uint8_t *data, size_t length) {
    static const char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string result;
    result.reserve(((length + 2) / 3) * 4);
    for (size_t i = 0; i < length; i += 3) {
        uint32_t chunk = static_cast<uint32_t>(data[i]) << 16;
        if (i + 1 < length) {
            chunk |= static_cast<uint32_t>(data[i + 1]) << 8;
        }
        if (i + 2 < length) {
            chunk |= data[i + 2];
        }
        result.push_back(alphabet[(chunk >> 18) & 0x3f]);
        result.push_back(alphabet[(chunk >> 12) & 0x3f]);
        result.push_back(i + 1 < length ? alphabet[(chunk >> 6) & 0x3f] : '=');
        result.push_back(i + 2 < length ? alphabet[chunk & 0x3f] : '=');
    }
    return result;
}

bool loadBundledRoots(SSL_CTX *context) {
    int loaded = 0;
    X509_STORE *store = SSL_CTX_get_cert_store(context);
    for (size_t i = 0; i < sizeof(kSSLCertCertificateList) / sizeof(kSSLCertCertificateList[0]); ++i) {
        const unsigned char *cursor = kSSLCertCertificateList[i];
        X509 *certificate = d2i_X509(nullptr, &cursor, static_cast<long>(kSSLCertCertificateSizeList[i]));
        if (certificate == nullptr) {
            ERR_clear_error();
            continue;
        }
        if (X509_STORE_add_cert(store, certificate) == 1) {
            ++loaded;
        } else {
            // Duplicate roots are harmless when the Android store was loaded.
            ERR_clear_error();
        }
        X509_free(certificate);
    }
    return loaded > 0;
}

SSL_CTX *wssSslContext() {
    static SSL_CTX *context = [] {
        SSL_CTX *created = SSL_CTX_new(TLS_client_method());
        if (created == nullptr) {
            return static_cast<SSL_CTX *>(nullptr);
        }
        SSL_CTX_set_min_proto_version(created, TLS1_2_VERSION);
        SSL_CTX_set_verify(created, SSL_VERIFY_PEER, nullptr);
        const bool androidRoots = SSL_CTX_load_verify_locations(
                created,
                nullptr,
                "/system/etc/security/cacerts") == 1;
        ERR_clear_error();
        const bool bundledRoots = loadBundledRoots(created);
        if (!androidRoots && !bundledRoots) {
            SSL_CTX_free(created);
            return static_cast<SSL_CTX *>(nullptr);
        }
        return created;
    }();
    return context;
}

void setDiagnostic(std::string *diagnostic, const char *value) {
    if (diagnostic != nullptr) {
        *diagnostic = value;
    }
}

} // namespace

bool OfficialRoute(int32_t dcId, bool mediaConnection, bool testBackend, Route *route) {
    if (route == nullptr || testBackend || (dcId != 2 && dcId != 4)) {
        return false;
    }
    Route result;
    result.relayHost = kOfficialRelayIp;
    result.relayPort = 443;
    result.path = kOfficialPath;
    const std::string prefix = dcId == 4 ? "kws4" : "kws2";
    result.domain = prefix + (mediaConnection ? "-1.web.telegram.org" : ".web.telegram.org");
    result.relayHostFallback = result.domain;
    result.viaFallback = preferFallback(result);
    result.connectHost = result.viaFallback ? result.relayHostFallback : result.relayHost;
    *route = std::move(result);
    return true;
}

Socket::Socket(Route route) : routeConfig(std::move(route)) {
}

Socket::~Socket() {
    close();
}

bool Socket::open(const struct sockaddr *address, socklen_t addressLength, std::string *diagnostic) {
    close();
    if (address == nullptr || (address->sa_family != AF_INET && address->sa_family != AF_INET6)) {
        setDiagnostic(diagnostic, "wss_invalid_address");
        return false;
    }
    socketFd = ::socket(address->sa_family, SOCK_STREAM, 0);
    if (socketFd < 0) {
        setDiagnostic(diagnostic, "wss_socket_create_failed");
        return false;
    }
    int yes = 1;
    setsockopt(socketFd, IPPROTO_TCP, TCP_NODELAY, &yes, sizeof(yes));
    if (fcntl(socketFd, F_SETFL, O_NONBLOCK) == -1) {
        setDiagnostic(diagnostic, "wss_socket_nonblocking_failed");
        close();
        return false;
    }
    state = State::TcpConnecting;
    phase = transport::HandshakePhase::None;
    failureRecorded = false;
    const int result = ::connect(socketFd, address, addressLength);
    if (result == 0) {
        const bool connected = finishTcpConnect(diagnostic);
        if (!connected) {
            noteAttemptFailed();
        }
        return connected;
    }
    if (errno != EINPROGRESS) {
        setDiagnostic(diagnostic, "wss_tcp_connect_failed");
        noteAttemptFailed();
        close();
        return false;
    }
    if (LOGS_ENABLED) {
        DEBUG_D("wss_socket connect relay=%s:%u domain=%s fallback=%d",
                routeConfig.connectHost.c_str(),
                static_cast<uint32_t>(routeConfig.relayPort),
                routeConfig.domain.c_str(),
                routeConfig.viaFallback ? 1 : 0);
    }
    return true;
}

int Socket::fd() const {
    return socketFd;
}

bool Socket::finishTcpConnect(std::string *diagnostic) {
    if (state != State::TcpConnecting) {
        return true;
    }
    int error = 0;
    socklen_t length = sizeof(error);
    if (getsockopt(socketFd, SOL_SOCKET, SO_ERROR, &error, &length) != 0 || error != 0) {
        setDiagnostic(diagnostic, "wss_tcp_connect_failed");
        noteAttemptFailed();
        return false;
    }
    phase = transport::HandshakePhase::TcpConnected;
    return startTls(diagnostic);
}

bool Socket::startTls(std::string *diagnostic) {
    SSL_CTX *context = wssSslContext();
    if (context == nullptr) {
        setDiagnostic(diagnostic, "wss_tls_context_failed");
        return false;
    }
    ssl = SSL_new(context);
    if (ssl == nullptr) {
        setDiagnostic(diagnostic, "wss_tls_create_failed");
        return false;
    }
    if (SSL_set_tlsext_host_name(ssl, routeConfig.domain.c_str()) != 1
            || SSL_set1_host(ssl, routeConfig.domain.c_str()) != 1
            || SSL_set_fd(ssl, socketFd) != 1) {
        setDiagnostic(diagnostic, "wss_tls_identity_failed");
        return false;
    }
    SSL_set_connect_state(ssl);
    state = State::TlsHandshake;
    return pumpTls(diagnostic);
}

bool Socket::onEvent(uint32_t events, std::vector<std::vector<uint8_t>> &payloads, std::string *diagnostic) {
    if (state == State::Closed || socketFd < 0) {
        setDiagnostic(diagnostic, "wss_closed");
        return false;
    }
    if (events & (EPOLLERR | EPOLLHUP | EPOLLRDHUP)) {
        setDiagnostic(diagnostic, "wss_socket_closed");
        noteAttemptFailed();
        return false;
    }
    if (state == State::TcpConnecting && (events & (EPOLLIN | EPOLLOUT))) {
        if (!finishTcpConnect(diagnostic)) {
            return false;
        }
    }
    if (state == State::TlsHandshake && !pumpTls(diagnostic)) {
        noteAttemptFailed();
        return false;
    }
    if ((events & (EPOLLIN | EPOLLOUT)) && !flushPending(diagnostic)) {
        noteAttemptFailed();
        return false;
    }
    if ((events & EPOLLIN) && (state == State::HttpRead || state == State::Ready)) {
        if (!readIntoBuffer(diagnostic)) {
            noteAttemptFailed();
            return false;
        }
    }
    if (state == State::HttpRead) {
        std::string parseDiagnostic;
        if (parseHttpResponse(&parseDiagnostic)) {
            state = State::Ready;
            phase = transport::HandshakePhase::WebSocketReady;
            noteUpgradeSucceeded();
            if (LOGS_ENABLED) {
                DEBUG_D("wss_socket upgrade_ok domain=%s", routeConfig.domain.c_str());
            }
        } else if (!parseDiagnostic.empty()) {
            if (diagnostic != nullptr) {
                *diagnostic = parseDiagnostic;
            }
            noteAttemptFailed();
            return false;
        }
    }
    if (state == State::Ready && !parseFrames(payloads, diagnostic)) {
        return false;
    }
    return true;
}

bool Socket::pumpTls(std::string *diagnostic) {
    if (state != State::TlsHandshake) {
        return true;
    }
    const int result = SSL_connect(ssl);
    if (result == 1) {
        if (SSL_get_verify_result(ssl) != X509_V_OK) {
            setDiagnostic(diagnostic, "wss_tls_verify_failed");
            return false;
        }
        phase = transport::HandshakePhase::TlsReady;
        if (!queueHttpUpgrade(diagnostic)) {
            return false;
        }
        state = State::HttpWrite;
        return true;
    }
    const int error = SSL_get_error(ssl, result);
    if (error == SSL_ERROR_WANT_READ || error == SSL_ERROR_WANT_WRITE) {
        return true;
    }
    setDiagnostic(diagnostic, "wss_tls_failed");
    return false;
}

bool Socket::queueHttpUpgrade(std::string *diagnostic) {
    uint8_t randomKey[16];
    if (RAND_bytes(randomKey, sizeof(randomKey)) != 1) {
        setDiagnostic(diagnostic, "wss_random_failed");
        return false;
    }
    secWebSocketKey = base64Encode(randomKey, sizeof(randomKey));
    std::string host = routeConfig.domain;
    if (routeConfig.relayPort != 443) {
        host += ":" + std::to_string(static_cast<uint32_t>(routeConfig.relayPort));
    }
    const std::string request =
            "GET " + routeConfig.path + " HTTP/1.1\r\n"
            "Host: " + host + "\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: " + secWebSocketKey + "\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Protocol: binary\r\n"
            "Origin: https://web.telegram.org\r\n"
            "User-Agent: Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36\r\n"
            "\r\n";
    pendingOutput.assign(request.begin(), request.end());
    pendingOutputOffset = 0;
    return true;
}

bool Socket::flushPending(std::string *diagnostic) {
    while (pendingOutputOffset < pendingOutput.size()) {
        const int result = SSL_write(
                ssl,
                pendingOutput.data() + pendingOutputOffset,
                static_cast<int>(pendingOutput.size() - pendingOutputOffset));
        if (result > 0) {
            pendingOutputOffset += static_cast<size_t>(result);
            continue;
        }
        const int error = SSL_get_error(ssl, result);
        if (error == SSL_ERROR_WANT_READ || error == SSL_ERROR_WANT_WRITE) {
            return true;
        }
        setDiagnostic(diagnostic, "wss_write_failed");
        return false;
    }
    if (!pendingOutput.empty()) {
        pendingOutput.clear();
        pendingOutputOffset = 0;
        if (state == State::HttpWrite) {
            state = State::HttpRead;
        }
    }
    return true;
}

bool Socket::readIntoBuffer(std::string *diagnostic) {
    uint8_t buffer[16 * 1024];
    while (true) {
        const int result = SSL_read(ssl, buffer, sizeof(buffer));
        if (result > 0) {
            inputBuffer.insert(inputBuffer.end(), buffer, buffer + result);
            if (inputBuffer.size() > kMaxPendingInput) {
                setDiagnostic(diagnostic, "wss_read_buffer_full");
                return false;
            }
            continue;
        }
        const int error = SSL_get_error(ssl, result);
        if (error == SSL_ERROR_WANT_READ || error == SSL_ERROR_WANT_WRITE) {
            return true;
        }
        setDiagnostic(diagnostic, result == 0 || error == SSL_ERROR_ZERO_RETURN
                ? "wss_recv_eof"
                : "wss_read_failed");
        return false;
    }
}

bool Socket::parseHttpResponse(std::string *diagnostic) {
    static const uint8_t delimiter[] = {'\r', '\n', '\r', '\n'};
    auto end = std::search(inputBuffer.begin(), inputBuffer.end(), delimiter, delimiter + sizeof(delimiter));
    if (end == inputBuffer.end()) {
        if (inputBuffer.size() > kMaxHttpHeader) {
            setDiagnostic(diagnostic, "wss_http_response_too_large");
        }
        return false;
    }
    const std::string response(inputBuffer.begin(), end);
    inputBuffer.erase(inputBuffer.begin(), end + sizeof(delimiter));
    if (response.compare(0, 12, "HTTP/1.1 101") != 0 && response.compare(0, 12, "HTTP/1.0 101") != 0) {
        setDiagnostic(diagnostic, "wss_http_upgrade_failed");
        return false;
    }
    const std::string acceptInput = secWebSocketKey + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
    uint8_t acceptHash[SHA_DIGEST_LENGTH];
    SHA1(reinterpret_cast<const uint8_t *>(acceptInput.data()), acceptInput.size(), acceptHash);
    const std::string expectedAccept = base64Encode(acceptHash, sizeof(acceptHash));
    std::string lowerResponse = response;
    std::transform(lowerResponse.begin(), lowerResponse.end(), lowerResponse.begin(),
            [](unsigned char value) { return static_cast<char>(std::tolower(value)); });
    const std::string marker = "sec-websocket-accept:";
    const size_t position = lowerResponse.find(marker);
    if (position == std::string::npos) {
        setDiagnostic(diagnostic, "wss_accept_missing");
        return false;
    }
    const size_t valueStart = position + marker.size();
    size_t valueEnd = response.find("\r\n", valueStart);
    if (valueEnd == std::string::npos) {
        valueEnd = response.size();
    }
    std::string value = response.substr(valueStart, valueEnd - valueStart);
    const size_t first = value.find_first_not_of(" \t");
    const size_t last = value.find_last_not_of(" \t\r");
    value = first == std::string::npos ? std::string() : value.substr(first, last - first + 1);
    if (value != expectedAccept) {
        setDiagnostic(diagnostic, "wss_accept_mismatch");
        return false;
    }
    return true;
}

bool Socket::parseFrames(std::vector<std::vector<uint8_t>> &payloads, std::string *diagnostic) {
    while (inputBuffer.size() >= 2) {
        const uint8_t first = inputBuffer[0];
        const uint8_t second = inputBuffer[1];
        const bool fin = (first & 0x80) != 0;
        const uint8_t opcode = first & 0x0f;
        const bool control = (opcode & 0x08) != 0;
        if ((first & 0x70) != 0 || (second & 0x80) != 0) {
            setDiagnostic(diagnostic, "wss_invalid_frame_flags");
            return false;
        }
        uint64_t length = second & 0x7f;
        size_t headerLength = 2;
        if (length == 126) {
            if (inputBuffer.size() < 4) {
                return true;
            }
            length = (static_cast<uint64_t>(inputBuffer[2]) << 8) | inputBuffer[3];
            headerLength = 4;
        } else if (length == 127) {
            if (inputBuffer.size() < 10) {
                return true;
            }
            length = 0;
            for (int i = 0; i < 8; ++i) {
                length = (length << 8) | inputBuffer[2 + i];
            }
            headerLength = 10;
        }
        if (length > kMaxFrame || (control && (!fin || length > 125))) {
            setDiagnostic(diagnostic, "wss_frame_too_large");
            return false;
        }
        if (inputBuffer.size() < headerLength + static_cast<size_t>(length)) {
            return true;
        }
        const uint8_t *payload = inputBuffer.data() + headerLength;
        if (opcode == 0x8) {
            setDiagnostic(diagnostic, "wss_close_frame");
            return false;
        } else if (opcode == 0x9) {
            if (!queueFrame(0xA, payload, static_cast<uint32_t>(length), diagnostic)) {
                return false;
            }
        } else if (opcode == 0xA) {
            // Pong.
        } else if (opcode == 0x2) {
            if (fragmentedMessage) {
                setDiagnostic(diagnostic, "wss_unexpected_binary_frame");
                return false;
            }
            fragmentedMessage = !fin;
            if (length > 0) {
                payloads.emplace_back(payload, payload + length);
            }
        } else if (opcode == 0x0) {
            if (!fragmentedMessage) {
                setDiagnostic(diagnostic, "wss_unexpected_continuation");
                return false;
            }
            fragmentedMessage = !fin;
            if (length > 0) {
                payloads.emplace_back(payload, payload + length);
            }
        } else {
            setDiagnostic(diagnostic, "wss_unsupported_opcode");
            return false;
        }
        inputBuffer.erase(inputBuffer.begin(), inputBuffer.begin() + headerLength + static_cast<size_t>(length));
    }
    if (!payloads.empty() && phase == transport::HandshakePhase::WebSocketReady) {
        phase = transport::HandshakePhase::FirstDataReceived;
    }
    return true;
}

bool Socket::queueFrame(uint8_t opcode, const uint8_t *data, uint32_t size, std::string *diagnostic) {
    if (size > 0 && data == nullptr) {
        setDiagnostic(diagnostic, "wss_invalid_payload");
        return false;
    }
    if (size > kMaxFrame) {
        setDiagnostic(diagnostic, "wss_frame_too_large");
        return false;
    }
    uint8_t mask[4];
    if (RAND_bytes(mask, sizeof(mask)) != 1) {
        setDiagnostic(diagnostic, "wss_random_failed");
        return false;
    }
    if (pendingOutputOffset > 0) {
        pendingOutput.erase(pendingOutput.begin(), pendingOutput.begin() + pendingOutputOffset);
        pendingOutputOffset = 0;
    }
    const size_t frameOverhead = size < 126 ? 6 : (size <= 0xffff ? 8 : 14);
    if (pendingOutput.size() + frameOverhead + size > kMaxPendingOutput) {
        setDiagnostic(diagnostic, "wss_write_queue_full");
        return false;
    }
    pendingOutput.push_back(static_cast<uint8_t>(0x80 | opcode));
    if (size < 126) {
        pendingOutput.push_back(static_cast<uint8_t>(0x80 | size));
    } else if (size <= 0xffff) {
        pendingOutput.push_back(0x80 | 126);
        pendingOutput.push_back(static_cast<uint8_t>((size >> 8) & 0xff));
        pendingOutput.push_back(static_cast<uint8_t>(size & 0xff));
    } else {
        pendingOutput.push_back(0x80 | 127);
        for (int i = 7; i >= 0; --i) {
            pendingOutput.push_back(static_cast<uint8_t>((static_cast<uint64_t>(size) >> (i * 8)) & 0xff));
        }
    }
    pendingOutput.insert(pendingOutput.end(), mask, mask + sizeof(mask));
    for (uint32_t i = 0; i < size; ++i) {
        pendingOutput.push_back(data[i] ^ mask[i % sizeof(mask)]);
    }
    return true;
}

bool Socket::write(const uint8_t *data, uint32_t size, std::string *diagnostic) {
    if (state != State::Ready) {
        setDiagnostic(diagnostic, "wss_not_ready");
        return false;
    }
    return queueFrame(0x2, data, size, diagnostic);
}

bool Socket::isReady() const {
    return state == State::Ready;
}

bool Socket::wantsWrite() const {
    return state == State::TcpConnecting
            || state == State::TlsHandshake
            || pendingOutputOffset < pendingOutput.size();
}

bool Socket::isClosed() const {
    return state == State::Closed;
}

transport::HandshakePhase Socket::handshakePhase() const {
    return phase;
}

const char *Socket::transportName() const {
    return "WSS";
}

void Socket::timedOut() {
    if (!isReady()) {
        noteAttemptFailed();
    }
}

void Socket::noteAttemptFailed() {
    if (!failureRecorded && state != State::Ready) {
        failureRecorded = true;
        recordAttemptFailed(routeConfig);
    }
}

void Socket::noteUpgradeSucceeded() {
    failureRecorded = false;
    recordUpgradeSucceeded(routeConfig);
}

void Socket::close() {
    if (ssl != nullptr) {
        SSL_free(ssl);
        ssl = nullptr;
    }
    if (socketFd >= 0) {
        ::close(socketFd);
        socketFd = -1;
    }
    state = State::Closed;
    phase = transport::HandshakePhase::None;
    pendingOutput.clear();
    pendingOutputOffset = 0;
    inputBuffer.clear();
    fragmentedMessage = false;
}

const Route &Socket::route() const {
    return routeConfig;
}

std::unique_ptr<transport::Socket> CreateSocket(Route route) {
    return std::unique_ptr<transport::Socket>(new Socket(std::move(route)));
}

} // namespace wss
} // namespace tgnet
