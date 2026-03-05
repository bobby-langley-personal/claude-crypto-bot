# Deployment Files for Stability Improvements

This directory contains deployment files that need to be copied to the server to implement the stability improvements from Issue #26.

## Files to Copy to Server

### 1. deploy.sh → /root/deploy.sh
Updated deployment script that uses systemctl instead of manual process management.

**Copy command:**
```bash
scp deploy.sh root@167.71.253.88:/root/deploy.sh
chmod +x /root/deploy.sh
```

### 2. cryptobot.service → /etc/systemd/system/cryptobot.service  
Enhanced systemd service with auto-restart policies and proper logging.

**Copy and enable command:**
```bash
scp cryptobot.service root@167.71.253.88:/etc/systemd/system/cryptobot.service
systemctl daemon-reload
systemctl enable cryptobot
```

### 3. healthcheck.sh → /root/healthcheck.sh
Self-healing monitor script that checks health every 5 minutes.

**Copy and setup command:**
```bash
scp healthcheck.sh root@167.71.253.88:/root/healthcheck.sh
chmod +x /root/healthcheck.sh

# Add to crontab
echo "*/5 * * * * /bin/bash /root/healthcheck.sh" | crontab -
```

## Verification

After copying files:

1. Verify deploy script: `systemctl status cryptobot`
2. Check healthcheck: `/root/healthcheck.sh` (should create /root/healthcheck.log)  
3. Test health endpoint: `curl http://localhost:8001/health`
4. View logs: `journalctl -u cryptobot -f`

## Dashboard Changes

The web dashboard now shows:
- **Header**: Uptime display next to trade count
- **Footer**: Git commit hash and health status link
- **Enhanced /health endpoint**: Now includes bot status, uptime, and version