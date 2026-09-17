/* pingpong.c - cross-cluster exclusive ping-pong for the BF2 counter
 * probe (E2-L3).  Two threads pinned to cores of different clusters
 * hammer one cache line with __atomic_fetch_add (ldaxr/stlxr loop):
 * every op steals the line through the POC, so POC_READS (0x53) sees
 * exclusive read requests if the counter is alive.
 *
 * Replaces stress-ng --futex (not built into the offline stress-ng).
 * Usage: pingpong [seconds] [cpu0] [cpu1]   (default 30 0 4)
 */
#define _GNU_SOURCE
#include <pthread.h>
#include <sched.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static uint64_t word __attribute__((aligned(64)));
static int seconds = 30;
static int cpus[2] = {0, 4};

static void pin(int cpu)
{
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    pthread_setaffinity_np(pthread_self(), sizeof(set), &set);
}

static void *spin(void *arg)
{
    int cpu = *(int *)arg;
    pin(cpu);
    time_t end = time(NULL) + seconds;
    while (time(NULL) < end)
        __atomic_fetch_add(&word, 1, __ATOMIC_SEQ_CST);
    return NULL;
}

int main(int argc, char **argv)
{
    pthread_t t[2];
    if (argc > 1)
        seconds = atoi(argv[1]);
    if (argc > 3) {
        cpus[0] = atoi(argv[2]);
        cpus[1] = atoi(argv[3]);
    }
    pthread_create(&t[0], NULL, spin, &cpus[0]);
    pthread_create(&t[1], NULL, spin, &cpus[1]);
    pthread_join(t[0], NULL);
    pthread_join(t[1], NULL);
    printf("pingpong done: %llu ops in %d s\n",
           (unsigned long long)word, seconds);
    return 0;
}
