"""
Spin a Baddie — Nullity Event Scanner (v2)
==========================================
Only alerts on servers tracked from birth (appeared while script running).
Pre-existing servers are ignored for timing — their start time is unknown.

Polls every 10s. Alerts at 90s, 60s, 30s before Nullity.
Discord webhook support via DISCORD_WEBHOOK env var.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Set, Optional

try:
    import requests
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.text import Text
    from rich import box
except ImportError:
    sys.exit("Run: pip install -r requirements.txt")


# ── Config ────────────────────────────────────────────────────────────────────

PLACE_ID           = 79305036070450
EVENT_INTERVAL_SEC = 15 * 60          # Nullity every 15 min from server start
ALERT_THRESHOLDS   = [90, 60, 30]     # alert at each of these seconds remaining
DISCORD_COOLDOWN   = 30               # min seconds between Discord messages
POLL_INTERVAL      = 10               # seconds between API polls
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
IST = timezone(timedelta(hours=5, minutes=30))

# ── State ─────────────────────────────────────────────────────────────────────

server_first_seen: Dict[str, float] = {}   # sid → unix time first seen
tracked_servers:   Set[str]         = set() # sids seen from birth (reliable)
initial_servers:   Set[str]         = set() # sids from very first poll (unreliable)
alerted:           Dict[str, Set[int]] = {} # sid → set of thresholds already fired
first_poll_done    = False
last_discord_sent: float = 0.0
console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def now() -> float:
    return time.time()

def ist_time() -> str:
    return datetime.now(IST).strftime("%H:%M:%S IST")

def secs_to_next(first_seen: float, t: float) -> float:
    return EVENT_INTERVAL_SEC - ((t - first_seen) % EVENT_INTERVAL_SEC)

def fmt(seconds: float) -> str:
    seconds = max(0, seconds)
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"

def deep_link(sid: str) -> str:
    return f"roblox://experiences/start?placeId={PLACE_ID}&gameInstanceId={sid}"

def web_link(sid: str) -> str:
    return f"https://www.roblox.com/games/start?placeId={PLACE_ID}&gameInstanceId={sid}"


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


# ── Discord ───────────────────────────────────────────────────────────────────

def post_discord(webhook_url: str, sid: str, secs: float, threshold: int):
    dl = deep_link(sid)
    wl = web_link(sid)
    payload = {
        "username": "Nullity Scanner",
        "embeds": [{
            "title": f"⚡ NULLITY IN {fmt(secs)}",
            "color": 0xFF4500 if threshold <= 30 else (0xFFAA00 if threshold <= 60 else 0xFFD700),
            "fields": [
                {"name": "Time remaining", "value": fmt(secs),   "inline": True},
                {"name": "Detected at",    "value": ist_time(),  "inline": True},
                {"name": "Server ID",      "value": f"`{sid}`",  "inline": False},
                {"name": "Desktop link",   "value": dl,          "inline": False},
                {"name": "Web link",       "value": wl,          "inline": False},
            ],
            "footer": {"text": f"Reliable timer ✓  |  Place {PLACE_ID}"}
        }]
    }
    try:
        requests.post(webhook_url, json=payload, timeout=5)
    except Exception as e:
        console.print(f"[red]Discord error:[/red] {e}")


# ── Log ───────────────────────────────────────────────────────────────────────

def log_alert(sid: str, secs: float, threshold: int):
    line = (
        f"\n{'='*60}\n"
        f"NULLITY ALERT ({threshold}s warning) — {ist_time()}\n"
        f"Server  : {sid}\n"
        f"In      : {fmt(secs)}\n"
        f"Desktop : {deep_link(sid)}\n"
        f"Web     : {web_link(sid)}\n"
        f"{'='*60}\n"
    )
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass


# ── Terminal alert ────────────────────────────────────────────────────────────

def terminal_alert(sid: str, secs: float, threshold: int, use_sound: bool):
    dl = deep_link(sid)
    wl = web_link(sid)
    console.print()
    console.rule(f"[bold yellow]⚡  NULLITY IN {fmt(secs)}  ⚡[/bold yellow]")
    console.print(f"[bold green]Server  :[/bold green] {sid}")
    console.print(f"[bold green]At      :[/bold green] {ist_time()}")
    console.print()
    console.print("[bold cyan]── DESKTOP LINK ──[/bold cyan]")
    console.print(dl)
    console.print()
    console.print("[bold cyan]── WEB LINK ──[/bold cyan]")
    console.print(wl)
    console.rule()
    if use_sound:
        sys.stdout.write("\a")
        sys.stdout.flush()


# ── Table ─────────────────────────────────────────────────────────────────────

def build_table(servers: list) -> Table:
    t = now()
    tracked_count   = sum(1 for s in servers if s.get("id") in tracked_servers)
    untracked_count = len(servers) - tracked_count

    table = Table(
        title=(
            f"[bold]Nullity Scanner[/bold]  [dim]{ist_time()}[/dim]  "
            f"[green]✓ {tracked_count} tracked[/green]  "
            f"[dim]~ {untracked_count} untracked[/dim]"
        ),
        box=box.ROUNDED,
        show_lines=False,
    )
    table.add_column("Server ID",    style="dim", width=36)
    table.add_column("Players",      justify="right", width=9)
    table.add_column("FPS",          justify="right", width=6)
    table.add_column("Age",          justify="right", width=8)
    table.add_column("Next Nullity", justify="center", width=14)
    table.add_column("Timer",        justify="center", width=12)

    # Only show tracked servers in the table (reliable timers)
    rows = []
    for s in servers:
        sid = s.get("id", "")
        if sid not in tracked_servers:
            continue
        fs   = server_first_seen[sid]
        secs = secs_to_next(fs, t)
        rows.append((secs, s, sid, fs))

    rows.sort(key=lambda x: x[0])

    for secs, s, sid, fs in rows:
        players = f"{s.get('playing', '?')}/{s.get('maxPlayers', '?')}"
        fps     = str(round(s.get('fps', 0), 1))
        age     = fmt(t - fs)
        cd      = fmt(secs)

        if secs <= 30:
            status = Text("🔥 NOW",   style="bold red")
            cd_txt = Text(cd,         style="bold red")
            style  = "bold yellow"
        elif secs <= 60:
            status = Text("⚠ 1 MIN", style="yellow")
            cd_txt = Text(cd,         style="yellow")
            style  = "green"
        elif secs <= 90:
            status = Text("● SOON",  style="cyan")
            cd_txt = Text(cd,         style="cyan")
            style  = ""
        else:
            status = Text("·",        style="dim")
            cd_txt = Text(cd)
            style  = ""

        table.add_row(sid, players, fps, age, cd_txt, status, style=style)

    if not rows:
        table.add_row(
            "[dim]Waiting for new servers to appear…[/dim]",
            "", "", "", "", ""
        )

    return table


# ── Main ──────────────────────────────────────────────────────────────────────

def run(use_sound: bool, webhook: Optional[str]):
    global first_poll_done, last_discord_sent
    console.print(
        f"\n[bold cyan]Nullity Scanner v2[/bold cyan]  "
        f"poll=[yellow]{POLL_INTERVAL}s[/yellow]  "
        f"alerts=[yellow]{ALERT_THRESHOLDS}s[/yellow]  "
        f"discord=[yellow]{'yes' if webhook else 'no'}[/yellow]"
        f"\n[dim]Only alerting on servers tracked from birth — zero false alarms.[/dim]\n"
    )

    last_poll  = 0.0
    servers    = []

    with Live(console=console, refresh_per_second=4, screen=False) as live:
        while True:
            t = now()

            # ── Poll ──────────────────────────────────────────────────────────
            if t - last_poll >= POLL_INTERVAL:
                fetched = fetch_servers()
                if fetched is not None:
                    servers = fetched
                    current_ids = {s.get("id", "") for s in servers}

                    for s in servers:
                        sid = s.get("id", "")
                        if not sid:
                            continue
                        if sid not in server_first_seen:
                            server_first_seen[sid] = t
                            if first_poll_done:
                                # New server appeared after first poll = reliable
                                tracked_servers.add(sid)
                                alerted[sid] = set()
                                console.print(
                                    f"[green]+ New server tracked:[/green] {sid[:20]}…"
                                )
                            else:
                                initial_servers.add(sid)

                    first_poll_done = True
                last_poll = t

            # ── Alerts (tracked only) — one message at a time ────────────────
            best_sid    = None
            best_secs   = float("inf")
            best_thresh = None

            for sid in list(tracked_servers):
                if sid not in server_first_seen:
                    continue
                secs = secs_to_next(server_first_seen[sid], t)
                for threshold in ALERT_THRESHOLDS:
                    if secs <= threshold and threshold not in alerted.get(sid, set()):
                        if secs < best_secs:
                            best_secs   = secs
                            best_sid    = sid
                            best_thresh = threshold

            if best_sid is not None:
                alerted.setdefault(best_sid, set()).add(best_thresh)
                log_alert(best_sid, best_secs, best_thresh)
                if webhook and (t - last_discord_sent) >= DISCORD_COOLDOWN:
                    post_discord(webhook, best_sid, best_secs, best_thresh)
                    last_discord_sent = t
                live.stop()
                terminal_alert(best_sid, best_secs, best_thresh, use_sound)
                live.start()

            # Reset alerted thresholds when we roll into a new 15-min window
            for sid in list(tracked_servers):
                if sid not in server_first_seen:
                    continue
                secs = secs_to_next(server_first_seen[sid], t)
                if secs > max(ALERT_THRESHOLDS) + 10:
                    alerted[sid] = set()

            # ── Render ────────────────────────────────────────────────────────
            live.update(
                build_table(servers) if servers
                else "[dim]Waiting for servers…[/dim]"
            )

            time.sleep(0.5)


# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--webhook", type=str,
                        default=os.environ.get("DISCORD_WEBHOOK"))
    parser.add_argument("--sound",   action="store_true")
    args = parser.parse_args()

    try:
        run(use_sound=args.sound, webhook=args.webhook)
    except KeyboardInterrupt:
        console.print("\n[dim]Scanner stopped.[/dim]")
