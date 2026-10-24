#!/usr/bin/env bash
# Aegis AI — Unified Production Installer for Linux
# Usage:
#   curl -sSL https://raw.githubusercontent.com/1kailas/Aegis/main/scripts/install.sh | bash
# Or locally:
#   ./scripts/install.sh [--role server|agent|all] [--dir /opt/aegis]

set -euo pipefail

ROLE="${1:-all}"
INSTALL_DIR="${2:-/opt/aegis}"
PYTHON_BIN=""

BOLD="\033[1m"
GREEN="\033[32m"
CYAN="\033[36m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

echo -e "${BOLD}${CYAN}"
echo "  ⛨  AEGIS AI — PRODUCTION INSTALLER"
echo "  Autonomous Infrastructure Control Plane & Agent"
echo -e "${RESET}"

# 1. Detect Python 3.11+
for py in python3 python3.14 python3.13 python3.12 python3.11; do
    if command -v "$py" >/dev/null 2>&1; then
        ver=$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON_BIN="$py"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo -e "${RED}Error:${RESET} Python 3.11 or higher is required to run Aegis."
    exit 1
fi

echo -e "${GREEN}✔${RESET} Using Python: $PYTHON_BIN ($($PYTHON_BIN --version))"

# 2. Setup install directory
echo -e "${CYAN}→${RESET} Installing Aegis to ${INSTALL_DIR}..."
mkdir -p "$INSTALL_DIR"

if [ -f "pyproject.toml" ]; then
    # Running from source checkout
    cp -r * "$INSTALL_DIR/" 2>/dev/null || true
fi

cd "$INSTALL_DIR"

# 3. Create virtual environment
if [ ! -d ".venv" ]; then
    echo -e "${CYAN}→${RESET} Creating Python virtual environment..."
    "$PYTHON_BIN" -m venv .venv
fi

# 4. Install Aegis & dependencies
echo -e "${CYAN}→${RESET} Installing dependencies and console entrypoints..."
.venv/bin/pip install --upgrade pip >/dev/null 2>&1 || true
.venv/bin/pip install -e . >/dev/null

# 5. Create symlinks in PATH
BIN_DIR="/usr/local/bin"
if [ ! -w "$BIN_DIR" ]; then
    BIN_DIR="$HOME/.local/bin"
    mkdir -p "$BIN_DIR"
fi

echo -e "${CYAN}→${RESET} Linking CLI binaries to ${BIN_DIR}..."
ln -sf "$INSTALL_DIR/.venv/bin/aegis" "$BIN_DIR/aegis"
ln -sf "$INSTALL_DIR/.venv/bin/aegis-server" "$BIN_DIR/aegis-server"
ln -sf "$INSTALL_DIR/.venv/bin/aegis-agent" "$BIN_DIR/aegis-agent"

# 6. Optional Systemd Unit Installation
if [ -d "/etc/systemd/system" ] && [ "$(id -u)" -eq 0 ]; then
    echo -e "${CYAN}→${RESET} Configuring systemd services..."
    if [ "$ROLE" = "server" ] || [ "$ROLE" = "all" ]; then
        cp "$INSTALL_DIR/scripts/systemd/aegis-server.service" /etc/systemd/system/
    fi
    if [ "$ROLE" = "agent" ] || [ "$ROLE" = "all" ]; then
        cp "$INSTALL_DIR/scripts/systemd/aegis-agent.service" /etc/systemd/system/
    fi
    systemctl daemon-reload || true
fi

echo ""
echo -e "${BOLD}${GREEN}✔ Aegis AI installation complete!${RESET}"
echo "─" * 60
echo -e "${BOLD}Commands installed:${RESET}"
echo -e "  ${CYAN}aegis${RESET}          - Unified developer CLI (status, nodes, incidents, canaries)"
echo -e "  ${CYAN}aegis-server${RESET}   - Start the control plane and web dashboard"
echo -e "  ${CYAN}aegis-agent${RESET}    - Start the node telemetry agent daemon"
echo ""
echo -e "${BOLD}Quick start:${RESET}"
echo -e "  Start server:   ${YELLOW}aegis-server${RESET} (Dashboard at http://localhost:8000)"
echo -e "  Start agent:    ${YELLOW}aegis-agent --server http://<server-ip>:8000${RESET}"
echo -e "  Check status:   ${YELLOW}aegis status${RESET}"
echo ""
