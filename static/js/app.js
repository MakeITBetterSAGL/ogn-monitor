"use strict";

const STATION = window.OGN_CONFIG.station;
const DISPLAY_LOCALE = "en-GB";
const DISPLAY_TIME_ZONE = STATION.timezone;
const REFRESH = { stats: 5000, aircraft: 5000, tracks: 10000, system: 15000, history: 60000, archive: 300000 };
const mapCard = document.querySelector(".map-card");
const activeAircraftCard = document.querySelector(".table-card");
if (mapCard && activeAircraftCard) mapCard.after(activeAircraftCard);
const map = L.map("map", { preferCanvas: true }).setView([STATION.latitude, STATION.longitude], 12);
const coverageRenderer = L.canvas({ padding: .5 });
const baseMaps = {
    street: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: "&copy; OpenStreetMap contributors"
    }),
    topographic: L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
        maxZoom: 17,
        attribution: "Map data: &copy; OpenStreetMap contributors, SRTM | Map style: &copy; OpenTopoMap (CC-BY-SA)"
    }),
    satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
        maxZoom: 19,
        attribution: "Tiles &copy; Esri and contributors"
    })
};
let selectedBaseMap = "topographic";
try {
    const savedBaseMap = localStorage.getItem("ogn-map-style");
    if (baseMaps[savedBaseMap]) selectedBaseMap = savedBaseMap;
} catch (error) {
    console.debug("Map preference storage unavailable", error);
}
baseMaps[selectedBaseMap].addTo(map);

function setBaseMap(style) {
    if (!baseMaps[style] || style === selectedBaseMap) return;
    map.removeLayer(baseMaps[selectedBaseMap]);
    selectedBaseMap = style;
    baseMaps[selectedBaseMap].addTo(map);
    try { localStorage.setItem("ogn-map-style", selectedBaseMap); } catch (error) {
        console.debug("Map preference storage unavailable", error);
    }
}

L.marker([STATION.latitude, STATION.longitude])
    .addTo(map)
    .bindPopup(`<strong>${escapeHtml(STATION.name)}</strong><br>OGN receiver`);

const aircraftMarkers = new Map();
const aircraftTracks = new Map();
let selectedAircraftId = null;
let latestAircraft = [];
let searchTerm = "";
let protocolFilter = "ALL";
let sortState = { key: "aircraft_id", direction: 1 };
let archiveDays = 7;
let selectedHistoryDate = null;
let mapMode = "live";
let replayPoints = [];
let replayIndex = 0;
let replayPlaying = false;
let replayAnimationFrame = null;
let replayStartedAt = 0;
let replayRecordedStart = 0;
let replayMarker = null;
let replayTrack = null;
let coverageLayer = null;
let coverageSummary = null;
let coverageStyle = "density";
let latestHistorySessions = [];
let historySortState = { key: "last_received", direction: -1 };
let previousActiveAircraftCount = Number(document.getElementById("active-aircraft")?.textContent || 0);
const activeAircraftToggle = activeAircraftCard.querySelector("summary");
activeAircraftToggle.setAttribute("aria-disabled", String(!previousActiveAircraftCount));
activeAircraftToggle.addEventListener("click", event => { if (!previousActiveAircraftCount) event.preventDefault(); });
const HISTORY_PAGE_SIZE = 10;
let historyPage = 1;
const aircraftMetadataCache = new Map();
const historyDayDetailPanel = document.querySelector(".history-day-detail");

