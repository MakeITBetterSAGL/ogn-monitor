const SVG_NS = "http://www.w3.org/2000/svg";
let selectedRange = "24h";
let selectedUnits = "metric";
try {
    selectedUnits = localStorage.getItem("ogn-stats-units") === "imperial" ? "imperial" : "metric";
} catch (error) {
    console.debug("Unit preference storage unavailable", error);
}
let currentData = null;

const KM_TO_MILES = 0.621371;
const METRES_TO_FEET = 3.28084;

function number(value, digits = 0) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
    return Number(value).toLocaleString("en-GB", { maximumFractionDigits: digits });
}

function setSummary(summary) {
    const imperial = selectedUnits === "imperial";
    document.getElementById("summary-packets").textContent = number(summary.packets);
    document.getElementById("summary-positions").textContent = number(summary.positions);
    document.getElementById("summary-aircraft").textContent = number(summary.aircraft);
    document.getElementById("summary-snr").textContent = summary.avg_snr_db === null ? "—" : `${number(summary.avg_snr_db, 1)} dB`;
    document.getElementById("summary-range").textContent = summary.max_distance_km === null ? "—" : `${number(summary.max_distance_km * (imperial ? KM_TO_MILES : 1), 1)} ${imperial ? "mi" : "km"}`;
    document.getElementById("summary-altitude").textContent = summary.max_altitude_m === null ? "—" : `${number(summary.max_altitude_m * (imperial ? METRES_TO_FEET : 1))} ${imperial ? "ft" : "m"}`;
}

function svgElement(name, attributes = {}, text = "") {
    const element = document.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    if (text) element.textContent = text;
    return element;
}

function timeLabel(value) {
    const date = new Date(value);
    if (["2h", "8h", "24h"].includes(selectedRange)) {
        return date.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    }
    if (selectedRange === "7d") {
        return date.toLocaleDateString("en-GB", { weekday: "short", hour: "2-digit" });
    }
    return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
}

function chart(svgId, rows, definitions, unit = "", leftMargin = 54, rightMargin = 78) {
    const svg = document.getElementById(svgId);
    const width = window.matchMedia("(max-width: 600px)").matches ? 560 : 900;
    const height = 260;
    const margin = { top: 14, right: rightMargin, bottom: 34, left: leftMargin };
    const plotWidth = width - margin.left - margin.right;
    const plotHeight = height - margin.top - margin.bottom;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.style.aspectRatio = `${width} / ${height}`;
    svg.replaceChildren();

    const values = definitions.flatMap(definition => rows.map(row => Number(row[definition.key])).filter(Number.isFinite));
    if (!values.length || values.every(value => value === 0)) {
        svg.appendChild(svgElement("text", { x: width / 2, y: height / 2, class: "empty-label" }, "No data for this period"));
        return;
    }
    let minValue = Math.min(0, ...values);
    let maxValue = Math.max(...values);
    if (minValue < 0) minValue = Math.floor(minValue / 5) * 5;
    maxValue = Math.ceil(maxValue * 1.08 || 1);
    if (maxValue === minValue) maxValue = minValue + 1;
    const x = index => margin.left + (rows.length < 2 ? plotWidth / 2 : index / (rows.length - 1) * plotWidth);
    const y = value => margin.top + (maxValue - value) / (maxValue - minValue) * plotHeight;

    for (let step = 0; step <= 4; step += 1) {
        const py = margin.top + step / 4 * plotHeight;
        const tickValue = maxValue - step / 4 * (maxValue - minValue);
        svg.appendChild(svgElement("line", { x1: margin.left, y1: py, x2: width - margin.right, y2: py, class: "grid-line" }));
        svg.appendChild(svgElement("text", { x: margin.left - 8, y: py + 4, "text-anchor": "end" }, `${number(tickValue, Math.abs(tickValue) < 10 ? 1 : 0)}${unit}`));
        svg.appendChild(svgElement("text", { x: width - margin.right + 8, y: py + 4, "text-anchor": "start" }, `${number(tickValue, Math.abs(tickValue) < 10 ? 1 : 0)}${unit}`));
    }
    for (let step = 1; step < 8; step += 1) {
        const px = margin.left + step / 8 * plotWidth;
        svg.appendChild(svgElement("line", { x1: px, y1: margin.top, x2: px, y2: height - margin.bottom, class: "grid-line vertical-grid-line" }));
    }
    svg.appendChild(svgElement("line", { x1: margin.left, y1: margin.top, x2: margin.left, y2: height - margin.bottom, class: "axis-line" }));
    svg.appendChild(svgElement("line", { x1: width - margin.right, y1: margin.top, x2: width - margin.right, y2: height - margin.bottom, class: "axis-line" }));
    svg.appendChild(svgElement("line", { x1: margin.left, y1: height - margin.bottom, x2: width - margin.right, y2: height - margin.bottom, class: "axis-line" }));

    const labelIndexes = [...new Set([0, Math.floor((rows.length - 1) / 4), Math.floor((rows.length - 1) / 2), Math.floor((rows.length - 1) * 3 / 4), rows.length - 1])];
    for (const index of labelIndexes) {
        svg.appendChild(svgElement("text", { x: x(index), y: height - 12, "text-anchor": index === 0 ? "start" : index === rows.length - 1 ? "end" : "middle" }, timeLabel(rows[index].timestamp)));
    }

    for (const definition of definitions) {
        const segments = [];
        let current = [];
        rows.forEach((row, index) => {
            const value = Number(row[definition.key]);
            if (Number.isFinite(value)) current.push([x(index), y(value), value, index]);
            else if (current.length) { segments.push(current); current = []; }
        });
        if (current.length) segments.push(current);
        for (const points of segments) {
            const pathData = points.map((point, index) => `${index ? "L" : "M"}${point[0].toFixed(2)},${point[1].toFixed(2)}`).join(" ");
            svg.appendChild(svgElement("path", { d: pathData, stroke: definition.color, class: "series-line" }));
            for (const point of points) {
                const circle = svgElement("circle", { cx: point[0], cy: point[1], r: 4, fill: definition.color, class: "point" });
                circle.appendChild(svgElement("title", {}, `${timeLabel(rows[point[3]].timestamp)} · ${definition.label}: ${number(point[2], definition.digits ?? 1)}${unit}`));
                svg.appendChild(circle);
            }
        }
    }
}

