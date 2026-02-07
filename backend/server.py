"""
NVIDIA GDX Spak Cluster Dashboard.
Provides a similar interface like btop but browser based

Author:  Paul Aviles
Created: 02-06-2026
Version: 0.2.0
License: CC-BY-NC-ND-4.0 Creative Commons Attribution Non Commercial No Derivatives 4.0 International
See License.txt
"""

# backend/server.py
import asyncio
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Tuple

import asyncssh
import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

app = FastAPI()
BASE_DIR = Path(__file__).parent
FRONTEND = BASE_DIR.parent / "frontend"

@app.get("/", response_class=FileResponse)
async def index() -> FileResponse:
    return FRONTEND / "index.html"

@app.get("/{file_name}")
async def static_file(file_name: str):
    requested = FRONTEND / file_name
    if requested.is_file():
        return FileResponse(requested)
    return {"error": f"File {file_name!r} not found"}, 404

# Load configuration
CONFIG_PATH = BASE_DIR / "config.yaml"
with CONFIG_PATH.open() as f:
    CFG = yaml.safe_load(f)

NODES: List[Dict[str, Any]] = CFG.get("nodes", [])

# SSH helper
async def ssh_run(node: dict, cmd: str) -> str:
    async with asyncssh.connect(
        node["host"],
        port=node.get("port", 22),
        username=node["username"],
        client_keys=[node["key_path"]],
        known_hosts=None,
    ) as conn:
        if node.get("pre_cmd"):
            await conn.run(node["pre_cmd"], check=False)
        result = await conn.run(cmd, check=False)
        return result.stdout

 
# Caches
 
CPU_CACHE: Dict[str, Dict[str, Any]] = {}
PREV_NET: Dict[str, Dict[str, Any]] = {}
PREV_DISK_IO: Dict[str, Dict[str, Any]] = {}

INTERFACES = ["enP7s7", "enp1s0f0np0", "enp1s0f1np1"]

 
# Helpers
 
def autoscale_bytes_per_sec(bps: float) -> Tuple[float, str]:
    if bps < 1024:
        return round(bps, 2), "B/s"
    kib = bps / 1024
    if kib < 1024:
        return round(kib, 2), "KiB/s"
    mib = kib / 1024
    if mib < 1024:
        return round(mib, 2), "MiB/s"
    gib = mib / 1024
    return round(gib, 2), "GiB/s"

 
# Batched command (includes IP for enP7s7 and diskstats)
 
BATCH_CMD = r"""
echo "LOAD $(cat /proc/loadavg | awk '{print $1,$2,$3}')"
echo "MEM $(free -m | awk 'NR==2{print $3,$2}')"
echo "IP_ENP7S7"
ip -4 addr show enP7s7 | awk '/inet / {print $2}'
echo "CPU"
grep '^cpu[0-9]' /proc/stat
echo "GPU"
nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>/dev/null || echo "N/A,N/A,N/A,N/A"
echo "DISK"
df -P -B1M | awk 'NR>1{printf "%s %s %s %s\n", $6, $3, $2-$3, $3/($2+$3)*100}'
echo "DISKDEV"
findmnt -n -o TARGET,SOURCE
echo "SWAP"
grep -E 'SwapTotal|SwapFree' /proc/meminfo
echo "NET"
for i in """ + " ".join(INTERFACES) + r"""; do
  if [ -d /sys/class/net/$i ]; then
    RX=$(cat /sys/class/net/$i/statistics/rx_bytes)
    TX=$(cat /sys/class/net/$i/statistics/tx_bytes)
    SPEED=$(cat /sys/class/net/$i/speed 2>/dev/null || echo -1)
    echo "$i $RX $TX $SPEED"
  fi
done
echo "DISKIO"
cat /proc/diskstats
"""

 
# Collector
 
