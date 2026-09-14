/*
 * collect_all.c -- BlueField-2 config-driven resource collector
 *
 * ============================================================================
 * Purpose
 * ============================================================================
 * One process, one CSV row per tick, driven by an INI configuration that
 * selects WHICH counters to collect and the sampling FREQUENCY of each
 * block.  The built-in default configuration reproduces the device-verified
 * 42-column baseline; configs/default.conf ships the same content as an
 * explicit file.
 *
 * Blocks and their mechanisms:
 *   tile HNF   (mechanism 1, rotating): up to 8 groups x up to 4 events,
 *              groups alternate every tile interval (time-division
 *              multiplexing -- the hardware has only 4 counter slots per
 *              tile).  tile_group column records the active group; columns
 *              of inactive groups are empty (NaN in pandas).
 *   tilenet    (mechanism 1, persistent): up to 3 events, summed over tiles
 *   trio       (mechanism 1, persistent): up to 4 events, summed over blocks
 *   smmu       (mechanism 1, persistent): up to 3 events, summed over blocks
 *   triogen    (mechanism 1, persistent): fixed triogen0=TX_DAT_AF,
 *              triogen1=RX_DAT_AF
 *   l3cache    (mechanism 1, enable=1/0 grouped start/stop per window)
 *   pcie TLR   (mechanism 2, persistent): direct register reads, per-block
 *   gic        (mechanism 1, unverified): same SMGEN events as smmu
 *   ARM PMU    (L1, persistent): fixed 4 events, output filter only
 *   software   (persistent): /proc/stat CPU, /proc/meminfo, /sys/class/net
 *
 * ============================================================================
 * Timing model
 * ============================================================================
 * One tick = [global] interval seconds (default 1 s).  A block with
 * interval k * global is sampled at ticks where tick % k == k-1, so every
 * window (including the first) spans exactly k ticks; rows where a block
 * is not sampled leave its columns empty.  Writing eventN resets the
 * counter and rebinds it; tile groups are re-programmed at the end of each
 * sampled tile tick and the first read after programming is discarded as
 * baseline.  L3 counters run while enable=1 and are frozen (enable=0) and
 * read at each sampled tick, so L3 windows align with tile windows.
 * Persistent blocks use the plain delta method (cur - prev).
 *
 * Measured sysfs cost on BF2: 4 event writes ~0.22 ms, 16 counter reads
 * ~1 ms.  Per tick this program does ~60 reads + 16 writes -> ~4-5 ms ->
 * ~0.5% of a 1 s window.  One-second rotation is safe.
 *
 * ============================================================================
 * Build & run
 * ============================================================================
 *   make            (or: make CROSS=aarch64-linux-gnu-)
 *   sudo ./collect_all [-c configs/default.conf] [-i 1] [-d 3600]
 *                      [-o trace.csv] [-q]
 *   ./collect_all --list-events    list the full event catalog
 *   ./collect_all --dump-config    print the default config template
 *   ./collect_all -c FILE --check-config   validate FILE, print resolution
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <signal.h>
#include <time.h>
#include <errno.h>
#include <dirent.h>
#include <stdint.h>
#include <sys/syscall.h>
#include <sys/ioctl.h>
#include <linux/perf_event.h>

#include "catalog.h"
#include "config.h"

/* ================================================================== */
/*  Constants                                                          */
/* ================================================================== */

#define MAX_TILES      8
#define MAX_TILENETS   8
#define MAX_TRIOS      4
#define MAX_SMMUS      4
#define MAX_GICS       4
#define MAX_L3HALVES   4
#define MAX_PCIES      4
#define MAX_CORES      32
#define MAX_IFACES     8
/* 512: worst case "%s/%s" with two MAX_PATH_LEN strings = 255+1+255 < 512 */
#define MAX_PATH_LEN   512

/* --- ARM PMU fds per core --- */
#define FDS_PER_CORE 4   /* L1D access, L1D miss, L1I access, L1I miss */

/* ================================================================== */
/*  Global state                                                       */
/* ================================================================== */

static volatile sig_atomic_t g_running = 1;
static int  g_quiet = 0;

static bf2_config_t   g_cfg;
static bf2_presence_t g_pres;

static void sig_handler(int sig) { (void)sig; g_running = 0; }

/* ---- tile rotation state ---- */
static int   g_ntiles = 0;
static int   g_have_tile = 0;
static int   g_tile_gid = 0;      /* active rotation group */
static char  g_tile_path[MAX_TILES][MAX_PATH_LEN];
static unsigned long long g_tile_prev[MAX_TILES][CFG_SLOTS_MAX];

/* ---- tilenet state ---- */
static int   g_ntilenets = 0;
static int   g_have_tilenet = 0;
static char  g_tn_path[MAX_TILENETS][MAX_PATH_LEN];
static unsigned long long g_tn_prev[MAX_TILENETS][CFG_SLOTS_MAX];

/* ---- trio state ---- */
static int   g_ntrios = 0;
static int   g_have_trio = 0;
static char  g_trio_path[MAX_TRIOS][MAX_PATH_LEN];
static unsigned long long g_trio_prev[MAX_TRIOS][CFG_SLOTS_MAX];

/* ---- smmu state ---- */
static int   g_nsmmus = 0;
static char  g_smmu_path[MAX_SMMUS][MAX_PATH_LEN];
static unsigned long long g_smmu_prev[MAX_SMMUS][CFG_SLOTS_MAX];

/* ---- triogen state (triogen0 = TX_DAT_AF, triogen1 = RX_DAT_AF) ---- */
static int   g_ntriogens = 0;
static char  g_triogen_path[MAX_TRIOS][MAX_PATH_LEN];
static unsigned long long g_triogen_prev[MAX_TRIOS];

/* ---- L3 state ---- */
static int   g_nl3halves = 0;
static int   g_l3_gid = 0;   /* active rotation group (rotation mode) */
static char  g_l3_path[MAX_L3HALVES][MAX_PATH_LEN];
static int   g_l3_has_enable[MAX_L3HALVES];
static unsigned long long g_l3_prev[MAX_L3HALVES][CFG_SLOTS_MAX];

/* ---- PCIe TLR state (mechanism 2) ---- */
static int   g_npcie = 0;
static int   g_have_pcie = 0;
static char  g_pcie_path[MAX_PCIES][MAX_PATH_LEN];
static unsigned long long g_pcie_prev[MAX_PCIES][CFG_REGS_MAX];

/* ---- gic state (unverified on device) ---- */
static int   g_ngics = 0;
static char  g_gic_path[MAX_GICS][MAX_PATH_LEN];
static unsigned long long g_gic_prev[MAX_GICS][CFG_SLOTS_MAX];

/* ---- ARM PMU state ---- */
static int   g_pmu_ok = 0;
static int   g_ncores = 0;
static int   g_fds[MAX_CORES][FDS_PER_CORE];
static unsigned long long g_pmu_prev[MAX_CORES][FDS_PER_CORE];

/* ---- software metrics state ---- */
static int   g_cpu_ok = 0;
static unsigned long long g_cpu_prev_total[8];          /* user,nice,sys,idle,iowait,irq,softirq,steal */
static unsigned long long g_cpu_prev_core[MAX_CORES][8];
static int   g_cpu_ncores = 0;

static int   g_net_ok = 0;
static char  g_ifname[MAX_IFACES][32];
static unsigned long long g_if_prev_rx[MAX_IFACES];
static unsigned long long g_if_prev_tx[MAX_IFACES];
static unsigned long long g_if_drx[MAX_IFACES];   /* per-iface deltas, */
static unsigned long long g_if_dtx[MAX_IFACES];   /* written as columns */
static int   g_nifaces = 0;

/* ================================================================== */
/*  sysfs helpers                                                      */
/* ================================================================== */

/* Warn once per kind of failure, then stay quiet to avoid flooding stderr */
static int g_read_warned = 0;
static int g_write_warned = 0;

static ssize_t file_read_all(const char *path, char *buf, size_t bufsz)
{
    FILE *fp = fopen(path, "r");
    if (!fp) return -1;
    size_t n = fread(buf, 1, bufsz - 1, fp);
    int saved_errno = errno;
    fclose(fp);
    if (n == 0 && bufsz > 0) { errno = saved_errno; return -1; }
    buf[n] = '\0';
    return (ssize_t)n;
}

static int sysfs_write(const char *path, const char *val)
{
    FILE *fp = fopen(path, "w");
    if (!fp) return -1;
    int rc = fprintf(fp, "%s", val);
    int saved_errno = errno;
    fclose(fp);
    errno = saved_errno;
    return (rc < 0) ? -1 : 0;
}

