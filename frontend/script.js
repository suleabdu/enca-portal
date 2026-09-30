// All calls go through /api/... — in production, Netlify's redirect proxy
// (see netlify.toml) forwards these to the Render backend, so this stays a
// same-origin path with no CORS involved from the browser's point of view.
const API_BASE = '/api';

const SESSION_KEY = 'enca_session';
const INACTIVITY_LIMIT_MS = 5 * 60 * 1000; // 5 minutes
const AUTO_SYNC_INTERVAL_MS = 60 * 1000;   // background refresh every 60s

// ---- Student state ----
let currentStudent = null;
let currentAssignments = [];
let activeAssignment = null;
let currentCourseContent = [];

// ---- Admin state ----
let currentAdmin = null;
let currentAdminCourseContent = [];
let currentAdminStudents = [];
let currentAdminProjects = [];

// ---- Shared state ----
let inactivityTimer = null;
let toastTimer = null;
let autoSyncTimer = null;

document.addEventListener('DOMContentLoaded', function () {
  wireUpEventListeners();
  attemptSessionResync();
});

function wireUpEventListeners() {
  // Landing navigation
  document.getElementById('goStudentLogin').addEventListener('click', function () {
    showScreen('loginScreen');
  });
  document.getElementById('goAdminLogin').addEventListener('click', function () {
    showScreen('adminLoginScreen');
  });
  document.getElementById('backFromStudentLogin').addEventListener('click', function () {
    showScreen('landingScreen');
  });
  document.getElementById('backFromAdminLogin').addEventListener('click', function () {
    showScreen('landingScreen');
  });

  // Student login/logout
  document.getElementById('loginForm').addEventListener('submit', handleLogin);
  document.getElementById('logoutBtn').addEventListener('click', function () { handleLogout(); });

  // Admin login/logout
  document.getElementById('adminLoginForm').addEventListener('submit', handleAdminLogin);
  document.getElementById('adminLogoutBtn').addEventListener('click', function () { handleAdminLogout(); });

  // Submission modal
  document.getElementById('closeModalBtn').addEventListener('click', closeModal);
  document.getElementById('submitForm').addEventListener('submit', handleSubmitAssignment);
  document.getElementById('submitModal').addEventListener('click', function (e) {
    if (e.target === this) closeModal();
  });

  // Shared detail modal
  document.getElementById('closeDetailModalBtn').addEventListener('click', closeDetailModal);
  document.getElementById('detailModal').addEventListener('click', function (e) {
    if (e.target === this) closeDetailModal();
  });

  // Student tabs
  document.querySelectorAll('.tab-btn[data-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () { activateTab(btn.dataset.tab); });
  });

  // Admin tabs
  document.querySelectorAll('.tab-btn[data-admin-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () { activateAdminTab(btn.dataset.adminTab); });
  });

  // Filters — each re-renders from the full cached list, so background
  // refreshes can re-apply whatever the user has typed
  document.getElementById('filterAssignments').addEventListener('input', applyAssignmentsFilter);
  document.getElementById('filterCourseContent').addEventListener('input', applyCourseContentFilter);
  document.getElementById('filterAdminStudents').addEventListener('input', applyAdminStudentsFilter);
  document.getElementById('filterAdminCourseContent').addEventListener('input', applyAdminCourseContentFilter);
  document.getElementById('filterAdminProjects').addEventListener('input', applyAdminProjectsFilter);

  // Manual "refresh now"
  document.getElementById('studentSyncBtn').addEventListener('click', function () { syncStudentData(); });
  document.getElementById('adminSyncBtn').addEventListener('click', function () { syncAdminData(); });

  // Sync straight away when the user comes back to the tab
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) return;
    if (currentStudent) syncStudentData();
    else if (currentAdmin) syncAdminData();
  });
}

const FILTER_FIELDS = {
  assignments: ['course', 'lectureId', 'content'],
  content: ['date', 'module', 'sessionFocus'],
  students: ['id', 'name', 'email', 'course', 'cohort', 'status'],
  projects: ['regId', 'name', 'category'],
};

function currentQuery(inputId) {
  return document.getElementById(inputId).value.trim().toLowerCase();
}

function filterList(items, query, fields) {
  if (!query) return items;
  return items.filter(function (item) {
    return fields.some(function (f) {
      return (item[f] || '').toString().toLowerCase().includes(query);
    });
  });
}

function applyAssignmentsFilter() {
  renderAssignments(filterList(currentAssignments, currentQuery('filterAssignments'), FILTER_FIELDS.assignments));
}
function applyCourseContentFilter() {
  renderContentList('courseContentList', filterList(currentCourseContent, currentQuery('filterCourseContent'), FILTER_FIELDS.content));
}
function applyAdminStudentsFilter() {
  renderStudentsTable(filterList(currentAdminStudents, currentQuery('filterAdminStudents'), FILTER_FIELDS.students));
}
function applyAdminCourseContentFilter() {
  renderContentList('adminCourseContentList', filterList(currentAdminCourseContent, currentQuery('filterAdminCourseContent'), FILTER_FIELDS.content));
}
function applyAdminProjectsFilter() {
  renderProjectsList(filterList(currentAdminProjects, currentQuery('filterAdminProjects'), FILTER_FIELDS.projects));
}

