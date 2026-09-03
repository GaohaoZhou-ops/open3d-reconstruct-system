"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

const elements = {
  cameraCards: $$(".camera-card"),
  hardwareInputs: $$("input[name='hardware']"),
  refreshDevices: $("#refresh-devices"),
  deviceHint: $("#device-hint"),
  sessionPanel: $("#session-panel"),
  deviceIndex: $("#device-index"),
  recordingName: $("#recording-name"),
  frameStride: $("#frame-stride"),
  startRecording: $("#start-recording"),
  stopRecording: $("#stop-recording"),
  startConversion: $("#start-conversion"),
  phaseBadge: $("#phase-badge"),
  taskSummary: $("#task-summary"),
  errorBanner: $("#error-banner"),
  metricHardware: $("#metric-hardware"),
  metricRecording: $("#metric-recording"),
  metricDataset: $("#metric-dataset"),
  logOutput: $("#log-output"),
  copyLog: $("#copy-log"),
  serverIndicator: $("#server-indicator"),
  processPanel: $("#process-panel"),
  processKicker: $("#process-kicker"),
  processTitle: $("#process-title"),
  processFrameRate: $("#process-frame-rate"),
  processFrameCount: $("#process-frame-count"),
  captureVisual: $("#capture-visual"),
  liveRgb: $("#live-rgb"),
  liveDepth: $("#live-depth"),
  rgbPlaceholder: $("#rgb-placeholder"),
  depthPlaceholder: $("#depth-placeholder"),
  rgbResolution: $("#rgb-resolution"),
  depthRange: $("#depth-range"),
  imuStatus: $("#imu-status"),
  imuAccel: $("#imu-accel"),
  imuAccelMagnitude: $("#imu-accel-magnitude"),
  imuGyro: $("#imu-gyro"),
  imuGyroMagnitude: $("#imu-gyro-magnitude"),
  imuTemperature: $("#imu-temperature"),
  imuRate: $("#imu-rate"),
  imuMessage: $("#imu-message"),
  imuChart: $("#imu-chart"),
  conversionVisual: $("#conversion-visual"),
  pipelineSteps: $$("#pipeline-steps li"),
  pipelineProgressBar: $("#pipeline-progress-bar"),
  pipelineLabel: $("#pipeline-label"),
  pipelineDetail: $("#pipeline-detail"),
  conversionPlaceholder: $("#conversion-placeholder"),
  conversionRgbd: $("#conversion-rgbd"),
  processRgb: $("#process-rgb"),
  processDepth: $("#process-depth"),
  conversionModel: $("#conversion-model"),
  processViewer: $("#process-viewer"),
  processViewerMessage: $("#process-viewer-message"),
  processModelLabel: $("#process-model-label"),
  resultPanel: $("#result-panel"),
  resultName: $("#result-name"),
  resultSize: $("#result-size"),
  downloadMesh: $("#download-mesh"),
  downloadRecording: $("#download-recording"),
  viewer: $("#model-viewer"),
  viewerMessage: $("#viewer-message"),
};

const phaseLabels = {
  idle: "等待录制",
  recording: "正在录制",
  stopping: "正在封装",
  recorded: "录制完成",
  converting: "正在转换",
  completed: "重建完成",
  error: "需要处理",
};

const app = {
  selectedHardware: null,
  devices: new Map(),
  state: null,
  lastPhase: null,
  requestBusy: false,
  deviceBusy: false,
  lastLogRevision: -1,
  loadedMeshKey: null,
  liveSequence: -1,
  processArtifactKey: null,
};

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / (1024 ** exponent);
  return `${value.toFixed(value >= 100 || exponent === 0 ? 0 : value >= 10 ? 1 : 2)} ${units[exponent]}`;
}

function basename(path) {
  if (!path) return "—";
  return String(path).split(/[\\/]/).pop() || path;
}

function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function vectorText(values, digits = 3) {
  if (!Array.isArray(values) || values.length < 3) return "— · — · —";
  return values.slice(0, 3).map((value) => finiteNumber(value).toFixed(digits)).join(" · ");
}

