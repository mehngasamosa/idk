"""
Spin a Baddie — Nullity Event Scanner
======================================
Polls Roblox server list for place 79305036070450.
Tracks when each server first appears (≈ server start time).
Alerts when a server is within ALERT_WINDOW seconds of Nullity
(fires every 15 min from server start).

Usage (local):
    pip install -r requirements.txt
    python3 nullity_scanner.py

Flags:
    --alert    30          seconds before Nullity to alert (default 30)
    --poll     30          API poll interval in seconds (default 30)
    --webhook  <url>       Discord webhook URL for remote alerts
    --sound                play terminal bell on alert

Remote (Railway / Render / any VPS):
    Set DISCORD_WEBHOOK env var — alerts arrive in Discord.
"""

import argparse
import os
import sys
import time
from datetime import datetime
from typing import Dict, Optional

try:
    import requests
except ImportError:
    sys.exit("Missing: pip install -r requirements.txt")

try:
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.text import Text
    from rich import box
except ImportError:
    sys.exit("Missing: pip install -r requirements.txt")


# ── Config ────────────────────────────────────────────────────────────────────

PLACE_ID           = 79305036070450
EVENT_INTERVAL_SEC = 15 * 60
API_URL            = (
    f"https://games.roblox.com/v1/games/{PLACE_ID}/servers/Public"
    "?sortOrder=Asc&excludeFullGames=false&limit=100"
)
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
LOG_FILE = "nullity_alerts.txt"

# ── State ─────────────────────────────────────────────────────────────────────

server_first_seen: Dict[str, float] = {}
alerted_windows:   Dict[str, int]   = {}
console = Console()


# ── Link helpers ──────────────────────────────────────────────────────────────

def deep_link(server_id: str) -> str:
    return f"roblox://experiences/start?placeId={PLACE_ID}&gameInstanceId={server_id}"

def web_link(server_id: str) -> str:
    return f"https://www.roblox.com/games/start?placeId={PLACE_ID}&gameInstanceId={server_id}"


# ── Time helpers ──────────────────────────────────────────────────────────────

def now() -> float:
    return time.time()

def secs_to_next(first_seen: float, t: float) -> float:
    return EVENT_INTERVAL_SEC - ((t - first_seen) % EVENT_INTERVAL_SEC)

