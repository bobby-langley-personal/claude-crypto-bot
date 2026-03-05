#!/bin/bash
echo "=== Deploy started at $(date) ===" >> /root/deploy.log
cd /root/claude-crypto-bot
git pull origin main >> /root/deploy.log 2>&1
pip3 install -r requirements.txt --break-system-packages --ignore-installed >> /root/deploy.log 2>&1
systemctl restart cryptobot
echo "=== Deploy complete at $(date) ===" >> /root/deploy.log
systemctl status cryptobot >> /root/deploy.log