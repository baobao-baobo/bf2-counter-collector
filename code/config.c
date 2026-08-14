/* config.c - INI-style configuration for bf2-counter-collector.
 *
 * Hand-written zero-dependency INI parser (libc only): CRLF, UTF-8
 * BOM, inline '#' / ';' comments, case-insensitive keys and event
 * names.  config_resolve() validates everything and fills derived
 * fields (event codes, k factors, merged PCIe/L3 columns, tile group
 * masks) so the engine only reads resolved data.
 *
 * ASCII only: the BlueField-2 gcc cannot handle UTF-8 in sources.
 */
#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "config.h"

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */
static void trim(char *s)
{
    char *p = s;
    size_t n;

    while (isspace((unsigned char)*p))
        p++;
    n = strlen(p);
    while (n > 0 && isspace((unsigned char)p[n - 1]))
        p[--n] = '\0';
    if (p != s)
        memmove(s, p, n + 1);
}

static void canon_name(char *s)
{
    for (; *s != '\0'; s++) {
        if (*s >= 'a' && *s <= 'z')
            *s = (char)(*s - 'a' + 'A');
    }
}

/* Copy into a fixed-size field; avoids -Wrestrict false positives on
 * fields of the same struct. */
static void copy_name(char *dst, const char *src)
{
    size_t n = strlen(src);

    if (n >= CFG_NAME_MAX)
        n = CFG_NAME_MAX - 1;
    memcpy(dst, src, n);
    dst[n] = '\0';
}

static int parse_int(const char *s, int *out)
{
    char *end;
    long v = strtol(s, &end, 10);

    if (*s == '\0' || *end != '\0')
        return -1;
    if (v > 2147483647L || v < -2147483648L)
        return -1;
    *out = (int)v;
    return 0;
}

static int parse_bool(const char *s, int *out)
{
    if (strcasecmp(s, "true") == 0 || strcasecmp(s, "yes") == 0 ||
        strcasecmp(s, "on") == 0 || strcmp(s, "1") == 0) {
        *out = 1;
        return 0;
    }
    if (strcasecmp(s, "false") == 0 || strcasecmp(s, "no") == 0 ||
        strcasecmp(s, "off") == 0 || strcmp(s, "0") == 0) {
        *out = 0;
        return 0;
    }
    return -1;
}

/* Split a comma-separated list into canonical upper-case event names. */
static int parse_event_list(const char *v, cfg_event_t *dst, int max,
                            const char *where, char *errbuf, size_t errsz)
{
    char buf[1024];
    char *tok, *save = NULL;
    int n = 0;

    if (strlen(v) >= sizeof(buf)) {
        snprintf(errbuf, errsz, "%s: list too long", where);
        return -1;
    }
    snprintf(buf, sizeof(buf), "%s", v);
    for (tok = strtok_r(buf, ",", &save); tok != NULL;
         tok = strtok_r(NULL, ",", &save)) {
        trim(tok);
        if (*tok == '\0') {
            snprintf(errbuf, errsz, "%s: empty list entry", where);
            return -1;
        }
        if (n >= max) {
            snprintf(errbuf, errsz, "%s: too many entries (max %d)",
                     where, max);
            return -1;
        }
        canon_name(tok);
        if (strlen(tok) >= CFG_NAME_MAX) {
            snprintf(errbuf, errsz, "%s: name too long: '%s'",
                     where, tok);
            return -1;
        }
        snprintf(dst[n].name, CFG_NAME_MAX, "%s", tok);
        dst[n].code = 0;
        dst[n].colname[0] = '\0';
        n++;
    }
    return n;
}

/* Split a comma-separated list of interface names (case kept). */
static int parse_iface_list(const char *v, char ifaces[][CFG_NAME_MAX],
                            int max, const char *where, char *errbuf,
                            size_t errsz)
{
    char buf[1024];
    char *tok, *save = NULL;
    int n = 0;

    if (strlen(v) >= sizeof(buf)) {
        snprintf(errbuf, errsz, "%s: list too long", where);
        return -1;
    }
    snprintf(buf, sizeof(buf), "%s", v);
    for (tok = strtok_r(buf, ",", &save); tok != NULL;
         tok = strtok_r(NULL, ",", &save)) {
        trim(tok);
        if (*tok == '\0') {
            snprintf(errbuf, errsz, "%s: empty list entry", where);
            return -1;
        }
        if (n >= max) {
            snprintf(errbuf, errsz, "%s: too many interfaces (max %d)",
                     where, max);
            return -1;
        }
        if (strlen(tok) >= 16) {  /* IFNAMSIZ - 1 on Linux */
            snprintf(errbuf, errsz,
                     "%s: interface name too long (max 15): '%s'",
                     where, tok);
            return -1;
        }
        snprintf(ifaces[n], CFG_NAME_MAX, "%s", tok);
        n++;
    }
    return n;
}

/* ------------------------------------------------------------------ */
/* Defaults: the device-verified 42-column baseline                    */
/* ------------------------------------------------------------------ */
static void block_init(cfg_block_t *b)
{
    memset(b, 0, sizeof(*b));
    b->enabled = 1;
}

static void ev_set(cfg_event_t *dst, const char *name)
{
    snprintf(dst->name, CFG_NAME_MAX, "%s", name);
    dst->code = 0;
    dst->colname[0] = '\0';
}

