/* catalog.c - event catalog for BlueField-2 performance counters.
 *
 * Event codes come from the official "BlueField-2 Performance
 * Monitoring Counters" documentation.  Reserved codes are omitted.
 * colname overrides reproduce the verified 42-column CSV layout of
 * collect_all.c (e.g. TDMA_DATA_BEAT -> dma_beats).
 *
 * ASCII only: the BlueField-2 gcc cannot handle UTF-8 in sources.
 */
#include <string.h>
#include <strings.h>

#include "catalog.h"

/* ------------------------------------------------------------------ */
/* Tile HNF (21 events, non-reserved)                                  */
/* ------------------------------------------------------------------ */
static const catalog_event_t tile_events[] = {
    { "HNF_REQUESTS",        NULL,           0x45 },
    { "MEMORY_READS",        "mem_reads",    0x4c },
    { "MEMORY_WRITES",       "mem_writes",   0x4d },
    { "VICTIM_WRITE",        NULL,           0x4e },
    { "POC_FAIL",            NULL,           0x50 },
    { "POC_SUCCESS",         NULL,           0x51 },
    { "POC_WRITES",          NULL,           0x52 },
    { "POC_READS",           NULL,           0x53 },
    { "A72_ACCESS",          NULL,           0x5d },
    { "IO_ACCESS",           NULL,           0x5e },
    { "TSO_WRITE",           NULL,           0x5f },
    { "DIR_HIT",             NULL,           0x61 },
    { "REQ_BUF_EMPTY",       NULL,           0x63 },
    { "MSS_NO_CREDIT",       "mss_nocredit", 0x67 },
    { "MEMORY_READS_BYPASS", NULL,           0x6d },
    { "ALLOCATE",            NULL,           0x6f },
    { "VICTIM",              NULL,           0x70 },
    { "A72_WRITE",           NULL,           0x71 },
    { "A72_READ",            NULL,           0x72 },  /* doc: A72_Read  */
    { "IO_WRITE",            NULL,           0x73 },
    { "IO_READS",            NULL,           0x74 },  /* doc: IO_Reads  */
    { NULL, NULL, 0 }
};

/* ------------------------------------------------------------------ */
/* tilenet: 3 core REQ events + 48 generated DIAG events               */
/* ------------------------------------------------------------------ */
#define TN_NETS  3
#define TN_DIAGS 48
#define TN_TOTAL (3 + TN_DIAGS)

static char s_tn_names[TN_TOTAL][CATALOG_NAME_MAX];
static catalog_event_t s_tn_events[TN_TOTAL + 1];

/* Doc layout per network (type-major), base 0x15 + 0x10 * net:
 *   base + 0..4   N/S/E/W/C _OUT_OF_CRED
 *   base + 5..9   N/S/E/W/C _EGRESS
 *   base + 10..14 N/S/E/W/C _INGRESS
 *   base + 15     CORE_SENT
 */
static void build_tn_events(void)
{
    static const char *nets[TN_NETS]  = { "CDN", "DDN", "NDN" };
    static const char *dirs[5]        = { "N", "S", "E", "W", "C" };
    static const char *types[3]       = { "OUT_OF_CRED", "EGRESS",
                                          "INGRESS" };
    static const char *req_names[3]   = { "CDN_REQ", "DDN_REQ", "NDN_REQ" };
    static const unsigned int req_codes[3] = { 0x12, 0x13, 0x14 };
    int e = 0, n, t, d;

    if (s_tn_events[0].name != NULL)
        return;  /* already built */

    for (n = 0; n < 3; n++) {
        snprintf(s_tn_names[e], CATALOG_NAME_MAX, "%s",
                 req_names[n]);
        s_tn_events[e].name = s_tn_names[e];
        s_tn_events[e].code = req_codes[n];
        e++;
    }
    for (n = 0; n < TN_NETS; n++) {
        for (t = 0; t < 3; t++) {
            for (d = 0; d < 5; d++) {
                snprintf(s_tn_names[e], CATALOG_NAME_MAX, "%s_DIAG_%s_%s",
                         nets[n], dirs[d], types[t]);
                s_tn_events[e].name = s_tn_names[e];
                s_tn_events[e].code = 0x15u + 0x10u * n + 5u * t + d;
                e++;
            }
        }
        snprintf(s_tn_names[e], CATALOG_NAME_MAX, "%s_DIAG_CORE_SENT",
                 nets[n]);
        s_tn_events[e].name = s_tn_names[e];
        s_tn_events[e].code = 0x24u + 0x10u * n;
        e++;
    }
    s_tn_events[e].name = NULL;
}