class ImuChart {
  constructor(canvas) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d");
    this.samples = [];
    this.lastTimestamp = null;
    new ResizeObserver(() => this.render()).observe(canvas.parentElement);
  }

  reset() {
    this.samples = [];
    this.lastTimestamp = null;
    this.render();
  }

  update(imu) {
    if (!imu || !imu.available || imu.timestamp_usec == null) return;
    if (imu.timestamp_usec === this.lastTimestamp) return;
    this.lastTimestamp = imu.timestamp_usec;
    this.samples.push({
      acceleration: finiteNumber(imu.acceleration_magnitude),
      gyroscope: finiteNumber(imu.gyroscope_magnitude),
    });
    if (this.samples.length > 90) this.samples.shift();
    this.render();
  }

  render() {
    const bounds = this.canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(bounds.width * dpr));
    const height = Math.max(1, Math.round(bounds.height * dpr));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }
    const context = this.context;
    context.clearRect(0, 0, width, height);
    context.strokeStyle = "rgba(224,237,223,.07)";
    context.lineWidth = 1;
    for (let row = 1; row < 4; row += 1) {
      const y = (height * row) / 4;
      context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke();
    }
    if (this.samples.length < 2) return;
    const draw = (key, color, maximum) => {
      context.strokeStyle = color;
      context.lineWidth = Math.max(1, dpr);
      context.beginPath();
      this.samples.forEach((sample, index) => {
        const x = (index / Math.max(this.samples.length - 1, 1)) * width;
        const normalized = Math.max(0, Math.min(1, sample[key] / maximum));
        const y = height - normalized * (height - 5 * dpr) - 2 * dpr;
        if (index === 0) context.moveTo(x, y); else context.lineTo(x, y);
      });
      context.stroke();
    };
    draw("acceleration", "#c7f36b", 20);
    draw("gyroscope", "#70ddcf", 3);
  }
}

async function api(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  let payload;
  try {
    payload = await response.json();
  } catch (_error) {
    throw new Error(`服务返回了无效响应（HTTP ${response.status}）`);
  }
  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || `请求失败（HTTP ${response.status}）`);
  }
  return payload;
}

function post(url, body = {}) {
  return api(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Open3D-Reconstruct": "web",
    },
    body: JSON.stringify(body),
  });
}

function setServerOnline(online) {
  elements.serverIndicator.classList.toggle("online", online);
  elements.serverIndicator.classList.toggle("offline", !online);
  elements.serverIndicator.innerHTML = `<i></i>${online ? "已连接" : "连接中断"}`;
}

function selectHardware(hardware) {
  app.selectedHardware = hardware;
  for (const card of elements.cameraCards) {
    const selected = card.dataset.hardware === hardware;
    card.classList.toggle("selected", selected);
    const input = card.querySelector("input");
    input.checked = selected;
  }
  elements.sessionPanel.classList.toggle("enabled", Boolean(hardware));
  populateDeviceOptions();
  updateControls();
  updateJourney();
}

function populateDeviceOptions() {
  const oldValue = elements.deviceIndex.value;
  const item = app.devices.get(app.selectedHardware);
  elements.deviceIndex.textContent = "";
  const devices = item && Array.isArray(item.devices) ? item.devices : [];
  if (devices.length) {
    for (const device of devices) {
      const option = document.createElement("option");
      option.value = String(device.index);
      const serial = device.serial ? ` · ${device.serial}` : "";
      option.textContent = `设备 ${device.index} · ${device.name}${serial}`;
      elements.deviceIndex.append(option);
    }
  } else {
    const option = document.createElement("option");
    option.value = "0";
    option.textContent = "设备 0";
    elements.deviceIndex.append(option);
  }
  if (Array.from(elements.deviceIndex.options).some((option) => option.value === oldValue)) {
    elements.deviceIndex.value = oldValue;
  }
}

async function fetchDevices(refresh = false) {
  if (app.deviceBusy) return;
  app.deviceBusy = true;
  elements.refreshDevices.disabled = true;
  elements.refreshDevices.textContent = "检测中…";
  for (const status of $$('[data-device-state]')) {
    status.className = "device-state checking";
    status.textContent = "检测中";
  }
  try {
    const payload = await api(`/api/devices${refresh ? "?refresh=1" : ""}`);
    app.devices.clear();
    for (const item of payload.devices.items) {
      app.devices.set(item.id, item);
      const status = $(`[data-device-state="${item.id}"]`);
      if (item.available) {
        status.className = "device-state available";
        status.textContent = `${item.devices.length} 台在线`;
      } else if (item.error) {
        status.className = "device-state error";
        status.textContent = "检测受限";
        status.title = item.error;
      } else {
        status.className = "device-state unavailable";
        status.textContent = "未发现";
      }
    }
    const detected = new Date(payload.devices.detected_at);
    const detectedText = Number.isNaN(detected.getTime()) ? "刚刚" : detected.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    elements.deviceHint.textContent = `最近检测：${detectedText}。未发现设备时可接入后重新检测。`;
    populateDeviceOptions();
  } catch (error) {
    for (const status of $$('[data-device-state]')) {
      status.className = "device-state error";
      status.textContent = "检测失败";
      status.title = error.message;
    }
    elements.deviceHint.textContent = `设备检测失败：${error.message}`;
  } finally {
    app.deviceBusy = false;
    elements.refreshDevices.disabled = Boolean(app.state && app.state.task);
    elements.refreshDevices.textContent = "重新检测";
  }
}