function appendEmptyNote(container) {
  const note = document.createElement('div');
  note.className = 'muted-text empty-note';
  note.textContent = 'No matching results.';
  container.appendChild(note);
}

// ======================================================================= //
// Screens, session persistence, inactivity logout & background sync
// ======================================================================= //

function showScreen(id) {
  ['bootScreen', 'landingScreen', 'loginScreen', 'adminLoginScreen', 'dashboardScreen', 'adminDashboardScreen']
    .forEach(function (screenId) {
      document.getElementById(screenId).hidden = screenId !== id;
    });
}

function showToast(message, type) {
  const el = document.getElementById('toast');
  el.textContent = message;
  el.className = 'toast' + (type ? ' toast-' + type : '');
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { el.hidden = true; }, 4000);
}

function saveSession(data) {
  try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(data)); } catch (err) { /* storage unavailable — session just won't persist */ }
}

function clearSession() {
  try { sessionStorage.removeItem(SESSION_KEY); } catch (err) { /* ignore */ }
}

function readSession() {
  try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null'); } catch (err) { return null; }
}

async function verifySavedSession(saved) {
  const endpoint = saved.type === 'admin' ? '/admin/login' : '/login';
  const res = await fetch(`${API_BASE}${endpoint}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: saved.username, password: saved.password }),
  });
  return res.json();
}

async function attemptSessionResync() {
  const saved = readSession();

  if (!saved || (saved.type !== 'student' && saved.type !== 'admin')) {
    showScreen('landingScreen');
    return;
  }

  try {
    const result = await verifySavedSession(saved);
    if (result.success) {
      if (saved.type === 'student') {
        currentStudent = result.student;
        showStudentDashboard();
      } else {
        currentAdmin = result.admin;
        showAdminDashboard();
      }
      return;
    }
    // The server explicitly rejected the saved credentials — discard them.
    clearSession();
    showScreen('landingScreen');
    showToast('Your session has expired. Please log in again.', 'error');
  } catch (err) {
    // Network problem (or a cold-starting backend) — keep the saved session
    // so a reload can retry, but don't leave the user on a blank screen.
    showScreen('landingScreen');
    showToast("Couldn't reach the server to restore your session. Please try again.", 'error');
  }
}

function startAutoSync() {
  stopAutoSync();
  autoSyncTimer = setInterval(function () {
    if (document.hidden) return; // don't poll while the tab is in the background
    if (currentStudent) syncStudentData();
    else if (currentAdmin) syncAdminData();
  }, AUTO_SYNC_INTERVAL_MS);
}

function stopAutoSync() {
  clearInterval(autoSyncTimer);
  autoSyncTimer = null;
}

function markSynced() {
  const stamp = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  ['studentSyncTime', 'adminSyncTime'].forEach(function (id) {
    document.getElementById(id).textContent = 'Synced ' + stamp;
  });
}

function setSyncing(isSyncing) {
  ['studentSyncBtn', 'adminSyncBtn'].forEach(function (id) {
    document.getElementById(id).classList.toggle('syncing', isSyncing);
  });
}

const ACTIVITY_EVENTS = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll'];

function resetInactivityTimer() {
  clearTimeout(inactivityTimer);
  inactivityTimer = setTimeout(handleInactivityTimeout, INACTIVITY_LIMIT_MS);
}

function startInactivityWatch() {
  ACTIVITY_EVENTS.forEach(function (evt) { document.addEventListener(evt, resetInactivityTimer); });
  resetInactivityTimer();
}

function stopInactivityWatch() {
  clearTimeout(inactivityTimer);
  ACTIVITY_EVENTS.forEach(function (evt) { document.removeEventListener(evt, resetInactivityTimer); });
}

function handleInactivityTimeout() {
  const wasStudent = !!currentStudent;
  const wasAdmin = !!currentAdmin;
  if (wasStudent) handleLogout(true);
  else if (wasAdmin) handleAdminLogout(true);
  if (wasStudent || wasAdmin) {
    showToast("You've been logged out after 5 minutes of inactivity.", 'info');
  }
}

function initials(name) {
  const trimmed = (name || '').trim();
  if (!trimmed) return '?';
  const parts = trimmed.split(/\s+/);
  return (parts[0][0] + (parts[1] ? parts[1][0] : '')).toUpperCase();
}

function renderStatusPill(el, status) {
  const label = status || 'Not Set';
  const admitted = label.trim().toLowerCase() === 'admitted';
  el.textContent = label;
  el.className = 'status-pill ' + (admitted ? 'status-admitted' : 'status-other');
  return admitted;
}

function performanceLabel(score) {
  if (score >= 85) return 'Excellent';
  if (score >= 70) return 'Good';
  if (score >= 50) return 'Fair';
  return 'Needs Improvement';
}

function performanceTierClass(score) {
  if (score >= 85) return 'tier-excellent';
  if (score >= 70) return 'tier-good';
  if (score >= 50) return 'tier-fair';
  return 'tier-low';
}

// ======================================================================= //
// STUDENT: Login / Logout
// ======================================================================= //

async function handleLogin(e) {
  e.preventDefault();
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value.trim();
  const errorBox = document.getElementById('loginError');
  const btn = document.getElementById('loginBtn');

  errorBox.hidden = true;
  btn.disabled = true;
  btn.textContent = 'Logging in…';

  try {
    const res = await fetch(`${API_BASE}/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const result = await res.json();

    if (result.success) {
      currentStudent = result.student;
      saveSession({ type: 'student', username, password });
      showStudentDashboard();
    } else {
      errorBox.textContent = result.message || 'Login failed.';
      errorBox.hidden = false;
    }
  } catch (err) {
    errorBox.textContent = 'Unexpected error: ' + err.message;
    errorBox.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Log In';
  }
}

