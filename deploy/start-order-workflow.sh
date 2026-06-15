#!/bin/sh
sleep 10
cd /home/zion/projects/order-workflow-sample || exit 1
exec /usr/bin/flock -n /tmp/order-workflow-sample.lock /usr/bin/env PORT=3000 /usr/bin/python3 src/server.py >> /tmp/order-workflow-server.log 2>&1
