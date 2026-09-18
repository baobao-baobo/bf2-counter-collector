/*
 * memrand - random-access memory microbenchmark (pointer chase)
 *
 * Replaces sysbench --memory-access-mode=rnd for the BF2 bench campaign:
 * sysbench needs autotools + bundled LuaJIT to build, which the offline
 * cross toolchain cannot provide.  This single-file program gives the
 * same workload class with *more* experimental control (working set
 * size and stride are explicit), which matters for cache-locality
 * experiments:
 *
 *   working set >> LLC  ->  each access misses to DDR (random access)
 *   working set <  LLC  ->  mostly L2/L3 hits (locality retained)
 *
 * Model: classic pointer chase (lat_mem_rd / HPCC RandomAccess style).
 * The buffer is split into stride-sized blocks; each block's first
 * 8 bytes hold the address of the next block in a random permutation,
 * forming one closed loop.  The walk then jumps all over the buffer,
 * defeating both the HW prefetcher and the linearity of STREAM.
 *
 * Usage:  memrand [-s SIZE_MB] [-b BLOCK_BYTES] [-d SECONDS] [-w]
 *   -s  working set in MB           (default 512)
 *   -b  stride / block size         (default 64, cache line)
 *   -d  run duration in seconds     (default 30)
 *   -w  write mode: store into each block instead of reading it
 *       (default: read-only pointer chase)
 *
 * Output: one summary line to stdout, e.g.
 *   memrand: size=512MB stride=64B mode=read
 *   memrand: 1872345 accesses in 30.0s = 0.41 GB/s = 16.0 ns/access
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdint.h>

/* xorshift64* PRNG: deterministic, no libc rand() state issues. */
static uint64_t rng_state = 0x9e3779b97f4a7c15ULL;
static uint64_t rng_next(void)
{
    uint64_t x = rng_state;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    rng_state = x;
    return x * 0x2545f4914f6cdd1dULL;
}

static double now_sec(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

int main(int argc, char **argv)
{
    long size_mb = 512;
    long stride = 64;
    long seconds = 30;
    int write_mode = 0;
    int i;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-s") == 0 && i + 1 < argc)
            size_mb = atol(argv[++i]);
        else if (strcmp(argv[i], "-b") == 0 && i + 1 < argc)
            stride = atol(argv[++i]);
        else if (strcmp(argv[i], "-d") == 0 && i + 1 < argc)
            seconds = atol(argv[++i]);
        else if (strcmp(argv[i], "-w") == 0)
            write_mode = 1;
        else {
            fprintf(stderr, "usage: %s [-s SIZE_MB] [-b BLOCK_BYTES] [-d SECONDS] [-w]\n",
                    argv[0]);
            return 1;
        }
    }
    if (size_mb < 1 || stride < 8 || seconds < 1) {
        fprintf(stderr, "bad arguments (size>=1MB, stride>=8, duration>=1s)\n");
        return 1;
    }
    if (write_mode && stride < 16) {
        fprintf(stderr, "write mode needs stride >= 16 (pointer at "
                        "offset 0, stamp at offset 8)\n");
        return 1;
    }

    size_t total = (size_t)size_mb * 1024 * 1024;
    size_t nblocks = total / (size_t)stride;
    if (nblocks < 2) {
        fprintf(stderr, "working set too small for stride (need >= 2 blocks)\n");
        return 1;
    }

    unsigned char *buf = malloc(total);
    if (buf == NULL) {
        fprintf(stderr, "malloc(%zu) failed\n", total);
        return 1;
    }

    /* Build a random permutation of block indices (Fisher-Yates). */
    uint32_t *perm = malloc(nblocks * sizeof(uint32_t));
    if (perm == NULL) {
        fprintf(stderr, "malloc perm failed\n");
        return 1;
    }
    for (i = 0; i < (int)nblocks; i++)
        perm[i] = (uint32_t)i;
    for (i = (int)nblocks - 1; i > 0; i--) {
        int j = (int)(rng_next() % (uint64_t)(i + 1));
        uint32_t t = perm[i];
        perm[i] = perm[j];
        perm[j] = t;
    }

    /* Link blocks: block k's first 8 bytes -> address of block k+1. */
    for (i = 0; i < (int)nblocks; i++) {
        uint32_t next = perm[(i + 1) % (int)nblocks];
        uintptr_t addr = (uintptr_t)(buf + (size_t)next * (size_t)stride);
        memcpy(buf + (size_t)perm[i] * (size_t)stride, &addr, sizeof(addr));
    }
    free(perm);

    /* Warm-up walk: bring the loop fully into caches / TLB if it fits. */
    unsigned char *p = buf + (size_t)(rng_next() % nblocks) * (size_t)stride;
    int warm = (int)nblocks / 8;
    if (warm < 16) warm = (int)nblocks;
    for (i = 0; i < warm; i++) {
        uintptr_t next;
        memcpy(&next, p, sizeof(next));
        p = (unsigned char *)next;
    }

    volatile uint64_t sink = 0;      /* defeats dead-code elimination */
    unsigned long long accesses = 0;
    double t0 = now_sec();

    if (write_mode) {
        for (;;) {
            uint64_t stamp = rng_next();
            unsigned char *q = p;
            uintptr_t next;
            memcpy(&next, q, sizeof(next));
            memcpy(q + 8, &stamp, sizeof(stamp)); /* store at offset 8:
                keeps the chase pointer (offset 0) intact - writing the
                pointer itself would corrupt the closed loop and crash
                on the next visit (segfault fixed 2026-09-17) */
            p = (unsigned char *)next;
            accesses++;
            if (now_sec() - t0 >= (double)seconds)
                break;
        }
    } else {
        for (;;) {
            uintptr_t next;
            memcpy(&next, p, sizeof(next));
            sink ^= next;
            p = (unsigned char *)next;
            accesses++;
            if (now_sec() - t0 >= (double)seconds)
                break;
        }
    }

    double dt = now_sec() - t0;
    double gbps = (double)accesses * (double)stride / dt / 1e9;
    double ns_per = dt / (double)accesses * 1e9;

    printf("memrand: size=%ldMB stride=%ldB mode=%s\n",
           size_mb, stride, write_mode ? "write" : "read");
    printf("memrand: %llu accesses in %.1fs = %.2f GB/s = %.1f ns/access\n",
           accesses, dt, gbps, ns_per);
    printf("memrand: sink=%llu\n", (unsigned long long)sink);

    free(buf);
    return 0;
}