function handleLogout(silent) {
  currentStudent = null;
  currentAssignments = [];
  currentCourseContent = [];
  stopInactivityWatch();
  stopAutoSync();
  clearSession();
  document.getElementById('username').value = '';
  document.getElementById('password').value = '';
  showScreen('landingScreen');
  activateTab('overviewTab');
  if (!silent) { /* manual logout — no extra toast needed */ }
}

// ======================================================================= //
// STUDENT: Dashboard
// ======================================================================= //

function renderStudentProfile() {
  document.getElementById('welcomeName').textContent = currentStudent.name || 'Student';
  document.getElementById('infoRegId').textContent = currentStudent.regId || '—';
  document.getElementById('infoName').textContent = currentStudent.name || '—';
  document.getElementById('infoGender').textContent = currentStudent.gender || '—';
  document.getElementById('infoCourse').textContent = currentStudent.course || '—';
  renderStatusPill(document.getElementById('infoStatusPill'), currentStudent.status);

  document.getElementById('topbarAvatar').textContent = initials(currentStudent.name);
  document.getElementById('topbarName').textContent = currentStudent.name || 'Student';
  document.getElementById('topbarCourse').textContent = currentStudent.course || '—';
}

function showStudentDashboard() {
  showScreen('dashboardScreen');
  renderStudentProfile();

  startInactivityWatch();
  startAutoSync();
  loadAssignments();
  loadCourseContent();
  loadDashboardSummary();
  markSynced();
}

// Pulls fresh data for the whole student dashboard: re-verifies the saved
// login (so an admin changing this student's Status shows up immediately),
// then reloads each tab without blanking anything already on screen.
async function syncStudentData() {
  if (!currentStudent) return;
  setSyncing(true);

  const saved = readSession();
  if (saved && saved.type === 'student') {
    try {
      const result = await verifySavedSession(saved);
      if (result.success) {
        currentStudent = result.student;
        renderStudentProfile();
      } else {
        setSyncing(false);
        handleLogout(true);
        showToast('Your session is no longer valid. Please log in again.', 'error');
        return;
      }
    } catch (err) {
      // Offline / server asleep — carry on with what's already on screen.
    }
  }

  await Promise.all([loadAssignments(true), loadCourseContent(true), loadDashboardSummary()]);
  markSynced();
  setSyncing(false);
}

function activateTab(tabId) {
  document.querySelectorAll('.tab-btn[data-tab]').forEach(function (b) {
    b.classList.toggle('active', b.dataset.tab === tabId);
  });
  document.querySelectorAll('.tab-panel').forEach(function (p) {
    p.classList.toggle('active', p.id === tabId);
  });
}

// ======================================================================= //
// STUDENT: Assignments
// ======================================================================= //

