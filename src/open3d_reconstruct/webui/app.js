"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const MAX_MESH_PREVIEW_FACES = 600000;

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
  openRecording: $("#open-recording"),
  openRecordingLabel: $("#open-recording-label"),
  manageRecordings: $("#manage-recordings"),
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
  emptyStage: $("#empty-stage"),
  emptyStageTitle: $("#empty-stage-title"),
  emptyStageCopy: $("#empty-stage-copy"),
  processPanel: $("#process-panel"),
  processKicker: $("#process-kicker"),
  processTitle: $("#process-title"),
  processFrameRate: $("#process-frame-rate"),
  processFrameCount: $("#process-frame-count"),
  captureVisual: $("#capture-visual"),
  liveRgb: $("#live-rgb"),
  liveRgbBuffer: $("#live-rgb-buffer"),
  liveDepth: $("#live-depth"),
  liveDepthBuffer: $("#live-depth-buffer"),
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
  imuOrientation: $("#imu-orientation"),
  imuAttitude: $("#imu-attitude"),
  conversionVisual: $("#conversion-visual"),
  pipelineSteps: $$("#pipeline-steps li"),
  pipelineProgressBar: $("#pipeline-progress-bar"),
  pipelineLabel: $("#pipeline-label"),
  pipelineDetail: $("#pipeline-detail"),
  processActivity: $("#process-activity"),
  processActivityTitle: $("#process-activity-title"),
  processActivityDetail: $("#process-activity-detail"),
  processElapsed: $("#process-elapsed"),
  conversionContent: $("#conversion-content"),
  conversionPlaceholder: $("#conversion-placeholder"),
  matchingDiagnostics: $("#matching-diagnostics"),
  matchingHeatmap: $("#matching-heatmap"),
  matchingTrend: $("#matching-trend"),
  matchingCount: $("#matching-count"),
  matchingSuccessRate: $("#matching-success-rate"),
  matchingCurrent: $("#matching-current"),
  matchingMotion: $("#matching-motion"),
  conversionRgbd: $("#conversion-rgbd"),
  processRgb: $("#process-rgb"),
  processDepth: $("#process-depth"),
  processRgbLabel: $("#process-rgb-label"),
  processDepthLabel: $("#process-depth-label"),
  conversionModel: $("#conversion-model"),
  processViewer: $("#process-viewer"),
  processViewerMessage: $("#process-viewer-message"),
  processViewerOrientation: $("#process-viewer-orientation"),
  processModelLabel: $("#process-model-label"),
  resultPanel: $("#result-panel"),
  resultName: $("#result-name"),
  resultSize: $("#result-size"),
  downloadMesh: $("#download-mesh"),
  downloadRecording: $("#download-recording"),
  viewer: $("#model-viewer"),
  meshViewer: $("#model-mesh-viewer"),
  viewerMessage: $("#viewer-message"),
  viewerOrientation: $("#model-viewer-orientation"),
  viewerStyleButtons: $$("#model-viewer-style [data-viewer-style]"),
  viewerNavigationButtons: $$("#model-viewer-navigation [data-viewer-navigation]"),
  viewerScale: $("#model-viewer-scale"),
  viewerScaleBar: $("#model-viewer-scale-bar"),
  viewerScaleValue: $("#model-viewer-scale-value"),
  viewerBounds: $("#model-viewer-bounds"),
  recordingManagerDialog: $("#recording-manager-dialog"),
  closeRecordingManager: $("#close-recording-manager"),
  refreshRecordings: $("#refresh-recordings"),
  recordingManagerList: $("#recording-manager-list"),
  recordingManagerCount: $("#recording-manager-count"),
  recordingManagerVideoSize: $("#recording-manager-video-size"),
  recordingManagerOutputSize: $("#recording-manager-output-size"),
  deleteRecordingDialog: $("#delete-recording-dialog"),
  closeDeleteRecording: $("#close-delete-recording"),
  cancelDeleteRecording: $("#cancel-delete-recording"),
  deleteRecordingOnly: $("#delete-recording-only"),
  deleteRecordingAll: $("#delete-recording-all"),
  deleteRecordingMessage: $("#delete-recording-message"),
  deleteRecordingArtifacts: $("#delete-recording-artifacts"),
  deleteRecordingNote: $("#delete-recording-note"),
  reconstructionDialog: $("#reconstruction-dialog"),
  reconstructionForm: $("#reconstruction-form"),
  closeReconstructionDialog: $("#close-reconstruction-dialog"),
  cancelReconstruction: $("#cancel-reconstruction"),
  reconstructionPresets: $$('[data-reconstruction-preset]'),
  reconstructionVoxel: $("#reconstruction-voxel"),
  reconstructionDepthMax: $("#reconstruction-depth-max"),
  reconstructionGlobal: $("#reconstruction-global"),
  reconstructionFragmentSize: $("#reconstruction-fragment-size"),
  reconstructionKeyframeInterval: $("#reconstruction-keyframe-interval"),
  reconstructionIcp: $("#reconstruction-icp"),
  reconstructionDepthDiff: $("#reconstruction-depth-diff"),
  qualityIndicator: $("#quality-indicator"),
  qualitySummaryTitle: $("#quality-summary-title"),
  qualitySummaryDetail: $("#quality-summary-detail"),
};

const phaseLabels = {
  idle: "等待数据",
  importing: "正在导入",
  recording: "正在录制",
  stopping: "正在封装",
  recorded: "等待重建",
  converting: "正在重建",
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
  livePollBusy: false,
  processRgbdKey: null,
  processModelKey: null,
  recordingManagerBusy: false,
  recordingsByName: new Map(),
  pendingRecordingDelete: null,
};

const reconstructionPresets = {
  fast: {
    label: "速度优先",
    stride: "4",
    voxel: "0.08",
    depthMax: "3",
    global: "fgr",
    fragmentSize: "60",
    keyframeInterval: "10",
    icp: "point_to_plane",
    depthDiff: "0.07",
  },
  balanced: {
    label: "均衡",
    stride: "2",
    voxel: "0.05",
    depthMax: "3",
    global: "ransac",
    fragmentSize: "100",
    keyframeInterval: "5",
    icp: "color",
    depthDiff: "0.07",
  },
  quality: {
    label: "质量优先",
    stride: "1",
    voxel: "0.03",
    depthMax: "3",
    global: "ransac",
    fragmentSize: "100",
    keyframeInterval: "3",
    icp: "color",
    depthDiff: "0.05",
  },
};

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / (1024 ** exponent);
  return `${value.toFixed(value >= 100 || exponent === 0 ? 0 : value >= 10 ? 1 : 2)} ${units[exponent]}`;
}

function sceneLengthUnit(meters) {
  if (meters >= 1000) return { multiplier: 0.001, unit: "km" };
  if (meters >= 1) return { multiplier: 1, unit: "m" };
  if (meters >= 0.01) return { multiplier: 100, unit: "cm" };
  return { multiplier: 1000, unit: "mm" };
}

function formatSceneNumber(value) {
  const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return value.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function formatSceneLength(meters) {
  if (!Number.isFinite(meters) || meters <= 0) return "—";
  const { multiplier, unit } = sceneLengthUnit(meters);
  return `${formatSceneNumber(meters * multiplier)} ${unit}`;
}

function formatSceneDimensions(dimensions) {
  if (!Array.isArray(dimensions) || dimensions.some((value) => !Number.isFinite(value))) {
    return "XYZ —";
  }
  const maximum = Math.max(...dimensions);
  const { multiplier, unit } = sceneLengthUnit(maximum);
  const values = dimensions.map((value) => formatSceneNumber(value * multiplier));
  return `XYZ ${values.join(" × ")} ${unit}`;
}

function niceScaleLength(targetMeters) {
  if (!Number.isFinite(targetMeters) || targetMeters <= 0) return null;
  const magnitude = 10 ** Math.floor(Math.log10(targetMeters));
  let best = magnitude;
  let bestDistance = Infinity;
  for (const factor of [1, 2, 5, 10]) {
    const candidate = factor * magnitude;
    const distance = Math.abs(Math.log(candidate / targetMeters));
    if (distance < bestDistance) {
      best = candidate;
      bestDistance = distance;
    }
  }
  return best;
}

function formatDuration(seconds) {
  const value = Math.max(0, Math.round(finiteNumber(seconds)));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const remaining = value % 60;
  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(remaining).padStart(2, "0")}`;
}

function withVersion(url, version) {
  if (!url) return "";
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}v=${encodeURIComponent(version)}`;
}

function basename(path) {
  if (!path) return "—";
  return String(path).split(/[\\/]/).pop() || path;
}

function finiteNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function wrapRadians(value) {
  const circle = Math.PI * 2;
  return ((value + Math.PI) % circle + circle) % circle - Math.PI;
}

function formatMetric(value) {
  const number = Math.max(0, finiteNumber(value));
  if (number === 0) return "0";
  if (number >= 1_000_000 || number < 0.01) return number.toExponential(2);
  return number.toLocaleString("zh-CN", { maximumFractionDigits: number >= 100 ? 0 : 2 });
}

function vectorText(values, digits = 3) {
  if (!Array.isArray(values) || values.length < 3) return "— · — · —";
  return values.slice(0, 3).map((value) => finiteNumber(value).toFixed(digits)).join(" · ");
}

function liveIsSynchronized(live) {
  return Boolean(
    live
    && finiteNumber(live.sequence, 0) > 0
    && live.rgb_url
    && live.depth_url
  );
}

