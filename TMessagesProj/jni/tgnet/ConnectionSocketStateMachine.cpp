/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#include "ConnectionSocketStateMachine.h"
#include <cerrno>
#include <unistd.h>
#include <cstring>
#include "FileLog.h"

const char *ConnectionSocketStateMachine::lifecycleName(LifecycleState state) const {
    switch (state) {
        case LifecycleState::Idle:
            return "idle";
        case LifecycleState::Prepared:
            return "prepared";
        case LifecycleState::WaitingGate:
            return "waiting_gate";
        case LifecycleState::TcpConnecting:
            return "tcp_connecting";
        case LifecycleState::EpollRegistered:
            return "epoll_registered";
        case LifecycleState::ProxyHandshake:
            return "proxy_handshake";
        case LifecycleState::FakeTlsHandshake:
            return "faketls_handshake";
        case LifecycleState::MtprotoReady:
            return "mtproto_ready";
        case LifecycleState::Closing:
            return "closing";
    }
    return "unknown";
}

const char *ConnectionSocketStateMachine::transportModeName(TransportMode mode) const {
    switch (mode) {
        case TransportMode::None:
            return "none";
        case TransportMode::Direct:
            return "direct";
        case TransportMode::Socks5:
            return "socks5";
        case TransportMode::PlainMtProxy:
            return "plain_mtproxy";
        case TransportMode::FakeTlsMtProxy:
            return "faketls_mtproxy";
        case TransportMode::Wss:
            return "wss";
    }
    return "unknown";
}