void config_set_defaults(bf2_config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->interval = 1;
    cfg->duration = 0;
    cfg->output[0] = '\0';

    block_init(&cfg->tile);
    block_init(&cfg->tilenet);
    block_init(&cfg->trio);
    block_init(&cfg->smmu);
    block_init(&cfg->triogen);
    block_init(&cfg->l3cache);
    block_init(&cfg->pcie);
    block_init(&cfg->l1);
    block_init(&cfg->cpu);
    block_init(&cfg->mem);
    block_init(&cfg->net);
    block_init(&cfg->gic);
    cfg->gic.enabled = 0;

    /* tile rotation groups (A72_ACCESS in both = 100% coverage) */
    cfg->tile.n_groups = 2;
    cfg->tile.n_group_ev[0] = 4;
    cfg->tile.n_group_ev[1] = 4;
    ev_set(&cfg->tile.groups[0][0], "A72_ACCESS");
    ev_set(&cfg->tile.groups[0][1], "MEMORY_READS");
    ev_set(&cfg->tile.groups[0][2], "MEMORY_WRITES");
    ev_set(&cfg->tile.groups[0][3], "MSS_NO_CREDIT");
    ev_set(&cfg->tile.groups[1][0], "A72_ACCESS");
    ev_set(&cfg->tile.groups[1][1], "DIR_HIT");
    ev_set(&cfg->tile.groups[1][2], "ALLOCATE");
    ev_set(&cfg->tile.groups[1][3], "VICTIM_WRITE");

    cfg->tilenet.n_events = 3;
    ev_set(&cfg->tilenet.events[0], "CDN_REQ");
    ev_set(&cfg->tilenet.events[1], "DDN_REQ");
    ev_set(&cfg->tilenet.events[2], "NDN_REQ");

    cfg->trio.n_events = 4;
    ev_set(&cfg->trio.events[0], "TDMA_DATA_BEAT");
    ev_set(&cfg->trio.events[1], "TDMA_RT_AF");
    ev_set(&cfg->trio.events[2], "TDMA_PBUF_MAC_AF");
    ev_set(&cfg->trio.events[3], "TRIO_MAP_WRQ_BUF_EMPTY");

    cfg->smmu.n_events = 3;
    ev_set(&cfg->smmu.events[0], "TBU_MISS");
    ev_set(&cfg->smmu.events[1], "TX_DAT_AF");
    ev_set(&cfg->smmu.events[2], "RX_DAT_AF");

    cfg->l3cache.n_events = 4;
    ev_set(&cfg->l3cache.events[0], "HITS_BANK0");
    ev_set(&cfg->l3cache.events[1], "HITS_BANK1");
    ev_set(&cfg->l3cache.events[2], "MISSES_BANK0");
    ev_set(&cfg->l3cache.events[3], "MISSES_BANK1");

    cfg->pcie.n_regs = 6;
    ev_set(&cfg->pcie.regs[0], "IN_P_BYTE_CNT");
    ev_set(&cfg->pcie.regs[1], "IN_NP_BYTE_CNT");
    ev_set(&cfg->pcie.regs[2], "IN_C_BYTE_CNT");
    ev_set(&cfg->pcie.regs[3], "OUT_P_BYTE_CNT");
    ev_set(&cfg->pcie.regs[4], "OUT_NP_BYTE_CNT");
    ev_set(&cfg->pcie.regs[5], "OUT_C_BYTE_CNT");

    cfg->l1.n_events = 4;
    ev_set(&cfg->l1.events[0], "L1D_ACCESS");
    ev_set(&cfg->l1.events[1], "L1D_MISS");
    ev_set(&cfg->l1.events[2], "L1I_ACCESS");
    ev_set(&cfg->l1.events[3], "L1I_MISS");
}

/* ------------------------------------------------------------------ */
/* Parser                                                              */
/* ------------------------------------------------------------------ */
#define SEC_GLOBAL  0
#define SEC_TILE    1
#define SEC_TILENET 2
#define SEC_TRIO    3
#define SEC_SMMU    4
#define SEC_TRIOGEN 5
#define SEC_L3      6
#define SEC_PCIE    7
#define SEC_L1      8
#define SEC_CPU     9
#define SEC_MEM     10
#define SEC_NET     11
#define SEC_GIC     12
#define SEC_COUNT   13

/* key ids for duplicate detection: 0 enabled, 1 interval, 2 events,
 * 3 registers, 4 interfaces, 5 output, 6 duration, 8+g groupN */
#define KEY_ENABLED    0
#define KEY_INTERVAL   1
#define KEY_EVENTS     2
#define KEY_REGISTERS  3
#define KEY_INTERFACES 4
#define KEY_OUTPUT     5
#define KEY_DURATION   6
#define KEY_GROUP0     8

static const char *sec_names[SEC_COUNT] = {
    "global", "tile", "tilenet", "trio", "smmu", "triogen",
    "l3cache", "pcie", "l1", "cpu", "mem", "net", "gic"
};

static int sec_index(const char *name)
{
    int i;
    for (i = 0; i < SEC_COUNT; i++) {
        if (strcasecmp(name, sec_names[i]) == 0)
            return i;
    }
    return -1;
}

static cfg_block_t *block_of(bf2_config_t *cfg, int sec)
{
    switch (sec) {
    case SEC_TILE:    return &cfg->tile;
    case SEC_TILENET: return &cfg->tilenet;
    case SEC_TRIO:    return &cfg->trio;
    case SEC_SMMU:    return &cfg->smmu;
    case SEC_TRIOGEN: return &cfg->triogen;
    case SEC_L3:      return &cfg->l3cache;
    case SEC_PCIE:    return &cfg->pcie;
    case SEC_L1:      return &cfg->l1;
    case SEC_CPU:     return &cfg->cpu;
    case SEC_MEM:     return &cfg->mem;
    case SEC_NET:     return &cfg->net;
    case SEC_GIC:     return &cfg->gic;
    default:          return NULL;
    }
}

