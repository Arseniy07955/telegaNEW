// Host-build stub for Tools/build_mtproxy_host.py (no BoringSSL on host).
// Signatures mirror BoringSSL. Compile-only: never linked or run.
#ifndef MTPROXY_HOST_STUB_OPENSSL_HMAC_H
#define MTPROXY_HOST_STUB_OPENSSL_HMAC_H

#include <stddef.h>
#include <stdint.h>

typedef struct env_md_st EVP_MD;

inline const EVP_MD *EVP_sha256(void) {
    return nullptr;
}

inline uint8_t *HMAC(const EVP_MD *evp_md, const void *key, size_t key_len,
                     const uint8_t *data, size_t data_len, uint8_t *out,
                     unsigned int *out_len) {
    (void) evp_md;
    (void) key;
    (void) key_len;
    (void) data;
    (void) data_len;
    if (out_len != nullptr) {
        *out_len = 32;
    }
    return out;
}

#endif