function updateJourney() {
  const phase = app.state ? app.state.phase : "idle";
  const hasSelection = Boolean(app.selectedHardware);
  const recordReached = ["recording", "stopping", "recorded", "converting", "completed", "error"].includes(phase);
  const convertReached = ["converting", "completed"].includes(phase);
  const items = {
    select: $("[data-journey='select']"),
    record: $("[data-journey='record']"),
    convert: $("[data-journey='convert']"),
  };
  Object.values(items).forEach((item) => item.classList.remove("active", "done"));
  if (!hasSelection) {
    items.select.classList.add("active");
    return;
  }
  items.select.classList.add("done");
  if (!recordReached || phase === "recording" || phase === "stopping") {
    items.record.classList.add("active");
  } else {
    items.record.classList.add("done");
  }
  if (phase === "recorded" || phase === "error") items.convert.classList.add("active");
  if (phase === "converting") items.convert.classList.add("active");
  if (phase === "completed") items.convert.classList.add("done");
}

function updateControls() {
  const state = app.state || {
    can_start_recording: true,
    can_stop_recording: false,
    can_start_conversion: false,
    task: null,
  };
  const selected = Boolean(app.selectedHardware);
  const locked = Boolean(state.task) || app.requestBusy;

  elements.startRecording.disabled = !selected || !state.can_start_recording || app.requestBusy || app.deviceBusy;
  elements.stopRecording.disabled = !state.can_stop_recording || app.requestBusy;
  elements.startConversion.disabled = !selected || !state.can_start_conversion || app.requestBusy;
  elements.deviceIndex.disabled = !selected || locked;
  elements.recordingName.disabled = !selected || locked;
  elements.frameStride.disabled = !selected || locked;
  elements.refreshDevices.disabled = app.deviceBusy || Boolean(state.task);
  for (const input of elements.hardwareInputs) input.disabled = locked;
}

function setSummary(kind, title, detail) {
  elements.taskSummary.className = `task-summary ${kind || ""}`.trim();
  elements.taskSummary.querySelector("strong").textContent = title;
  elements.taskSummary.querySelector("small").textContent = detail;
}

function renderLogs(state) {
  if (state.revision === app.lastLogRevision) return;
  app.lastLogRevision = state.revision;
  const atBottom = elements.logOutput.scrollHeight - elements.logOutput.scrollTop - elements.logOutput.clientHeight < 45;
  elements.logOutput.textContent = "";
  if (!state.logs.length) {
    const placeholder = document.createElement("span");
    placeholder.className = "log-placeholder";
    placeholder.textContent = "任务日志将在这里显示。";
    elements.logOutput.append(placeholder);
    return;
  }
  const fragment = document.createDocumentFragment();
  for (const line of state.logs) {
    const row = document.createElement("span");
    row.className = `log-line ${line.level || "info"}`;
    const time = document.createElement("span");
    time.className = "time";
    time.textContent = `[${line.time}] `;
    row.append(time, document.createTextNode(line.message), document.createTextNode("\n"));
    fragment.append(row);
  }
  elements.logOutput.append(fragment);
  if (atBottom) elements.logOutput.scrollTop = elements.logOutput.scrollHeight;
}

function renderImu(imu) {
  const status = imu && imu.status ? imu.status : "waiting";
  const labels = {
    streaming: "实时采样",
    starting: "正在启动",
    unavailable: "当前不可用",
    unsupported: "硬件不支持",
    waiting: "等待数据",
  };
  elements.imuStatus.className = `sensor-status ${status}`;
  elements.imuStatus.textContent = labels[status] || status;
  elements.imuMessage.textContent = (imu && imu.message) || "正在等待 IMU 状态…";
  if (imu && imu.available) {
    elements.imuAccel.textContent = vectorText(imu.acceleration_m_s2);
    elements.imuGyro.textContent = vectorText(imu.gyroscope_rad_s);
    elements.imuAccelMagnitude.textContent = `|a| ${finiteNumber(imu.acceleration_magnitude).toFixed(3)} m/s²`;
    elements.imuGyroMagnitude.textContent = `|ω| ${finiteNumber(imu.gyroscope_magnitude).toFixed(3)} rad/s`;
    elements.imuTemperature.textContent = `温度 ${finiteNumber(imu.temperature_c).toFixed(1)} °C`;
    elements.imuRate.textContent = `采样率 ${finiteNumber(imu.sample_rate_hz).toFixed(0)} Hz`;
    imuChart.update(imu);
  } else {
    elements.imuAccel.textContent = "— · — · —";
    elements.imuGyro.textContent = "— · — · —";
    elements.imuAccelMagnitude.textContent = "|a| —";
    elements.imuGyroMagnitude.textContent = "|ω| —";
    elements.imuTemperature.textContent = "温度 —";
    elements.imuRate.textContent = "采样率 —";
  }
}