static int sysfs_exists(const char *path)
{
    FILE *fp = fopen(path, "r");
    if (!fp) return 0;
    fclose(fp);
    return 1;
}

static unsigned long long read_counter(const char *dir, int idx)
{
    char path[MAX_PATH_LEN], buf[64];
    snprintf(path, sizeof(path), "%s/counter%d", dir, idx);
    if (file_read_all(path, buf, sizeof(buf)) < 0) {
        if (!g_read_warned) {
            fprintf(stderr, "[WARN] read failed: %s: %s\n",
                    path, strerror(errno));
            g_read_warned = 1;
        }
        return 0;
    }
    return strtoull(buf, NULL, 0);
}

/* Read a named register (PCIe TLR, mechanism 2) */
static unsigned long long read_reg(const char *dir, const char *name)
{
    char path[MAX_PATH_LEN], buf[64];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    if (file_read_all(path, buf, sizeof(buf)) < 0) {
        if (!g_read_warned) {
            fprintf(stderr, "[WARN] read failed: %s: %s\n",
                    path, strerror(errno));
            g_read_warned = 1;
        }
        return 0;
    }
    return strtoull(buf, NULL, 0);
}

/* Program event slot idx of one block with an event code */
static void write_event(const char *dir, int idx, unsigned int code)
{
    char path[MAX_PATH_LEN], hex[16];
    snprintf(path, sizeof(path), "%s/event%d", dir, idx);
    snprintf(hex, sizeof(hex), "0x%x", code);
    if (sysfs_write(path, hex) != 0) {
        if (!g_write_warned) {
            fprintf(stderr, "[WARN] program failed: %s: %s\n",
                    path, strerror(errno));
            g_write_warned = 1;
        }
    }
}

/* Program the same event list into several block directories */
static void program_block_events(char (*paths)[MAX_PATH_LEN], int nblocks,
                                 const cfg_event_t *evs, int nev)
{
    int i, j;
    for (i = 0; i < nblocks; i++)
        for (j = 0; j < nev; j++)
            write_event(paths[i], j, (unsigned int)evs[j].code);
}

/* Sleep for sec seconds; return -1 if a signal stopped the run so the
 * caller exits WITHOUT writing a partial row. */
static int sleep_sec_interruptible(int sec)
{
    struct timespec rem = { .tv_sec = sec, .tv_nsec = 0 };
    while (nanosleep(&rem, &rem) == -1 && errno == EINTR) {
        if (!g_running)
            return -1;
    }
    return 0;
}

/* ================================================================== */
/*  Phase 1 -- Find bfperf                                              */
/* ================================================================== */

static int find_bfperf(char *base, size_t base_sz)
{
    int i;
    for (i = 0; i < 16; i++) {
        char name_path[MAX_PATH_LEN], name_buf[64];
        snprintf(name_path, sizeof(name_path),
                 "/sys/class/hwmon/hwmon%d/name", i);
        if (file_read_all(name_path, name_buf, sizeof(name_buf)) < 0)
            continue;
        size_t len = strlen(name_buf);
        while (len > 0 && (name_buf[len-1] == '\n' || name_buf[len-1] == '\r'))
            name_buf[--len] = '\0';
        if (strcmp(name_buf, "bfperf") == 0) {
            snprintf(base, base_sz, "/sys/class/hwmon/hwmon%d", i);
            return 0;
        }
    }
    return -1;
}

/* ================================================================== */
/*  Tile HNF -- rotating groups                                        */
/* ================================================================== */

static int discover_tiles(const char *base)
{
    g_ntiles = 0;
    DIR *d = opendir(base);
    if (!d) return -1;

    struct dirent *entry;
    while ((entry = readdir(d)) != NULL && g_ntiles < MAX_TILES) {
        /* Match "tileN" (exactly one digit), exclude "tilenetN" */
        if (strncmp(entry->d_name, "tile", 4) != 0) continue;
        if (entry->d_name[4] < '0' || entry->d_name[4] > '9') continue;
        if (entry->d_name[5] != '\0') continue;

        snprintf(g_tile_path[g_ntiles], MAX_PATH_LEN,
                 "%.400s/%.16s", base, entry->d_name);
        g_ntiles++;
    }
    closedir(d);
    return (g_ntiles > 0) ? 0 : -1;
}

/*
 * Program rotation group gid into every tile.  Writing eventN resets
 * the counter and binds it to the new event, so this both re-binds
 * and zeroes the window.
 */
static void program_tile_group(int gid)
{
    int nev = g_cfg.tile.n_group_ev[gid];
    program_block_events(g_tile_path, g_ntiles, g_cfg.tile.groups[gid],
                         nev);
}

/* Baseline: read current values so the first window delta starts here */
static void read_tile_baseline(void)
{
    int nev = g_cfg.tile.n_group_ev[g_tile_gid];
    int i, j;
    for (i = 0; i < g_ntiles; i++)
        for (j = 0; j < nev; j++)
            g_tile_prev[i][j] = read_counter(g_tile_path[i], j);
}

/* Read the active group's counters, sum per-event deltas over tiles */
static void read_tile_delta(unsigned long long *delta_out)
{
    int nev = g_cfg.tile.n_group_ev[g_tile_gid];
    int i, j;

    for (j = 0; j < nev; j++) delta_out[j] = 0;
    for (i = 0; i < g_ntiles; i++) {
        for (j = 0; j < nev; j++) {
            unsigned long long cur = read_counter(g_tile_path[i], j);
            unsigned long long d = (cur >= g_tile_prev[i][j])
                                 ? (cur - g_tile_prev[i][j]) : 0;
            g_tile_prev[i][j] = cur;
            delta_out[j] += d;
        }
    }
}

/* ================================================================== */
/*  tilenet -- persistent                                              */
/* ================================================================== */

static int discover_tilenets(const char *base)
{
    g_ntilenets = 0;
    DIR *d = opendir(base);
    if (!d) return -1;

    struct dirent *entry;
    while ((entry = readdir(d)) != NULL && g_ntilenets < MAX_TILENETS) {
        /* Match "tilenetN" exactly */
        if (strncmp(entry->d_name, "tilenet", 7) != 0) continue;
        if (entry->d_name[7] < '0' || entry->d_name[7] > '9') continue;
        if (entry->d_name[8] != '\0') continue;

        snprintf(g_tn_path[g_ntilenets], MAX_PATH_LEN,
                 "%.400s/%.16s", base, entry->d_name);
        g_ntilenets++;
    }
    closedir(d);
    return (g_ntilenets > 0) ? 0 : -1;
}

static void program_tilenets(void)
{
    program_block_events(g_tn_path, g_ntilenets, g_cfg.tilenet.events,
                         g_cfg.tilenet.n_events);
}

static void read_tilenet_delta(unsigned long long *delta_out)
{
    int nev = g_cfg.tilenet.n_events;
    int i, j;

    for (j = 0; j < nev; j++) delta_out[j] = 0;
    for (i = 0; i < g_ntilenets; i++) {
        for (j = 0; j < nev; j++) {
            unsigned long long cur = read_counter(g_tn_path[i], j);
            unsigned long long d = (cur >= g_tn_prev[i][j])
                                 ? (cur - g_tn_prev[i][j]) : 0;
            g_tn_prev[i][j] = cur;
            delta_out[j] += d;
        }
    }
}

/* ================================================================== */
/*  trio -- persistent                                                 */
/* ================================================================== */

static int discover_trios(const char *base)
{
    g_ntrios = 0;
    DIR *d = opendir(base);
    if (!d) return -1;

    struct dirent *entry;
    while ((entry = readdir(d)) != NULL && g_ntrios < MAX_TRIOS) {
        /* Match "trioN" (not "triogenN") */
        if (strncmp(entry->d_name, "trio", 4) != 0) continue;
        if (entry->d_name[4] < '0' || entry->d_name[4] > '9') continue;
        if (entry->d_name[5] != '\0') continue;

        snprintf(g_trio_path[g_ntrios], MAX_PATH_LEN,
                 "%.400s/%.16s", base, entry->d_name);
        g_ntrios++;
    }
    closedir(d);
    return (g_ntrios > 0) ? 0 : -1;
}

static void program_trios(void)
{
    program_block_events(g_trio_path, g_ntrios, g_cfg.trio.events,
                         g_cfg.trio.n_events);
}

static void read_trio_delta(unsigned long long *delta_out)
{
    int nev = g_cfg.trio.n_events;
    int i, j;

    for (j = 0; j < nev; j++) delta_out[j] = 0;
    for (i = 0; i < g_ntrios; i++) {
        for (j = 0; j < nev; j++) {
            unsigned long long cur = read_counter(g_trio_path[i], j);
            unsigned long long d = (cur >= g_trio_prev[i][j])
                                 ? (cur - g_trio_prev[i][j]) : 0;
            g_trio_prev[i][j] = cur;
            delta_out[j] += d;
        }
    }
}

