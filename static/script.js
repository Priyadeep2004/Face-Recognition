/**
 * FaceID Pro — Dashboard Script
 * Handles camera, face recognition, attendance log updates
 */

// ─── DOM References ─────────────────────────────────────────────────
// NOTE: 'captureCanvas' is used (not 'canvas') to avoid conflict with
// the particle canvas declared as 'const canvas' in base.html inline script.
const video          = document.getElementById('videoElement');
const startButton    = document.getElementById('startButton');
const stopButton     = document.getElementById('stopButton');
const captureButton  = document.getElementById('captureButton');
const captureCanvas  = document.getElementById('canvasElement');   // renamed
const flashEl        = document.getElementById('flashEffect');
const statusBar      = document.getElementById('statusBar');
const statusText     = document.getElementById('statusText');
const statusIcon     = document.getElementById('statusIcon');
const statusSpinner  = document.getElementById('statusSpinner');
const statusDot      = document.getElementById('statusDot');
const placeholder    = document.getElementById('cameraPlaceholder');
const logBody        = document.getElementById('attendanceLogBody');
const processSpin    = document.getElementById('processingSpinner');
const recBadge       = document.getElementById('recognitionBadge');
const recName        = document.getElementById('recognitionName');

// Stats
const todayCount    = document.getElementById('todayCount');
const totalCount    = document.getElementById('totalCount');
const lastTimestamp = document.getElementById('lastTimestamp');

let stream = null;

// ─── Toast Helper (visible even if statusBar is behind overlay) ──────
function showToast(message, type = 'info') {
  const container = document.getElementById('toastContainer')
    || (() => {
      const c = document.createElement('div');
      c.id = 'toastContainer';
      c.className = 'toast-container';
      document.body.appendChild(c);
      return c;
    })();

  const icons = { success: 'fa-circle-check', error: 'fa-circle-xmark', info: 'fa-circle-info' };
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.innerHTML = `
    <div class="toast-icon"><i class="fas ${icons[type] || icons.info}"></i></div>
    <span class="toast-message">${message}</span>
    <button class="toast-close" onclick="this.parentElement.remove()"><i class="fas fa-xmark"></i></button>
  `;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = 'slideOutRight 0.4s ease forwards';
    setTimeout(() => toast.remove(), 400);
  }, 5000);
}

// ─── Status Bar Helper ───────────────────────────────────────────────
function setStatus(message, type = 'info', spinning = false) {
  if (!statusBar) return;
  statusBar.className = `status-bar visible ${type}`;
  if (statusText) statusText.textContent = message;
  const iconMap = { success: 'fas fa-circle-check', error: 'fas fa-circle-xmark', info: 'fas fa-circle-info' };
  if (statusIcon) {
    statusIcon.className = iconMap[type] || iconMap.info;
    statusIcon.style.display = spinning ? 'none' : 'inline';
  }
  if (statusSpinner) statusSpinner.style.display = spinning ? 'inline-block' : 'none';
}

// ─── Camera Controls ─────────────────────────────────────────────────
if (startButton) {
  startButton.addEventListener('click', async () => {

    // Check browser support
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      showToast('Your browser does not support camera access. Please use Chrome or Firefox.', 'error');
      return;
    }

    startButton.disabled = true;
    startButton.innerHTML = '<div class="spinner" style="width:16px;height:16px;margin:0;"></div> Starting…';

    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' }
      });

      video.srcObject = stream;

      // Hide placeholder once video metadata is loaded
      video.onloadedmetadata = () => {
        if (placeholder) placeholder.style.display = 'none';
      };
      // Fallback: also hide after short delay
      setTimeout(() => { if (placeholder) placeholder.style.display = 'none'; }, 500);

      startButton.style.display   = 'none';
      captureButton.style.display = 'inline-flex';
      stopButton.style.display    = 'inline-flex';
      if (statusDot) statusDot.classList.add('active');

      // Enable capture after camera warms up
      captureButton.disabled = true;
      setTimeout(() => {
        captureButton.disabled = false;
        captureButton.title = '';
      }, 1500);

      setStatus('Camera ready — centre your face and click Capture & Mark', 'info');

    } catch (err) {
      console.error('Camera error:', err);
      startButton.disabled = false;
      startButton.innerHTML = '<i class="fas fa-play"></i> Start Camera';

      let msg = 'Could not access camera.';
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        msg = 'Camera permission denied. Click the camera icon in your browser address bar and allow access, then try again.';
      } else if (err.name === 'NotFoundError' || err.name === 'DevicesNotFoundError') {
        msg = 'No camera found. Please connect a webcam and try again.';
      } else if (err.name === 'NotReadableError') {
        msg = 'Camera is in use by another application. Close other apps using the camera and try again.';
      } else {
        msg = `Camera error: ${err.message}`;
      }
      showToast(msg, 'error');
      setStatus(msg, 'error');
    }
  });
}