async function loadAssignments(silent) {
  const statusEl = document.getElementById('assignmentsStatus');
  const listEl = document.getElementById('assignmentsList');
  const restrictedEl = document.getElementById('assignmentsRestricted');
  if (!silent) {
    restrictedEl.hidden = true;
    statusEl.hidden = false;
    statusEl.textContent = 'Loading assignments…';
    listEl.innerHTML = '';
  }

  try {
    const res = await fetch(`${API_BASE}/assignments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ regId: currentStudent.regId, studentName: currentStudent.name }),
    });
    const result = await res.json();

    if (!result.success) {
      if (!silent) statusEl.textContent = result.message || 'Could not load assignments.';
      return;
    }
    if (result.restricted) {
      currentAssignments = [];
      listEl.innerHTML = '';
      statusEl.hidden = true;
      restrictedEl.hidden = false;
      restrictedEl.textContent = result.message;
      return;
    }
    restrictedEl.hidden = true;
    currentAssignments = result.assignments || [];
    if (currentAssignments.length === 0) {
      listEl.innerHTML = '';
      statusEl.hidden = false;
      statusEl.textContent = 'No assignments have been issued yet.';
      return;
    }
    statusEl.hidden = true;
    applyAssignmentsFilter();
  } catch (err) {
    if (!silent) statusEl.textContent = 'Unexpected error: ' + err.message;
  }
}

function renderAssignments(items) {
  const listEl = document.getElementById('assignmentsList');
  listEl.innerHTML = '';
  if (items.length === 0) { appendEmptyNote(listEl); return; }

  items.forEach(function (item) {
    const row = document.createElement('div');
    row.className = 'assignment-item' + (item.submitted ? ' submitted' : '');

    const main = document.createElement('div');
    main.className = 'assignment-main';

    const topRow = document.createElement('div');
    topRow.className = 'assignment-top-row';
    if (item.lectureId) {
      const chip = document.createElement('span');
      chip.className = 'lecture-chip';
      chip.textContent = 'Lecture: ' + item.lectureId;
      topRow.appendChild(chip);
    }
    const course = document.createElement('span');
    course.className = 'assignment-course';
    course.textContent = item.course || 'Course';
    topRow.appendChild(course);
    main.appendChild(topRow);

    const snippet = document.createElement('div');
    snippet.className = 'assignment-snippet';
    snippet.textContent = item.content;
    main.appendChild(snippet);

    const scoreChip = document.createElement('div');
    if (item.score) {
      scoreChip.className = 'score-chip graded';
      scoreChip.textContent = 'Score: ' + item.score + (item.mark ? ' / ' + item.mark : '');
    } else if (item.mark) {
      scoreChip.className = 'score-chip ungraded';
      scoreChip.textContent = 'Max Mark: ' + item.mark + ' • Not graded yet';
    } else {
      scoreChip.className = 'score-chip ungraded';
      scoreChip.textContent = 'Not graded yet';
    }
    main.appendChild(scoreChip);

    const badge = document.createElement('div');
    badge.className = 'badge ' + (item.submitted ? 'badge-submitted' : 'badge-pending');
    badge.textContent = item.submitted ? 'Submitted' : 'Open';

    row.appendChild(main);
    row.appendChild(badge);

    if (!item.submitted) {
      row.addEventListener('click', function () { openSubmitModal(item); });
    }

    listEl.appendChild(row);
  });
}

// ======================================================================= //
// STUDENT: Submission modal
// ======================================================================= //

function openSubmitModal(assignment) {
  activeAssignment = assignment;
  document.getElementById('modalLectureId').value = activeAssignment.lectureId || '';
  document.getElementById('modalCourse').value = activeAssignment.course || '';
  document.getElementById('modalContent').textContent = activeAssignment.content || '';
  document.getElementById('modalDescription').value = '';
  document.getElementById('modalFile').value = '';

  const statusEl = document.getElementById('submitStatus');
  statusEl.hidden = true;
  statusEl.className = 'status-text';

  document.getElementById('submitModal').hidden = false;
}

function closeModal() {
  document.getElementById('submitModal').hidden = true;
  activeAssignment = null;
}

async function handleSubmitAssignment(e) {
  e.preventDefault();
  if (!activeAssignment) return;

  const fileInput = document.getElementById('modalFile');
  const statusEl = document.getElementById('submitStatus');
  const btn = document.getElementById('submitAssignmentBtn');

  if (!fileInput.files || fileInput.files.length === 0) {
    statusEl.textContent = 'Please attach a file before submitting.';
    statusEl.className = 'status-text error';
    statusEl.hidden = false;
    return;
  }

  const formData = new FormData();
  formData.append('lectureId', activeAssignment.lectureId || '');
  formData.append('regId', currentStudent.regId);
  formData.append('studentName', currentStudent.name);
  formData.append('course', activeAssignment.course);
  formData.append('description', document.getElementById('modalDescription').value.trim());
  formData.append('file', fileInput.files[0]);

  btn.disabled = true;
  btn.textContent = 'Submitting…';
  statusEl.hidden = true;

  try {
    const res = await fetch(`${API_BASE}/submit`, { method: 'POST', body: formData });
    const result = await res.json();

    statusEl.hidden = false;
    if (result.success) {
      statusEl.textContent = result.message;
      statusEl.className = 'status-text success';
      setTimeout(function () {
        closeModal();
        loadAssignments();
      }, 1200);
    } else {
      statusEl.textContent = result.message || 'Submission failed.';
      statusEl.className = 'status-text error';
    }
  } catch (err) {
    statusEl.hidden = false;
    statusEl.textContent = 'Unexpected error: ' + err.message;
    statusEl.className = 'status-text error';
  } finally {
    btn.disabled = false;
    btn.textContent = 'Submit';
  }
}

// ======================================================================= //
// STUDENT: Dashboard summary cards (Attendance / Assignments / Aggregate)
// ======================================================================= //

async function loadDashboardSummary() {
  try {
    const res = await fetch(`${API_BASE}/dashboard-summary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ regId: currentStudent.regId }),
    });
    const result = await res.json();
    if (!result.success) return;

    document.getElementById('statPresent').textContent = result.present;
    document.getElementById('statAbsent').textContent = result.absent;
    document.getElementById('statAttendanceRate').textContent = result.attendanceRate + '%';
    document.getElementById('statAttendanceBar').style.width = Math.min(result.attendanceRate, 100) + '%';

    document.getElementById('statSubmissionFraction').textContent = result.submitted + ' / ' + result.issued;
    document.getElementById('statScoreFraction').textContent =
      result.totalScore + ' / ' + result.totalMark;
    document.getElementById('statScorePercentage').textContent = result.scorePercentage + '%';
    document.getElementById('statScoreBar').style.width = Math.min(result.scorePercentage, 100) + '%';

    document.getElementById('statAggregatePerformance').textContent = result.aggregatePerformance + '%';
    document.getElementById('statAggregateLabel').textContent =
      performanceLabel(result.aggregatePerformance) + ' — combines attendance, submissions & scores';
  } catch (err) {
    console.warn('Could not load dashboard summary:', err.message);
  }
}