/* ================================================================== */
/*  SMMU / gic -- persistent, "smmuN"/"gicN" directories               */
/* ================================================================== */

static int discover_named(const char *base, const char *prefix,
                          char (*paths)[MAX_PATH_LEN], int max_blocks)
{
    int n = 0;
    size_t plen = strlen(prefix);
    DIR *d = opendir(base);
    if (!d) return 0;

    struct dirent *entry;
    while ((entry = readdir(d)) != NULL && n < max_blocks) {
        if (strncmp(entry->d_name, prefix, plen) != 0) continue;
        if (entry->d_name[plen] < '0' || entry->d_name[plen] > '9')
            continue;
        if (entry->d_name[plen + 1] != '\0') continue;
        snprintf(paths[n], MAX_PATH_LEN, "%.400s/%.16s",
                 base, entry->d_name);
        n++;
    }
    closedir(d);
    return n;
}

static int init_smmu(const char *base)
{
    g_nsmmus = discover_named(base, "smmu", g_smmu_path, MAX_SMMUS);
    if (g_nsmmus > 0)
        program_block_events(g_smmu_path, g_nsmmus, g_cfg.smmu.events,
                             g_cfg.smmu.n_events);
    return g_nsmmus;
}

static int init_gic(const char *base)
{
    g_ngics = discover_named(base, "gic", g_gic_path, MAX_GICS);
    if (g_ngics > 0)
        program_block_events(g_gic_path, g_ngics, g_cfg.gic.events,
                             g_cfg.gic.n_events);
    return g_ngics;
}

static void read_multi_delta(char (*paths)[MAX_PATH_LEN], int nblocks,
                             int nev, unsigned long long (*prev)[CFG_SLOTS_MAX],
                             unsigned long long *delta_out)
{
    int i, j;

    for (j = 0; j < nev; j++) delta_out[j] = 0;
    for (i = 0; i < nblocks; i++) {
        for (j = 0; j < nev; j++) {
            unsigned long long cur = read_counter(paths[i], j);
            unsigned long long d = (cur >= prev[i][j])
                                 ? (cur - prev[i][j]) : 0;
            prev[i][j] = cur;
            delta_out[j] += d;
        }
    }
}

static void read_smmu_delta(unsigned long long *delta_out)
{
    read_multi_delta(g_smmu_path, g_nsmmus, g_cfg.smmu.n_events,
                     g_smmu_prev, delta_out);
}

static void read_gic_delta(unsigned long long *delta_out)
{
    read_multi_delta(g_gic_path, g_ngics, g_cfg.gic.n_events,
                     g_gic_prev, delta_out);
}

/* ================================================================== */
/*  triogen -- persistent (triogen0 = TX_DAT_AF, triogen1 = RX_DAT_AF) */
/* ================================================================== */

static void init_triogens(const char *base)
{
    const unsigned int codes[2] = { 0x0f, 0x10 };
    int i;

    g_ntriogens = 0;
    for (i = 0; i < 2; i++) {
        char path[MAX_PATH_LEN];
        snprintf(path, sizeof(path), "%.502s/triogen%d", base, i);
        if (!sysfs_exists(path)) continue;
        snprintf(g_triogen_path[g_ntriogens], MAX_PATH_LEN, "%s", path);
        write_event(path, 0, codes[i]);
        g_ntriogens++;
    }
}

static void read_triogen_delta(unsigned long long *delta_out)
{
    int i;
    for (i = 0; i < g_ntriogens; i++) {
        unsigned long long cur = read_counter(g_triogen_path[i], 0);
        delta_out[i] = (cur >= g_triogen_prev[i])
                     ? (cur - g_triogen_prev[i]) : 0;
        g_triogen_prev[i] = cur;
    }
}

/* ================================================================== */
/*  L3 cache -- persistent, enable=1/0 grouped start/stop              */
/* ================================================================== */

static int discover_l3halves(const char *base)
{
    g_nl3halves = 0;
    DIR *d = opendir(base);
    if (!d) return -1;

    struct dirent *entry;
    while ((entry = readdir(d)) != NULL && g_nl3halves < MAX_L3HALVES) {
        /* Match "l3cachehalfN" (exactly one digit) */
        if (strncmp(entry->d_name, "l3cachehalf", 11) != 0) continue;
        if (entry->d_name[11] < '0' || entry->d_name[11] > '9') continue;

        snprintf(g_l3_path[g_nl3halves], MAX_PATH_LEN,
                 "%.400s/%.16s", base, entry->d_name);

        char en_path[MAX_PATH_LEN];
        snprintf(en_path, sizeof(en_path), "%.500s/enable",
                 g_l3_path[g_nl3halves]);
        g_l3_has_enable[g_nl3halves] = sysfs_exists(en_path);

        g_nl3halves++;
    }
    closedir(d);
    return (g_nl3halves > 0) ? 0 : -1;
}

static void program_l3_group(int gid)
{
    program_block_events(g_l3_path, g_nl3halves,
                         g_cfg.l3cache.groups[gid],
                         g_cfg.l3cache.n_group_ev[gid]);
}

static void program_l3halves(void)
{
    if (g_cfg.l3cache.n_groups > 0)
        program_l3_group(g_l3_gid);
    else
        program_block_events(g_l3_path, g_nl3halves, g_cfg.l3cache.events,
                             g_cfg.l3cache.n_events);
}

/* Write 1 to enable -> counters reset and start */
static void l3_enable_all(void)
{
    int i;
    for (i = 0; i < g_nl3halves; i++) {
        char path[MAX_PATH_LEN];
        if (!g_l3_has_enable[i]) continue;
        snprintf(path, sizeof(path), "%.500s/enable", g_l3_path[i]);
        if (sysfs_write(path, "1") != 0 && !g_write_warned) {
            fprintf(stderr, "[WARN] L3 enable failed: %s: %s\n",
                    path, strerror(errno));
            g_write_warned = 1;
        }
    }
}

/* Write 0 to enable -> counters freeze */
static void l3_disable_all(void)
{
    int i;
    for (i = 0; i < g_nl3halves; i++) {
        char path[MAX_PATH_LEN];
        if (!g_l3_has_enable[i]) continue;
        snprintf(path, sizeof(path), "%.500s/enable", g_l3_path[i]);
        if (sysfs_write(path, "0") != 0 && !g_write_warned) {
            fprintf(stderr, "[WARN] L3 disable failed: %s: %s\n",
                    path, strerror(errno));
            g_write_warned = 1;
        }
    }
}

/* Baseline for halves WITHOUT an enable file (delta mode) */
static void read_l3_baseline(void)
{
    int nev = (g_cfg.l3cache.n_groups > 0)
            ? g_cfg.l3cache.n_group_ev[g_l3_gid]
            : g_cfg.l3cache.n_events;
    int i, j;
    for (i = 0; i < g_nl3halves; i++) {
        if (g_l3_has_enable[i]) continue;
        for (j = 0; j < nev; j++)
            g_l3_prev[i][j] = read_counter(g_l3_path[i], j);
    }
}

/*
 * Read L3 counters after they have been frozen (enable=0): the value IS
 * the window delta (the next enable resets).  Halves without an enable
 * file fall back to the plain delta method.
 */
static void read_l3_vals(unsigned long long vals[MAX_L3HALVES][CFG_SLOTS_MAX])
{
    int nev = (g_cfg.l3cache.n_groups > 0)
            ? g_cfg.l3cache.n_group_ev[g_l3_gid]
            : g_cfg.l3cache.n_events;
    int i, j;

    for (i = 0; i < g_nl3halves; i++) {
        for (j = 0; j < nev; j++) {
            unsigned long long cur = read_counter(g_l3_path[i], j);
            if (g_l3_has_enable[i]) {
                vals[i][j] = cur;
            } else {
                vals[i][j] = (cur >= g_l3_prev[i][j])
                           ? (cur - g_l3_prev[i][j]) : 0;
                g_l3_prev[i][j] = cur;
            }
        }
    }
}

/* ================================================================== */
/*  PCIe TLR -- persistent, mechanism 2                                */
/* ================================================================== */

static int discover_pcie_blocks(const char *base)
{
    g_npcie = 0;
    DIR *d = opendir(base);
    if (!d) return -1;

    struct dirent *entry;
    while ((entry = readdir(d)) != NULL && g_npcie < MAX_PCIES) {
        if (strncmp(entry->d_name, "pcie", 4) != 0) continue;
        if (entry->d_name[4] < '0' || entry->d_name[4] > '9') continue;

        snprintf(g_pcie_path[g_npcie], MAX_PATH_LEN,
                 "%.400s/%.16s", base, entry->d_name);
        g_npcie++;
    }
    closedir(d);
    return (g_npcie > 0) ? 0 : -1;
}

