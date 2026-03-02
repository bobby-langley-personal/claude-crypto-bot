#!/usr/bin/env python3
"""
deploy_aws.py  –  Provisions an EC2 instance and deploys the Crypto Sentiment Bot.

Uses the AWS credentials already configured in ~/.aws/credentials
(same account as job-search-gmail-monitor).

What this script does:
  1. Looks up the latest Amazon Linux 2023 AMI automatically
  2. Creates a key pair (saved to deploy/crypto-bot-key.pem)
  3. Creates a security group (allows port 22 SSH + port 8000 dashboard)
  4. Launches a t2.micro instance (free tier eligible)
  5. Waits for SSH to be ready
  6. Uploads all bot code via SFTP
  7. Installs Python dependencies
  8. Creates and starts a systemd service
  9. Prints the dashboard URL

Usage:
    cd c:/Users/Bobby/Projects/crypto-bot
    python deploy/deploy_aws.py

After deployment:
    - Dashboard: http://<public-ip>:8000
    - SSH:       ssh -i deploy/crypto-bot-key.pem ec2-user@<public-ip>
    - Logs:      ssh in → sudo journalctl -u crypto-bot -f

Re-running this script on an existing instance will redeploy the code
without creating a new server.

Requirements (install once):
    pip install boto3 paramiko
"""
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
import paramiko

# ── Configuration ─────────────────────────────────────────────────────────────
REGION        = "us-east-1"
INSTANCE_TYPE = "t2.micro"          # Free tier eligible
KEY_NAME      = "crypto-bot-key"
SG_NAME       = "crypto-bot-sg"
INSTANCE_TAG  = "crypto-sentiment-bot"
REMOTE_DIR    = "/home/ec2-user/crypto-bot"
DASHBOARD_PORT = 8000

# Files/dirs to exclude from the upload
EXCLUDE = frozenset({
    "__pycache__", ".git", ".env", "deploy",
    "portfolio.json", "trades.json", "bot.log",
    "*.pyc", ".DS_Store",
})

SCRIPT_DIR = Path(__file__).parent
BOT_DIR    = SCRIPT_DIR.parent
KEY_PATH   = SCRIPT_DIR / f"{KEY_NAME}.pem"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


def main():
    log.info("=" * 55)
    log.info("  Crypto Sentiment Bot  –  AWS Deployment")
    log.info("=" * 55)

    ec2 = boto3.client("ec2", region_name=REGION)
    ssm = boto3.client("ssm", region_name=REGION)

    # ── 1. Get latest Amazon Linux 2023 AMI ───────────────────────────────
    log.info("\n[1/7] Fetching latest Amazon Linux 2023 AMI…")
    ami_id = _get_latest_al2023_ami(ssm)
    log.info(f"      AMI: {ami_id}")

    # ── 2. Key pair ───────────────────────────────────────────────────────
    log.info("\n[2/7] Key pair…")
    _ensure_key_pair(ec2, KEY_NAME, KEY_PATH)

    # ── 3. Security group ─────────────────────────────────────────────────
    log.info("\n[3/7] Security group…")
    sg_id = _ensure_security_group(ec2, SG_NAME, DASHBOARD_PORT)
    log.info(f"      SG: {sg_id}")

    # ── 4. EC2 instance ───────────────────────────────────────────────────
    log.info("\n[4/7] EC2 instance…")
    instance_id, public_ip = _ensure_instance(ec2, ami_id, sg_id)
    log.info(f"      Instance: {instance_id}  IP: {public_ip}")

    # ── 5. Wait for SSH ───────────────────────────────────────────────────
    log.info("\n[5/7] Waiting for SSH…")
    _wait_for_ssh(public_ip, KEY_PATH)
    log.info("      SSH ready")

    # ── 6. Upload code ────────────────────────────────────────────────────
    log.info("\n[6/7] Uploading code…")
    _upload_code(public_ip, KEY_PATH)

    # ── 7. Install + start service ────────────────────────────────────────
    log.info("\n[7/7] Installing dependencies and starting service…")
    _setup_service(public_ip, KEY_PATH)

    # ── Done ──────────────────────────────────────────────────────────────
    log.info("\n" + "=" * 55)
    log.info("  Deployment complete!")
    log.info(f"\n  Dashboard:  http://{public_ip}:{DASHBOARD_PORT}")
    log.info(f"  SSH:        ssh -i {KEY_PATH} ec2-user@{public_ip}")
    log.info(
        f"\n  IMPORTANT: Your .env was NOT uploaded (security).\n"
        f"  SSH in and create it:\n\n"
        f"    ssh -i {KEY_PATH} ec2-user@{public_ip}\n"
        f"    nano {REMOTE_DIR}/.env\n"
        f"    sudo systemctl restart crypto-bot\n\n"
        f"  Then visit http://{public_ip}:{DASHBOARD_PORT}"
    )
    log.info("=" * 55)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_latest_al2023_ami(ssm) -> str:
    """Fetch the current Amazon Linux 2023 x86_64 AMI from SSM Parameter Store."""
    param = ssm.get_parameter(
        Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
    )
    return param["Parameter"]["Value"]


