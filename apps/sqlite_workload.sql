-- G4 workload: sustained eMMC read/write via SQLite.
-- Usage:   apps/bin/sqlite3 <db-path-on-emmc> < apps/sqlite_workload.sql
-- Calibrate: run once with `time`, adjust the 1000000 row count so the
-- whole script takes ~30-35 s (inside the -t 40 app window).  Delete the
-- database between calibration runs.
.timer on
CREATE TABLE t(id INTEGER PRIMARY KEY, a TEXT, b REAL);
INSERT INTO t WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<1000000)
  SELECT x, printf('%020d', x), x*1.5 FROM c;
CREATE INDEX idx_a ON t(a);
SELECT count(*), sum(b) FROM t WHERE a > '000000000000000000500000';
SELECT avg(b) FROM t a JOIN t b ON b.id=a.id+1 WHERE a.id<500000;
DELETE FROM t WHERE id % 2 = 0;
VACUUM;
SELECT count(*) FROM t;