const ConnectionSocketStateMachine::ActionRule *ConnectionSocketStateMachine::findActionRule(const char *action) const {
    if (action == nullptr) {
        return nullptr;
    }
    static const ActionRule rules[] = {
            {"create_wss_socket", LifecycleState::Prepared, TransportSocketPolicy::NoSocket, -1, -1, false, false},
            {"create_wss_ipv6_socket", LifecycleState::Prepared, TransportSocketPolicy::NoSocket, -1, -1, false, false},
            {"create_proxy_socket", LifecycleState::Prepared, TransportSocketPolicy::NoSocket, -1, -1, false, false},
            {"create_direct_socket", LifecycleState::Prepared, TransportSocketPolicy::NoSocket, -1, -1, false, false},
            {"configure_socket", LifecycleState::Prepared, TransportSocketPolicy::OpenWithoutEpoll, -1, -1, false, false},
            {"connect", LifecycleState::TcpConnecting, TransportSocketPolicy::OpenWithoutEpoll, -1, -1, false, false},
            {"epoll_ctl_add", LifecycleState::TcpConnecting, TransportSocketPolicy::OpenWithoutEpoll, -1, -1, false, false},
            {"checkSocketError", LifecycleState::EpollRegistered, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"checkSocketError", LifecycleState::ProxyHandshake, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"checkSocketError", LifecycleState::FakeTlsHandshake, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"checkSocketError", LifecycleState::MtprotoReady, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"onEvent", LifecycleState::EpollRegistered, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"onEvent", LifecycleState::ProxyHandshake, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"onEvent", LifecycleState::FakeTlsHandshake, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"onEvent", LifecycleState::MtprotoReady, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"adjustWriteOp", LifecycleState::EpollRegistered, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"adjustWriteOp", LifecycleState::ProxyHandshake, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"adjustWriteOp", LifecycleState::FakeTlsHandshake, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"adjustWriteOp", LifecycleState::MtprotoReady, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"sendPendingClientHello", LifecycleState::FakeTlsHandshake, TransportSocketPolicy::LiveEpoll, 11, -1, false, false},
            {"sendPendingTlsFrame", LifecycleState::MtprotoReady, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"raw_client_hello_send", LifecycleState::FakeTlsHandshake, TransportSocketPolicy::LiveEpoll, 11, -1, false, false},
            {"raw_tls_frame_send", LifecycleState::MtprotoReady, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"socks_method_select", LifecycleState::EpollRegistered, TransportSocketPolicy::LiveEpoll, 1, -1, false, false},
            {"socks_auth", LifecycleState::EpollRegistered, TransportSocketPolicy::LiveEpoll, 3, -1, false, false},
            {"socks_connect", LifecycleState::EpollRegistered, TransportSocketPolicy::LiveEpoll, 5, -1, false, false},
            {"raw_socks_method_send", LifecycleState::EpollRegistered, TransportSocketPolicy::LiveEpoll, 1, -1, false, false},
            {"raw_socks_auth_send", LifecycleState::EpollRegistered, TransportSocketPolicy::LiveEpoll, 3, -1, false, false},
            {"raw_socks_connect_send", LifecycleState::EpollRegistered, TransportSocketPolicy::LiveEpoll, 5, -1, false, false},
            {"sendPlainMtProtoPayload", LifecycleState::MtprotoReady, TransportSocketPolicy::LiveEpoll, 0, 0, false, false},
            {"raw_plain_mtproto_send", LifecycleState::MtprotoReady, TransportSocketPolicy::LiveEpoll, 0, 0, false, false},
            {"raw_socket_recv", LifecycleState::EpollRegistered, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"raw_socket_recv", LifecycleState::ProxyHandshake, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"raw_socket_recv", LifecycleState::FakeTlsHandshake, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"raw_socket_recv", LifecycleState::MtprotoReady, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"closeSocket", LifecycleState::Prepared, TransportSocketPolicy::None, -1, -1, false, false},
            {"closeSocket", LifecycleState::WaitingGate, TransportSocketPolicy::None, -1, -1, false, false},
            {"closeSocket", LifecycleState::TcpConnecting, TransportSocketPolicy::None, -1, -1, false, false},
            {"closeSocket", LifecycleState::EpollRegistered, TransportSocketPolicy::None, -1, -1, false, false},
            {"closeSocket", LifecycleState::ProxyHandshake, TransportSocketPolicy::None, -1, -1, false, false},
            {"closeSocket", LifecycleState::FakeTlsHandshake, TransportSocketPolicy::None, -1, -1, false, false},
            {"closeSocket", LifecycleState::MtprotoReady, TransportSocketPolicy::None, -1, -1, false, false},
            {"closeSocket", LifecycleState::Closing, TransportSocketPolicy::None, -1, -1, false, false},
            {"closeSocket_cleanup", LifecycleState::Closing, TransportSocketPolicy::None, -1, -1, false, false},
            {"epoll_ctl_del", LifecycleState::Closing, TransportSocketPolicy::LiveEpoll, -1, -1, false, false},
            {"close_native_socket", LifecycleState::Closing, TransportSocketPolicy::OpenWithoutEpoll, -1, -1, false, false},
            {"releaseProxyHandshakeAdmission", LifecycleState::WaitingGate, TransportSocketPolicy::None, -1, -1, false, false},
            {"releaseProxyHandshakeAdmission", LifecycleState::TcpConnecting, TransportSocketPolicy::None, -1, -1, false, false},
            {"releaseProxyHandshakeAdmission", LifecycleState::EpollRegistered, TransportSocketPolicy::None, -1, -1, false, false},
            {"releaseProxyHandshakeAdmission", LifecycleState::ProxyHandshake, TransportSocketPolicy::None, -1, -1, false, false},
            {"releaseProxyHandshakeAdmission", LifecycleState::FakeTlsHandshake, TransportSocketPolicy::None, -1, -1, false, false},
            {"releaseProxyHandshakeAdmission", LifecycleState::MtprotoReady, TransportSocketPolicy::None, -1, -1, false, false},
            {"releaseProxyHandshakeAdmission", LifecycleState::Closing, TransportSocketPolicy::None, -1, -1, false, false},
            {"host_resolve_start", LifecycleState::Prepared, TransportSocketPolicy::None, -1, -1, false, false},
            {"host_resolve_start", LifecycleState::WaitingGate, TransportSocketPolicy::None, -1, -1, false, false},
            {"host_resolve_callback", LifecycleState::WaitingGate, TransportSocketPolicy::None, -1, -1, false, false},
            {"wss_ready", LifecycleState::MtprotoReady, TransportSocketPolicy::None, -1, -1, true, true},
            {"on_connected", LifecycleState::MtprotoReady, TransportSocketPolicy::None, -1, -1, false, false},
            {"first_tls_app_recv", LifecycleState::MtprotoReady, TransportSocketPolicy::None, -1, -1, false, false},
            {"first_mtproxy_packet_recv", LifecycleState::MtprotoReady, TransportSocketPolicy::None, -1, -1, false, false},
            {"wss_payload_recv", LifecycleState::MtprotoReady, TransportSocketPolicy::None, -1, -1, true, true},
            {"sendWssFrame", LifecycleState::MtprotoReady, TransportSocketPolicy::LiveEpoll, -1, -1, true, true},
            {"writeBufferRaw", LifecycleState::Prepared, TransportSocketPolicy::None, -1, -1, false, false},
            {"writeBufferRaw", LifecycleState::WaitingGate, TransportSocketPolicy::None, -1, -1, false, false},
            {"writeBufferRaw", LifecycleState::TcpConnecting, TransportSocketPolicy::None, -1, -1, false, false},
            {"writeBufferRaw", LifecycleState::EpollRegistered, TransportSocketPolicy::None, -1, -1, false, false},
            {"writeBufferRaw", LifecycleState::ProxyHandshake, TransportSocketPolicy::None, -1, -1, false, false},
            {"writeBufferRaw", LifecycleState::FakeTlsHandshake, TransportSocketPolicy::None, -1, -1, false, false},
            {"writeBufferRaw", LifecycleState::MtprotoReady, TransportSocketPolicy::None, -1, -1, false, false},
            {"writeBuffer", LifecycleState::Prepared, TransportSocketPolicy::None, -1, -1, false, false},
            {"writeBuffer", LifecycleState::WaitingGate, TransportSocketPolicy::None, -1, -1, false, false},
            {"writeBuffer", LifecycleState::TcpConnecting, TransportSocketPolicy::None, -1, -1, false, false},
            {"writeBuffer", LifecycleState::EpollRegistered, TransportSocketPolicy::None, -1, -1, false, false},
            {"writeBuffer", LifecycleState::ProxyHandshake, TransportSocketPolicy::None, -1, -1, false, false},
            {"writeBuffer", LifecycleState::FakeTlsHandshake, TransportSocketPolicy::None, -1, -1, false, false},
            {"writeBuffer", LifecycleState::MtprotoReady, TransportSocketPolicy::None, -1, -1, false, false},
    };
    for (const ActionRule &rule : rules) {
        if (strcmp(action, rule.action) == 0 && diagnostics.lifecycle == rule.state) {
            return &rule;
        }
    }
    return nullptr;
}