int config_parse_file(const char *path, bf2_config_t *cfg,
                      char *errbuf, size_t errsz)
{
    FILE *fp;
    char line[1024];
    int lineno = 0;
    int first_line = 1;
    int cur_sec = -1;          /* -1 = before first section */
    int seen_sec = 0;
    int seen_key[16];
    int ret = 0;

    fp = fopen(path, "r");
    if (fp == NULL) {
        snprintf(errbuf, errsz, "%s: cannot open", path);
        return -1;
    }

    memset(seen_key, 0, sizeof(seen_key));
    while (fgets(line, sizeof(line), fp) != NULL) {
        char *key, *val, *comment;
        char where[640];
        int kid = -1;

        lineno++;
        {
            size_t len = strlen(line);
            if (len == sizeof(line) - 1 && line[len - 1] != '\n') {
                snprintf(errbuf, errsz, "%s:%d: line too long",
                         path, lineno);
                ret = -1;
                break;
            }
        }
        if (first_line) {
            first_line = 0;
            if (line[0] == '\xEF' && line[1] == '\xBB' &&
                line[2] == '\xBF')
                memmove(line, line + 3, strlen(line + 3) + 1);
        }
        /* strip CR, then inline comment */
        trim(line);
        comment = strpbrk(line, "#;");
        if (comment != NULL)
            *comment = '\0';
        trim(line);
        if (line[0] == '\0')
            continue;

        if (line[0] == '[') {
            char *close = strchr(line, ']');
            int sec;

            if (close == NULL) {
                snprintf(errbuf, errsz, "%s:%d: missing ']'",
                         path, lineno);
                ret = -1;
                break;
            }
            *close = '\0';
            trim(line + 1);
            sec = sec_index(line + 1);
            if (sec < 0) {
                snprintf(errbuf, errsz,
                         "%s:%d: unknown section [%s]", path,
                         lineno, line + 1);
                ret = -1;
                break;
            }
            if (seen_sec & (1 << sec)) {
                snprintf(errbuf, errsz,
                         "%s:%d: duplicate section [%s]", path,
                         lineno, line + 1);
                ret = -1;
                break;
            }
            seen_sec |= 1 << sec;
            memset(seen_key, 0, sizeof(seen_key));
            cur_sec = sec;
            continue;
        }

        if (cur_sec < 0) {
            snprintf(errbuf, errsz,
                     "%s:%d: key '%s' outside any section",
                     path, lineno, line);
            ret = -1;
            break;
        }

        val = strchr(line, '=');
        if (val == NULL) {
            snprintf(errbuf, errsz, "%s:%d: expected key = value, got '%s'",
                     path, lineno, line);
            ret = -1;
            break;
        }
        *val = '\0';
        key = line;
        trim(key);
        val++;
        trim(val);
        snprintf(where, sizeof(where), "%s:%d", path, lineno);

        /* identify the key */
        if (strcasecmp(key, "enabled") == 0) {
            kid = KEY_ENABLED;
        } else if (strcasecmp(key, "interval") == 0) {
            kid = KEY_INTERVAL;
        } else if (strcasecmp(key, "events") == 0) {
            kid = KEY_EVENTS;
        } else if (strcasecmp(key, "registers") == 0) {
            kid = KEY_REGISTERS;
        } else if (strcasecmp(key, "interfaces") == 0) {
            kid = KEY_INTERFACES;
        } else if (strcasecmp(key, "output") == 0) {
            kid = KEY_OUTPUT;
        } else if (strcasecmp(key, "duration") == 0) {
            kid = KEY_DURATION;
        } else if (strncasecmp(key, "group", 5) == 0 &&
                   isdigit((unsigned char)key[5]) && key[6] == '\0') {
            int g = key[5] - '0';
            kid = KEY_GROUP0 + g;
            if (g >= CFG_GROUPS_MAX)
                kid = -1;
        }
        if (kid < 0) {
            snprintf(errbuf, errsz,
                     "%s:%d: unknown key '%s' in [%s]", path,
                     lineno, key, sec_names[cur_sec]);
            ret = -1;
            break;
        }

        /* dispatch */
        if (cur_sec == SEC_GLOBAL) {
            if (kid == KEY_OUTPUT) {
                if (strlen(val) >= CFG_PATH_MAX) {
                    snprintf(errbuf, errsz, "%s:%d: output path too long",
                             path, lineno);
                    ret = -1;
                    break;
                }
                snprintf(cfg->output, CFG_PATH_MAX, "%s", val);
            } else if (kid == KEY_INTERVAL) {
                if (parse_int(val, &cfg->interval) != 0) {
                    snprintf(errbuf, errsz, "%s:%d: bad integer '%s'",
                             path, lineno, val);
                    ret = -1;
                    break;
                }
            } else if (kid == KEY_DURATION) {
                if (parse_int(val, &cfg->duration) != 0) {
                    snprintf(errbuf, errsz, "%s:%d: bad integer '%s'",
                             path, lineno, val);
                    ret = -1;
                    break;
                }
            } else {
                snprintf(errbuf, errsz,
                         "%s:%d: unknown key '%s' in [global]",
                         path, lineno, key);
                ret = -1;
                break;
            }
        } else {
            cfg_block_t *b = block_of(cfg, cur_sec);
            int n;

            /* per-section key whitelist */
            if (kid == KEY_ENABLED || kid == KEY_INTERVAL) {
                /* common to all blocks */
            } else if (kid >= KEY_GROUP0 &&
                       cur_sec == SEC_TILE) {
                /* ok */
            } else if (kid == KEY_EVENTS && (cur_sec == SEC_TILENET ||
                       cur_sec == SEC_TRIO || cur_sec == SEC_SMMU ||
                       cur_sec == SEC_L3 || cur_sec == SEC_L1 ||
                       cur_sec == SEC_GIC)) {
                /* ok */
            } else if (kid == KEY_REGISTERS && cur_sec == SEC_PCIE) {
                /* ok */
            } else if (kid == KEY_INTERFACES && cur_sec == SEC_NET) {
                /* ok */
            } else {
                snprintf(errbuf, errsz,
                         "%s:%d: unknown key '%s' in [%s]",
                         path, lineno, key, sec_names[cur_sec]);
                ret = -1;
                break;
            }
            if (seen_key[kid]) {
                snprintf(errbuf, errsz,
                         "%s:%d: duplicate key '%s' in [%s]",
                         path, lineno, key, sec_names[cur_sec]);
                ret = -1;
                break;
            }
            seen_key[kid] = 1;

            if (kid == KEY_ENABLED) {
                if (parse_bool(val, &b->enabled) != 0) {
                    snprintf(errbuf, errsz, "%s:%d: bad boolean '%s'",
                             path, lineno, val);
                    ret = -1;
                    break;
                }
            } else if (kid == KEY_INTERVAL) {
                if (parse_int(val, &b->interval) != 0) {
                    snprintf(errbuf, errsz, "%s:%d: bad integer '%s'",
                             path, lineno, val);
                    ret = -1;
                    break;
                }
            } else if (kid >= KEY_GROUP0) {
                int g = kid - KEY_GROUP0;

                if (!b->overridden) {  /* first groupN key clears defaults */
                    b->n_groups = 0;
                    memset(b->n_group_ev, 0, sizeof(b->n_group_ev));
                    b->overridden = 1;
                }
                n = parse_event_list(val, b->groups[g],
                                     CFG_SLOTS_MAX, where, errbuf, errsz);
                if (n < 0) {
                    ret = -1;
                    break;
                }
                b->n_group_ev[g] = n;
                if (g + 1 > b->n_groups)
                    b->n_groups = g + 1;
            } else if (kid == KEY_EVENTS) {
                if (!b->overridden) {  /* first events key clears defaults */
                    b->n_events = 0;
                    b->overridden = 1;
                }
                n = parse_event_list(val, b->events, CFG_SLOTS_MAX,
                                     where, errbuf, errsz);
                if (n < 0) {
                    ret = -1;
                    break;
                }
                b->n_events = n;
            } else if (kid == KEY_REGISTERS) {
                if (!b->overridden) {
                    b->n_regs = 0;
                    b->overridden = 1;
                }
                n = parse_event_list(val, b->regs, CFG_REGS_MAX,
                                     where, errbuf, errsz);
                if (n < 0) {
                    ret = -1;
                    break;
                }
                b->n_regs = n;
            } else if (kid == KEY_INTERFACES) {
                n = parse_iface_list(val, b->ifaces, CFG_IFACES_MAX,
                                     where, errbuf, errsz);
                if (n < 0) {
                    ret = -1;
                    break;
                }
                b->n_ifaces = n;
            }
        }
    }
    fclose(fp);
    return ret;
}