class ImuOrientation {
  constructor(canvas) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d");
    this.roll = 0;
    this.pitch = 0;
    this.yaw = 0;
    this.available = false;
    this.lastTimestamp = null;
    if ("ResizeObserver" in window) {
      new ResizeObserver(() => this.render()).observe(canvas.parentElement);
    }
    this.render();
  }

  reset() {
    this.roll = 0;
    this.pitch = 0;
    this.yaw = 0;
    this.available = false;
    this.lastTimestamp = null;
    this.render();
  }

  static wrapAngle(value) {
    let angle = value;
    while (angle > Math.PI) angle -= Math.PI * 2;
    while (angle < -Math.PI) angle += Math.PI * 2;
    return angle;
  }

  static blendAngle(current, target, weight) {
    return ImuOrientation.wrapAngle(
      current + ImuOrientation.wrapAngle(target - current) * weight,
    );
  }

  update(imu) {
    if (!imu || !imu.available || imu.timestamp_usec == null) {
      this.available = false;
      this.render();
      return;
    }
    if (imu.timestamp_usec === this.lastTimestamp) return;
    const acceleration = Array.isArray(imu.acceleration_m_s2)
      ? imu.acceleration_m_s2.map((value) => finiteNumber(value))
      : [0, 0, 0];
    const gyroscope = Array.isArray(imu.gyroscope_rad_s)
      ? imu.gyroscope_rad_s.map((value) => finiteNumber(value))
      : [0, 0, 0];
    const accelerationMagnitude = Math.hypot(...acceleration);
    const rollFromGravity = Math.atan2(acceleration[1], acceleration[2]);
    const pitchFromGravity = Math.atan2(
      -acceleration[0],
      Math.hypot(acceleration[1], acceleration[2]),
    );

    if (this.lastTimestamp == null) {
      this.roll = rollFromGravity;
      this.pitch = pitchFromGravity;
      this.yaw = 0;
    } else {
      const elapsed = (finiteNumber(imu.timestamp_usec) - this.lastTimestamp) / 1_000_000;
      const delta = Math.max(0, Math.min(elapsed, 0.12));
      this.roll = ImuOrientation.wrapAngle(this.roll + gyroscope[0] * delta);
      this.pitch = ImuOrientation.wrapAngle(this.pitch + gyroscope[1] * delta);
      this.yaw = ImuOrientation.wrapAngle(this.yaw + gyroscope[2] * delta);
      if (accelerationMagnitude > 4 && accelerationMagnitude < 16) {
        this.roll = ImuOrientation.blendAngle(this.roll, rollFromGravity, 0.06);
        this.pitch = ImuOrientation.blendAngle(this.pitch, pitchFromGravity, 0.06);
      }
    }
    this.lastTimestamp = imu.timestamp_usec;
    this.available = true;
    this.render();
  }

  rotate(point) {
    const [x, y, z] = point;
    const cosR = Math.cos(this.roll); const sinR = Math.sin(this.roll);
    const cosP = Math.cos(this.pitch); const sinP = Math.sin(this.pitch);
    const cosY = Math.cos(this.yaw); const sinY = Math.sin(this.yaw);
    const y1 = y * cosR - z * sinR;
    const z1 = y * sinR + z * cosR;
    const x2 = x * cosP + z1 * sinP;
    const z2 = -x * sinP + z1 * cosP;
    const x3 = x2 * cosY - y1 * sinY;
    const y3 = x2 * sinY + y1 * cosY;

    const viewYaw = 0.62;
    const viewPitch = -0.46;
    const x4 = x3 * Math.cos(viewYaw) + z2 * Math.sin(viewYaw);
    const z4 = -x3 * Math.sin(viewYaw) + z2 * Math.cos(viewYaw);
    return [
      x4,
      y3 * Math.cos(viewPitch) - z4 * Math.sin(viewPitch),
      y3 * Math.sin(viewPitch) + z4 * Math.cos(viewPitch),
    ];
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
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    const cssWidth = width / dpr;
    const cssHeight = height / dpr;
    context.clearRect(0, 0, cssWidth, cssHeight);
    context.globalAlpha = this.available ? 1 : 0.34;

    const origin = { x: cssWidth * 0.54, y: cssHeight * 0.52 };
    const scale = Math.min(cssWidth * 0.27, cssHeight * 0.36);
    const project = (point) => {
      const rotated = this.rotate(point);
      return {
        x: origin.x + rotated[0] * scale,
        y: origin.y - rotated[1] * scale,
        depth: rotated[2],
      };
    };

    context.strokeStyle = "rgba(210,225,217,.09)";
    context.lineWidth = 1;
    context.beginPath();
    context.ellipse(origin.x, origin.y + scale * 0.54, scale * 1.45, scale * 0.48, 0, 0, Math.PI * 2);
    context.stroke();

    const vertices = [
      [-0.42, -0.24, -0.15], [0.42, -0.24, -0.15],
      [-0.42, 0.24, -0.15], [0.42, 0.24, -0.15],
      [-0.42, -0.24, 0.15], [0.42, -0.24, 0.15],
      [-0.42, 0.24, 0.15], [0.42, 0.24, 0.15],
    ].map(project);
    const edges = [[0, 1], [0, 2], [1, 3], [2, 3], [4, 5], [4, 6], [5, 7], [6, 7], [0, 4], [1, 5], [2, 6], [3, 7]];
    context.strokeStyle = "rgba(224,237,231,.3)";
    for (const [startIndex, endIndex] of edges) {
      const start = vertices[startIndex]; const end = vertices[endIndex];
      context.beginPath(); context.moveTo(start.x, start.y); context.lineTo(end.x, end.y); context.stroke();
    }

    const axes = [
      { point: [1.15, 0, 0], color: "#ff7770", label: "X" },
      { point: [0, 1.15, 0], color: "#bfe96b", label: "Y" },
      { point: [0, 0, 1.15], color: "#70d8cc", label: "Z" },
    ].map((axis) => ({ ...axis, end: project(axis.point) }))
      .sort((left, right) => left.end.depth - right.end.depth);
    for (const axis of axes) {
      const dx = axis.end.x - origin.x;
      const dy = axis.end.y - origin.y;
      const length = Math.max(1, Math.hypot(dx, dy));
      const ux = dx / length; const uy = dy / length;
      context.strokeStyle = axis.color;
      context.fillStyle = axis.color;
      context.lineWidth = 2;
      context.beginPath(); context.moveTo(origin.x, origin.y); context.lineTo(axis.end.x, axis.end.y); context.stroke();
      context.beginPath();
      context.moveTo(axis.end.x, axis.end.y);
      context.lineTo(axis.end.x - ux * 8 - uy * 4, axis.end.y - uy * 8 + ux * 4);
      context.lineTo(axis.end.x - ux * 8 + uy * 4, axis.end.y - uy * 8 - ux * 4);
      context.closePath(); context.fill();
      context.font = "700 9px SFMono-Regular, Consolas, monospace";
      context.fillText(axis.label, axis.end.x + ux * 5 - 3, axis.end.y + uy * 5 + 3);
    }
    context.globalAlpha = 1;
    const degrees = (angle) => `${(angle * 180 / Math.PI).toFixed(1)}°`;
    elements.imuAttitude.textContent = this.available
      ? `Roll ${degrees(this.roll)} · Pitch ${degrees(this.pitch)} · Yaw ${degrees(this.yaw)}`
      : "Roll — · Pitch — · Yaw —";
  }
}

class LiveFrameBuffer {
  constructor(rgbImages, depthImages, rgbPlaceholder, depthPlaceholder) {
    this.rgbImages = rgbImages;
    this.depthImages = depthImages;
    this.rgbPlaceholder = rgbPlaceholder;
    this.depthPlaceholder = depthPlaceholder;
    this.activeIndex = 0;
    this.hasFrame = false;
    this.loading = false;
    this.pending = null;
    this.currentVersion = null;
    this.loadToken = 0;
  }

  reset() {
    this.loadToken += 1;
    this.pending = null;
    this.currentVersion = null;
    this.hasFrame = false;
    this.activeIndex = 0;
    this.loading = false;
    for (const image of [...this.rgbImages, ...this.depthImages]) {
      image.onload = null;
      image.onerror = null;
      image.classList.remove("active");
      image.removeAttribute("src");
    }
    this.rgbPlaceholder.hidden = false;
    this.depthPlaceholder.hidden = false;
    this.rgbPlaceholder.textContent = "正在连接彩色数据流…";
    this.depthPlaceholder.textContent = "正在连接深度数据流…";
  }

  request(rgbUrl, depthUrl, version) {
    if (!rgbUrl || !depthUrl || version === this.currentVersion) return;
    this.pending = { rgbUrl, depthUrl, version };
    this.loadNext();
  }

  waitForImage(image, url) {
    return new Promise((resolve, reject) => {
      const clear = () => {
        image.onload = null;
        image.onerror = null;
      };
      image.onload = () => {
        clear();
        resolve();
      };
      image.onerror = () => {
        clear();
        reject(new Error("实时图像载入失败"));
      };
      image.src = url;
    });
  }