bool ConnectionSocketStateMachine::can(const char *action) const {
    const ActionRule *rule = findActionRule(action);
    if (rule == nullptr) {
        return false;
    }
    if (rule->socketPolicy == TransportSocketPolicy::LiveEpoll && (socket.fd < 0 || !epoll.registered)) {
        return false;
    }
    if (rule->socketPolicy == TransportSocketPolicy::NoSocket && (socket.fd >= 0 || epoll.registered)) {
        return false;
    }
    if (rule->socketPolicy == TransportSocketPolicy::OpenWithoutEpoll && (socket.fd < 0 || epoll.registered)) {
        return false;
    }
    if (rule->expectedProxyAuthState >= 0 && socks.proxyAuthState != (uint8_t) rule->expectedProxyAuthState) {
        return false;
    }
    if (rule->expectedTlsState >= 0 && fakeTls.tlsState != rule->expectedTlsState) {
        return false;
    }
    if (rule->requireWssTransport && !wss.active) {
        return false;
    }
    if (rule->requireWssReady && (wss.transport == nullptr || !wss.transport->isReady())) {
        return false;
    }
    return true;
}

bool ConnectionSocketStateMachine::isAllowedTransition(LifecycleState previous, LifecycleState next) const {
    if (previous == next || next == LifecycleState::Closing) {
        return true;
    }
    static const struct {
        LifecycleState previous;
        LifecycleState next;
    } transitions[] = {
            {LifecycleState::Idle, LifecycleState::Prepared},
            {LifecycleState::Prepared, LifecycleState::WaitingGate},
            {LifecycleState::Prepared, LifecycleState::TcpConnecting},
            {LifecycleState::WaitingGate, LifecycleState::TcpConnecting},
            {LifecycleState::TcpConnecting, LifecycleState::EpollRegistered},
            {LifecycleState::EpollRegistered, LifecycleState::ProxyHandshake},
            {LifecycleState::EpollRegistered, LifecycleState::FakeTlsHandshake},
            {LifecycleState::EpollRegistered, LifecycleState::MtprotoReady},
            {LifecycleState::ProxyHandshake, LifecycleState::MtprotoReady},
            {LifecycleState::FakeTlsHandshake, LifecycleState::MtprotoReady},
            {LifecycleState::FakeTlsHandshake, LifecycleState::WaitingGate},
            {LifecycleState::Closing, LifecycleState::Idle},
    };
    for (const auto &transition : transitions) {
        if (transition.previous == previous && transition.next == next) {
            return true;
        }
    }
    return false;
}

