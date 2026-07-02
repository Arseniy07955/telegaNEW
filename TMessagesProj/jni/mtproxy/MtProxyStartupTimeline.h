/*
 * This is the source code of tgnet library v. 1.1
 * It is licensed under GNU GPL v. 2 or later.
 */

#ifndef MTPROXYSTARTUPTIMELINE_H
#define MTPROXYSTARTUPTIMELINE_H

#include <stdint.h>
#include <time.h>

enum class MtProxyStartupPhase : uint8_t {
    None,
    AdmissionQueue,
    EndpointCooldown,
    // MtProxyStartupPhase::ProbeWait keeps joiners pre-TCP while the owner probes.
    ProbeWait,
    DnsCoalesceWait,
    TcpConnectGate,
    HostResolve,
    TcpConnect,
};

enum class MtProxyStartupTimerKind : uint8_t {
    None,
    Admission,
    HostResolveAdmission,
    EndpointBackoff,
    ProbeWait,
    DnsCoalesce,
    TcpConnectGate,
};

struct MtProxyStartupTimeoutDecision {
    bool active = false;
    bool expired = false;
    MtProxyStartupPhase phase = MtProxyStartupPhase::None;
    const char *diagnostic = nullptr;
    const char *event = nullptr;
    int64_t startMs = 0;
    int64_t deadlineMs = 0;
    int64_t elapsedMs = 0;
};

struct MtProxyStartupTimerDecision {
    bool canRun = false;
    const char *ignoreReason = nullptr;
};

class MtProxyStartupTimeline {
public:
    void reset();

    void beginLocalWait(MtProxyStartupPhase phase, int64_t deadlineMs);
    void finishLocalWait();

    void beginDnsResolve(int64_t nowMs, time_t timeoutSeconds);
    void finishDnsResolve();

    void beginTcpConnect(int64_t nowMs, time_t timeoutSeconds);
    void finishTcpConnect();

    MtProxyStartupTimeoutDecision timeoutDecision(int64_t nowMs, bool socketConnectedLogged) const;
    const char *terminalDiagnostic(bool socketConnectedLogged) const;
    MtProxyStartupTimerDecision canRunPreTcpTimer(MtProxyStartupTimerKind expectedKind,
                                                  uint32_t timerGeneration,
                                                  uint32_t currentGeneration,
                                                  MtProxyStartupTimerKind currentKind,
                                                  bool socketAlive,
                                                  bool waitingGate,
                                                  bool epollRegistered) const;

    MtProxyStartupPhase phase() const;
    const char *phaseName() const;
    static const char *phaseName(MtProxyStartupPhase phase);
    static const char *timerKindName(MtProxyStartupTimerKind kind);

    bool hasLocalWait() const;
    bool dnsResolveAttemptStarted() const;
    bool tcpConnectAttemptStarted() const;

    int64_t localWaitDeadlineMs() const;
    int64_t dnsResolveStartTimeMs() const;
    int64_t dnsResolveDeadlineMs() const;
    int64_t tcpConnectStartTimeMs() const;
    int64_t tcpConnectDeadlineMs() const;

private:
    static bool isLocalWaitPhase(MtProxyStartupPhase phase);
    static int64_t deadlineFromTimeout(int64_t nowMs, time_t timeoutSeconds);
    static const char *timeoutDiagnosticForPhase(MtProxyStartupPhase phase);
    static const char *timeoutEventForPhase(MtProxyStartupPhase phase);

    MtProxyStartupPhase phase_ = MtProxyStartupPhase::None;
    int64_t localWaitDeadlineMs_ = 0;
    bool dnsResolveAttemptStarted_ = false;
    int64_t dnsResolveStartTimeMs_ = 0;
    int64_t dnsResolveDeadlineMs_ = 0;
    bool tcpConnectAttemptStarted_ = false;
    int64_t tcpConnectStartTimeMs_ = 0;
    int64_t tcpConnectDeadlineMs_ = 0;
};

#endif
