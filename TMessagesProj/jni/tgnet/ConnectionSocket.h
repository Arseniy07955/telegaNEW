/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 * You should have received a copy of the license in this archive (see LICENSE).
 *
 * Copyright Nikolai Kudashov, 2015-2018.
 */

#ifndef CONNECTIONSOCKET_H
#define CONNECTIONSOCKET_H

#include <sys/epoll.h>
#include <netinet/in.h>
#include <memory>
#include <string>
#include "WssTransport.h"

class NativeByteBuffer;
class ConnectionsManager;
class ByteStream;
class EventObject;
class ByteArray;
class Timer;
class ConnectionSocket {

public:
    ConnectionSocket(int32_t instance);
    virtual ~ConnectionSocket();

    void writeBuffer(uint8_t *data, uint32_t size);
    void writeBuffer(NativeByteBuffer *buffer);
    void openConnection(std::string address, uint16_t port, std::string secret, bool ipv6, int32_t networkType, int32_t datacenterId = 0, bool mediaConnection = false);
    void setTimeout(time_t timeout);
    time_t getTimeout();
    bool isDisconnected();
    bool isCurrentMtProxyConnection();
    void dropConnection();
    void setOverrideProxy(std::string address, uint16_t port, std::string username, std::string password, std::string secret, int32_t mtProxyTlsProfile, int32_t mtProxyClientHelloFragmentation, int32_t mtProxyConnectionPatternMode, int32_t mtProxyRecordSizingMode, int32_t mtProxyTimingMode, int32_t mtProxyStartupCoverMode);
    void onHostNameResolved(std::string host, std::string ip, bool ipv6);
    void setMtProxyHandshakePriority(int32_t priority);
    const char *getProxyCheckDiagnostic();
    bool isProxyCloseDiagnosticSuppressed();

protected:
    int32_t instanceNum;
    void onEvent(uint32_t events);
    bool checkTimeout(int64_t now);
    void resetLastEventTime();
    bool hasTlsHashMismatch();
    void publishProxyConnectionStage(const char *diagnostic);
    void markMtProxyFirstPlainDataSent(uint32_t bytes);
    void markMtProxyFirstPlainDataReceived(uint32_t bytes);
    virtual void onReceivedData(NativeByteBuffer *buffer) = 0;
    virtual void onDisconnected(int32_t reason, int32_t error) = 0;
    virtual void onConnected() = 0;
    virtual bool hasPendingRequests() = 0;

    std::string overrideProxyUser = "";
    std::string overrideProxyPassword = "";
    std::string overrideProxyAddress = "";
    std::string overrideProxySecret = "";
    uint16_t overrideProxyPort = 1080;
    int32_t overrideProxyTlsProfile = 0;
    int32_t overrideProxyClientHelloFragmentation = 0;
    int32_t overrideProxyConnectionPatternMode = 0;
    int32_t overrideProxyRecordSizingMode = 0;
    int32_t overrideProxyTimingMode = 0;
    int32_t overrideProxyStartupCoverMode = 0;

private:
    ByteStream *outgoingByteStream = nullptr;
    struct epoll_event eventMask;
    struct sockaddr_in socketAddress;
    struct sockaddr_in6 socketAddress6;
    int socketFd = -1;
    time_t timeout = 12;
    bool onConnectedSent = false;
    bool socketCloseNotified = false;
    bool proxyCloseDiagnosticSuppressed = false;
    int64_t lastEventTime = 0;
    EventObject *eventObject;
    int32_t currentNetworkType;
    bool isIpv6;
    std::string currentAddress;
    uint16_t currentPort;
    std::string currentSocksUsername;
    std::string currentSocksPassword;

    std::string waitingForHostResolve;
    bool adjustWriteOpAfterResolve;

    std::string currentSecret;
    std::string currentSecretDomain;
    const char *currentSecretKind = "none";
    bool currentSecretIsFakeTls = false;
    int32_t currentProxyTlsProfile = 0;
    int32_t currentEffectiveProxyTlsProfile = 0;
    int32_t currentClientHelloFragmentation = 0;
    int32_t currentConnectionPatternMode = 0;
    int32_t currentRecordSizingMode = 0;
    int32_t currentTimingMode = 0;
    int32_t currentStartupCoverMode = 0;
    int64_t startupCoverStartTime = 0;
    uint32_t startupCoverFrameCount = 0;
    bool startupCoverStartedLogged = false;
    bool startupCoverEndedLogged = false;
    std::string currentProxyTlsProfileKey;
    std::string currentMtProxyEndpointKey;
    std::string currentMtProxyNetworkEndpointKey;
    std::string currentMtProxyDnsCacheKey;
    std::string proxyCheckDiagnostic = "tcp_not_connected";

