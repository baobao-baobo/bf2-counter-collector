/* test_config.c - unit tests for catalog.c and config.c.
 *
 * Runs on the HOST (x86 WSL), not on the BlueField-2.  The parity
 * gate at the end of test_defaults renders the CSV header of the
 * built-in default configuration and requires it to be byte-identical
 * to the device-verified header (all_test.txt line 2).
 *
 * ASCII only: the BlueField-2 gcc cannot handle UTF-8 in sources.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "catalog.h"
#include "config.h"

static int n_fail = 0;
static int n_pass = 0;

#define CHECK(cond) do {                                                \
    if (cond) { n_pass++; }                                             \
    else { printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);       \
           n_fail++; }                                                  \
} while (0)

#define CHECK_STR(a, b) do {                                            \
    const char *va_ = (a), *vb_ = (b);                                  \
    if (va_ != NULL && vb_ != NULL && strcmp(va_, vb_) == 0) { n_pass++; } \
    else { printf("FAIL %s:%d:\n  got:      %s\n  expected: %s\n",      \
                  __FILE__, __LINE__, va_ ? va_ : "(null)",             \
                  vb_ ? vb_ : "(null)"); n_fail++; }                    \
} while (0)

static void write_file(const char *path, const char *content)
{
    FILE *fp = fopen(path, "wb");
    if (fp == NULL) {
        printf("FATAL: cannot write %s\n", path);
        exit(1);
    }
    fputs(content, fp);
    fclose(fp);
}

/* parse + resolve an inline config; returns -1 (errbuf filled) or 0 */
static int parse_resolve(const char *content, bf2_config_t *cfg,
                         char *errbuf, size_t errsz)
{
    const char *path = "test_tmp.conf";

    errbuf[0] = '\0';
    config_set_defaults(cfg);
    write_file(path, content);
    if (config_parse_file(path, cfg, errbuf, errsz) != 0)
        return -1;
    return config_resolve(cfg, errbuf, errsz);
}

/* render header into a malloc'ed string (caller frees) */
static char *render_header(const bf2_config_t *cfg)
{
    static bf2_presence_t p;
    FILE *fp = tmpfile();
    char *buf;
    long sz;

    memset(&p, 0, sizeof(p));
    p.have_tile = 1;     p.n_tiles = 4;
    p.have_tilenet = 1;  p.n_tilenets = 4;
    p.have_trio = 1;     p.n_trios = 2;
    p.have_smmu = 1;     p.n_smmus = 1;
    p.have_triogen = 1;  p.n_triogens = 2;
    p.have_l3 = 1;       p.n_l3halves = 2;
    p.have_pcie = 1;     p.n_pcies = 2;
    p.have_l1 = 1;
    p.have_cpu = 1;
    p.have_mem = 1;
    p.have_net = 1;
    p.have_gic = 1;      p.n_gics = 1;

    if (fp == NULL)
        return NULL;
    config_write_csv_header(fp, cfg, &p);
    sz = ftell(fp);
    buf = malloc((size_t)sz + 1);
    if (buf == NULL)
        return NULL;
    rewind(fp);
    if (fread(buf, 1, (size_t)sz, fp) != (size_t)sz)
        return NULL;
    buf[sz] = '\0';
    fclose(fp);
    return buf;
}

/* ------------------------------------------------------------------ */
/* Tests                                                               */
/* ------------------------------------------------------------------ */
static const char *parity_line =
    "timestamp,tile_group,"
    "tile_a72_access,tile_mem_reads,tile_mem_writes,tile_mss_nocredit,"
    "tile_dir_hit,tile_allocate,tile_victim_write,"
    "tilenet_cdn_req,tilenet_ddn_req,tilenet_ndn_req,"
    "trio_dma_beats,trio_rt_af,trio_pbuf_af,trio_wrq_empty,"
    "smmu_tbu_miss,smmu_tx_dat_af,smmu_rx_dat_af,"
    "triogen_tx_dat_af,triogen_rx_dat_af,"
    "l3half0_hits,l3half0_misses,l3half1_hits,l3half1_misses,"
    "pcie0_rx_bytes,pcie0_tx_bytes,pcie1_rx_bytes,pcie1_tx_bytes,"
    "l1d_access,l1d_miss,l1i_access,l1i_miss,"
    "cpu_util_pct,cpu_min_pct,cpu_max_pct,cpu_avg_pct,"
    "mem_total_kb,mem_used_kb,mem_util_pct,"
    "net_rx_bytes,net_tx_bytes\n";

