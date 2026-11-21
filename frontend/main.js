/*
NVIDIA DGX Spark Cluster Dashboard.
Provides a similar interface like btop but browser based

Author: Paul Aviles
Created: 02-06-2026
Version: 0.2.6
License: CC-BY-NC-ND-4.0 Creative Commons Attribution Non Commercial No Derivatives 4.0 International
See License.txt
*/

const dashboard = document.getElementById("dashboard");

const graphSettings = {
    scale: true,
    grid: true,
    cpuCores: false
};

try {
    const saved = JSON.parse(
        localStorage.getItem("WEBTOP_GRAPH_SETTINGS") || "{}"
    );
    graphSettings.scale = saved.scale !== false;
    graphSettings.grid = saved.grid !== false;
    // CPU core details are collapsed by default.
    graphSettings.cpuCores = saved.cpuCores === true;
} catch (_) {}

const graphToolbar = document.createElement("div");
graphToolbar.style.cssText =
    "display:flex;align-items:center;justify-content:flex-end;" +
    "gap:18px;padding:6px 10px;font-size:12px;color:#9ca3af;";

function saveGraphSettings() {
    try {
        localStorage.setItem(
            "WEBTOP_GRAPH_SETTINGS",
            JSON.stringify(graphSettings)
        );
    } catch (_) {}
}

function addGraphToggle(label, setting) {
    const wrapper = document.createElement("label");
    wrapper.style.cssText =
        "display:flex;align-items:center;gap:5px;cursor:pointer;";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = graphSettings[setting];
    checkbox.addEventListener("change", () => {
        graphSettings[setting] = checkbox.checked;

        // Immediately update CPU core visibility on all nodes.
        if (setting === "cpuCores") {
            Object.values(nodesMap).forEach(card => {
                const cores = card.querySelector(".cpu-cores");
                if (cores) {
                    cores.style.display =
                        graphSettings.cpuCores ? "" : "none";
                }
            });
        }

        saveGraphSettings();
    });

    const text = document.createElement("span");
    text.textContent = label;

    wrapper.appendChild(checkbox);
    wrapper.appendChild(text);
    graphToolbar.appendChild(wrapper);
}

addGraphToggle("Scale", "scale");
addGraphToggle("Grid", "grid");
addGraphToggle("CPU cores", "cpuCores");

if (dashboard && dashboard.parentNode) {
    dashboard.parentNode.insertBefore(graphToolbar, dashboard);
}

const nodesMap = {};
const history = {};
const MAX_HISTORY = 180;

function createNodeCard(n) {
    const card = document.createElement("div");
    card.className = "node";
    card.innerHTML = `
        <b>${n.node}</b>
        <div class="node-status" aria-live="polite" style="margin-bottom:0; line-height:1.1;"></div>
        <div class="load"></div><div class="mem"></div>
        <canvas class="spark mem-spark" height="40"></canvas>
        <div class="gpu"></div><canvas class="spark gpu-spark" height="40"></canvas>
        <div class="cpu-overall"></div><div class="cpu-cores"></div>
        <canvas class="spark cpu-spark" height="86"></canvas>
        <div class="netifs"></div>RDMA<br><div class="rdma"></div>💽<br><div class="disks"></div>`;
    return card;
}

function renderBar(container, label, pct, type) {
    const value = Number.isFinite(Number(pct))
        ? Math.max(0, Math.min(100, Number(pct)))
        : 0;

    container.innerHTML = `<div>${label}: ${value.toFixed(0)}%</div>
        <div class="bar-container ${type}">
            <div class="bar-fill ${type}" style="width:${value}%"></div>
        </div>`;
}

function drawSpark(canvas, values, color) {
    const width = Math.max(
        1,
        Math.floor((canvas.parentElement.clientWidth || 300) * 0.9)
    );

    canvas.width = width;
    canvas.height = 40;

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    ctx.strokeStyle = "#1f2937";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, 39);
    ctx.lineTo(canvas.width, 39);
    ctx.stroke();

    if (!values.length) return;

    const max = Math.max(...values, 1);

    ctx.strokeStyle = color;
    ctx.beginPath();

    values.forEach((v, i) => {
        const x = (i / (values.length - 1 || 1)) * canvas.width;
        const y = 38 - (v / max) * 34;

        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });

    ctx.stroke();
}

