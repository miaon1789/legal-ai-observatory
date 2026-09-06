#!/usr/bin/env bash
# Apply the whole SQL layer, in order, against the Docker SQL Server instance.
# Idempotent: schema is dropped and rebuilt, views are CREATE OR ALTER.
#
#   ./sql/run_all.sh
set -euo pipefail

CONTAINER=${CONTAINER:-mssql-legalai}
cd "$(dirname "$0")/.."
[ -f .env ] && set -a && . ./.env && set +a

sql() {
  docker exec -i "$CONTAINER" /opt/mssql-tools18/bin/sqlcmd \
    -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -b -I < "$1"
}

# Cap the buffer pool. Left uncapped, SQL Server grows to ~4 GB on this
# container and competes with the Power BI VM on the same laptop; capped, the
# load below is no slower and the host keeps its headroom.
docker exec -i "$CONTAINER" /opt/mssql-tools18/bin/sqlcmd \
  -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -b -Q "
EXEC sp_configure 'show advanced options', 1; RECONFIGURE;
EXEC sp_configure 'max server memory (MB)', ${MSSQL_MAX_MEMORY_MB:-1400}; RECONFIGURE;" \
  > /dev/null

for f in sql/01_schema/*.sql sql/02_load/*.sql sql/03_views/*.sql sql/04_procs/*.sql; do
  printf '%-52s' "$(basename "$f")"
  start=$(date +%s)
  sql "$f" > /tmp/sqlout.$$ 2>&1 || { echo "FAILED"; cat /tmp/sqlout.$$; exit 1; }
  echo "ok  ($(( $(date +%s) - start ))s)"
done
rm -f /tmp/sqlout.$$

docker exec -i "$CONTAINER" /opt/mssql-tools18/bin/sqlcmd \
  -S localhost -U sa -P "$MSSQL_SA_PASSWORD" -C -d LegalAIObservatory -W -s' | ' -Q "
SET NOCOUNT ON;
EXEC dbo.usp_refresh_adoption_snapshot;
SELECT 'tables' AS object_type, COUNT(*) AS n FROM sys.tables
UNION ALL SELECT 'views', COUNT(*) FROM sys.views
UNION ALL SELECT 'procedures', COUNT(*) FROM sys.procedures;"