static void test_defaults_resolve(void)
{
    bf2_config_t cfg;
    char err[CFG_ERR_MAX];

    config_set_defaults(&cfg);
    CHECK(config_resolve(&cfg, err, sizeof(err)) == 0);

    /* tile: 2 groups x 4 slots, 7 unique columns, masks */
    CHECK(cfg.tile.n_groups == 2);
    CHECK(cfg.tile.n_group_ev[0] == 4 && cfg.tile.n_group_ev[1] == 4);
    CHECK(cfg.tile.n_tile_cols == 7);
    CHECK(strcmp(cfg.tile.tile_cols[0].name, "a72_access") == 0);
    CHECK(cfg.tile.tile_cols[0].mask == 3);
    CHECK(cfg.tile.tile_cols[0].slot[0] == 0);
    CHECK(cfg.tile.tile_cols[0].slot[1] == 0);
    CHECK(strcmp(cfg.tile.tile_cols[1].name, "mem_reads") == 0);
    CHECK(cfg.tile.tile_cols[1].mask == 1);
    CHECK(strcmp(cfg.tile.tile_cols[4].name, "dir_hit") == 0);
    CHECK(cfg.tile.tile_cols[4].mask == 2);
    CHECK(cfg.tile.tile_cols[4].slot[1] == 1);
    CHECK(cfg.tile.groups[0][0].code == 0x5d);
    CHECK(cfg.tile.groups[0][1].code == 0x4c);
    CHECK(cfg.tile.groups[0][2].code == 0x4d);
    CHECK(cfg.tile.groups[0][3].code == 0x67);
    CHECK(cfg.tile.groups[1][1].code == 0x61);
    CHECK(cfg.tile.groups[1][2].code == 0x6f);
    CHECK(cfg.tile.groups[1][3].code == 0x4e);

    /* flat blocks */
    CHECK(cfg.tilenet.n_events == 3);
    CHECK(cfg.tilenet.events[0].code == 0x12);
    CHECK(cfg.trio.n_events == 4);
    CHECK(cfg.trio.events[0].code == 0xa1);
    CHECK(strcmp(cfg.trio.events[0].colname, "dma_beats") == 0);
    CHECK(cfg.trio.events[1].code == 0xa8);
    CHECK(cfg.trio.events[3].code == 0xaa);
    CHECK(strcmp(cfg.trio.events[3].colname, "wrq_empty") == 0);
    CHECK(cfg.smmu.n_events == 3);

    /* l3 pairs + pcie merge + l1 all selected */
    CHECK(cfg.l3cache.n_l3_cols == 2);
    CHECK(strcmp(cfg.l3cache.l3_cols[0].name, "hits") == 0);
    CHECK(cfg.l3cache.l3_cols[0].ev0 == 0 && cfg.l3cache.l3_cols[0].ev1 == 1);
    CHECK(strcmp(cfg.l3cache.l3_cols[1].name, "misses") == 0);
    CHECK(cfg.pcie.rx_merged == 1 && cfg.pcie.tx_merged == 1);
    CHECK(cfg.pcie.rx_idx[0] == 0 && cfg.pcie.rx_idx[1] == 1 &&
          cfg.pcie.rx_idx[2] == 2);
    CHECK(cfg.l1.l1_sel[0] == 1 && cfg.l1.l1_sel[1] == 1 &&
          cfg.l1.l1_sel[2] == 1 && cfg.l1.l1_sel[3] == 1);

    /* k factors all 1 */
    CHECK(cfg.tile.k == 1 && cfg.tilenet.k == 1 && cfg.pcie.k == 1 &&
          cfg.cpu.k == 1);

    /* PARITY GATE: header byte-identical to the verified all_test line */
    {
        char *hdr = render_header(&cfg);
        CHECK(hdr != NULL);
        if (hdr != NULL) {
            CHECK_STR(hdr, parity_line);
            free(hdr);
        }
    }
}