  async loadNext() {
    if (this.loading || !this.pending) return;
    const frame = this.pending;
    this.pending = null;
    this.loading = true;
    const token = ++this.loadToken;
    const bufferIndex = this.hasFrame ? 1 - this.activeIndex : 0;
    const rgbImage = this.rgbImages[bufferIndex];
    const depthImage = this.depthImages[bufferIndex];

    try {
      await Promise.all([
        this.waitForImage(rgbImage, frame.rgbUrl),
        this.waitForImage(depthImage, frame.depthUrl),
      ]);
      await Promise.all([rgbImage, depthImage].map(async (image) => {
        if (typeof image.decode !== "function") return;
        try {
          await image.decode();
        } catch (_error) {
          // The load event already proved that the frame is renderable.
        }
      }));
      if (token !== this.loadToken) return;

      this.rgbImages.forEach((image, index) => {
        image.classList.toggle("active", index === bufferIndex);
      });
      this.depthImages.forEach((image, index) => {
        image.classList.toggle("active", index === bufferIndex);
      });
      this.activeIndex = bufferIndex;
      this.hasFrame = true;
      this.currentVersion = frame.version;
      this.rgbPlaceholder.hidden = true;
      this.depthPlaceholder.hidden = true;
    } catch (_error) {
      if (token === this.loadToken && !this.hasFrame) {
        this.rgbPlaceholder.hidden = false;
        this.depthPlaceholder.hidden = false;
        this.rgbPlaceholder.textContent = "彩色预览暂时不可用，正在重试…";
        this.depthPlaceholder.textContent = "深度预览暂时不可用，正在重试…";
      }
    } finally {
      if (token !== this.loadToken) return;
      this.loading = false;
      if (this.pending) queueMicrotask(() => this.loadNext());
    }
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

function reconstructionFormValues() {
  return {
    stride: elements.frameStride.value,
    voxel: elements.reconstructionVoxel.value,
    depthMax: elements.reconstructionDepthMax.value,
    global: elements.reconstructionGlobal.value,
    fragmentSize: elements.reconstructionFragmentSize.value,
    keyframeInterval: elements.reconstructionKeyframeInterval.value,
    icp: elements.reconstructionIcp.value,
    depthDiff: elements.reconstructionDepthDiff.value,
  };
}

function matchingReconstructionPreset(values) {
  return Object.entries(reconstructionPresets).find(([, preset]) => (
    Object.keys(values).every((key) => values[key] === preset[key])
  ))?.[0] || null;
}

function updateReconstructionSummary() {
  const values = reconstructionFormValues();
  const presetName = matchingReconstructionPreset(values);
  for (const button of elements.reconstructionPresets) {
    button.classList.toggle("selected", button.dataset.reconstructionPreset === presetName);
  }
  const indicatorClass = presetName || "balanced";
  const indicatorLabel = presetName ? reconstructionPresets[presetName].label : "自定义";
  elements.qualityIndicator.className = `quality-indicator ${indicatorClass}`;
  elements.qualityIndicator.textContent = indicatorLabel;
  elements.qualitySummaryTitle.textContent = presetName
    ? `${indicatorLabel}设置`
    : "自定义重建设置";
  const strideText = values.stride === "1" ? "使用每一帧" : `每 ${values.stride} 帧取 1 帧`;
  const icpText = values.icp === "color" ? "彩色 ICP" : "点到平面 ICP";
  elements.qualitySummaryDetail.textContent = (
    `${strideText} · ${(Number(values.voxel) * 100).toFixed(0)} cm 体素 · ${icpText} · ${values.global.toUpperCase()}`
  );
}

function applyReconstructionPreset(name) {
  const preset = reconstructionPresets[name];
  if (!preset) return;
  elements.frameStride.value = preset.stride;
  elements.reconstructionVoxel.value = preset.voxel;
  elements.reconstructionDepthMax.value = preset.depthMax;
  elements.reconstructionGlobal.value = preset.global;
  elements.reconstructionFragmentSize.value = preset.fragmentSize;
  elements.reconstructionKeyframeInterval.value = preset.keyframeInterval;
  elements.reconstructionIcp.value = preset.icp;
  elements.reconstructionDepthDiff.value = preset.depthDiff;
  updateReconstructionSummary();
}

function reconstructionRequest() {
  const values = reconstructionFormValues();
  return {
    stride: Number(values.stride),
    parameters: {
      voxel_size: Number(values.voxel),
      depth_max: Number(values.depthMax),
      global_registration: values.global,
      n_frames_per_fragment: Number(values.fragmentSize),
      n_keyframes_per_n_frame: Number(values.keyframeInterval),
      icp_method: values.icp,
      depth_diff_max: Number(values.depthDiff),
    },
  };
}

function openReconstructionDialog() {
  if (elements.startConversion.disabled) return;
  updateReconstructionSummary();
  if (typeof elements.reconstructionDialog.showModal === "function") {
    elements.reconstructionDialog.showModal();
  } else {
    elements.reconstructionDialog.setAttribute("open", "");
  }
}

function closeReconstructionDialog() {
  if (typeof elements.reconstructionDialog.close === "function") {
    elements.reconstructionDialog.close();
  } else {
    elements.reconstructionDialog.removeAttribute("open");
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
  const recordReached = ["importing", "recording", "stopping", "recorded", "converting", "completed", "error"].includes(phase);
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
  if (!recordReached || phase === "importing" || phase === "recording" || phase === "stopping") {
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
    can_import_recording: true,
    can_stop_recording: false,
    can_start_conversion: false,
    task: null,
  };
  const selected = Boolean(app.selectedHardware);
  const locked = Boolean(state.task) || app.requestBusy;

  elements.startRecording.disabled = !selected || !state.can_start_recording || app.requestBusy || app.deviceBusy;
  elements.stopRecording.disabled = !state.can_stop_recording || app.requestBusy;
  elements.openRecording.disabled = !state.can_import_recording || app.requestBusy;
  elements.manageRecordings.disabled = app.requestBusy;
  elements.startConversion.disabled = !state.can_start_conversion || app.requestBusy;
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
    imuOrientation.update(imu);
  } else {
    elements.imuAccel.textContent = "— · — · —";
    elements.imuGyro.textContent = "— · — · —";
    elements.imuAccelMagnitude.textContent = "|a| —";
    elements.imuGyroMagnitude.textContent = "|ω| —";
    elements.imuTemperature.textContent = "温度 —";
    elements.imuRate.textContent = "采样率 —";
    imuOrientation.update(null);
  }
}

function renderLive(live) {
  if (!live) {
    elements.processFrameRate.textContent = "等待视频流";
    elements.processFrameCount.textContent = "0 帧";
    renderImu(null);
    return;
  }
  const captureFps = finiteNumber(live.fps);
  const previewFps = finiteNumber(live.preview_fps);
  elements.processFrameRate.textContent = `采集 ${captureFps.toFixed(1)} FPS · 预览 ${previewFps.toFixed(1)} FPS`;
  elements.processFrameCount.textContent = `${Math.max(0, Math.round(finiteNumber(live.frame_count)))} 帧`;
  const sequence = Math.round(finiteNumber(live.sequence, -1));
  if (sequence !== app.liveSequence) {
    app.liveSequence = sequence;
    if (live.rgb_url && live.depth_url) {
      const rgbVersion = live.rgb_file ? live.rgb_file.version : sequence;
      const depthVersion = live.depth_file ? live.depth_file.version : sequence;
      liveFrameBuffer.request(
        `${live.rgb_url}?v=${encodeURIComponent(rgbVersion)}`,
        `${live.depth_url}?v=${encodeURIComponent(depthVersion)}`,
        `${sequence}:${rgbVersion}:${depthVersion}`,
      );
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
  elements.pipelineLabel.textContent = conversion.label || "正在重建";
  elements.pipelineDetail.textContent = conversion.detail || "等待进度信息";
  const fragmentTotal = Number(conversion.fragment_total);
  if (conversion.stage === "make" && Number.isFinite(fragmentTotal) && fragmentTotal > 0) {
    elements.processFrameRate.textContent = `片段 ${Math.round(finiteNumber(conversion.fragment_completed))} / ${Math.round(fragmentTotal)}`;
  } else if (measurable) {
    elements.processFrameRate.textContent = `${Math.round(processed)} / ${Math.round(total)}`;
  } else {
    elements.processFrameRate.textContent = `阶段 ${Math.min(stageIndex + 1, 5)} / 5`;
  }
  elements.processFrameCount.textContent = conversion.frame_count
    ? `${conversion.frame_count} 帧数据集`
    : "正在分析数据";

  const quietSeconds = Math.max(0, finiteNumber(conversion.quiet_seconds));
  const stageElapsed = Math.max(0, finiteNumber(conversion.stage_elapsed_seconds));
  const processAlive = Boolean(conversion.process_alive);
  const quiet = processAlive && quietSeconds >= 8;
  elements.processActivity.classList.toggle("quiet", quiet);
  elements.processActivity.classList.toggle("failed", conversion.status === "failed");
  elements.processElapsed.textContent = `阶段 ${formatDuration(stageElapsed)}`;
  elements.processElapsed.title = `重建总用时 ${formatDuration(conversion.elapsed_seconds)}`;
  if (conversion.status === "failed") {
    elements.processActivityTitle.textContent = "重建进程已结束";
    elements.processActivityDetail.textContent = "请结合上方错误信息和下方日志排查";
  } else if (quiet) {
    elements.processActivityTitle.textContent = conversion.stage === "make"
      ? "当前帧对仍在计算，后端进程保持运行"
      : "进程仍在运行，当前步骤暂时没有新日志";
    elements.processActivityDetail.textContent = conversion.stage === "make"
      ? `${Math.round(quietSeconds)} 秒未返回新结果 · 热力图会在位姿估计完成后更新`
      : `本阶段已用时 ${formatDuration(stageElapsed)} · ${Math.round(quietSeconds)} 秒无新输出`;
  } else if (conversion.stage === "make" && conversion.fragment_total) {
    const finished = Math.round(finiteNumber(conversion.fragment_completed));
    elements.processActivityTitle.textContent = `局部片段计算中 · 已完成 ${finished}/${conversion.fragment_total}`;
    elements.processActivityDetail.textContent = quietSeconds < 2
      ? "刚刚收到帧对结果 · 匹配热力图与信息强度趋势正在更新"
      : `${Math.round(quietSeconds)} 秒前收到进度 · 子进程仍在计算`;
  } else {
    elements.processActivityTitle.textContent = processAlive ? "后端进程正在运行" : "正在整理阶段结果";
    elements.processActivityDetail.textContent = quietSeconds < 2
      ? "刚刚收到后端进度"
      : `${Math.round(quietSeconds)} 秒前收到最新日志`;
  }

  const artifacts = conversion.artifacts || {};
  const rgbdArtifact = artifacts.rgbd
    || (conversion.artifact && conversion.artifact.kind === "rgbd" ? conversion.artifact : null);
  const modelArtifact = artifacts.model
    || (conversion.artifact && conversion.artifact.kind === "model" ? conversion.artifact : null);
  const showMatching = conversion.stage === "make" && Boolean(conversion.matching);
  const showRgbd = conversion.stage === "extract" && Boolean(rgbdArtifact);
  const showModel = Boolean(modelArtifact);
  elements.matchingDiagnostics.hidden = !showMatching;
  elements.conversionPlaceholder.hidden = showMatching || showRgbd || showModel;
  elements.conversionRgbd.hidden = !showRgbd;
  elements.conversionModel.hidden = !modelArtifact;
  elements.conversionContent.classList.toggle("has-model", showModel);

  const matching = conversion.matching || {};
  if (showMatching) {
    const attempted = Math.max(0, Math.round(finiteNumber(matching.attempted)));
    const expected = Math.max(0, Math.round(finiteNumber(matching.expected)));
    const succeeded = Math.max(0, Math.round(finiteNumber(matching.succeeded)));
    const active = Array.isArray(matching.active) ? matching.active : [];
    const current = active.length ? active[active.length - 1] : matching.last;
    elements.matchingCount.textContent = expected > 0 ? `${attempted} / ${expected}` : `${attempted} / —`;
    elements.matchingSuccessRate.textContent = attempted > 0
      ? `${Math.round((succeeded / attempted) * 100)}%`
      : "—";
    elements.matchingCurrent.textContent = current
      ? `${Math.round(finiteNumber(current.source))} ↔ ${Math.round(finiteNumber(current.target))}${active.length > 1 ? ` +${active.length - 1}` : ""}`
      : "等待启动";
    if (matching.last) {
      const last = matching.last;
      elements.matchingMotion.textContent = last.success
        ? `${last.kind === "loop" ? "回环候选" : "相邻帧"} · 位移 ${(finiteNumber(last.translation_m) * 100).toFixed(1)} cm · 旋转 ${finiteNumber(last.rotation_deg).toFixed(1)}° · 信息迹 ${formatMetric(last.information)}`
        : `最近帧对 ${Math.round(finiteNumber(last.source))} ↔ ${Math.round(finiteNumber(last.target))} 匹配失败，未加入有效约束。`;
    } else {
      elements.matchingMotion.textContent = "等待首组位姿估计…";
    }
    matchingDiagnostics.update(matching);
  }

  if (showRgbd) {
    const frameNumber = Math.max(1, Math.round(finiteNumber(rgbdArtifact.frame_index)) + 1);
    const frameCount = Math.max(frameNumber, Math.round(finiteNumber(rgbdArtifact.frame_count, frameNumber)));
    const frameLabel = `${rgbdArtifact.label || "RGB-D 帧"} · ${frameNumber}/${frameCount}`;
    elements.processRgbLabel.textContent = frameLabel;
    elements.processDepthLabel.textContent = frameLabel;
    if (app.processRgbdKey !== rgbdArtifact.version) {
      app.processRgbdKey = rgbdArtifact.version;
      elements.processRgb.src = withVersion(rgbdArtifact.rgb_url, rgbdArtifact.rgb.version);
      elements.processDepth.src = withVersion(rgbdArtifact.depth_url, rgbdArtifact.depth.version);
    }
  } else {
    app.processRgbdKey = null;
  }

  if (modelArtifact) {
    elements.processModelLabel.textContent = `${modelArtifact.label || "局部点云"} · 任意方向旋转 · 滚轮缩放`;
    if (app.processModelKey !== modelArtifact.version) {
      app.processModelKey = modelArtifact.version;
      processViewer.load(withVersion(modelArtifact.model_url, modelArtifact.version));
    }
  } else {
    app.processModelKey = null;
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
      imuOrientation.reset();
      liveFrameBuffer.reset();
    }
    const synchronized = liveIsSynchronized(state.live);
    elements.processKicker.textContent = synchronized ? "RECORDING" : "CONNECTING";
    elements.processTitle.textContent = synchronized ? "正在录制" : "正在连接并同步 RGB-D";
    renderLive(state.live);
  } else {
    if (app.lastPhase !== "converting" && state.phase === "converting") {
      app.processRgbdKey = null;
      app.processModelKey = null;
    }
    elements.processKicker.textContent = "RECONSTRUCTION";
    elements.processTitle.textContent = "正在重建";
    renderConversion(state.conversion);
  }
}

function renderEmptyStage(state) {
  const processActive = state.phase === "recording"
    || state.phase === "stopping"
    || state.phase === "converting"
    || (state.phase === "error" && state.conversion);
  const resultReady = Boolean(state.mesh_url && state.mesh && state.mesh.exists);
  elements.emptyStage.hidden = processActive || resultReady;
  if (elements.emptyStage.hidden) return;

  if (state.phase === "importing") {
    const progress = state.recording_import || {};
    elements.emptyStageTitle.textContent = "正在导入已有录制";
    elements.emptyStageCopy.textContent = `${basename(progress.name)} · ${Math.round(finiteNumber(progress.percent))}% · ${formatBytes(progress.received_bytes)} / ${formatBytes(progress.total_bytes)}`;
  } else if (state.phase === "recorded") {
    elements.emptyStageTitle.textContent = "录制文件已就绪";
    elements.emptyStageCopy.textContent = "点击左侧“开始重建”，确认速度与质量参数后执行。";
  } else if (state.phase === "error") {
    elements.emptyStageTitle.textContent = "任务未完成";
    elements.emptyStageCopy.textContent = state.error || "请查看下方日志后重试。";
  } else if (app.selectedHardware) {
    elements.emptyStageTitle.textContent = "准备连接相机";
    elements.emptyStageCopy.textContent = "点击左侧“连接并录制”，同步画面后自动开始采集。";
  } else {
    elements.emptyStageTitle.textContent = "选择相机或录制文件";
    elements.emptyStageCopy.textContent = "连接设备进行实时录制，或直接打开已有的 MKV/BAG。";
  }
}

function renderState(state) {
  app.state = state;
  setServerOnline(true);

  if (state.hardware && state.phase !== "idle" && app.selectedHardware !== state.hardware) {
    selectHardware(state.hardware);
  }

  const synchronized = liveIsSynchronized(state.live);
  const phaseLabel = state.phase === "recording" && !synchronized
    ? "连接中"
    : (phaseLabels[state.phase] || state.phase);
  if (state.phase !== "importing" && !app.requestBusy) {
    elements.openRecordingLabel.textContent = "打开本地录制";
  }
  elements.phaseBadge.className = `phase-badge ${state.phase}`;
  elements.phaseBadge.innerHTML = `<span></span>${phaseLabel}`;
  elements.metricHardware.textContent = state.hardware_label ? `${state.hardware_label} · #${state.device}` : "—";
  elements.metricRecording.textContent = state.recording ? `${state.recording.path}${state.recording.exists ? ` · ${formatBytes(state.recording.size)}` : ""}` : "—";
  elements.metricDataset.textContent = state.dataset || "—";

  elements.errorBanner.hidden = !state.error;
  elements.errorBanner.textContent = state.error || "";

  const recordingPath = state.recording ? state.recording.path : "等待生成录制文件";
  switch (state.phase) {
    case "importing": {
      const progress = state.recording_import || {};
      const percent = Math.round(finiteNumber(progress.percent));
      elements.openRecordingLabel.textContent = `正在导入 ${percent}%`;
      setSummary("running", "正在导入已有录制", `${basename(progress.name)} · ${formatBytes(progress.received_bytes)} / ${formatBytes(progress.total_bytes)}`);
      break;
    }
    case "recording":
      if (synchronized) {
        setSummary("running", "RGB-D 已同步，正在录制", recordingPath);
      } else {
        setSummary("running", "正在连接相机", "等待首组同步 RGB-D 画面");
      }
      break;
    case "stopping":
      setSummary("running", "正在安全结束录制", "正在等待相机后端封装文件，请不要拔出设备");
      break;
    case "recorded":
      setSummary("success", "录制文件已就绪，可以开始重建", `${recordingPath} · ${formatBytes(state.recording && state.recording.size)}`);
      break;
    case "converting":
      setSummary("running", "正在重建三维场景", (state.conversion && state.conversion.detail) || state.dataset || "正在准备数据集");
      break;
    case "completed":
      setSummary("success", "重建已完成", state.mesh ? state.mesh.path : "integrated.ply");
      break;
    case "error":
      setSummary("failed", "任务未能完成", state.error || "请检查进程日志");
      break;
    default:
      if (app.selectedHardware) {
        setSummary("", "相机已选择", "连接后会等待 RGB-D 同步，再自动开始录制");
      } else {
        setSummary("", "选择相机或打开录制", "可实时录制，也可零拷贝引用本机 MKV/BAG");
      }
  }

  renderLogs(state);
  updateControls();
  updateJourney();
  renderProcess(state);
  renderResult(state);
  renderEmptyStage(state);
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
  if (
    document.hidden
    || app.livePollBusy
    || !app.state
    || !["recording", "stopping"].includes(app.state.phase)
  ) return;
  app.livePollBusy = true;
  try {
    const payload = await api("/api/live/state");
    renderLive(payload.live);
  } catch (_error) {
    // Main state polling owns the server connectivity indicator.
  } finally {
    app.livePollBusy = false;
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

function localDateTime(value) {
  if (!value) return "时间未知";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "时间未知";
  return parsed.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function recordingStorageLabel(mode) {
  return {
    owned: "项目文件",
    hardlink: "硬链接 · 零拷贝",
    symlink: "符号引用 · 零拷贝",
  }[mode] || "本地文件";
}

function formatStorageBytes(bytes) {
  return finiteNumber(bytes) > 0 ? formatBytes(bytes) : "0 B";
}

function createTextElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function renderRecordingManager(recordings) {
  const items = Array.isArray(recordings?.items) ? recordings.items : [];
  const totals = recordings?.totals || {};
  app.recordingsByName = new Map(items.map((item) => [item.name, item]));
  elements.recordingManagerCount.textContent = `${finiteNumber(totals.count)} 个`;
  elements.recordingManagerVideoSize.textContent = formatStorageBytes(totals.additional_bytes);
  elements.recordingManagerOutputSize.textContent = formatStorageBytes(totals.output_bytes);
  elements.recordingManagerList.replaceChildren();

  if (!items.length) {
    elements.recordingManagerList.append(
      createTextElement("div", "recording-manager-empty", "尚无本地录制"),
    );
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const item of items) {
    const row = document.createElement("article");
    row.className = `recording-item${item.active ? " active" : ""}${item.exists ? "" : " broken"}`;

    const main = createTextElement("div", "recording-item-main", "");
    const title = createTextElement("div", "recording-item-title", "");
    title.append(createTextElement("strong", "", item.name));
    title.append(createTextElement(
      "span",
      `recording-badge${item.active ? " active" : ""}`,
      item.exists ? (item.active ? "当前使用" : recordingStorageLabel(item.storage_mode)) : "引用失效",
    ));
    const pathText = item.source_path ? `${item.path} → ${item.source_path}` : item.path;
    const path = createTextElement("p", "recording-item-path", pathText);
    path.title = pathText;
    const metadata = createTextElement("div", "recording-item-meta", "");
    metadata.append(createTextElement("span", "", `视频 ${formatStorageBytes(item.size)}`));
    metadata.append(createTextElement("span", "", `产物 ${formatStorageBytes(item.dataset?.size)}`));
    metadata.append(createTextElement("span", "", localDateTime(item.modified_at)));
    if (item.dataset?.has_preprocessed) {
      metadata.append(createTextElement("span", "ready", "RGB-D 已提取"));
    }
    if (item.dataset?.mesh_exists) {
      metadata.append(createTextElement("span", "ready", "模型已生成"));
    }
    main.append(title, path, metadata);

    const actions = createTextElement("div", "recording-item-actions", "");
    const useButton = createTextElement(
      "button",
      "recording-use",
      item.active ? "当前使用" : (item.dataset?.mesh_exists ? "打开结果" : "用于重建"),
    );
    useButton.type = "button";
    useButton.dataset.recordingAction = "use";
    useButton.dataset.recordingName = item.name;
    useButton.disabled = !item.can_use || item.active || app.recordingManagerBusy;
    const deleteButton = createTextElement("button", "recording-delete", "删除");
    deleteButton.type = "button";
    deleteButton.dataset.recordingAction = "delete";
    deleteButton.dataset.recordingName = item.name;
    deleteButton.disabled = !item.can_delete || app.recordingManagerBusy;
    actions.append(useButton, deleteButton);
    row.append(main, actions);
    fragment.append(row);
  }
  elements.recordingManagerList.append(fragment);
}

async function refreshRecordingManager() {
  if (app.recordingManagerBusy) return;
  app.recordingManagerBusy = true;
  elements.refreshRecordings.disabled = true;
  elements.refreshRecordings.textContent = "读取中";
  elements.recordingManagerList.replaceChildren(
    createTextElement("div", "recording-manager-empty", "正在读取本地录制…"),
  );
  try {
    const payload = await api("/api/recordings");
    app.recordingManagerBusy = false;
    renderRecordingManager(payload.recordings);
  } catch (error) {
    app.recordingManagerBusy = false;
    elements.recordingManagerList.replaceChildren(
      createTextElement("div", "recording-manager-empty", `读取失败：${error.message}`),
    );
  } finally {
    app.recordingManagerBusy = false;
    elements.refreshRecordings.disabled = false;
    elements.refreshRecordings.textContent = "刷新";
  }
}

function openRecordingManager() {
  if (!elements.recordingManagerDialog.open) {
    elements.recordingManagerDialog.showModal();
  }
  refreshRecordingManager();
}

async function useManagedRecording(item) {
  if (!item || app.recordingManagerBusy) return;
  app.recordingManagerBusy = true;
  elements.recordingManagerList.querySelectorAll("button").forEach((button) => {
    button.disabled = true;
  });
  const hardware = item.hardware === "d435" && ["d435", "d435i"].includes(app.selectedHardware)
    ? app.selectedHardware
    : item.hardware;
  try {
    const payload = await post("/api/recordings/use", {
      name: item.name,
      hardware,
    });
    renderState(payload.state);
    elements.recordingManagerDialog.close();
    if (payload.state.phase === "recorded") openReconstructionDialog();
  } catch (error) {
    elements.errorBanner.hidden = false;
    elements.errorBanner.textContent = error.message;
    app.recordingManagerBusy = false;
    refreshRecordingManager();
    return;
  }
  app.recordingManagerBusy = false;
}

function renderDeleteArtifact(label, value) {
  const row = document.createElement("div");
  row.append(
    createTextElement("span", "", label),
    createTextElement("strong", "", value),
  );
  return row;
}

function openDeleteRecordingDialog(item) {
  if (!item) return;
  app.pendingRecordingDelete = item;
  elements.deleteRecordingMessage.classList.remove("error");
  elements.deleteRecordingMessage.textContent = `将处理 ${item.name}，请选择删除范围。`;
  elements.deleteRecordingArtifacts.replaceChildren(
    renderDeleteArtifact("录制视频", formatStorageBytes(item.size)),
    renderDeleteArtifact("预处理与重建产物", formatStorageBytes(item.dataset?.size)),
    renderDeleteArtifact("关联数据集", item.dataset?.path || "—"),
  );
  const reference = ["hardlink", "symlink"].includes(item.storage_mode);
  elements.deleteRecordingNote.textContent = reference
    ? "删除后无法从本页面恢复；这里只会移除项目内引用，外部原视频不受影响。"
    : "删除的是项目保存的视频原件，操作后无法从本页面恢复。";
  elements.deleteRecordingOnly.textContent = reference ? "仅移除引用" : "仅删除视频";
  elements.deleteRecordingAll.textContent = reference ? "引用及全部产物" : "视频及全部产物";
  elements.deleteRecordingAll.disabled = !item.dataset?.exists;
  elements.deleteRecordingOnly.disabled = false;
  elements.cancelDeleteRecording.disabled = false;
  if (!elements.deleteRecordingDialog.open) elements.deleteRecordingDialog.showModal();
}

async function deletePendingRecording(deleteOutputs) {
  const item = app.pendingRecordingDelete;
  if (!item || app.recordingManagerBusy) return;
  app.recordingManagerBusy = true;
  elements.deleteRecordingOnly.disabled = true;
  elements.deleteRecordingAll.disabled = true;
  elements.cancelDeleteRecording.disabled = true;
  elements.deleteRecordingMessage.textContent = "正在删除，请稍候…";
  try {
    const payload = await post("/api/recordings/delete", {
      name: item.name,
      delete_outputs: deleteOutputs,
    });
    renderState(payload.state);
    app.recordingManagerBusy = false;
    renderRecordingManager(payload.recordings);
    app.pendingRecordingDelete = null;
    elements.deleteRecordingDialog.close();
  } catch (error) {
    elements.deleteRecordingMessage.classList.add("error");
    elements.deleteRecordingMessage.textContent = `删除失败：${error.message}`;
  } finally {
    app.recordingManagerBusy = false;
    if (elements.deleteRecordingDialog.open && app.pendingRecordingDelete) {
      elements.deleteRecordingOnly.disabled = false;
      elements.deleteRecordingAll.disabled = !app.pendingRecordingDelete.dataset?.exists;
      elements.cancelDeleteRecording.disabled = false;
    }
  }
}

async function chooseLocalRecording() {
  if (app.requestBusy) return;
  app.requestBusy = true;
  elements.errorBanner.hidden = true;
  elements.openRecordingLabel.textContent = "等待本机选择…";
  setSummary("running", "本机文件选择窗口已打开", "将建立零拷贝引用，不会上传或复制视频");
  updateControls();
  let selectedState = null;
  try {
    const payload = await post("/api/recording/select-local", {
      hardware: app.selectedHardware,
    });
    if (!payload.cancelled) {
      selectedState = payload.state;
      renderState(payload.state);
    }
  } catch (error) {
    elements.errorBanner.hidden = false;
    elements.errorBanner.textContent = error.message;
  } finally {
    app.requestBusy = false;
    elements.openRecordingLabel.textContent = "打开本地录制";
    updateControls();
  }
  if (selectedState?.phase === "recorded") openReconstructionDialog();
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

elements.openRecording.addEventListener("click", chooseLocalRecording);
elements.manageRecordings.addEventListener("click", openRecordingManager);
elements.refreshRecordings.addEventListener("click", refreshRecordingManager);
elements.closeRecordingManager.addEventListener("click", () => {
  elements.recordingManagerDialog.close();
});
elements.recordingManagerDialog.addEventListener("click", (event) => {
  if (event.target === elements.recordingManagerDialog) {
    elements.recordingManagerDialog.close();
  }
});
elements.recordingManagerList.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-recording-action]");
  if (!button) return;
  const item = app.recordingsByName.get(button.dataset.recordingName);
  if (button.dataset.recordingAction === "use") useManagedRecording(item);
  if (button.dataset.recordingAction === "delete") openDeleteRecordingDialog(item);
});
elements.closeDeleteRecording.addEventListener("click", () => {
  elements.deleteRecordingDialog.close();
});
elements.cancelDeleteRecording.addEventListener("click", () => {
  elements.deleteRecordingDialog.close();
});
elements.deleteRecordingDialog.addEventListener("close", () => {
  app.pendingRecordingDelete = null;
});
elements.deleteRecordingOnly.addEventListener("click", () => deletePendingRecording(false));
elements.deleteRecordingAll.addEventListener("click", () => deletePendingRecording(true));

elements.startConversion.addEventListener("click", openReconstructionDialog);

for (const button of elements.reconstructionPresets) {
  button.addEventListener("click", () => {
    applyReconstructionPreset(button.dataset.reconstructionPreset);
  });
}

for (const select of elements.reconstructionForm.querySelectorAll("select")) {
  select.addEventListener("change", updateReconstructionSummary);
}

elements.closeReconstructionDialog.addEventListener("click", closeReconstructionDialog);
elements.cancelReconstruction.addEventListener("click", closeReconstructionDialog);
elements.reconstructionDialog.addEventListener("click", (event) => {
  if (event.target === elements.reconstructionDialog) closeReconstructionDialog();
});
elements.reconstructionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const request = reconstructionRequest();
  closeReconstructionDialog();
  runAction(() => post("/api/convert/start", request));
});

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
    const previewUrl = state.mesh_preview_url || state.mesh_url;
    modelViewer.load(`${previewUrl}?v=${encodeURIComponent(key)}`);
  }
}