function formatNumber(value, decimals = 0) {
    return value === null || value === undefined || !Number.isFinite(Number(value))
        ? "—" : Number(value).toFixed(decimals);
}
function formatInteger(value) { return Number(value || 0).toLocaleString(DISPLAY_LOCALE); }
function protocolKey(protocol) { return String(protocol || "OTHER").toUpperCase().replace(/[^A-Z]/g, ""); }
function protocolColor(protocol) {
    return { FLARM: "#3b82f6", FANET: "#22c55e", ADSL: "#f97316" }[protocolKey(protocol)] || "#a855f7";
}
function protocolClass(protocol) {
    return { FLARM: "aircraft-flarm", FANET: "aircraft-fanet", ADSL: "aircraft-adsl" }[protocolKey(protocol)] || "aircraft-other";
}
function aircraftTypeLabel(type) {
    return {
        glider: "Glider", tow_plane: "Tow plane", helicopter: "Helicopter",
        skydiver: "Skydiver", drop_plane: "Drop plane", hang_glider: "Hang glider",
        paraglider: "Paraglider", powered_aircraft: "Powered aircraft",
        jet_aircraft: "Jet / turboprop", balloon: "Balloon", airship: "Airship",
        uav: "UAV / drone", static_obstacle: "Static obstacle", unknown: "Unknown"
    }[type || "unknown"] || "Unknown";
}
function aircraftGlyph(type) {
    return {
        glider: "➤", tow_plane: "✈", helicopter: "✣", skydiver: "◇",
        drop_plane: "✈", hang_glider: "⌃", paraglider: "◡",
        powered_aircraft: "✈", jet_aircraft: "✈", balloon: "●",
        airship: "⬭", uav: "✥", static_obstacle: "■", unknown: "➤"
    }[type || "unknown"] || "➤";
}
function aircraftProtocolLabel(aircraft) {
    const protocols = Array.isArray(aircraft.protocols) && aircraft.protocols.length
        ? aircraft.protocols : [aircraft.protocol || "OTHER"];
    return protocols.join(" / ");
}
function escapeHtml(value) {
    const element = document.createElement("span");
    element.textContent = String(value ?? "—");
    return element.innerHTML;
}
function secondsLabel(seconds) {
    if (seconds === null || seconds === undefined) return "No packets";
    if (seconds < 60) return `${seconds} s ago`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes} min ago`;
    return `${Math.floor(minutes / 60)} h ago`;
}
function timestampAge(value) {
    const milliseconds = Date.now() - new Date(value).getTime();
    if (!Number.isFinite(milliseconds)) return "—";
    return secondsLabel(Math.max(0, Math.floor(milliseconds / 1000)));
}
function formatBytes(bytes) {
    const value = Number(bytes);
    if (!Number.isFinite(value) || value < 0) return "—";
    const units = ["B", "KB", "MB", "GB", "TB"];
    const index = Math.min(Math.floor(Math.log(Math.max(value, 1)) / Math.log(1024)), units.length - 1);
    return `${(value / 1024 ** index).toFixed(index < 2 ? 0 : 1)} ${units[index]}`;
}
function formatDuration(seconds) {
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    return days > 0 ? `${days} d ${hours} h` : `${hours} h ${Math.floor((seconds % 3600) / 60)} min`;
}
function formatFlightDuration(seconds) {
    const value = Number(seconds || 0);
    const hours = Math.floor(value / 3600);
    const minutes = Math.floor((value % 3600) / 60);
    return hours > 0 ? `${hours} h ${minutes} min` : `${minutes} min`;
}
function formatHistoryTime(value) {
    const timestamp = new Date(value);
    return Number.isNaN(timestamp.getTime()) ? "—" : timestamp.toLocaleTimeString(DISPLAY_LOCALE, { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: DISPLAY_TIME_ZONE });
}
function formatEuropeanDate(value, includeWeekday = false) {
    const timestamp = new Date(String(value).length === 10 ? `${value}T12:00:00` : value);
    if (Number.isNaN(timestamp.getTime())) return "—";
    return timestamp.toLocaleDateString(DISPLAY_LOCALE, {
        ...(includeWeekday ? { weekday: "long" } : {}),
        day: "2-digit", month: "2-digit", year: "numeric",
        timeZone: DISPLAY_TIME_ZONE,
    });
}
function formatCompactDayDate(value) {
    const timestamp = new Date(String(value).length === 10 ? `${value}T12:00:00` : value);
    if (Number.isNaN(timestamp.getTime())) return "—";
    const weekday = timestamp.toLocaleDateString("en-GB", { weekday: "short", timeZone: DISPLAY_TIME_ZONE });
    const day = timestamp.toLocaleDateString("en-GB", { day: "2-digit", timeZone: DISPLAY_TIME_ZONE });
    const month = timestamp.toLocaleDateString("en-GB", { month: "2-digit", timeZone: DISPLAY_TIME_ZONE });
    const year = timestamp.toLocaleDateString("en-GB", { year: "2-digit", timeZone: DISPLAY_TIME_ZONE });
    return `${weekday}, ${day}.${month}.${year}`;
}
function formatEuropeanDateTime(value) {
    const timestamp = new Date(value);
    return Number.isNaN(timestamp.getTime()) ? "—" : timestamp.toLocaleString(DISPLAY_LOCALE, {
        day: "2-digit", month: "2-digit", year: "numeric",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
        hour12: false, timeZone: DISPLAY_TIME_ZONE,
    });
}
function aircraftIcon(aircraft, selected = false) {
    const course = Number.isFinite(Number(aircraft.course_deg)) ? Number(aircraft.course_deg) : 0;
    return L.divIcon({
        className: "",
        html: `<div class="aircraft-icon aircraft-type-${escapeHtml(aircraft.aircraft_type || "unknown")} ${protocolClass(aircraft.protocol)}${selected ? " aircraft-selected" : ""}" title="${escapeHtml(aircraftTypeLabel(aircraft.aircraft_type))}" style="transform:rotate(${course - 45}deg)">${aircraftGlyph(aircraft.aircraft_type)}</div>`,
        iconSize: [32, 32], iconAnchor: [16, 16], popupAnchor: [0, -16]
    });
}
function markerPopup(aircraft) {
    return `<strong>${escapeHtml(aircraft.aircraft_id)}</strong><br>
        Aircraft type: ${escapeHtml(aircraftTypeLabel(aircraft.aircraft_type))}<br>
        Protocols: ${escapeHtml(aircraftProtocolLabel(aircraft))}<br>
        Source IDs: ${escapeHtml((aircraft.raw_aircraft_ids || [aircraft.aircraft_id]).join(", "))}<br>
        Dist.: ${formatNumber(aircraft.distance_km, 1)} km<br>
        Altitude: ${formatNumber(aircraft.altitude_m)} m<br>
        Speed: ${formatNumber(aircraft.speed_kmh)} km/h<br>
        Course: ${formatNumber(aircraft.course_deg)}°<br>
        Climb rate: ${formatNumber(aircraft.climb_ms, 1)} m/s<br>
        SNR: ${formatNumber(aircraft.snr_db, 1)} dB<br>
        Frequency offset: ${formatNumber(aircraft.frequency_offset_khz, 1)} kHz`;
}
function setConnectionState(text, error = false) {
    const element = document.getElementById("update-state");
    element.textContent = text;
    element.classList.toggle("error", error);
}
async function getJson(url) {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    return response.json();
}
function selectAircraft(aircraftId, center = true) {
    selectedAircraftId = aircraftId;
    for (const [id, entry] of aircraftMarkers) entry.marker.setIcon(aircraftIcon(entry.data, id === aircraftId));
    document.querySelectorAll("#aircraft-table tr").forEach(row => row.classList.toggle("selected", row.dataset.aircraftId === aircraftId));
    const entry = aircraftMarkers.get(aircraftId);
    if (entry) {
        if (center) map.setView(entry.marker.getLatLng(), Math.max(map.getZoom(), 13));
        entry.marker.openPopup();
        updateDetails(entry.data);
    }
}
function clearSelection() {
    selectedAircraftId = null;
    for (const entry of aircraftMarkers.values()) entry.marker.setIcon(aircraftIcon(entry.data));
    renderAircraft();
    document.getElementById("aircraft-details").innerHTML = `<div class="details-placeholder"><span class="details-icon">➤</span><strong>Select an aircraft</strong><span>Click a marker or table row to view live details.</span></div>`;
}
function updateDetails(aircraft) {
    const details = document.getElementById("aircraft-details");
    details.innerHTML = `<div class="details-header"><div><h3>${escapeHtml(aircraft.aircraft_id)}</h3><span class="protocol-pill">${escapeHtml(aircraftProtocolLabel(aircraft))}</span></div><button class="details-close" type="button" aria-label="Close aircraft details">×</button></div>
        <dl class="details-grid">
            <div><dt>Aircraft type</dt><dd>${escapeHtml(aircraftTypeLabel(aircraft.aircraft_type))}</dd></div>
            <div><dt>Dist.</dt><dd>${formatNumber(aircraft.distance_km, 1)} km</dd></div>
            <div><dt>Last received</dt><dd>${timestampAge(aircraft.received_at)}</dd></div>
            <div><dt>Altitude</dt><dd>${formatNumber(aircraft.altitude_m)} m</dd></div>
            <div><dt>Speed</dt><dd>${formatNumber(aircraft.speed_kmh)} km/h</dd></div>
            <div><dt>Course</dt><dd>${formatNumber(aircraft.course_deg)}°</dd></div>
            <div><dt>Climb rate</dt><dd>${formatNumber(aircraft.climb_ms, 1)} m/s</dd></div>
            <div><dt>SNR</dt><dd>${formatNumber(aircraft.snr_db, 1)} dB</dd></div>
            <div><dt>Frequency offset</dt><dd>${formatNumber(aircraft.frequency_offset_khz, 1)} kHz</dd></div>
            <div class="details-wide"><dt>Position</dt><dd>${formatNumber(aircraft.latitude, 5)}, ${formatNumber(aircraft.longitude, 5)}</dd></div>
            <div class="details-wide"><dt>Source IDs</dt><dd>${escapeHtml((aircraft.raw_aircraft_ids || [aircraft.aircraft_id]).join(", "))}</dd></div>
        </dl>`;
    details.querySelector(".details-close").addEventListener("click", clearSelection);
}
function compareAircraft(a, b) {
    const first = a[sortState.key];
    const second = b[sortState.key];
    if (first === null || first === undefined) return 1;
    if (second === null || second === undefined) return -1;
    const result = typeof first === "number"
        ? first - second
        : String(first).localeCompare(String(second), "en", { numeric: true, sensitivity: "base" });
    return result * sortState.direction;
}
function filteredAircraft() {
    return latestAircraft
        .filter(aircraft => !searchTerm || [aircraft.aircraft_id, ...(aircraft.raw_aircraft_ids || [])].some(id => String(id).toUpperCase().includes(searchTerm)))
        .filter(aircraft => protocolFilter === "ALL" || (aircraft.protocols || [aircraft.protocol]).some(protocol => protocolKey(protocol) === protocolKey(protocolFilter)))
        .sort(compareAircraft);
}
function renderAircraft() {
    updateTable(filteredAircraft());
}
function updateTable(aircraftList) {
    const body = document.getElementById("aircraft-table");
    const empty = document.getElementById("empty-message");
    document.getElementById("aircraft-count").textContent = aircraftList.length === latestAircraft.length
        ? aircraftList.length : `${aircraftList.length}/${latestAircraft.length}`;
    body.replaceChildren();
    empty.hidden = aircraftList.length > 0;
    empty.textContent = latestAircraft.length === 0
        ? "No traffic in the last 10 mins."
        : "No aircraft match the current search and filters.";
    for (const aircraft of aircraftList) {
        const row = document.createElement("tr");
        row.tabIndex = 0;
        row.dataset.aircraftId = aircraft.aircraft_id;
        row.classList.toggle("selected", aircraft.aircraft_id === selectedAircraftId);
        row.innerHTML = `<td><strong>${escapeHtml(aircraft.aircraft_id)}</strong></td>
            <td><span class="protocol-pill">${escapeHtml(aircraftProtocolLabel(aircraft))}</span></td>
            <td>${formatNumber(aircraft.distance_km, 1)} km</td><td>${formatNumber(aircraft.altitude_m)} m</td>
            <td>${formatNumber(aircraft.speed_kmh)} km/h</td><td>${formatNumber(aircraft.course_deg)}°</td>
            <td>${formatNumber(aircraft.snr_db, 1)} dB</td>`;
        const activate = () => selectAircraft(aircraft.aircraft_id);
        row.addEventListener("click", activate);
        row.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); } });
        body.appendChild(row);
    }
}
async function refreshStats() {
    const stats = await getJson("/api/stats");
    document.getElementById("total-packets").textContent = formatInteger(stats.total_packets);
    document.getElementById("aircraft-today").textContent = formatInteger(stats.aircraft_today);
    document.getElementById("active-aircraft").textContent = formatInteger(stats.active_aircraft);
    document.getElementById("last-packet").textContent = secondsLabel(stats.seconds_since_last);
    const status = document.getElementById("receiver-status");
    const activeTraffic = Boolean(stats.active_traffic);
    const receiverOnline = Boolean(stats.receiver_online);
    status.textContent = activeTraffic
        ? "Online"
        : receiverOnline
            ? "No active traffic right now"
            : "Receiver offline";
    status.classList.toggle("online", activeTraffic);
    status.classList.toggle("idle", receiverOnline && !activeTraffic);
    status.classList.toggle("offline", !receiverOnline);
}
async function refreshSystem() {
    const system = await getJson("/api/system");
    document.getElementById("health-temperature").textContent = system.temperature_c === null ? "—" : `${formatNumber(system.temperature_c, 1)} °C`;
    document.getElementById("health-load").textContent = `${formatNumber(system.load.one_minute, 2)} / ${system.load.cpu_count}`;
    document.getElementById("health-memory").textContent = formatBytes(system.memory.available_bytes);
    document.getElementById("health-disk").textContent = formatBytes(system.disk.free_bytes);
    document.getElementById("health-uptime").textContent = formatDuration(system.uptime_seconds);
    document.getElementById("health-database").textContent = formatBytes(system.database_bytes);
    for (const service of ["decoder", "collector", "monitor"]) {
        document.getElementById(`service-${service}`).classList.toggle("running", Boolean(system.services[service]));
    }
    const allRunning = Object.values(system.services).every(Boolean);
    const warm = system.temperature_c !== null && system.temperature_c >= 75;
    const summary = document.getElementById("health-summary");
    summary.textContent = allRunning ? (warm ? "Running warm" : "All systems operational") : "Service attention required";
    summary.classList.toggle("healthy", allRunning && !warm);
    summary.classList.toggle("warning", warm || !allRunning);
}
async function refreshHistory() {
    const history = await getJson("/api/history/today");
    document.getElementById("activity-packets").textContent = formatInteger(history.packets);
    document.getElementById("activity-aircraft").textContent = formatInteger(history.aircraft);
    document.getElementById("activity-altitude").textContent = `${formatNumber(history.max_altitude_m)} m`;
    document.getElementById("activity-speed").textContent = `${formatNumber(history.max_speed_kmh)} km/h`;
    document.getElementById("activity-distance").textContent = `${formatNumber(history.max_distance_km, 1)} km`;
    document.getElementById("activity-summary").textContent = `${formatInteger(history.packets)} packets · ${formatInteger(history.aircraft)} aircraft`;

    const maximumPackets = Math.max(1, ...history.hourly_packets.map(item => item.packets));
    const chart = document.getElementById("hourly-chart");
    chart.replaceChildren();
    for (const item of history.hourly_packets) {
        const bar = document.createElement("div");
        bar.className = "hour-bar";
        bar.dataset.hour = String(item.hour).padStart(2, "0");
        bar.title = `${bar.dataset.hour}:00 — ${formatInteger(item.packets)} packets`;
        bar.style.setProperty("--bar-height", `${Math.max(2, item.packets / maximumPackets * 100)}%`);
        chart.appendChild(bar);
    }

    const maximumAircraft = Math.max(1, ...history.protocols.map(item => item.aircraft));
    const protocols = document.getElementById("protocol-summary");
    protocols.replaceChildren();
    if (history.protocols.length === 0) protocols.textContent = "No protocol data today.";
    for (const item of history.protocols) {
        const row = document.createElement("div");
        row.className = "protocol-row";
        row.innerHTML = `<span>${escapeHtml(item.protocol)}</span><div class="protocol-track"><div class="protocol-fill" style="--protocol-width:${item.aircraft / maximumAircraft * 100}%;--protocol-color:${protocolColor(item.protocol)}"></div></div><strong>${formatInteger(item.aircraft)}</strong>`;
        protocols.appendChild(row);
    }
}
function placeHistoryDayDetail(day, scrollToCard = false) {
    const card = document.querySelector(`.history-day-card[data-date="${CSS.escape(day)}"]`);
    if (!card || !historyDayDetailPanel) return;
    document.querySelectorAll(".history-day-card").forEach(item => {
        const selected = item === card;
        item.classList.toggle("selected", selected);
        item.querySelector(".history-day-summary")?.setAttribute("aria-expanded", String(selected));
    });
    card.appendChild(historyDayDetailPanel);
    if (scrollToCard) card.scrollIntoView({ behavior: "smooth", block: "start" });
}
function closeHistoryDay() {
    selectedHistoryDate = null;
    document.querySelectorAll(".history-day-card").forEach(item => {
        item.classList.remove("selected");
        item.querySelector(".history-day-summary")?.setAttribute("aria-expanded", "false");
    });
    historyDayDetailPanel?.remove();
}
async function loadHistoryDay(day, scrollToCard = false) {
    selectedHistoryDate = day;
    historyPage = 1;
    placeHistoryDayDetail(day);
    historySortState = { key: "last_received", direction: -1 };
    syncHistorySortControls();
    document.getElementById("history-date").value = day;
    const detail = await getJson(`/api/history/day?date=${encodeURIComponent(day)}`);
    document.getElementById("history-day-title").textContent = formatEuropeanDate(day, true);
    document.getElementById("history-day-distance").textContent = detail.max_distance_km === null ? "" : `Max. dist. ${formatNumber(detail.max_distance_km, 1)} km`;
    const protocolContainer = document.getElementById("history-day-protocols");
    protocolContainer.replaceChildren();
    for (const protocol of detail.protocols) {
        const pill = document.createElement("span");
        pill.className = "history-protocol-pill";
        pill.textContent = `${protocol.protocol}: ${protocol.aircraft}`;
        protocolContainer.appendChild(pill);
    }
    latestHistorySessions = detail.aircraft;
    const typeSelector = document.getElementById("history-type-filter");
    typeSelector.innerHTML = `<option value="ALL">All aircraft types</option>`;
    const types = [...new Set(detail.aircraft.map(item => item.aircraft_type || "unknown"))]
        .sort((a, b) => aircraftTypeLabel(a).localeCompare(aircraftTypeLabel(b)));
    for (const type of types) {
        const option = document.createElement("option");
        option.value = type; option.textContent = aircraftTypeLabel(type);
        typeSelector.appendChild(option);
    }
    renderHistorySessions();
    placeHistoryDayDetail(day, scrollToCard);
}
function filteredHistorySessions() {
    const search = document.getElementById("history-aircraft-filter").value.trim().toUpperCase();
    const protocol = document.getElementById("history-protocol-filter").value;
    const type = document.getElementById("history-type-filter").value;
    return latestHistorySessions.filter(session => {
        const identifiers = [session.aircraft_id, ...(session.raw_aircraft_ids || [])];
        const matchesSearch = !search || identifiers.some(value => String(value).toUpperCase().includes(search));
        const matchesProtocol = protocol === "ALL" || (session.protocols || [session.protocol]).some(value => protocolKey(value) === protocolKey(protocol));
        const matchesType = type === "ALL" || (session.aircraft_type || "unknown") === type;
        return matchesSearch && matchesProtocol && matchesType;
    }).sort((first, second) => {
        const key = historySortState.key;
        const displayValue = session => {
            if (key === "aircraft_type") return aircraftTypeLabel(session.aircraft_type);
            if (key === "protocol") return aircraftProtocolLabel(session);
            return session[key];
        };
        const firstValue = displayValue(first);
        const secondValue = displayValue(second);
        if (firstValue === null || firstValue === undefined) return secondValue === null || secondValue === undefined ? 0 : 1;
        if (secondValue === null || secondValue === undefined) return -1;
        const result = typeof firstValue === "number"
            ? firstValue - secondValue
            : String(firstValue).localeCompare(String(secondValue), "en", { numeric: true, sensitivity: "base" });
        return result * historySortState.direction;
    });
}
function syncHistorySortControls() {
    document.getElementById("history-mobile-sort").value = historySortState.key;
    document.getElementById("history-mobile-direction").value = String(historySortState.direction);
    document.querySelectorAll(".history-sort-button").forEach(button => {
        const active = button.dataset.historySort === historySortState.key;
        button.classList.toggle("active", active);
        button.querySelector("span").textContent = active ? (historySortState.direction === 1 ? "▲" : "▼") : "";
    });
}
async function openHistoryReplay(session) {
    if (!selectedHistoryDate) return;
    document.getElementById("replay-date").value = selectedHistoryDate;
    await setMapMode("replay");
    const selector = document.getElementById("replay-session");
    selector.value = `${session.aircraft_id}|${session.session_number}`;
    await loadReplaySession();
    document.querySelector(".map-card").scrollIntoView({ behavior: "smooth", block: "start" });
}
function renderAircraftMetadata(metadata) {
    const records = metadata.records || [];
    const recordHtml = records.length ? records.map(record => `
        <div class="metadata-record">
            <div><span>Registration</span><strong>${escapeHtml(record.registration || "Unknown")}</strong></div>
            <div><span>Aircraft model</span><strong>${escapeHtml(record.aircraft_model || "Unknown")}</strong></div>
            <div><span>Competition ID</span><strong>${escapeHtml(record.competition_id || "—")}</strong></div>
            <div><span>Device</span><strong>${escapeHtml(record.device_type)} · ${escapeHtml(record.device_id)}</strong></div>
        </div>`).join("") : `<div class="metadata-empty">No public identification record was found for this aircraft ID.</div>`;
    const sourceLabel = name => /OGN/i.test(name) ? "OGN DB" : /OpenSky/i.test(name) ? "OpenSky" : /FOCA|Swiss Aircraft Register/i.test(name) ? "FOCA" : name;
    const sources = (metadata.sources || []).map(source => `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(sourceLabel(source.name))} ↗</a>`).join("");
    return `<div class="aircraft-metadata-panel"><div class="metadata-heading"><strong>Aircraft information</strong><span>Public aviation metadata only</span></div>${recordHtml}<div class="metadata-sources"><span>Sources</span>${sources}</div></div>`;
}
async function toggleAircraftMetadata(session, row) {
    if (row) {
        if (row.classList.contains("metadata-active")) {
            row.classList.remove("metadata-active");
            row.querySelector(".aircraft-metadata-cell")?.remove();
            return;
        }
        document.querySelectorAll("#history-aircraft > tr.metadata-active").forEach(item => {
            item.classList.remove("metadata-active");
            item.querySelector(".aircraft-metadata-cell")?.remove();
        });
        const detailCell = document.createElement("td");
        detailCell.className = "aircraft-metadata-cell";
        const switchHeader = `<div class="session-view-switch"><strong>Aircraft details</strong><button type="button">Session information</button></div>`;
        detailCell.innerHTML = `${switchHeader}<div class="metadata-loading">Loading aircraft information…</div>`;
        detailCell.addEventListener("click", event => event.stopPropagation());
        const closeDetails = event => { event.stopPropagation(); row.classList.remove("metadata-active"); detailCell.remove(); };
        detailCell.querySelector("button").addEventListener("click", closeDetails);
        row.appendChild(detailCell);
        row.classList.add("metadata-active");
        const cacheKey = `${session.aircraft_id}|${(session.raw_aircraft_ids || []).join(",")}`;
        try {
            let metadata = aircraftMetadataCache.get(cacheKey);
            if (!metadata) {
                metadata = await getJson(`/api/aircraft/metadata?aircraft_id=${encodeURIComponent(session.aircraft_id)}&raw_ids=${encodeURIComponent((session.raw_aircraft_ids || []).join(","))}`);
                aircraftMetadataCache.set(cacheKey, metadata);
            }
            if (detailCell.isConnected) detailCell.innerHTML = `${switchHeader}${renderAircraftMetadata(metadata)}`;
        } catch (error) {
            console.error("Aircraft metadata unavailable:", error);
            if (detailCell.isConnected) detailCell.innerHTML = `${switchHeader}<div class="metadata-empty">Aircraft information is temporarily unavailable.</div>`;
        }
        if (detailCell.isConnected) detailCell.querySelector("button").addEventListener("click", closeDetails);
        return;
    }
    const existing = row.nextElementSibling;
    if (existing && existing.classList.contains("metadata-detail-row")) { existing.remove(); return; }
    document.querySelectorAll(".metadata-detail-row").forEach(item => item.remove());
    const detailRow = document.createElement("tr");
    detailRow.className = "metadata-detail-row";
    const cell = document.createElement("td");
    cell.colSpan = 14;
    cell.innerHTML = `<div class="metadata-loading">Loading aircraft information…</div>`;
    detailRow.appendChild(cell);
    row.after(detailRow);
    const cacheKey = `${session.aircraft_id}|${(session.raw_aircraft_ids || []).join(",")}`;
    try {
        let metadata = aircraftMetadataCache.get(cacheKey);
        if (!metadata) {
            metadata = await getJson(`/api/aircraft/metadata?aircraft_id=${encodeURIComponent(session.aircraft_id)}&raw_ids=${encodeURIComponent((session.raw_aircraft_ids || []).join(","))}`);
            aircraftMetadataCache.set(cacheKey, metadata);
        }
        if (detailRow.isConnected) cell.innerHTML = renderAircraftMetadata(metadata);
    } catch (error) {
        console.error("Aircraft metadata unavailable:", error);
        if (detailRow.isConnected) cell.innerHTML = `<div class="metadata-empty">Aircraft information is temporarily unavailable.</div>`;
    }
}
function renderHistorySessions() {
    const sessions = filteredHistorySessions();
    const pageCount = Math.max(1, Math.ceil(sessions.length / HISTORY_PAGE_SIZE));
    historyPage = Math.min(Math.max(1, historyPage), pageCount);
    const pageStart = (historyPage - 1) * HISTORY_PAGE_SIZE;
    const pageSessions = sessions.slice(pageStart, pageStart + HISTORY_PAGE_SIZE);
    const body = document.getElementById("history-aircraft");
    body.replaceChildren();
    const empty = document.getElementById("history-empty");
    empty.hidden = sessions.length > 0;
    empty.textContent = latestHistorySessions.length ? "No sessions match the selected filters." : "No sessions recorded on this date.";
    const rangeStart = sessions.length ? pageStart + 1 : 0;
    const rangeEnd = Math.min(pageStart + HISTORY_PAGE_SIZE, sessions.length);
    document.getElementById("history-session-count").textContent = `${rangeStart}-${rangeEnd} of ${sessions.length} sessions`;
    const pagination = document.getElementById("history-pagination");
    pagination.hidden = sessions.length <= HISTORY_PAGE_SIZE;
    document.getElementById("history-page-status").textContent = `Page ${historyPage} of ${pageCount}`;
    document.getElementById("history-page-prev").disabled = historyPage <= 1;
    document.getElementById("history-page-next").disabled = historyPage >= pageCount;
    for (const aircraft of pageSessions) {
        const row = document.createElement("tr");
        row.tabIndex = 0;
        row.title = `Source IDs: ${(aircraft.raw_aircraft_ids || []).join(", ")}`;
        const aircraftIdHtml = aircraft.has_metadata
            ? `<button type="button" class="aircraft-metadata-button" title="Show aircraft information">${escapeHtml(aircraft.aircraft_id)} <span>＋</span></button>`
            : `<strong>${escapeHtml(aircraft.aircraft_id)}</strong>`;
        row.innerHTML = `<td>${aircraftIdHtml}</td><td>${escapeHtml(aircraftTypeLabel(aircraft.aircraft_type))}</td><td>#${formatInteger(aircraft.session_number)}</td><td>${escapeHtml(aircraftProtocolLabel(aircraft))}</td><td>${formatHistoryTime(aircraft.first_received)}</td><td>${formatHistoryTime(aircraft.last_received)}</td><td>${formatFlightDuration(aircraft.duration_seconds)}</td><td>${formatNumber(aircraft.min_distance_km, 1)} km</td><td>${formatNumber(aircraft.max_distance_km, 1)} km</td><td>${formatNumber(aircraft.max_altitude_m)} m</td><td>${formatNumber(aircraft.max_speed_kmh)} km/h</td><td>${formatNumber(aircraft.max_snr_db, 1)} dB</td><td>${formatInteger(aircraft.position_points)}</td><td><button type="button" class="replay-session-button">Replay</button></td>`;
        const openReplay = () => runRefresh(() => openHistoryReplay(aircraft));
        row.addEventListener("click", openReplay);
        row.addEventListener("keydown", event => { if (!event.target.closest("button") && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); openReplay(); } });
        row.querySelector(".replay-session-button").addEventListener("click", event => { event.stopPropagation(); openReplay(); });
        row.querySelector(".aircraft-metadata-button")?.addEventListener("click", event => { event.stopPropagation(); toggleAircraftMetadata(aircraft, row); });
        body.appendChild(row);
    }
}
async function refreshArchive() {
    const archive = await getJson(`/api/history/days?days=${archiveDays}`);
    document.getElementById("history-summary").textContent = `Last ${archive.days} days`;
    const body = document.getElementById("history-days");
    historyDayDetailPanel?.remove();
    body.replaceChildren();
    for (const day of archive.history) {
        const card = document.createElement("article");
        card.className = "history-day-card";
        card.dataset.date = day.date;
        const summary = document.createElement("button");
        summary.type = "button";
        summary.className = "history-day-summary";
        summary.setAttribute("aria-expanded", String(day.date === selectedHistoryDate));
        summary.innerHTML = `<span class="history-day-date"><small>Date</small><strong>${formatCompactDayDate(day.date)}</strong></span>
            <span><small>Packets</small><strong>${formatInteger(day.packets)}</strong></span>
            <span><small>Aircraft</small><strong>${formatInteger(day.aircraft)}</strong></span>
            <span class="history-day-secondary"><small>Max. altitude</small><strong>${formatNumber(day.max_altitude_m)} m</strong></span>
            <span class="history-day-secondary"><small>Max. speed</small><strong>${formatNumber(day.max_speed_kmh)} km/h</strong></span>
            <span class="history-day-secondary"><small>Max. dist.</small><strong>${formatNumber(day.max_distance_km, 1)} km</strong></span>
            <span class="history-day-expand" aria-hidden="true">⌄</span>`;
        summary.addEventListener("click", () => {
            if (card.classList.contains("selected")) closeHistoryDay();
            else runRefresh(() => loadHistoryDay(day.date, true));
        });
        card.appendChild(summary);
        body.appendChild(card);
    }
    if (selectedHistoryDate && body.querySelector(`.history-day-card[data-date="${CSS.escape(selectedHistoryDate)}"]`)) {
        placeHistoryDayDetail(selectedHistoryDate);
    } else {
        selectedHistoryDate = null;
    }
}
async function refreshAircraft() {
    if (mapMode !== "live") return;
    const data = await getJson("/api/aircraft");
    const activeIds = new Set();
    for (const aircraft of data.aircraft) {
        activeIds.add(aircraft.aircraft_id);
        const position = [aircraft.latitude, aircraft.longitude];
        const entry = aircraftMarkers.get(aircraft.aircraft_id);
        if (entry) {
            entry.data = aircraft; entry.marker.setLatLng(position);
            entry.marker.setIcon(aircraftIcon(aircraft, aircraft.aircraft_id === selectedAircraftId));
            entry.marker.setPopupContent(markerPopup(aircraft));
        } else {
            const marker = L.marker(position, { icon: aircraftIcon(aircraft) }).addTo(map).bindPopup(markerPopup(aircraft));
            marker.on("click", () => selectAircraft(aircraft.aircraft_id, false));
            aircraftMarkers.set(aircraft.aircraft_id, { marker, data: aircraft });
        }
    }
    for (const [id, entry] of aircraftMarkers) if (!activeIds.has(id)) { map.removeLayer(entry.marker); aircraftMarkers.delete(id); }
    if (selectedAircraftId && !activeIds.has(selectedAircraftId)) clearSelection();
    latestAircraft = data.aircraft;
    const activeAircraftCard = document.querySelector(".table-card");
    const activeCount = data.aircraft.length;
    document.getElementById("active-aircraft-summary").textContent = activeCount ? `${activeCount} active` : "No traffic in the last 10 mins.";
    activeAircraftToggle.setAttribute("aria-disabled", String(!activeCount));
    if (!activeCount) activeAircraftCard.open = false;
    else if (!previousActiveAircraftCount) activeAircraftCard.open = true;
    previousActiveAircraftCount = activeCount;
    renderAircraft();
    if (selectedAircraftId) {
        const selected = aircraftMarkers.get(selectedAircraftId);
        if (selected) updateDetails(selected.data);
    }
}
async function refreshTracks() {
    if (mapMode !== "live") return;
    const data = await getJson("/api/tracks");
    const activeIds = new Set();
    for (const [id, track] of Object.entries(data.tracks)) {
        const coordinates = track.points.map(point => [point.latitude, point.longitude]);
        if (coordinates.length < 2) continue;
        activeIds.add(id);
        const line = aircraftTracks.get(id);
        if (line) { line.setLatLngs(coordinates); line.setStyle({ color: protocolColor(track.protocol) }); }
        else aircraftTracks.set(id, L.polyline(coordinates, { color: protocolColor(track.protocol), weight: 3, opacity: .72, interactive: false }).addTo(map));
    }
    for (const [id, line] of aircraftTracks) if (!activeIds.has(id)) { map.removeLayer(line); aircraftTracks.delete(id); }
}
function stopReplay() {
    replayPlaying = false;
    document.getElementById("replay-play").textContent = "Play";
    if (replayAnimationFrame !== null) cancelAnimationFrame(replayAnimationFrame);
    replayAnimationFrame = null;
}
function clearReplayLayers() {
    stopReplay();
    if (replayMarker) map.removeLayer(replayMarker);
    if (replayTrack) map.removeLayer(replayTrack);
    replayMarker = null; replayTrack = null; replayPoints = []; replayIndex = 0;
    document.getElementById("replay-flight-summary").hidden = true;
}
function replayPopup(point) {
    return `<strong>${escapeHtml(point.aircraft_id)}</strong><br>Aircraft type: ${escapeHtml(aircraftTypeLabel(point.aircraft_type))}<br>Time: ${formatHistoryTime(point.received_at)}<br>Protocol: ${escapeHtml(point.protocol || "OTHER")}<br>Altitude: ${formatNumber(point.altitude_m)} m<br>Speed: ${formatNumber(point.speed_kmh)} km/h<br>Course: ${formatNumber(point.course_deg)}°<br>Climb rate: ${formatNumber(point.climb_ms, 1)} m/s`;
}
function renderReplayPoint(index) {
    if (!replayPoints.length) return;
    replayIndex = Math.max(0, Math.min(index, replayPoints.length - 1));
    const point = replayPoints[replayIndex];
    const position = [point.latitude, point.longitude];
    if (!replayMarker) replayMarker = L.marker(position, { icon: aircraftIcon(point, true) }).addTo(map).bindPopup(replayPopup(point));
    else { replayMarker.setLatLng(position); replayMarker.setIcon(aircraftIcon(point, true)); replayMarker.setPopupContent(replayPopup(point)); }
    const coordinates = replayPoints.slice(0, replayIndex + 1).map(item => [item.latitude, item.longitude]);
    if (!replayTrack) replayTrack = L.polyline(coordinates, { color: protocolColor(point.protocol), weight: 4, opacity: .85, interactive: false }).addTo(map);
    else { replayTrack.setLatLngs(coordinates); replayTrack.setStyle({ color: protocolColor(point.protocol) }); }
    document.getElementById("replay-timeline").value = replayIndex;
    document.getElementById("replay-time").textContent = `${formatHistoryTime(point.received_at)} · ${replayIndex + 1}/${replayPoints.length}`;
}
function replayFrame(now) {
    if (!replayPlaying || replayPoints.length < 2) return;
    const target = replayRecordedStart + (now - replayStartedAt) * Number(document.getElementById("replay-speed").value || 1);
    let index = replayIndex;
    while (index + 1 < replayPoints.length && new Date(replayPoints[index + 1].received_at).getTime() <= target) index += 1;
    renderReplayPoint(index);
    if (index >= replayPoints.length - 1) { stopReplay(); return; }
    replayAnimationFrame = requestAnimationFrame(replayFrame);
}
function toggleReplay() {
    if (!replayPoints.length) return;
    if (replayPlaying) { stopReplay(); return; }
    if (replayIndex >= replayPoints.length - 1) renderReplayPoint(0);
    replayPlaying = true;
    document.getElementById("replay-play").textContent = "Pause";
    replayStartedAt = performance.now();
    replayRecordedStart = new Date(replayPoints[replayIndex].received_at).getTime();
    replayAnimationFrame = requestAnimationFrame(replayFrame);
}
async function loadReplaySessions(day) {
    const selector = document.getElementById("replay-session");
    selector.innerHTML = `<option value="">Loading sessions…</option>`;
    selector.disabled = true; clearReplayLayers();
    const detail = await getJson(`/api/history/day?date=${encodeURIComponent(day)}`);
    selector.innerHTML = `<option value="">Select a session</option>`;
    for (const session of detail.aircraft.filter(item => item.position_points > 0)) {
        const option = document.createElement("option");
        option.value = `${session.aircraft_id}|${session.session_number}`;
        option.textContent = `${session.aircraft_id} · session ${session.session_number} · ${formatHistoryTime(session.first_received)}–${formatHistoryTime(session.last_received)} · ${session.position_points} points`;
        selector.appendChild(option);
    }
    selector.disabled = false;
    document.getElementById("replay-time").textContent = selector.options.length > 1 ? "Select a session" : "No sessions this day";
}
async function loadReplaySession() {
    const selector = document.getElementById("replay-session");
    if (!selector.value) return;
    const [aircraftId, sessionNumber] = selector.value.split("|");
    const day = document.getElementById("replay-date").value;
    clearReplayLayers(); document.getElementById("replay-time").textContent = "Loading…";
    const data = await getJson(`/api/replay/session?date=${encodeURIComponent(day)}&aircraft_id=${encodeURIComponent(aircraftId)}&session=${encodeURIComponent(sessionNumber)}`);
    replayPoints = data.points.map(point => ({ ...point, aircraft_id: data.aircraft_id }));
    if (replayPoints.length) {
        const aircraftTypes = [...new Set(replayPoints.map(point => aircraftTypeLabel(point.aircraft_type)))];
        const protocols = [...new Set(replayPoints.map(point => point.protocol || "OTHER"))];
        document.getElementById("replay-aircraft-code").textContent = data.aircraft_id;
        document.getElementById("replay-aircraft-type").textContent = aircraftTypes.join(" / ");
        document.getElementById("replay-aircraft-protocol").textContent = protocols.join(" / ");
        document.getElementById("replay-flight-summary").hidden = false;
    }
    const timeline = document.getElementById("replay-timeline");
    timeline.max = Math.max(0, replayPoints.length - 1); timeline.value = 0; timeline.disabled = replayPoints.length === 0;
    document.getElementById("replay-play").disabled = replayPoints.length < 2;
    if (replayPoints.length) {
        renderReplayPoint(0);
        const bounds = L.latLngBounds(replayPoints.map(point => [point.latitude, point.longitude]));
        if (bounds.isValid()) map.fitBounds(bounds.pad(.15), { maxZoom: 14 });
    }
}
function applyCoverageStyle(style) {
    coverageStyle = style;
    document.getElementById("coverage-density").classList.toggle("active", style === "density");
    document.getElementById("coverage-altitude").classList.toggle("active", style === "altitude");
    if (!coverageLayer || !coverageSummary) return;
    let maximumCount = 1;
    for (const cell of coverageSummary.cells) maximumCount = Math.max(maximumCount, Number(cell.points));
    const minimumAltitude = Number(coverageSummary.min_altitude_m);
    const maximumAltitude = Number(coverageSummary.max_altitude_m);
    coverageLayer.eachLayer(marker => {
        const cell = marker.coverageCell;
        if (style === "altitude") {
            const altitude = Number(cell.avg_altitude_m);
            const ratio = Number.isFinite(altitude) && maximumAltitude > minimumAltitude
                ? Math.max(0, Math.min(1, (altitude - minimumAltitude) / (maximumAltitude - minimumAltitude))) : 0;
            marker.setStyle({ fillColor: "#a855f7", fillOpacity: .16 + ratio * .78 });
        } else {
            const ratio = maximumCount > 1 ? Math.log(Number(cell.points) + 1) / Math.log(maximumCount + 1) : 1;
            marker.setStyle({ fillColor: "#ef4444", fillOpacity: .16 + ratio * .78 });
        }
    });
    document.getElementById("coverage-scale").textContent = style === "altitude"
        ? `Purple intensity shows average altitude ASL · ${formatNumber(minimumAltitude)}–${formatNumber(maximumAltitude)} m`
        : "Red intensity shows positions per cell";
}
async function loadCoverage() {
    if (coverageLayer && coverageSummary) {
        coverageLayer.addTo(map);
    } else {
        const data = await getJson("/api/coverage");
        coverageSummary = data;
        coverageLayer = L.featureGroup();
        for (const cell of data.cells) {
            const count = Number(cell.points);
            const marker = L.circleMarker([cell.latitude, cell.longitude], {
                renderer: coverageRenderer,
                radius: 3,
                stroke: false,
                fillColor: "#ef4444",
                fillOpacity: .5,
            });
            marker.coverageCell = cell;
            marker.bindPopup(`<strong>Coverage cell</strong><br>${formatInteger(count)} recorded positions<br>Average altitude: ${formatNumber(cell.avg_altitude_m)} m ASL<br>Altitude range: ${formatNumber(cell.min_altitude_m)}–${formatNumber(cell.max_altitude_m)} m ASL<br>${formatNumber(cell.latitude, 3)}, ${formatNumber(cell.longitude, 3)}`).addTo(coverageLayer);
        }
        coverageLayer.addTo(map);
    }
    applyCoverageStyle(coverageStyle);
    map.setView([STATION.latitude, STATION.longitude], 12);
    setConnectionState(`All-time coverage · ${formatInteger(coverageSummary.position_points)} positions · ${formatInteger(coverageSummary.cells.length)} cells`);
}
async function setMapMode(mode) {
    mapMode = mode;
    const replay = mode === "replay";
    const coverage = mode === "coverage";
    document.getElementById("map-live-mode").classList.toggle("active", mode === "live");
    document.getElementById("map-replay-mode").classList.toggle("active", replay);
    document.getElementById("map-coverage-mode").classList.toggle("active", coverage);
    document.getElementById("replay-controls").hidden = !replay;
    document.getElementById("coverage-controls").hidden = !coverage;
    document.getElementById("map-title").textContent = replay ? "Map replay" : coverage ? "All-time coverage" : "Live map";
    for (const entry of aircraftMarkers.values()) mode !== "live" ? map.removeLayer(entry.marker) : entry.marker.addTo(map);
    for (const line of aircraftTracks.values()) mode !== "live" ? map.removeLayer(line) : line.addTo(map);
    if (!coverage && coverageLayer) map.removeLayer(coverageLayer);
    if (replay) {
        const dateInput = document.getElementById("replay-date");
        if (!dateInput.value) dateInput.value = new Date().toLocaleDateString("sv-SE", { timeZone: DISPLAY_TIME_ZONE });
        await loadReplaySessions(dateInput.value); setConnectionState("Replay mode");
    } else if (coverage) {
        clearReplayLayers(); await loadCoverage();
    } else { clearReplayLayers(); await Promise.all([refreshAircraft(), refreshTracks()]); }
    setTimeout(() => map.invalidateSize(), 0);
}
async function runRefresh(task) {
    try { await task(); if (mapMode === "live") setConnectionState(`Live · updated ${new Date().toLocaleTimeString(DISPLAY_LOCALE, { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: DISPLAY_TIME_ZONE })}`); }
    catch (error) { console.error("Live update failed:", error); setConnectionState("Update unavailable", true); }
}