// ======================================================================= //
// Shared Course Content row builder (used by student + admin content tabs)
// ======================================================================= //

function contentField(label, value, extraClass) {
  const wrap = document.createElement('div');
  wrap.className = 'content-field';
  const labelEl = document.createElement('div');
  labelEl.className = 'content-field-label';
  labelEl.textContent = label;
  const valueEl = document.createElement('div');
  valueEl.className = 'content-field-value' + (extraClass ? ' ' + extraClass : '');
  valueEl.textContent = value || '—';
  wrap.appendChild(labelEl);
  wrap.appendChild(valueEl);
  return wrap;
}

function buildContentRow(item) {
  const row = document.createElement('div');
  row.className = 'content-item' + (item.locked ? ' locked' : '');

  row.appendChild(contentField('Date', item.date));
  row.appendChild(contentField('Module', item.module, 'course-title'));
  row.appendChild(contentField('Session Focus', item.sessionFocus));
  row.appendChild(contentField('Attendance', item.attendance));

  const resourceField = document.createElement('div');
  resourceField.className = 'content-field';
  const resourceLabel = document.createElement('div');
  resourceLabel.className = 'content-field-label';
  resourceLabel.textContent = 'Learning Resource';
  resourceField.appendChild(resourceLabel);
  const resourcePill = document.createElement('div');
  resourcePill.className = item.locked ? 'lock-pill' : 'resource-pill';
  resourcePill.textContent = item.locked ? '🔒 Locked' : (item.videoId ? '▶ Video' : 'Available');
  resourceField.appendChild(resourcePill);
  row.appendChild(resourceField);

  const actionField = document.createElement('div');
  actionField.className = 'content-field';
  const actionBtn = document.createElement('button');
  actionBtn.type = 'button';
  actionBtn.className = 'btn ' + (item.locked ? 'btn-ghost' : 'btn-primary');
  actionBtn.textContent = item.locked ? 'Locked' : 'View';
  actionBtn.disabled = !!item.locked;
  if (!item.locked) {
    actionBtn.addEventListener('click', function () { openDetailModal(item, 'Session Details'); });
  }
  actionField.appendChild(actionBtn);
  row.appendChild(actionField);

  return row;
}

function renderContentList(containerId, items) {
  const listEl = document.getElementById(containerId);
  listEl.innerHTML = '';
  if (items.length === 0) { appendEmptyNote(listEl); return; }
  items.forEach(function (item) { listEl.appendChild(buildContentRow(item)); });
}

function openDetailModal(item, title) {
  if (!item || item.locked || !item.details) return;

  document.getElementById('detailModalTitle').textContent = title || 'Details';

  const fieldsEl = document.getElementById('detailFields');
  fieldsEl.innerHTML = '';

  Object.keys(item.details).forEach(function (label) {
    const row = document.createElement('div');
    row.className = 'detail-row';
    const labelEl = document.createElement('div');
    labelEl.className = 'field-label';
    labelEl.textContent = label;
    const valueEl = document.createElement('div');
    valueEl.className = 'detail-row-value';
    valueEl.textContent = item.details[label] || '—';
    row.appendChild(labelEl);
    row.appendChild(valueEl);
    fieldsEl.appendChild(row);
  });

  const videoBlock = document.getElementById('detailVideoBlock');
  const videoFrame = document.getElementById('detailVideoFrame');
  if (item.videoId) {
    videoFrame.src = 'https://www.youtube.com/embed/' + item.videoId;
    videoBlock.hidden = false;
  } else {
    videoFrame.src = '';
    videoBlock.hidden = true;
  }

  const fileBlock = document.getElementById('detailFileBlock');
  const fileLink = document.getElementById('detailFileLink');
  if (item.fileUrl) {
    fileLink.href = item.fileUrl;
    fileBlock.hidden = false;
  } else {
    fileLink.href = '#';
    fileBlock.hidden = true;
  }

  document.getElementById('detailModal').hidden = false;
}

function closeDetailModal() {
  document.getElementById('detailModal').hidden = true;
  document.getElementById('detailVideoFrame').src = ''; // stop playback
}

// ======================================================================= //
// STUDENT: Course Content tab
// ======================================================================= //

