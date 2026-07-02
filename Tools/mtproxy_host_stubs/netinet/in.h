// Host-build stub for Tools/build_mtproxy_host.py (MSVC has no netinet/in.h).
// Compile-only: the harness never links or runs this code.
#ifndef MTPROXY_HOST_STUB_NETINET_IN_H
#define MTPROXY_HOST_STUB_NETINET_IN_H

#include <stdint.h>

struct in_addr {
    uint32_t s_addr;
};

struct in6_addr {
    uint8_t s6_addr[16];
};

#define IN6ADDR_ANY_INIT {{0}}

#ifndef AF_INET
#define AF_INET 2
#endif
#ifndef AF_INET6
#define AF_INET6 23
#endif

#endif
