#!/usr/bin/env python3
"""
Bulwark Management CLI
Entry point for all infrastructure management operations.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Ensure stdout/stderr can handle Unicode on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

# Ensure project root is on sys.path regardless of how cli.py is invoked
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env early so env vars are available to all modules on import
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    with open(_env_file) as _fh:
        for _line in _fh:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

from core.node_manager import NodeManager, Node
from core.config_gen import ConfigGenerator
from core.deployer import NodeDeployer
from core.bridge_manager import BridgeManager

console = Console()
err_console = Console(stderr=True, style="red")


# ---------------------------------------------------------------------------
# Lazy singletons — created on first access to avoid import-time I/O
# ---------------------------------------------------------------------------

_nm: NodeManager | None = None
_cg: ConfigGenerator | None = None
_deployer: NodeDeployer | None = None
_bridge: BridgeManager | None = None


def get_nm() -> NodeManager:
    global _nm
    if _nm is None:
        _nm = NodeManager()
    return _nm


def get_cg() -> ConfigGenerator:
    global _cg
    if _cg is None:
        _cg = ConfigGenerator()
    return _cg


def get_deployer() -> NodeDeployer:
    global _deployer
    if _deployer is None:
        _deployer = NodeDeployer(get_nm(), get_cg())
    return _deployer


def get_bridge() -> BridgeManager:
    global _bridge
    if _bridge is None:
        _bridge = BridgeManager(get_nm(), get_cg())
    return _bridge


def resolve_node(name: str) -> Node:
    """Get a node by name with friendly error message."""
    try:
        return get_nm().get_node(name)
    except KeyError as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version="1.0.0", prog_name="bulwark")
def cli():
    """
    Bulwark — node management CLI.

    Manages VLESS+Reality, Hysteria2 and AmneziaWG across your node network.
    """


# ===========================================================================
# bulwark status [node]
# ===========================================================================

@cli.command("status")
@click.argument("node_name", required=False, metavar="[NODE]")
def cmd_status(node_name: str | None):
    """Show status of all nodes, or detailed status for a single NODE."""
    from rich.spinner import Spinner
    from rich.live import Live

    nm = get_nm()

    if node_name:
        node = resolve_node(node_name)
        _show_node_detail(node)
    else:
        _show_all_nodes(nm)


def _show_all_nodes(nm: NodeManager) -> None:
    """Print a rich table of all nodes."""
    from core.monitor import NodeMonitor, NodeStatus
    import asyncio

    nodes = nm.all_nodes()

    table = Table(
        title="Bulwark — Node Overview",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        expand=False,
    )
    table.add_column("Node", style="bold white", min_width=20)
    table.add_column("Role", justify="center", min_width=8)
    table.add_column("IP", min_width=16)
    table.add_column("Region", justify="center", min_width=8)
    table.add_column("Priority", justify="center", min_width=8)
    table.add_column("Protocols", min_width=30)
    table.add_column("Enabled", justify="center", min_width=8)

    role_style = {"bridge": "yellow", "exit": "green"}

    for node in nodes:
        role_text = Text(node.role.upper(), style=role_style.get(node.role, "white"))
        enabled_text = Text("✓", style="green") if node.enabled else Text("✗", style="red")
        protos = ", ".join(node.protocols) if node.protocols else "—"
        table.add_row(
            node.display_name,
            role_text,
            node.ip,
            node.region.upper(),
            str(node.priority),
            protos,
            enabled_text,
        )

    console.print()
    console.print(table)
    console.print()
    console.print(
        "[dim]Run [bold]bulwark status <node>[/bold] for live connectivity check on a single node.[/dim]"
    )


def _show_node_detail(node: Node) -> None:
    """Run a live connectivity check and show detailed results."""
    import asyncio
    from core.monitor import NodeMonitor, NodeStatus
    from core.bridge_manager import BridgeManager
    from rich.progress import Progress, SpinnerColumn, TextColumn

    console.print()
    console.print(Panel(
        f"[bold cyan]{node.display_name}[/bold cyan]  [dim]{node.ip}[/dim]",
        title="Node Detail",
        expand=False,
    ))

    # Run async checks in a sync context
    async def _do_checks():
        import yaml
        cfg_path = PROJECT_ROOT / "config" / "global.yaml"
        global_cfg = {}
        if cfg_path.exists():
            with open(cfg_path) as fh:
                global_cfg = yaml.safe_load(fh) or {}

        monitor = NodeMonitor(get_nm(), get_bridge(), global_cfg)
        return await monitor.run_checks(node)

    with console.status("[bold green]Running health checks...", spinner="dots"):
        status = asyncio.run(_do_checks())

    # Build result table
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Check", style="bold white", min_width=20)
    table.add_column("Status", justify="center", min_width=10)
    table.add_column("Latency", justify="right", min_width=10)
    table.add_column("Detail", min_width=30)

    # ICMP row
    icmp_status = Text("UP", style="bold green") if status.icmp_ok else Text("DOWN", style="bold red")
    icmp_latency = f"{status.icmp_latency_ms:.1f} ms" if status.icmp_latency_ms else "—"
    table.add_row("ICMP (ping)", icmp_status, icmp_latency, "")

    # Protocol rows
    for proto_name, ps in status.protocol_statuses.items():
        ps_status = Text("UP", style="bold green") if ps.healthy else Text("DOWN", style="bold red")
        ps_latency = f"{ps.latency_ms:.1f} ms" if ps.latency_ms else "—"
        ps_detail = ps.error or ""
        table.add_row(proto_name, ps_status, ps_latency, ps_detail)

    console.print(table)

    overall = Text("HEALTHY", style="bold green") if status.overall_healthy else Text("UNHEALTHY", style="bold red")
    console.print(f"\nOverall: {overall}")
    if status.consecutive_failures > 0:
        console.print(f"[yellow]Consecutive failures: {status.consecutive_failures}[/yellow]")
    console.print()


# ===========================================================================
# bulwark deploy <node>
# ===========================================================================

@cli.command("deploy")
@click.argument("node_name", metavar="NODE")
@click.option("--protocol", "-p",
              type=click.Choice(["xray", "hysteria2", "amneziawg", "all"]),
              default="all", show_default=True,
              help="Deploy only a specific protocol.")
@click.option("--skip-base", is_flag=True, default=False,
              help="Skip the base system preparation step.")
def cmd_deploy(node_name: str, protocol: str, skip_base: bool):
    """Deploy the full protocol stack to NODE (or a specific protocol)."""
    node = resolve_node(node_name)
    deployer = get_deployer()

    if node.is_bridge:
        console.print("[yellow]Bridge nodes don't run exit protocols. Use 'bulwark bridge update' instead.[/yellow]")
        sys.exit(0)

    console.print(Panel(
        f"Deploying [bold]{protocol}[/bold] to [bold cyan]{node.display_name}[/bold cyan] ({node.ip})",
        title="Deploy",
        expand=False,
    ))

    try:
        if not skip_base and protocol == "all":
            with console.status("Running base installation..."):
                deployer.deploy_base(node)
            console.print("[green]✓ Base installation complete[/green]")

        if protocol in ("xray", "all"):
            with console.status("Deploying xray-core (VLESS+Reality)..."):
                deployer.deploy_xray(node)
            console.print("[green]✓ xray deployed[/green]")

        if protocol in ("hysteria2", "all"):
            with console.status("Deploying Hysteria2..."):
                deployer.deploy_hysteria2(node)
            console.print("[green]✓ Hysteria2 deployed[/green]")

        if protocol in ("amneziawg", "all"):
            with console.status("Deploying AmneziaWG..."):
                deployer.deploy_amneziawg(node)
            console.print("[green]✓ AmneziaWG deployed[/green]")

        # --- Post-deploy automation ---
        console.print()
        _post_deploy_bridges()
        _post_deploy_sub_push()
        _post_deploy_bot_sync()

        # Send Telegram notification
        _notify_deploy_complete(node)

        console.print()
        console.print(f"[bold green]Deployment to {node.display_name} complete![/bold green]")

    except Exception as exc:
        _notify_deploy_failed(node, str(exc))
        err_console.print(f"[bold red]Deployment failed:[/bold red] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Post-deploy helpers — called automatically after cmd_deploy
# ---------------------------------------------------------------------------

BOT_SYNC_FILES = [
    "cli.py",
    "requirements.txt",
    "core/__init__.py",
    "core/node_manager.py",
    "core/deployer.py",
    "core/config_gen.py",
    "core/bridge_manager.py",
    "core/monitor.py",
    "core/telegram.py",
    "core/stats.py",
    "core/awg_users.py",
    "config/global.yaml",
    "config/nodes.yaml",
]

BOT_SYNC_DIRS = [
    "core/bot",
    "config/secrets",
    "config/templates",
]


def _post_deploy_bridges() -> None:
    """Regenerate and push bridge config to all bridges."""
    try:
        with console.status("Обновление bridge-конфигов..."):
            get_bridge().update_bridge()
        console.print("[green]✓ Bridge configs updated[/green]")
    except Exception as exc:
        console.print(f"[yellow]⚠ Bridge update failed: {exc}[/yellow]")


def _post_deploy_sub_push() -> None:
    """Regenerate subscription files and upload to management bridge portal."""
    import json as _json
    import urllib.parse as _urlparse
    from datetime import datetime as _dt

    nm, cg = get_nm(), get_cg()
    sub_uuid = os.environ.get("SUBSCRIPTION_UUID", "")
    if not sub_uuid:
        console.print("[yellow]⚠ SUBSCRIPTION_UUID not set — skipping sub push[/yellow]")
        return
    exit_nodes = nm.exit_nodes()
    bridge_nodes = nm.bridge_nodes()
    if not bridge_nodes:
        console.print("[yellow]⚠ No bridge nodes — skipping sub push[/yellow]")
        return

    try:
        with console.status("Генерация и push подписки на портал..."):
            content = cg.generate_subscription(exit_nodes, bridge_nodes=bridge_nodes)
            raw_uris = cg.generate_subscription_plain(exit_nodes, bridge_nodes=bridge_nodes)

            # connections.json
            conn_entries = []
            for uri in raw_uris:
                label = _urlparse.unquote(uri.split("#")[-1]) if "#" in uri else ""
                proto = uri.split("://")[0]
                parts = label.split(" | ")
                conn_entries.append({
                    "uri": uri, "label": label, "protocol": proto,
                    "region": parts[1].strip() if len(parts) > 1 else "",
                    "node": parts[2].strip() if len(parts) > 2 else "",
                    "type": parts[3].strip() if len(parts) > 3 else "",
                    "via_bridge": "via" in label.lower(),
                })
            connections_json = _json.dumps({
                "updated_at": _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "uris": conn_entries,
            }, ensure_ascii=False, indent=2)

            # awg_info.json
            global_cfg_awg = cg._load_global().get("amneziawg", {})
            awg_nodes = {}
            for nd in exit_nodes:
                sec = cg.load_secrets(nd.name)
                if sec.get("awg_public_key"):
                    peers = sec.get("awg_peers", [])
                    vpn_links = []
                    for p in peers:
                        if p.get("private_key"):
                            link = cg.generate_amneziawg_vpn_link(nd, sec, p)
                            vpn_links.append({
                                "name": p.get("name", "default"),
                                "address": p.get("address", ""),
                                "vpn_link": link,
                            })
                    awg_nodes[nd.name] = {
                        "display_name": nd.display_name,
                        "region": nd.region.upper(),
                        "endpoint": f"{nd.ip}:51820",
                        "public_key": sec["awg_public_key"],
                        "jc": int(global_cfg_awg.get("jc", 4)),
                        "jmin": int(global_cfg_awg.get("jmin", 40)),
                        "jmax": int(global_cfg_awg.get("jmax", 70)),
                        "s1": int(sec.get("awg_s1", 0)),
                        "s2": int(sec.get("awg_s2", 0)),
                        "h1": int(sec.get("awg_h1", 1)),
                        "h2": int(sec.get("awg_h2", 2)),
                        "h3": int(sec.get("awg_h3", 3)),
                        "h4": int(sec.get("awg_h4", 4)),
                        "peers": vpn_links,
                    }
            awg_json = _json.dumps(awg_nodes, ensure_ascii=False, indent=2)

            # Write locally
            runtime_dir = PROJECT_ROOT / "deploy" / "portal" / "runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            (runtime_dir / "subscription.b64").write_text(content, encoding="utf-8")
            (runtime_dir / "connections.json").write_text(connections_json, encoding="utf-8")
            (runtime_dir / "awg_info.json").write_text(awg_json, encoding="utf-8")

            # Upload to management bridge
            mgmt = bridge_nodes[0]
            remote_rt = "/opt/bulwark/deploy/portal/runtime"
            with nm.ssh(mgmt) as conn:
                for fname in ("subscription.b64", "connections.json", "awg_info.json"):
                    conn.upload_file(str(runtime_dir / fname), f"{remote_rt}/{fname}")

        console.print(f"[green]✓ Subscription pushed ({len(raw_uris)} URIs)[/green]")
    except Exception as exc:
        console.print(f"[yellow]⚠ Sub push failed: {exc}[/yellow]")


def _post_deploy_bot_sync() -> None:
    """Sync core project files to management bridge and restart service."""
    nm = get_nm()
    bridge_nodes = nm.bridge_nodes()
    if not bridge_nodes:
        console.print("[yellow]⚠ No bridge nodes — skipping bot sync[/yellow]")
        return

    mgmt = bridge_nodes[0]
    remote_root = "/opt/bulwark"

    try:
        with console.status(f"Синхронизация файлов на {mgmt.display_name}..."):
            with nm.ssh(mgmt) as conn:
                # Sync individual files
                for rel in BOT_SYNC_FILES:
                    local = PROJECT_ROOT / rel
                    if local.exists():
                        conn.upload_file(str(local), f"{remote_root}/{rel}")

                # Sync directories (bot package, secrets, templates)
                for rel_dir in BOT_SYNC_DIRS:
                    local_dir = PROJECT_ROOT / rel_dir
                    if not local_dir.exists():
                        continue
                    for local_file in local_dir.rglob("*"):
                        if local_file.is_file() and "__pycache__" not in str(local_file):
                            rel = local_file.relative_to(PROJECT_ROOT).as_posix()
                            conn.upload_file(str(local_file), f"{remote_root}/{rel}")

                # Restart management service if active
                _, _, rc = conn.exec("systemctl is-active bulwark-monitor 2>/dev/null")
                if rc == 0:
                    conn.exec("systemctl restart bulwark-monitor")

        console.print(f"[green]✓ Files synced to {mgmt.display_name}[/green]")
    except Exception as exc:
        console.print(f"[yellow]⚠ Bot sync failed: {exc}[/yellow]")


def _notify_deploy_complete(node: Node) -> None:
    async def _send():
        from core.telegram import TelegramNotifier
        notifier = TelegramNotifier()
        if notifier.enabled:
            await notifier.notify_deploy_complete(node)
            await notifier.close()
    try:
        asyncio.run(_send())
    except Exception:
        pass


def _notify_deploy_failed(node: Node, error: str) -> None:
    async def _send():
        from core.telegram import TelegramNotifier
        notifier = TelegramNotifier()
        if notifier.enabled:
            await notifier.notify_deploy_failed(node, error)
            await notifier.close()
    try:
        asyncio.run(_send())
    except Exception:
        pass


# ===========================================================================
# bulwark redeploy <node>
# ===========================================================================

@cli.command("redeploy")
@click.argument("node_name", metavar="NODE")
def cmd_redeploy(node_name: str):
    """Re-upload configs and restart services on NODE (no binary reinstall)."""
    node = resolve_node(node_name)
    deployer = get_deployer()

    console.print(Panel(
        f"Redeploying configs to [bold cyan]{node.display_name}[/bold cyan] ({node.ip})",
        title="Redeploy",
        expand=False,
    ))

    try:
        with console.status("Redeploying..."):
            deployer.redeploy(node)
        console.print(f"[bold green]Redeploy to {node.display_name} complete![/bold green]")
    except Exception as exc:
        err_console.print(f"[bold red]Redeploy failed:[/bold red] {exc}")
        sys.exit(1)


# ===========================================================================
# bulwark config <subcommand>
# ===========================================================================

@cli.group("config")
def grp_config():
    """Manage node configurations."""


@grp_config.command("sync")
@click.argument("node_name", metavar="NODE")
def cmd_config_sync(node_name: str):
    """Upload updated configs to NODE without restarting services."""
    node = resolve_node(node_name)
    deployer = get_deployer()

    try:
        with console.status(f"Syncing config to {node.display_name}..."):
            deployer.sync_config(node)
        console.print(
            f"[green]✓ Config synced to {node.display_name}. "
            f"Files written as *.pending — restart services to apply.[/green]"
        )
    except Exception as exc:
        err_console.print(f"[bold red]Config sync failed:[/bold red] {exc}")
        sys.exit(1)


@grp_config.command("show")
@click.argument("node_name", metavar="NODE")
@click.option("--protocol", "-p",
              type=click.Choice(["xray", "hysteria2", "amneziawg"]),
              default="xray", show_default=True,
              help="Which protocol config to show.")
def cmd_config_show(node_name: str, protocol: str):
    """Show the current remote config for NODE."""
    node = resolve_node(node_name)
    deployer = get_deployer()

    try:
        with console.status(f"Fetching {protocol} config from {node.display_name}..."):
            content = deployer.show_remote_config(node, protocol)
        console.print(Panel(content, title=f"{node.display_name} — {protocol} config", expand=False))
    except Exception as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)


# ===========================================================================
# bulwark monitor
# ===========================================================================

@cli.command("monitor")
def cmd_monitor():
    """Start the monitoring daemon (blocking). Ctrl-C to stop."""
    import yaml
    from core.monitor import NodeMonitor
    from core.telegram import TelegramNotifier

    cfg_path = PROJECT_ROOT / "config" / "global.yaml"
    global_cfg = {}
    if cfg_path.exists():
        with open(cfg_path) as fh:
            global_cfg = yaml.safe_load(fh) or {}

    nm = get_nm()
    bm = get_bridge()

    notifier = TelegramNotifier()
    monitor = NodeMonitor(nm, bm, global_cfg, notifier=notifier)

    console.print(Panel(
        "[bold green]Bulwark Monitor starting...[/bold green]\n"
        f"Interval: [cyan]{monitor.monitor_interval}s[/cyan]  "
        f"Failover threshold: [cyan]{monitor.failover_threshold} failures[/cyan]\n"
        "Press [bold]Ctrl-C[/bold] to stop.",
        title="Monitor",
        expand=False,
    ))

    async def run():
        try:
            await monitor.monitor_loop()
        except KeyboardInterrupt:
            monitor.stop()
            if notifier:
                await notifier.close()
            console.print("\n[yellow]Monitor stopped.[/yellow]")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Monitor stopped.[/yellow]")


# ===========================================================================
# bulwark bridge <subcommand>
# ===========================================================================

@cli.group("bridge")
def grp_bridge():
    """Manage bridge nodes."""


@grp_bridge.command("update")
def cmd_bridge_update():
    """Regenerate and push bridge config to all bridges."""
    bm = get_bridge()
    nm = get_nm()

    active = bm.get_active_routes()
    console.print(Panel(
        f"Updating bridge config. Active exit nodes: [cyan]{', '.join(n.name for n in active)}[/cyan]",
        title="Bridge Update",
        expand=False,
    ))

    try:
        with console.status("Generating and uploading bridge config..."):
            bm.update_bridge()
        console.print("[bold green]Bridge config updated successfully.[/bold green]")
    except Exception as exc:
        err_console.print(f"[bold red]Bridge update failed:[/bold red] {exc}")
        sys.exit(1)


@grp_bridge.command("deploy")
@click.argument("node_name", metavar="NODE")
def cmd_bridge_deploy(node_name: str):
    """Install xray and push bridge config to a new bridge NODE."""
    node = resolve_node(node_name)
    if not node.is_bridge:
        err_console.print(f"[bold red]{node_name} is not a bridge node.[/bold red]")
        sys.exit(1)
    deployer = get_deployer()
    bm = get_bridge()

    console.print(Panel(
        f"Deploying bridge: [bold cyan]{node.display_name}[/bold cyan] ({node.ip})",
        title="Bridge Deploy",
        expand=False,
    ))

    try:
        with console.status("Running base installation..."):
            deployer.deploy_base(node)
        console.print("[green]✓ Base installation complete[/green]")

        with console.status("Installing xray binary..."):
            deployer.install_xray_binary(node)
        console.print("[green]✓ xray binary installed[/green]")

        with console.status("Generating secrets..."):
            deployer.ensure_node_secrets(node)
        console.print("[green]✓ Secrets generated[/green]")

        with console.status("Pushing bridge config to all bridges..."):
            bm.update_bridge()
        console.print("[green]✓ Bridge config pushed to all bridges[/green]")

        console.print(f"\n[bold green]Bridge {node.display_name} deployed successfully![/bold green]")
    except Exception as exc:
        err_console.print(f"[bold red]Bridge deploy failed:[/bold red] {exc}")
        sys.exit(1)


@grp_bridge.command("routes")
def cmd_bridge_routes():
    """Show current active routing in the bridge."""
    bm = get_bridge()
    active = bm.get_active_routes()
    disabled = bm.get_disabled_nodes()

    table = Table(title="Bridge Active Routes", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Node", min_width=22)
    table.add_column("IP", min_width=16)
    table.add_column("Region", justify="center", min_width=8)
    table.add_column("Priority", justify="center")
    table.add_column("Status", justify="center")

    for node in get_nm().exit_nodes():
        if node.name in disabled:
            status = Text("DISABLED (failover)", style="red")
        else:
            status = Text("ACTIVE", style="green")
        table.add_row(node.display_name, node.ip, node.region.upper(), str(node.priority), status)

    console.print()
    console.print(table)
    console.print()


# ===========================================================================
# bulwark node <subcommand>
# ===========================================================================

@cli.group("node")
def grp_node():
    """Manage node inventory."""


@grp_node.command("list")
def cmd_node_list():
    """List all nodes defined in nodes.yaml."""
    nm = get_nm()
    nodes = nm.all_nodes()

    table = Table(
        title="Node Inventory",
        box=box.ROUNDED,
        header_style="bold cyan",
        expand=False,
    )
    table.add_column("#", justify="right", style="dim", min_width=3)
    table.add_column("Name", min_width=20)
    table.add_column("Display Name", min_width=22)
    table.add_column("Role", justify="center")
    table.add_column("IP", min_width=16)
    table.add_column("Region", justify="center")
    table.add_column("Priority", justify="center")
    table.add_column("Protocols", min_width=28)
    table.add_column("Enabled", justify="center")

    for i, node in enumerate(nodes, 1):
        enabled = Text("✓", style="bold green") if node.enabled else Text("✗", style="red")
        protos = ", ".join(node.protocols) if node.protocols else "—"
        role_style = "yellow" if node.is_bridge else "green"
        table.add_row(
            str(i),
            node.name,
            node.display_name,
            Text(node.role, style=role_style),
            node.ip,
            node.region.upper(),
            str(node.priority),
            protos,
            enabled,
        )

    console.print()
    console.print(table)
    console.print()


@grp_node.command("add")
def cmd_node_add():
    """Interactive wizard to add a new node to nodes.yaml."""
    import yaml
    from rich.prompt import Prompt, Confirm

    console.print(Panel("[bold]Add New Node[/bold]", expand=False))

    name = Prompt.ask("Node name (snake_case, e.g. amsterdam_primary)")
    display_name = Prompt.ask("Display name", default=name.replace("_", " ").title())
    ip = Prompt.ask("IP address")
    role = Prompt.ask("Role", choices=["exit", "bridge"], default="exit")
    region = Prompt.ask("Region code (e.g. nl, de, us)", default="unknown")
    priority = int(Prompt.ask("Priority (1=primary, 2=fallback)", default="1"))
    ssh_port = int(Prompt.ask("SSH port", default="22"))
    ssh_user = Prompt.ask("SSH user", default="root")
    ssh_key_path = Prompt.ask("SSH key path", default="~/.ssh/df_rsa")

    protocols = []
    if role == "exit":
        if Confirm.ask("Enable VLESS+Reality?", default=True):
            protocols.append("vless_reality")
        if Confirm.ask("Enable Hysteria2?", default=True):
            protocols.append("hysteria2")
        if Confirm.ask("Enable AmneziaWG?", default=True):
            protocols.append("amneziawg")

    enabled = Confirm.ask("Enable node?", default=True)

    new_node = {
        "name": name,
        "display_name": display_name,
        "ip": ip,
        "ssh_port": ssh_port,
        "ssh_user": ssh_user,
        "ssh_key_path": ssh_key_path,
        "role": role,
        "region": region,
        "priority": priority,
        "enabled": enabled,
        "protocols": protocols,
        "description": f"{display_name} — added via bulwark CLI",
    }

    nodes_path = PROJECT_ROOT / "config" / "nodes.yaml"
    with open(nodes_path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if name in data["nodes"]:
        overwrite = Confirm.ask(
            f"[yellow]Node '{name}' already exists. Overwrite?[/yellow]", default=False
        )
        if not overwrite:
            console.print("[yellow]Aborted.[/yellow]")
            return

    data["nodes"][name] = {k: v for k, v in new_node.items() if k != "name"}

    with open(nodes_path, "w", encoding="utf-8") as fh:
        yaml.dump(data, fh, default_flow_style=False, allow_unicode=True, sort_keys=False)

    console.print(f"\n[bold green]Node '{name}' added to nodes.yaml.[/bold green]")
    console.print(f"Deploy with: [bold]bulwark deploy {name}[/bold]")


# ===========================================================================
# bulwark sub <subcommand>
# ===========================================================================

@cli.group("sub")
def grp_sub():
    """Manage subscriptions."""


@grp_sub.command("show")
def cmd_sub_show():
    """Show all proxy URIs that would go into the subscription (human-readable)."""
    nm = get_nm()
    cg = get_cg()
    uris = cg.generate_subscription_plain(nm.exit_nodes(), bridge_nodes=nm.bridge_nodes())

    if not uris:
        console.print("[yellow]No URIs generated — deploy nodes first to create secrets.[/yellow]")
        return

    table = Table(
        title="Subscription URIs",
        box=box.ROUNDED,
        header_style="bold cyan",
        show_lines=True,
    )
    table.add_column("#", justify="right", style="dim", min_width=3)
    table.add_column("Protocol", min_width=12)
    table.add_column("URI", overflow="fold")

    for i, uri in enumerate(uris, 1):
        if uri.startswith("vless://"):
            proto = "VLESS+Reality via Bridge" if "via" in uri.split("#")[-1] else "VLESS+Reality"
        elif uri.startswith("hysteria2://"):
            proto = "Hysteria2"
        else:
            proto = uri.split("://")[0].upper()
        table.add_row(str(i), proto, uri)

    console.print()
    console.print(table)
    console.print(
        f"\n[dim]Subscription URL (unchanged): "
        f"[bold]{get_cg()._load_global().get('subscription', {}).get('base_url', '')}{os.environ.get('SUBSCRIPTION_UUID', '<SUBSCRIPTION_UUID>')}[/bold][/dim]"
    )
    console.print()


@grp_sub.command("generate")
@click.option("--output", "-o", default="-", help="Output file path, or - for stdout.")
def cmd_sub_generate(output: str):
    """Generate base64-encoded subscription content (V2Ray format)."""
    nm = get_nm()
    cg = get_cg()
    content = cg.generate_subscription(nm.exit_nodes(), bridge_nodes=nm.bridge_nodes())

    if output == "-":
        console.print(content)
    else:
        Path(output).write_text(content, encoding="utf-8")
        console.print(f"[green]✓ Subscription written to {output}[/green]")


@grp_sub.command("push")
def cmd_sub_push():
    """
    Push updated subscription to the management bridge portal.

    Writes:
      - runtime/subscription.b64  — base64 V2Ray subscription
      - runtime/connections.json  — decoded URI list for portal display
      - runtime/awg_info.json     — AWG server params per exit node
    """
    import json as _json
    import base64 as _b64
    import urllib.parse as _urlparse
    from datetime import datetime as _dt

    nm = get_nm()
    cg = get_cg()

    sub_uuid = os.environ.get("SUBSCRIPTION_UUID", "")
    if not sub_uuid:
        err_console.print(
            "[bold red]Error:[/bold red] SUBSCRIPTION_UUID not set in .env"
        )
        sys.exit(1)

    exit_nodes = nm.exit_nodes()
    bridge_nodes = nm.bridge_nodes()

    content = cg.generate_subscription(exit_nodes, bridge_nodes=bridge_nodes)
    if not bridge_nodes:
        err_console.print("[bold red]Error:[/bold red] No bridge node found in nodes.yaml")
        sys.exit(1)

    mgmt_bridge = bridge_nodes[0]

    # --- Build connections.json (URIs for portal display) ---
    raw_uris = cg.generate_subscription_plain(exit_nodes, bridge_nodes=bridge_nodes)
    conn_entries = []
    for uri in raw_uris:
        label = _urlparse.unquote(uri.split("#")[-1]) if "#" in uri else ""
        proto = uri.split("://")[0]
        parts = label.split(" | ")
        conn_entries.append({
            "uri": uri,
            "label": label,
            "protocol": proto,
            "region": parts[1].strip() if len(parts) > 1 else "",
            "node": parts[2].strip() if len(parts) > 2 else "",
            "type": parts[3].strip() if len(parts) > 3 else "",
            "via_bridge": "via" in label.lower(),
        })
    connections_json = _json.dumps({
        "updated_at": _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "uris": conn_entries,
    }, ensure_ascii=False, indent=2)

    # --- Build awg_info.json ---
    global_cfg_awg = cg._load_global().get("amneziawg", {})
    awg_nodes = {}
    for node in exit_nodes:
        secrets = cg.load_secrets(node.name)
        if secrets.get("awg_public_key"):
            peers = secrets.get("awg_peers", [])
            # Generate vpn:// links for all peers that have a private key
            vpn_links = []
            for peer in peers:
                if peer.get("private_key"):
                    link = cg.generate_amneziawg_vpn_link(node, secrets, peer)
                    vpn_links.append({
                        "name": peer.get("name", "default"),
                        "address": peer.get("address", ""),
                        "vpn_link": link,
                    })
            awg_nodes[node.name] = {
                "display_name": node.display_name,
                "region": node.region.upper(),
                "endpoint": f"{node.ip}:51820",
                "public_key": secrets["awg_public_key"],
                "jc": int(global_cfg_awg.get("jc", 4)),
                "jmin": int(global_cfg_awg.get("jmin", 40)),
                "jmax": int(global_cfg_awg.get("jmax", 70)),
                "s1": int(secrets.get("awg_s1", 0)),
                "s2": int(secrets.get("awg_s2", 0)),
                "h1": int(secrets.get("awg_h1", 1)),
                "h2": int(secrets.get("awg_h2", 2)),
                "h3": int(secrets.get("awg_h3", 3)),
                "h4": int(secrets.get("awg_h4", 4)),
                "peers": vpn_links,
            }
    awg_json = _json.dumps(awg_nodes, ensure_ascii=False, indent=2)

    runtime_dir = PROJECT_ROOT / "deploy" / "portal" / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    try:
        (runtime_dir / "subscription.b64").write_text(content, encoding="utf-8")
        (runtime_dir / "connections.json").write_text(connections_json, encoding="utf-8")
        (runtime_dir / "awg_info.json").write_text(awg_json, encoding="utf-8")

        console.print(f"[green]✓ subscription.b64 pushed ({len(raw_uris)} URIs)[/green]")
        console.print(f"[green]✓ connections.json pushed[/green]")
        console.print(f"[green]✓ awg_info.json pushed ({len(awg_nodes)} nodes)[/green]")
        console.print(
            f"[dim]Verify: [bold]{cg._load_global().get('subscription', {}).get('base_url', '')}{sub_uuid}[/bold][/dim]"
        )
    except Exception as exc:
        err_console.print(f"[bold red]Push failed:[/bold red] {exc}")
        sys.exit(1)


# ===========================================================================
# bulwark secrets <subcommand>
# ===========================================================================

@cli.group("secrets")
def grp_secrets():
    """Manage node secrets (UUIDs, keys, passwords)."""


@grp_secrets.command("show")
@click.argument("node_name", metavar="NODE")
def cmd_secrets_show(node_name: str):
    """Show generated secrets for NODE."""
    node = resolve_node(node_name)
    deployer = get_deployer()
    secrets = deployer.load_node_secrets(node.name)

    if not secrets:
        console.print(
            f"[yellow]No secrets found for '{node.name}'. "
            f"Run 'bulwark deploy {node.name}' first.[/yellow]"
        )
        return

    table = Table(
        title=f"Secrets — {node.display_name}",
        box=box.ROUNDED,
        header_style="bold cyan",
        expand=False,
    )
    table.add_column("Key", style="bold white", min_width=25)
    table.add_column("Value", min_width=50, overflow="fold")

    for key, value in sorted(secrets.items()):
        if isinstance(value, list):
            value_str = "\n".join(
                f"{item.get('name', '?')}: {item.get('uuid', item.get('public_key', '?'))}"
                for item in value
            ) if value else "[]"
        elif isinstance(value, dict):
            value_str = "\n".join(f"{k}: {v}" for k, v in value.items())
        else:
            value_str = str(value)
        table.add_row(key, value_str)

    console.print()
    console.print(table)
    console.print()
    console.print(
        "[dim]Secrets are stored in [bold]config/secrets/<node>.yaml[/bold] "
        "(git-ignored).[/dim]"
    )


@grp_secrets.command("regenerate")
@click.argument("node_name", metavar="NODE")
@click.option("--key", "-k", multiple=True,
              help="Specific secret key(s) to regenerate. Omit to regenerate all.")
@click.confirmation_option(prompt="This will overwrite existing secrets. Continue?")
def cmd_secrets_regenerate(node_name: str, key: tuple[str, ...]):
    """Regenerate secrets for NODE (will require redeploy afterwards)."""
    node = resolve_node(node_name)
    deployer = get_deployer()
    secrets_path = PROJECT_ROOT / "config" / "secrets" / f"{node.name}.yaml"

    if key:
        import yaml
        existing = deployer.load_node_secrets(node.name)
        for k in key:
            existing.pop(k, None)
        if secrets_path.exists():
            with open(secrets_path, "w") as fh:
                yaml.dump(existing, fh, default_flow_style=False, allow_unicode=True)
        console.print(f"[yellow]Cleared keys: {', '.join(key)}. Run 'bulwark deploy {node.name}' to regenerate.[/yellow]")
    else:
        if secrets_path.exists():
            secrets_path.unlink()
        console.print(f"[yellow]All secrets cleared for '{node.name}'. Run 'bulwark deploy {node.name}' to regenerate.[/yellow]")


# ===========================================================================
# bulwark awg <subcommand>
# ===========================================================================

@cli.group("awg")
def grp_awg():
    """Manage AmneziaWG peers and vpn:// links."""