function drawCpuSpark(canvas, values, color = "#f59e0b") {
    const height = 86;
    const showScale = graphSettings.scale;
    const showGrid = graphSettings.grid;

    const leftMargin = showScale ? 43 : 5;
    const rightMargin = showScale ? 43 : 5;
    const topMargin = 7;
    const bottomMargin = 7;

    const width = Math.max(
        180,
        Math.floor(canvas.parentElement.clientWidth || 300)
    );

    canvas.width = width;
    canvas.height = height;
    canvas.style.cssText =
        `display:block;width:100%;height:${height}px;margin-top:8px;`;

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, width, height);

    const plotLeft = leftMargin;
    const plotRight = width - rightMargin;
    const plotTop = topMargin;
    const plotBottom = height - bottomMargin;
    const plotWidth = plotRight - plotLeft;
    const plotHeight = plotBottom - plotTop;

    const tickValues = [100, 75, 50, 25, 0];

    tickValues.forEach(value => {
        const y = plotBottom - (value / 100) * plotHeight;

        if (showGrid) {
            ctx.beginPath();
            ctx.strokeStyle = "rgba(128,128,128,0.22)";
            ctx.lineWidth = 1;
            ctx.setLineDash([3, 3]);
            ctx.moveTo(plotLeft, y);
            ctx.lineTo(plotRight, y);
            ctx.stroke();
            ctx.setLineDash([]);
        }

        if (showScale) {
            ctx.fillStyle = "rgba(156,163,175,0.9)";
            ctx.font = "9px monospace";
            ctx.textBaseline = "middle";

            ctx.textAlign = "right";
            ctx.fillText(`${value}%`, plotLeft - 5, y);

            ctx.textAlign = "left";
            ctx.fillText(`${value}%`, plotRight + 5, y);
        }
    });

    if (showScale) {
        ctx.strokeStyle = "rgba(128,128,128,0.4)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(plotLeft, plotTop);
        ctx.lineTo(plotLeft, plotBottom);
        ctx.moveTo(plotRight, plotTop);
        ctx.lineTo(plotRight, plotBottom);
        ctx.stroke();
    }

    if (!values.length) return;

    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.8;

    values.forEach((value, index) => {
        const x = plotLeft +
            (index / (values.length - 1 || 1)) * plotWidth;

        const boundedValue = Math.max(0, Math.min(100, num(value)));
        const y = plotBottom - (boundedValue / 100) * plotHeight;

        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });

    ctx.stroke();
}



function networkRateToBps(value, unit) {
    const multipliers = {
        "B/s": 1,
        "KB/s": 1000,
        "MB/s": 1000000,
        "GB/s": 1000000000,
        "KiB/s": 1024,
        "MiB/s": 1048576,
        "GiB/s": 1073741824
    };

    return num(value) * (multipliers[unit] || 1);
}


function drawNetworkSpark(canvas, rxValues, txValues) {
    const height = 86;
    const showScale = graphSettings.scale;
    const showGrid = graphSettings.grid;

    const leftMargin = showScale ? 43 : 5;
    const rightMargin = showScale ? 43 : 5;
    const topMargin = 7;
    const bottomMargin = 7;

    const width = Math.max(
        180,
        Math.floor(canvas.parentElement.clientWidth || 300)
    );

    canvas.width = width;
    canvas.height = height;
    canvas.style.cssText =
        `display:block;width:100%;height:${height}px;margin-top:8px;`;

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, width, height);

    const plotLeft = leftMargin;
    const plotRight = width - rightMargin;
    const plotTop = topMargin;
    const plotBottom = height - bottomMargin;
    const plotWidth = plotRight - plotLeft;
    const plotHeight = plotBottom - plotTop;

    const max = Math.max(1, ...rxValues, ...txValues);
    const scaleMax = Math.pow(10, Math.ceil(Math.log10(max)));
    const tickValues = [1, 0.75, 0.5, 0.25, 0];

    function formatRate(value) {
        if (value >= 1073741824)
            return (value / 1073741824).toFixed(1) + " GiB";
        if (value >= 1048576)
            return (value / 1048576).toFixed(1) + " MiB";
        if (value >= 1024)
            return (value / 1024).toFixed(1) + " KiB";
        return value.toFixed(0) + " B";
    }

    tickValues.forEach(fraction => {
        const y = plotBottom - fraction * plotHeight;

        if (showGrid) {
            ctx.beginPath();
            ctx.strokeStyle = "rgba(128,128,128,0.22)";
            ctx.lineWidth = 1;
            ctx.setLineDash([3, 3]);
            ctx.moveTo(plotLeft, y);
            ctx.lineTo(plotRight, y);
            ctx.stroke();
            ctx.setLineDash([]);
        }

        if (showScale) {
            const label = formatRate(scaleMax * fraction);
            ctx.fillStyle = "rgba(156,163,175,0.9)";
            ctx.font = "9px monospace";
            ctx.textBaseline = "middle";

            ctx.textAlign = "right";
            ctx.fillText(label, plotLeft - 5, y);

            ctx.textAlign = "left";
            ctx.fillText(label, plotRight + 5, y);
        }
    });

    if (showScale) {
        ctx.strokeStyle = "rgba(128,128,128,0.4)";
        ctx.lineWidth = 1;

        ctx.beginPath();
        ctx.moveTo(plotLeft, plotTop);
        ctx.lineTo(plotLeft, plotBottom);
        ctx.moveTo(plotRight, plotTop);
        ctx.lineTo(plotRight, plotBottom);
        ctx.stroke();
    }

    function drawLine(values, color) {
        if (!values.length) return;

        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.8;

        values.forEach((value, index) => {
            const x = plotLeft +
                (index / (values.length - 1 || 1)) * plotWidth;

            const y = plotBottom -
                (Math.max(0, value) / scaleMax) * plotHeight;

            if (index === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });

        ctx.stroke();
    }

    drawLine(rxValues, "#22c55e");
    drawLine(txValues, "#38bdf8");
}