/* ------------------------------------------------------------------ */
/* Resolve: validate + fill codes, k, merged columns, tile masks       */
/* ------------------------------------------------------------------ */
static int find_ev(const cfg_event_t *evs, int n, const char *name)
{
    int i;
    for (i = 0; i < n; i++) {
        if (strcmp(evs[i].name, name) == 0)
            return i;
    }
    return -1;
}

static int resolve_list(const char *blk, cfg_event_t *evs, int n,
                        char *errbuf, size_t errsz)
{
    int i, j;

    for (i = 0; i < n; i++) {
        const catalog_event_t *ce = catalog_find_event(blk, evs[i].name);

        if (ce == NULL) {
            snprintf(errbuf, errsz,
                     "[%s] event '%s' not in catalog (see --list-events)",
                     blk, evs[i].name);
            return -1;
        }
        for (j = 0; j < i; j++) {
            if (strcmp(evs[j].name, evs[i].name) == 0) {
                snprintf(errbuf, errsz,
                         "[%s] duplicate event '%s'", blk, evs[i].name);
                return -1;
            }
        }
        evs[i].code = (int)ce->code;
        snprintf(evs[i].colname, CFG_NAME_MAX, "%s",
                 catalog_colname(ce));
    }
    return 0;
}

static int resolve_k(const char *blk, cfg_block_t *b, int ginterval,
                     char *errbuf, size_t errsz)
{
    if (b->interval == 0) {
        b->k = 1;
        return 0;
    }
    if (b->interval < 0) {
        snprintf(errbuf, errsz, "[%s] interval must be >= 0", blk);
        return -1;
    }
    if (b->enabled && b->interval % ginterval != 0) {
        snprintf(errbuf, errsz,
                 "[%s] interval %d must be a multiple of the global "
                 "interval %d", blk, b->interval, ginterval);
        return -1;
    }
    b->k = b->interval / ginterval;
    return 0;
}

static int resolve_block(const char *blk, cfg_block_t *b, int max_slots,
                         int ginterval, char *errbuf, size_t errsz)
{
    if (resolve_list(blk, b->events, b->n_events, errbuf, errsz) != 0)
        return -1;
    if (b->n_events > max_slots) {
        snprintf(errbuf, errsz, "[%s] at most %d events (got %d)",
                 blk, max_slots, b->n_events);
        return -1;
    }
    return resolve_k(blk, b, ginterval, errbuf, errsz);
}