@grp_awg.command("add-peer")
@click.argument("node_name", metavar="NODE")
@click.option("--name", "-n", default="", help="Peer name (default: peer-N).")
@click.option("--pubkey", default="", help="Client public key (if empty, generate keypair on server).")
def cmd_awg_add_peer(node_name: str, name: str, pubkey: str):
    """
    Add a new AWG client peer for NODE and update the server config.

    If --pubkey is given, uses the provided public key (API provisioning mode,
    no private key stored). Otherwise generates a keypair on the server.
    """
    from core.config_gen import REGION_OCTET

    node = resolve_node(node_name)
    cg = get_cg()
    nm = get_nm()
    deployer = get_deployer()
    secrets = cg.load_secrets(node.name)

    if not secrets.get("awg_public_key"):
        err_console.print(
            f"[bold red]Error:[/bold red] AmneziaWG not deployed on '{node.name}'. "
            f"Run 'bulwark deploy {node.name} --protocol amneziawg' first."
        )
        sys.exit(1)

    peers = secrets.get("awg_peers", [])

    # Check if pubkey already exists
    if pubkey:
        for p in peers:
            if p.get("public_key") == pubkey:
                console.print(f"[yellow]Peer with this pubkey already exists: {p['name']}[/yellow]")
                console.print(f"  Address: [cyan]{p['address']}[/cyan]")
                return

    peer_name = name or f"peer-{len(peers) + 1}"
    region_octet = REGION_OCTET.get(node.region, 99)
    peer_addr = f"10.{region_octet}.0.{len(peers) + 2}"  # .2, .3, .4 ...

    with console.status(f"Adding peer '{peer_name}' on {node.display_name}..."):
        with nm.ssh(node) as conn:
            if pubkey:
                # API mode: client provided their pubkey, no private key stored
                client_pubkey = pubkey
                client_privkey = ""  # not available — client holds it
            else:
                # Interactive mode: generate keypair on server
                out, err, rc = conn.exec("awg genkey")
                if rc != 0:
                    out, err, rc = conn.exec("wg genkey")
                if rc != 0:
                    err_console.print(f"[bold red]Failed to generate key:[/bold red] {err}")
                    sys.exit(1)
                client_privkey = out.strip()
                conn.exec(f"printf '%s' {client_privkey!r} > /tmp/df_awg_client.tmp")
                pub_out, _, pub_rc = conn.exec(
                    "awg pubkey < /tmp/df_awg_client.tmp"
                    " || wg pubkey < /tmp/df_awg_client.tmp"
                )
                conn.exec("rm -f /tmp/df_awg_client.tmp")
                if pub_rc != 0:
                    err_console.print("[bold red]Failed to derive public key[/bold red]")
                    sys.exit(1)
                client_pubkey = pub_out.strip()

            peer_data = {
                "name": peer_name,
                "public_key": client_pubkey,
                "address": peer_addr,
            }
            if client_privkey:
                peer_data["private_key"] = client_privkey

            peers.append(peer_data)
            secrets["awg_peers"] = peers
            deployer.save_node_secrets(node.name, secrets)

            # Re-upload config and restart AWG
            config_content = cg.generate_amneziawg(node, secrets)
            conn.upload_content(config_content, "/etc/amnezia/amneziawg/awg0.conf")
            conn.exec("chmod 600 /etc/amnezia/amneziawg/awg0.conf")
            _, _, rc = conn.exec("systemctl restart wg-quick@awg0")
            if rc != 0:
                err_console.print("[bold red]Warning:[/bold red] AWG restart failed — check server")

    console.print(f"\n[green]✓ Peer '[bold]{peer_name}[/bold]' added to {node.display_name}[/green]")
    console.print(f"  Address: [cyan]{peer_addr}/32[/cyan]")

    if client_privkey:
        vpn_link = cg.generate_amneziawg_vpn_link(node, secrets, peers[-1])
        console.print(f"\n[bold]vpn:// link:[/bold]")
        console.print(f"[dim]{vpn_link}[/dim]")
    else:
        console.print("  [dim]No vpn:// link — client provided their own pubkey (API mode)[/dim]")

    console.print("\n[dim]Run 'bulwark sub push' to update the portal.[/dim]")


