// Host-build stub for Tools/build_mtproxy_host.py (no BoringSSL on host).
// Signatures mirror BoringSSL.
//
// RAND_bytes fills the buffer from a deterministic xorshift32 stream — NOT
// random, but reproducible across test runs. It must not leave the buffer
// zeroed: the rejection-sampling helpers (endpointSecureRandomBounded /
// mtProxySecureRandomBounded) spin forever on an all-zero source when the
// bound is not a power of two. Tests therefore assert jitter ENVELOPES
// (base <= delay <= base + limit), never exact jittered values.
#ifndef MTPROXY_HOST_STUB_OPENSSL_RAND_H
#define MTPROXY_HOST_STUB_OPENSSL_RAND_H

#include <stddef.h>
#include <stdint.h>

inline int RAND_bytes(uint8_t *buf, size_t len) {
    static uint32_t state = 0x12345678u;
    for (size_t i = 0; i < len; i++) {
        state ^= state << 13;
        state ^= state >> 17;
        state ^= state << 5;
        buf[i] = (uint8_t) state;
    }
    return 1;
}

#endif
