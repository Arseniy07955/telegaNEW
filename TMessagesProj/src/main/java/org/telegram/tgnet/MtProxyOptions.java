package org.telegram.tgnet;

public final class MtProxyOptions {
    public final int tlsProfile;
    public final int clientHelloFragmentation;
    public final int connectionPatternMode;
    public final int recordSizingMode;
    public final int timingMode;
    public final int startupCoverMode;

    public MtProxyOptions(int tlsProfile, int clientHelloFragmentation, int connectionPatternMode, int recordSizingMode, int timingMode, int startupCoverMode) {
        this.tlsProfile = tlsProfile;
        this.clientHelloFragmentation = clientHelloFragmentation;
        this.connectionPatternMode = connectionPatternMode;
        this.recordSizingMode = recordSizingMode;
        this.timingMode = timingMode;
        this.startupCoverMode = startupCoverMode;
    }

    public static MtProxyOptions resolve() {
        return resolve("", 1080, "");
    }

    public static MtProxyOptions resolve(String address, int port, String secret) {
        return new MtProxyOptions(
                ConnectionsManager.resolveMtProxyTlsProfile(address, port, secret),
                ConnectionsManager.resolveMtProxyClientHelloFragmentationMode(),
                ConnectionsManager.resolveMtProxyConnectionPatternMode(),
                ConnectionsManager.resolveMtProxyRecordSizingMode(),
                ConnectionsManager.resolveMtProxyTimingMode(),
                ConnectionsManager.resolveMtProxyStartupCoverMode());
    }

    public static MtProxyOptions disabled() {
        return new MtProxyOptions(
                ConnectionsManager.MT_PROXY_TLS_PROFILE_AUTO,
                ConnectionsManager.MT_PROXY_CLIENT_HELLO_FRAGMENTATION_OFF,
                ConnectionsManager.MT_PROXY_CONNECTION_PATTERN_OFF,
                ConnectionsManager.MT_PROXY_RECORD_SIZING_OFF,
                ConnectionsManager.MT_PROXY_TIMING_OFF,
                ConnectionsManager.MT_PROXY_STARTUP_COVER_OFF);
    }
}
