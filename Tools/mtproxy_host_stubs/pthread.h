// Host-build stub for Tools/build_mtproxy_host.py (MSVC has no pthread).
// Compile-only: the harness never links or runs this code.
#ifndef MTPROXY_HOST_STUB_PTHREAD_H
#define MTPROXY_HOST_STUB_PTHREAD_H

typedef struct pthread_mutex_t {
    int unused;
} pthread_mutex_t;

#define PTHREAD_MUTEX_INITIALIZER {0}

inline int pthread_mutex_lock(pthread_mutex_t *mutex) {
    (void) mutex;
    return 0;
}

inline int pthread_mutex_unlock(pthread_mutex_t *mutex) {
    (void) mutex;
    return 0;
}

#endif
