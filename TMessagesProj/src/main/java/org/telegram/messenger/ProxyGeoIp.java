package org.telegram.messenger;

import android.content.Context;
import android.content.SharedPreferences;
import android.text.TextUtils;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.RandomAccessFile;
import java.net.InetAddress;
import java.nio.ByteOrder;
import java.nio.MappedByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;

/**
 * Offline IP -> (country, network owner) lookup over a bundled, memory-mapped GeoIP/ASN table.
 *
 * The table is produced by {@code Tools/build_geoip_asn.py} from the free iptoasn.com dataset and
 * shipped as {@code assets/geoip_asn.dat}. If the asset is missing every lookup returns {@code null}
 * and the UI simply shows nothing extra — the feature is fully optional at build time.
 *
 * File format (big-endian):
 * <pre>
 *   header (16 bytes):
 *     0  : magic  "ZGIP"            (4 bytes)
 *     4  : version (1)              (1 byte)
 *     5  : reserved                 (3 bytes)
 *     8  : recordCount              (uint32)
 *     12 : stringTableOffset        (uint32, absolute)
 *   records[recordCount], sorted ascending by ipStart, 14 bytes each:
 *     +0 : ipStart                  (uint32)
 *     +4 : ipEnd                    (uint32)
 *     +8 : country (2 ASCII, 0x0000 = unknown)
 *     +10: ownerOffset              (uint32, relative to stringTableOffset, 0xFFFFFFFF = none)
 *   string table (at stringTableOffset): entries of [uint16 length][UTF-8 bytes].
 * </pre>
 */
public final class ProxyGeoIp {

    public static final class Result {
        public final String country; // ISO-3166 alpha-2, or null
        public final String owner;   // ASN org / network owner, or null

        Result(String country, String owner) {
            this.country = country;
            this.owner = owner;
        }
    }

    public interface Callback {
        void onResult(Result result);
    }

    private static final String ASSET_NAME = "geoip_asn.dat";
    private static final int ASSET_VERSION = 1; // bump when the bundled table is regenerated
    private static final int HEADER_SIZE = 16;
    private static final int RECORD_SIZE = 14;
    private static final long NO_OWNER = 0xFFFFFFFFL;

    private static final DispatchQueue queue = new DispatchQueue("proxyGeoIp");

    private static volatile ProxyGeoIp instance;
    private static volatile boolean loadFailed;

    private final MappedByteBuffer buffer;
    private final int recordCount;
    private final int stringTableOffset;

    private ProxyGeoIp(MappedByteBuffer buffer, int recordCount, int stringTableOffset) {
        this.buffer = buffer;
        this.recordCount = recordCount;
        this.stringTableOffset = stringTableOffset;
    }

    /**
     * Resolves {@code host} (an IPv4 literal or a hostname) to its country/owner off the UI thread.
     * The callback is always delivered on the UI thread; the result may be {@code null}.
     */
    public static void resolveAsync(final String host, final Callback callback) {
        if (TextUtils.isEmpty(host)) {
            if (callback != null) {
                AndroidUtilities.runOnUIThread(() -> callback.onResult(null));
            }
            return;
        }
        queue.postRunnable(() -> {
            Result result = null;
            try {
                ProxyGeoIp geo = getInstance();
                if (geo != null) {
                    result = geo.lookupHostBlocking(host);
                }
            } catch (Throwable t) {
                FileLog.e(t);
            }
            final Result delivered = result;
            AndroidUtilities.runOnUIThread(() -> {
                if (callback != null) {
                    callback.onResult(delivered);
                }
            });
        });
    }

    // Loaded lazily on the lookup queue, never on the UI thread (the first call copies + maps ~MBs).
    private static ProxyGeoIp getInstance() {
        if (instance == null && !loadFailed) {
            synchronized (ProxyGeoIp.class) {
                if (instance == null && !loadFailed) {
                    ProxyGeoIp loaded = load();
                    if (loaded == null) {
                        loadFailed = true;
                    } else {
                        instance = loaded;
                    }
                }
            }
        }
        return instance;
    }

    private static ProxyGeoIp load() {
        try {
            File file = ensureAssetCopied();
            if (file == null || !file.exists()) {
                return null;
            }
            RandomAccessFile raf = new RandomAccessFile(file, "r");
            FileChannel channel = raf.getChannel();
            long size = channel.size();
            if (size < HEADER_SIZE) {
                channel.close();
                raf.close();
                return null;
            }
            MappedByteBuffer buf = channel.map(FileChannel.MapMode.READ_ONLY, 0, size);
            buf.order(ByteOrder.BIG_ENDIAN);
            // The mapping stays valid after the channel/file are closed.
            channel.close();
            raf.close();
            if (buf.get(0) != 'Z' || buf.get(1) != 'G' || buf.get(2) != 'I' || buf.get(3) != 'P') {
                return null;
            }
            if ((buf.get(4) & 0xFF) != 1) {
                return null;
            }
            int recordCount = buf.getInt(8);
            int stringTableOffset = buf.getInt(12);
            if (recordCount <= 0 || stringTableOffset < HEADER_SIZE || stringTableOffset > size) {
                return null;
            }
            if ((long) HEADER_SIZE + (long) recordCount * RECORD_SIZE > size) {
                return null;
            }
            return new ProxyGeoIp(buf, recordCount, stringTableOffset);
        } catch (Throwable t) {
            FileLog.e(t);
            return null;
        }
    }