/* Mechanism 2: registers accumulate since boot and are never reset,
 * so an explicit baseline read is required. */
static void read_pcie_baseline(void)
{
    int i, r;
    int nr = g_cfg.pcie.n_regs;

    for (i = 0; i < g_npcie; i++)
        for (r = 0; r < nr; r++)
            g_pcie_prev[i][r] =
                read_reg(g_pcie_path[i], g_cfg.pcie.regs[r].name);
}

static void read_pcie_delta(unsigned long long delta[MAX_PCIES][CFG_REGS_MAX])
{
    int i, r;
    int nr = g_cfg.pcie.n_regs;

    for (i = 0; i < g_npcie; i++) {
        for (r = 0; r < nr; r++) {
            unsigned long long cur =
                read_reg(g_pcie_path[i], g_cfg.pcie.regs[r].name);
            delta[i][r] = (cur >= g_pcie_prev[i][r])
                        ? (cur - g_pcie_prev[i][r]) : 0;
            g_pcie_prev[i][r] = cur;
        }
    }
}

/* ================================================================== */
/*  ARM PMU (L1) -- persistent, perf_event_open                        */
/* ================================================================== */

static long perf_event_open(struct perf_event_attr *attr,
                            pid_t pid, int cpu, int group_fd,
                            unsigned long flags)
{
    return syscall(__NR_perf_event_open, attr, pid, cpu, group_fd, flags);
}

static void make_attr(struct perf_event_attr *attr,
                      uint8_t cache_type, uint8_t cache_op,
                      uint8_t cache_result)
{
    memset(attr, 0, sizeof(*attr));
    attr->type   = PERF_TYPE_HW_CACHE;
    attr->size   = sizeof(*attr);
    attr->config = cache_type
                 | ((uint64_t)cache_op     << 8)
                 | ((uint64_t)cache_result << 16);
    attr->disabled       = 1;
    attr->inherit         = 0;
    attr->exclude_kernel  = 0;
    attr->exclude_hv      = 1;
    attr->read_format     = PERF_FORMAT_TOTAL_TIME_ENABLED
                          | PERF_FORMAT_TOTAL_TIME_RUNNING;
}

static int pmu_init(void)
{
    int level = 2;
    FILE *fp = fopen("/proc/sys/kernel/perf_event_paranoid", "r");
    if (fp) {
        char buf[16];
        if (fgets(buf, sizeof(buf), fp)) level = atoi(buf);
        fclose(fp);
    }
    if (level > 1 && !g_quiet)
        fprintf(stderr, "[WARN] perf_event_paranoid=%d (needs <=1 or root)\n", level);

    g_ncores = (int)sysconf(_SC_NPROCESSORS_CONF);
    if (g_ncores <= 0) return -1;
    if (g_ncores > MAX_CORES) g_ncores = MAX_CORES;

    int c, j;
    for (c = 0; c < g_ncores; c++)
        for (j = 0; j < FDS_PER_CORE; j++)
            g_fds[c][j] = -1;

    struct perf_event_attr attrs[FDS_PER_CORE];
    make_attr(&attrs[0], PERF_COUNT_HW_CACHE_L1D,
              PERF_COUNT_HW_CACHE_OP_READ, PERF_COUNT_HW_CACHE_RESULT_ACCESS);
    make_attr(&attrs[1], PERF_COUNT_HW_CACHE_L1D,
              PERF_COUNT_HW_CACHE_OP_READ, PERF_COUNT_HW_CACHE_RESULT_MISS);
    make_attr(&attrs[2], PERF_COUNT_HW_CACHE_L1I,
              PERF_COUNT_HW_CACHE_OP_READ, PERF_COUNT_HW_CACHE_RESULT_ACCESS);
    make_attr(&attrs[3], PERF_COUNT_HW_CACHE_L1I,
              PERF_COUNT_HW_CACHE_OP_READ, PERF_COUNT_HW_CACHE_RESULT_MISS);

    int cores_ok = 0;
    for (c = 0; c < g_ncores; c++) {
        int leader = -1;
        for (j = 0; j < FDS_PER_CORE; j++) {
            int fd = (int)perf_event_open(&attrs[j], -1, c, leader, 0);
            if (fd < 0) {
                int k;
                for (k = 0; k < j; k++) {
                    close(g_fds[c][k]); g_fds[c][k] = -1;
                }
                break;
            }
            g_fds[c][j] = fd;
            if (j == 0) leader = fd;
        }
        if (g_fds[c][0] >= 0) {
            cores_ok++;
            for (j = 0; j < FDS_PER_CORE; j++) {
                ioctl(g_fds[c][j], PERF_EVENT_IOC_RESET, 0);
                ioctl(g_fds[c][j], PERF_EVENT_IOC_ENABLE, 0);
            }
        }
    }

    if (cores_ok == 0) return -1;
    if (!g_quiet)
        fprintf(stderr, "[INFO] L1 PMU opened on %d/%d cores\n", cores_ok, g_ncores);
    return 0;
}

static unsigned long long read_pmu(int cpu, int idx)
{
    if (g_fds[cpu][idx] < 0) return 0;

    struct { unsigned long long val, enabled, running; } buf;
    if (read(g_fds[cpu][idx], &buf, sizeof(buf)) != sizeof(buf)) {
        if (!g_read_warned) {
            fprintf(stderr, "[WARN] PMU read failed (cpu %d, fd %d): %s\n",
                    cpu, g_fds[cpu][idx], strerror(errno));
            g_read_warned = 1;
        }
        return 0;
    }

    /* Scale if counter was multiplexed (running < enabled) */
    if (buf.running > 0 && buf.enabled > buf.running)
        buf.val = buf.val * buf.enabled / buf.running;
    return buf.val;
}

static void pmu_shutdown(void)
{
    int c, j;
    for (c = 0; c < g_ncores; c++) {
        for (j = 0; j < FDS_PER_CORE; j++) {
            if (g_fds[c][j] >= 0) {
                ioctl(g_fds[c][j], PERF_EVENT_IOC_DISABLE, 0);
                close(g_fds[c][j]);
                g_fds[c][j] = -1;
            }
        }
    }
    g_ncores = 0;
}

static void read_pmu_delta(unsigned long long *delta_out)
{
    int c, j;

    for (j = 0; j < FDS_PER_CORE; j++) delta_out[j] = 0;
    for (c = 0; c < g_ncores; c++) {
        if (g_fds[c][0] < 0) continue;

        unsigned long long cur[FDS_PER_CORE];
        for (j = 0; j < FDS_PER_CORE; j++)
            cur[j] = read_pmu(c, j);

        for (j = 0; j < FDS_PER_CORE; j++) {
            delta_out[j] += (cur[j] >= g_pmu_prev[c][j])
                          ? (cur[j] - g_pmu_prev[c][j]) : 0;
            g_pmu_prev[c][j] = cur[j];
        }
    }
}

/* ================================================================== */
/*  Software metrics: /proc/stat, /proc/meminfo, /sys/class/net        */
/* ================================================================== */

static int cpu_init(void)
{
    FILE *fp = fopen("/proc/stat", "r");
    if (!fp) return -1;

    char line[256];
    int core_idx = 0;
    while (fgets(line, sizeof(line), fp)) {
        if (strncmp(line, "cpu", 3) != 0) break;
        unsigned long long v[8];
        if (line[3] == ' ') {
            int n = sscanf(line + 4, "%llu %llu %llu %llu %llu %llu %llu %llu",
                           &v[0], &v[1], &v[2], &v[3],
                           &v[4], &v[5], &v[6], &v[7]);
            int k;
            if (n < 4) continue;
            for (k = n; k < 8; k++) v[k] = 0;
            for (k = 0; k < 8; k++) g_cpu_prev_total[k] = v[k];
        } else if (core_idx < MAX_CORES) {
            int n = sscanf(line + 3, "%*d %llu %llu %llu %llu %llu %llu %llu %llu",
                           &v[0], &v[1], &v[2], &v[3],
                           &v[4], &v[5], &v[6], &v[7]);
            int k;
            if (n < 4) continue;
            for (k = n; k < 8; k++) v[k] = 0;
            for (k = 0; k < 8; k++) g_cpu_prev_core[core_idx][k] = v[k];
            core_idx++;
        }
    }
    fclose(fp);

    g_cpu_ncores = core_idx;
    if (g_cpu_ncores == 0) return -1;
    return 0;
}

static unsigned long long cpu_total(const unsigned long long v[8])
{
    return v[0] + v[1] + v[2] + v[3] + v[4] + v[5] + v[6] + v[7];
}

