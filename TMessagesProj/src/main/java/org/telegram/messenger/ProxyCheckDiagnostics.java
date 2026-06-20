package org.telegram.messenger;

import android.text.TextUtils;

import org.telegram.tgnet.ConnectionsManager;
import org.telegram.ui.ActionBar.Theme;

public class ProxyCheckDiagnostics {

    public static final String OK = "ok";
    public static final String CHECKING = "checking";
    public static final String WAITING_TCP = "waiting_tcp";
    public static final String START_FAILED = "start_failed";
    public static final String TCP_NOT_CONNECTED = "tcp_not_connected";
    public static final String TCP_CONNECTED_NO_PONG = "tcp_connected_no_pong";
    public static final String NETWORK_BLOCK_SUSPECTED = "network_block_suspected";
    public static final String CLIENT_HELLO_SENT_NO_SERVER_HELLO = "client_hello_sent_no_server_hello";
    public static final String SERVER_HELLO_HMAC_MISMATCH = "server_hello_hmac_mismatch";
    public static final String POST_HANDSHAKE_NO_APPDATA = "post_handshake_no_appdata";
    public static final String DROPPED_AFTER_APPDATA = "dropped_after_appdata";
    public static final String CANCELLED = "cancelled";
    public static final String UNKNOWN_FAIL = "unknown_fail";

    public static String normalize(String diagnostic) {
        if (TextUtils.isEmpty(diagnostic)) {
            return UNKNOWN_FAIL;
        }
        switch (diagnostic) {
            case OK:
            case CHECKING:
            case WAITING_TCP:
            case START_FAILED:
            case TCP_NOT_CONNECTED:
            case TCP_CONNECTED_NO_PONG:
            case NETWORK_BLOCK_SUSPECTED:
            case CLIENT_HELLO_SENT_NO_SERVER_HELLO:
            case SERVER_HELLO_HMAC_MISMATCH:
            case POST_HANDSHAKE_NO_APPDATA:
            case DROPPED_AFTER_APPDATA:
            case CANCELLED:
            case UNKNOWN_FAIL:
                return diagnostic;
            default:
                return UNKNOWN_FAIL;
        }
    }

    public static boolean isFailure(String diagnostic) {
        String normalized = normalize(diagnostic);
        return !OK.equals(normalized) && !CHECKING.equals(normalized) && !CANCELLED.equals(normalized);
    }

    public static boolean hasFreshFailure(SharedConfig.ProxyInfo proxyInfo) {
        return proxyInfo != null && ProxyCheckScheduler.isFresh(proxyInfo) && isFailure(proxyInfo.lastCheckDiagnostic);
    }

    public static String statusText(SharedConfig.ProxyInfo proxyInfo, boolean currentProxyEnabled, int currentConnectionState) {
        if (proxyInfo == null) {
            return LocaleController.getString(R.string.ProxyStatusUnknownFail);
        }
        if (currentProxyEnabled) {
            if (currentConnectionState == ConnectionsManager.ConnectionStateConnected || currentConnectionState == ConnectionsManager.ConnectionStateUpdating) {
                if (proxyInfo.ping != 0) {
                    return LocaleController.getString(R.string.Connected) + ", " + LocaleController.formatString("Ping", R.string.Ping, proxyInfo.ping);
                }
                return LocaleController.getString(R.string.Connected);
            }
            if (hasFreshFailure(proxyInfo)) {
                return shortDiagnosticText(proxyInfo.lastCheckDiagnostic);
            }
            if (proxyInfo.checking) {
                return LocaleController.getString(R.string.ProxyStatusCheckingConnection);
            }
            if (currentConnectionState == ConnectionsManager.ConnectionStateConnectingToProxy) {
                return LocaleController.getString(R.string.ProxyStatusWaitingTcp);
            }
            return LocaleController.getString(R.string.ProxyStatusConnectingSlow);
        }
        if (proxyInfo.checking) {
            return LocaleController.getString(R.string.ProxyStatusCheckingConnection);
        }
        if (proxyInfo.available && ProxyCheckScheduler.isFresh(proxyInfo)) {
            if (proxyInfo.ping != 0) {
                return LocaleController.getString(R.string.Available) + ", " + LocaleController.formatString("Ping", R.string.Ping, proxyInfo.ping);
            }
            return LocaleController.getString(R.string.Available);
        }
        if (hasFreshFailure(proxyInfo)) {
            return shortDiagnosticText(proxyInfo.lastCheckDiagnostic);
        }
        return LocaleController.getString(R.string.ProxyStatusUnchecked);
    }

