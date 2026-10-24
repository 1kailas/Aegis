#!/usr/bin/env bash
# Aegis AI — Unified Production Installer & Client Node Joiner
# Usage on client machine:
#   curl -sSL http://<SERVER_IP>:8000/install.sh | bash
#   curl -sSL http://<SERVER_IP>:8000/install.sh | bash -s <SERVER_IP>
# Or local checkout:
#   ./scripts/install.sh [--role server|agent|all]

set -euo pipefail

ARG1="${1:-all}"
INSTALL_DIR="${2:-/opt/aegis}"
SERVER_URL="http://127.0.0.1:8000"
ROLE="all"
PYTHON_BIN=""

BOLD="\033[1m"
GREEN="\033[32m"
CYAN="\033[36m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

# Check if first argument is an IP address or URL
if [[ "$ARG1" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+ ]] || [[ "$ARG1" =~ ^https?:// ]] || [[ "$ARG1" =~ :[0-9]+ ]]; then
    ROLE="agent"
    SERVER_IP="$ARG1"
    if [[ ! "$SERVER_IP" =~ ^https?:// ]]; then
        if [[ "$SERVER_IP" =~ : ]]; then
            SERVER_URL="http://${SERVER_IP}"
        else
            SERVER_URL="http://${SERVER_IP}:8000"
        fi
    else
        SERVER_URL="$SERVER_IP"
    fi
elif [ "$ARG1" = "server" ] || [ "$ARG1" = "agent" ] || [ "$ARG1" = "all" ]; then
    ROLE="$ARG1"
fi

echo -e "${BOLD}${CYAN}"
echo "  ⛨  AEGIS AI — PRODUCTION NODE INSTALLER"
echo "  Target Server: ${SERVER_URL} | Role: ${ROLE}"
echo -e "${RESET}"

# 1. Detect Python 3 (3.8+ for lightweight agent, 3.11+ for server)
for py in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8; do
    if command -v "$py" >/dev/null 2>&1; then
        PYTHON_BIN="$py"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo -e "${RED}Error:${RESET} python3 is required to run Aegis."
    exit 1
fi

echo -e "${GREEN}✔${RESET} Using Python: $PYTHON_BIN ($($PYTHON_BIN --version))"

# 2. Setup install directory
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

BIN_DIR="/usr/local/bin"
if [ ! -w "$BIN_DIR" ]; then
    BIN_DIR="$HOME/.local/bin"
    mkdir -p "$BIN_DIR"
fi

# 3. If running from repository checkout with pyproject.toml
if [ -f "pyproject.toml" ] || [ -f "../pyproject.toml" ]; then
    echo -e "${CYAN}→${RESET} Installing from source checkout..."
    if [ ! -d ".venv" ]; then
        "$PYTHON_BIN" -m venv .venv
    fi
    .venv/bin/pip install --upgrade pip >/dev/null 2>&1 || true
    .venv/bin/pip install -e . >/dev/null

    ln -sf "$INSTALL_DIR/.venv/bin/aegis" "$BIN_DIR/aegis"
    ln -sf "$INSTALL_DIR/.venv/bin/aegis-server" "$BIN_DIR/aegis-server"
    ln -sf "$INSTALL_DIR/.venv/bin/aegis-agent" "$BIN_DIR/aegis-agent"
else
    # Remote client installation: fetch standalone agent directly from the server
    echo -e "${CYAN}→${RESET} Fetching standalone agent from ${SERVER_URL}/agent.py..."
    if command -v curl >/dev/null 2>&1; then
        curl -sSL "${SERVER_URL}/agent.py" -o "$INSTALL_DIR/agent.py"
    elif command -v wget >/dev/null 2>&1; then
        wget -q "${SERVER_URL}/agent.py" -O "$INSTALL_DIR/agent.py"
    else
        "$PYTHON_BIN" -c "import urllib.request; urllib.request.urlretrieve('${SERVER_URL}/agent.py', '$INSTALL_DIR/agent.py')"
    fi

    # Create wrapper launcher script
    cat << 'EOF' > "$BIN_DIR/aegis-agent"
#!/usr/bin/env bash
python3 /opt/aegis/agent.py "$@"
EOF
    chmod +x "$BIN_DIR/aegis-agent"

    cat << 'EOF' > "$BIN_DIR/aegis"
#!/usr/bin/env bash
if [ "$1" = "agent" ] || [ "$1" = "join" ]; then
    shift
    python3 /opt/aegis/agent.py "$@"
else
    python3 /opt/aegis/agent.py "$@"
fi
EOF
    chmod +x "$BIN_DIR/aegis"
fi

# 4. Optional Systemd Unit Installation
if [ -d "/etc/systemd/system" ] && [ "$(id -u)" -eq 0 ]; then
    echo -e "${CYAN}→${RESET} Configuring systemd services..."
    mkdir -p /etc/default
    echo "AEGIS_SERVER_URL=${SERVER_URL}" > /etc/default/aegis-agent

    if [ -f "$INSTALL_DIR/scripts/systemd/aegis-agent.service" ]; then
        cp "$INSTALL_DIR/scripts/systemd/aegis-agent.service" /etc/systemd/system/
    else
        cat << EOF > /etc/systemd/system/aegis-agent.service
[Unit]
Description=Aegis AI Telemetry Agent
After=network.target

[Service]
Type=simple
User=root
EnvironmentFile=-/etc/default/aegis-agent
ExecStart=/usr/local/bin/aegis-agent \${AEGIS_SERVER_URL}
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF
    fi
    systemctl daemon-reload || true
fi

echo ""
echo -e "${BOLD}${GREEN}✔ Aegis node installation complete!${RESET}"
echo "────────────────────────────────────────────────────────────"
echo -e "${BOLD}Commands ready:${RESET}"
echo -e "  ${CYAN}aegis-agent ${SERVER_URL}${RESET}  - Start telemetry daemon reporting to server"
echo -e "  ${CYAN}aegis join ${SERVER_URL}${RESET}   - Join cluster mesh directly"
echo ""

# If role is agent and an explicit remote server was provided, run a quick connectivity test
if [ "$ROLE" = "agent" ] && [ "$SERVER_URL" != "http://127.0.0.1:8000" ]; then
    echo -e "${CYAN}→${RESET} Testing connectivity to Aegis Server at ${SERVER_URL}..."
    if "$PYTHON_BIN" "$INSTALL_DIR/agent.py" "$SERVER_URL" --once; then
        echo -e "${BOLD}${GREEN}✔ Successfully registered and heartbeat verified with ${SERVER_URL}!${RESET}"
    else
        echo -e "${YELLOW}Note:${RESET} Initial ping check failed. Check that the server is running and port is open."
    fi
fi