def _ensure_key_pair(ec2, key_name: str, key_path: Path) -> None:
    """Create a key pair if it doesn't exist locally."""
    if key_path.exists():
        log.info(f"      Using existing key: {key_path}")
        return

    try:
        ec2.delete_key_pair(KeyName=key_name)   # clean up any orphaned remote key
    except ClientError:
        pass

    log.info(f"      Creating key pair '{key_name}'…")
    resp = ec2.create_key_pair(KeyName=key_name)
    key_path.parent.mkdir(exist_ok=True)
    key_path.write_text(resp["KeyMaterial"])
    # Restrict permissions (required by SSH)
    try:
        key_path.chmod(0o400)
    except NotImplementedError:
        pass  # Windows – chmod is a no-op; use icacls separately if needed
    log.info(f"      Saved to {key_path}")


def _ensure_security_group(ec2, sg_name: str, dashboard_port: int) -> str:
    """Return existing security group ID or create a new one."""
    try:
        resp = ec2.describe_security_groups(GroupNames=[sg_name])
        sg_id = resp["SecurityGroups"][0]["GroupId"]
        log.info(f"      Using existing security group: {sg_id}")
        return sg_id
    except ClientError:
        pass

    vpc_resp = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpc_id   = vpc_resp["Vpcs"][0]["VpcId"]

    sg = ec2.create_security_group(
        GroupName=sg_name,
        Description="Crypto Bot dashboard and SSH access",
        VpcId=vpc_id,
    )
    sg_id = sg["GroupId"]

    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            # SSH
            {"IpProtocol": "tcp", "FromPort": 22,
             "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0",
                                          "Description": "SSH"}]},
            # Dashboard
            {"IpProtocol": "tcp", "FromPort": dashboard_port,
             "ToPort": dashboard_port,
             "IpRanges": [{"CidrIp": "0.0.0.0/0",
                            "Description": "Bot dashboard"}]},
        ],
    )
    log.info(f"      Created security group: {sg_id}")
    return sg_id


def _ensure_instance(ec2, ami_id: str, sg_id: str) -> tuple[str, str]:
    """Return (instance_id, public_ip) – reuses an existing instance if found."""
    # Check for an existing instance with our tag
    resp = ec2.describe_instances(Filters=[
        {"Name": "tag:Name",                "Values": [INSTANCE_TAG]},
        {"Name": "instance-state-name",     "Values": ["running", "pending", "stopped"]},
    ])
    for reservation in resp["Reservations"]:
        for inst in reservation["Instances"]:
            state = inst["State"]["Name"]
            iid   = inst["InstanceId"]
            if state == "stopped":
                log.info(f"      Found stopped instance {iid} – starting it…")
                ec2.start_instances(InstanceIds=[iid])
            log.info(f"      Waiting for instance {iid} to be running…")
            _wait_for_running(ec2, iid)
            public_ip = _get_public_ip(ec2, iid)
            return iid, public_ip

    # No existing instance – launch one
    log.info(f"      Launching new {INSTANCE_TYPE} instance…")
    result = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=INSTANCE_TYPE,
        KeyName=KEY_NAME,
        SecurityGroupIds=[sg_id],
        MinCount=1, MaxCount=1,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": INSTANCE_TAG}],
        }],
        # Install Python packages on first boot
        UserData="""#!/bin/bash
yum update -y
yum install -y python3-pip python3-devel nano git
""",
    )
    iid = result["Instances"][0]["InstanceId"]
    log.info(f"      Instance {iid} launched. Waiting for running state…")
    _wait_for_running(ec2, iid)
    public_ip = _get_public_ip(ec2, iid)
    return iid, public_ip


def _wait_for_running(ec2, instance_id: str) -> None:
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])


def _get_public_ip(ec2, instance_id: str) -> str:
    for _ in range(30):
        resp = ec2.describe_instances(InstanceIds=[instance_id])
        ip   = resp["Reservations"][0]["Instances"][0].get("PublicIpAddress")
        if ip:
            return ip
        time.sleep(5)
    raise RuntimeError(f"Instance {instance_id} has no public IP after 150 s")