async function loadCourseContent(silent) {
  const statusEl = document.getElementById('courseContentStatus');
  const listEl = document.getElementById('courseContentList');
  const restrictedEl = document.getElementById('courseContentRestricted');
  if (!silent) {
    restrictedEl.hidden = true;
    statusEl.hidden = false;
    statusEl.textContent = 'Loading course content…';
    listEl.innerHTML = '';
  }

  try {
    const res = await fetch(`${API_BASE}/course-content`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ regId: currentStudent.regId }),
    });
    const result = await res.json();

    if (!result.success) {
      if (!silent) statusEl.textContent = result.message || 'Could not load course content.';
      return;
    }
    if (result.restricted) {
      currentCourseContent = [];
      listEl.innerHTML = '';
      statusEl.hidden = true;
      restrictedEl.hidden = false;
      restrictedEl.textContent = result.message;
      return;
    }
    restrictedEl.hidden = true;
    currentCourseContent = result.entries || [];
    if (currentCourseContent.length === 0) {
      listEl.innerHTML = '';
      statusEl.hidden = false;
      statusEl.textContent = 'No course content has been scheduled yet.';
      return;
    }
    statusEl.hidden = true;
    applyCourseContentFilter();
  } catch (err) {
    if (!silent) statusEl.textContent = 'Unexpected error: ' + err.message;
  }
}

// ======================================================================= //
// ADMIN: Login / Logout
// ======================================================================= //

