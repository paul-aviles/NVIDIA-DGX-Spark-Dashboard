import os
"""
NVIDIA DGX Spark Cluster Dashboard backend (node-resilient polling).
Provides a similar interface like btop but browser based

Author:  Paul Aviles
Created: 02-06-2026
Version: 0.2.6
License: CC-BY-NC-ND-4.0 Creative Commons Attribution Non Commercial No Derivatives 4.0 International
See License.txt
"""

import asyncio
import json
import time
from copy import deepcopy
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
    return FileResponse(
        FRONTEND / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate"
        }
    )


@app.get("/{file_name}")
async def static_file(file_name: str):
    requested = FRONTEND / file_name

    if requested.is_file():
        return FileResponse(
            requested,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate"
            }
        )

    return {"error": f"File {file_name!r} not found"}, 404

CONFIG_PATH = BASE_DIR / "config.yaml"
with CONFIG_PATH.open() as f:
    CFG = yaml.safe_load(f)
NODES: List[Dict[str, Any]] = CFG.get("nodes", [])

SSH_CONNECT_TIMEOUT = 5
SSH_LOGIN_TIMEOUT = 5
SSH_COMMAND_TIMEOUT = 8
NODE_COLLECTION_TIMEOUT = 15
REFRESH_INTERVAL = 1.0

async def ssh_run(node: dict, cmd: str) -> str:
    async with asyncssh.connect(
        node["host"], port=node.get("port", 22), username=node["username"],
        client_keys=[node["key_path"]], known_hosts=None,
        connect_timeout=SSH_CONNECT_TIMEOUT, login_timeout=SSH_LOGIN_TIMEOUT,
    ) as conn:
        if node.get("pre_cmd"):
            await conn.run(node["pre_cmd"], check=False, timeout=SSH_COMMAND_TIMEOUT)
        result = await conn.run(cmd, check=False, timeout=SSH_COMMAND_TIMEOUT)
        return result.stdout

CPU_CACHE: Dict[str, Dict[str, Any]] = {}
PREV_NET: Dict[str, Dict[str, Any]] = {}
PREV_RDMA: Dict[str, Dict[str, Any]] = {}
PREV_DISK_IO: Dict[str, Dict[str, Any]] = {}
LAST_GOOD: Dict[str, Dict[str, Any]] = {}
HIDE_INTERFACES = {
    name.strip()
    for name in os.environ.get("WEBTOP_HIDE_INTERFACES", "").split(",")
    if name.strip()
}

def autoscale_bytes_per_sec(bps: float) -> Tuple[float, str]:
    if bps < 1024:
        return round(bps, 2), "B/s"
    kib = bps / 1024
    if kib < 1024:
        return round(kib, 2), "KiB/s"
    mib = kib / 1024
    if mib < 1024:
        return round(mib, 2), "MiB/s"
    return round(mib / 1024, 2), "GiB/s"

BATCH_CMD = r'''
echo "LOAD $(cat /proc/loadavg | awk '{print $1,$2,$3}')"
echo "MEM $(free -m | awk 'NR==2{print $3,$2}')"
echo "PRIMARY_IP"
PRIMARY_DEV=$(ip -o -4 route show default 2>/dev/null | awk 'NR==1 {print $5}')
if [ -n "$PRIMARY_DEV" ]; then
  ip -o -4 addr show dev "$PRIMARY_DEV" scope global 2>/dev/null |
    awk 'NR==1 {split($4,a,"/"); print a[1]}'
fi
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
for path in /sys/class/net/*; do
  [ -e "$path" ] || continue
  i=${path##*/}
  [ "$i" = "lo" ] && continue
  # Keep physical device-backed NICs only. This excludes loopback, bridges,
  # veth, Docker, wireless and other software-created interfaces.
  [ -e "$path/device" ] || continue
  [ -d "$path/wireless" ] && continue
  [ -d "$path/bridge" ] && continue
  [ -d "$path/bonding" ] && continue
  RX=$(cat "$path/statistics/rx_bytes" 2>/dev/null || echo 0)
  TX=$(cat "$path/statistics/tx_bytes" 2>/dev/null || echo 0)
  SPEED=$(cat "$path/speed" 2>/dev/null || echo -1)
  IP=$(ip -o -4 addr show dev "$i" scope global 2>/dev/null |
       awk '{split($4,a,"/"); printf "%s%s", sep, a[1]; sep=","}')
  [ -n "$IP" ] || IP="N/A"
  echo "$i $RX $TX $SPEED $IP"
done
echo "RDMA"
RDMA_LINKS=$(rdma link show 2>/dev/null || true)
for d in /sys/class/infiniband/*; do
  [ -d "$d/ports" ] || continue
  dev=${d##*/}
  for port_path in "$d"/ports/*/counters; do
    [ -d "$port_path" ] || continue
    port_dir=${port_path%/counters}
    port=${port_dir##*/}
    netdev=$(printf '%s\\n' "$RDMA_LINKS" | awk -v hca="$dev" -v port="$port" '
      {
        link=""; linkport=""; netdev="";
        for (i=1; i<=NF; i++) {
          if ($i=="link") {
            split($(i+1), a, "/");
            link=a[1]; linkport=a[2];
          }
          if ($i=="netdev") netdev=$(i+1);
        }
        if (link==hca && linkport==port && netdev!="") {
          print netdev; exit;
        }
      }')
    [ -n "$netdev" ] || netdev="unknown"
    rcv_data=$(cat "$port_path/port_rcv_data" 2>/dev/null || echo 0)
    xmit_data=$(cat "$port_path/port_xmit_data" 2>/dev/null || echo 0)
    rcv_packets=$(cat "$port_path/port_rcv_packets" 2>/dev/null || echo 0)
    xmit_packets=$(cat "$port_path/port_xmit_packets" 2>/dev/null || echo 0)

    netdev_ip="N/A"
    netdev_speed="-1"

    if [ "$netdev" != "unknown" ] && [ -d "/sys/class/net/$netdev" ]; then
      netdev_ip=$(ip -o -4 addr show dev "$netdev" scope global 2>/dev/null |
        awk '{split($4,a,"/"); printf "%s%s", sep, a[1]; sep=","}')
      [ -n "$netdev_ip" ] || netdev_ip="N/A"

      netdev_speed=$(cat "/sys/class/net/$netdev/speed" 2>/dev/null || echo -1)
    fi

    echo "$dev $port $netdev $netdev_ip $netdev_speed $rcv_data $xmit_data $rcv_packets $xmit_packets"
  done
done
echo "DISKIO"
cat /proc/diskstats
'''