static unsigned long long cpu_busy(const unsigned long long v[8])
{
    return v[0] + v[1] + v[2] + v[4] + v[5] + v[6] + v[7];
}

/* Compute CPU utilisation (delta method).  Output: total util, min/max/avg
 * per-core util.  All values are percentages 0..100. */
static void cpu_sample(double *util_pct, double *min_pct,
                       double *max_pct, double *avg_pct)
{
    FILE *fp = fopen("/proc/stat", "r");
    if (!fp) { *util_pct = 0.0; *min_pct = 0.0; *max_pct = 0.0; *avg_pct = 0.0; return; }

    unsigned long long cur_total[8] = {0};
    unsigned long long cur_core[MAX_CORES][8];
    memset(cur_core, 0, sizeof(cur_core));
    int core_idx = 0;

    char line[256];
    while (fgets(line, sizeof(line), fp)) {
        if (strncmp(line, "cpu", 3) != 0) break;
        unsigned long long v[8];
        if (line[3] == ' ') {
            int n = sscanf(line + 4, "%llu %llu %llu %llu %llu %llu %llu %llu",
                           &v[0], &v[1], &v[2], &v[3],
                           &v[4], &v[5], &v[6], &v[7]);
            int k;
            if (n < 4) continue;
            for (k = n; k < 8; k++) v[k] = 0;
            for (k = 0; k < 8; k++) cur_total[k] = v[k];
        } else if (core_idx < g_cpu_ncores) {
            int n = sscanf(line + 3, "%*d %llu %llu %llu %llu %llu %llu %llu %llu",
                           &v[0], &v[1], &v[2], &v[3],
                           &v[4], &v[5], &v[6], &v[7]);
            int k;
            if (n < 4) continue;
            for (k = n; k < 8; k++) v[k] = 0;
            for (k = 0; k < 8; k++) cur_core[core_idx][k] = v[k];
            core_idx++;
        }
    }
    fclose(fp);

    unsigned long long dt_total = cpu_total(cur_total) - cpu_total(g_cpu_prev_total);
    unsigned long long dt_busy  = cpu_busy(cur_total)  - cpu_busy(g_cpu_prev_total);
    if (dt_total > 0) {
        double u = 100.0 * (double)dt_busy / (double)dt_total;
        if (u < 0.0) u = 0.0;
        if (u > 100.0) u = 100.0;
        *util_pct = u;
    } else {
        *util_pct = 0.0;
    }

    double mn = 100.0, mx = 0.0, sum = 0.0;
    int valid = 0;
    int i;
    for (i = 0; i < g_cpu_ncores; i++) {
        unsigned long long d_t = cpu_total(cur_core[i]) - cpu_total(g_cpu_prev_core[i]);
        if (d_t == 0) continue;
        unsigned long long d_b = cpu_busy(cur_core[i]) - cpu_busy(g_cpu_prev_core[i]);
        double u = 100.0 * (double)d_b / (double)d_t;
        if (u < 0.0) u = 0.0;
        if (u > 100.0) u = 100.0;
        if (u < mn) mn = u;
        if (u > mx) mx = u;
        sum += u;
        valid++;
    }
    *min_pct = (valid > 0) ? mn : 0.0;
    *max_pct = (valid > 0) ? mx : 0.0;
    *avg_pct = (valid > 0) ? sum / (double)valid : 0.0;

    memcpy(g_cpu_prev_total, cur_total, sizeof(cur_total));
    memcpy(g_cpu_prev_core, cur_core, sizeof(cur_core));
}

static void meminfo_sample(unsigned long long *total_kb,
                           unsigned long long *used_kb, double *util_pct)
{
    *total_kb = 0; *used_kb = 0; *util_pct = 0.0;

    FILE *fp = fopen("/proc/meminfo", "r");
    if (!fp) return;

    char line[256];
    unsigned long long total = 0, available = 0;
    while (fgets(line, sizeof(line), fp)) {
        unsigned long long val;
        if (strncmp(line, "MemTotal:", 9) == 0) {
            sscanf(line + 9, "%llu", &val); total = val;
        } else if (strncmp(line, "MemAvailable:", 13) == 0) {
            sscanf(line + 13, "%llu", &val); available = val;
        }
        if (total > 0 && available > 0) break;
    }
    fclose(fp);

    *total_kb = total;
    *used_kb  = (total > available) ? (total - available) : 0;
    *util_pct = (total > 0)
        ? (100.0 * (double)*used_kb / (double)total) : 0.0;
}

static void net_add_iface(const char *name)
{
    char path[MAX_PATH_LEN];
    char buf[64];
    unsigned long long rx = 0, tx = 0;

    snprintf(path, sizeof(path), "/sys/class/net/%s/statistics/rx_bytes",
             name);
    if (file_read_all(path, buf, sizeof(buf)) >= 0)
        rx = strtoull(buf, NULL, 10);
    snprintf(path, sizeof(path), "/sys/class/net/%s/statistics/tx_bytes",
             name);
    if (file_read_all(path, buf, sizeof(buf)) >= 0)
        tx = strtoull(buf, NULL, 10);

    snprintf(g_ifname[g_nifaces], sizeof(g_ifname[g_nifaces]),
             "%.31s", name);
    g_if_prev_rx[g_nifaces] = rx;
    g_if_prev_tx[g_nifaces] = tx;
    g_nifaces++;
}

static int net_init(void)
{
    g_nifaces = 0;

    /* explicit filter: use exactly the configured interfaces */
    if (g_cfg.net.n_ifaces > 0) {
        int i;
        for (i = 0; i < g_cfg.net.n_ifaces && g_nifaces < MAX_IFACES; i++) {
            char path[MAX_PATH_LEN];
            snprintf(path, sizeof(path), "/sys/class/net/%s",
                     g_cfg.net.ifaces[i]);
            if (!sysfs_exists(path))
                fprintf(stderr, "[WARN] interface '%s' not found, "
                        "zero columns\n", g_cfg.net.ifaces[i]);
            /* still register it: the per-interface columns must stay
             * aligned with the header, missing ifaces report 0 delta */
            net_add_iface(g_cfg.net.ifaces[i]);
        }
        return (g_nifaces > 0) ? 0 : -1;
    }

    /* no filter: all non-loopback interfaces */
    DIR *d = opendir("/sys/class/net");
    if (!d) return -1;

    struct dirent *entry;
    while ((entry = readdir(d)) != NULL && g_nifaces < MAX_IFACES) {
        if (entry->d_name[0] == '.') continue;
        if (strcmp(entry->d_name, "lo") == 0) continue;
        net_add_iface(entry->d_name);
    }
    closedir(d);
    return (g_nifaces > 0) ? 0 : -1;
}

static void net_sample(unsigned long long *rx_delta,
                       unsigned long long *tx_delta)
{
    *rx_delta = 0; *tx_delta = 0;

    char buf[64];
    int i;
    for (i = 0; i < g_nifaces; i++) {
        char path[MAX_PATH_LEN];

        snprintf(path, sizeof(path), "/sys/class/net/%s/statistics/rx_bytes",
                 g_ifname[i]);
        unsigned long long rx = 0;
        if (file_read_all(path, buf, sizeof(buf)) >= 0)
            rx = strtoull(buf, NULL, 10);

        snprintf(path, sizeof(path), "/sys/class/net/%s/statistics/tx_bytes",
                 g_ifname[i]);
        unsigned long long tx = 0;
        if (file_read_all(path, buf, sizeof(buf)) >= 0)
            tx = strtoull(buf, NULL, 10);

        g_if_drx[i] = (rx >= g_if_prev_rx[i]) ? (rx - g_if_prev_rx[i]) : 0;
        g_if_dtx[i] = (tx >= g_if_prev_tx[i]) ? (tx - g_if_prev_tx[i]) : 0;
        *rx_delta += g_if_drx[i];
        *tx_delta += g_if_dtx[i];
        g_if_prev_rx[i] = rx;
        g_if_prev_tx[i] = tx;
    }
}

/* ================================================================== */
/*  CSV output -- per-block row writers (order mirrors the header)     */
/* ================================================================== */

