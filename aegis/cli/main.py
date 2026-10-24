"""Aegis unified developer CLI.

Provides developer and SRE operations from the command line:
  aegis server                 # Start the Aegis control plane
  aegis agent                  # Start the node telemetry agent
  aegis status                 # Overview of system health, guardians & cluster
  aegis nodes list             # List registered cluster mesh nodes
  aegis nodes drain <node_id>  # Drain a cluster node
  aegis incidents list         # List recent incidents
  aegis incidents diagnose <id># Trigger multi-tier AI diagnosis
  aegis canary list            # List active canary deployments
  aegis canary rollback <id>   # Trigger emergency canary rollback
  aegis keys create            # Generate cryptographic RBAC API key
  aegis mode [mode]            # Query or update autonomous policy mode
  aegis estop [on|off]         # Query or trigger emergency stop
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import httpx

from aegis import __version__
from aegis.client.agent import normalize_server_url


# Terminal colors
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def get_client(server_url: str, token: str | None = None) -> httpx.Client:
    url = normalize_server_url(server_url)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=url, headers=headers, timeout=10.0)


def fmt_severity(sev: str) -> str:
    s = str(sev).upper()
    if s in ("CRITICAL", "CRIT"):
        return f"{RED}{BOLD}CRITICAL{RESET}"
    if s in ("WARNING", "WARN"):
        return f"{YELLOW}WARNING{RESET}"
    return f"{CYAN}{s}{RESET}"


def fmt_status(st: str) -> str:
    s = str(st).upper()
    if s in ("ONLINE", "OK", "NOMINAL", "PROMOTED", "RESOLVED"):
        return f"{GREEN}{s}{RESET}"
    if s in ("DEGRADED", "WARNING", "WARN", "STALE", "CANARY_OBSERVING"):
        return f"{YELLOW}{s}{RESET}"
    if s in ("OFFLINE", "CRITICAL", "FAILED", "ROLLING_BACK", "ROLLED_BACK"):
        return f"{RED}{s}{RESET}"
    return f"{DIM}{s}{RESET}"


def cmd_status(client: httpx.Client, _args: argparse.Namespace) -> int:
    """Prints a fast, structured terminal dashboard."""
    try:
        health_r = client.get("/api/health")
        if health_r.status_code != 200:
            print(f"{RED}Error:{RESET} Server returned HTTP {health_r.status_code}: {health_r.text}")
            return 1
        health = health_r.json()
    except Exception as e:
        print(f"{RED}Cannot connect to Aegis Server:{RESET} {e}")
        print(f"{DIM}Hint: Run 'aegis server' to start the local control plane.{RESET}")
        return 1

    try:
        snap_r = client.get("/api/overview")
        snap = snap_r.json() if snap_r.status_code == 200 else {}
    except Exception:
        snap = {}

    try:
        cluster_r = client.get("/api/cluster/summary")
        cluster = cluster_r.json() if cluster_r.status_code == 200 else {}
    except Exception:
        cluster = {}

    print(f"\n{BOLD}⛨ Aegis Control Plane v{health.get('version', __version__)}{RESET}")
    print(f"{DIM}Server URL:{RESET} {client.base_url}  |  {DIM}Uptime:{RESET} {health.get('uptime_s', 0)}s  |  {DIM}Host:{RESET} {health.get('host_id', 'local')}")
    print("─" * 68)

    # Core Status
    mode = health.get("mode", "unknown")
    estop = health.get("emergency_stop", False)
    estop_str = f"{RED}{BOLD}ENGAGED{RESET}" if estop else f"{GREEN}DISENGAGED{RESET}"
    mode_str = f"{YELLOW}{mode}{RESET}" if mode == "monitoring_only" else f"{GREEN}{mode}{RESET}"

    print(f"{BOLD}OPERATIONAL STATE:{RESET}")
    print(f"  Mode:            {mode_str}")
    print(f"  Emergency Stop:  {estop_str}")
    print(f"  Engine Running:  {GREEN if health.get('engine_running') else RED}{health.get('engine_running')}{RESET}")
    print(f"  DB Integrity:    {GREEN if health.get('db_integrity') else RED}{health.get('db_integrity')}{RESET}")

    # Guardians
    thermal = snap.get("thermal", {})
    t_state = thermal.get("state", "UNKNOWN")
    print(f"\n{BOLD}SAFETY GUARDIANS:{RESET}")
    print(f"  Thermal Guardian:   {fmt_status(t_state)} ({thermal.get('highest_c', 0)}°C, sensor: {thermal.get('highest_sensor', 'none')})")

    # Cluster Mesh
    if cluster:
        total = cluster.get("total_nodes", 0)
        online = cluster.get("online_nodes", 0)
        cores = cluster.get("total_cluster_cpu_cores", 0)
        ram_gb = cluster.get("total_cluster_ram_gb", 0)
        gpus = cluster.get("total_cluster_gpus", 0)
        print(f"\n{BOLD}CLUSTER MESH:{RESET}")
        print(f"  Nodes Online:       {GREEN}{online}/{total}{RESET}")
        print(f"  Total Capacity:     {cores} CPU cores | {ram_gb} GB RAM | {gpus} GPUs")

    # Active Incidents
    incidents = snap.get("active_incidents", [])
    print(f"\n{BOLD}ACTIVE INCIDENTS ({len(incidents)}):{RESET}")
    if not incidents:
        print(f"  {GREEN}✔ No active incidents detected.{RESET}")
    else:
        for inc in incidents[:5]:
            iid = inc.get("incident_id", "INC-?")
            sev = fmt_severity(inc.get("severity", "INFO"))
            title = inc.get("title", "Unknown")
            state = fmt_status(inc.get("state", "DETECTED"))
            print(f"  [{iid}] {sev} {title} -> {state}")

    print("─" * 68)
    print(f"{DIM}Run 'aegis incidents list' or 'aegis nodes list' for detailed tables.{RESET}\n")
    return 0


def cmd_nodes(client: httpx.Client, args: argparse.Namespace) -> int:
    """Manages cluster mesh nodes."""
    sub = args.nodes_action or "list"

    if sub == "list":
        try:
            r = client.get("/api/cluster/nodes")
            if r.status_code != 200:
                print(f"{RED}Error fetching nodes:{RESET} {r.text}")
                return 1
            nodes = r.json()
        except Exception as e:
            print(f"{RED}Error:{RESET} {e}")
            return 1

        print(f"\n{BOLD}{'NODE ID':<22} {'HOSTNAME':<18} {'IP ADDRESS':<16} {'STATUS':<12} {'LAST HEARTBEAT':<20} {'LABELS'}{RESET}")
        print("─" * 96)
        if not nodes:
            print(f"{DIM}No nodes registered in cluster mesh yet.{RESET}")
            print(f"{DIM}Start an agent with: aegis agent --server {client.base_url}{RESET}\n")
            return 0

        for n in nodes:
            nid = n.get("node_id", "")
            host = n.get("hostname", "")
            ip = n.get("ip_address", "")
            st = fmt_status(n.get("status", "UNKNOWN"))
            hb = n.get("last_heartbeat", "never")
            labels = json.dumps(n.get("labels", {})) if n.get("labels") else ""
            print(f"{nid:<22} {host:<18} {ip:<16} {st:<21} {hb:<20} {labels}")
        print(f"\nTotal: {len(nodes)} node(s)\n")
        return 0

    elif sub == "drain":
        if not args.node_id:
            print(f"{RED}Error:{RESET} node_id required for drain command")
            return 1
        try:
            r = client.post(f"/api/cluster/nodes/{args.node_id}/drain", json={"reason": args.reason or "CLI drain"})
            if r.status_code == 200:
                print(f"{GREEN}✔ Successfully drained node '{args.node_id}'.{RESET}")
                return 0
            else:
                print(f"{RED}Drain failed (HTTP {r.status_code}):{RESET} {r.text}")
                return 1
        except Exception as e:
            print(f"{RED}Error:{RESET} {e}")
            return 1

    return 0


def cmd_incidents(client: httpx.Client, args: argparse.Namespace) -> int:
    """Manages incidents."""
    sub = args.incidents_action or "list"

    if sub == "list":
        params: dict[str, Any] = {"limit": args.limit or 25}
        if args.state:
            params["state"] = args.state
        try:
            r = client.get("/api/incidents", params=params)
            if r.status_code != 200:
                print(f"{RED}Error:{RESET} {r.text}")
                return 1
            incs = r.json()
        except Exception as e:
            print(f"{RED}Error:{RESET} {e}")
            return 1

        print(f"\n{BOLD}{'INCIDENT ID':<18} {'SEVERITY':<12} {'CATEGORY':<14} {'STATE':<16} {'TITLE'}{RESET}")
        print("─" * 86)
        if not incs:
            print(f"{GREEN}✔ No incidents matching criteria.{RESET}\n")
            return 0

        for inc in incs:
            iid = inc.get("incident_id", "")
            sev = fmt_severity(inc.get("severity", "INFO"))
            cat = inc.get("category", "")
            st = fmt_status(inc.get("state", "DETECTED"))
            title = inc.get("title", "")
            print(f"{iid:<18} {sev:<21} {cat:<14} {st:<25} {title}")
        print(f"\nTotal: {len(incs)} incident(s)\n")
        return 0

    elif sub == "diagnose":
        if not args.incident_id:
            print(f"{RED}Error:{RESET} incident_id required")
            return 1
        print(f"Triggering multi-tier AI diagnosis for {args.incident_id}...")
        try:
            r = client.post(f"/api/incidents/{args.incident_id}/diagnose")
            if r.status_code == 200:
                res = r.json()
                print(f"{GREEN}✔ Diagnosis Complete:{RESET}")
                print(f"  Summary:     {res.get('summary', 'None')}")
                print(f"  Root Cause:  {res.get('root_cause', 'Unknown')}")
                print(f"  Confidence:  {res.get('confidence', 0.0)}")
                print(f"  Remediation: {res.get('recommended_remediation', 'None')}")
                return 0
            else:
                print(f"{RED}Diagnosis failed (HTTP {r.status_code}):{RESET} {r.text}")
                return 1
        except Exception as e:
            print(f"{RED}Error:{RESET} {e}")
            return 1

    return 0


def cmd_canary(client: httpx.Client, args: argparse.Namespace) -> int:
    """Manages canary rollouts."""
    sub = args.canary_action or "list"

    if sub == "list":
        try:
            r = client.get("/api/canary/rollouts")
            if r.status_code != 200:
                print(f"{RED}Error:{RESET} {r.text}")
                return 1
            rollouts = r.json()
        except Exception as e:
            print(f"{RED}Error:{RESET} {e}")
            return 1

        print(f"\n{BOLD}{'ROLLOUT ID':<26} {'TARGET':<22} {'STATE':<14} {'STEP':<8} {'ERR RATE':<10} {'LATENCY'}{RESET}")
        print("─" * 92)
        if not rollouts:
            print(f"{DIM}No canary rollouts found.{RESET}\n")
            return 0
        for ro in rollouts:
            rid = ro.get("rollout_id", "")
            tgt = ro.get("target", "")
            st = fmt_status(ro.get("state", "UNKNOWN"))
            step = f"{ro.get('current_step', 0)}/{ro.get('total_steps', 0)}"
            err = f"{ro.get('live_error_rate', 0.0):.1%}"
            lat = f"{ro.get('live_latency_ms', 0.0):.1f}ms"
            print(f"{rid:<26} {tgt:<22} {st:<23} {step:<8} {err:<10} {lat}")
        print(f"\nTotal: {len(rollouts)} rollout(s)\n")
        return 0

    elif sub == "rollback":
        if not args.rollout_id:
            print(f"{RED}Error:{RESET} rollout_id required")
            return 1
        try:
            r = client.post(f"/api/canary/rollouts/{args.rollout_id}/rollback", json={"reason": args.reason or "CLI rollback"})
            if r.status_code == 200:
                print(f"{GREEN}✔ Rollback triggered for rollout '{args.rollout_id}'.{RESET}")
                return 0
            else:
                print(f"{RED}Rollback failed (HTTP {r.status_code}):{RESET} {r.text}")
                return 1
        except Exception as e:
            print(f"{RED}Error:{RESET} {e}")
            return 1

    return 0


def cmd_keys(client: httpx.Client, args: argparse.Namespace) -> int:
    """Manages cryptographic RBAC API keys."""
    sub = args.keys_action or "list"

    if sub == "list":
        try:
            r = client.get("/api/security/keys")
            if r.status_code != 200:
                print(f"{RED}Error:{RESET} {r.text}")
                return 1
            keys = r.json()
        except Exception as e:
            print(f"{RED}Error:{RESET} {e}")
            return 1

        print(f"\n{BOLD}{'NAME':<20} {'ROLE':<14} {'KEY PREFIX':<20} {'CREATED':<22} {'LAST USED'}{RESET}")
        print("─" * 86)
        if not keys:
            print(f"{DIM}No API keys created yet.{RESET}\n")
            return 0
        for k in keys:
            name = k.get("name", "")
            role = k.get("role", "")
            prefix = k.get("key_prefix", "")
            created = k.get("created_at", "")
            used = k.get("last_used_at") or "never"
            print(f"{name:<20} {role:<14} {prefix:<20} {created:<22} {used}")
        print(f"\nTotal: {len(keys)} key(s)\n")
        return 0

    elif sub == "create":
        if not args.name:
            print(f"{RED}Error:{RESET} --name required to create API key")
            return 1
        try:
            r = client.post("/api/security/keys", json={"name": args.name, "role": args.role or "OPERATOR"})
            if r.status_code == 200:
                res = r.json()
                print(f"{GREEN}✔ Cryptographic API Key Created:{RESET}")
                print(f"  Name:       {res.get('name')}")
                print(f"  Role:       {res.get('role')}")
                print(f"  Secret Key: {YELLOW}{BOLD}{res.get('secret_key')}{RESET}")
                print(f"\n{RED}Important:{RESET} Save this key now! The full secret is never stored or shown again.")
                return 0
            else:
                print(f"{RED}Key creation failed (HTTP {r.status_code}):{RESET} {r.text}")
                return 1
        except Exception as e:
            print(f"{RED}Error:{RESET} {e}")
            return 1

    return 0


def cmd_mode(client: httpx.Client, args: argparse.Namespace) -> int:
    """Gets or sets the autonomous operation mode."""
    if not args.new_mode:
        # Get mode from health
        try:
            r = client.get("/api/health")
            if r.status_code == 200:
                print(f"Current mode: {BOLD}{r.json().get('mode')}{RESET}")
                return 0
        except Exception as e:
            print(f"{RED}Error:{RESET} {e}")
            return 1
    else:
        try:
            r = client.post("/api/policy/mode", json={"mode": args.new_mode})
            if r.status_code == 200:
                print(f"{GREEN}✔ Mode updated to:{RESET} {BOLD}{args.new_mode}{RESET}")
                return 0
            else:
                print(f"{RED}Failed (HTTP {r.status_code}):{RESET} {r.text}")
                return 1
        except Exception as e:
            print(f"{RED}Error:{RESET} {e}")
            return 1
    return 0


def cmd_estop(client: httpx.Client, args: argparse.Namespace) -> int:
    """Gets or toggles emergency stop."""
    if not args.state:
        try:
            r = client.get("/api/health")
            if r.status_code == 200:
                val = r.json().get("emergency_stop")
                print(f"Emergency stop: {RED if val else GREEN}{BOLD}{val}{RESET}")
                return 0
        except Exception as e:
            print(f"{RED}Error:{RESET} {e}")
            return 1
    else:
        enable = args.state.lower() in ("on", "true", "1", "enable", "engaged")
        try:
            r = client.post("/api/policy/emergency_stop", json={"enabled": enable})
            if r.status_code == 200:
                state_str = f"{RED}ENGAGED{RESET}" if enable else f"{GREEN}DISENGAGED{RESET}"
                print(f"{GREEN}✔ Emergency stop {state_str}.{RESET}")
                return 0
            else:
                print(f"{RED}Failed (HTTP {r.status_code}):{RESET} {r.text}")
                return 1
        except Exception as e:
            print(f"{RED}Error:{RESET} {e}")
            return 1
    return 0


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="aegis",
        description="Aegis AI — Autonomous Infrastructure Control Plane & Developer CLI"
    )
    p.add_argument(
        "--server", "-s",
        default=os.getenv("AEGIS_SERVER_URL", "http://127.0.0.1:8000"),
        help="Aegis Server URL (default: http://127.0.0.1:8000)"
    )
    p.add_argument(
        "--token", "-t",
        default=os.getenv("AEGIS_API_TOKEN", os.getenv("AEGIS_AGENT_TOKEN")),
        help="RBAC Bearer token"
    )
    p.add_argument(
        "--version", "-v",
        action="version",
        version=f"Aegis AI v{__version__}"
    )

    sub = p.add_subparsers(dest="subcommand", help="Command to execute")

    # aegis server
    p_server = sub.add_parser("server", help="Start the Aegis control plane server")
    p_server.add_argument("--host", default=None)
    p_server.add_argument("--port", type=int, default=None)
    p_server.add_argument("--config", default=None)
    p_server.add_argument("--policies", default=None)
    p_server.add_argument("--db", default=None)
    p_server.add_argument("--log-level", default=None)

    # aegis agent [server_ip]
    p_agent = sub.add_parser("agent", help="Start the node telemetry agent daemon")
    p_agent.add_argument("server_ip", nargs="?", default=None, help="Server IP or URL (e.g. 192.168.1.100 or http://192.168.1.100:8000)")
    p_agent.add_argument("--node-id", default=None)
    p_agent.add_argument("--interval", "-i", type=float, default=5.0)
    p_agent.add_argument("--labels", "-l", default="role=worker")
    p_agent.add_argument("--once", action="store_true")
    p_agent.add_argument("--no-drain", action="store_true")

    # aegis join <server_ip>
    p_join = sub.add_parser("join", help="Join a remote Aegis Server cluster mesh")
    p_join.add_argument("server_ip", help="Server IP or URL (e.g. 192.168.1.100 or http://192.168.1.100:8000)")
    p_join.add_argument("--node-id", default=None)
    p_join.add_argument("--interval", "-i", type=float, default=5.0)
    p_join.add_argument("--labels", "-l", default="role=worker")
    p_join.add_argument("--once", action="store_true")
    p_join.add_argument("--no-drain", action="store_true")

    # aegis status
    sub.add_parser("status", help="Show overview of health, guardians and cluster")

    # aegis nodes
    p_nodes = sub.add_parser("nodes", help="Manage cluster mesh nodes")
    sub_nodes = p_nodes.add_subparsers(dest="nodes_action")
    sub_nodes.add_parser("list", help="List registered nodes")
    p_drain = sub_nodes.add_parser("drain", help="Drain a cluster node")
    p_drain.add_argument("node_id", help="Node ID to drain")
    p_drain.add_argument("--reason", default="Manual operator drain")

    # aegis incidents
    p_inc = sub.add_parser("incidents", help="Manage incidents")
    sub_inc = p_inc.add_subparsers(dest="incidents_action")
    p_inc_list = sub_inc.add_parser("list", help="List incidents")
    p_inc_list.add_argument("--state", choices=["DETECTED", "DIAGNOSING", "REPAIRING", "CONTAINED", "NEEDS_HUMAN_APPROVAL", "RESOLVED"])
    p_inc_list.add_argument("--limit", type=int, default=25)
    p_diag = sub_inc.add_parser("diagnose", help="Trigger AI diagnosis for an incident")
    p_diag.add_argument("incident_id", help="Incident ID")

    # aegis canary
    p_canary = sub.add_parser("canary", help="Manage canary rollouts")
    sub_canary = p_canary.add_subparsers(dest="canary_action")
    sub_canary.add_parser("list", help="List canary rollouts")
    p_canary_rb = sub_canary.add_parser("rollback", help="Trigger canary rollback")
    p_canary_rb.add_argument("rollout_id", help="Rollout ID")
    p_canary_rb.add_argument("--reason", default="Manual rollback from CLI")

    # aegis keys
    p_keys = sub.add_parser("keys", help="Manage cryptographic RBAC API keys")
    sub_keys = p_keys.add_subparsers(dest="keys_action")
    sub_keys.add_parser("list", help="List API keys")
    p_keys_create = sub_keys.add_parser("create", help="Create a new API key")
    p_keys_create.add_argument("--name", "-n", required=True, help="Friendly name")
    p_keys_create.add_argument("--role", "-r", default="OPERATOR", choices=["ADMIN", "OPERATOR", "READONLY", "NODE_AGENT"])

    # aegis mode
    p_mode = sub.add_parser("mode", help="Query or update autonomous policy mode")
    p_mode.add_argument("new_mode", nargs="?", choices=["monitoring_only", "operator_approval", "policy_autonomous"])

    # aegis estop
    p_estop = sub.add_parser("estop", help="Query or toggle emergency stop")
    p_estop.add_argument("state", nargs="?", choices=["on", "off", "enable", "disable"])

    args = p.parse_args(argv)

    if not args.subcommand:
        # Default to status if no command provided
        client = get_client(args.server, args.token)
        sys.exit(cmd_status(client, args))

    if args.subcommand == "server":
        from aegis.main import build_context, run
        ctx = build_context(args)
        run(ctx, host=args.host, port=args.port)
        return

    if args.subcommand in ("agent", "join"):
        from aegis.client.agent import AegisAgent, normalize_server_url
        server_target = getattr(args, "server_ip", None) or args.server or os.getenv("AEGIS_SERVER_URL", "http://127.0.0.1:8000")
        server_target = normalize_server_url(server_target)
        labels: dict[str, str] = {}
        if args.labels:
            for part in args.labels.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    labels[k.strip()] = v.strip()
        agent = AegisAgent(
            server_url=server_target,
            token=args.token,
            node_id=args.node_id,
            labels=labels,
            interval_s=args.interval,
            drain_on_stop=not args.no_drain,
        )
        if args.once:
            ok = agent.run_once()
            sys.exit(0 if ok else 1)
        else:
            agent.run()
        return

    client = get_client(args.server, args.token)

    if args.subcommand == "status":
        sys.exit(cmd_status(client, args))
    elif args.subcommand == "nodes":
        sys.exit(cmd_nodes(client, args))
    elif args.subcommand == "incidents":
        sys.exit(cmd_incidents(client, args))
    elif args.subcommand == "canary":
        sys.exit(cmd_canary(client, args))
    elif args.subcommand == "keys":
        sys.exit(cmd_keys(client, args))
    elif args.subcommand == "mode":
        sys.exit(cmd_mode(client, args))
    elif args.subcommand == "estop":
        sys.exit(cmd_estop(client, args))


if __name__ == "__main__":
    main()