/* ------------------------------------------------------------------ */
/* TRIO (16 events, non-reserved; 0xa2/a3/a6/a7 reserved)              */
/* ------------------------------------------------------------------ */
static const catalog_event_t trio_events[] = {
    { "TPIO_DATA_BEAT",          NULL,         0xa0 },
    { "TDMA_DATA_BEAT",          "dma_beats",  0xa1 },
    { "TPIO_DATA_PACKET",        NULL,         0xa4 },
    { "TDMA_DATA_PACKET",        NULL,         0xa5 },
    { "TDMA_RT_AF",              "rt_af",      0xa8 },
    { "TDMA_PBUF_MAC_AF",        "pbuf_af",    0xa9 },
    { "TRIO_MAP_WRQ_BUF_EMPTY",  "wrq_empty",  0xaa },
    { "TRIO_MAP_CPL_BUF_EMPTY",  NULL,         0xab },
    { "TRIO_MAP_RDQ0_BUF_EMPTY", NULL,         0xac },
    { "TRIO_MAP_RDQ1_BUF_EMPTY", NULL,         0xad },
    { "TRIO_MAP_RDQ2_BUF_EMPTY", NULL,         0xae },
    { "TRIO_MAP_RDQ3_BUF_EMPTY", NULL,         0xaf },
    { "TRIO_MAP_RDQ4_BUF_EMPTY", NULL,         0xb0 },
    { "TRIO_MAP_RDQ5_BUF_EMPTY", NULL,         0xb1 },
    { "TRIO_MAP_RDQ6_BUF_EMPTY", NULL,         0xb2 },
    { "TRIO_MAP_RDQ7_BUF_EMPTY", NULL,         0xb3 },
    { NULL, NULL, 0 }
};

/* ------------------------------------------------------------------ */
/* SMGEN events, shared by smmu and gic blocks                         */
/* ------------------------------------------------------------------ */
static const catalog_event_t smgen_events[] = {
    { "TBU_MISS",  NULL, 0x0e },
    { "TX_DAT_AF", NULL, 0x0f },
    { "RX_DAT_AF", NULL, 0x10 },
    { NULL, NULL, 0 }
};

/* ------------------------------------------------------------------ */
/* triogen (fixed: triogen0 = TX_DAT_AF, triogen1 = RX_DAT_AF)         */
/* ------------------------------------------------------------------ */
static const catalog_event_t triogen_events[] = {
    { "TX_DAT_AF", NULL, 0x0f },
    { "RX_DAT_AF", NULL, 0x10 },
    { NULL, NULL, 0 }
};

/* ------------------------------------------------------------------ */
/* L3 cache (29 events, non-reserved)                                  */
/* ------------------------------------------------------------------ */
static const catalog_event_t l3cache_events[] = {
    { "TOTAL_RD_REQ_IN",             NULL, 0x02 },
    { "TOTAL_WR_REQ_IN",             NULL, 0x03 },
    { "TOTAL_WR_DBID_ACK",           NULL, 0x04 },
    { "TOTAL_WR_DATA_IN",            NULL, 0x05 },
    { "TOTAL_WR_COMP",               NULL, 0x06 },
    { "TOTAL_RD_DATA_OUT",           NULL, 0x07 },
    { "TOTAL_CDN_REQ_IN_BANK0",      NULL, 0x08 },
    { "TOTAL_CDN_REQ_IN_BANK1",      NULL, 0x09 },
    { "TOTAL_DDN_REQ_IN_BANK0",      NULL, 0x0a },
    { "TOTAL_DDN_REQ_IN_BANK1",      NULL, 0x0b },
    { "TOTAL_EMEM_RD_RES_IN_BANK0",  NULL, 0x0c },
    { "TOTAL_EMEM_RD_RES_IN_BANK1",  NULL, 0x0d },
    { "TOTAL_CACHE_RD_RES_IN_BANK0", NULL, 0x0e },
    { "TOTAL_CACHE_RD_RES_IN_BANK1", NULL, 0x0f },
    { "TOTAL_EMEM_RD_REQ_BANK0",     NULL, 0x10 },
    { "TOTAL_EMEM_RD_REQ_BANK1",     NULL, 0x11 },
    { "TOTAL_EMEM_WR_REQ_BANK0",     NULL, 0x12 },
    { "TOTAL_EMEM_WR_REQ_BANK1",     NULL, 0x13 },
    { "TOTAL_RD_REQ_OUT",            NULL, 0x14 },
    { "TOTAL_WR_REQ_OUT",            NULL, 0x15 },
    { "TOTAL_RD_RES_IN",             NULL, 0x16 },
    { "HITS_BANK0",                  NULL, 0x17 },
    { "HITS_BANK1",                  NULL, 0x18 },
    { "MISSES_BANK0",                NULL, 0x19 },
    { "MISSES_BANK1",                NULL, 0x1a },
    { "ALLOCATIONS_BANK0",           NULL, 0x1b },
    { "ALLOCATIONS_BANK1",           NULL, 0x1c },
    { "EVICTIONS_BANK0",             NULL, 0x1d },
    { "EVICTIONS_BANK1",             NULL, 0x1e },
    { NULL, NULL, 0 }
};