function renderLive(live) {
  if (!live) {
    elements.processFrameRate.textContent = "等待视频流";
    elements.processFrameCount.textContent = "0 帧";
    renderImu(null);
    return;
  }
  elements.processFrameRate.textContent = `${finiteNumber(live.fps).toFixed(1)} FPS`;
  elements.processFrameCount.textContent = `${Math.max(0, Math.round(finiteNumber(live.frame_count)))} 帧`;
  const sequence = Math.round(finiteNumber(live.sequence, -1));
  if (sequence !== app.liveSequence) {
    app.liveSequence = sequence;
    if (live.rgb_url) {
      const version = live.rgb_file ? live.rgb_file.version : sequence;
      elements.liveRgb.src = `${live.rgb_url}?v=${encodeURIComponent(version)}`;
      elements.rgbPlaceholder.hidden = true;
    }
    if (live.depth_url) {
      const version = live.depth_file ? live.depth_file.version : sequence;
      elements.liveDepth.src = `${live.depth_url}?v=${encodeURIComponent(version)}`;
      elements.depthPlaceholder.hidden = true;
    }
  }
  elements.rgbResolution.textContent = live.rgb
    ? `${live.rgb.width} × ${live.rgb.height}`
    : "等待彩色帧";
  if (live.depth) {
    const valid = finiteNumber(live.depth.valid_ratio) * 100;
    const minimum = live.depth.min_mm == null ? "—" : (finiteNumber(live.depth.min_mm) / 1000).toFixed(2);
    const maximum = live.depth.max_mm == null ? "—" : (finiteNumber(live.depth.max_mm) / 1000).toFixed(2);
    const coordinate = live.depth.aligned_to_color ? "已对齐" : "原始深度坐标";
    elements.depthRange.textContent = `${minimum}—${maximum} m · 有效 ${valid.toFixed(0)}% · ${coordinate}`;
  } else {
    elements.depthRange.textContent = "0.3—3.0 m 伪彩";
  }
  renderImu(live.imu);
}

function renderConversion(conversion) {
  if (!conversion) return;
  const stageIndex = Math.max(0, Math.min(5, Math.round(finiteNumber(conversion.stage_index))));
  const completed = conversion.status === "completed";
  elements.pipelineSteps.forEach((item, index) => {
    item.classList.toggle("done", completed || index < stageIndex);
    item.classList.toggle("active", !completed && index === stageIndex);
    item.classList.toggle("failed", conversion.status === "failed" && index === stageIndex);
  });
  const processed = finiteNumber(conversion.processed, NaN);
  const total = finiteNumber(conversion.total, NaN);
  const measurable = Number.isFinite(processed) && Number.isFinite(total) && total > 0;
  const localProgress = measurable ? Math.max(0, Math.min(1, processed / total)) : 0;
  const percent = completed ? 100 : Math.min(99, ((stageIndex + localProgress) / 5) * 100);
  elements.pipelineProgressBar.style.width = `${percent}%`;
  elements.pipelineProgressBar.classList.toggle("indeterminate", !completed && !measurable);
  elements.pipelineLabel.textContent = conversion.label || "正在转换";
  elements.pipelineDetail.textContent = conversion.detail || "等待进度信息";
  if (measurable) {
    elements.processFrameRate.textContent = `${Math.round(processed)} / ${Math.round(total)}`;
  } else {
    elements.processFrameRate.textContent = `阶段 ${Math.min(stageIndex + 1, 5)} / 5`;
  }
  elements.processFrameCount.textContent = conversion.frame_count
    ? `${conversion.frame_count} 帧数据集`
    : "正在分析数据";

  const artifact = conversion.artifact;
  elements.conversionPlaceholder.hidden = Boolean(artifact);
  elements.conversionRgbd.hidden = !artifact || artifact.kind !== "rgbd";
  elements.conversionModel.hidden = !artifact || artifact.kind !== "model";
  if (!artifact) return;
  if (artifact.kind === "rgbd") {
    if (app.processArtifactKey !== artifact.version) {
      app.processArtifactKey = artifact.version;
      elements.processRgb.src = `${artifact.rgb_url}?v=${encodeURIComponent(artifact.rgb.version)}`;
      elements.processDepth.src = `${artifact.depth_url}?v=${encodeURIComponent(artifact.depth.version)}`;
    }
    return;
  }
  elements.processModelLabel.textContent = `${artifact.label || "局部点云"} · 拖动旋转 · 滚轮缩放`;
  if (app.processArtifactKey !== artifact.version) {
    app.processArtifactKey = artifact.version;
    processViewer.load(`${artifact.model_url}?v=${encodeURIComponent(artifact.version)}`);
  }
}

