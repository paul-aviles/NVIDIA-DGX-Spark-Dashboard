/*
NVIDIA GDX Spak Cluster Dashboard.
Provides a similar interface like btop but browser based

Author:  Paul Aviles
Created: 02-06-2026
Version: 0.2.0
License: CC-BY-NC-ND-4.0 Creative Commons Attribution Non Commercial No Derivatives 4.0 International
See License.txt
*/

const dashboard = document.getElementById("dashboard");
const nodesMap = {};
const history = {}; // { node: { cpu:[], mem:[], gpu:[], disks:{} } }

const MAX_HISTORY = 180;

function createNodeCard(n) {
    const nodeDiv = document.createElement("div");
    nodeDiv.className = "node";

    const ip = n.ip_enp7s7 || "N/A";

    nodeDiv.innerHTML = `
        <b>${n.node}:</b> <span style="color:#9ca3af">(${ip})</span><br>
        <div class="load"></div>

        <div class="mem"></div>
        <canvas class="spark mem-spark" height="40"></canvas>

        <div class="gpu"></div>
        <canvas class="spark gpu-spark" height="40"></canvas>

        <div class="cpu-overall"></div>
        <div class="cpu-cores"></div>
        <canvas class="spark cpu-spark" height="40"></canvas>

        🌐<br>
        <div class="netifs"></div>

        💽<br>
        <div class="disks"></div>
    `;
    return nodeDiv;
}

function renderBar(container, label, pct, type) {
    container.innerHTML = `
        <div>${label}: ${pct}%</div>
        <div class="bar-container ${type}">
            <div class="bar-fill ${type}" style="width:${pct}%"></div>
        </div>
    `;
}

function drawSpark(canvas, values, color) {
    const parent = canvas.parentElement;
    const fullWidth = parent.clientWidth || 300;
    const width = Math.floor(fullWidth * 0.9);
    canvas.width = width;
    canvas.height = 40;

    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // baseline
    ctx.strokeStyle = "#1f2937";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, canvas.height - 1);
    ctx.lineTo(canvas.width, canvas.height - 1);
    ctx.stroke();

    if (!values.length) return;

    const maxVal = Math.max(...values, 1);
    const len = values.length;

    ctx.strokeStyle = color;
    ctx.lineWidth = 1;
    ctx.beginPath();

    values.forEach((v, i) => {
        const x = (i / (len - 1 || 1)) * canvas.width;
        const y = canvas.height - (v / maxVal) * (canvas.height - 4) - 2;
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });

    ctx.stroke();
}

const ws = new WebSocket(`ws://${location.host}/ws`);