def _wait_for_ssh(ip: str, key_path: Path, timeout: int = 300) -> None:
    """Block until SSH port is reachable and a connection succeeds."""
    deadline = time.time() + timeout
    key = paramiko.RSAKey.from_private_key_file(str(key_path))
    while time.time() < deadline:
        # Quick TCP check first
        try:
            with socket.create_connection((ip, 22), timeout=3):
                pass
        except OSError:
            time.sleep(5)
            continue
        # Try full SSH handshake
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(ip, username="ec2-user", pkey=key, timeout=10)
            ssh.close()
            return
        except Exception:
            time.sleep(5)
    raise TimeoutError(f"SSH on {ip}:22 did not become available within {timeout} s")


def _upload_code(ip: str, key_path: Path) -> None:
    """Upload all bot files (except .env and state files) via SFTP."""
    key = paramiko.RSAKey.from_private_key_file(str(key_path))
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(ip, username="ec2-user", pkey=key)

    sftp = ssh.open_sftp()

    def _ensure_remote_dir(path: str) -> None:
        try:
            sftp.stat(path)
        except FileNotFoundError:
            sftp.mkdir(path)

    _ensure_remote_dir(REMOTE_DIR)
    _ensure_remote_dir(f"{REMOTE_DIR}/templates")

    def _upload_file(local: Path, remote: str) -> None:
        sftp.put(str(local), remote)
        log.info(f"      ↑ {local.name}")

    # Upload all eligible files from bot root
    for item in sorted(BOT_DIR.iterdir()):
        if item.name in EXCLUDE or item.name.startswith("."):
            continue
        if item.is_file():
            _upload_file(item, f"{REMOTE_DIR}/{item.name}")

    # Upload templates directory
    tpl_dir = BOT_DIR / "templates"
    if tpl_dir.exists():
        for f in sorted(tpl_dir.iterdir()):
            if f.is_file():
                _upload_file(f, f"{REMOTE_DIR}/templates/{f.name}")

    # Create a .env template on the server (without real keys)
    env_template = (
        "# Add your API keys here, then: sudo systemctl restart crypto-bot\n"
        "COINBASE_API_KEY=\n"
        "COINBASE_API_SECRET=\n"
        "ANTHROPIC_API_KEY=\n"
        "CRYPTOPANIC_API_KEY=\n"
    )
    with sftp.file(f"{REMOTE_DIR}/.env", "w") as f:
        f.write(env_template)
    log.info("      ↑ .env (template – fill in your keys after deployment)")

    sftp.close()
    ssh.close()


def _ssh_run(ssh: paramiko.SSHClient, cmd: str) -> str:
    """Run a command over SSH and return stdout. Logs stderr as warnings."""
    _, stdout, stderr = ssh.exec_command(cmd)
    out = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    if err:
        for line in err.splitlines():
            if not any(x in line.lower() for x in ("warning", "notice", "hint")):
                log.warning(f"      stderr: {line}")
    return out


def _setup_service(ip: str, key_path: Path) -> None:
    """Install Python dependencies and configure the systemd service."""
    key = paramiko.RSAKey.from_private_key_file(str(key_path))
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(ip, username="ec2-user", pkey=key)

    log.info("      Installing Python dependencies…")
    _ssh_run(ssh, f"pip3 install -r {REMOTE_DIR}/requirements.txt --quiet")

    service_content = f"""[Unit]
Description=Crypto Sentiment Bot Dashboard
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory={REMOTE_DIR}
ExecStart=/usr/bin/python3 -m uvicorn web_server:app --host 0.0.0.0 --port {DASHBOARD_PORT}
Restart=always
RestartSec=10
Environment=PYTHONUTF8=1

[Install]
WantedBy=multi-user.target
"""
    # Write the service file
    escaped = service_content.replace("'", "'\\''")
    _ssh_run(ssh, f"echo '{escaped}' | sudo tee /etc/systemd/system/crypto-bot.service > /dev/null")
    _ssh_run(ssh, "sudo systemctl daemon-reload")
    _ssh_run(ssh, "sudo systemctl enable crypto-bot")
    _ssh_run(ssh, "sudo systemctl restart crypto-bot")

    # Verify
    time.sleep(3)
    status = _ssh_run(ssh, "sudo systemctl is-active crypto-bot")
    log.info(f"      Service status: {status}")

    if status != "active":
        log.warning("      Service did not start cleanly. Check logs:")
        log.warning(f"      ssh -i {KEY_PATH} ec2-user@{ip}")
        log.warning("      sudo journalctl -u crypto-bot -n 50")

    ssh.close()


if __name__ == "__main__":
    main()