class MatchingDiagnostics {
  constructor(heatmapCanvas, trendCanvas) {
    this.heatmapCanvas = heatmapCanvas;
    this.trendCanvas = trendCanvas;
    this.heatmapContext = heatmapCanvas.getContext("2d");
    this.trendContext = trendCanvas.getContext("2d");
    this.state = null;
    if ("ResizeObserver" in window) {
      this.resizeObserver = new ResizeObserver(() => this.render());
      this.resizeObserver.observe(heatmapCanvas);
      this.resizeObserver.observe(trendCanvas);
    }
  }

  update(state) {
    this.state = state;
    this.render();
  }

  surface(canvas, context) {
    const bounds = canvas.getBoundingClientRect();
    if (bounds.width < 2 || bounds.height < 2) return null;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(bounds.width * dpr));
    const height = Math.max(1, Math.round(bounds.height * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, bounds.width, bounds.height);
    context.fillStyle = "#07090a";
    context.fillRect(0, 0, bounds.width, bounds.height);
    return { width: bounds.width, height: bounds.height };
  }

  matchingColor(information, maximum) {
    const denominator = Math.log1p(Math.max(0, maximum));
    const normalized = denominator > 0
      ? Math.max(0, Math.min(1, Math.log1p(Math.max(0, information)) / denominator))
      : 0;
    const amount = 0.18 + normalized * 0.82;
    const low = [54, 72, 68];
    const high = [191, 233, 107];
    const channels = low.map((value, index) => Math.round(value + (high[index] - value) * amount));
    return `rgb(${channels.join(",")})`;
  }