@grp_awg.command("gen-link")
@click.argument("node_name", metavar="NODE")
@click.option("--pubkey", required=True, help="Client public key to find peer by.")
def cmd_awg_gen_link(node_name: str, pubkey: str):
    """Generate a vpn:// link for an existing peer (looked up by pubkey)."""
    node = resolve_node(node_name)
    cg = get_cg()
    secrets = cg.load_secrets(node.name)
    peers = secrets.get("awg_peers", [])

    peer = None
    for p in peers:
        if p.get("public_key") == pubkey:
            peer = p
            break

    if not peer:
        err_console.print(f"[bold red]Error:[/bold red] No peer with pubkey '{pubkey[:20]}...' on {node.name}")
        sys.exit(1)

    # For API-provisioned peers (no private_key), use placeholder
    if not peer.get("private_key"):
        peer = dict(peer)
        peer["private_key"] = "$WIREGUARD_CLIENT_PRIVATE_KEY"

    vpn_link = cg.generate_amneziawg_vpn_link(node, secrets, peer)
    # Output just the link (for subprocess parsing by portal)
    print(vpn_link)


@grp_awg.command("list-peers")
@click.argument("node_name", metavar="NODE")
def cmd_awg_list_peers(node_name: str):
    """List configured AWG peers for NODE."""
    node = resolve_node(node_name)
    cg = get_cg()
    secrets = cg.load_secrets(node.name)
    peers = secrets.get("awg_peers", [])

    if not peers:
        console.print(f"[yellow]No peers configured for '{node.name}'.[/yellow]")
        return

    table = Table(title=f"AWG Peers — {node.display_name}", box=box.ROUNDED,
                  header_style="bold cyan")
    table.add_column("Name", style="bold white")
    table.add_column("Address")
    table.add_column("Public Key", overflow="fold")
    table.add_column("Has vpn:// link")

    for p in peers:
        has_link = "yes" if p.get("private_key") else "no (no private key)"
        table.add_row(
            p.get("name", "?"),
            p.get("address", "?"),
            p.get("public_key", "?"),
            has_link,
        )
    console.print()
    console.print(table)