function render(data) {
    currentData = data;
    setSummary(data.summary);
    const perMinute = 60 / data.bucket_seconds;
    const imperial = selectedUnits === "imperial";
    const rows = data.series.map(row => ({
        ...row,
        packet_rate: row.packets * perMinute,
        position_rate: row.positions * perMinute,
        flarm_rate: row.flarm * perMinute,
        fanet_rate: row.fanet * perMinute,
        adsl_rate: row.adsl * perMinute,
        display_max_distance: row.max_distance_km === null ? null : row.max_distance_km * (imperial ? KM_TO_MILES : 1),
        display_avg_distance: row.avg_distance_km === null ? null : row.avg_distance_km * (imperial ? KM_TO_MILES : 1),
        display_max_altitude: row.max_altitude_m === null ? null : row.max_altitude_m * (imperial ? METRES_TO_FEET : 1),
        display_avg_altitude: row.avg_altitude_m === null ? null : row.avg_altitude_m * (imperial ? METRES_TO_FEET : 1),
        display_max_speed: row.max_speed_kmh === null ? null : row.max_speed_kmh * (imperial ? KM_TO_MILES : 1),
        display_avg_speed: row.avg_speed_kmh === null ? null : row.avg_speed_kmh * (imperial ? KM_TO_MILES : 1),
    }));
    chart("chart-traffic", rows, [
        { key: "packet_rate", label: "Packets", color: "#38bdf8" },
        { key: "position_rate", label: "Positions", color: "#4ade80" },
    ]);
    chart("chart-aircraft", rows, [{ key: "aircraft", label: "Aircraft", color: "#a78bfa", digits: 0 }]);
    chart("chart-range", rows, [
        { key: "display_max_distance", label: "Maximum", color: "#2dd4bf" },
        { key: "display_avg_distance", label: "Average", color: "#60a5fa" },
    ], imperial ? " mi" : " km", 78);
    chart("chart-signal", rows, [
        { key: "max_snr_db", label: "Peak", color: "#facc15" },
        { key: "avg_snr_db", label: "Average", color: "#fb923c" },
    ], " dB");
    chart("chart-altitude", rows, [
        { key: "display_max_altitude", label: "Maximum", color: "#22d3ee", digits: 0 },
        { key: "display_avg_altitude", label: "Average", color: "#818cf8", digits: 0 },
    ], imperial ? " ft" : " m", 90);
    chart("chart-speed", rows, [
        { key: "display_max_speed", label: "Maximum", color: "#f472b6", digits: 0 },
        { key: "display_avg_speed", label: "Average", color: "#c084fc", digits: 0 },
    ], imperial ? " mph" : " km/h", 90);
    chart("chart-protocols", rows, [
        { key: "flarm_rate", label: "FLARM", color: "#3b82f6" },
        { key: "fanet_rate", label: "FANET", color: "#22c55e" },
        { key: "adsl_rate", label: "ADS-L", color: "#f97316" },
    ]);
    document.getElementById("stats-updated").textContent = `Updated ${new Date(data.generated_at).toLocaleTimeString("en-GB")}`;
}

async function loadStatistics() {
    const error = document.getElementById("stats-error");
    error.hidden = true;
    document.getElementById("stats-updated").textContent = "Loading…";
    try {
        const response = await fetch(`/api/statistics?range=${encodeURIComponent(selectedRange)}`, { cache: "no-store" });
        if (!response.ok) throw new Error(`Statistics request failed (${response.status})`);
        render(await response.json());
    } catch (failure) {
        error.textContent = failure.message;
        error.hidden = false;
        document.getElementById("stats-updated").textContent = "Update failed";
    }
}

document.querySelectorAll("[data-range]").forEach(button => button.addEventListener("click", () => {
    selectedRange = button.dataset.range;
    document.querySelectorAll("[data-range]").forEach(item => item.classList.toggle("active", item === button));
    loadStatistics();
}));

function updateUnitSelector() {
    document.querySelectorAll("[data-units]").forEach(button => {
        const active = button.dataset.units === selectedUnits;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
    });
}

document.querySelectorAll("[data-units]").forEach(button => button.addEventListener("click", () => {
    selectedUnits = button.dataset.units;
    try { localStorage.setItem("ogn-stats-units", selectedUnits); } catch (error) {
        console.debug("Unit preference storage unavailable", error);
    }
    updateUnitSelector();
    if (currentData) render(currentData);
}));

updateUnitSelector();
loadStatistics();
setInterval(loadStatistics, 60_000);

let resizeTimer;
window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
        if (currentData) render(currentData);
    }, 150);
});