    public static String headerStatusText(SharedConfig.ProxyInfo proxyInfo, boolean proxyEnabled, int currentConnectionState) {
        if (!proxyEnabled) {
            return LocaleController.getString(R.string.ProxyWindowStatusDisabled);
        }
        if (proxyInfo == null) {
            return LocaleController.getString(R.string.ProxyWindowStatusNoProxy);
        }
        if (currentConnectionState == ConnectionsManager.ConnectionStateConnected || currentConnectionState == ConnectionsManager.ConnectionStateUpdating) {
            if (proxyInfo.ping != 0) {
                return LocaleController.getString(R.string.ProxyWindowStatusReady) + ", " + LocaleController.formatString("Ping", R.string.Ping, proxyInfo.ping);
            }
            return LocaleController.getString(R.string.ProxyWindowStatusReady);
        }
        if (proxyInfo.checking) {
            return LocaleController.getString(R.string.ProxyWindowStatusChecking);
        }
        if (hasFreshFailure(proxyInfo)) {
            return shortDiagnosticText(proxyInfo.lastCheckDiagnostic);
        }
        if (currentConnectionState == ConnectionsManager.ConnectionStateConnectingToProxy) {
            return LocaleController.getString(R.string.ProxyStatusWaitingTcp);
        }
        return LocaleController.getString(R.string.ProxyStatusConnectingSlow);
    }

    public static String shortDiagnosticText(String diagnostic) {
        return diagnosticText(diagnostic);
    }

    public static int statusColorKey(SharedConfig.ProxyInfo proxyInfo, boolean currentProxyEnabled, int currentConnectionState) {
        if (currentProxyEnabled) {
            if (currentConnectionState == ConnectionsManager.ConnectionStateConnected || currentConnectionState == ConnectionsManager.ConnectionStateUpdating) {
                return Theme.key_windowBackgroundWhiteBlueText6;
            }
            return hasFreshFailure(proxyInfo) ? Theme.key_text_RedRegular : Theme.key_windowBackgroundWhiteGrayText2;
        }
        if (proxyInfo == null) {
            return Theme.key_text_RedRegular;
        }
        if (proxyInfo.checking) {
            return Theme.key_windowBackgroundWhiteGrayText2;
        }
        if (proxyInfo.available && ProxyCheckScheduler.isFresh(proxyInfo)) {
            return Theme.key_windowBackgroundWhiteGreenText;
        }
        return hasFreshFailure(proxyInfo) ? Theme.key_text_RedRegular : Theme.key_windowBackgroundWhiteGrayText2;
    }

    public static String diagnosticText(String diagnostic) {
        switch (normalize(diagnostic)) {
            case OK:
                return LocaleController.getString(R.string.Available);
            case CHECKING:
                return LocaleController.getString(R.string.ProxyStatusCheckingConnection);
            case WAITING_TCP:
                return LocaleController.getString(R.string.ProxyStatusWaitingTcp);
            case START_FAILED:
                return LocaleController.getString(R.string.ProxyStatusStartFailed);
            case TCP_NOT_CONNECTED:
                return LocaleController.getString(R.string.ProxyStatusTcpNotConnected);
            case TCP_CONNECTED_NO_PONG:
                return LocaleController.getString(R.string.ProxyStatusTcpConnectedNoPong);
            case NETWORK_BLOCK_SUSPECTED:
                return LocaleController.getString(R.string.ProxyStatusNetworkBlockSuspected);
            case CLIENT_HELLO_SENT_NO_SERVER_HELLO:
                return LocaleController.getString(R.string.ProxyStatusClientHelloNoServerHello);
            case SERVER_HELLO_HMAC_MISMATCH:
                return LocaleController.getString(R.string.ProxyStatusServerHelloHmacMismatch);
            case POST_HANDSHAKE_NO_APPDATA:
                return LocaleController.getString(R.string.ProxyStatusPostHandshakeNoAppData);
            case DROPPED_AFTER_APPDATA:
                return LocaleController.getString(R.string.ProxyStatusDroppedAfterAppData);
            case CANCELLED:
                return LocaleController.getString(R.string.ProxyStatusCancelled);
            case UNKNOWN_FAIL:
            default:
                return LocaleController.getString(R.string.ProxyStatusUnknownFail);
        }
    }
}