async function handleAdminLogin(e) {
  e.preventDefault();
  const username = document.getElementById('adminUsername').value.trim();
  const password = document.getElementById('adminPassword').value.trim();
  const errorBox = document.getElementById('adminLoginError');
  const btn = document.getElementById('adminLoginBtn');

  errorBox.hidden = true;
  btn.disabled = true;
  btn.textContent = 'Logging in…';

  try {
    const res = await fetch(`${API_BASE}/admin/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    const result = await res.json();

    if (result.success) {
      currentAdmin = result.admin;
      saveSession({ type: 'admin', username, password });
      showAdminDashboard();
    } else {
      errorBox.textContent = result.message || 'Login failed.';
      errorBox.hidden = false;
    }
  } catch (err) {
    errorBox.textContent = 'Unexpected error: ' + err.message;
    errorBox.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Log In';
  }
}

function handleAdminLogout(silent) {
  currentAdmin = null;
  currentAdminCourseContent = [];
  currentAdminStudents = [];
  currentAdminProjects = [];
  stopInactivityWatch();
  stopAutoSync();
  clearSession();
  document.getElementById('adminUsername').value = '';
  document.getElementById('adminPassword').value = '';
  showScreen('landingScreen');
  activateAdminTab('adminOverviewTab');
  if (!silent) { /* manual logout — no extra toast needed */ }
}

// ======================================================================= //
// ADMIN: Dashboard shell
// ======================================================================= //

function showAdminDashboard() {
  showScreen('adminDashboardScreen');
  document.getElementById('adminTopbarAvatar').textContent = initials(currentAdmin.name);
  document.getElementById('adminTopbarName').textContent = currentAdmin.name || 'Admin';

  startInactivityWatch();
  startAutoSync();
  loadAdminOverview();
  loadAdminStudents();
  loadAdminCourseContent();
  loadAdminProjects();
  markSynced();
}

async function syncAdminData() {
  if (!currentAdmin) return;
  setSyncing(true);

  const saved = readSession();
  if (saved && saved.type === 'admin') {
    try {
      const result = await verifySavedSession(saved);
      if (result.success) {
        currentAdmin = result.admin;
      } else {
        setSyncing(false);
        handleAdminLogout(true);
        showToast('Your session is no longer valid. Please log in again.', 'error');
        return;
      }
    } catch (err) {
      // Offline / server asleep — carry on with what's already on screen.
    }
  }

  // Don't yank the Students table out from under an admin mid-edit.
  const editingStatus = document.activeElement && document.activeElement.classList &&
    document.activeElement.classList.contains('status-select');

  const jobs = [loadAdminOverview(true), loadAdminCourseContent(true), loadAdminProjects(true)];
  if (!editingStatus) jobs.push(loadAdminStudents(true));
  await Promise.all(jobs);

  markSynced();
  setSyncing(false);
}

function activateAdminTab(tabId) {
  document.querySelectorAll('.tab-btn[data-admin-tab]').forEach(function (b) {
    b.classList.toggle('active', b.dataset.adminTab === tabId);
  });
  document.querySelectorAll('.admin-tab-panel').forEach(function (p) {
    p.classList.toggle('active', p.id === tabId);
  });
}

// ======================================================================= //
// ADMIN: Overview analytics (totals, top/bottom, bar chart, leaderboard)
// ======================================================================= //

async function loadAdminOverview(silent) {
  const statusEl = document.getElementById('adminOverviewStatus');
  const gridEl = document.getElementById('adminStatGrid');
  const analyticsEl = document.getElementById('cohortAnalyticsSection');
  if (!silent) {
    statusEl.hidden = false;
    statusEl.textContent = 'Loading cohort analytics…';
    gridEl.hidden = true;
    analyticsEl.hidden = true;
  }

  try {
    const res = await fetch(`${API_BASE}/admin/dashboard-summary`, { method: 'POST' });
    const result = await res.json();

    if (!result.success) {
      if (!silent) statusEl.textContent = result.message || 'Could not load cohort analytics.';
      return;
    }

    document.getElementById('adminTotalStudents').textContent = result.totalStudents;
    document.getElementById('adminStudentsWithData').textContent =
      result.studentsWithData + ' with recorded activity';

    if (result.topPerformer) {
      document.getElementById('topPerformerName').textContent =
        result.topPerformer.name + (result.topPerformer.course ? ' · ' + result.topPerformer.course : '');
      document.getElementById('topPerformerScore').textContent = result.topPerformer.aggregatePerformance + '%';
      document.getElementById('topPerformerHeadline').textContent = result.topPerformer.headline;
    } else {
      document.getElementById('topPerformerName').textContent = '—';
      document.getElementById('topPerformerScore').textContent = '—';
      document.getElementById('topPerformerHeadline').textContent = 'Not enough recorded activity yet.';
    }

    if (result.lowestPerformer) {
      document.getElementById('bottomPerformerName').textContent =
        result.lowestPerformer.name + (result.lowestPerformer.course ? ' · ' + result.lowestPerformer.course : '');
      document.getElementById('bottomPerformerScore').textContent = result.lowestPerformer.aggregatePerformance + '%';
      document.getElementById('bottomPerformerHeadline').textContent = result.lowestPerformer.headline;
    } else {
      document.getElementById('bottomPerformerName').textContent = '—';
      document.getElementById('bottomPerformerScore').textContent = '—';
      document.getElementById('bottomPerformerHeadline').textContent = 'Not enough recorded activity yet.';
    }

    renderCohortBarChart(result.leaderboard || []);
    renderLeaderboardTable(result.leaderboard || []);

    const noDataNote = document.getElementById('noDataNote');
    if (result.studentsWithoutData > 0) {
      noDataNote.hidden = false;
      noDataNote.textContent = `${result.studentsWithoutData} student(s) have no recorded activity yet and are excluded from the comparison below.`;
    } else {
      noDataNote.hidden = true;
    }

    statusEl.hidden = true;
    gridEl.hidden = false;
    analyticsEl.hidden = false;
  } catch (err) {
    if (!silent) statusEl.textContent = 'Unexpected error: ' + err.message;
  }
}

function renderCohortBarChart(leaderboard) {
  const chartEl = document.getElementById('cohortBarChart');
  chartEl.innerHTML = '';

  if (leaderboard.length === 0) {
    chartEl.innerHTML = '<p class="muted-text">No recorded activity to compare yet.</p>';
    return;
  }

  leaderboard.forEach(function (entry) {
    const row = document.createElement('div');
    row.className = 'bar-row';

    const label = document.createElement('div');
    label.className = 'bar-label';
    label.textContent = entry.name;
    label.title = entry.name;

    const track = document.createElement('div');
    track.className = 'bar-track';
    const fill = document.createElement('div');
    fill.className = 'bar-fill ' + performanceTierClass(entry.consistency);
    fill.style.width = Math.min(entry.consistency, 100) + '%';
    track.appendChild(fill);

    const value = document.createElement('div');
    value.className = 'bar-value';
    value.textContent = entry.consistency + '%';

    row.appendChild(label);
    row.appendChild(track);
    row.appendChild(value);
    chartEl.appendChild(row);
  });
}

function renderLeaderboardTable(leaderboard) {
  const bodyEl = document.getElementById('leaderboardTableBody');
  bodyEl.innerHTML = '';

  leaderboard.forEach(function (entry) {
    const tr = document.createElement('tr');

    const nameTd = document.createElement('td');
    nameTd.textContent = entry.name;

    const consistencyTd = document.createElement('td');
    consistencyTd.textContent = entry.consistency + '%';

    const remarkTd = document.createElement('td');
    remarkTd.className = 'remark-cell';
    remarkTd.textContent = entry.remark;

    tr.appendChild(nameTd);
    tr.appendChild(consistencyTd);
    tr.appendChild(remarkTd);
    bodyEl.appendChild(tr);
  });
}

// ======================================================================= //
// ADMIN: Students tab
// ======================================================================= //

const STATUS_PRESETS = ['Admitted', 'Pending', 'Suspended', 'Withdrawn', 'Graduated'];

async function loadAdminStudents(silent) {
  const statusEl = document.getElementById('adminStudentsStatus');
  if (!silent) {
    statusEl.hidden = false;
    statusEl.textContent = 'Loading students…';
    document.getElementById('adminStudentsTable').hidden = true;
  }

  try {
    const res = await fetch(`${API_BASE}/admin/students`, { method: 'POST' });
    const result = await res.json();

    if (!result.success) {
      if (!silent) statusEl.textContent = result.message || 'Could not load students.';
      return;
    }
    currentAdminStudents = result.students || [];
    if (currentAdminStudents.length === 0) {
      statusEl.hidden = false;
      statusEl.textContent = 'No students found.';
      return;
    }
    statusEl.hidden = true;
    applyAdminStudentsFilter();
  } catch (err) {
    if (!silent) statusEl.textContent = 'Unexpected error: ' + err.message;
  }
}

function renderStudentsTable(students) {
  const tableEl = document.getElementById('adminStudentsTable');
  const bodyEl = document.getElementById('adminStudentsTableBody');
  bodyEl.innerHTML = '';

  if (students.length === 0) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 10;
    td.className = 'muted-text';
    td.textContent = 'No matching results.';
    tr.appendChild(td);
    bodyEl.appendChild(tr);
  } else {
    students.forEach(function (student) { bodyEl.appendChild(buildStudentRow(student)); });
  }
  tableEl.hidden = false;
}

function buildStudentRow(student) {
  const tr = document.createElement('tr');

  [student.id, student.name, student.gender, student.email, student.phone,
   student.course, student.cohort, student.admissionDate, student.year].forEach(function (val) {
    const td = document.createElement('td');
    td.textContent = val || '—';
    tr.appendChild(td);
  });

  const statusTd = document.createElement('td');
  const select = document.createElement('select');
  select.className = 'status-select ' + (student.status.toLowerCase() === 'admitted' ? 'status-admitted' : 'status-other');

  const options = STATUS_PRESETS.slice();
  if (student.status && options.indexOf(student.status) === -1 && student.status !== 'Not Set') {
    options.push(student.status);
  }
  options.forEach(function (opt) {
    const optionEl = document.createElement('option');
    optionEl.value = opt;
    optionEl.textContent = opt;
    if (opt === student.status) optionEl.selected = true;
    select.appendChild(optionEl);
  });

  select.addEventListener('change', function () {
    updateStudentStatus(student.id, select.value, select);
  });

  statusTd.appendChild(select);
  tr.appendChild(statusTd);

  return tr;
}

async function updateStudentStatus(studentId, newStatus, selectEl) {
  selectEl.disabled = true;

  try {
    const res = await fetch(`${API_BASE}/admin/students/update-status`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: studentId, status: newStatus }),
    });
    const result = await res.json();

    if (result.success) {
      selectEl.className = 'status-select ' + (newStatus.toLowerCase() === 'admitted' ? 'status-admitted' : 'status-other');
      const student = currentAdminStudents.find(function (s) { return s.id === studentId; });
      if (student) student.status = newStatus;
      showToast(`Updated ${studentId}'s status to "${newStatus}".`, 'success');
    } else {
      showToast(result.message || 'Could not update status.', 'error');
    }
  } catch (err) {
    showToast('Unexpected error: ' + err.message, 'error');
  } finally {
    selectEl.disabled = false;
  }
}