if (stopButton) {
  stopButton.addEventListener('click', () => {
    if (stream) stream.getTracks().forEach(t => t.stop());
    video.srcObject = null;
    stream = null;

    if (placeholder) placeholder.style.display = 'flex';
    startButton.disabled = false;
    startButton.innerHTML = '<i class="fas fa-play"></i> Start Camera';
    startButton.style.display   = 'inline-flex';
    captureButton.style.display = 'none';
    stopButton.style.display    = 'none';
    captureButton.disabled = true;
    if (statusDot) statusDot.classList.remove('active');
    if (recBadge)  recBadge.style.display = 'none';
    setStatus('Camera stopped.', 'info');
    setTimeout(() => { if (statusBar) statusBar.classList.remove('visible'); }, 3000);
  });
}

// ─── Capture & Recognize ─────────────────────────────────────────────
if (captureButton) {
  captureButton.addEventListener('click', async () => {
    if (!stream) {
      showToast('Start the camera first.', 'error');
      return;
    }

    captureButton.disabled = true;
    captureButton.innerHTML = '<div class="spinner" style="width:14px;height:14px;margin:0;"></div> Processing…';
    if (processSpin) processSpin.style.display = 'flex';
    if (recBadge)    recBadge.style.display = 'none';

    // Flash
    if (flashEl) {
      flashEl.classList.add('active');
      setTimeout(() => flashEl.classList.remove('active'), 150);
    }

    // Draw frame to capture canvas
    captureCanvas.width  = video.videoWidth;
    captureCanvas.height = video.videoHeight;
    captureCanvas.getContext('2d').drawImage(video, 0, 0);
    const imageDataUrl = captureCanvas.toDataURL('image/jpeg', 0.92);

    setStatus('Processing face recognition…', 'info', true);

    try {
      const res = await fetch('/process_face_recognition', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: `image_data=${encodeURIComponent(imageDataUrl)}`
      });
      const data = await res.json();

      if (data.status === 'success' || data.status === 'info') {
        const isSuccess = data.status === 'success';
        setStatus(isSuccess ? `✓ ${data.message}` : data.message, isSuccess ? 'success' : 'info');
        if (isSuccess) showToast(data.message, 'success');
        else           showToast(data.message, 'info');

        // Show the recognition card
        showRecognitionCard(data.name, data.roll_number, data.timestamp);
        addLogEntry(data.name, data.roll_number, data.timestamp);
        if (isSuccess) loadStats();

      } else {
        setStatus(data.message, 'error');
        showToast(data.message, 'error');
      }

    } catch (err) {
      const msg = `Network error: ${err.message}`;
      setStatus(msg, 'error');
      showToast(msg, 'error');
    } finally {
      captureButton.disabled = false;
      captureButton.innerHTML = '<i class="fas fa-camera"></i> Capture & Mark';
      if (processSpin) processSpin.style.display = 'none';
      setTimeout(() => {
        if (statusBar && statusBar.classList.contains('success')) {
          statusBar.classList.remove('visible');
        }
      }, 6000);
    }
  });
}