@grp_awg.group("sub")
def grp_awg_sub():
    """Manage AWG API subscriptions (Type 2 vpn:// links)."""


@grp_awg_sub.command("add")
@click.argument("name")
@click.option("--nodes", "-n", default="", help="Comma-separated exit node names (empty = all).")
@click.option("--max-peers", default=5, help="Max peers for this user.")
def cmd_awg_sub_add(name: str, nodes: str, max_peers: int):
    """Create a new AWG API subscription user and generate a Type 2 vpn:// link."""
    from core.awg_users import AWGUserManager
    from core.config_gen import ConfigGenerator

    mgr = AWGUserManager()
    cg = ConfigGenerator()
    nm = get_nm()

    assigned = [n.strip() for n in nodes.split(",") if n.strip()] if nodes else []
    if not assigned:
        assigned = [n.name for n in nm.exit_nodes() if "amneziawg" in n.protocols]

    try:
        user = mgr.add_user(name, assigned_nodes=assigned, max_peers=max_peers)
    except ValueError as e:
        err_console.print(f"[bold red]Error:[/bold red] {e}")
        sys.exit(1)

    _sub_base = cg._load_global().get("subscription", {}).get("base_url", "").rstrip("/")
    _portal_base = _sub_base.rsplit("/", 1)[0] if "/" in _sub_base.lstrip("https://").lstrip("http://") else _sub_base
    _awg_prefix = os.environ.get("PORTAL_AWG_PREFIX", "/awg-api")
    link = cg.generate_awg_subscription_link(api_key=user["api_key"], base_url=_portal_base, awg_prefix=_awg_prefix)

    console.print(f"\n[green]✓ AWG subscription created: [bold]{name}[/bold][/green]")
    console.print(f"  API key: [cyan]{user['api_key']}[/cyan]")
    console.print(f"  Nodes: {', '.join(assigned)}")
    console.print(f"  Max peers: {max_peers}")
    console.print(f"\n[bold]Type 2 vpn:// link:[/bold]")
    console.print(f"[dim]{link}[/dim]")