  renderHeatmap() {
    const surface = this.surface(this.heatmapCanvas, this.heatmapContext);
    if (!surface || !this.state) return;
    const context = this.heatmapContext;
    const events = Array.isArray(this.state.events) ? this.state.events : [];
    const active = Array.isArray(this.state.active) ? this.state.active : [];
    const inferredFrames = [...events, ...active].reduce(
      (maximum, event) => Math.max(maximum, finiteNumber(event.target) + 1),
      0,
    );
    const frameCount = Math.max(2, Math.round(finiteNumber(this.state.frame_count, inferredFrames)), inferredFrames);
    const margins = { left: 28, right: 9, top: 8, bottom: 21 };
    const plotWidth = Math.max(1, surface.width - margins.left - margins.right);
    const plotHeight = Math.max(1, surface.height - margins.top - margins.bottom);
    const x = (frame) => margins.left + ((frame + 0.5) / frameCount) * plotWidth;
    const y = (frame) => margins.top + ((frame + 0.5) / frameCount) * plotHeight;

    context.strokeStyle = "rgba(132,144,138,.13)";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(margins.left, margins.top);
    context.lineTo(margins.left + plotWidth, margins.top + plotHeight);
    context.stroke();

    const fragmentSize = Math.max(1, Math.round(finiteNumber(this.state.frames_per_fragment, 100)));
    context.strokeStyle = "rgba(112,216,204,.16)";
    for (let boundary = fragmentSize; boundary < frameCount; boundary += fragmentSize) {
      const boundaryX = margins.left + (boundary / frameCount) * plotWidth;
      const boundaryY = margins.top + (boundary / frameCount) * plotHeight;
      context.beginPath();
      context.moveTo(boundaryX, margins.top);
      context.lineTo(boundaryX, margins.top + plotHeight);
      context.moveTo(margins.left, boundaryY);
      context.lineTo(margins.left + plotWidth, boundaryY);
      context.stroke();
    }

    const cellWidth = Math.max(1.8, Math.min(7, (plotWidth / frameCount) * 0.86));
    const cellHeight = Math.max(1.8, Math.min(7, (plotHeight / frameCount) * 0.86));
    const drawPair = (event, color, outline = false) => {
      const source = Math.round(finiteNumber(event.source));
      const target = Math.round(finiteNumber(event.target));
      for (const [column, row] of [[target, source], [source, target]]) {
        const left = x(column) - cellWidth / 2;
        const top = y(row) - cellHeight / 2;
        if (outline) {
          context.strokeStyle = color;
          context.lineWidth = 1.25;
          context.strokeRect(left - 1, top - 1, cellWidth + 2, cellHeight + 2);
        } else {
          context.fillStyle = color;
          context.fillRect(left, top, cellWidth, cellHeight);
          if (event.kind === "loop") {
            context.strokeStyle = "rgba(237,242,239,.78)";
            context.lineWidth = 0.8;
            context.strokeRect(left - 0.5, top - 0.5, cellWidth + 1, cellHeight + 1);
          }
        }
      }
    };

    const maximum = Math.max(0, finiteNumber(this.state.information_max));
    for (const event of events) {
      drawPair(
        event,
        event.success ? this.matchingColor(event.information, maximum) : "#ff7770",
      );
    }
    for (const event of active) drawPair(event, "#eeb967", true);

    context.fillStyle = "#58625d";
    context.font = "7px SFMono-Regular, Consolas, monospace";
    context.textAlign = "left";
    context.fillText("0", margins.left, surface.height - 6);
    context.textAlign = "right";
    context.fillText(String(frameCount - 1), margins.left + plotWidth, surface.height - 6);
    context.textAlign = "right";
    context.fillText("0", margins.left - 5, margins.top + 3);
    context.fillText(String(frameCount - 1), margins.left - 5, margins.top + plotHeight);

    if (!events.length && !active.length) {
      context.fillStyle = "#84908a";
      context.font = "9px Inter, system-ui, sans-serif";
      context.textAlign = "center";
      context.fillText("正在启动 RGB-D 里程计…", margins.left + plotWidth / 2, margins.top + plotHeight / 2);
    }
  }

  renderTrend() {
    const surface = this.surface(this.trendCanvas, this.trendContext);
    if (!surface || !this.state) return;
    const context = this.trendContext;
    const events = (Array.isArray(this.state.events) ? this.state.events : []).slice(-120);
    const margins = { left: 34, right: 8, top: 12, bottom: 19 };
    const plotWidth = Math.max(1, surface.width - margins.left - margins.right);
    const plotHeight = Math.max(1, surface.height - margins.top - margins.bottom);
    const maximum = Math.max(
      0,
      ...events.filter((event) => event.success).map((event) => finiteNumber(event.information)),
    );
    const logMaximum = Math.log1p(maximum);
    const x = (index) => margins.left + (index / Math.max(1, events.length - 1)) * plotWidth;
    const y = (information) => margins.top + plotHeight * (
      1 - (logMaximum > 0 ? Math.log1p(Math.max(0, information)) / logMaximum : 0)
    );

    context.strokeStyle = "rgba(132,144,138,.12)";
    context.lineWidth = 1;
    for (let row = 0; row <= 3; row += 1) {
      const lineY = margins.top + (row / 3) * plotHeight;
      context.beginPath();
      context.moveTo(margins.left, lineY);
      context.lineTo(margins.left + plotWidth, lineY);
      context.stroke();
    }

    let connected = false;
    context.strokeStyle = "#70d8cc";
    context.lineWidth = 1.4;
    context.beginPath();
    events.forEach((event, index) => {
      if (!event.success) {
        connected = false;
        return;
      }
      const pointX = x(index);
      const pointY = y(finiteNumber(event.information));
      if (connected) context.lineTo(pointX, pointY);
      else context.moveTo(pointX, pointY);
      connected = true;
    });
    context.stroke();

    events.forEach((event, index) => {
      const pointX = x(index);
      if (!event.success) {
        const pointY = margins.top + plotHeight - 3;
        context.strokeStyle = "#ff7770";
        context.lineWidth = 1.4;
        context.beginPath();
        context.moveTo(pointX - 2.5, pointY - 2.5);
        context.lineTo(pointX + 2.5, pointY + 2.5);
        context.moveTo(pointX + 2.5, pointY - 2.5);
        context.lineTo(pointX - 2.5, pointY + 2.5);
        context.stroke();
        return;
      }
      context.fillStyle = event.kind === "loop" ? "#edf2ef" : "#bfe96b";
      context.beginPath();
      context.arc(pointX, y(finiteNumber(event.information)), event.kind === "loop" ? 2.2 : 1.6, 0, Math.PI * 2);
      context.fill();
    });

    context.fillStyle = "#58625d";
    context.font = "7px SFMono-Regular, Consolas, monospace";
    context.textAlign = "right";
    context.fillText(formatMetric(maximum), margins.left - 5, margins.top + 3);
    context.fillText("0", margins.left - 5, margins.top + plotHeight);
    context.textAlign = "left";
    context.fillText(events.length ? `最近 ${events.length} 组` : "0 组", margins.left, surface.height - 5);
    if (!events.length) {
      context.fillStyle = "#84908a";
      context.font = "9px Inter, system-ui, sans-serif";
      context.textAlign = "center";
      context.fillText("等待第一组帧对完成", margins.left + plotWidth / 2, margins.top + plotHeight / 2);
    }
  }

