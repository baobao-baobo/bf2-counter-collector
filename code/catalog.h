/* catalog.h - event catalog for BlueField-2 performance counters.
 *
 * Every documented non-reserved event of the mlxbf-pmc programmable
 * counters (mechanism 1) plus the PCIe TLR statistics registers
 * (mechanism 2) and the fixed ARM PMU events (L1).  Data extracted
 * from the official "BlueField-2 Performance Monitoring Counters"
 * documentation.
 *
 * ASCII only: the BlueField-2 gcc cannot handle UTF-8 in sources.
 */
#ifndef BF2_CATALOG_H
#define BF2_CATALOG_H

#include <stdio.h>

#define CATALOG_NAME_MAX 48   /* longest event name fits, incl. NUL */

typedef struct {
    const char *name;    /* canonical UPPER_CASE event name */
    const char *colname; /* CSV column suffix; NULL = lowercase(name) */
    unsigned int code;   /* event code (0 for registers / fixed) */
} catalog_event_t;

typedef struct {
    const char *block;              /* config section name */
    const char *dir_prefix;         /* hwmon sysfs directory prefix */
    int max_slots;                  /* programmable counter slots (0 = fixed) */
    const catalog_event_t *events;  /* NULL-terminated */
} catalog_block_t;

/* Build generated catalog entries (tilenet DIAG family).  Idempotent. */
void catalog_init(void);

extern catalog_block_t g_catalog[];
extern const int g_catalog_n;

/* Case-insensitive lookup.  Returns NULL if not found. */
const catalog_block_t *catalog_find_block(const char *name);
const catalog_event_t *catalog_find_event(const char *block,
                                           const char *name);

/* Resolved CSV column suffix (static buffer, single-threaded use). */
const char *catalog_colname(const catalog_event_t *ev);

/* Print the full catalog (used by --list-events). */
int catalog_list_events(FILE *fp);

#endif /* BF2_CATALOG_H */