static void test_catalog_spots(void)
{
    const catalog_event_t *ev;

    catalog_init();
    ev = catalog_find_event("tile", "A72_ACCESS");
    CHECK(ev != NULL && ev->code == 0x5d);
    ev = catalog_find_event("tile", "IO_READS");
    CHECK(ev != NULL && ev->code == 0x74);
    ev = catalog_find_event("tile", "A72_READ");
    CHECK(ev != NULL && ev->code == 0x72);

    /* tilenet DIAG: type-major layout from the PMC doc */
    ev = catalog_find_event("tilenet", "CDN_REQ");
    CHECK(ev != NULL && ev->code == 0x12);
    ev = catalog_find_event("tilenet", "CDN_DIAG_N_OUT_OF_CRED");
    CHECK(ev != NULL && ev->code == 0x15);
    ev = catalog_find_event("tilenet", "CDN_DIAG_S_OUT_OF_CRED");
    CHECK(ev != NULL && ev->code == 0x16);
    ev = catalog_find_event("tilenet", "CDN_DIAG_C_OUT_OF_CRED");
    CHECK(ev != NULL && ev->code == 0x19);
    ev = catalog_find_event("tilenet", "CDN_DIAG_C_EGRESS");
    CHECK(ev != NULL && ev->code == 0x1e);
    ev = catalog_find_event("tilenet", "CDN_DIAG_C_INGRESS");
    CHECK(ev != NULL && ev->code == 0x23);
    ev = catalog_find_event("tilenet", "CDN_DIAG_CORE_SENT");
    CHECK(ev != NULL && ev->code == 0x24);
    ev = catalog_find_event("tilenet", "DDN_DIAG_N_OUT_OF_CRED");
    CHECK(ev != NULL && ev->code == 0x25);
    ev = catalog_find_event("tilenet", "DDN_DIAG_CORE_SENT");
    CHECK(ev != NULL && ev->code == 0x34);
    ev = catalog_find_event("tilenet", "NDN_DIAG_C_INGRESS");
    CHECK(ev != NULL && ev->code == 0x43);
    ev = catalog_find_event("tilenet", "NDN_DIAG_CORE_SENT");
    CHECK(ev != NULL && ev->code == 0x44);

    /* trio / l3 / pcie / case-insensitivity / colnames */
    ev = catalog_find_event("trio", "TRIO_MAP_RDQ7_BUF_EMPTY");
    CHECK(ev != NULL && ev->code == 0xb3);
    ev = catalog_find_event("l3cache", "TOTAL_EMEM_WR_REQ_BANK1");
    CHECK(ev != NULL && ev->code == 0x13);
    ev = catalog_find_event("pcie", "IN_C_PKT_CNT");
    CHECK(ev != NULL);
    ev = catalog_find_event("TILE", "a72_access");   /* case-insens */
    CHECK(ev != NULL && ev->code == 0x5d);
    ev = catalog_find_event("tile", "MEMORY_READS");
    CHECK(ev != NULL && strcmp(catalog_colname(ev), "mem_reads") == 0);
    ev = catalog_find_event("tile", "MSS_NO_CREDIT");
    CHECK(ev != NULL && strcmp(catalog_colname(ev), "mss_nocredit") == 0);
    ev = catalog_find_event("trio", "TDMA_RT_AF");
    CHECK(ev != NULL && strcmp(catalog_colname(ev), "rt_af") == 0);
    ev = catalog_find_event("tile", "NOT_AN_EVENT");
    CHECK(ev == NULL);
}