@grp_awg_sub.command("list")
def cmd_awg_sub_list():
    """List all AWG API subscription users."""
    from core.awg_users import AWGUserManager

    mgr = AWGUserManager()
    users = mgr.list_users()

    if not users:
        console.print("[yellow]No AWG subscriptions configured.[/yellow]")
        return

    table = Table(title="AWG API Subscriptions", box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Name", style="bold white")
    table.add_column("Active")
    table.add_column("Nodes")
    table.add_column("Peers")
    table.add_column("API Key", overflow="fold")

    for u in users:
        active = "[green]yes[/green]" if u.get("active") else "[red]revoked[/red]"
        table.add_row(
            u["name"],
            active,
            ", ".join(u.get("assigned_nodes", [])),
            str(len(u.get("peers", []))),
            u.get("api_key", "")[:16] + "..." if u.get("api_key") else "-",
        )
    console.print()
    console.print(table)


@grp_awg_sub.command("show")
@click.argument("name")
def cmd_awg_sub_show(name: str):
    """Show details for an AWG API subscription user."""
    from core.awg_users import AWGUserManager
    from core.config_gen import ConfigGenerator

    mgr = AWGUserManager()
    user = mgr.get_user(name)
    if not user:
        err_console.print(f"[bold red]Error:[/bold red] User '{name}' not found")
        sys.exit(1)

    cg = ConfigGenerator()
    active = "[green]active[/green]" if user.get("active") else "[red]revoked[/red]"

    console.print(f"\n[bold]{user['name']}[/bold] — {active}")
    console.print(f"  API key: [cyan]{user.get('api_key', '-')}[/cyan]")
    console.print(f"  Nodes: {', '.join(user.get('assigned_nodes', []))}")
    console.print(f"  Max peers: {user.get('max_peers', 5)}")

    peers = user.get("peers", [])
    if peers:
        console.print(f"  Peers ({len(peers)}):")
        for p in peers:
            console.print(f"    {p.get('node', '?')} — {p.get('address', '?')} — {p['pubkey'][:20]}...")
    else:
        console.print("  Peers: none")

    if user.get("api_key"):
        _sb = cg._load_global().get("subscription", {}).get("base_url", "").rstrip("/")
        _pb = _sb.rsplit("/", 1)[0] if "/" in _sb.lstrip("https://").lstrip("http://") else _sb
        _awgp = os.environ.get("PORTAL_AWG_PREFIX", "/awg-api")
        link = cg.generate_awg_subscription_link(api_key=user["api_key"], base_url=_pb, awg_prefix=_awgp)
        console.print(f"\n[bold]Type 2 AWG 2.0 link:[/bold]")
        console.print(f"[dim]{link}[/dim]")


@grp_awg_sub.command("revoke")
@click.argument("name")
@click.confirmation_option(prompt="Revoke this subscription? Existing connections will keep working.")
def cmd_awg_sub_revoke(name: str):
    """Revoke an AWG API subscription (disables api_key, peers stay)."""
    from core.awg_users import AWGUserManager

    mgr = AWGUserManager()
    if mgr.revoke_user(name):
        console.print(f"[green]✓ Subscription '{name}' revoked.[/green]")
    else:
        err_console.print(f"[bold red]Error:[/bold red] User '{name}' not found")
        sys.exit(1)


# ===========================================================================
# bulwark management <subcommand>
# ===========================================================================

@cli.group("management")
def grp_management():
    """Install and manage Bulwark on a remote node."""


@grp_management.command("install")
@click.argument("node_name", metavar="NODE")
@click.option("--start-monitor", is_flag=True, default=False,
              help="Start bulwark-monitor service immediately after install.")
def cmd_management_install(node_name: str, start_monitor: bool):
    """
    Deploy the Bulwark management system to NODE.

    Uploads the entire project, installs Python venv, creates /usr/local/bin/bulwark
    wrapper and registers bulwark-monitor.service (enabled but not started by default).
    """
    import shutil
    import tempfile
    import tarfile

    node = resolve_node(node_name)
    nm = get_nm()

    console.print(Panel(
        f"Installing Bulwark on [bold cyan]{node.display_name}[/bold cyan] ({node.ip})",
        title="Management Install",
        expand=False,
    ))

    # Build a tarball of the project, excluding secrets, venv, __pycache__
    console.print("[dim]Packing project files...[/dim]")
    tmp_tar = Path(tempfile.mktemp(suffix=".tar.gz"))
    try:
        with tarfile.open(tmp_tar, "w:gz") as tar:
            def _exclude(t: tarfile.TarInfo) -> tarfile.TarInfo | None:
                skip = ("venv", "__pycache__", ".git", "*.pyc", ".env")
                for pat in skip:
                    if pat.lstrip("*") in t.name:
                        return None
                return t
            tar.add(str(PROJECT_ROOT), arcname="bulwark", filter=_exclude)

        tar_bytes = tmp_tar.read_bytes()
        console.print(f"[dim]Archive: {len(tar_bytes)//1024} KB[/dim]")

        with console.status("Uploading and installing..."):
            with nm.ssh(node) as conn:
                # Upload tarball
                import io
                buf = io.BytesIO(tar_bytes)
                sftp = conn.client.open_sftp()
                sftp.putfo(buf, "/tmp/bulwark_mgmt.tar.gz")
                sftp.close()

                # Extract
                conn.exec("rm -rf /opt/bulwark && mkdir -p /opt/bulwark")
                conn.exec("tar -xzf /tmp/bulwark_mgmt.tar.gz --strip-components=1 -C /opt/bulwark")
                conn.exec("rm -f /tmp/bulwark_mgmt.tar.gz")

                # Upload .env (without secrets for remote — just SSH vars)
                env_path = PROJECT_ROOT / ".env"
                if env_path.exists():
                    conn.upload_file(str(env_path), "/opt/bulwark/.env")

                # Run install script
                script_path = PROJECT_ROOT / "deploy" / "scripts" / "install_management.sh"
                with open(script_path, "r") as fh:
                    script_content = fh.read()
                conn.upload_content(script_content, "/tmp/bulwark_install_mgmt.sh")
                conn.exec("chmod +x /tmp/bulwark_install_mgmt.sh")
                out, err, rc = conn.exec("bash /tmp/bulwark_install_mgmt.sh", timeout=180)
                conn.exec("rm -f /tmp/bulwark_install_mgmt.sh")

                if rc != 0:
                    raise RuntimeError(f"Install script failed (exit {rc}):\n{err}")

                if start_monitor:
                    out, err, rc = conn.exec("systemctl start bulwark-monitor")
                    if rc != 0:
                        console.print(f"[yellow]Warning: could not start bulwark-monitor: {err}[/yellow]")
                    else:
                        console.print("[green]✓ bulwark-monitor started[/green]")

        console.print(f"[bold green]Bulwark installed on {node.display_name}.[/bold green]")
        console.print(f"[dim]SSH to {node.ip} and run: bulwark status[/dim]")
        if not start_monitor:
            console.print(f"[dim]Start monitor: systemctl start bulwark-monitor[/dim]")

    except Exception as exc:
        err_console.print(f"[bold red]Management install failed:[/bold red] {exc}")
        sys.exit(1)
    finally:
        tmp_tar.unlink(missing_ok=True)


@grp_management.command("status")
@click.argument("node_name", metavar="NODE")
def cmd_management_status(node_name: str):
    """Check bulwark-monitor service status on NODE."""
    node = resolve_node(node_name)
    nm = get_nm()

    try:
        with nm.ssh(node) as conn:
            out, _, _ = conn.exec("systemctl status bulwark-monitor --no-pager 2>&1 | head -20")
            ver, _, _ = conn.exec("bulwark --version 2>/dev/null || echo 'not installed'")
        console.print(Panel(out, title=f"bulwark-monitor @ {node.display_name}", expand=False))
        console.print(f"[dim]CLI version: {ver.strip()}[/dim]")
    except Exception as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        sys.exit(1)


# ===========================================================================
# bulwark bot start
# ===========================================================================

@cli.group("bot")
def grp_bot():
    """Telegram-бот: мониторинг нод и интерактивные команды."""


@grp_bot.command("start")
@click.option("--no-monitor", is_flag=True, help="Не запускать monitor loop вместе с ботом.")
def cmd_bot_start(no_monitor: bool):
    """Запустить Telegram-бота (long-polling + monitor loop)."""
    import yaml
    from core.telegram import TelegramNotifier
    from core.monitor import NodeMonitor
    from core.bot import BulwarkBot

    cfg_path = PROJECT_ROOT / "config" / "global.yaml"
    with open(cfg_path, "r", encoding="utf-8") as fh:
        global_cfg = yaml.safe_load(fh) or {}

    notifier = TelegramNotifier()
    if not notifier.enabled:
        err_console.print(
            "[bold red]Ошибка:[/bold red] Telegram не настроен.\n"
            "Добавь TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env (или config/global.yaml)."
        )
        sys.exit(1)

    nm = get_nm()
    bridge = get_bridge()

    monitor = NodeMonitor(nm, bridge, global_cfg, notifier)

    tg_cfg = global_cfg.get("telegram", {})
    raw_ids = tg_cfg.get("admin_user_ids", []) or []
    allowed_ids = [int(x) for x in raw_ids if str(x).strip()]

    deployer = get_deployer()

    bot = BulwarkBot(
        token=notifier.bot_token,
        chat_id=notifier.chat_id,
        allowed_user_ids=allowed_ids,
        nm=nm,
        global_cfg=global_cfg,
        monitor=monitor,
        bm=bridge,
        deployer=deployer,
    )

    console.print(
        Panel(
            f"Бот запущен.\n"
            f"Monitor loop: {'[green]да[/green]' if not no_monitor else '[yellow]нет[/yellow]'}\n"
            f"Разрешённые user IDs: {allowed_ids or '[yellow]все[/yellow] (небезопасно!)'}\n"
            f"Ctrl+C для остановки.",
            title="🏰 Bulwark Bot",
            expand=False,
        )
    )

    try:
        asyncio.run(bot.run(with_monitor=not no_monitor))
    except KeyboardInterrupt:
        console.print("\n[yellow]Бот остановлен.[/yellow]")


# ===========================================================================
# Entry point
# ===========================================================================

if __name__ == "__main__":
    cli()