static int resolve_tile(cfg_block_t *b, char *errbuf, size_t errsz)
{
    int g, s, i;
    int ncols = 0;

    for (g = 0; g < b->n_groups; g++) {
        if (b->n_group_ev[g] < 1) {
            snprintf(errbuf, errsz,
                     "[tile] groups must be contiguous: group%d is empty",
                     g);
            return -1;
        }
        if (b->n_group_ev[g] != b->n_group_ev[0]) {
            snprintf(errbuf, errsz,
                     "[tile] group sizes must be equal (group0 has %d, "
                     "group%d has %d)", b->n_group_ev[0], g,
                     b->n_group_ev[g]);
            return -1;
        }
        for (s = 0; s < b->n_group_ev[g]; s++) {
            cfg_event_t *ev = &b->groups[g][s];
            const catalog_event_t *ce =
                catalog_find_event("tile", ev->name);
            int j;

            if (ce == NULL) {
                snprintf(errbuf, errsz,
                         "[tile] event '%s' not in catalog "
                         "(see --list-events)", ev->name);
                return -1;
            }
            for (j = 0; j < s; j++) {
                if (strcmp(b->groups[g][j].name, ev->name) == 0) {
                    snprintf(errbuf, errsz,
                             "[tile] duplicate event '%s' in group%d",
                             ev->name, g);
                    return -1;
                }
            }
            ev->code = (int)ce->code;
            snprintf(ev->colname, CFG_NAME_MAX, "%s",
                     catalog_colname(ce));
        }
    }
    /* build column table: one column per unique event name */
    for (g = 0; g < b->n_groups; g++) {
        for (s = 0; s < b->n_group_ev[g]; s++) {
            const cfg_event_t *ev = &b->groups[g][s];
            cfg_tile_col_t *col = NULL;

            for (i = 0; i < ncols; i++) {
                if (strcmp(b->tile_cols[i].name, ev->colname) == 0) {
                    col = &b->tile_cols[i];
                    break;
                }
            }
            if (col == NULL) {
                if (ncols >= CFG_TILE_COLS_MAX) {
                    snprintf(errbuf, errsz,
                             "[tile] too many unique event names (max %d)",
                             CFG_TILE_COLS_MAX);
                    return -1;
                }
                col = &b->tile_cols[ncols++];
                copy_name(col->name, ev->colname);
                col->mask = 0;
                for (i = 0; i < CFG_GROUPS_MAX; i++)
                    col->slot[i] = -1;
            }
            col->mask |= 1 << g;
            col->slot[g] = s;
        }
    }
    b->n_tile_cols = ncols;
    return 0;
}

static void add_l3_col(cfg_block_t *b, int *ncols, const char *name,
                       int ev0, int ev1)
{
    cfg_col_t *c = &b->l3_cols[(*ncols)++];

    copy_name(c->name, name);
    c->ev0 = ev0;
    c->ev1 = ev1;
}

static void resolve_l3(cfg_block_t *b)
{
    int h0 = find_ev(b->events, b->n_events, "HITS_BANK0");
    int h1 = find_ev(b->events, b->n_events, "HITS_BANK1");
    int m0 = find_ev(b->events, b->n_events, "MISSES_BANK0");
    int m1 = find_ev(b->events, b->n_events, "MISSES_BANK1");
    int used[CFG_SLOTS_MAX] = { 0, 0, 0, 0 };
    int i;
    int ncols = 0;

    if (h0 >= 0 && h1 >= 0) {
        add_l3_col(b, &ncols, "hits", h0, h1);
        used[h0] = used[h1] = 1;
    } else if (h0 >= 0) {
        add_l3_col(b, &ncols, b->events[h0].colname, h0, -1);
        used[h0] = 1;
    } else if (h1 >= 0) {
        add_l3_col(b, &ncols, b->events[h1].colname, h1, -1);
        used[h1] = 1;
    }
    if (m0 >= 0 && m1 >= 0) {
        add_l3_col(b, &ncols, "misses", m0, m1);
        used[m0] = used[m1] = 1;
    } else if (m0 >= 0) {
        add_l3_col(b, &ncols, b->events[m0].colname, m0, -1);
        used[m0] = 1;
    } else if (m1 >= 0) {
        add_l3_col(b, &ncols, b->events[m1].colname, m1, -1);
        used[m1] = 1;
    }
    for (i = 0; i < b->n_events; i++) {
        if (!used[i])
            add_l3_col(b, &ncols, b->events[i].colname, i, -1);
    }
    b->n_l3_cols = ncols;
}

static void resolve_pcie(cfg_block_t *b)
{
    static const char *rx[3] = { "IN_P_BYTE_CNT", "IN_NP_BYTE_CNT",
                                 "IN_C_BYTE_CNT" };
    static const char *tx[3] = { "OUT_P_BYTE_CNT", "OUT_NP_BYTE_CNT",
                                 "OUT_C_BYTE_CNT" };
    int i;

    for (i = 0; i < 3; i++) {
        b->rx_idx[i] = find_ev(b->regs, b->n_regs, rx[i]);
        b->tx_idx[i] = find_ev(b->regs, b->n_regs, tx[i]);
    }
    b->rx_merged = b->rx_idx[0] >= 0 && b->rx_idx[1] >= 0 &&
                   b->rx_idx[2] >= 0;
    b->tx_merged = b->tx_idx[0] >= 0 && b->tx_idx[1] >= 0 &&
                   b->tx_idx[2] >= 0;
}

static void resolve_l1(cfg_block_t *b)
{
    static const char *canon[4] = { CFG_L1_CANON };
    int i;

    for (i = 0; i < 4; i++)
        b->l1_sel[i] = find_ev(b->events, b->n_events, canon[i]) >= 0;
}