static void fill_presence(void)
{
    memset(&g_pres, 0, sizeof(g_pres));
    g_pres.have_tile = g_have_tile;       g_pres.n_tiles = g_ntiles;
    g_pres.have_tilenet = g_have_tilenet; g_pres.n_tilenets = g_ntilenets;
    g_pres.have_trio = g_have_trio;       g_pres.n_trios = g_ntrios;
    g_pres.have_smmu = g_nsmmus > 0;      g_pres.n_smmus = g_nsmmus;
    g_pres.have_triogen = g_ntriogens > 0; g_pres.n_triogens = g_ntriogens;
    g_pres.have_l3 = g_nl3halves > 0;     g_pres.n_l3halves = g_nl3halves;
    g_pres.have_pcie = g_have_pcie;       g_pres.n_pcies = g_npcie;
    g_pres.have_l1 = g_pmu_ok;
    g_pres.have_cpu = g_cpu_ok;
    g_pres.have_mem = 1;   /* /proc/meminfo always readable on Linux */
    g_pres.have_net = g_net_ok;
    g_pres.have_gic = g_ngics > 0;        g_pres.n_gics = g_ngics;
}

static void row_tile(FILE *fp, int sampled, const unsigned long long *delta)
{
    const cfg_block_t *b = &g_cfg.tile;
    int c;

    if (!b->enabled || !g_have_tile) return;
    if (sampled)
        fprintf(fp, ",%d", g_tile_gid);
    else
        fprintf(fp, ",");
    for (c = 0; c < b->n_tile_cols; c++) {
        const cfg_tile_col_t *col = &b->tile_cols[c];
        if (sampled && (col->mask & (1 << g_tile_gid)))
            fprintf(fp, ",%llu", delta[col->slot[g_tile_gid]]);
        else
            fprintf(fp, ",");
    }
}

static void row_list(FILE *fp, const cfg_block_t *b, int present,
                     int sampled, const unsigned long long *delta)
{
    int j;

    if (!b->enabled || !present) return;
    for (j = 0; j < b->n_events; j++) {
        if (sampled)
            fprintf(fp, ",%llu", delta[j]);
        else
            fprintf(fp, ",");
    }
}

static void row_triogen(FILE *fp, int sampled,
                        const unsigned long long *delta)
{
    if (!g_cfg.triogen.enabled || g_ntriogens == 0) return;
    if (sampled) {
        if (g_ntriogens == 2)
            fprintf(fp, ",%llu,%llu", delta[0], delta[1]);
        else
            fprintf(fp, ",%llu,", delta[0]);
    } else {
        fprintf(fp, ",,");
    }
}

static void row_l3(FILE *fp, int sampled, int l3_gid,
                   const unsigned long long vals[MAX_L3HALVES][CFG_SLOTS_MAX])
{
    const cfg_block_t *b = &g_cfg.l3cache;
    int i, u;

    if (!b->enabled || g_nl3halves == 0) return;
    if (b->n_groups > 0) {   /* rotation mode: marker + mask/slot columns */
        if (sampled)
            fprintf(fp, ",%d", l3_gid);
        else
            fprintf(fp, ",");
        for (i = 0; i < g_nl3halves; i++) {
            for (u = 0; u < b->n_l3_rot_cols; u++) {
                const cfg_l3_rot_col_t *col = &b->l3_rot_cols[u];

                if (sampled && (col->mask & (1 << l3_gid))) {
                    unsigned long long v = 0;
                    int j;

                    for (j = 0; j < col->n_slots[l3_gid]; j++)
                        v += vals[i][col->slot[l3_gid][j]];
                    fprintf(fp, ",%llu", v);
                } else {
                    fprintf(fp, ",");
                }
            }
        }
        return;
    }
    for (i = 0; i < g_nl3halves; i++) {
        for (u = 0; u < b->n_l3_cols; u++) {
            const cfg_col_t *col = &b->l3_cols[u];
            if (sampled) {
                unsigned long long v = vals[i][col->ev0];
                if (col->ev1 >= 0)
                    v += vals[i][col->ev1];
                fprintf(fp, ",%llu", v);
            } else {
                fprintf(fp, ",");
            }
        }
    }
}

static void row_pcie(FILE *fp, int sampled,
                     const unsigned long long delta[MAX_PCIES][CFG_REGS_MAX])
{
    const cfg_block_t *b = &g_cfg.pcie;
    int i, u;

    if (!b->enabled || !g_have_pcie) return;
    for (i = 0; i < g_npcie; i++) {
        int kind[CFG_PCIE_UNITS_MAX], idx[CFG_PCIE_UNITS_MAX];
        int nu = config_pcie_units(&g_cfg, kind, idx,
                                   CFG_PCIE_UNITS_MAX);
        for (u = 0; u < nu; u++) {
            if (!sampled) {
                fprintf(fp, ",");
                continue;
            }
            if (kind[u] == 0) {
                unsigned long long v = delta[i][b->rx_idx[0]] +
                                       delta[i][b->rx_idx[1]] +
                                       delta[i][b->rx_idx[2]];
                fprintf(fp, ",%llu", v);
            } else if (kind[u] == 2) {
                unsigned long long v = delta[i][b->tx_idx[0]] +
                                       delta[i][b->tx_idx[1]] +
                                       delta[i][b->tx_idx[2]];
                fprintf(fp, ",%llu", v);
            } else {
                fprintf(fp, ",%llu", delta[i][idx[u]]);
            }
        }
    }
}

static void row_l1(FILE *fp, int sampled, const unsigned long long *delta)
{
    int i;

    if (!g_cfg.l1.enabled || !g_pmu_ok) return;
    for (i = 0; i < 4; i++) {
        if (!g_cfg.l1.l1_sel[i]) continue;
        if (sampled)
            fprintf(fp, ",%llu", delta[i]);
        else
            fprintf(fp, ",");
    }
}

static void row_cpu(FILE *fp, int sampled, double util, double mn,
                    double mx, double avg)
{
    if (!g_cfg.cpu.enabled || !g_cpu_ok) return;
    if (sampled)
        fprintf(fp, ",%.2f,%.2f,%.2f,%.2f", util, mn, mx, avg);
    else
        fprintf(fp, ",,,,");
}

static void row_mem(FILE *fp, int sampled, unsigned long long total,
                    unsigned long long used, double util)
{
    if (!g_cfg.mem.enabled) return;
    if (sampled)
        fprintf(fp, ",%llu,%llu,%.2f", total, used, util);
    else
        fprintf(fp, ",,,");
}

static void row_net(FILE *fp, int sampled, unsigned long long rx,
                    unsigned long long tx)
{
    int i;

    if (!g_cfg.net.enabled || !g_net_ok) return;
    if (sampled) {
        fprintf(fp, ",%llu,%llu", rx, tx);
        for (i = 0; i < g_cfg.net.n_ifaces; i++)
            fprintf(fp, ",%llu,%llu", g_if_drx[i], g_if_dtx[i]);
    } else {
        fprintf(fp, ",,");
        for (i = 0; i < g_cfg.net.n_ifaces; i++)
            fprintf(fp, ",,");
    }
}

/* ================================================================== */
/*  main                                                               */
/* ================================================================== */

static void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s [OPTIONS]\n"
        "Config-driven BF2 resource collector: select counters and per-block\n"
        "sampling rates via an INI config file, then run collection.  One\n"
        "CSV row per global interval tick.\n"
        "\n"
        "OPTIONS:\n"
        "  -c, --config F      Config file (default: built-in = verified\n"
        "                      baseline, see --dump-config)\n"
        "  -i, --interval N    Override [global] interval (default: 1)\n"
        "  -d, --duration N    Override [global] duration, 0 = forever\n"
        "  -o, --output   F    Override [global] output (default: stdout)\n"
        "  -q, --quiet         Suppress info messages\n"
        "  --list-events       Print the event catalog and exit (no hardware)\n"
        "  --dump-config       Print the default config template and exit\n"
        "  --check-config      Validate -c FILE, print the resolved config,\n"
        "                      exit (no hardware access)\n"
        "  -h, --help          Show this help\n",
        prog);
}