static void test_template_roundtrip(void)
{
    bf2_config_t cfg;
    char err[CFG_ERR_MAX];
    FILE *fp = tmpfile();
    char *tpl;
    long sz;
    char *hdr;

    config_dump_template(fp);
    sz = ftell(fp);
    tpl = malloc((size_t)sz + 1);
    rewind(fp);
    CHECK(fread(tpl, 1, (size_t)sz, fp) == (size_t)sz);
    tpl[sz] = '\0';
    fclose(fp);

    CHECK(parse_resolve(tpl, &cfg, err, sizeof(err)) == 0);
    hdr = render_header(&cfg);
    CHECK(hdr != NULL);
    if (hdr != NULL) {
        CHECK_STR(hdr, parity_line);
        free(hdr);
    }
    free(tpl);
}

static void test_multirate(void)
{
    bf2_config_t cfg;
    char err[CFG_ERR_MAX];

    /* all inherit */
    CHECK(parse_resolve("[global]\ninterval = 2\n", &cfg,
                        err, sizeof(err)) == 0);
    CHECK(cfg.tilenet.k == 1 && cfg.tile.k == 1);

    /* per-block multiple */
    CHECK(parse_resolve("[global]\ninterval = 2\n"
                        "[tilenet]\ninterval = 10\n", &cfg,
                        err, sizeof(err)) == 0);
    CHECK(cfg.tilenet.k == 5 && cfg.tile.k == 1);

    /* not a multiple -> error */
    CHECK(parse_resolve("[global]\ninterval = 2\n"
                        "[tilenet]\ninterval = 3\n", &cfg,
                        err, sizeof(err)) == -1);

    /* global interval 1, block 2 */
    CHECK(parse_resolve("[l3cache]\ninterval = 2\n", &cfg,
                        err, sizeof(err)) == 0);
    CHECK(cfg.l3cache.k == 2 && cfg.tile.k == 1);

    /* negative block interval -> error */
    CHECK(parse_resolve("[l3cache]\ninterval = -1\n", &cfg,
                        err, sizeof(err)) == -1);

    /* disabled block with non-multiple interval is fine */
    CHECK(parse_resolve("[global]\ninterval = 2\n"
                        "[tilenet]\nenabled = false\ninterval = 3\n",
                        &cfg, err, sizeof(err)) == 0);
    CHECK(cfg.tilenet.k == 1);   /* unused, clamped to inherited */
}

static void test_errors(void)
{
    bf2_config_t cfg;
    char err[CFG_ERR_MAX];

    CHECK(parse_resolve("[tilenet]\nevents = FOO_BAR, CDN_REQ\n", &cfg,
                        err, sizeof(err)) == -1);
    CHECK(strstr(err, "FOO_BAR") != NULL);

    CHECK(parse_resolve("[bogus]\nenabled = true\n", &cfg,
                        err, sizeof(err)) == -1);
    CHECK(strstr(err, "unknown section") != NULL);

    CHECK(parse_resolve("[trio]\neventz = CDN_REQ\n", &cfg,
                        err, sizeof(err)) == -1);
    CHECK(strstr(err, "unknown key") != NULL);

    CHECK(parse_resolve("[trio]\nenabled = true\nenabled = true\n", &cfg,
                        err, sizeof(err)) == -1);
    CHECK(strstr(err, "duplicate key") != NULL);

    CHECK(parse_resolve("[trio]\nenabled = maybe\n", &cfg,
                        err, sizeof(err)) == -1);
    CHECK(strstr(err, "bad boolean") != NULL);

    CHECK(parse_resolve("[global]\ninterval 1\n", &cfg,
                        err, sizeof(err)) == -1);
    CHECK(strstr(err, "expected key") != NULL);

    CHECK(parse_resolve("[trio]\nevents = TDMA_DATA_BEAT, TDMA_RT_AF, "
                        "TDMA_PBUF_MAC_AF, TRIO_MAP_WRQ_BUF_EMPTY, "
                        "TPIO_DATA_BEAT\n", &cfg,
                        err, sizeof(err)) == -1);

    CHECK(parse_resolve("[global]\ninterval = 0\n", &cfg,
                        err, sizeof(err)) == -1);

    CHECK(parse_resolve("[global]\nduration = -5\n", &cfg,
                        err, sizeof(err)) == -1);

    /* duplicate section */
    CHECK(parse_resolve("[global]\n[global]\n", &cfg,
                        err, sizeof(err)) == -1);

    /* duplicate event in flat list */
    CHECK(parse_resolve("[tilenet]\nevents = CDN_REQ, CDN_REQ\n", &cfg,
                        err, sizeof(err)) == -1);
    CHECK(strstr(err, "duplicate event") != NULL);
}