int config_resolve(bf2_config_t *cfg, char *errbuf, size_t errsz)
{
    int g;

    if (cfg->interval < 1) {
        snprintf(errbuf, errsz,
                 "[global] interval must be >= 1 (got %d)", cfg->interval);
        return -1;
    }
    if (cfg->duration < 0) {
        snprintf(errbuf, errsz,
                 "[global] duration must be >= 0 (got %d)", cfg->duration);
        return -1;
    }
    if (cfg->tile.n_groups < 1) {
        snprintf(errbuf, errsz, "[tile] no groups defined");
        return -1;
    }
    if (cfg->tile.n_groups > CFG_GROUPS_MAX) {
        snprintf(errbuf, errsz, "[tile] at most %d groups (got %d)",
                 CFG_GROUPS_MAX, cfg->tile.n_groups);
        return -1;
    }
    for (g = 0; g < cfg->tile.n_groups; g++) {
        if (cfg->tile.n_group_ev[g] < 1) {
            snprintf(errbuf, errsz,
                     "[tile] groups must be contiguous: group%d is empty",
                     g);
            return -1;
        }
        if (cfg->tile.n_group_ev[g] > CFG_SLOTS_MAX) {
            snprintf(errbuf, errsz,
                     "[tile] group%d size must be 1..%d (got %d)",
                     g, CFG_SLOTS_MAX, cfg->tile.n_group_ev[g]);
            return -1;
        }
        if (cfg->tile.n_group_ev[g] != cfg->tile.n_group_ev[0]) {
            snprintf(errbuf, errsz,
                     "[tile] group sizes must be equal (group0 has %d, "
                     "group%d has %d)", cfg->tile.n_group_ev[0], g,
                     cfg->tile.n_group_ev[g]);
            return -1;
        }
    }

    if (resolve_k("tile", &cfg->tile, cfg->interval, errbuf, errsz) != 0)
        return -1;
    if (resolve_tile(&cfg->tile, errbuf, errsz) != 0)
        return -1;

    if (resolve_block("tilenet", &cfg->tilenet, 3, cfg->interval,
                      errbuf, errsz) != 0)
        return -1;
    if (resolve_block("trio", &cfg->trio, 4, cfg->interval,
                      errbuf, errsz) != 0)
        return -1;
    if (resolve_block("smmu", &cfg->smmu, 3, cfg->interval,
                      errbuf, errsz) != 0)
        return -1;
    if (resolve_block("l3cache", &cfg->l3cache, 4, cfg->interval,
                      errbuf, errsz) != 0)
        return -1;
    if (resolve_list("pcie", cfg->pcie.regs, cfg->pcie.n_regs,
                     errbuf, errsz) != 0)
        return -1;
    if (cfg->pcie.n_regs > CFG_REGS_MAX) {
        snprintf(errbuf, errsz, "[pcie] at most %d registers (got %d)",
                 CFG_REGS_MAX, cfg->pcie.n_regs);
        return -1;
    }
    if (resolve_k("pcie", &cfg->pcie, cfg->interval, errbuf, errsz) != 0)
        return -1;
    if (resolve_block("l1", &cfg->l1, 4, cfg->interval,
                      errbuf, errsz) != 0)
        return -1;
    if (resolve_block("gic", &cfg->gic, 4, cfg->interval,
                      errbuf, errsz) != 0)
        return -1;
    /* triogen / cpu / mem / net have no event lists */
    if (resolve_k("triogen", &cfg->triogen, cfg->interval,
                  errbuf, errsz) != 0)
        return -1;
    if (resolve_k("cpu", &cfg->cpu, cfg->interval, errbuf, errsz) != 0)
        return -1;
    if (resolve_k("mem", &cfg->mem, cfg->interval, errbuf, errsz) != 0)
        return -1;
    if (resolve_k("net", &cfg->net, cfg->interval, errbuf, errsz) != 0)
        return -1;

    resolve_l3(&cfg->l3cache);
    resolve_pcie(&cfg->pcie);
    resolve_l1(&cfg->l1);
    return 0;
}

