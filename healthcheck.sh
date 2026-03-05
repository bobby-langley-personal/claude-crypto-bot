#!/bin/bash
HEALTH_URL="http://localhost:8001/health"
LOG="/root/healthcheck.log"

response=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 $HEALTH_URL)

if [ "$response" != "200" ]; then
    echo "$(date) — Health check FAILED (HTTP $response). Restarting..." >> $LOG
    systemctl restart cryptobot
    sleep 15
    response=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 $HEALTH_URL)
    if [ "$response" == "200" ]; then
        echo "$(date) — Restart successful" >> $LOG
    else
        echo "$(date) — Restart FAILED. Manual intervention needed." >> $LOG
    fi
else
    echo "$(date) — Health check OK" >> $LOG
fi