// ─── Show Recognition Card ──────────────────────────────────────────
function showRecognitionCard(name, roll, timestamp) {
  const card    = document.getElementById('recognitionCard');
  const avatar  = document.getElementById('recognizedAvatar');
  const nameEl  = document.getElementById('recognizedName');
  const rollEl  = document.getElementById('recognizedRoll');
  const timeEl  = document.getElementById('recognizedTime');
  if (!card) return;

  if (avatar) avatar.textContent = name ? name[0].toUpperCase() : '?';
  if (nameEl) nameEl.textContent = name || 'Unknown';
  if (rollEl) rollEl.textContent = roll || 'N/A';
  if (timeEl) {
    const t = timestamp ? timestamp.split(' ')[1] || timestamp : new Date().toLocaleTimeString();
    timeEl.textContent = t;
  }

  card.style.display = 'block';
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

  // Auto-hide after 15 seconds
  clearTimeout(card._hideTimer);
  card._hideTimer = setTimeout(() => { card.style.display = 'none'; }, 15000);
}

// ─── Add Entry to Log ────────────────────────────────────────────────
function addLogEntry(name, roll, timestamp) {
  if (!logBody) return;
  const empty = logBody.querySelector('.log-empty');
  if (empty) empty.remove();

  const timeStr = timestamp
    ? (timestamp.split(' ')[1] || timestamp)
    : new Date().toLocaleTimeString();

  const entry = document.createElement('div');
  entry.className = 'log-entry';
  entry.innerHTML = `
    <div class="log-entry-dot"></div>
    <div class="log-entry-info">
      <div class="log-entry-name">${name || 'Unknown'}</div>
      <div class="log-entry-time">${roll ? '<span style="font-family:var(--font-mono);color:var(--accent);font-size:.72rem;">Roll: ' + roll + '</span> · ' : ''}${timeStr}</div>
    </div>
    <i class="fas fa-check-circle" style="color:var(--success);font-size:13px;flex-shrink:0;"></i>
  `;
  logBody.insertBefore(entry, logBody.firstChild);
}

// ─── Load Stats ──────────────────────────────────────────────────────
async function loadStats() {
  try {
    const res = await fetch('/get_attendance_data');
    const data = await res.json();
    if (data.status !== 'success') return;

    const total = data.counts.reduce((a, b) => a + b, 0);
    if (totalCount) totalCount.textContent = total;

    const today    = new Date().toISOString().split('T')[0];
    const todayIdx = data.dates.indexOf(today);
    if (todayCount) todayCount.textContent = todayIdx >= 0 ? data.counts[todayIdx] : 0;

    if (lastTimestamp) {
      if (data.last_timestamp) {
        lastTimestamp.textContent = data.last_timestamp.split(' ')[1] || '—';
        lastTimestamp.style.fontSize = '1.2rem';
      } else if (data.dates.length > 0) {
        lastTimestamp.textContent = data.dates[data.dates.length - 1];
        lastTimestamp.style.fontSize = '0.85rem';
      }
    }
  } catch (e) {
    console.error('Stats fetch error:', e);
  }
}

// ─── Load Recent Attendance ──────────────────────────────────────────
async function loadRecentAttendance() {
  if (!logBody) return;
  try {
    const res = await fetch('/get_attendance_data');
    const data = await res.json();
    if (data.status !== 'success' || !data.dates || data.dates.length === 0) return;

    const fullRes = await fetch('/attendance');
    const html = await fullRes.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, 'text/html');
    const rows = doc.querySelectorAll('.data-table tbody tr');
    if (rows.length === 0) return;

    logBody.innerHTML = '';
    const limit = Math.min(rows.length, 6);
    for (let i = 0; i < limit; i++) {
      const cells = rows[i].querySelectorAll('td');
      if (cells.length >= 2) {
        const name = cells[1]?.textContent?.trim() || '';
        const date = cells[cells.length - 3]?.textContent?.trim() || '';
        const time = cells[cells.length - 2]?.textContent?.trim() || '';
        addLogEntry(name, `${date} ${time}`);
      }
    }
  } catch (e) { /* silent */ }
}

// ─── Stop camera on page unload ──────────────────────────────────────
window.addEventListener('beforeunload', () => {
  if (stream) stream.getTracks().forEach(t => t.stop());
});

// ─── Init ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  loadStats();
  loadRecentAttendance();
  if (statusBar) setStatus("Click 'Start Camera' to begin face recognition", 'info');
});