/* ------------------------------------------------------------------ */
/* Dumps                                                               */
/* ------------------------------------------------------------------ */
int config_dump_template(FILE *fp)
{
    fprintf(fp,
        "# bf2-counter-collector - default configuration.\n"
        "# Reproduces the device-verified 42-column CSV layout\n"
        "# (BlueField-2 with 2 L3 halves, 2 PCIe blocks, 8 cores).\n"
        "#\n"
        "# Rules:\n"
        "# - [global] interval: base tick in seconds, one CSV row per\n"
        "#   tick.\n"
        "# - Each block may set its own interval; it must be a multiple\n"
        "#   of the global interval (0 = inherit).  Rows where a block\n"
        "#   is not sampled leave its columns empty (NaN).\n"
        "# - The first `groupN` key in [tile] replaces ALL default\n"
        "#   groups.  The first `events`/`registers` key in a block\n"
        "#   replaces that block's default list.\n"
        "# - Event names are case-insensitive.  Run --list-events for\n"
        "#   the full catalog.\n"
        "\n"
        "[global]\n"
        "interval = 1\n"
        "duration = 0\n"
        "output   = collector.csv\n"
        "\n"
        "[tile]\n"
        "enabled  = true\n"
        "interval = 1\n"
        "group0   = A72_ACCESS, MEMORY_READS, MEMORY_WRITES, MSS_NO_CREDIT\n"
        "group1   = A72_ACCESS, DIR_HIT, ALLOCATE, VICTIM_WRITE\n"
        "\n"
        "[tilenet]\n"
        "enabled  = true\n"
        "events   = CDN_REQ, DDN_REQ, NDN_REQ\n"
        "\n"
        "[trio]\n"
        "enabled  = true\n"
        "events   = TDMA_DATA_BEAT, TDMA_RT_AF, TDMA_PBUF_MAC_AF, "
        "TRIO_MAP_WRQ_BUF_EMPTY\n"
        "\n"
        "[smmu]\n"
        "enabled  = true\n"
        "events   = TBU_MISS, TX_DAT_AF, RX_DAT_AF\n"
        "\n"
        "[triogen]\n"
        "enabled  = true\n"
        "\n"
        "[l3cache]\n"
        "enabled  = true\n"
        "events   = HITS_BANK0, HITS_BANK1, MISSES_BANK0, MISSES_BANK1\n"
        "\n"
        "[pcie]\n"
        "enabled  = true\n"
        "registers = IN_P_BYTE_CNT, IN_NP_BYTE_CNT, IN_C_BYTE_CNT, "
        "OUT_P_BYTE_CNT, OUT_NP_BYTE_CNT, OUT_C_BYTE_CNT\n"
        "\n"
        "[l1]\n"
        "enabled  = true\n"
        "events   = L1D_ACCESS, L1D_MISS, L1I_ACCESS, L1I_MISS\n"
        "\n"
        "[cpu]\n"
        "enabled  = true\n"
        "\n"
        "[mem]\n"
        "enabled  = true\n"
        "\n"
        "[net]\n"
        "enabled  = true\n"
        "# interfaces = eth0, eth1   # optional filter; empty = all\n"
        "\n"
        "[gic]\n"
        "enabled  = false\n");
    return 0;
}

static void dump_block(FILE *fp, const char *name, const cfg_block_t *b)
{
    fprintf(fp, "[%s] enabled=%s", name, b->enabled ? "true" : "false");
    if (b->interval > 0)
        fprintf(fp, " interval=%ds (k=%d)", b->interval, b->k);
    else
        fprintf(fp, " interval=inherit (k=%d)", b->k);
    fprintf(fp, "\n");
}

static void dump_event_list(FILE *fp, const char *indent,
                            const char *what, const cfg_event_t *evs,
                            int n)
{
    int i;

    fprintf(fp, "%s%s (%d):", indent, what, n);
    for (i = 0; i < n; i++)
        fprintf(fp, " %s(0x%02x)", evs[i].name, evs[i].code);
    fprintf(fp, "\n");
}

int config_dump_resolved(FILE *fp, const bf2_config_t *cfg)
{
    int i, g;

    fprintf(fp, "[global] interval=%ds duration=%ds output=%s\n",
            cfg->interval, cfg->duration,
            cfg->output[0] != '\0' ? cfg->output : "(stdout)");

    dump_block(fp, "tile", &cfg->tile);
    for (g = 0; g < cfg->tile.n_groups; g++) {
        char lbl[24];
        snprintf(lbl, sizeof(lbl), "group%d", g);
        dump_event_list(fp, "  ", lbl, cfg->tile.groups[g],
                        cfg->tile.n_group_ev[g]);
    }
    fprintf(fp, "  columns (%d):\n", cfg->tile.n_tile_cols);
    for (i = 0; i < cfg->tile.n_tile_cols; i++) {
        const cfg_tile_col_t *c = &cfg->tile.tile_cols[i];

        fprintf(fp, "    tile_%s  groups:", c->name);
        for (g = 0; g < cfg->tile.n_groups; g++) {
            if (c->mask & (1 << g))
                fprintf(fp, " %d@slot%d", g, c->slot[g]);
        }
        fprintf(fp, "\n");
    }

    dump_block(fp, "tilenet", &cfg->tilenet);
    dump_event_list(fp, "  ", "events", cfg->tilenet.events,
                    cfg->tilenet.n_events);
    dump_block(fp, "trio", &cfg->trio);
    dump_event_list(fp, "  ", "events", cfg->trio.events,
                    cfg->trio.n_events);
    dump_block(fp, "smmu", &cfg->smmu);
    dump_event_list(fp, "  ", "events", cfg->smmu.events,
                    cfg->smmu.n_events);
    dump_block(fp, "triogen", &cfg->triogen);
    fprintf(fp, "  fixed: triogen0=TX_DAT_AF triogen1=RX_DAT_AF\n");
    dump_block(fp, "l3cache", &cfg->l3cache);
    dump_event_list(fp, "  ", "events", cfg->l3cache.events,
                    cfg->l3cache.n_events);
    fprintf(fp, "  columns:");
    for (i = 0; i < cfg->l3cache.n_l3_cols; i++)
        fprintf(fp, " %s(ev%d%s)", cfg->l3cache.l3_cols[i].name,
                cfg->l3cache.l3_cols[i].ev0,
                cfg->l3cache.l3_cols[i].ev1 >= 0 ? "+ev1" : "");
    fprintf(fp, "\n");
    dump_block(fp, "pcie", &cfg->pcie);
    dump_event_list(fp, "  ", "registers", cfg->pcie.regs,
                    cfg->pcie.n_regs);
    fprintf(fp, "  rx_merged=%d tx_merged=%d\n", cfg->pcie.rx_merged,
            cfg->pcie.tx_merged);
    dump_block(fp, "l1", &cfg->l1);
    dump_event_list(fp, "  ", "events", cfg->l1.events,
                    cfg->l1.n_events);
    fprintf(fp, "  selected:");
    for (i = 0; i < 4; i++)
        fprintf(fp, " %d", cfg->l1.l1_sel[i]);
    fprintf(fp, "\n");
    dump_block(fp, "cpu", &cfg->cpu);
    dump_block(fp, "mem", &cfg->mem);
    dump_block(fp, "net", &cfg->net);
    if (cfg->net.n_ifaces > 0) {
        fprintf(fp, "  interfaces:");
        for (i = 0; i < cfg->net.n_ifaces; i++)
            fprintf(fp, " %s", cfg->net.ifaces[i]);
        fprintf(fp, "\n");
    }
    dump_block(fp, "gic", &cfg->gic);
    dump_event_list(fp, "  ", "events", cfg->gic.events,
                    cfg->gic.n_events);
    return 0;
}