void ConnectionSocketStateMachine::setLifecycle(LifecycleState next) {
    diagnostics.lifecycle = next;
}

void ConnectionSocketStateMachine::setTransportMode(TransportMode mode) {
    diagnostics.transportMode = mode;
}

bool ConnectionSocketStateMachine::setSocketFd(int fd) {
    if (socket.fd == fd) {
        return false;
    }
    socket.fd = fd;
    return true;
}

bool ConnectionSocketStateMachine::setEpollRegistered(bool registered) {
    if (epoll.registered == registered) {
        return false;
    }
    epoll.registered = registered;
    return true;
}

bool ConnectionSocketStateMachine::setProxyAuthState(uint8_t state) {
    if (socks.proxyAuthState == state) {
        return false;
    }
    socks.proxyAuthState = state;
    return true;
}

bool ConnectionSocketStateMachine::setTlsState(int8_t state) {
    if (fakeTls.tlsState == state) {
        return false;
    }
    fakeTls.tlsState = state;
    return true;
}

int ConnectionSocketStateMachine::createNativeSocket(int domain, int type, int protocol) {
    return ::socket(domain, type, protocol);
}

bool ConnectionSocketStateMachine::closeNativeSocket(const char *reason) {
    (void) reason;
    if (socket.fd < 0) {
        return true;
    }
    return ::close(socket.fd) == 0;
}

bool ConnectionSocketStateMachine::connectNativeSocket(const sockaddr *address, socklen_t addressLen) {
    if (socket.fd < 0) {
        errno = EBADF;
        return false;
    }
    return ::connect(socket.fd, address, addressLen) != -1 || errno == EINPROGRESS;
}

bool ConnectionSocketStateMachine::epollCtlAdd(int epollFd) {
    if (socket.fd < 0) {
        errno = EBADF;
        return false;
    }
    return ::epoll_ctl(epollFd, EPOLL_CTL_ADD, socket.fd, &socket.eventMask) == 0;
}

bool ConnectionSocketStateMachine::epollCtlMod(int epollFd) {
    if (socket.fd < 0) {
        errno = EBADF;
        return false;
    }
    return ::epoll_ctl(epollFd, EPOLL_CTL_MOD, socket.fd, &socket.eventMask) == 0;
}

bool ConnectionSocketStateMachine::epollCtlDel(int epollFd) {
    if (socket.fd < 0) {
        errno = EBADF;
        return false;
    }
    return ::epoll_ctl(epollFd, EPOLL_CTL_DEL, socket.fd, nullptr) == 0;
}

ssize_t ConnectionSocketStateMachine::sendBytes(const void *bytes, size_t size, int flags) {
    if (socket.fd < 0) {
        errno = EBADF;
        return -1;
    }
    return ::send(socket.fd, bytes, size, flags);
}

ssize_t ConnectionSocketStateMachine::recvBytes(void *bytes, size_t size, int flags) {
    if (socket.fd < 0) {
        errno = EBADF;
        return -1;
    }
    return ::recv(socket.fd, bytes, size, flags);
}