function renderProcess(state) {
  const captureActive = state.phase === "recording" || state.phase === "stopping";
  const conversionActive = state.phase === "converting" || (state.phase === "error" && state.conversion);
  elements.processPanel.hidden = !captureActive && !conversionActive;
  if (elements.processPanel.hidden) return;

  elements.captureVisual.hidden = !captureActive;
  elements.conversionVisual.hidden = !conversionActive;
  if (captureActive) {
    if (app.lastPhase !== "recording" && state.phase === "recording") {
      app.liveSequence = -1;
      imuChart.reset();
      elements.liveRgb.removeAttribute("src");
      elements.liveDepth.removeAttribute("src");
      elements.rgbPlaceholder.hidden = false;
      elements.depthPlaceholder.hidden = false;
    }
    elements.processKicker.textContent = "LIVE CAPTURE";
    elements.processTitle.textContent = "实时 RGB-D 与 IMU";
    renderLive(state.live);
  } else {
    elements.processKicker.textContent = "RECONSTRUCTION PIPELINE";
    elements.processTitle.textContent = "转换与重建过程";
    renderConversion(state.conversion);
  }
}

function renderState(state) {
  app.state = state;
  setServerOnline(true);

  if (!app.selectedHardware && state.hardware && state.phase !== "idle") {
    selectHardware(state.hardware);
  }

  elements.phaseBadge.className = `phase-badge ${state.phase}`;
  elements.phaseBadge.innerHTML = `<span></span>${phaseLabels[state.phase] || state.phase}`;
  elements.metricHardware.textContent = state.hardware_label ? `${state.hardware_label} · #${state.device}` : "—";
  elements.metricRecording.textContent = state.recording ? `${state.recording.path}${state.recording.exists ? ` · ${formatBytes(state.recording.size)}` : ""}` : "—";
  elements.metricDataset.textContent = state.dataset || "—";

  elements.errorBanner.hidden = !state.error;
  elements.errorBanner.textContent = state.error || "";

  const recordingPath = state.recording ? state.recording.path : "等待生成录制文件";
  switch (state.phase) {
    case "recording":
      setSummary("running", "正在采集 RGB-D 数据", recordingPath);
      break;
    case "stopping":
      setSummary("running", "正在安全结束录制", "正在等待相机后端封装文件，请不要拔出设备");
      break;
    case "recorded":
      setSummary("success", "录制已完成，可以开始转换", `${recordingPath} · ${formatBytes(state.recording && state.recording.size)}`);
      break;
    case "converting":
      setSummary("running", "正在提取帧并重建三维场景", state.dataset || "正在准备数据集");
      break;
    case "completed":
      setSummary("success", "转换与重建已完成", state.mesh ? state.mesh.path : "integrated.ply");
      break;
    case "error":
      setSummary("failed", "任务未能完成", state.error || "请检查进程日志");
      break;
    default:
      if (app.selectedHardware) {
        setSummary("", "相机已选择，可以开始录制", "录制将以无窗口模式运行，结束按钮会安全封装文件");
      } else {
        setSummary("", "请先选择相机", "相机选定后即可开始录制");
      }
  }

  renderLogs(state);
  updateControls();
  updateJourney();
  renderProcess(state);
  renderResult(state);
  app.lastPhase = state.phase;
}

async function pollState() {
  try {
    const payload = await api("/api/state");
    renderState(payload.state);
  } catch (_error) {
    setServerOnline(false);
  }
}

async function pollLive() {
  if (document.hidden || !app.state || !["recording", "stopping"].includes(app.state.phase)) return;
  try {
    const payload = await api("/api/live/state");
    renderLive(payload.live);
  } catch (_error) {
    // Main state polling owns the server connectivity indicator.
  }
}

async function runAction(action) {
  if (app.requestBusy) return;
  app.requestBusy = true;
  updateControls();
  try {
    const payload = await action();
    renderState(payload.state);
  } catch (error) {
    elements.errorBanner.hidden = false;
    elements.errorBanner.textContent = error.message;
  } finally {
    app.requestBusy = false;
    updateControls();
  }
}

elements.hardwareInputs.forEach((input) => {
  input.addEventListener("change", () => selectHardware(input.value));
});

elements.refreshDevices.addEventListener("click", () => fetchDevices(true));

elements.startRecording.addEventListener("click", () => runAction(() => post("/api/record/start", {
  hardware: app.selectedHardware,
  device: Number(elements.deviceIndex.value),
  name: elements.recordingName.value.trim() || null,
})));