// ======================================================================= //
// ADMIN: Course Content tab
// ======================================================================= //

async function loadAdminCourseContent(silent) {
  const statusEl = document.getElementById('adminCourseContentStatus');
  if (!silent) {
    statusEl.hidden = false;
    statusEl.textContent = 'Loading course content…';
    document.getElementById('adminCourseContentList').innerHTML = '';
  }

  try {
    const res = await fetch(`${API_BASE}/admin/course-content`, { method: 'POST' });
    const result = await res.json();

    if (!result.success) {
      if (!silent) statusEl.textContent = result.message || 'Could not load course content.';
      return;
    }
    currentAdminCourseContent = result.entries || [];
    if (currentAdminCourseContent.length === 0) {
      document.getElementById('adminCourseContentList').innerHTML = '';
      statusEl.hidden = false;
      statusEl.textContent = 'No course content has been scheduled yet.';
      return;
    }
    statusEl.hidden = true;
    applyAdminCourseContentFilter();
  } catch (err) {
    if (!silent) statusEl.textContent = 'Unexpected error: ' + err.message;
  }
}

// ======================================================================= //
// ADMIN: Student Projects tab (capstone submissions)
// ======================================================================= //

async function loadAdminProjects(silent) {
  const statusEl = document.getElementById('adminProjectsStatus');
  if (!silent) {
    statusEl.hidden = false;
    statusEl.textContent = 'Loading student projects…';
    document.getElementById('adminProjectsList').innerHTML = '';
  }

  try {
    const res = await fetch(`${API_BASE}/admin/projects`, { method: 'POST' });
    const result = await res.json();

    if (!result.success) {
      if (!silent) statusEl.textContent = result.message || 'Could not load student projects.';
      return;
    }
    currentAdminProjects = result.projects || [];
    if (currentAdminProjects.length === 0) {
      document.getElementById('adminProjectsList').innerHTML = '';
      statusEl.hidden = false;
      statusEl.textContent = 'No capstone project submissions found yet.';
      return;
    }
    statusEl.hidden = true;
    applyAdminProjectsFilter();
  } catch (err) {
    if (!silent) statusEl.textContent = 'Unexpected error: ' + err.message;
  }
}

function renderProjectsList(projects) {
  const listEl = document.getElementById('adminProjectsList');
  listEl.innerHTML = '';
  if (projects.length === 0) { appendEmptyNote(listEl); return; }

  projects.forEach(function (item) {
    const row = document.createElement('div');
    row.className = 'content-item';

    row.appendChild(contentField('Reg ID', item.regId));
    row.appendChild(contentField('Name', item.name, 'course-title'));
    row.appendChild(contentField('Category', item.category));

    const actionField = document.createElement('div');
    actionField.className = 'content-field';
    const actionBtn = document.createElement('button');
    actionBtn.type = 'button';
    actionBtn.className = 'btn btn-primary';
    actionBtn.textContent = 'View Details';
    actionBtn.addEventListener('click', function () {
      openDetailModal(item, `${item.name} — ${item.category}`);
    });
    actionField.appendChild(actionBtn);
    row.appendChild(actionField);

    listEl.appendChild(row);
  });
}