/* ------------------------------------------------------------------ */
/* PCIe TLR statistics registers (mechanism 2, sysfs file names)       */
/* ------------------------------------------------------------------ */
static const catalog_event_t pcie_events[] = {
    { "IN_P_BYTE_CNT",  NULL, 0 },
    { "IN_NP_BYTE_CNT", NULL, 0 },
    { "IN_C_BYTE_CNT",  NULL, 0 },
    { "OUT_P_BYTE_CNT", NULL, 0 },
    { "OUT_NP_BYTE_CNT", NULL, 0 },
    { "OUT_C_BYTE_CNT", NULL, 0 },
    { "IN_P_PKT_CNT",   NULL, 0 },
    { "IN_NP_PKT_CNT",  NULL, 0 },
    { "IN_C_PKT_CNT",   NULL, 0 },
    { "OUT_P_PKT_CNT",  NULL, 0 },
    { "OUT_NP_PKT_CNT", NULL, 0 },
    { "OUT_C_PKT_CNT",  NULL, 0 },
    { NULL, NULL, 0 }
};

/* ------------------------------------------------------------------ */
/* ARM PMU L1 events (fixed; codes informational)                      */
/* ------------------------------------------------------------------ */
static const catalog_event_t l1_events[] = {
    { "L1D_ACCESS", NULL, 0x40 },
    { "L1D_MISS",   NULL, 0x42 },
    { "L1I_ACCESS", NULL, 0x14 },
    { "L1I_MISS",   NULL, 0x01 },
    { NULL, NULL, 0 }
};

/* ------------------------------------------------------------------ */
/* Block table                                                         */
/* ------------------------------------------------------------------ */
catalog_block_t g_catalog[] = {
    { "tile",     "tile",        4, tile_events },
    { "tilenet",  "tilenet",     3, s_tn_events },  /* slots 0-2 verified */
    { "trio",     "trio",        4, trio_events },
    { "smmu",     "smmu",        3, smgen_events },
    { "triogen",  "triogen",     0, triogen_events },  /* fixed, not configurable */
    { "l3cache",  "l3cachehalf", 4, l3cache_events },
    { "pcie",     "pcie",        0, pcie_events },     /* mechanism 2 registers */
    { "l1",       "",            0, l1_events },       /* ARM PMU, fixed */
    { "gic",      "gic",         4, smgen_events },    /* 3 valid SMGEN events */
};
const int g_catalog_n = (int)(sizeof(g_catalog) / sizeof(g_catalog[0]));

/* ------------------------------------------------------------------ */
/* Lookups                                                             */
/* ------------------------------------------------------------------ */
void catalog_init(void)
{
    build_tn_events();
}

const catalog_block_t *catalog_find_block(const char *name)
{
    int i;
    catalog_init();
    for (i = 0; i < g_catalog_n; i++) {
        if (strcasecmp(g_catalog[i].block, name) == 0)
            return &g_catalog[i];
    }
    return NULL;
}

const catalog_event_t *catalog_find_event(const char *block,
                                          const char *name)
{
    const catalog_block_t *b = catalog_find_block(block);
    const catalog_event_t *ev;

    if (b == NULL)
        return NULL;
    for (ev = b->events; ev->name != NULL; ev++) {
        if (strcasecmp(ev->name, name) == 0)
            return ev;
    }
    return NULL;
}

const char *catalog_colname(const catalog_event_t *ev)
{
    static char buf[CATALOG_NAME_MAX];
    size_t i;

    if (ev->colname != NULL)
        return ev->colname;
    for (i = 0; ev->name[i] != '\0' && i < CATALOG_NAME_MAX - 1; i++) {
        char c = ev->name[i];
        buf[i] = (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c;
    }
    buf[i] = '\0';
    return buf;
}

/* ------------------------------------------------------------------ */
/* --list-events                                                      */
/* ------------------------------------------------------------------ */
static int list_block(FILE *fp, const catalog_block_t *b)
{
    const catalog_event_t *ev;
    int n;

    fprintf(fp, "[%s]  %s  %d programmable slot%s\n", b->block,
            b->dir_prefix, b->max_slots,
            b->max_slots == 1 ? "" : "s");
    n = 0;
    for (ev = b->events; ev->name != NULL; ev++) {
        if (ev->code != 0)
            fprintf(fp, "  0x%02x  %-24s -> %s_%s\n", ev->code, ev->name,
                    b->block, catalog_colname(ev));
        else
            fprintf(fp, "  reg    %-24s -> %s_%s\n", ev->name,
                    b->block, catalog_colname(ev));
        n++;
    }
    fprintf(fp, "  (%d events)\n", n);
    return n;
}

int catalog_list_events(FILE *fp)
{
    int i, total = 0;

    catalog_init();
    fprintf(fp, "BlueField-2 performance counter events (PMC doc):\n");
    for (i = 0; i < g_catalog_n; i++)
        total += list_block(fp, &g_catalog[i]);
    fprintf(fp, "\nSoftware blocks (no hardware events):\n");
    fprintf(fp, "[cpu]  /proc/stat        cpu_util_pct cpu_min_pct "
            "cpu_max_pct cpu_avg_pct\n");
    fprintf(fp, "[mem]  /proc/meminfo     mem_total_kb mem_used_kb "
            "mem_util_pct\n");
    fprintf(fp, "[net]  /sys/class/net    net_rx_bytes net_tx_bytes\n");
    fprintf(fp, "\nTotal hardware events: %d\n", total);
    return 0;
}