  render() {
    this.renderHeatmap();
    this.renderTrend();
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
  let faceCount = 0;
  const properties = [];
  const faceProperties = [];
  for (const raw of lines) {
    const parts = raw.trim().split(/\s+/);
    if (parts[0] === "format") format = parts[1];
    if (parts[0] === "element") {
      currentElement = parts[1];
      if (currentElement === "vertex") vertexCount = Number(parts[2]);
      if (currentElement === "face") faceCount = Number(parts[2]);
    }
    if (parts[0] === "property" && currentElement === "vertex") {
      if (parts[1] === "list") throw new Error("不支持顶点中的 PLY list 属性");
      const type = parts[1].toLowerCase();
      if (!plyTypes[type]) throw new Error(`不支持的 PLY 属性类型：${type}`);
      properties.push({ type, name: parts[2] });
    }
    if (parts[0] === "property" && currentElement === "face") {
      if (parts[1] === "list") {
        const countType = parts[2].toLowerCase();
        const itemType = parts[3].toLowerCase();
        if (!plyTypes[countType] || !plyTypes[itemType]) {
          throw new Error("网格包含不支持的 PLY 索引类型");
        }
        faceProperties.push({
          list: true,
          countType,
          itemType,
          name: parts[4],
        });
      } else {
        const type = parts[1].toLowerCase();
        if (!plyTypes[type]) throw new Error(`不支持的 PLY 面属性类型：${type}`);
        faceProperties.push({ list: false, type, name: parts[2] });
      }
    }
  }
  if (
    !format
    || !Number.isSafeInteger(vertexCount)
    || vertexCount <= 0
    || !Number.isSafeInteger(faceCount)
    || faceCount < 0
  ) {
    throw new Error("PLY 文件没有有效顶点数据");
  }
  for (const required of ["x", "y", "z"]) {
    if (!properties.some((property) => property.name === required)) {
      throw new Error(`PLY 顶点缺少 ${required} 属性`);
    }
  }
  if (
    faceCount > 0
    && !faceProperties.some((property) => (
      property.list && ["vertex_indices", "vertex_index"].includes(property.name)
    ))
  ) {
    throw new Error("PLY 网格缺少顶点索引");
  }
  return {
    format,
    vertexCount,
    faceCount,
    properties,
    faceProperties,
    dataOffset,
  };
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
  const hasFaces = info.faceCount > 0;
  const meshPositions = hasFaces ? new Float32Array(info.vertexCount * 3) : null;
  const meshColors = hasFaces ? new Uint8Array(info.vertexCount * 3) : null;
  const hasNormals = Boolean(byName.nx && byName.ny && byName.nz);
  const meshNormals = hasFaces && hasNormals
    ? new Float32Array(info.vertexCount * 3)
    : null;
  let output = 0;
  const read = (base, property) => view[property.getter](base + property.offset, littleEndian);
  const red = byName.red || byName.r;
  const green = byName.green || byName.g;
  const blue = byName.blue || byName.b;
  const readVertex = (index, targetPositions, targetColors, targetIndex, targetNormals = null) => {
    const base = info.dataOffset + index * stride;
    const offset = targetIndex * 3;
    targetPositions[offset] = read(base, byName.x);
    targetPositions[offset + 1] = read(base, byName.y);
    targetPositions[offset + 2] = read(base, byName.z);
    targetColors[offset] = red ? normalizedColor(read(base, red), red.type) : 199;
    targetColors[offset + 1] = green ? normalizedColor(read(base, green), green.type) : 243;
    targetColors[offset + 2] = blue ? normalizedColor(read(base, blue), blue.type) : 107;
    if (targetNormals) {
      targetNormals[offset] = read(base, byName.nx);
      targetNormals[offset + 1] = read(base, byName.ny);
      targetNormals[offset + 2] = read(base, byName.nz);
    }
  };
  if (hasFaces) {
    for (let index = 0; index < info.vertexCount; index += 1) {
      readVertex(index, meshPositions, meshColors, index, meshNormals);
      if (index % step === 0) {
        const sourceOffset = index * 3;
        positions.set(meshPositions.subarray(sourceOffset, sourceOffset + 3), output * 3);
        colors.set(meshColors.subarray(sourceOffset, sourceOffset + 3), output * 3);
        output += 1;
      }
    }
  } else {
    for (let index = 0; index < info.vertexCount; index += step) {
      readVertex(index, positions, colors, output);
      output += 1;
    }
  }

  let indices = null;
  if (hasFaces) {
    if (info.faceCount > MAX_MESH_PREVIEW_FACES) {
      throw new Error("网格预览面数过多，请刷新页面以生成轻量预览");
    }
    indices = new Uint32Array(info.faceCount * 3);
    let faceOffset = needed;
    const readFaceValue = (type) => {
      const [size, getter] = plyTypes[type];
      if (faceOffset + size > buffer.byteLength) throw new Error("PLY 面数据不完整");
      const value = view[getter](faceOffset, littleEndian);
      faceOffset += size;
      return value;
    };
    for (let face = 0; face < info.faceCount; face += 1) {
      let faceVertexCount = 0;
      const targetOffset = face * 3;
      for (const property of info.faceProperties) {
        if (!property.list) {
          readFaceValue(property.type);
          continue;
        }
        const count = readFaceValue(property.countType);
        if (!Number.isSafeInteger(count) || count < 0 || count > 1024) {
          throw new Error("PLY 面索引数量无效");
        }
        const isVertexList = ["vertex_indices", "vertex_index"].includes(property.name);
        if (isVertexList) faceVertexCount = count;
        for (let item = 0; item < count; item += 1) {
          const value = readFaceValue(property.itemType);
          if (isVertexList && item < 3) indices[targetOffset + item] = value;
        }
      }
      if (faceVertexCount !== 3) {
        throw new Error("网格预览目前仅支持三角面");
      }
      for (let offset = 0; offset < 3; offset += 1) {
        const vertexIndex = indices[targetOffset + offset];
        if (!Number.isSafeInteger(vertexIndex) || vertexIndex < 0 || vertexIndex >= info.vertexCount) {
          throw new Error("PLY 三角面包含无效顶点索引");
        }
      }
    }
  }
  return {
    positions,
    colors,
    meshPositions,
    meshColors,
    meshNormals,
    indices,
    sourceCount: info.vertexCount,
    faceCount: info.faceCount,
  };
}

function parseAsciiVertices(buffer, info) {
  const text = new TextDecoder("utf-8").decode(new Uint8Array(buffer, info.dataOffset));
  let cursor = 0;
  const nextToken = () => {
    while (cursor < text.length && /\s/.test(text[cursor])) cursor += 1;
    const start = cursor;
    while (cursor < text.length && !/\s/.test(text[cursor])) cursor += 1;
    if (start === cursor) throw new Error("ASCII PLY 数据不完整");
    return text.slice(start, cursor);
  };
  const maxPoints = 50000;
  const step = Math.max(1, Math.ceil(info.vertexCount / maxPoints));
  const sampledCount = Math.ceil(info.vertexCount / step);
  const positions = new Float32Array(sampledCount * 3);
  const colors = new Uint8Array(sampledCount * 3);
  const hasFaces = info.faceCount > 0;
  const meshPositions = hasFaces ? new Float32Array(info.vertexCount * 3) : null;
  const meshColors = hasFaces ? new Uint8Array(info.vertexCount * 3) : null;
  const propertyIndex = Object.fromEntries(info.properties.map((property, index) => [property.name, index]));
  const hasNormals = ["nx", "ny", "nz"].every((name) => propertyIndex[name] !== undefined);
  const meshNormals = hasFaces && hasNormals
    ? new Float32Array(info.vertexCount * 3)
    : null;
  let output = 0;
  for (let index = 0; index < info.vertexCount; index += 1) {
    const values = info.properties.map(() => Number(nextToken()));
    const color = (name, fallback) => {
      const propertyPosition = propertyIndex[name];
      if (propertyPosition === undefined) return fallback;
      return normalizedColor(values[propertyPosition], info.properties[propertyPosition].type);
    };
    const vertex = [
      values[propertyIndex.x],
      values[propertyIndex.y],
      values[propertyIndex.z],
    ];
    const vertexColor = [
      color("red", color("r", 199)),
      color("green", color("g", 243)),
      color("blue", color("b", 107)),
    ];
    if (hasFaces) {
      meshPositions.set(vertex, index * 3);
      meshColors.set(vertexColor, index * 3);
      if (meshNormals) {
        meshNormals[index * 3] = values[propertyIndex.nx];
        meshNormals[index * 3 + 1] = values[propertyIndex.ny];
        meshNormals[index * 3 + 2] = values[propertyIndex.nz];
      }
    }
    if (index % step === 0) {
      positions.set(vertex, output * 3);
      colors.set(vertexColor, output * 3);
      output += 1;
    }
  }

  let indices = null;
  if (hasFaces) {
    if (info.faceCount > MAX_MESH_PREVIEW_FACES) {
      throw new Error("网格预览面数过多，请刷新页面以生成轻量预览");
    }
    indices = new Uint32Array(info.faceCount * 3);
    for (let face = 0; face < info.faceCount; face += 1) {
      let faceVertexCount = 0;
      const targetOffset = face * 3;
      for (const property of info.faceProperties) {
        if (!property.list) {
          nextToken();
          continue;
        }
        const count = Number(nextToken());
        if (!Number.isSafeInteger(count) || count < 0 || count > 1024) {
          throw new Error("PLY 面索引数量无效");
        }
        const isVertexList = ["vertex_indices", "vertex_index"].includes(property.name);
        if (isVertexList) faceVertexCount = count;
        for (let item = 0; item < count; item += 1) {
          const value = Number(nextToken());
          if (isVertexList && item < 3) indices[targetOffset + item] = value;
        }
      }
      if (faceVertexCount !== 3) {
        throw new Error("网格预览目前仅支持三角面");
      }
      for (let offset = 0; offset < 3; offset += 1) {
        const vertexIndex = indices[targetOffset + offset];
        if (!Number.isSafeInteger(vertexIndex) || vertexIndex < 0 || vertexIndex >= info.vertexCount) {
          throw new Error("PLY 三角面包含无效顶点索引");
        }
      }
    }
  }
  return {
    positions,
    colors,
    meshPositions,
    meshColors,
    meshNormals,
    indices,
    sourceCount: info.vertexCount,
    faceCount: info.faceCount,
  };
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

class ViewerOrientation {
  constructor(canvas) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d");
  }

  rotate(point, yaw, pitch) {
    const [x, y, z] = point;
    const cosY = Math.cos(yaw); const sinY = Math.sin(yaw);
    const cosP = Math.cos(pitch); const sinP = Math.sin(pitch);
    const x1 = cosY * x + sinY * z;
    const z1 = -sinY * x + cosY * z;
    return [x1, cosP * y - sinP * z1, sinP * y + cosP * z1];
  }

  render(yaw, pitch) {
    const bounds = this.canvas.getBoundingClientRect();
    if (bounds.width < 2 || bounds.height < 2) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(bounds.width * dpr));
    const height = Math.max(1, Math.round(bounds.height * dpr));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }
    const context = this.context;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, bounds.width, bounds.height);
    const center = { x: bounds.width / 2, y: bounds.height / 2 };
    const radius = Math.min(bounds.width, bounds.height) * 0.39;
    const project = (point) => {
      const rotated = this.rotate(point, yaw, pitch);
      return {
        x: center.x + rotated[0] * radius,
        y: center.y - rotated[1] * radius,
        z: rotated[2],
      };
    };

    const fill = context.createRadialGradient(
      center.x - radius * 0.28,
      center.y - radius * 0.32,
      radius * 0.08,
      center.x,
      center.y,
      radius,
    );
    fill.addColorStop(0, "rgba(43,58,55,.82)");
    fill.addColorStop(0.72, "rgba(13,18,19,.88)");
    fill.addColorStop(1, "rgba(5,8,9,.94)");
    context.fillStyle = fill;
    context.beginPath();
    context.arc(center.x, center.y, radius, 0, Math.PI * 2);
    context.fill();

    context.save();
    context.beginPath();
    context.arc(center.x, center.y, radius, 0, Math.PI * 2);
    context.clip();
    const circles = [
      { color: "112,216,204", point: (angle) => [Math.cos(angle), Math.sin(angle), 0] },
      { color: "191,233,107", point: (angle) => [Math.cos(angle), 0, Math.sin(angle)] },
      { color: "255,119,112", point: (angle) => [0, Math.cos(angle), Math.sin(angle)] },
    ];
    const drawHemisphere = (circle, front) => {
      context.strokeStyle = `rgba(${circle.color},${front ? 0.34 : 0.15})`;
      context.lineWidth = front ? 0.9 : 0.7;
      context.setLineDash(front ? [] : [2, 2]);
      context.beginPath();
      let drawing = false;
      for (let index = 0; index <= 72; index += 1) {
        const rotated = project(circle.point((index / 72) * Math.PI * 2));
        const visible = front ? rotated.z >= 0 : rotated.z < 0;
        if (!visible) {
          drawing = false;
        } else if (drawing) {
          context.lineTo(rotated.x, rotated.y);
        } else {
          context.moveTo(rotated.x, rotated.y);
          drawing = true;
        }
      }
      context.stroke();
    };
    for (const circle of circles) drawHemisphere(circle, false);
    for (const circle of circles) drawHemisphere(circle, true);
    context.restore();
    context.setLineDash([]);

    context.strokeStyle = "rgba(224,237,231,.32)";
    context.lineWidth = 1;
    context.beginPath();
    context.arc(center.x, center.y, radius, 0, Math.PI * 2);
    context.stroke();

    const axes = [
      { point: [1, 0, 0], color: "#ff7770", label: "X" },
      { point: [0, 1, 0], color: "#bfe96b", label: "Y" },
      { point: [0, 0, 1], color: "#70d8cc", label: "Z" },
    ].map((axis) => ({
      ...axis,
      positive: project(axis.point),
      negative: project(axis.point.map((value) => -value)),
    })).sort((left, right) => left.positive.z - right.positive.z);

    for (const axis of axes) {
      context.strokeStyle = `${axis.color}55`;
      context.setLineDash([2, 2]);
      context.beginPath();
      context.moveTo(axis.negative.x, axis.negative.y);
      context.lineTo(center.x, center.y);
      context.stroke();
      context.setLineDash([]);
      context.strokeStyle = axis.color;
      context.fillStyle = axis.color;
      context.lineWidth = 1.7;
      context.beginPath();
      context.moveTo(center.x, center.y);
      context.lineTo(axis.positive.x, axis.positive.y);
      context.stroke();
      const dx = axis.positive.x - center.x;
      const dy = axis.positive.y - center.y;
      const length = Math.hypot(dx, dy);
      if (length > 4) {
        const ux = dx / length; const uy = dy / length;
        context.beginPath();
        context.moveTo(axis.positive.x, axis.positive.y);
        context.lineTo(axis.positive.x - ux * 5 - uy * 2.6, axis.positive.y - uy * 5 + ux * 2.6);
        context.lineTo(axis.positive.x - ux * 5 + uy * 2.6, axis.positive.y - uy * 5 - ux * 2.6);
        context.closePath();
        context.fill();
      } else {
        context.beginPath();
        context.arc(center.x, center.y, axis.positive.z >= 0 ? 3 : 2.2, 0, Math.PI * 2);
        axis.positive.z >= 0 ? context.fill() : context.stroke();
      }
      context.font = "700 8px SFMono-Regular, Consolas, monospace";
      const labelX = axis.positive.x + (length > 4 ? dx / length * 5 : 5);
      const labelY = axis.positive.y + (length > 4 ? dy / length * 5 : -4);
      context.fillText(axis.label, labelX - 2, labelY + 3);
    }
    context.setLineDash([]);
    context.fillStyle = "#edf2ef";
    context.beginPath();
    context.arc(center.x, center.y, 1.7, 0, Math.PI * 2);
    context.fill();
  }
}

class MeshRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.gl = canvas.getContext("webgl2", {
      alpha: true,
      antialias: true,
      premultipliedAlpha: false,
    });
    this.available = Boolean(this.gl);
    this.indexCount = 0;
    this.surfaceMode = 0;
    this.error = null;
    if (!this.available) {
      this.error = "当前浏览器不支持 WebGL 2";
      return;
    }
    try {
      this.initialize();
    } catch (error) {
      this.available = false;
      this.error = error.message;
    }
  }

  compile(type, source) {
    const gl = this.gl;
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const message = gl.getShaderInfoLog(shader) || "着色器编译失败";
      gl.deleteShader(shader);
      throw new Error(message);
    }
    return shader;
  }

  initialize() {
    const gl = this.gl;
    const vertexShader = this.compile(gl.VERTEX_SHADER, `#version 300 es
      precision highp float;
      layout(location = 0) in vec3 a_position;
      layout(location = 1) in vec3 a_normal;
      layout(location = 2) in vec3 a_color;
      uniform float u_yaw;
      uniform float u_pitch;
      uniform float u_zoom;
      uniform float u_aspect;
      uniform vec2 u_pan;
      out vec3 v_normal;
      out vec3 v_linear_color;
      out vec3 v_view_position;

      vec3 rotate_model(vec3 point) {
        float cy = cos(u_yaw);
        float sy = sin(u_yaw);
        float cp = cos(u_pitch);
        float sp = sin(u_pitch);
        float x = cy * point.x + sy * point.z;
        float z = -sy * point.x + cy * point.z;
        return vec3(x, cp * point.y - sp * z, sp * point.y + cp * z);
      }

      void main() {
        vec3 point = rotate_model(a_position);
        float depth = 2.35 - point.z;
        float near_plane = 0.1;
        float far_plane = 10.0;
        float projection_a = (far_plane + near_plane) / (far_plane - near_plane);
        float projection_b = (-2.0 * far_plane * near_plane) / (far_plane - near_plane);
        float scale = 3.1 * u_zoom * min(1.0, u_aspect);
        gl_Position = vec4(
          point.x * scale / u_aspect,
          point.y * scale,
          projection_a * depth + projection_b,
          depth
        );
        gl_Position.xy += u_pan * depth;
        v_normal = rotate_model(a_normal);
        v_linear_color = pow(max(a_color, vec3(0.001)), vec3(2.2));
        v_view_position = point;
      }
    `);
    const fragmentShader = this.compile(gl.FRAGMENT_SHADER, `#version 300 es
      precision highp float;
      in vec3 v_normal;
      in vec3 v_linear_color;
      in vec3 v_view_position;
      uniform float u_surface_mode;
      out vec4 output_color;

      void main() {
        vec3 normal = normalize(v_normal);
        if (!gl_FrontFacing) normal = -normal;
        vec3 key_light = normalize(vec3(-0.42, 0.72, 0.56));
        vec3 fill_light = normalize(vec3(0.55, -0.18, 0.82));
        vec3 view_direction = normalize(vec3(0.0, 0.0, 2.35) - v_view_position);
        vec3 half_direction = normalize(key_light + view_direction);
        float diffuse = max(dot(normal, key_light), 0.0);
        float fill = max(dot(normal, fill_light), 0.0);
        float specular = pow(max(dot(normal, half_direction), 0.0), 32.0);
        float rim = pow(1.0 - max(dot(normal, view_direction), 0.0), 2.4);
        vec3 neutral = vec3(0.43, 0.49, 0.47);
        vec3 base_color = mix(v_linear_color, neutral, u_surface_mode);
        vec3 lit = base_color * (0.28 + 0.82 * diffuse + 0.18 * fill);
        lit += vec3(0.12 * specular + 0.07 * rim);
        vec3 mapped = lit / (lit + vec3(0.82));
        output_color = vec4(pow(max(mapped, vec3(0.0)), vec3(1.0 / 2.2)), 1.0);
      }
    `);
    this.program = gl.createProgram();
    gl.attachShader(this.program, vertexShader);
    gl.attachShader(this.program, fragmentShader);
    gl.linkProgram(this.program);
    gl.deleteShader(vertexShader);
    gl.deleteShader(fragmentShader);
    if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) {
      throw new Error(gl.getProgramInfoLog(this.program) || "网格渲染程序链接失败");
    }
    this.uniforms = {
      yaw: gl.getUniformLocation(this.program, "u_yaw"),
      pitch: gl.getUniformLocation(this.program, "u_pitch"),
      zoom: gl.getUniformLocation(this.program, "u_zoom"),
      aspect: gl.getUniformLocation(this.program, "u_aspect"),
      pan: gl.getUniformLocation(this.program, "u_pan"),
      surfaceMode: gl.getUniformLocation(this.program, "u_surface_mode"),
    };
    this.vao = gl.createVertexArray();
    this.buffers = [];
  }

  clear() {
    this.indexCount = 0;
    if (this.available) this.render(-0.55, -0.28, 1);
  }

  setSurfaceMode(mode) {
    this.surfaceMode = mode === "solid" ? 1 : 0;
  }

  prepareNormals(positions, normals, indices) {
    const prepared = new Float32Array(positions.length);
    let useProvided = Boolean(normals && normals.length === positions.length);
    if (useProvided) {
      for (let offset = 0; offset < normals.length; offset += 3) {
        const length = Math.hypot(normals[offset], normals[offset + 1], normals[offset + 2]);
        if (!Number.isFinite(length) || length < 1e-8) {
          useProvided = false;
          break;
        }
        prepared[offset] = normals[offset] / length;
        prepared[offset + 1] = normals[offset + 1] / length;
        prepared[offset + 2] = normals[offset + 2] / length;
      }
    }
    if (useProvided) return prepared;

    prepared.fill(0);
    for (let index = 0; index < indices.length; index += 3) {
      const ia = indices[index] * 3;
      const ib = indices[index + 1] * 3;
      const ic = indices[index + 2] * 3;
      const abx = positions[ib] - positions[ia];
      const aby = positions[ib + 1] - positions[ia + 1];
      const abz = positions[ib + 2] - positions[ia + 2];
      const acx = positions[ic] - positions[ia];
      const acy = positions[ic + 1] - positions[ia + 1];
      const acz = positions[ic + 2] - positions[ia + 2];
      const nx = aby * acz - abz * acy;
      const ny = abz * acx - abx * acz;
      const nz = abx * acy - aby * acx;
      if (!Number.isFinite(nx + ny + nz)) continue;
      for (const offset of [ia, ib, ic]) {
        prepared[offset] += nx;
        prepared[offset + 1] += ny;
        prepared[offset + 2] += nz;
      }
    }
    for (let offset = 0; offset < prepared.length; offset += 3) {
      const length = Math.hypot(prepared[offset], prepared[offset + 1], prepared[offset + 2]);
      if (!Number.isFinite(length) || length < 1e-8) {
        prepared[offset] = 0;
        prepared[offset + 1] = 0;
        prepared[offset + 2] = 1;
        continue;
      }
      prepared[offset] /= length;
      prepared[offset + 1] /= length;
      prepared[offset + 2] /= length;
    }
    return prepared;
  }

  setModel(positions, normals, colors, indices) {
    if (!this.available) throw new Error(this.error || "WebGL 2 不可用");
    if (!positions.length || !indices.length || indices.length % 3 !== 0) {
      throw new Error("网格没有有效三角面");
    }
    const preparedNormals = this.prepareNormals(positions, normals, indices);

    const gl = this.gl;
    for (const buffer of this.buffers) gl.deleteBuffer(buffer);
    this.buffers = [];
    gl.bindVertexArray(this.vao);
    const upload = (location, values, size, type, normalized = false) => {
      const buffer = gl.createBuffer();
      this.buffers.push(buffer);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, values, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, size, type, normalized, 0, 0);
    };
    upload(0, positions, 3, gl.FLOAT);
    upload(1, preparedNormals, 3, gl.FLOAT);
    upload(2, colors, 3, gl.UNSIGNED_BYTE, true);
    const indexBuffer = gl.createBuffer();
    this.buffers.push(indexBuffer);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, indexBuffer);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
    gl.bindVertexArray(null);
    this.indexCount = indices.length;
  }

  render(yaw, pitch, zoom, panX = 0, panY = 0) {
    if (!this.available) return;
    const gl = this.gl;
    const bounds = this.canvas.parentElement.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.round(bounds.width * dpr));
    const height = Math.max(1, Math.round(bounds.height * dpr));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }
    gl.viewport(0, 0, width, height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    if (!this.indexCount) return;
    gl.enable(gl.DEPTH_TEST);
    gl.depthFunc(gl.LEQUAL);
    gl.disable(gl.CULL_FACE);
    gl.useProgram(this.program);
    gl.uniform1f(this.uniforms.yaw, yaw);
    gl.uniform1f(this.uniforms.pitch, pitch);
    gl.uniform1f(this.uniforms.zoom, zoom);
    gl.uniform1f(this.uniforms.aspect, width / height);
    gl.uniform2f(this.uniforms.pan, panX * 2, panY * -2);
    gl.uniform1f(this.uniforms.surfaceMode, this.surfaceMode);
    gl.bindVertexArray(this.vao);
    gl.drawElements(gl.TRIANGLES, this.indexCount, gl.UNSIGNED_INT, 0);
    gl.bindVertexArray(null);
  }
}