static void test_case_insensitive(void)
{
    bf2_config_t cfg;
    char err[CFG_ERR_MAX];
    char *hdr;

    CHECK(parse_resolve("[Global]\nInterval = 1\n"
                        "[TILENET]\nEnabled = TRUE\n"
                        "Events = cdn_req, ddn_req, NDN_REQ\n", &cfg,
                        err, sizeof(err)) == 0);
    CHECK(cfg.tilenet.events[0].code == 0x12);
    CHECK(cfg.tilenet.events[2].code == 0x14);
    CHECK(cfg.tilenet.enabled == 1);
    /* only tilenet was overridden; tile defaults intact */
    CHECK(cfg.tile.n_groups == 2);
    hdr = render_header(&cfg);
    CHECK(hdr != NULL);
    if (hdr != NULL) {
        CHECK_STR(hdr, parity_line);
        free(hdr);
    }
}

static void test_crlf_bom(void)
{
    bf2_config_t cfg;
    char err[CFG_ERR_MAX];
    const char *path = "test_bom.conf";
    char *hdr;

    /* BOM + CRLF + comments + camelCase event names */
    write_file(path,
               "\xEF\xBB\xBF# comment\r\n"
               "[global]\r\n"
               "interval = 1 ; trailing comment\r\n"
               "[tilenet]\r\n"
               "events = cdn_req, DDN_REQ, ndn_req\r\n");
    err[0] = '\0';
    config_set_defaults(&cfg);
    CHECK(config_parse_file(path, &cfg, err, sizeof(err)) == 0);
    CHECK(config_resolve(&cfg, err, sizeof(err)) == 0);
    CHECK(cfg.tilenet.n_events == 3);
    hdr = render_header(&cfg);
    CHECK(hdr != NULL);
    if (hdr != NULL) {
        CHECK_STR(hdr, parity_line);
        free(hdr);
    }
    remove(path);
}

static void test_replacement(void)
{
    bf2_config_t cfg;
    char err[CFG_ERR_MAX];
    char *hdr;

    /* only group0 -> single group rotation */
    CHECK(parse_resolve("[tile]\ngroup0 = A72_ACCESS, DIR_HIT\n", &cfg,
                        err, sizeof(err)) == 0);
    CHECK(cfg.tile.n_groups == 1);
    CHECK(cfg.tile.n_group_ev[0] == 2);
    CHECK(cfg.tile.n_tile_cols == 2);
    CHECK(cfg.tile.tile_cols[0].mask == 1);
    CHECK(cfg.tile.tile_cols[1].mask == 1);
    hdr = render_header(&cfg);
    CHECK(hdr != NULL);
    if (hdr != NULL) {
        /* tile_group + 2 columns; mem_* gone */
        CHECK(strstr(hdr, "tile_group,tile_a72_access,tile_dir_hit") !=
              NULL);
        CHECK(strstr(hdr, "tile_mem_reads") == NULL);
        free(hdr);
    }

    /* events= replaces defaults */
    CHECK(parse_resolve("[tilenet]\nevents = CDN_REQ, NDN_REQ\n", &cfg,
                        err, sizeof(err)) == 0);
    CHECK(cfg.tilenet.n_events == 2);
    CHECK(cfg.tilenet.events[0].code == 0x12);
    CHECK(cfg.tilenet.events[1].code == 0x14);

    /* two events keys -> second replaces (duplicate key = error) */
    CHECK(parse_resolve("[tilenet]\nevents = CDN_REQ\n"
                        "events = NDN_REQ\n", &cfg,
                        err, sizeof(err)) == -1);
}