    bool tlsHashMismatch = false;
    bool serverHelloHmacMismatchObserved = false;
    int64_t serverHelloHmacMismatchTime = 0;
    bool tlsBufferSized = true;
    uint8_t tlsBufferRecordType = 0;
    NativeByteBuffer *tlsBuffer = nullptr;
    ByteArray *tempBuffer = nullptr;
    ByteArray *pendingClientHello = nullptr;
    ByteArray *pendingTlsFrame = nullptr;
    size_t bytesRead = 0;
    uint32_t pendingClientHelloSize = 0;
    uint32_t pendingClientHelloOffset = 0;
    uint32_t pendingClientHelloFragmentTarget = 0;
    uint32_t pendingClientHelloFragmentIndex = 0;
    uint32_t pendingClientHelloFragmentCount = 0;
    int64_t pendingClientHelloNextWriteTime = 0;
    uint32_t pendingTlsFrameSize = 0;
    uint32_t pendingTlsFrameOffset = 0;
    uint32_t pendingTlsPayloadSize = 0;
    int64_t nextTlsFrameWriteTime = 0;
    int8_t tlsState = 0;
    bool mtproxyFirstTlsFrameSentLogged = false;
    bool mtproxyFirstTlsDataReceivedLogged = false;
    bool mtproxyFirstPlainDataSentLogged = false;
    bool mtproxyFirstPlainDataReceivedLogged = false;
    int64_t mtproxyFirstPlainDataSentTime = 0;
    int64_t mtproxyFirstDataReceivedTime = 0;
    uint32_t mtproxyTlsFrameCompletedCount = 0;
    bool currentTransportWss = false;
    int32_t currentDatacenterId = 0;
    bool currentMediaConnection = false;
    WssRouteConfig currentWssRoute;
    std::unique_ptr<WssTransport> currentWssTransport;

    uint8_t proxyAuthState = 0;
    Timer *proxyHandshakeAdmissionTimer = nullptr;
    bool proxyHandshakeAdmissionQueued = false;
    bool proxyHandshakeAdmissionActive = false;
    bool proxyHandshakeAdmissionReady = false;
    bool proxyEndpointBackoffReady = false;
    bool proxyEndpointTcpConnectActive = false;
    bool proxyEndpointTcpConnectReady = false;
    bool proxyEndpointDnsCoalesceReady = false;
    bool proxyHandshakeAdmissionIpv6 = false;
    bool mtproxySocketConnectedLogged = false;
    uint32_t proxyHandshakeAdmissionGeneration = 0;
    uint32_t proxyHandshakeAdmissionTimerGeneration = 0;
    int32_t proxyHandshakeAdmissionPriority = 0;
    int32_t proxyHandshakeAdmissionTimerMode = 0;
    int64_t proxyHandshakeAdmissionStartTime = 0;
    int64_t proxyHandshakeClientHelloSentTime = 0;
    std::string proxyHandshakeAdmissionKey;

    int32_t checkSocketError(int32_t *error);
    void closeSocket(int32_t reason, int32_t error);
    void openConnectionInternal(bool ipv6);
    void adjustWriteOp();
    bool isCurrentTransportWss();
    bool dispatchWssPayloads(std::vector<std::vector<uint8_t>> &payloads);
    bool scheduleProxyHandshakeAdmissionIfNeeded(bool ipv6, int32_t timerMode);
    void scheduleProxyHandshakeAdmissionTimer(uint32_t delay, int32_t mode, bool ipv6);
    void grantProxyHandshakeAdmission(bool ipv6, uint32_t generation, uint32_t delay, int32_t timerMode, const char *reason);
    void requestPendingHostResolve();
    void cancelProxyHandshakeAdmission();
    void releaseProxyHandshakeAdmission(bool succeeded, const char *reason);
    std::string mtProxyEndpointStateKeyForPhase(const std::string &phase);
    void resetMtProxyEndpointStateForKey(const std::string &key, int64_t now, bool resetRecipe);
    bool scheduleMtProxyEndpointCircuitBreakerIfNeeded(bool ipv6);
    bool scheduleMtProxyEndpointTcpConnectGateIfNeeded(bool ipv6);
    void releaseMtProxyEndpointTcpConnect(const char *reason);
    bool scheduleMtProxyDnsCoalesceIfNeeded(bool ipv6);
    void recordMtProxyEndpointFailure(const char *diagnostic, const char *reason);
    void recordMtProxyEndpointSuccess(const char *reason);
    bool mtProxyEndpointUseCachedHostAddress(const std::string &host, bool *ipv6);
    void mtProxyEndpointStoreResolvedAddress(const std::string &host, const std::string &ip);
    void applyMtProxyPhaseAdaptiveRecipe();
    void rotateMtProxyTlsProfileOnFailureIfNeeded(int32_t reason, int32_t error);
    void markProxyHandshakeClientHelloSent();
    void markProxyHandshakeFreezeIfNeeded();
    void markProxyServerHelloHmacTimeoutIfNeeded();
    void clearPendingClientHello();
    bool buildPendingClientHello(uint32_t size);
    bool sendPendingClientHelloFragment(uint32_t limit);
    bool sendPendingClientHello();
    void clearPendingTlsFrame();
    bool buildPendingTlsFrame(NativeByteBuffer *buffer, uint32_t remaining);
    bool sendPendingTlsFrame();
    uint32_t nextMtProxyTlsRecordPayloadSize(uint32_t remaining);
    bool scheduleMtProxyDataTimingIfNeeded();
    void startMtProxyStartupCover();
    bool mtProxyStartupCoverActive();
    int32_t effectiveMtProxyRecordSizingMode();
    int32_t effectiveMtProxyTimingMode();

    friend class EventObject;
    friend class ConnectionsManager;
    friend class Connection;
};

#endif
