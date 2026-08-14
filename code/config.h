/* config.h - INI-style configuration for bf2-counter-collector.
 *
 * The config selects WHICH counters are collected and at WHICH
 * frequency (per-block intervals must be multiples of the global
 * interval).  config_set_defaults() reproduces the device-verified
 * 42-column baseline; configs/default.conf ships the same content
 * as an explicit file.
 *
 * ASCII only: the BlueField-2 gcc cannot handle UTF-8 in sources.
 */
#ifndef BF2_CONFIG_H
#define BF2_CONFIG_H

#include <stdio.h>

#include "catalog.h"

#define CFG_NAME_MAX      48   /* event / interface names */
#define CFG_PATH_MAX      512
#define CFG_ERR_MAX       512
#define CFG_GROUPS_MAX    8    /* tile rotation groups */
#define CFG_SLOTS_MAX     4    /* mechanism-1 programmable slots */
#define CFG_REGS_MAX      12   /* PCIe TLR statistics registers */
#define CFG_IFACES_MAX    8
#define CFG_L3_COLS_MAX   8
#define CFG_TILE_COLS_MAX 32
#define CFG_PCIE_UNITS_MAX 14

typedef struct {
    char name[CFG_NAME_MAX];    /* canonical UPPER_CASE event name */
    char colname[CFG_NAME_MAX]; /* CSV suffix, filled by resolve */
    int code;                   /* event code, filled by resolve */
} cfg_event_t;

typedef struct {
    char name[CFG_NAME_MAX];
    int ev0, ev1;               /* ev1 == -1: single event column */
} cfg_col_t;

typedef struct {
    char name[CFG_NAME_MAX];
    int mask;                   /* bit g = group g provides this column */
    int slot[CFG_GROUPS_MAX];   /* counter slot within each group */
} cfg_tile_col_t;

typedef struct {
    int enabled;
    int interval;               /* 0 = inherit global */
    int overridden;             /* an events/registers key was parsed */
    /* tile (rotating block) */
    int n_groups;
    int n_group_ev[CFG_GROUPS_MAX];
    cfg_event_t groups[CFG_GROUPS_MAX][CFG_SLOTS_MAX];
    /* non-rotating blocks */
    int n_events;
    cfg_event_t events[CFG_SLOTS_MAX];
    /* pcie mechanism-2 registers */
    int n_regs;
    cfg_event_t regs[CFG_REGS_MAX];
    /* net interface filter */
    int n_ifaces;
    char ifaces[CFG_IFACES_MAX][CFG_NAME_MAX];
    /* --- filled by config_resolve --- */
    int k;                      /* block interval / global interval */
    int l1_sel[4];              /* 1 = emit this L1 column */
    int rx_merged, tx_merged;   /* pcie: merge IN/OUT byte triple */
    int rx_idx[3], tx_idx[3];   /* pcie regs[] indices, -1 = absent */
    int n_l3_cols;
    cfg_col_t l3_cols[CFG_L3_COLS_MAX];
    int n_tile_cols;
    cfg_tile_col_t tile_cols[CFG_TILE_COLS_MAX];
} cfg_block_t;

typedef struct {
    int interval;                /* global tick, seconds (>= 1) */
    int duration;                /* 0 = forever */
    char output[CFG_PATH_MAX];   /* empty = stdout */
    cfg_block_t tile, tilenet, trio, smmu, triogen, l3cache,
                pcie, l1, cpu, mem, net, gic;
} bf2_config_t;

/* Device discovery results (which blocks are present). */
typedef struct {
    int n_tiles, n_tilenets, n_trios, n_smmus, n_triogens,
        n_l3halves, n_pcies, n_gics;
    int have_tile, have_tilenet, have_trio, have_smmu, have_triogen,
        have_l3, have_pcie, have_l1, have_cpu, have_mem, have_net, have_gic;
} bf2_presence_t;

/* Fill cfg with the device-verified default baseline. */
void config_set_defaults(bf2_config_t *cfg);

/* Parse an INI file.  Returns 0 on success, -1 on error (errbuf
 * filled with "file:line: message").  CRLF, UTF-8 BOM, inline '#'
 * comments and case-insensitive keys/values are handled. */
int config_parse_file(const char *path, bf2_config_t *cfg,
                      char *errbuf, size_t errsz);

/* Validate and resolve: event names -> codes, k factors, merged
 * columns, tile masks.  Returns 0 or -1 with errbuf message. */
int config_resolve(bf2_config_t *cfg, char *errbuf, size_t errsz);

/* Write the default.conf template (used by --dump-config). */
int config_dump_template(FILE *fp);

/* Write the resolved configuration (used by --check-config). */
int config_dump_resolved(FILE *fp, const bf2_config_t *cfg);

/* Write the CSV header for the resolved config and discovery results. */
int config_write_csv_header(FILE *fp, const bf2_config_t *cfg,
                            const bf2_presence_t *p);

/* PCIe column order per block: kind 0 = merged rx column, 1 = single
 * register column (regs[idx]), 2 = merged tx column.  Returns the
 * number of units. */
int config_pcie_units(const bf2_config_t *cfg, int kind[],
                      int idx[], int max_units);

/* CSV column suffix for one PCIe unit ("rx_bytes", "in_p_pkt_cnt"...). */
void config_pcie_unit_name(const bf2_config_t *cfg, int kind, int idx,
                           char *buf, size_t bufsz);

/* Canonical L1 event names in PMU output order. */
#define CFG_L1_CANON                                                       \
    "L1D_ACCESS", "L1D_MISS", "L1I_ACCESS", "L1I_MISS"

#endif /* BF2_CONFIG_H */