/* ------------------------------------------------------------------ */
/* CSV header                                                          */
/* ------------------------------------------------------------------ */
int config_pcie_units(const bf2_config_t *cfg, int kind[], int idx[],
                      int max_units)
{
    const cfg_block_t *b = &cfg->pcie;
    int n = 0, i;

    if (b->rx_merged) {
        kind[n] = 0; idx[n] = -1; n++;
    }
    for (i = 0; i < b->n_regs; i++) {
        int in_triple = (i == b->rx_idx[0] || i == b->rx_idx[1] ||
                         i == b->rx_idx[2]);
        if (strncmp(b->regs[i].name, "IN_", 3) == 0 &&
            (!b->rx_merged || !in_triple)) {
            kind[n] = 1; idx[n] = i; n++;
        }
    }
    if (b->tx_merged) {
        kind[n] = 2; idx[n] = -1; n++;
    }
    for (i = 0; i < b->n_regs; i++) {
        int in_triple = (i == b->tx_idx[0] || i == b->tx_idx[1] ||
                         i == b->tx_idx[2]);
        if (strncmp(b->regs[i].name, "OUT_", 4) == 0 &&
            (!b->tx_merged || !in_triple)) {
            kind[n] = 1; idx[n] = i; n++;
        }
    }
    if (n > max_units)
        n = max_units;
    return n;
}

void config_pcie_unit_name(const bf2_config_t *cfg, int kind, int idx,
                           char *buf, size_t bufsz)
{
    if (kind == 0)
        snprintf(buf, bufsz, "rx_bytes");
    else if (kind == 2)
        snprintf(buf, bufsz, "tx_bytes");
    else
        snprintf(buf, bufsz, "%s", cfg->pcie.regs[idx].colname);
}

int config_write_csv_header(FILE *fp, const bf2_config_t *cfg,
                            const bf2_presence_t *p)
{
    static const char *l1_cols[4] = { "l1d_access", "l1d_miss",
                                      "l1i_access", "l1i_miss" };
    int i, u;

    fputs("timestamp", fp);

    if (cfg->tile.enabled && p->have_tile) {
        fputs(",tile_group", fp);
        for (i = 0; i < cfg->tile.n_tile_cols; i++)
            fprintf(fp, ",tile_%s", cfg->tile.tile_cols[i].name);
    }
    if (cfg->tilenet.enabled && p->have_tilenet) {
        for (i = 0; i < cfg->tilenet.n_events; i++)
            fprintf(fp, ",tilenet_%s", cfg->tilenet.events[i].colname);
    }
    if (cfg->trio.enabled && p->have_trio) {
        for (i = 0; i < cfg->trio.n_events; i++)
            fprintf(fp, ",trio_%s", cfg->trio.events[i].colname);
    }
    if (cfg->smmu.enabled && p->have_smmu) {
        for (i = 0; i < cfg->smmu.n_events; i++)
            fprintf(fp, ",smmu_%s", cfg->smmu.events[i].colname);
    }
    if (cfg->triogen.enabled && p->have_triogen)
        fputs(",triogen_tx_dat_af,triogen_rx_dat_af", fp);
    if (cfg->l3cache.enabled && p->have_l3) {
        for (i = 0; i < p->n_l3halves; i++) {
            for (u = 0; u < cfg->l3cache.n_l3_cols; u++)
                fprintf(fp, ",l3half%d_%s", i,
                        cfg->l3cache.l3_cols[u].name);
        }
    }
    if (cfg->pcie.enabled && p->have_pcie) {
        for (i = 0; i < p->n_pcies; i++) {
            int kind[CFG_PCIE_UNITS_MAX], idx[CFG_PCIE_UNITS_MAX];
            int nu = config_pcie_units(cfg, kind, idx,
                                       CFG_PCIE_UNITS_MAX);

            for (u = 0; u < nu; u++) {
                char buf[CFG_NAME_MAX];
                config_pcie_unit_name(cfg, kind[u], idx[u], buf,
                                      sizeof(buf));
                fprintf(fp, ",pcie%d_%s", i, buf);
            }
        }
    }
    if (cfg->l1.enabled && p->have_l1) {
        for (i = 0; i < 4; i++) {
            if (cfg->l1.l1_sel[i])
                fprintf(fp, ",%s", l1_cols[i]);
        }
    }
    if (cfg->cpu.enabled && p->have_cpu)
        fputs(",cpu_util_pct,cpu_min_pct,cpu_max_pct,cpu_avg_pct", fp);
    if (cfg->mem.enabled && p->have_mem)
        fputs(",mem_total_kb,mem_used_kb,mem_util_pct", fp);
    if (cfg->net.enabled && p->have_net)
        fputs(",net_rx_bytes,net_tx_bytes", fp);
    if (cfg->gic.enabled && p->have_gic) {
        for (i = 0; i < cfg->gic.n_events; i++)
            fprintf(fp, ",gic_%s", cfg->gic.events[i].colname);
    }
    fputs("\n", fp);
    return 0;
}
