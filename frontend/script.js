// All calls go through /api/... — in production, Netlify's redirect proxy
// (see netlify.toml) forwards these to the Render backend, so this stays a
// same-origin path with no CORS involved from the browser's point of view.
const API_BASE = '/api';

let currentStudent = null;
let currentAssignments = [];
let activeAssignment = null;

document.addEventListener('DOMContentLoaded', function () {
  document.getElementById('loginForm').addEventListener('submit', handleLogin);
  document.getElementById('logoutBtn').addEventListener('click', handleLogout);
  document.getElementById('closeModalBtn').addEventListener('click', closeModal);
  document.getElementById('submitForm').addEventListener('submit', handleSubmitAssignment);

  document.querySelectorAll('.tab-btn').forEach(function (btn) {
    btn.addEventListener('click', function () { activateTab(btn.dataset.tab); });
  });

  document.getElementById('submitModal').addEventListener('click', function (e) {
    if (e.target === this) closeModal();
  });
});

// --------------------------------------------------------------------- //
// Login
// --------------------------------------------------------------------- //

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
      showDashboard();
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
  document.getElementById('username').value = '';
  document.getElementById('password').value = '';
  document.getElementById('dashboardScreen').hidden = true;
  document.getElementById('loginScreen').hidden = false;
  activateTab('overviewTab');
}

// --------------------------------------------------------------------- //
// Dashboard
// --------------------------------------------------------------------- //

function showDashboard() {
  document.getElementById('loginScreen').hidden = true;
  document.getElementById('dashboardScreen').hidden = false;

  document.getElementById('welcomeName').textContent = currentStudent.name || 'Student';
  document.getElementById('infoRegId').textContent = currentStudent.regId || '—';
  document.getElementById('infoName').textContent = currentStudent.name || '—';
  document.getElementById('infoGender').textContent = currentStudent.gender || '—';
  document.getElementById('infoCourse').textContent = currentStudent.course || '—';

  loadAssignments();
}

function activateTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(function (b) {
    b.classList.toggle('active', b.dataset.tab === tabId);
  });
  document.querySelectorAll('.tab-panel').forEach(function (p) {
    p.classList.toggle('active', p.id === tabId);
  });
}

// --------------------------------------------------------------------- //
// Assignments
// --------------------------------------------------------------------- //

async function loadAssignments() {
  const statusEl = document.getElementById('assignmentsStatus');
  const listEl = document.getElementById('assignmentsList');
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

    const course = document.createElement('div');
    course.className = 'assignment-course';
    course.textContent = item.course || 'Course';

    const snippet = document.createElement('div');
    snippet.className = 'assignment-snippet';
    snippet.textContent = item.content;

    main.appendChild(course);
    main.appendChild(snippet);

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

// --------------------------------------------------------------------- //
// Submission modal
// --------------------------------------------------------------------- //

function openSubmitModal(index) {
  activeAssignment = currentAssignments[index];
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