int main(int argc, char **argv)
{
    const char *config_path = NULL;
    const char *output_path = NULL;
    int interval_override = -1;
    int duration_override = -1;
    int do_list = 0, do_dump = 0, do_check = 0;
    FILE *out_fp = stdout;
    char errbuf[CFG_ERR_MAX];
    int i;

    /* --- Argument parsing --- */
    for (i = 1; i < argc; i++) {
        if ((strcmp(argv[i], "-c") == 0 || strcmp(argv[i], "--config") == 0)
            && i + 1 < argc) {
            config_path = argv[++i];
        } else if ((strcmp(argv[i], "-i") == 0 || strcmp(argv[i], "--interval") == 0)
                   && i + 1 < argc) {
            interval_override = atoi(argv[++i]);
            if (interval_override < 1) { fprintf(stderr, "ERROR: interval>=1\n"); return 1; }
        } else if ((strcmp(argv[i], "-d") == 0 || strcmp(argv[i], "--duration") == 0)
                   && i + 1 < argc) {
            duration_override = atoi(argv[++i]);
            if (duration_override < 0) { fprintf(stderr, "ERROR: duration>=0\n"); return 1; }
        } else if ((strcmp(argv[i], "-o") == 0 || strcmp(argv[i], "--output") == 0)
                   && i + 1 < argc) {
            output_path = argv[++i];
        } else if (strcmp(argv[i], "-q") == 0 || strcmp(argv[i], "--quiet") == 0) {
            g_quiet = 1;
        } else if (strcmp(argv[i], "--list-events") == 0) {
            do_list = 1;
        } else if (strcmp(argv[i], "--dump-config") == 0) {
            do_dump = 1;
        } else if (strcmp(argv[i], "--check-config") == 0) {
            do_check = 1;
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            usage(argv[0]); return 0;
        } else {
            fprintf(stderr, "ERROR: unknown '%s'\n", argv[i]); usage(argv[0]); return 1;
        }
    }

    /* --- Informational modes: no hardware, no root needed --- */
    if (do_list) {
        catalog_list_events(stdout);
        return 0;
    }
    if (do_dump) {
        config_dump_template(stdout);
        return 0;
    }

    /* --- Load configuration --- */
    catalog_init();
    config_set_defaults(&g_cfg);
    errbuf[0] = '\0';
    if (config_path != NULL &&
        config_parse_file(config_path, &g_cfg, errbuf, sizeof(errbuf)) != 0) {
        fprintf(stderr, "ERROR: %s\n", errbuf);
        return 1;
    }
    if (interval_override > 0)
        g_cfg.interval = interval_override;
    if (duration_override >= 0)
        g_cfg.duration = duration_override;
    if (output_path != NULL)
        snprintf(g_cfg.output, sizeof(g_cfg.output), "%s", output_path);
    if (config_resolve(&g_cfg, errbuf, sizeof(errbuf)) != 0) {
        fprintf(stderr, "ERROR: %s\n", errbuf);
        return 1;
    }
    if (do_check) {
        config_dump_resolved(stdout, &g_cfg);
        return 0;
    }

    /* --- Open output --- */
    if (g_cfg.output[0] != '\0') {
        out_fp = fopen(g_cfg.output, "w");
        if (!out_fp) {
            fprintf(stderr, "ERROR: open '%s': %s\n",
                    g_cfg.output, strerror(errno));
            return 1;
        }
        setvbuf(out_fp, NULL, _IOLBF, 0);
    }

    /* --- Signals --- */
    struct sigaction sa; memset(&sa, 0, sizeof(sa));
    sa.sa_handler = sig_handler;
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGTERM, &sa, NULL);

    /* ================================================================ */
    /*  Initialisation                                                   */
    /* ================================================================ */

    /* 1. Locate bfperf (only needed if any hardware block is enabled) */
    char bfperf_base[MAX_PATH_LEN] = "";
    int need_bfperf = g_cfg.tile.enabled || g_cfg.tilenet.enabled ||
                      g_cfg.trio.enabled || g_cfg.smmu.enabled ||
                      g_cfg.triogen.enabled || g_cfg.l3cache.enabled ||
                      g_cfg.pcie.enabled || g_cfg.gic.enabled;
    if (need_bfperf) {
        if (find_bfperf(bfperf_base, sizeof(bfperf_base)) != 0) {
            fprintf(stderr, "ERROR: bfperf not found. mlxbf-pmc loaded?\n");
            if (out_fp != stdout) fclose(out_fp);
            return 1;
        }
        if (!g_quiet) fprintf(stderr, "[INFO] bfperf: %s\n", bfperf_base);
    }

    /* 2. Tile HNF (rotating) -- mandatory when enabled */
    if (g_cfg.tile.enabled) {
        if (discover_tiles(bfperf_base) != 0 || g_ntiles == 0) {
            fprintf(stderr, "ERROR: no tile blocks found\n");
            if (out_fp != stdout) fclose(out_fp);
            return 1;
        }
        program_tile_group(0);
        read_tile_baseline();
        g_have_tile = 1;
        if (!g_quiet) {
            int g, j;
            fprintf(stderr, "[INFO] %d tile(s), groups:", g_ntiles);
            for (g = 0; g < g_cfg.tile.n_groups; g++) {
                fprintf(stderr, " G%d={", g);
                for (j = 0; j < g_cfg.tile.n_group_ev[g]; j++)
                    fprintf(stderr, "%s%s", j ? "," : "",
                            g_cfg.tile.groups[g][j].name);
                fprintf(stderr, "}");
            }
            fprintf(stderr, "\n");
        }
    }

    /* 3. Persistent mlxbf-pmc blocks -- skipped if disabled or absent */
    if (g_cfg.tilenet.enabled) {
        if (discover_tilenets(bfperf_base) == 0) {
            program_tilenets();
            g_have_tilenet = 1;
            if (!g_quiet) fprintf(stderr, "[INFO] %d tilenet block(s)\n", g_ntilenets);
        } else {
            fprintf(stderr, "[WARN] No tilenet blocks\n");
        }
    }

    if (g_cfg.trio.enabled) {
        if (discover_trios(bfperf_base) == 0) {
            program_trios();
            g_have_trio = 1;
            if (!g_quiet) fprintf(stderr, "[INFO] %d trio block(s)\n", g_ntrios);
        } else {
            fprintf(stderr, "[WARN] No trio blocks\n");
        }
    }

    if (g_cfg.smmu.enabled) {
        if (init_smmu(bfperf_base) > 0) {
            if (!g_quiet) fprintf(stderr, "[INFO] %d smmu block(s)\n", g_nsmmus);
        } else {
            fprintf(stderr, "[WARN] No smmu blocks\n");
        }
    }

    if (g_cfg.triogen.enabled) {
        init_triogens(bfperf_base);
        if (g_ntriogens > 0) {
            if (!g_quiet) fprintf(stderr, "[INFO] %d triogen block(s)\n", g_ntriogens);
        } else {
            fprintf(stderr, "[WARN] No triogen blocks\n");
        }
    }

    if (g_cfg.l3cache.enabled) {
        if (discover_l3halves(bfperf_base) == 0) {
            program_l3halves();
            read_l3_baseline();   /* for halves without enable */
            l3_enable_all();      /* L3 window 0 starts here (enable resets) */
            if (!g_quiet) {
                if (g_cfg.l3cache.n_groups > 0) {
                    int g, j;

                    fprintf(stderr, "[INFO] %d l3cache half(s), groups:",
                            g_nl3halves);
                    for (g = 0; g < g_cfg.l3cache.n_groups; g++) {
                        fprintf(stderr, " G%d={", g);
                        for (j = 0; j < g_cfg.l3cache.n_group_ev[g]; j++)
                            fprintf(stderr, "%s%s", j ? "," : "",
                                    g_cfg.l3cache.groups[g][j].name);
                        fprintf(stderr, "}");
                    }
                    fprintf(stderr, "\n");
                } else {
                    fprintf(stderr, "[INFO] %d l3cache half(s)\n",
                            g_nl3halves);
                }
            }
        } else {
            fprintf(stderr, "[WARN] No l3cache blocks\n");
        }
    }

    if (g_cfg.pcie.enabled) {
        if (discover_pcie_blocks(bfperf_base) == 0) {
            read_pcie_baseline();
            g_have_pcie = 1;
            if (!g_quiet) fprintf(stderr, "[INFO] %d PCIe TLR block(s)\n", g_npcie);
        } else {
            fprintf(stderr, "[WARN] No PCIe blocks\n");
        }
    }

    if (g_cfg.gic.enabled) {
        if (init_gic(bfperf_base) > 0) {
            if (!g_quiet) fprintf(stderr, "[INFO] %d gic block(s)\n", g_ngics);
        } else {
            fprintf(stderr, "[WARN] No gic blocks\n");
        }
    }

    /* 4. ARM PMU (L1) -- optional */
    if (g_cfg.l1.enabled) {
        if (pmu_init() == 0) {
            int c, j;
            /* Baseline read right after enable: counters start at 0, but
             * reading immediately keeps the first row's window exact */
            for (c = 0; c < g_ncores; c++)
                for (j = 0; j < FDS_PER_CORE; j++)
                    g_pmu_prev[c][j] = read_pmu(c, j);
            g_pmu_ok = 1;
        } else {
            fprintf(stderr, "[WARN] L1 PMU unavailable (try sudo)\n");
        }
    }

    /* 5. Software metrics -- optional */
    if (g_cfg.cpu.enabled) {
        if (cpu_init() == 0) g_cpu_ok = 1;
        else fprintf(stderr, "[WARN] /proc/stat unavailable\n");
    }

    if (g_cfg.net.enabled) {
        if (net_init() == 0) g_net_ok = 1;
        else fprintf(stderr, "[WARN] no network interfaces\n");
    }

    /* 6. CSV header (mirrors the config resolution exactly) */
    fill_presence();
    config_write_csv_header(out_fp, &g_cfg, &g_pres);
    if (!g_quiet)
        fprintf(stderr, "[INFO] Collecting... (Ctrl+C to stop)\n");

    /* ================================================================ */
    /*  Main loop                                                        */
    /* ================================================================ */
    /*
     * Window timing (interval = 1 s, k factors from the config):
     *
     *   init:
     *     program tile group 0, baseline read   <- tile window 0 starts
     *     l3_enable_all()                       <- L3 window 0 starts
     *     baseline reads for all persistent blocks
     *
     *   tick N (loop body):
     *     sleep(interval)
     *     sample flags: s = (tick % k == k-1) per block
     *     if s_l3: disable; read L3;            <- L3 window done
     *              (rotating: program next group while frozen)
     *              enable
     *     if s_tile: read tile delta            <- tile window done
     *     read persistent blocks (deltas) + software
     *     write row: ts, tile_group, per-block fields (empty if unsampled)
     *     if s_tile: program next group; baseline <- next tile window starts
     *
     *   Every window spans exactly k ticks, including the first one.
     */
    /* k of a disabled block may be 0 (resolve skips the multiple check);
     * force 1 so the modulo below can never divide by zero */
