// All calls go through /api/... — in production, Netlify's redirect proxy
// (see netlify.toml) forwards these to the Render backend, so this stays a
// same-origin path with no CORS involved from the browser's point of view.
const API_BASE = '/api';

// ---- Student state ----
let currentStudent = null;
let currentAssignments = [];
let activeAssignment = null;
let currentCourseContent = [];

// ---- Admin state ----
let currentAdmin = null;
let currentAdminCourseContent = [];

document.addEventListener('DOMContentLoaded', function () {
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
  document.getElementById('logoutBtn').addEventListener('click', handleLogout);

  // Admin login/logout
  document.getElementById('adminLoginForm').addEventListener('submit', handleAdminLogin);
  document.getElementById('adminLogoutBtn').addEventListener('click', handleAdminLogout);

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
});

function showScreen(id) {
  ['landingScreen', 'loginScreen', 'adminLoginScreen', 'dashboardScreen', 'adminDashboardScreen']
    .forEach(function (screenId) {
      document.getElementById(screenId).hidden = screenId !== id;
    });
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

function handleLogout() {
  currentStudent = null;
  currentAssignments = [];
  currentCourseContent = [];
  document.getElementById('username').value = '';
  document.getElementById('password').value = '';
  showScreen('landingScreen');
  activateTab('overviewTab');
}

// ======================================================================= //
// STUDENT: Dashboard
// ======================================================================= //

function showStudentDashboard() {
  showScreen('dashboardScreen');

  document.getElementById('welcomeName').textContent = currentStudent.name || 'Student';
  document.getElementById('infoRegId').textContent = currentStudent.regId || '—';
  document.getElementById('infoName').textContent = currentStudent.name || '—';
  document.getElementById('infoGender').textContent = currentStudent.gender || '—';
  document.getElementById('infoCourse').textContent = currentStudent.course || '—';
  renderStatusPill(document.getElementById('infoStatusPill'), currentStudent.status);

  document.getElementById('topbarAvatar').textContent = initials(currentStudent.name);
  document.getElementById('topbarName').textContent = currentStudent.name || 'Student';
  document.getElementById('topbarCourse').textContent = currentStudent.course || '—';

  loadAssignments();
  loadCourseContent();
  loadDashboardSummary();
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

async function loadAssignments() {
  const statusEl = document.getElementById('assignmentsStatus');
  const listEl = document.getElementById('assignmentsList');
  const restrictedEl = document.getElementById('assignmentsRestricted');
  restrictedEl.hidden = true;
  statusEl.hidden = false;
  statusEl.textContent = 'Loading assignments…';
  listEl.innerHTML = '';

  try {
    const res = await fetch(`${API_BASE}/assignments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ regId: currentStudent.regId, studentName: currentStudent.name }),
    });
    const result = await res.json();

    if (!result.success) {
      statusEl.textContent = result.message || 'Could not load assignments.';
      return;
    }
    if (result.restricted) {
      statusEl.hidden = true;
      restrictedEl.hidden = false;
      restrictedEl.textContent = result.message;
      return;
    }
    currentAssignments = result.assignments || [];
    if (currentAssignments.length === 0) {
      statusEl.textContent = 'No assignments have been issued yet.';
      return;
    }
    statusEl.hidden = true;
    renderAssignments();
  } catch (err) {
    statusEl.textContent = 'Unexpected error: ' + err.message;
  }
}

function renderAssignments() {
  const listEl = document.getElementById('assignmentsList');
  listEl.innerHTML = '';

  currentAssignments.forEach(function (item, index) {
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
      row.addEventListener('click', function () { openSubmitModal(index); });
    }

    listEl.appendChild(row);
  });
}

// ======================================================================= //
// STUDENT: Submission modal
// ======================================================================= //

function openSubmitModal(index) {
  activeAssignment = currentAssignments[index];
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
// Shared Course Content row builder (used by both student and admin views)
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

function buildContentRow(item, onView) {
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
    actionBtn.addEventListener('click', onView);
  }
  actionField.appendChild(actionBtn);
  row.appendChild(actionField);

  return row;
}

function openDetailModal(item) {
  if (!item || item.locked || !item.details) return;

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

  document.getElementById('detailModal').hidden = false;
}

function closeDetailModal() {
  document.getElementById('detailModal').hidden = true;
  document.getElementById('detailVideoFrame').src = ''; // stop playback
}

// ======================================================================= //
// STUDENT: Course Content tab
// ======================================================================= //

async function loadCourseContent() {
  const statusEl = document.getElementById('courseContentStatus');
  const listEl = document.getElementById('courseContentList');
  const restrictedEl = document.getElementById('courseContentRestricted');
  restrictedEl.hidden = true;
  statusEl.hidden = false;
  statusEl.textContent = 'Loading course content…';
  listEl.innerHTML = '';

  try {
    const res = await fetch(`${API_BASE}/course-content`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ regId: currentStudent.regId }),
    });
    const result = await res.json();

    if (!result.success) {
      statusEl.textContent = result.message || 'Could not load course content.';
      return;
    }
    if (result.restricted) {
      statusEl.hidden = true;
      restrictedEl.hidden = false;
      restrictedEl.textContent = result.message;
      return;
    }
    currentCourseContent = result.entries || [];
    if (currentCourseContent.length === 0) {
      statusEl.textContent = 'No course content has been scheduled yet.';
      return;
    }
    statusEl.hidden = true;
    listEl.innerHTML = '';
    currentCourseContent.forEach(function (item) {
      listEl.appendChild(buildContentRow(item, function () { openDetailModal(item); }));
    });
  } catch (err) {
    statusEl.textContent = 'Unexpected error: ' + err.message;
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

function handleAdminLogout() {
  currentAdmin = null;
  currentAdminCourseContent = [];
  document.getElementById('adminUsername').value = '';
  document.getElementById('adminPassword').value = '';
  showScreen('landingScreen');
  activateAdminTab('adminOverviewTab');
}

// ======================================================================= //
// ADMIN: Dashboard shell
// ======================================================================= //

function showAdminDashboard() {
  showScreen('adminDashboardScreen');
  document.getElementById('adminTopbarAvatar').textContent = initials(currentAdmin.name);
  document.getElementById('adminTopbarName').textContent = currentAdmin.name || 'Admin';

  loadAdminOverview();
  loadAdminStudents();
  loadAdminCourseContent();
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
// ADMIN: Overview analytics
// ======================================================================= //

async function loadAdminOverview() {
  const statusEl = document.getElementById('adminOverviewStatus');
  const gridEl = document.getElementById('adminStatGrid');
  statusEl.hidden = false;
  statusEl.textContent = 'Loading cohort analytics…';
  gridEl.hidden = true;

  try {
    const res = await fetch(`${API_BASE}/admin/dashboard-summary`, { method: 'POST' });
    const result = await res.json();

    if (!result.success) {
      statusEl.textContent = result.message || 'Could not load cohort analytics.';
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

    statusEl.hidden = true;
    gridEl.hidden = false;
  } catch (err) {
    statusEl.textContent = 'Unexpected error: ' + err.message;
  }
}

// ======================================================================= //
// ADMIN: Students tab
// ======================================================================= //

const STATUS_PRESETS = ['Admitted', 'Pending', 'Suspended', 'Withdrawn', 'Graduated'];

async function loadAdminStudents() {
  const statusEl = document.getElementById('adminStudentsStatus');
  const tableEl = document.getElementById('adminStudentsTable');
  const bodyEl = document.getElementById('adminStudentsTableBody');
  statusEl.hidden = false;
  statusEl.textContent = 'Loading students…';
  tableEl.hidden = true;
  bodyEl.innerHTML = '';

  try {
    const res = await fetch(`${API_BASE}/admin/students`, { method: 'POST' });
    const result = await res.json();

    if (!result.success) {
      statusEl.textContent = result.message || 'Could not load students.';
      return;
    }
    const students = result.students || [];
    if (students.length === 0) {
      statusEl.textContent = 'No students found.';
      return;
    }

    students.forEach(function (student) {
      bodyEl.appendChild(buildStudentRow(student));
    });

    statusEl.hidden = true;
    tableEl.hidden = false;
  } catch (err) {
    statusEl.textContent = 'Unexpected error: ' + err.message;
  }
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
  const statusEl = document.getElementById('adminStudentsStatus');
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
      statusEl.hidden = false;
      statusEl.textContent = `Updated ${studentId} to "${newStatus}".`;
      setTimeout(function () { statusEl.hidden = true; }, 2500);
    } else {
      statusEl.hidden = false;
      statusEl.textContent = result.message || 'Could not update status.';
    }
  } catch (err) {
    statusEl.hidden = false;
    statusEl.textContent = 'Unexpected error: ' + err.message;
  } finally {
    selectEl.disabled = false;
  }
}

// ======================================================================= //
// ADMIN: Course Content tab
// ======================================================================= //

async function loadAdminCourseContent() {
  const statusEl = document.getElementById('adminCourseContentStatus');
  const listEl = document.getElementById('adminCourseContentList');
  statusEl.hidden = false;
  statusEl.textContent = 'Loading course content…';
  listEl.innerHTML = '';

  try {
    const res = await fetch(`${API_BASE}/admin/course-content`, { method: 'POST' });
    const result = await res.json();

    if (!result.success) {
      statusEl.textContent = result.message || 'Could not load course content.';
      return;
    }
    currentAdminCourseContent = result.entries || [];
    if (currentAdminCourseContent.length === 0) {
      statusEl.textContent = 'No course content has been scheduled yet.';
      return;
    }
    statusEl.hidden = true;
    listEl.innerHTML = '';
    currentAdminCourseContent.forEach(function (item) {
      listEl.appendChild(buildContentRow(item, function () { openDetailModal(item); }));
    });
  } catch (err) {
    statusEl.textContent = 'Unexpected error: ' + err.message;
  }
}