syncHistorySortControls();
document.getElementById("replay-speed").value = "10";
Promise.all([runRefresh(refreshStats), runRefresh(refreshAircraft), runRefresh(refreshTracks), runRefresh(refreshSystem), runRefresh(refreshHistory), runRefresh(refreshArchive)]);
document.getElementById("aircraft-search").addEventListener("input", event => {
    searchTerm = event.target.value.trim().toUpperCase();
    renderAircraft();
});
document.getElementById("protocol-filters").addEventListener("click", event => {
    const button = event.target.closest("[data-protocol]");
    if (!button) return;
    protocolFilter = button.dataset.protocol;
    document.querySelectorAll("[data-protocol]").forEach(item => item.classList.toggle("active", item === button));
    renderAircraft();
});
document.querySelectorAll(".sort-button").forEach(button => button.addEventListener("click", () => {
    const key = button.dataset.sort;
    sortState = sortState.key === key
        ? { key, direction: sortState.direction * -1 }
        : { key, direction: 1 };
    document.querySelectorAll(".sort-button").forEach(item => {
        const active = item === button;
        item.classList.toggle("active", active);
        item.querySelector("span").textContent = active ? (sortState.direction === 1 ? "▲" : "▼") : "";
    });
    renderAircraft();
}));
document.querySelectorAll("[data-history-days]").forEach(button => button.addEventListener("click", () => {
    archiveDays = Number(button.dataset.historyDays);
    document.querySelectorAll("[data-history-days]").forEach(item => item.classList.toggle("active", item === button));
    runRefresh(refreshArchive);
}));
document.getElementById("history-date").addEventListener("change", event => {
    if (event.target.value) runRefresh(() => loadHistoryDay(event.target.value, true));
});
function resetHistoryPageAndRender() { historyPage = 1; renderHistorySessions(); }
document.getElementById("history-aircraft-filter").addEventListener("input", resetHistoryPageAndRender);
document.getElementById("history-protocol-filter").addEventListener("change", resetHistoryPageAndRender);
document.getElementById("history-type-filter").addEventListener("change", resetHistoryPageAndRender);
document.getElementById("history-mobile-sort").addEventListener("change", event => {
    historySortState = { key: event.target.value, direction: Number(document.getElementById("history-mobile-direction").value) };
    syncHistorySortControls();
    resetHistoryPageAndRender();
});
document.getElementById("history-mobile-direction").addEventListener("change", event => {
    historySortState = { key: document.getElementById("history-mobile-sort").value, direction: Number(event.target.value) };
    syncHistorySortControls();
    resetHistoryPageAndRender();
});
document.querySelectorAll(".history-sort-button").forEach(button => button.addEventListener("click", () => {
    const key = button.dataset.historySort;
    historySortState = historySortState.key === key
        ? { key, direction: historySortState.direction * -1 }
        : { key, direction: 1 };
    syncHistorySortControls();
    resetHistoryPageAndRender();
}));
function changeHistoryPage(offset) {
    historyPage += offset;
    renderHistorySessions();
    requestAnimationFrame(() => document.querySelector(".history-session-wrapper")?.scrollIntoView({ behavior: "smooth", block: "start" }));
}
document.getElementById("history-page-prev").addEventListener("click", () => changeHistoryPage(-1));
document.getElementById("history-page-next").addEventListener("click", () => changeHistoryPage(1));
document.getElementById("map-live-mode").addEventListener("click", () => runRefresh(() => setMapMode("live")));
document.getElementById("map-replay-mode").addEventListener("click", () => runRefresh(() => setMapMode("replay")));
document.getElementById("map-coverage-mode").addEventListener("click", () => runRefresh(() => setMapMode("coverage")));
document.getElementById("coverage-density").addEventListener("click", () => applyCoverageStyle("density"));
document.getElementById("coverage-altitude").addEventListener("click", () => applyCoverageStyle("altitude"));
const mapStyleSelector = document.getElementById("map-style");
mapStyleSelector.value = selectedBaseMap;
mapStyleSelector.addEventListener("change", event => setBaseMap(event.target.value));
document.getElementById("replay-date").addEventListener("change", event => {
    if (event.target.value) runRefresh(() => loadReplaySessions(event.target.value));
});
document.getElementById("replay-session").addEventListener("change", () => runRefresh(loadReplaySession));
document.getElementById("replay-play").addEventListener("click", toggleReplay);
document.getElementById("replay-timeline").addEventListener("input", event => {
    stopReplay(); renderReplayPoint(Number(event.target.value));
});
setInterval(() => runRefresh(refreshStats), REFRESH.stats);
setInterval(() => runRefresh(refreshAircraft), REFRESH.aircraft);
setInterval(() => runRefresh(refreshTracks), REFRESH.tracks);
setInterval(() => runRefresh(refreshSystem), REFRESH.system);
setInterval(() => runRefresh(refreshHistory), REFRESH.history);
setInterval(() => runRefresh(refreshArchive), REFRESH.archive);