    private static File ensureAssetCopied() throws Exception {
        Context ctx = ApplicationLoader.applicationContext;
        if (ctx == null) {
            return null;
        }
        File out = new File(ctx.getFilesDir(), ASSET_NAME);
        SharedPreferences prefs = MessagesController.getGlobalMainSettings();
        if (out.exists() && prefs.getInt("geoip_asset_version", -1) == ASSET_VERSION) {
            return out;
        }
        InputStream in;
        try {
            in = ctx.getAssets().open(ASSET_NAME); // throws if the asset was not bundled
        } catch (Exception e) {
            return null; // no table shipped -> feature stays off
        }
        try (FileOutputStream fos = new FileOutputStream(out)) {
            byte[] chunk = new byte[64 * 1024];
            int n;
            while ((n = in.read(chunk)) > 0) {
                fos.write(chunk, 0, n);
            }
        } finally {
            in.close();
        }
        prefs.edit().putInt("geoip_asset_version", ASSET_VERSION).apply();
        return out;
    }

    private Result lookupHostBlocking(String host) {
        if (host != null) {
            host = host.trim();
        }
        if (TextUtils.isEmpty(host)) {
            return null;
        }
        long ip = parseIpv4(host);
        if (ip < 0) {
            try {
                byte[] addr = InetAddress.getByName(host).getAddress();
                if (addr.length != 4) {
                    return null; // IPv6 is not covered by the v4 table
                }
                ip = ((addr[0] & 0xFFL) << 24) | ((addr[1] & 0xFFL) << 16) | ((addr[2] & 0xFFL) << 8) | (addr[3] & 0xFFL);
            } catch (Exception e) {
                return null;
            }
        }
        return lookupIp(ip);
    }

    private Result lookupIp(long ip) {
        int lo = 0;
        int hi = recordCount - 1;
        while (lo <= hi) {
            int mid = (lo + hi) >>> 1;
            int base = HEADER_SIZE + mid * RECORD_SIZE;
            long start = buffer.getInt(base) & 0xFFFFFFFFL;
            long end = buffer.getInt(base + 4) & 0xFFFFFFFFL;
            if (ip < start) {
                hi = mid - 1;
            } else if (ip > end) {
                lo = mid + 1;
            } else {
                int c0 = buffer.get(base + 8) & 0xFF;
                int c1 = buffer.get(base + 9) & 0xFF;
                String country = (c0 != 0 && c1 != 0) ? ("" + (char) c0 + (char) c1) : null;
                long ownerOffset = buffer.getInt(base + 10) & 0xFFFFFFFFL;
                String owner = null;
                if (ownerOffset != NO_OWNER) {
                    // Validate before reading: a corrupt/truncated asset must never yield a wrong
                    // in-bounds string (a negative (int) cast could otherwise wrap back into range).
                    long absOffset = (long) stringTableOffset + ownerOffset;
                    if (absOffset >= stringTableOffset && absOffset + 2 <= buffer.limit()) {
                        owner = readString((int) absOffset);
                    }
                }
                if (country == null && TextUtils.isEmpty(owner)) {
                    return null;
                }
                return new Result(country, owner);
            }
        }
        return null;
    }

    private String readString(int offset) {
        if (offset < 0 || offset + 2 > buffer.limit()) {
            return null;
        }
        int length = buffer.getShort(offset) & 0xFFFF;
        if (length <= 0 || (long) offset + 2 + length > buffer.limit()) {
            return null;
        }
        byte[] bytes = new byte[length];
        int p = offset + 2;
        for (int i = 0; i < length; i++) {
            bytes[i] = buffer.get(p + i);
        }
        return new String(bytes, StandardCharsets.UTF_8);
    }

    // Parses "a.b.c.d" into an unsigned 32-bit value, or -1 if it is not an IPv4 literal.
    private static long parseIpv4(String host) {
        if (host == null) {
            return -1;
        }
        long result = 0;
        int part = 0;
        int parts = 0;
        boolean hasDigit = false;
        for (int i = 0; i < host.length(); i++) {
            char c = host.charAt(i);
            if (c >= '0' && c <= '9') {
                part = part * 10 + (c - '0');
                if (part > 255) {
                    return -1;
                }
                hasDigit = true;
            } else if (c == '.') {
                if (!hasDigit) {
                    return -1;
                }
                result = (result << 8) | part;
                part = 0;
                hasDigit = false;
                parts++;
                if (parts > 3) {
                    return -1;
                }
            } else {
                return -1;
            }
        }
        if (parts != 3 || !hasDigit) {
            return -1;
        }
        result = (result << 8) | part;
        return result & 0xFFFFFFFFL;
    }
}