def win_index(first_seen: float, t: float) -> int:
    return int((t - first_seen) // EVENT_INTERVAL_SEC)

def fmt(seconds: float) -> str:
    seconds = max(0, seconds)
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch_servers() -> Optional[list]:
    cursor  = None
    servers = []
    backoff = 2
    while True:
        url = API_URL + (f"&cursor={cursor}" if cursor else "")
        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            if resp.status_code == 429:
                console.print(f"[yellow]Rate limited — waiting {backoff}s…[/yellow]")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            resp.raise_for_status()
            data    = resp.json()
            backoff = 2
        except Exception as e:
            console.print(f"[red]API error:[/red] {e}")
            return None

        servers.extend(data.get("data", []))
        cursor = data.get("nextPageCursor")
        if not cursor:
            break
        time.sleep(1.5)

    return servers


# ── Discord webhook ───────────────────────────────────────────────────────────

def post_discord(webhook_url: str, server_id: str, secs: float):
    dl = deep_link(server_id)
    wl = web_link(server_id)
    ts = datetime.now().strftime("%H:%M:%S")
    payload = {
        "username": "Nullity Scanner",
        "embeds": [{
            "title": "⚡ NULLITY INCOMING",
            "color": 0xFFD700,
            "fields": [
                {"name": "Time remaining", "value": fmt(secs), "inline": True},
                {"name": "Detected at",    "value": ts,        "inline": True},
                {"name": "Server ID",      "value": f"`{server_id}`", "inline": False},
                {"name": "Desktop link",   "value": dl,        "inline": False},
                {"name": "Web link",       "value": wl,        "inline": False},
            ],
            "footer": {"text": f"Place ID: {PLACE_ID}"}
        }]
    }
    try:
        requests.post(webhook_url, json=payload, timeout=5)
    except Exception as e:
        console.print(f"[red]Discord webhook failed:[/red] {e}")


# ── Log to file ───────────────────────────────────────────────────────────────

def log_alert(server_id: str, secs: float):
    dl = deep_link(server_id)
    wl = web_link(server_id)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = (
        f"\n{'='*60}\n"
        f"NULLITY ALERT — {ts}\n"
        f"Server ID : {server_id}\n"
        f"In        : {fmt(secs)}\n"
        f"Desktop   : {dl}\n"
        f"Web       : {wl}\n"
        f"{'='*60}\n"
    )
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass


# ── Terminal alert ────────────────────────────────────────────────────────────

def terminal_alert(server_id: str, secs: float, use_sound: bool):
    dl = deep_link(server_id)
    wl = web_link(server_id)
    console.print()
    console.rule("[bold yellow]⚡  NULLITY INCOMING  ⚡[/bold yellow]")
    console.print(f"[bold green]Server  :[/bold green] {server_id}")
    console.print(f"[bold green]In      :[/bold green] {fmt(secs)}")
    console.print()
    console.print("[bold cyan]── DESKTOP LINK ──[/bold cyan]")
    console.print(dl)
    console.print()
    console.print("[bold cyan]── WEB LINK ──[/bold cyan]")
    console.print(wl)
    console.print()
    console.print(f"[dim]Links also saved to → {LOG_FILE}[/dim]")
    console.rule()
    if use_sound:
        sys.stdout.write("\a")
        sys.stdout.flush()


# ── Table ─────────────────────────────────────────────────────────────────────

def build_table(servers: list, alert_window: int) -> Table:
    t = now()
    table = Table(
        title=(
            f"[bold]Spin a Baddie — Nullity Scanner[/bold]  "
            f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]  "
            f"[dim]{len(servers)} servers[/dim]"
        ),
        box=box.ROUNDED,
        show_lines=False,
        highlight=True,
    )
    table.add_column("Server ID",    style="dim",      width=36)
    table.add_column("Players",      justify="right",  width=9)
    table.add_column("FPS",          justify="right",  width=6)
    table.add_column("Ping",         justify="right",  width=7)
    table.add_column("Age",          justify="right",  width=8)
    table.add_column("Next Nullity", justify="center", width=14)
    table.add_column("Status",       justify="center", width=10)

    rows = []
    for s in servers:
        sid  = s.get("id", "")
        fs   = server_first_seen.get(sid, t)
        secs = secs_to_next(fs, t)
        rows.append((secs, s, sid, fs))

    rows.sort(key=lambda x: x[0])

    for secs, s, sid, fs in rows:
        players = f"{s.get('playing', '?')}/{s.get('maxPlayers', '?')}"
        fps     = str(round(s.get('fps', 0), 1))
        ping    = str(s.get('ping', '?'))
        age     = fmt(t - fs)
        cd      = fmt(secs)

        if secs <= alert_window:
            status     = Text("🔥 ALERT", style="bold red")
            cd_display = Text(cd,         style="bold red")
            row_style  = "bold yellow"
        elif secs <= 60:
            status     = Text("⚠ SOON",  style="yellow")
            cd_display = Text(cd,         style="yellow")
            row_style  = "green"
        else:
            status     = Text("·",        style="dim")
            cd_display = Text(cd)
            row_style  = ""

        table.add_row(sid, players, fps, ping, age, cd_display, status, style=row_style)

    return table


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(alert_window: int, poll_interval: int, use_sound: bool, webhook: Optional[str]):
    console.print(
        f"\n[bold cyan]Nullity Scanner[/bold cyan]  "
        f"alert=[yellow]{alert_window}s[/yellow]  "
        f"poll=[yellow]{poll_interval}s[/yellow]  "
        f"discord=[yellow]{'yes' if webhook else 'no'}[/yellow]"
        f"\nPlace ID : [dim]{PLACE_ID}[/dim]"
        f"\nLog file : [dim]{LOG_FILE}[/dim]\n"
    )

    last_poll = 0.0
    servers: list = []

    with Live(console=console, refresh_per_second=2, screen=False) as live:
        while True:
            t = now()

            # ── Poll ──────────────────────────────────────────────────────────
            if t - last_poll >= poll_interval:
                fetched = fetch_servers()
                if fetched is not None:
                    servers = fetched
                    for s in servers:
                        sid = s.get("id", "")
                        if sid and sid not in server_first_seen:
                            server_first_seen[sid] = t
                last_poll = t

            # ── Alerts ────────────────────────────────────────────────────────
            for s in servers:
                sid = s.get("id", "")
                if not sid or sid not in server_first_seen:
                    continue
                secs = secs_to_next(server_first_seen[sid], t)
                widx = win_index(server_first_seen[sid], t)

                if secs <= alert_window and alerted_windows.get(sid, -1) != widx:
                    alerted_windows[sid] = widx
                    log_alert(sid, secs)
                    if webhook:
                        post_discord(webhook, sid, secs)
                    live.stop()
                    terminal_alert(sid, secs, use_sound)
                    live.start()

            # ── Render ────────────────────────────────────────────────────────
            live.update(
                build_table(servers, alert_window)
                if servers
                else "[dim]Waiting for servers…[/dim]"
            )

            time.sleep(0.5)


# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spin a Baddie Nullity Event Scanner")
    parser.add_argument("--alert",   type=int, default=60,
                        help="Alert N seconds before Nullity (default: 60)")
    parser.add_argument("--poll",    type=int, default=30,
                        help="API poll interval in seconds (default: 30)")
    parser.add_argument("--webhook", type=str,
                        default=os.environ.get("DISCORD_WEBHOOK"),
                        help="Discord webhook URL (or set DISCORD_WEBHOOK env var)")
    parser.add_argument("--sound",   action="store_true",
                        help="Play terminal bell on alert")
    args = parser.parse_args()

    try:
        run(
            alert_window  = args.alert,
            poll_interval = args.poll,
            use_sound     = args.sound,
            webhook       = args.webhook,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Scanner stopped.[/dim]")