static void test_pcie_merge(void)
{
    bf2_config_t cfg;
    char err[CFG_ERR_MAX];
    int kind[CFG_PCIE_UNITS_MAX], idx[CFG_PCIE_UNITS_MAX], nu;
    char *hdr;

    /* drop IN_C -> rx not merged, per-reg columns */
    CHECK(parse_resolve("[pcie]\nregisters = IN_P_BYTE_CNT, "
                        "IN_NP_BYTE_CNT, OUT_P_BYTE_CNT, OUT_NP_BYTE_CNT, "
                        "OUT_C_BYTE_CNT\n", &cfg,
                        err, sizeof(err)) == 0);
    CHECK(cfg.pcie.rx_merged == 0 && cfg.pcie.tx_merged == 1);
    nu = config_pcie_units(&cfg, kind, idx, CFG_PCIE_UNITS_MAX);
    CHECK(nu == 3);   /* in_p, in_np, tx_bytes */
    CHECK(kind[0] == 1 && kind[1] == 1 && kind[2] == 2);
    hdr = render_header(&cfg);
    CHECK(hdr != NULL);
    if (hdr != NULL) {
        CHECK(strstr(hdr, "pcie0_in_p_byte_cnt") != NULL);
        CHECK(strstr(hdr, "pcie0_in_np_byte_cnt") != NULL);
        CHECK(strstr(hdr, "pcie0_tx_bytes") != NULL);
        CHECK(strstr(hdr, "pcie0_rx_bytes") == NULL);
        free(hdr);
    }

    /* PKT registers never merge */
    CHECK(parse_resolve("[pcie]\nregisters = IN_P_BYTE_CNT, "
                        "IN_NP_BYTE_CNT, IN_C_BYTE_CNT, OUT_P_BYTE_CNT, "
                        "OUT_NP_BYTE_CNT, OUT_C_BYTE_CNT, IN_P_PKT_CNT\n",
                        &cfg, err, sizeof(err)) == 0);
    CHECK(cfg.pcie.rx_merged == 1 && cfg.pcie.tx_merged == 1);
    nu = config_pcie_units(&cfg, kind, idx, CFG_PCIE_UNITS_MAX);
    CHECK(nu == 3);   /* rx_bytes, in_p_pkt, tx_bytes */
    CHECK(kind[0] == 0 && kind[1] == 1 && kind[2] == 2);
    hdr = render_header(&cfg);
    CHECK(hdr != NULL);
    if (hdr != NULL) {
        CHECK(strstr(hdr, "pcie0_rx_bytes,pcie0_in_p_pkt_cnt,"
                       "pcie0_tx_bytes") != NULL);
        free(hdr);
    }
}

static void test_l3_pair_rule(void)
{
    bf2_config_t cfg;
    char err[CFG_ERR_MAX];
    char *hdr;

    /* only bank0 -> single column with default name */
    CHECK(parse_resolve("[l3cache]\nevents = HITS_BANK0\n", &cfg,
                        err, sizeof(err)) == 0);
    CHECK(cfg.l3cache.n_l3_cols == 1);
    CHECK(strcmp(cfg.l3cache.l3_cols[0].name, "hits_bank0") == 0);
    CHECK(cfg.l3cache.l3_cols[0].ev1 == -1);
    hdr = render_header(&cfg);
    CHECK(hdr != NULL);
    if (hdr != NULL) {
        CHECK(strstr(hdr, "l3half0_hits_bank0,l3half1_hits_bank0") !=
              NULL);
        CHECK(strstr(hdr, "l3half0_hits,") == NULL);
        free(hdr);
    }

    /* reversed pair order still merges into "hits" */
    CHECK(parse_resolve("[l3cache]\nevents = HITS_BANK1, HITS_BANK0\n",
                        &cfg, err, sizeof(err)) == 0);
    CHECK(cfg.l3cache.n_l3_cols == 1);
    CHECK(strcmp(cfg.l3cache.l3_cols[0].name, "hits") == 0);
    CHECK(cfg.l3cache.l3_cols[0].ev0 == 1);
    CHECK(cfg.l3cache.l3_cols[0].ev1 == 0);

    /* one bank + misses pair */
    CHECK(parse_resolve("[l3cache]\nevents = HITS_BANK0, MISSES_BANK0, "
                        "MISSES_BANK1\n", &cfg,
                        err, sizeof(err)) == 0);
    CHECK(cfg.l3cache.n_l3_cols == 2);
    CHECK(strcmp(cfg.l3cache.l3_cols[0].name, "hits_bank0") == 0);
    CHECK(strcmp(cfg.l3cache.l3_cols[1].name, "misses") == 0);
}