async def collect(node: dict) -> dict:
    raw = await ssh_run(node, BATCH_CMD)
    lines = [l.strip() for l in raw.splitlines() if l.strip()]

    load = "0 0 0"
    mem = "0 0"
    ip_enp7s7 = "N/A"

    cpu_lines = []
    gpu_line = "N/A,N/A,N/A,N/A"
    disk_lines = []
    diskdev_lines = []
    swap_lines = []
    net_lines = []
    diskio_lines = []
    ip_lines = []

    mode = None
    for l in lines:
        if l.startswith("LOAD"):
            load = l.split(" ", 1)[1]
        elif l.startswith("MEM"):
            mem = l.split(" ", 1)[1]
        elif l == "IP_ENP7S7":
            mode = "IP_ENP7S7"
        elif l == "CPU":
            mode = "CPU"
        elif l == "GPU":
            mode = "GPU"
        elif l == "DISK":
            mode = "DISK"
        elif l == "DISKDEV":
            mode = "DISKDEV"
        elif l == "SWAP":
            mode = "SWAP"
        elif l == "NET":
            mode = "NET"
        elif l == "DISKIO":
            mode = "DISKIO"
        else:
            if mode == "CPU":
                cpu_lines.append(l)
            elif mode == "GPU":
                gpu_line = l
            elif mode == "DISK":
                disk_lines.append(l)
            elif mode == "DISKDEV":
                diskdev_lines.append(l)
            elif mode == "SWAP":
                swap_lines.append(l)
            elif mode == "NET":
                net_lines.append(l)
            elif mode == "DISKIO":
                diskio_lines.append(l)
            elif mode == "IP_ENP7S7":
                ip_lines.append(l)

    if ip_lines:
        ip_enp7s7 = ip_lines[0].split("/")[0]

    # ---- Build mount -> device map ----
    mount_to_dev: Dict[str, str] = {}
    for l in diskdev_lines:
        parts = l.split()
        if len(parts) != 2:
            continue
        mount, src = parts
        if src.startswith("/dev/"):
            mount_to_dev[mount] = src.replace("/dev/", "")

    # ---- CPU ----
    percents: List[int] = []
    cache = CPU_CACHE.setdefault(node["name"], {})
    for line in cpu_lines:
        parts = [int(v) for v in line.split()[1:]]
        idle, total = parts[3], sum(parts)
        key = line.split()[0]
        prev = cache.get(key, {"idle": idle, "total": total})
        diff_total = total - prev["total"]
        diff_idle = idle - prev["idle"]
        usage = int(100 * (1 - diff_idle / diff_total)) if diff_total else 0
        percents.append(usage)
        cache[key] = {"idle": idle, "total": total}

    # ---- GPU ----
    gpu_parts = [p.strip() for p in gpu_line.split(",")]
    def clean(v: str) -> str:
        return v if v.isdigit() else "N/A"
    util, mem_used, mem_total, temp = map(clean, gpu_parts)

    # ---- Disks capacity ----
    disks: List[Dict[str, Any]] = []
    for line in disk_lines:
        try:
            mount, used_mib, avail_mib, used_pct = line.split()
            disks.append({
                "mount": mount,
                "used_mib": int(used_mib),
                "avail_mib": int(avail_mib),
                "used_pct": int(float(used_pct)),
            })
        except ValueError:
            pass

    # ---- Swap ----
    swap_total = swap_free = 0
    for l in swap_lines:
        if l.startswith("SwapTotal"):
            swap_total = int(l.split()[1])
        elif l.startswith("SwapFree"):
            swap_free = int(l.split()[1])

    if swap_total > 0:
        used_kb = swap_total - swap_free
        used_pct = round(used_kb / swap_total * 100)
        disks.append({
            "mount": "swap",
            "used_mib": used_kb // 1024,
            "avail_mib": (swap_total - used_kb) // 1024,
            "used_pct": used_pct,
        })
    else:
        disks.append({"mount": "swap", "used_mib": 0, "avail_mib": 0, "used_pct": 0})

    wanted = {"/", "/boot/efi", "swap"}
    disks = [d for d in disks if d["mount"] in wanted]

    # ---- Disk I/O ----
    disk_io_now: Dict[str, Tuple[int, int]] = {}
    for l in diskio_lines:
        parts = l.split()
        if len(parts) < 14:
            continue
        dev = parts[2]
        sectors_read = int(parts[5])
        sectors_written = int(parts[9])
        disk_io_now[dev] = (sectors_read * 512, sectors_written * 512)

    now = time.time()
    disk_io_rates: Dict[str, Dict[str, Any]] = {}

    for dev, (r_bytes, w_bytes) in disk_io_now.items():
        key = f"{node['name']}|{dev}"
        prev = PREV_DISK_IO.get(key, {"ts": now, "r": r_bytes, "w": w_bytes})
        elapsed = max(0.001, now - prev["ts"])
        dr = max(0, r_bytes - prev["r"])
        dw = max(0, w_bytes - prev["w"])

        PREV_DISK_IO[key] = {"ts": now, "r": r_bytes, "w": w_bytes}

        r_bps = dr / elapsed
        w_bps = dw / elapsed

        r_val, r_unit = autoscale_bytes_per_sec(r_bps)
        w_val, w_unit = autoscale_bytes_per_sec(w_bps)

        disk_io_rates[dev] = {
            "read_value": r_val,
            "read_unit": r_unit,
            "write_value": w_val,
            "write_unit": w_unit,
            "total_mibps": (r_bps + w_bps) / (1024 * 1024),
        }

    # Attach IO stats using exact device mapping
    for d in disks:
        dev = mount_to_dev.get(d["mount"])
        if dev and dev in disk_io_rates:
            d["io"] = disk_io_rates[dev]
        else:
            d["io"] = {
                "read_value": 0, "read_unit": "B/s",
                "write_value": 0, "write_unit": "B/s",
                "total_mibps": 0,
            }

    # ---- Network ----
    netifs = []
    now = time.time()
    for l in net_lines:
        try:
            name, rx_b, tx_b, speed = l.split()
            rx_b = int(rx_b)
            tx_b = int(tx_b)
            speed = int(speed)
        except ValueError:
            continue

        if speed < 0:
            continue

        key = f"{node['name']}|{name}"
        prev = PREV_NET.get(key, {"ts": now, "rx": rx_b, "tx": tx_b})
        elapsed = max(0.001, now - prev["ts"])
        rx_rate = max(0, rx_b - prev["rx"])
        tx_rate = max(0, tx_b - prev["tx"])

        PREV_NET[key] = {"ts": now, "rx": rx_b, "tx": tx_b}

        rx_bps = rx_rate / elapsed
        tx_bps = tx_rate / elapsed

        rx_val, rx_unit = autoscale_bytes_per_sec(rx_bps)
        tx_val, tx_unit = autoscale_bytes_per_sec(tx_bps)

        netifs.append({
            "name": name,
            "speed_mbps": speed,
            "rx_value": rx_val,
            "rx_unit": rx_unit,
            "tx_value": tx_val,
            "tx_unit": tx_unit,
            "total_mibps": (rx_bps + tx_bps) / (1024 * 1024),
        })

    return {
        "node": node["name"],
        "ip_enp7s7": ip_enp7s7,
        "load": load,
        "mem": mem,
        "cpu": percents,
        "disks": disks,
        "netifs": netifs,
        "gpu_util": util,
        "gpu_mem_used": mem_used,
        "gpu_mem_total": mem_total,
        "gpu_temp": temp,
    }

 
# WebSocket
REFRESH_INTERVAL = 1.0

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            payload = await asyncio.gather(*(collect(n) for n in NODES))
            await ws.send_text(json.dumps(payload))
            await asyncio.sleep(REFRESH_INTERVAL)
    except WebSocketDisconnect:
        print("WebSocket client disconnected")
    except Exception as exc:
        print(f"WebSocket error: {exc}")
        await ws.close()
