// Host-build stub for Tools/build_mtproxy_host.py (MSVC has no arpa/inet.h).
// Compile-only: the harness never links or runs this code.
#ifndef MTPROXY_HOST_STUB_ARPA_INET_H
#define MTPROXY_HOST_STUB_ARPA_INET_H

#ifndef AF_INET
#define AF_INET 2
#endif
#ifndef AF_INET6
#define AF_INET6 23
#endif

inline int inet_pton(int af, const char *src, void *dst) {
    (void) af;
    (void) src;
    (void) dst;
    return 0;
}

#endif