static void test_l1_subset(void)
{
    bf2_config_t cfg;
    char err[CFG_ERR_MAX];
    char *hdr;

    CHECK(parse_resolve("[l1]\nevents = L1D_ACCESS, L1I_MISS\n", &cfg,
                        err, sizeof(err)) == 0);
    CHECK(cfg.l1.l1_sel[0] == 1 && cfg.l1.l1_sel[1] == 0 &&
          cfg.l1.l1_sel[2] == 0 && cfg.l1.l1_sel[3] == 1);
    hdr = render_header(&cfg);
    CHECK(hdr != NULL);
    if (hdr != NULL) {
        CHECK(strstr(hdr, "l1d_access,l1i_miss") != NULL);
        CHECK(strstr(hdr, "l1d_miss,") == NULL);
        free(hdr);
    }

    /* unknown L1 event */
    CHECK(parse_resolve("[l1]\nevents = L1D_ACCESS, L2D_ACCESS\n", &cfg,
                        err, sizeof(err)) == -1);
}

static void test_tile_errors(void)
{
    bf2_config_t cfg;
    char err[CFG_ERR_MAX];

    /* unequal group sizes */
    CHECK(parse_resolve("[tile]\ngroup0 = A72_ACCESS, DIR_HIT\n"
                        "group1 = A72_ACCESS, DIR_HIT, ALLOCATE\n", &cfg,
                        err, sizeof(err)) == -1);
    CHECK(strstr(err, "equal") != NULL);

    /* gap: group0 + group2 */
    CHECK(parse_resolve("[tile]\ngroup0 = A72_ACCESS, DIR_HIT\n"
                        "group2 = A72_ACCESS, DIR_HIT\n", &cfg,
                        err, sizeof(err)) == -1);
    CHECK(strstr(err, "contiguous") != NULL);

    /* duplicate within group */
    CHECK(parse_resolve("[tile]\ngroup0 = A72_ACCESS, A72_ACCESS\n",
                        &cfg, err, sizeof(err)) == -1);
    CHECK(strstr(err, "duplicate event") != NULL);

    /* group too large */
    CHECK(parse_resolve("[tile]\ngroup0 = A72_ACCESS, DIR_HIT, "
                        "ALLOCATE, VICTIM_WRITE, IO_ACCESS\n", &cfg,
                        err, sizeof(err)) == -1);

    /* no groups at all */
    CHECK(parse_resolve("[tile]\ngroup0 = A72_ACCESS\n", &cfg,
                        err, sizeof(err)) == 0);
    CHECK(cfg.tile.n_groups == 1);
}

static void test_net_ifaces(void)
{
    bf2_config_t cfg;
    char err[CFG_ERR_MAX];

    CHECK(parse_resolve("[net]\ninterfaces = eth0, enp3s0f1s0\n", &cfg,
                        err, sizeof(err)) == 0);
    CHECK(cfg.net.n_ifaces == 2);
    CHECK(strcmp(cfg.net.ifaces[0], "eth0") == 0);
    CHECK(strcmp(cfg.net.ifaces[1], "enp3s0f1s0") == 0);

    /* too long iface name */
    CHECK(parse_resolve("[net]\ninterfaces = abcdefghijklmnop\n", &cfg,
                        err, sizeof(err)) == -1);
}

int main(void)
{
    test_defaults_resolve();
    test_catalog_spots();
    test_template_roundtrip();
    test_multirate();
    test_errors();
    test_case_insensitive();
    test_crlf_bom();
    test_replacement();
    test_pcie_merge();
    test_l3_pair_rule();
    test_l1_subset();
    test_tile_errors();
    test_net_ifaces();

    remove("test_tmp.conf");   /* don't leave the scratch file behind */

    printf("%d passed, %d failed\n", n_pass, n_fail);
    return n_fail == 0 ? 0 : 1;
}