ws.onmessage = e => {
    const nodes = JSON.parse(e.data);

    nodes.forEach((n, idx) => {
        if (!nodesMap[n.node]) {
            const card = createNodeCard(n);

            let col;
            if (idx % 2 === 0) {
                col = document.querySelector(".column.left");
                if (!col) {
                    col = document.createElement("div");
                    col.className = "column left";
                    dashboard.appendChild(col);
                }
            } else {
                col = document.querySelector(".column.right");
                if (!col) {
                    col = document.createElement("div");
                    col.className = "column right";
                    dashboard.appendChild(col);
                }
            }
            col.appendChild(card);

            nodesMap[n.node] = card;
            history[n.node] = { cpu: [], mem: [], gpu: [], disks: {} };
        }

        const card = nodesMap[n.node];

        // ---- Load ----
        card.querySelector(".load").textContent = `Load: ${n.load}`;

        // ---- Memory ----
        const [usedMemMiB, totalMemMiB] = n.mem.split(" ").map(Number);
        const memPct = Math.round(usedMemMiB / totalMemMiB * 100);
        history[n.node].mem.push(memPct);
        if (history[n.node].mem.length > MAX_HISTORY) history[n.node].mem.shift();

        renderBar(card.querySelector(".mem"),
            `Memory: ${(usedMemMiB/1024).toFixed(2)}/${(totalMemMiB/1024).toFixed(2)} GiB`,
            memPct, "memory");
        drawSpark(card.querySelector(".mem-spark"), history[n.node].mem, "#22c55e");

        // ---- GPU ----
        const gpuPct = n.gpu_util !== "N/A" ? parseInt(n.gpu_util) : 0;
        history[n.node].gpu.push(gpuPct);
        if (history[n.node].gpu.length > MAX_HISTORY) history[n.node].gpu.shift();

        renderBar(card.querySelector(".gpu"),
            `█▄ GPU ${n.gpu_mem_used}/${n.gpu_mem_total} MB (${n.gpu_temp}°C)`,
            gpuPct, "gpu");
        drawSpark(card.querySelector(".gpu-spark"), history[n.node].gpu, "#a855f7");

        // ---- CPU overall ----
        const overallCpu = Math.round(n.cpu.reduce((a,b)=>a+b,0)/n.cpu.length);
        history[n.node].cpu.push(overallCpu);
        if (history[n.node].cpu.length > MAX_HISTORY) history[n.node].cpu.shift();

        renderBar(card.querySelector(".cpu-overall"), "💻 CPU", overallCpu, "cpu");

        // ---- CPU cores ----
        const coresDiv = card.querySelector(".cpu-cores");
        coresDiv.innerHTML = `<div style="display:flex;gap:6px">
            <div class="cpu-subcol left"></div>
            <div class="cpu-subcol right"></div>
        </div>`;

        const leftCol = coresDiv.querySelector(".cpu-subcol.left");
        const rightCol = coresDiv.querySelector(".cpu-subcol.right");

        n.cpu.forEach((pct, i) => {
            const d = document.createElement("div");
            renderBar(d, `Core ${i}`, pct, "cpu");
            if (i < Math.ceil(n.cpu.length / 2)) leftCol.appendChild(d);
            else rightCol.appendChild(d);
        });

        drawSpark(card.querySelector(".cpu-spark"), history[n.node].cpu, "#f59e0b");

        // ---- Network ----
        const netDiv = card.querySelector(".netifs");
        netDiv.innerHTML = "";
        if (Array.isArray(n.netifs)) {
            n.netifs.forEach(iface => {
                const pct = Math.min(100, Math.round(iface.total_mibps * 5));
                const line = `Net: ${iface.name} (${iface.speed_mbps} Mbps) In ${iface.rx_value} ${iface.rx_unit} Out ${iface.tx_value} ${iface.tx_unit}`;
                const c = document.createElement("div");
                renderBar(c, line, pct, "normal");
                netDiv.appendChild(c);
            });
        }

        // ---- Disks ----
        const disksDiv = card.querySelector(".disks");
        disksDiv.innerHTML = "";
        n.disks.forEach(d => {
            if (!history[n.node].disks[d.mount]) {
                history[n.node].disks[d.mount] = [];
            }

            const io = d.io || { total_mibps: 0, read_value: 0, read_unit: "B/s", write_value: 0, write_unit: "B/s" };
            const val = io.total_mibps;

            history[n.node].disks[d.mount].push(val);
            if (history[n.node].disks[d.mount].length > MAX_HISTORY) {
                history[n.node].disks[d.mount].shift();
            }

            const totalGiB = ((d.used_mib + d.avail_mib) / 1024).toFixed(2);
            const usedGiB  = (d.used_mib / 1024).toFixed(2);
            const line = `Disk: ${d.mount} [Total: ${totalGiB} GiB | Used: ${usedGiB} GiB] IO R ${io.read_value} ${io.read_unit} W ${io.write_value} ${io.write_unit}`;

            const c = document.createElement("div");
            renderBar(c, line, d.used_pct, "disk");

            const spark = document.createElement("canvas");
            spark.className = "spark";
            spark.height = 40;

            disksDiv.appendChild(c);
            disksDiv.appendChild(spark);

            drawSpark(spark, history[n.node].disks[d.mount], "#06b6d4");
        });
    });
};