elements.stopRecording.addEventListener("click", () => runAction(() => post("/api/record/stop")));

elements.startConversion.addEventListener("click", () => runAction(() => post("/api/convert/start", {
  stride: Number(elements.frameStride.value),
})));

elements.copyLog.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(elements.logOutput.innerText);
    const original = elements.copyLog.textContent;
    elements.copyLog.textContent = "已复制";
    setTimeout(() => { elements.copyLog.textContent = original; }, 1200);
  } catch (_error) {
    elements.logOutput.focus();
  }
});

function renderResult(state) {
  const ready = Boolean(state.mesh_url && state.mesh && state.mesh.exists);
  elements.resultPanel.hidden = !ready;
  if (!ready) {
    app.loadedMeshKey = null;
    return;
  }
  elements.resultName.textContent = basename(state.mesh.path);
  elements.resultSize.textContent = formatBytes(state.mesh.size);
  elements.downloadMesh.href = state.mesh_url;
  elements.downloadRecording.href = state.recording_url || "#";
  elements.downloadRecording.hidden = !state.recording_url;
  const key = `${state.mesh.path}:${state.mesh.size}`;
  if (app.loadedMeshKey !== key) {
    app.loadedMeshKey = key;
    modelViewer.load(`${state.mesh_url}?v=${encodeURIComponent(key)}`);
  }
}

const plyTypes = {
  char: [1, "getInt8"], int8: [1, "getInt8"],
  uchar: [1, "getUint8"], uint8: [1, "getUint8"],
  short: [2, "getInt16"], int16: [2, "getInt16"],
  ushort: [2, "getUint16"], uint16: [2, "getUint16"],
  int: [4, "getInt32"], int32: [4, "getInt32"],
  uint: [4, "getUint32"], uint32: [4, "getUint32"],
  float: [4, "getFloat32"], float32: [4, "getFloat32"],
  double: [8, "getFloat64"], float64: [8, "getFloat64"],
};

function locatePlyHeader(bytes) {
  const limit = Math.min(bytes.length, 1024 * 1024);
  const text = new TextDecoder("ascii").decode(bytes.subarray(0, limit));
  const marker = text.indexOf("end_header");
  if (marker < 0) throw new Error("PLY 文件头超过 1 MiB 或格式无效");
  const newline = text.indexOf("\n", marker);
  if (newline < 0) throw new Error("PLY 文件头没有正常结束");
  return { text: text.slice(0, newline + 1), dataOffset: newline + 1 };
}

function plyHeaderInfo(bytes) {
  const { text, dataOffset } = locatePlyHeader(bytes);
  const lines = text.split(/\r?\n/);
  let format = null;
  let currentElement = null;
  let vertexCount = 0;
  const properties = [];
  for (const raw of lines) {
    const parts = raw.trim().split(/\s+/);
    if (parts[0] === "format") format = parts[1];
    if (parts[0] === "element") {
      currentElement = parts[1];
      if (currentElement === "vertex") vertexCount = Number(parts[2]);
    }
    if (parts[0] === "property" && currentElement === "vertex") {
      if (parts[1] === "list") throw new Error("不支持顶点中的 PLY list 属性");
      const type = parts[1].toLowerCase();
      if (!plyTypes[type]) throw new Error(`不支持的 PLY 属性类型：${type}`);
      properties.push({ type, name: parts[2] });
    }
  }
  if (!format || !Number.isSafeInteger(vertexCount) || vertexCount <= 0) {
    throw new Error("PLY 文件没有有效顶点数据");
  }
  for (const required of ["x", "y", "z"]) {
    if (!properties.some((property) => property.name === required)) {
      throw new Error(`PLY 顶点缺少 ${required} 属性`);
    }
  }
  return { format, vertexCount, properties, dataOffset };
}

function normalizedColor(value, type) {
  if (!Number.isFinite(value)) return 190;
  if (type === "float" || type === "float32" || type === "double" || type === "float64") {
    return Math.max(0, Math.min(255, Math.round(value <= 1 ? value * 255 : value)));
  }
  return Math.max(0, Math.min(255, Math.round(value)));
}

