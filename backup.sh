#!/bin/bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
COMPOSE_FILE="$SCRIPT_DIR/compose.prod.yaml"

# get the current date and time
now=$(date +"%Y%m%d_%H%M")

# create zoomail_db_dump directory if it doesn't exist
mkdir -p ~/zoomail_db_dump

# dump only the application database so local restore can use different credentials
# use the application user; existing databases may have a different root password
docker compose -f "$COMPOSE_FILE" exec -T database \
    sh -lc 'exec mariadb-dump -u"$MARIADB_USER" -p"$MARIADB_PASSWORD" --single-transaction --databases "$MARIADB_DATABASE"' \
    > ~/zoomail_db_dump/zoomail_$now.sql

cd ~/zoomail_db_dump

# compress the database dump
tar -czvf zoomail_$now.tar.gz zoomail_$now.sql

# remove the uncompressed database dump
rm zoomail_$now.sql

chmod 600 zoomail_$now.tar.gz

cd -

# copy the compressed database dump to the backup server
docker run \
    --rm \
    -v ~/zoomail_db_dump:/zoomail_db_dump \
    -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID \
    -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY \
    -e AWS_DEFAULT_REGION=$AWS_DEFAULT_REGION \
    amazon/aws-cli:latest \
    s3 cp /zoomail_db_dump/zoomail_$now.tar.gz s3://zoomail-db-dump/dumps/zoomail_$now.tar.gz