class PointViewer {
  constructor(canvas, message, orientationCanvas, options = {}) {
    this.canvas = canvas;
    this.message = message;
    this.context = canvas.getContext("2d", { alpha: true });
    this.orientation = new ViewerOrientation(orientationCanvas);
    this.meshCanvas = options.meshCanvas || null;
    this.meshRenderer = this.meshCanvas ? new MeshRenderer(this.meshCanvas) : null;
    this.styleButtons = options.styleButtons || [];
    this.navigationButtons = options.navigationButtons || [];
    this.scaleElement = options.scaleElement || null;
    this.scaleBar = options.scaleBar || null;
    this.scaleValue = options.scaleValue || null;
    this.boundsValue = options.boundsValue || null;
    this.defaultStyle = options.defaultStyle || "points";
    this.loadingLabel = options.loadingLabel || "正在载入模型预览…";
    this.style = "points";
    this.navigationMode = "rotate";
    this.positions = null;
    this.colors = null;
    this.modelExtent = null;
    this.modelDimensions = null;
    this.meshAvailable = false;
    this.yaw = -0.55;
    this.pitch = -0.28;
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this.dragging = false;
    this.dragMode = null;
    this.lastX = 0;
    this.lastY = 0;
    this.loadToken = 0;
    this.bind();
    this.updateStyleControls();
    this.updateNavigationControls();
    new ResizeObserver(() => this.render()).observe(canvas.parentElement);
  }

  bind() {
    const surfaces = [this.canvas, this.meshCanvas].filter(Boolean);
    for (const surface of surfaces) {
      surface.addEventListener("pointerdown", (event) => {
        if (![0, 1, 2].includes(event.button)) return;
        event.preventDefault();
        this.dragging = true;
        this.dragMode = (
          this.navigationMode === "pan"
          || event.shiftKey
          || event.button === 1
          || event.button === 2
        ) ? "pan" : "rotate";
        this.lastX = event.clientX;
        this.lastY = event.clientY;
        surface.setPointerCapture(event.pointerId);
      });
      surface.addEventListener("pointermove", (event) => {
        if (!this.dragging) return;
        const deltaX = event.clientX - this.lastX;
        const deltaY = event.clientY - this.lastY;
        if (this.dragMode === "pan") {
          const bounds = surface.getBoundingClientRect();
          this.panX = Math.max(-1.5, Math.min(1.5, this.panX + deltaX / Math.max(1, bounds.width)));
          this.panY = Math.max(-1.5, Math.min(1.5, this.panY + deltaY / Math.max(1, bounds.height)));
        } else {
          this.yaw = wrapRadians(this.yaw + deltaX * 0.009);
          this.pitch = wrapRadians(this.pitch + deltaY * 0.009);
        }
        this.lastX = event.clientX;
        this.lastY = event.clientY;
        this.render();
      });
      const finishDrag = () => {
        this.dragging = false;
        this.dragMode = null;
      };
      surface.addEventListener("pointerup", finishDrag);
      surface.addEventListener("pointercancel", finishDrag);
      surface.addEventListener("lostpointercapture", finishDrag);
      surface.addEventListener("contextmenu", (event) => event.preventDefault());
      surface.addEventListener("wheel", (event) => {
        event.preventDefault();
        this.zoom = Math.max(0.35, Math.min(5, this.zoom * Math.exp(-event.deltaY * 0.001)));
        this.render();
      }, { passive: false });
    }
    for (const button of this.styleButtons) {
      button.addEventListener("click", () => this.setStyle(button.dataset.viewerStyle));
    }
    for (const button of this.navigationButtons) {
      button.addEventListener("click", () => {
        const action = button.dataset.viewerNavigation;
        if (action === "reset") this.resetView();
        else this.setNavigationMode(action);
      });
    }
  }

  setNavigationMode(mode) {
    this.navigationMode = mode === "pan" ? "pan" : "rotate";
    for (const surface of [this.canvas, this.meshCanvas].filter(Boolean)) {
      surface.classList.toggle("viewer-pan-mode", this.navigationMode === "pan");
    }
    this.updateNavigationControls();
  }

  updateNavigationControls() {
    for (const button of this.navigationButtons) {
      const action = button.dataset.viewerNavigation;
      if (action !== "reset") {
        button.setAttribute("aria-pressed", String(action === this.navigationMode));
      }
    }
  }

  resetView() {
    this.yaw = -0.55;
    this.pitch = -0.28;
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this.render();
  }

  updateStyleControls() {
    for (const button of this.styleButtons) {
      const needsMesh = ["mesh", "solid"].includes(button.dataset.viewerStyle);
      button.disabled = needsMesh && !this.meshAvailable;
      button.setAttribute("aria-pressed", String(button.dataset.viewerStyle === this.style));
      if (needsMesh && this.meshRenderer && !this.meshRenderer.available) {
        button.title = this.meshRenderer.error || "WebGL 2 不可用";
      }
    }
  }

  setStyle(style) {
    const requested = ["mesh", "solid"].includes(style) ? style : "points";
    if (requested !== "points" && !this.meshAvailable) return;
    this.style = requested;
    this.canvas.hidden = this.style !== "points";
    if (this.meshCanvas) this.meshCanvas.hidden = this.style === "points";
    if (this.meshRenderer) this.meshRenderer.setSurfaceMode(this.style);
    this.updateStyleControls();
    this.render();
  }

  async load(url) {
    const token = ++this.loadToken;
    this.message.hidden = false;
    this.message.textContent = this.loadingLabel;
    this.positions = null;
    this.modelExtent = null;
    this.modelDimensions = null;
    if (this.scaleElement) this.scaleElement.hidden = true;
    this.meshAvailable = false;
    if (this.meshRenderer) this.meshRenderer.clear();
    this.setStyle("points");
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
    const boundsSource = model.meshPositions || model.positions;
    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    for (let index = 0; index < boundsSource.length; index += 3) {
      const x = boundsSource[index], y = boundsSource[index + 1], z = boundsSource[index + 2];
      if (!Number.isFinite(x + y + z)) continue;
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
      minZ = Math.min(minZ, z); maxZ = Math.max(maxZ, z);
    }
    const extent = Math.max(maxX - minX, maxY - minY, maxZ - minZ);
    if (!Number.isFinite(extent) || extent <= 0) throw new Error("模型顶点范围无效");
    this.modelExtent = extent;
    this.modelDimensions = [maxX - minX, maxY - minY, maxZ - minZ];
    if (this.scaleElement) this.scaleElement.hidden = false;
    const centerX = (minX + maxX) / 2;
    const centerY = (minY + maxY) / 2;
    const centerZ = (minZ + maxZ) / 2;
    const normalize = (source) => {
      const normalized = new Float32Array(source.length);
      for (let index = 0; index < source.length; index += 3) {
        const x = source[index], y = source[index + 1], z = source[index + 2];
        normalized[index] = Number.isFinite(x) ? (x - centerX) / extent : 0;
        normalized[index + 1] = Number.isFinite(y) ? (y - centerY) / extent : 0;
        normalized[index + 2] = Number.isFinite(z) ? (z - centerZ) / extent : 0;
      }
      return normalized;
    };
    this.positions = normalize(model.positions);
    this.colors = model.colors;
    this.meshAvailable = false;
    if (
      this.meshRenderer
      && this.meshRenderer.available
      && model.meshPositions
      && model.meshColors
      && model.indices
      && model.indices.length
    ) {
      this.meshRenderer.setModel(
        normalize(model.meshPositions),
        model.meshNormals,
        model.meshColors,
        model.indices,
      );
      this.meshAvailable = true;
      for (const button of this.styleButtons) {
        if (["mesh", "solid"].includes(button.dataset.viewerStyle)) {
          button.title = `${model.faceCount.toLocaleString()} 个三角面 · 高清索引渲染`;
        }
      }
    }
    this.yaw = -0.55;
    this.pitch = -0.28;
    this.zoom = 1;
    this.panX = 0;
    this.panY = 0;
    this.setStyle(this.defaultStyle === "mesh" && this.meshAvailable ? "mesh" : "points");
  }

  updateScale() {
    if (!this.scaleElement || !this.scaleBar || !this.scaleValue || !this.boundsValue) return;
    if (!Number.isFinite(this.modelExtent) || this.modelExtent <= 0 || !this.modelDimensions) {
      this.scaleElement.hidden = true;
      return;
    }
    const bounds = this.canvas.parentElement.getBoundingClientRect();
    const referencePixels = Math.min(bounds.width, bounds.height);
    const pixelsPerMeter = (
      referencePixels * 1.55 * this.zoom / (2.35 * this.modelExtent)
    );
    const targetPixels = Math.max(72, Math.min(120, bounds.width * 0.14));
    const scaleMeters = niceScaleLength(targetPixels / pixelsPerMeter);
    if (!scaleMeters || !Number.isFinite(pixelsPerMeter) || pixelsPerMeter <= 0) {
      this.scaleElement.hidden = true;
      return;
    }
    const label = formatSceneLength(scaleMeters);
    const dimensions = formatSceneDimensions(this.modelDimensions);
    this.scaleBar.style.width = `${Math.max(1, Math.round(scaleMeters * pixelsPerMeter))}px`;
    this.scaleValue.textContent = label;
    this.boundsValue.textContent = dimensions;
    const description = `中心平面比例尺 ${label}；模型包围盒 ${dimensions}`;
    this.scaleElement.setAttribute("aria-label", description);
    this.scaleElement.title = `${description}。透视视图中，前后位置的屏幕比例会略有不同。`;
    this.scaleElement.hidden = false;
  }

  render() {
    this.orientation.render(this.yaw, this.pitch);
    this.updateScale();
    if (this.style !== "points" && this.meshAvailable) {
      this.meshRenderer.render(this.yaw, this.pitch, this.zoom, this.panX, this.panY);
      return;
    }
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
    const centerX = width * (0.5 + this.panX);
    const centerY = height * (0.5 + this.panY);
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

const imuOrientation = new ImuOrientation(elements.imuOrientation);
const liveFrameBuffer = new LiveFrameBuffer(
  [elements.liveRgb, elements.liveRgbBuffer],
  [elements.liveDepth, elements.liveDepthBuffer],
  elements.rgbPlaceholder,
  elements.depthPlaceholder,
);
const processViewer = new PointViewer(
  elements.processViewer,
  elements.processViewerMessage,
  elements.processViewerOrientation,
);
const matchingDiagnostics = new MatchingDiagnostics(
  elements.matchingHeatmap,
  elements.matchingTrend,
);
const modelViewer = new PointViewer(
  elements.viewer,
  elements.viewerMessage,
  elements.viewerOrientation,
  {
    meshCanvas: elements.meshViewer,
    styleButtons: elements.viewerStyleButtons,
    navigationButtons: elements.viewerNavigationButtons,
    scaleElement: elements.viewerScale,
    scaleBar: elements.viewerScaleBar,
    scaleValue: elements.viewerScaleValue,
    boundsValue: elements.viewerBounds,
    defaultStyle: "mesh",
    loadingLabel: "正在准备高清网格预览，首次加载可能稍久…",
  },
);

updateReconstructionSummary();

window.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    pollState();
    pollLive();
  }
});

fetchDevices(false);
pollState();
setInterval(pollState, 1000);
setInterval(pollLive, 40);