function num(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function setStatus(card, n) {
    const offline = n.status === "offline";

    card.classList.toggle("node-offline", offline);
    card.classList.toggle("node-stale", Boolean(n.stale));

    const el = card.querySelector(".node-status");

    if (offline) {
        const when = n.last_seen
            ? new Date(n.last_seen * 1000).toLocaleTimeString()
            : "never";

        el.textContent = n.stale
            ? `OFFLINE — showing stale metrics; last successful update ${when}`
            : `OFFLINE — waiting for connection${n.error ? ` (${n.error})` : ""}`;

        el.style.color = "#f87171";
    } else {
        el.textContent = "ONLINE — live metrics";
        el.style.color = "#22c55e";
    }

    el.style.fontWeight = "bold";
}

const protocol = location.protocol === "https:" ? "wss:" : "ws:";
const ws = new WebSocket(`${protocol}//${location.host}/ws`);

ws.onmessage = event => {
    let nodes;

    try {
        nodes = JSON.parse(event.data);
    } catch (err) {
        console.error("Invalid dashboard payload", err);
        return;
    }

    // One column per node, capped at four.
    const columnCount = Math.max(1, Math.min(nodes.length, 4));
    dashboard.style.setProperty("--node-columns", columnCount);

    nodes.forEach(n => {
        if (!nodesMap[n.node]) {
            dashboard.appendChild(createNodeCard(n));
            nodesMap[n.node] = dashboard.lastElementChild;

            history[n.node] = {
                cpu: [],
                mem: [],
                gpu: [],
                disks: {}
            };
        }

        const card = nodesMap[n.node];

        setStatus(card, n);

        if (n.status === "offline" && !n.stale) {
            [
                "load",
                "mem",
                "gpu",
                "cpu-overall",
                "netifs",
                "rdma",
                "disks"
            ].forEach(cls => {
                card.querySelector(`.${cls}`).textContent =
                    `${cls}: unavailable`;
            });

            return;
        }

        card.querySelector(".load").textContent = `Load: ${n.load}`;

        // Memory
        const mp = String(n.mem || "0 1")
            .split(/\s+/)
            .map(Number);

        const used = num(mp[0]);
        const total = Math.max(1, num(mp[1], 1));
        const memPct = Math.round(used / total * 100);

        history[n.node].mem.push(memPct);

        if (history[n.node].mem.length > MAX_HISTORY) {
            history[n.node].mem.shift();
        }

        renderBar(
            card.querySelector(".mem"),
            `Memory: ${(used / 1024).toFixed(2)}/${(total / 1024).toFixed(2)} GiB`,
            memPct,
            "memory"
        );

        drawSpark(
            card.querySelector(".mem-spark"),
            history[n.node].mem,
            "#22c55e"
        );

        // GPU
        const gpuPct = n.gpu_util !== "N/A"
            ? num(parseInt(n.gpu_util, 10))
            : 0;

        history[n.node].gpu.push(gpuPct);

        if (history[n.node].gpu.length > MAX_HISTORY) {
            history[n.node].gpu.shift();
        }

        renderBar(
            card.querySelector(".gpu"),
            `GPU ${n.gpu_mem_used}/${n.gpu_mem_total} MB (${n.gpu_temp}°C)`,
            gpuPct,
            "gpu"
        );

        drawCpuSpark(
            card.querySelector(".gpu-spark"),
            history[n.node].gpu,
            "#a855f7"
        );

        // CPU
        const cpus = Array.isArray(n.cpu) ? n.cpu : [];

        const cpu = cpus.length
            ? Math.round(
                cpus.reduce((a, b) => a + num(b), 0) / cpus.length
            )
            : 0;

        history[n.node].cpu.push(cpu);

        if (history[n.node].cpu.length > MAX_HISTORY) {
            history[n.node].cpu.shift();
        }

        renderBar(
            card.querySelector(".cpu-overall"),
            "💻 CPU",
            cpu,
            "cpu"
        );

        const cores = card.querySelector(".cpu-cores");
        cores.style.display = graphSettings.cpuCores ? "" : "none";

        cores.innerHTML = `
            <div style="display:flex;gap:6px">
                <div class="cpu-subcol left"></div>
                <div class="cpu-subcol right"></div>
            </div>`;

        const left = cores.querySelector(".left");
        const right = cores.querySelector(".right");

        cpus.forEach((pct, i) => {
            const item = document.createElement("div");

            renderBar(item, `Core ${i}`, num(pct), "cpu");

            (
                i < Math.ceil(cpus.length / 2)
                    ? left
                    : right
            ).appendChild(item);
        });

        drawCpuSpark(
            card.querySelector(".cpu-spark"),
            history[n.node].cpu,
            "#f59e0b"
        );

        // Network interfaces
        // Network interfaces
        const net = card.querySelector(".netifs");
        net.innerHTML = "";

        history[n.node].net ||= {};

        (n.netifs || []).forEach(iface => {
            const speedGbps = num(iface.speed_mbps) / 1000;
            const rxPct = Math.max(0, num(iface.rx_util_pct));
            const txPct = Math.max(0, num(iface.tx_util_pct));

            // Full-duplex: use the busier direction for interface utilization.
            const utilPct = Math.min(100, Math.max(0, num(iface.aggregate_util_pct, (rxPct + txPct) / 2)));

            history[n.node].net[iface.name] ||= {
                rx: [],
                tx: []
            };

            const samples = history[n.node].net[iface.name];

            samples.rx.push(
                networkRateToBps(iface.rx_value, iface.rx_unit)
            );
            samples.tx.push(
                networkRateToBps(iface.tx_value, iface.tx_unit)
            );

            if (samples.rx.length > MAX_HISTORY) samples.rx.shift();
            if (samples.tx.length > MAX_HISTORY) samples.tx.shift();

            const item = document.createElement("div");
            item.style.cssText =
                "width:100%;box-sizing:border-box;padding:10px 0 12px 0;" +
                "border-bottom:1px solid rgba(128,128,128,0.18);";

            const header = document.createElement("div");
            header.style.cssText =
                "display:flex;justify-content:space-between;" +
                "align-items:center;gap:12px;margin-bottom:8px;";

            const name = document.createElement("span");
            const speedLabel = num(iface.speed_mbps) > 0
                ? `${speedGbps.toFixed(0)} Gbps`
                : "N/A";
            name.textContent = `${iface.name} · ${iface.ip_address || "N/A"} · ${speedLabel}`;
            name.style.cssText = "font-weight:500;";

            const percentage = document.createElement("span");
            percentage.textContent =
                `${utilPct > 0 && utilPct < 0.1
                    ? utilPct.toFixed(2)
                    : utilPct.toFixed(0)}%`;

            percentage.style.cssText =
                "font-size:0.85em;white-space:nowrap;color:" +
                (utilPct > 80
                    ? "#ef4444"
                    : utilPct > 50
                        ? "#eab308"
                        : "#16a34a");

            const networkLabel = document.createElement("div");
            networkLabel.style.cssText =
                "display:flex;align-items:center;gap:6px;min-width:0;";

            const networkIcon = document.createElement("span");
            networkIcon.textContent = "🌐";
            networkIcon.style.cssText = "flex-shrink:0;";

            name.style.cssText =
                "font-weight:500;min-width:0;";

            networkLabel.appendChild(networkIcon);
            networkLabel.appendChild(name);

            header.appendChild(networkLabel);
            header.appendChild(percentage);

            const traffic = document.createElement("div");
            traffic.textContent =
                `RX ${iface.rx_value} ${iface.rx_unit}   |   ` +
                `TX ${iface.tx_value} ${iface.tx_unit}`;

            traffic.style.cssText =
                "font-size:0.9em;margin-bottom:7px;color:#16a34a;";

            const track = document.createElement("div");
            track.style.cssText =
                "width:100%;height:7px;" +
                "background:rgba(128,128,128,0.16);" +
                "border-radius:6px;overflow:hidden;";

            const fill = document.createElement("div");
            fill.style.cssText =
                `height:100%;width:${utilPct}%;max-width:100%;` +
                "background:#16a34a;border-radius:6px;" +
                "transition:width 0.5s ease;";

            track.appendChild(fill);

            const graph = document.createElement("canvas");
            graph.className = "network-spark";
            graph.setAttribute("height", "44");

            item.appendChild(header);
            item.appendChild(traffic);
            item.appendChild(track);
            item.appendChild(graph);
            net.appendChild(item);

            drawNetworkSpark(graph, samples.rx, samples.tx);
        });

        const rdma = card.querySelector(".rdma");
        rdma.innerHTML = "";
        history[n.node].rdma ||= {};

        (n.rdma || []).forEach(port => {
            const rdmaSpeed = num(port.speed_mbps) > 0
                ? `${(num(port.speed_mbps) / 1000).toFixed(0)} Gbps`
                : "speed N/A";
            const rdmaInterface = port.netdev && port.netdev !== "unknown"
                ? `${port.netdev} · ${port.ip_address || "N/A"} · ${rdmaSpeed}`
                : `interface/IP unavailable · ${rdmaSpeed}`;
            const label =
                `${port.hca}${port.port ? `/${port.port}` : ""} (${rdmaInterface})`;

            const detail =
                `RX ${port.rx_value} ${port.rx_unit} | TX ${port.tx_value} ${port.tx_unit}`;

            const packetRate =
                `Packets/s RX ${num(port.rx_packets_per_sec).toFixed(0)} packets/s | TX ${num(port.tx_packets_per_sec).toFixed(0)} packets/s`;

            const item = document.createElement("div");
            item.textContent = label;

            const traffic = document.createElement("div");
            traffic.textContent = detail;
            traffic.style.cssText =
                "font-size:0.9em;margin:2px 0 3px 0;color:#16a34a;";

            const sub = document.createElement("div");
            sub.textContent = packetRate;
            sub.style.cssText =
                "font-size:0.85em;color:#9ca3af;margin:2px 0 8px 0";

            rdma.appendChild(item);
            rdma.appendChild(traffic);
            rdma.appendChild(sub);

            // Maintain independent throughput history for each HCA.
            history[n.node].rdma[port.hca] ||= {
                rx: [],
                tx: []
            };

            const samples = history[n.node].rdma[port.hca];

            samples.rx.push(
                networkRateToBps(port.rx_value, port.rx_unit)
            );
            samples.tx.push(
                networkRateToBps(port.tx_value, port.tx_unit)
            );

            if (samples.rx.length > MAX_HISTORY) samples.rx.shift();
            if (samples.tx.length > MAX_HISTORY) samples.tx.shift();

            const graph = document.createElement("canvas");
            graph.className = "rdma-spark";
            graph.setAttribute("height", "44");

            rdma.appendChild(graph);
            drawNetworkSpark(graph, samples.rx, samples.tx);

        });

        if (!(n.rdma || []).length) {
            rdma.textContent = "RDMA counters: unavailable";
        }

        // Disks
        const disks = card.querySelector(".disks");
        disks.innerHTML = "";

        (n.disks || []).forEach(d => {
            history[n.node].disks[d.mount] ||= [];

            const io = d.io || {
                total_mibps: 0,
                read_value: 0,
                read_unit: "B/s",
                write_value: 0,
                write_unit: "B/s"
            };

            history[n.node].disks[d.mount].push(
                num(io.total_mibps)
            );

            if (history[n.node].disks[d.mount].length > MAX_HISTORY) {
                history[n.node].disks[d.mount].shift();
            }

            const totalGiB = (
                (num(d.used_mib) + num(d.avail_mib)) / 1024
            ).toFixed(2);

            const usedGiB = (
                num(d.used_mib) / 1024
            ).toFixed(2);

            const label =
                `Disk: ${d.mount} [Total: ${totalGiB} GiB | Used: ${usedGiB} GiB] IO R ${io.read_value} ${io.read_unit} W ${io.write_value} ${io.write_unit}`;

            const item = document.createElement("div");

            renderBar(
                item,
                label,
                num(d.used_pct),
                "disk"
            );

            const spark = document.createElement("canvas");
            spark.className = "spark";
            spark.height = 40;

            disks.appendChild(item);
            disks.appendChild(spark);

            drawSpark(
                spark,
                history[n.node].disks[d.mount],
                "#06b6d4"
            );
        });
    });
};

ws.onerror = err => {
    console.error("Dashboard WebSocket error:", err);
};

ws.onclose = () => {
    console.warn("Dashboard WebSocket connection closed.");
};