function parseBinaryVertices(buffer, info) {
  const littleEndian = info.format === "binary_little_endian";
  if (!littleEndian && info.format !== "binary_big_endian") {
    throw new Error(`不支持的 PLY 格式：${info.format}`);
  }
  let stride = 0;
  const properties = info.properties.map((property) => {
    const [size, getter] = plyTypes[property.type];
    const result = { ...property, size, getter, offset: stride };
    stride += size;
    return result;
  });
  const needed = info.dataOffset + info.vertexCount * stride;
  if (needed > buffer.byteLength) throw new Error("PLY 顶点数据不完整");
  const view = new DataView(buffer);
  const byName = Object.fromEntries(properties.map((property) => [property.name, property]));
  const maxPoints = 50000;
  const step = Math.max(1, Math.ceil(info.vertexCount / maxPoints));
  const sampledCount = Math.ceil(info.vertexCount / step);
  const positions = new Float32Array(sampledCount * 3);
  const colors = new Uint8Array(sampledCount * 3);
  let output = 0;
  const read = (base, property) => view[property.getter](base + property.offset, littleEndian);
  for (let index = 0; index < info.vertexCount; index += step) {
    const base = info.dataOffset + index * stride;
    positions[output * 3] = read(base, byName.x);
    positions[output * 3 + 1] = read(base, byName.y);
    positions[output * 3 + 2] = read(base, byName.z);
    const red = byName.red || byName.r;
    const green = byName.green || byName.g;
    const blue = byName.blue || byName.b;
    colors[output * 3] = red ? normalizedColor(read(base, red), red.type) : 199;
    colors[output * 3 + 1] = green ? normalizedColor(read(base, green), green.type) : 243;
    colors[output * 3 + 2] = blue ? normalizedColor(read(base, blue), blue.type) : 107;
    output += 1;
  }
  return { positions, colors, sourceCount: info.vertexCount };
}

function parseAsciiVertices(buffer, info) {
  const text = new TextDecoder("utf-8").decode(new Uint8Array(buffer, info.dataOffset));
  let cursor = 0;
  const nextToken = () => {
    while (cursor < text.length && /\s/.test(text[cursor])) cursor += 1;
    const start = cursor;
    while (cursor < text.length && !/\s/.test(text[cursor])) cursor += 1;
    if (start === cursor) throw new Error("ASCII PLY 顶点数据不完整");
    return text.slice(start, cursor);
  };
  const maxPoints = 50000;
  const step = Math.max(1, Math.ceil(info.vertexCount / maxPoints));
  const sampledCount = Math.ceil(info.vertexCount / step);
  const positions = new Float32Array(sampledCount * 3);
  const colors = new Uint8Array(sampledCount * 3);
  const propertyIndex = Object.fromEntries(info.properties.map((property, index) => [property.name, index]));
  let output = 0;
  for (let index = 0; index < info.vertexCount; index += 1) {
    const values = info.properties.map(() => Number(nextToken()));
    if (index % step !== 0) continue;
    positions[output * 3] = values[propertyIndex.x];
    positions[output * 3 + 1] = values[propertyIndex.y];
    positions[output * 3 + 2] = values[propertyIndex.z];
    const color = (name, fallback) => {
      const propertyPosition = propertyIndex[name];
      if (propertyPosition === undefined) return fallback;
      return normalizedColor(values[propertyPosition], info.properties[propertyPosition].type);
    };
    colors[output * 3] = color("red", color("r", 199));
    colors[output * 3 + 1] = color("green", color("g", 243));
    colors[output * 3 + 2] = color("blue", color("b", 107));
    output += 1;
  }
  return { positions, colors, sourceCount: info.vertexCount };
}

function parsePly(buffer) {
  const bytes = new Uint8Array(buffer);
  if (new TextDecoder("ascii").decode(bytes.subarray(0, 3)) !== "ply") {
    throw new Error("结果文件不是 PLY 格式");
  }
  const info = plyHeaderInfo(bytes);
  if (info.format === "ascii") return parseAsciiVertices(buffer, info);
  return parseBinaryVertices(buffer, info);
}

class PointViewer {
  constructor(canvas, message) {
    this.canvas = canvas;
    this.message = message;
    this.context = canvas.getContext("2d", { alpha: true });
    this.positions = null;
    this.colors = null;
    this.yaw = -0.55;
    this.pitch = -0.28;
    this.zoom = 1;
    this.dragging = false;
    this.lastX = 0;
    this.lastY = 0;
    this.loadToken = 0;
    this.bind();
    new ResizeObserver(() => this.render()).observe(canvas.parentElement);
  }