async def collect(node: dict) -> dict:
    raw = await ssh_run(node, BATCH_CMD)
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    load, mem, primary_ip = "0 0 0", "0 1", "N/A"
    cpu_lines, disk_lines, diskdev_lines = [], [], []
    swap_lines, net_lines, rdma_lines, diskio_lines, primary_ip_lines = [], [], [], [], []
    gpu_line, mode = "N/A,N/A,N/A,N/A", None
    for line in lines:
        if line.startswith("LOAD"):
            load = line.split(" ", 1)[1]
        elif line.startswith("MEM"):
            mem = line.split(" ", 1)[1]
        elif line in ("PRIMARY_IP", "CPU", "GPU", "DISK", "DISKDEV", "SWAP", "NET", "RDMA", "DISKIO"):
            mode = line
        elif mode == "CPU": cpu_lines.append(line)
        elif mode == "GPU": gpu_line = line
        elif mode == "DISK": disk_lines.append(line)
        elif mode == "DISKDEV": diskdev_lines.append(line)
        elif mode == "SWAP": swap_lines.append(line)
        elif mode == "NET": net_lines.append(line)
        elif mode == "RDMA": rdma_lines.append(line)
        elif mode == "DISKIO": diskio_lines.append(line)
        elif mode == "PRIMARY_IP": primary_ip_lines.append(line)
    if primary_ip_lines:
        primary_ip = primary_ip_lines[0].split("/")[0]

    mount_to_dev = {}
    for line in diskdev_lines:
        parts = line.split()
        if len(parts) == 2 and parts[1].startswith("/dev/"):
            mount_to_dev[parts[0]] = parts[1].replace("/dev/", "")

    percents = []
    cache = CPU_CACHE.setdefault(node["name"], {})
    for line in cpu_lines:
        parts = [int(v) for v in line.split()[1:]]
        if len(parts) < 4: continue
        idle, total = parts[3], sum(parts)
        key = line.split()[0]
        prev = cache.get(key, {"idle": idle, "total": total})
        dt, di = total - prev["total"], idle - prev["idle"]
        usage = int(100 * (1 - di / dt)) if dt > 0 else 0
        percents.append(max(0, min(100, usage)))
        cache[key] = {"idle": idle, "total": total}

    gpu_parts = [p.strip() for p in gpu_line.split(",")]
    gpu_parts += ["N/A"] * max(0, 4 - len(gpu_parts))
    clean = lambda value: value if value.isdigit() else "N/A"
    util, mem_used, mem_total, temp = map(clean, gpu_parts[:4])

    disks = []
    for line in disk_lines:
        try:
            mount, used_mib, avail_mib, used_pct = line.split()
            disks.append({"mount": mount, "used_mib": int(used_mib), "avail_mib": int(avail_mib), "used_pct": int(float(used_pct))})
        except ValueError: pass
    swap_total = swap_free = 0
    for line in swap_lines:
        if line.startswith("SwapTotal"): swap_total = int(line.split()[1])
        elif line.startswith("SwapFree"): swap_free = int(line.split()[1])
    if swap_total:
        used_kb = swap_total - swap_free
        disks.append({"mount": "swap", "used_mib": used_kb // 1024, "avail_mib": swap_free // 1024, "used_pct": round(used_kb / swap_total * 100)})
    else:
        disks.append({"mount": "swap", "used_mib": 0, "avail_mib": 0, "used_pct": 0})
    disks = [d for d in disks if d["mount"] in {"/", "/boot/efi", "swap"}]

    disk_io_now = {}
    for line in diskio_lines:
        parts = line.split()
        if len(parts) >= 14:
            disk_io_now[parts[2]] = (int(parts[5]) * 512, int(parts[9]) * 512)
    now = time.time()
    disk_io_rates = {}
    for dev, (r_bytes, w_bytes) in disk_io_now.items():
        key = f"{node['name']}|{dev}"
        prev = PREV_DISK_IO.get(key, {"ts": now, "r": r_bytes, "w": w_bytes})
        elapsed = max(0.001, now - prev["ts"])
        rbps = max(0, r_bytes - prev["r"]) / elapsed
        wbps = max(0, w_bytes - prev["w"]) / elapsed
        PREV_DISK_IO[key] = {"ts": now, "r": r_bytes, "w": w_bytes}
        rv, ru = autoscale_bytes_per_sec(rbps)
        wv, wu = autoscale_bytes_per_sec(wbps)
        disk_io_rates[dev] = {"read_value": rv, "read_unit": ru, "write_value": wv, "write_unit": wu, "total_mibps": (rbps + wbps) / (1024 * 1024)}
    for disk in disks:
        dev = mount_to_dev.get(disk["mount"])
        disk["io"] = disk_io_rates.get(dev, {"read_value": 0, "read_unit": "B/s", "write_value": 0, "write_unit": "B/s", "total_mibps": 0})

    netifs = []
    now = time.time()
    for line in net_lines:
        try:
            name, rx_b, tx_b, speed, ip_address = line.split(maxsplit=4)
            rx_b, tx_b, speed = int(rx_b), int(tx_b), int(speed)
        except ValueError:
            continue
        if name in HIDE_INTERFACES:
            continue
        key = f"{node['name']}|{name}"
        prev = PREV_NET.get(key, {"ts": now, "rx": rx_b, "tx": tx_b})
        elapsed = max(0.001, now - prev["ts"])
        rx_bps = max(0, rx_b - prev["rx"]) / elapsed
        tx_bps = max(0, tx_b - prev["tx"]) / elapsed
        PREV_NET[key] = {"ts": now, "rx": rx_b, "tx": tx_b}
        rxv, rxu = autoscale_bytes_per_sec(rx_bps)
        txv, txu = autoscale_bytes_per_sec(tx_bps)
        # Linux reports link speed in Mb/s. RX and TX each have the full
        # link capacity; aggregate utilization uses the full-duplex capacity.
        capacity_bps = speed * 1_000_000 if speed > 0 else 0
        rx_util_pct = (rx_bps * 8 / capacity_bps * 100) if capacity_bps > 0 else 0.0
        tx_util_pct = (tx_bps * 8 / capacity_bps * 100) if capacity_bps > 0 else 0.0
        aggregate_util_pct = ((rx_bps + tx_bps) * 8 / (2 * capacity_bps) * 100) if capacity_bps > 0 else 0.0
        netifs.append({
            "name": name, "ip_address": ip_address, "speed_mbps": max(0, speed),
            "rx_value": rxv, "rx_unit": rxu, "tx_value": txv, "tx_unit": txu,
            "rx_bps": round(rx_bps, 2), "tx_bps": round(tx_bps, 2),
            "rx_util_pct": round(min(100.0, rx_util_pct), 2),
            "tx_util_pct": round(min(100.0, tx_util_pct), 2),
            "aggregate_util_pct": round(min(100.0, aggregate_util_pct), 2),
            "total_mibps": (rx_bps + tx_bps) / (1024 * 1024),
        })

    # RDMA port counters are cumulative 4-byte words. Calculate rates from
    # deltas, independently per node and HCA, and tolerate resets/new devices.
    # Reuse the speed discovered for each physical netdev; do not hardcode NICs.
    netif_speeds = {item["name"]: item["speed_mbps"] for item in netifs}
    rdma = []
    rdma_now = time.time()
    for line in rdma_lines:
        parts = line.split()

        if len(parts) != 9:
            continue

        hca, port, netdev, netdev_ip = parts[:4]

        try:
            netdev_speed = int(parts[4])
            rcv_data, xmit_data, rcv_packets, xmit_packets = map(int, parts[5:])
        except ValueError:
            continue

        except ValueError:
            continue
        key = f"{node['name']}|{hca}|{port}"
        prev = PREV_RDMA.get(key, {
            "ts": rdma_now, "rcv_data": rcv_data, "xmit_data": xmit_data,
            "rcv_packets": rcv_packets, "xmit_packets": xmit_packets,
        })
        elapsed = max(0.001, rdma_now - prev["ts"])
        rcv_delta = max(0, rcv_data - prev["rcv_data"])
        xmit_delta = max(0, xmit_data - prev["xmit_data"])
        rcv_pkt_delta = max(0, rcv_packets - prev["rcv_packets"])
        xmit_pkt_delta = max(0, xmit_packets - prev["xmit_packets"])
        PREV_RDMA[key] = {
            "ts": rdma_now, "rcv_data": rcv_data, "xmit_data": xmit_data,
            "rcv_packets": rcv_packets, "xmit_packets": xmit_packets,
        }
        rx_bps = (rcv_delta * 4) / elapsed
        tx_bps = (xmit_delta * 4) / elapsed
        rxv, rxu = autoscale_bytes_per_sec(rx_bps)
        txv, txu = autoscale_bytes_per_sec(tx_bps)
        rdma.append({
            "hca": hca, "port": port, "netdev": netdev, "ip_address": netdev_ip,
            "speed_mbps": max(0, netdev_speed),
            "rx_value": rxv, "rx_unit": rxu,
            "tx_value": txv, "tx_unit": txu,
            "rx_bps": round(rx_bps, 2), "tx_bps": round(tx_bps, 2),
            "rx_packets_per_sec": round(rcv_pkt_delta / elapsed, 2),
            "tx_packets_per_sec": round(xmit_pkt_delta / elapsed, 2),
            "rx_counter": rcv_data, "tx_counter": xmit_data,
        })

    return {"node": node["name"], "primary_ip": primary_ip, "ip_enp7s7": primary_ip, "load": load, "mem": mem,
            "cpu": percents, "disks": disks, "netifs": netifs, "rdma": rdma, "gpu_util": util,
            "gpu_mem_used": mem_used, "gpu_mem_total": mem_total, "gpu_temp": temp,
            "status": "online", "stale": False, "last_seen": time.time(), "error": None}

def offline_payload(node: dict, exc: Exception) -> dict:
    name = node["name"]
    previous = LAST_GOOD.get(name)
    status = {"status": "offline", "stale": previous is not None,
              "last_seen": previous.get("last_seen") if previous else None,
              "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    if previous is not None:
        result = deepcopy(previous)
        result.update(status)
        return result
    return {"node": name, "primary_ip": "N/A", "ip_enp7s7": "N/A", "load": "N/A", "mem": "0 1", "cpu": [],
            "disks": [], "netifs": [], "rdma": [], "gpu_util": "N/A", "gpu_mem_used": "N/A",
            "gpu_mem_total": "N/A", "gpu_temp": "N/A", **status}

async def collect_one_node(node: dict) -> dict:
    try:
        result = await asyncio.wait_for(collect(node), timeout=NODE_COLLECTION_TIMEOUT)
        LAST_GOOD[node["name"]] = deepcopy(result)
        return result
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"[{node['name']}] collection failed: {type(exc).__name__}: {exc}")
        return offline_payload(node, exc)

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    latest: Dict[str, Dict[str, Any]] = {}
    stop = asyncio.Event()

    async def node_poll_loop(node: dict):
        # Each node owns an independent polling loop. A slow/offline host never
        # delays the WebSocket snapshots for healthy hosts.
        while not stop.is_set():
            latest[node["name"]] = await collect_one_node(node)
            try:
                await asyncio.wait_for(stop.wait(), timeout=REFRESH_INTERVAL)
            except asyncio.TimeoutError:
                pass

    tasks = [asyncio.create_task(node_poll_loop(node)) for node in NODES]
    try:
        while True:
            # Send the most recent snapshot without waiting for every SSH poll.
            payload = [latest.get(node["name"]) or {
                "node": node["name"], "ip_enp7s7": "N/A", "load": "N/A",
                "mem": "0 1", "cpu": [], "disks": [], "netifs": [], "rdma": [],
                "gpu_util": "N/A", "gpu_mem_used": "N/A", "gpu_mem_total": "N/A",
                "gpu_temp": "N/A", "status": "offline", "stale": False,
                "last_seen": None, "error": "Initial connection pending",
            } for node in NODES]
            await ws.send_text(json.dumps(payload))
            await asyncio.sleep(REFRESH_INTERVAL)
    except WebSocketDisconnect:
        print("WebSocket client disconnected")
    except Exception as exc:
        print(f"WebSocket error: {type(exc).__name__}: {exc}")
        try:
            await ws.close()
        except Exception:
            pass
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