#define BLK_K(b) ((b).enabled ? (b).k : 1)
    int k_tile = BLK_K(g_cfg.tile), k_tn = BLK_K(g_cfg.tilenet);
    int k_trio = BLK_K(g_cfg.trio), k_smmu = BLK_K(g_cfg.smmu);
    int k_triogen = BLK_K(g_cfg.triogen), k_l3 = BLK_K(g_cfg.l3cache);
    int k_pcie = BLK_K(g_cfg.pcie), k_l1 = BLK_K(g_cfg.l1);
    int k_cpu = BLK_K(g_cfg.cpu), k_mem = BLK_K(g_cfg.mem);
    int k_net = BLK_K(g_cfg.net), k_gic = BLK_K(g_cfg.gic);

    time_t start_time = time(NULL);
    unsigned long long row_count = 0;
    int tick = 0;

    while (g_running) {
        if (g_cfg.duration > 0 && (time(NULL) - start_time) >= g_cfg.duration)
            break;

        if (sleep_sec_interruptible(g_cfg.interval) < 0)
            break;

        int s_tile = g_have_tile && tick % k_tile == k_tile - 1;
        int s_tn = g_have_tilenet && tick % k_tn == k_tn - 1;
        int s_trio = g_have_trio && tick % k_trio == k_trio - 1;
        int s_smmu = g_nsmmus > 0 && tick % k_smmu == k_smmu - 1;
        int s_triogen = g_ntriogens > 0 && tick % k_triogen == k_triogen - 1;
        int s_l3 = g_nl3halves > 0 && tick % k_l3 == k_l3 - 1;
        int s_pcie = g_have_pcie && tick % k_pcie == k_pcie - 1;
        int s_l1 = g_pmu_ok && tick % k_l1 == k_l1 - 1;
        int s_cpu = g_cpu_ok && tick % k_cpu == k_cpu - 1;
        int s_mem = g_cfg.mem.enabled && tick % k_mem == k_mem - 1;
        int s_net = g_net_ok && tick % k_net == k_net - 1;
        int s_gic = g_ngics > 0 && tick % k_gic == k_gic - 1;
#undef BLK_K

        /* --- L3: freeze, read, restart (only on sampled ticks).  In
         * rotation mode the next group is programmed while frozen so the
         * enable below starts it with a clean reset. --- */
        unsigned long long l3_vals[MAX_L3HALVES][CFG_SLOTS_MAX];
        int l3_gid = 0;   /* group whose window just closed (for the row) */
        memset(l3_vals, 0, sizeof(l3_vals));
        if (s_l3) {
            l3_disable_all();
            read_l3_vals(l3_vals);
            l3_gid = g_l3_gid;
            if (g_cfg.l3cache.n_groups > 0) {
                g_l3_gid = (g_l3_gid + 1) % g_cfg.l3cache.n_groups;
                program_l3_group(g_l3_gid);
                read_l3_baseline();   /* halves without enable: new baseline */
            }
            l3_enable_all();
        }

        /* --- Tile HNF: delta since program at end of previous sampled tick */
        unsigned long long tile_delta[CFG_SLOTS_MAX] = {0};
        if (s_tile) read_tile_delta(tile_delta);

        /* --- Persistent mlxbf-pmc blocks --- */
        unsigned long long tn_delta[CFG_SLOTS_MAX]     = {0};
        unsigned long long trio_delta[CFG_SLOTS_MAX]   = {0};
        unsigned long long smmu_delta[CFG_SLOTS_MAX]   = {0};
        unsigned long long gic_delta[CFG_SLOTS_MAX]    = {0};
        unsigned long long triogen_delta[MAX_TRIOS]    = {0};
        unsigned long long pcie_delta[MAX_PCIES][CFG_REGS_MAX];

        memset(pcie_delta, 0, sizeof(pcie_delta));
        if (s_tn)      read_tilenet_delta(tn_delta);
        if (s_trio)    read_trio_delta(trio_delta);
        if (s_smmu)    read_smmu_delta(smmu_delta);
        if (s_gic)     read_gic_delta(gic_delta);
        if (s_triogen) read_triogen_delta(triogen_delta);
        if (s_pcie)    read_pcie_delta(pcie_delta);

        /* --- ARM PMU --- */
        unsigned long long pmu_delta[FDS_PER_CORE] = {0};
        if (s_l1) read_pmu_delta(pmu_delta);

        /* --- Software metrics --- */
        double cpu_util = 0.0, cpu_min = 0.0, cpu_max = 0.0, cpu_avg = 0.0;
        if (s_cpu) cpu_sample(&cpu_util, &cpu_min, &cpu_max, &cpu_avg);

        unsigned long long mem_total = 0, mem_used = 0;
        double mem_util = 0.0;
        if (s_mem) meminfo_sample(&mem_total, &mem_used, &mem_util);

        unsigned long long net_rx = 0, net_tx = 0;
        if (s_net) net_sample(&net_rx, &net_tx);

        /* --- Timestamp --- */
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);

        /* --- Write CSV row (per-block writers mirror the header) --- */
        fprintf(out_fp, "%lld", (long long)ts.tv_sec);
        row_tile(out_fp, s_tile, tile_delta);
        row_list(out_fp, &g_cfg.tilenet, g_have_tilenet, s_tn, tn_delta);
        row_list(out_fp, &g_cfg.trio, g_have_trio, s_trio, trio_delta);
        row_list(out_fp, &g_cfg.smmu, g_nsmmus > 0, s_smmu, smmu_delta);
        row_triogen(out_fp, s_triogen, triogen_delta);
        row_l3(out_fp, s_l3, l3_gid, l3_vals);
        row_pcie(out_fp, s_pcie, pcie_delta);
        row_l1(out_fp, s_l1, pmu_delta);
        row_cpu(out_fp, s_cpu, cpu_util, cpu_min, cpu_max, cpu_avg);
        row_mem(out_fp, s_mem, mem_total, mem_used, mem_util);
        row_net(out_fp, s_net, net_rx, net_tx);
        row_list(out_fp, &g_cfg.gic, g_ngics > 0, s_gic, gic_delta);
        fprintf(out_fp, "\n");
        fflush(out_fp);

        row_count++;

        if (!g_quiet && row_count % 10 == 0) {
            time_t elapsed = time(NULL) - start_time;
            fprintf(stderr,
                    "[INFO] %llu rows | %lds elapsed | "
                    "cpu=%.1f%% l1d=%.3fM l1i=%.3fM trio=%llu\n",
                    row_count, elapsed, cpu_util,
                    (double)pmu_delta[0] / 1e6, (double)pmu_delta[2] / 1e6,
                    trio_delta[0]);
        }

        if (!g_running) break;

        /* --- Rotate tile group for the next window (sampled ticks only) --- */
        if (s_tile) {
            int ng = g_cfg.tile.n_groups;
            g_tile_gid = (g_tile_gid + 1) % ng;
            program_tile_group(g_tile_gid);
            read_tile_baseline();
        }

        tick++;
    }

    /* ================================================================ */
    /*  Cleanup                                                          */
    /* ================================================================ */

    l3_disable_all();          /* freeze L3 so nothing runs away */
    if (g_pmu_ok) pmu_shutdown();

    if (out_fp != stdout) { fflush(out_fp); fclose(out_fp); }
    if (!g_quiet) fprintf(stderr, "[INFO] Done. %llu rows.\n", row_count);
    return 0;
}