  bind() {
    this.canvas.addEventListener("pointerdown", (event) => {
      this.dragging = true;
      this.lastX = event.clientX;
      this.lastY = event.clientY;
      this.canvas.setPointerCapture(event.pointerId);
    });
    this.canvas.addEventListener("pointermove", (event) => {
      if (!this.dragging) return;
      this.yaw += (event.clientX - this.lastX) * 0.009;
      this.pitch = Math.max(-1.5, Math.min(1.5, this.pitch + (event.clientY - this.lastY) * 0.009));
      this.lastX = event.clientX;
      this.lastY = event.clientY;
      this.render();
    });
    this.canvas.addEventListener("pointerup", () => { this.dragging = false; });
    this.canvas.addEventListener("pointercancel", () => { this.dragging = false; });
    this.canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      this.zoom = Math.max(0.35, Math.min(5, this.zoom * Math.exp(-event.deltaY * 0.001)));
      this.render();
    }, { passive: false });
    this.canvas.addEventListener("dblclick", () => {
      this.yaw = -0.55;
      this.pitch = -0.28;
      this.zoom = 1;
      this.render();
    });
  }

  async load(url) {
    const token = ++this.loadToken;
    this.message.hidden = false;
    this.message.textContent = "正在载入模型预览…";
    this.positions = null;
    this.render();
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const buffer = await response.arrayBuffer();
      await new Promise((resolve) => setTimeout(resolve, 0));
      const model = parsePly(buffer);
      if (token !== this.loadToken) return;
      this.setModel(model);
      this.message.hidden = true;
    } catch (error) {
      if (token !== this.loadToken) return;
      this.message.hidden = false;
      this.message.textContent = `模型预览失败：${error.message}`;
    }
  }

  setModel(model) {
    const source = model.positions;
    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    for (let index = 0; index < source.length; index += 3) {
      const x = source[index], y = source[index + 1], z = source[index + 2];
      if (!Number.isFinite(x + y + z)) continue;
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      minZ = Math.min(minZ, z); maxZ = Math.max(maxZ, z);
    }
    const extent = Math.max(maxX - minX, maxY - minY, maxZ - minZ);
    if (!Number.isFinite(extent) || extent <= 0) throw new Error("模型顶点范围无效");
    const centerX = (minX + maxX) / 2;
    const centerY = (minY + maxY) / 2;
    const centerZ = (minZ + maxZ) / 2;
    this.positions = new Float32Array(source.length);
    for (let index = 0; index < source.length; index += 3) {
      this.positions[index] = (source[index] - centerX) / extent;
      this.positions[index + 1] = (source[index + 1] - centerY) / extent;
      this.positions[index + 2] = (source[index + 2] - centerZ) / extent;
    }
    this.colors = model.colors;
    this.yaw = -0.55;
    this.pitch = -0.28;
    this.zoom = 1;
    this.render();
  }

  render() {
    const bounds = this.canvas.parentElement.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(bounds.width * dpr));
    const height = Math.max(1, Math.round(bounds.height * dpr));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }
    const context = this.context;
    context.clearRect(0, 0, width, height);
    if (!this.positions || !this.positions.length) return;

    const cosY = Math.cos(this.yaw), sinY = Math.sin(this.yaw);
    const cosP = Math.cos(this.pitch), sinP = Math.sin(this.pitch);
    const focal = Math.min(width, height) * 1.55 * this.zoom;
    const centerX = width / 2, centerY = height / 2;
    const pointSize = Math.max(1, dpr * 1.15);
    context.globalAlpha = 0.84;
    for (let index = 0; index < this.positions.length; index += 3) {
      const x = this.positions[index], y = this.positions[index + 1], z = this.positions[index + 2];
      const x1 = cosY * x + sinY * z;
      const z1 = -sinY * x + cosY * z;
      const y2 = cosP * y - sinP * z1;
      const z2 = sinP * y + cosP * z1;
      const depth = 2.35 - z2;
      if (depth <= 0.05) continue;
      const screenX = centerX + (x1 * focal) / depth;
      const screenY = centerY - (y2 * focal) / depth;
      if (screenX < 0 || screenX >= width || screenY < 0 || screenY >= height) continue;
      const colorIndex = index;
      context.fillStyle = `rgb(${this.colors[colorIndex]},${this.colors[colorIndex + 1]},${this.colors[colorIndex + 2]})`;
      context.fillRect(screenX, screenY, pointSize, pointSize);
    }
    context.globalAlpha = 1;
  }
}

const imuChart = new ImuChart(elements.imuChart);
const processViewer = new PointViewer(
  elements.processViewer,
  elements.processViewerMessage,
);
const modelViewer = new PointViewer(elements.viewer, elements.viewerMessage);

for (const [image, placeholder] of [
  [elements.liveRgb, elements.rgbPlaceholder],
  [elements.liveDepth, elements.depthPlaceholder],
]) {
  image.addEventListener("load", () => { placeholder.hidden = true; });
  image.addEventListener("error", () => {
    placeholder.hidden = false;
    placeholder.textContent = "预览帧暂时不可用，正在重试…";
  });
}

window.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    pollState();
    pollLive();
  }
});

fetchDevices(false);
pollState();
setInterval(pollState, 1000);
setInterval(pollLive, 250);
