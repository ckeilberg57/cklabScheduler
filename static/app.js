const state = {
  endpoints: [],
  meetings: [],
  config: { about_to_start_minutes: 1, poll_seconds: 3 },
  timelineOffsetHours: null,
  invitees: [],
  selectedEndpointAliases: new Set(),
  adjustmentMinutesByMeeting: {},
};

const APP_ROOT = (window.APP_ROOT || '').replace(/\/$/, '');
const API_BASE = `${APP_ROOT}/api`;

const $ = (sel) => document.querySelector(sel);

const fmt = new Intl.DateTimeFormat(undefined, {
  hour: 'numeric',
  minute: '2-digit',
});

const fullDateFmt = new Intl.DateTimeFormat(undefined, {
  weekday: 'long',
  month: 'long',
  day: 'numeric',
  year: 'numeric',
});

function randomAlias() {
  const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
  let out = 'doc';
  for (let i = 0; i < 16; i += 1) out += chars[Math.floor(Math.random() * chars.length)];
  return out;
}

function showToast(message) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2800);
}

function toLocalInputValue(date) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function escapeHtml(str) {
  return String(str)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');
}

async function api(path, options = {}) {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });

  const data = await resp.json();
  if (!resp.ok || data.ok === false) {
    throw new Error(data.error || `Request failed (${resp.status})`);
  }
  return data;
}

function overlaps(startA, endA, startB, endB) {
  return startA < endB && endA > startB;
}

function getSelectedWindow() {
  const startValue = $('#startTime')?.value;
  const endValue = $('#endTime')?.value;

  if (!startValue || !endValue) return null;

  const start = new Date(startValue);
  const end = new Date(endValue);

  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null;
  return { start, end };
}

function getEndpointScheduleStatus(endpointAlias, currentMeetingId = null) {
  const window = getSelectedWindow();
  if (!window) return { busy: false, reason: '' };

  for (const meeting of state.meetings) {
    if (currentMeetingId && meeting.id === currentMeetingId) continue;

    const meetingStart = new Date(meeting.start_time);
    const meetingEnd = new Date(meeting.end_time);

    if (!overlaps(window.start, window.end, meetingStart, meetingEnd)) continue;

    const assigned = (meeting.endpoints || []).some(
      (ep) => (ep.endpoint_alias || '').toLowerCase() === (endpointAlias || '').toLowerCase()
    );

    if (assigned) {
      return {
        busy: true,
        reason: `${meeting.title || meeting.meeting_alias} • ${meetingStart.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}–${meetingEnd.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`,
      };
    }
  }

  return { busy: false, reason: '' };
}


function isValidEmail(email) {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(email || '').trim());
}

function injectInviteeSection() {
  const endpointList = $('#endpointList');
  if (!endpointList || $('#inviteeSection')) return;

  const section = document.createElement('div');
  section.id = 'inviteeSection';
  section.className = 'invitee-section';
  section.innerHTML = `
    <div class="section-head slim">
      <h3>WebRTC email participants</h3>
    </div>
    <label>
      Participant email
      <div class="inline-input">
        <input type="email" id="inviteeEmail" placeholder="participant@example.com" />
        <button type="button" id="addInvitee" class="secondary">Add</button>
      </div>
    </label>
    <div id="inviteeList" class="invitee-list"></div>
  `;

  endpointList.insertAdjacentElement('afterend', section);

  $('#addInvitee').onclick = addInviteeFromInput;
  $('#inviteeEmail').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      addInviteeFromInput();
    }
  });

  renderInvitees();
}

function addInviteeFromInput() {
  const input = $('#inviteeEmail');
  const email = (input?.value || '').trim();
  if (!email) return;

  if (!isValidEmail(email)) {
    showToast('Enter a valid email address.');
    return;
  }

  const exists = state.invitees.some((item) => item.email.toLowerCase() === email.toLowerCase());
  if (exists) {
    showToast('That email is already added.');
    return;
  }

  state.invitees.push({ email, display_name: '' });
  input.value = '';
  renderInvitees();
}

function removeInvitee(email) {
  state.invitees = state.invitees.filter((item) => item.email !== email);
  renderInvitees();
}

function renderInvitees() {
  const list = $('#inviteeList');
  if (!list) return;

  if (!state.invitees.length) {
    list.innerHTML = '<div class="empty">No WebRTC email participants added.</div>';
    return;
  }

  list.innerHTML = state.invitees.map((item) => `
    <div class="invitee-row">
      <span>${escapeHtml(item.email)}</span>
      <button type="button" class="tiny-btn remove-invitee" data-email="${escapeHtml(item.email)}">Remove</button>
    </div>
  `).join('');

  list.querySelectorAll('.remove-invitee').forEach((btn) => {
    btn.onclick = () => removeInvitee(btn.dataset.email);
  });
}

function renderInviteeChips(invitees, meetingId) {
  if (!invitees || !invitees.length) {
    return '<span class="muted">No WebRTC email participants</span>';
  }

  return invitees.map((inv) => `
    <div class="chip-row">
      <span class="chip">${escapeHtml(inv.email)} • ${escapeHtml(inv.email_status || 'pending')}</span>
      <button type="button" class="tiny-btn resend-invite-btn" data-meeting-id="${meetingId}" data-invitee-id="${inv.id}">Resend invite</button>
      ${inv.join_url ? `<a class="tiny-btn" href="${escapeHtml(inv.join_url)}" target="_blank" rel="noopener noreferrer">Open URL</a>` : ''}
    </div>
  `).join('');
}

async function resendInvite(meetingId, inviteeId) {
  try {
    await api(`/meetings/${meetingId}/invitees/${inviteeId}/resend`, { method: 'POST' });
    showToast('Invite resent.');
    await loadMeetings();
  } catch (err) {
    showToast(err.message);
  }
}

async function loadConfig() {
  const data = await api('/config');
  state.config = { ...state.config, ...data };
}

async function loadEndpoints() {
  const list = $('#endpointList');

  if (!state.endpoints.length) {
    list.innerHTML = '<div class="empty">Loading registered endpoints...</div>';
  }

  const data = await api('/endpoints');
  state.endpoints = data.items || [];
  renderEndpoints();
}

async function loadMeetings() {
  const date = $('#dayPicker').value;
  const data = await api(`/meetings?date=${encodeURIComponent(date)}`);
  state.meetings = data.items || [];
  renderStats();
  renderTimeline();
  renderCards();
  renderEndpoints();
}


function rememberEndpointSelections() {
  document.querySelectorAll('.endpoint-check').forEach((box) => {
    if (!box.value) return;
    if (box.checked) {
      state.selectedEndpointAliases.add(box.value);
    } else {
      state.selectedEndpointAliases.delete(box.value);
    }
  });
}

function syncEndpointCheckboxSelection(box) {
  if (!box || !box.value) return;
  if (box.checked) {
    state.selectedEndpointAliases.add(box.value);
  } else {
    state.selectedEndpointAliases.delete(box.value);
  }
}

function renderEndpoints() {
  const list = $('#endpointList');
  const tpl = $('#endpointTemplate');
  if (!list || !tpl) return;

  rememberEndpointSelections();

  list.innerHTML = '';

  if (!state.endpoints.length) {
    list.innerHTML = '<div class="empty">No registered endpoints were returned from Pexip.</div>';
    return;
  }

  state.endpoints.forEach((ep) => {
    const scheduleStatus = getEndpointScheduleStatus(ep.alias);
    const node = tpl.content.cloneNode(true);
    const check = node.querySelector('.endpoint-check');
    const name = node.querySelector('.endpoint-name');
    const sub = node.querySelector('.endpoint-sub');

    check.value = ep.alias || '';
    check.dataset.displayName = ep.display_name || ep.alias || '';
    check.disabled = scheduleStatus.busy;
    check.checked = state.selectedEndpointAliases.has(ep.alias || '');

    if (check.disabled) {
      check.checked = false;
      state.selectedEndpointAliases.delete(ep.alias || '');
    }

    check.addEventListener('change', () => syncEndpointCheckboxSelection(check));

    name.textContent = ep.display_name || ep.alias || 'Unknown endpoint';

    const base = `${ep.alias || ''}${ep.protocol ? ` • ${ep.protocol}` : ''}`;
    const statusText = scheduleStatus.busy
      ? ` • BUSY • ${scheduleStatus.reason}`
      : ' • FREE';

    sub.textContent = `${base}${statusText}`;

    list.appendChild(node);
  });
}

function renderStats() {
  const counts = { scheduled: 0, warning: 0, started: 0, ended: 0 };
  sortedMeetingQueue().forEach((m) => {
    const status = m.timeline_status || m.status;
    if (status === 'ended') counts.ended += 1;
    else if (status === 'started') counts.started += 1;
    else if (status === 'about_to_start') counts.warning += 1;
    else counts.scheduled += 1;
  });

  $('#countScheduled').textContent = counts.scheduled;
  $('#countWarning').textContent = counts.warning;
  $('#countStarted').textContent = counts.started;
  $('#countEnded').textContent = counts.ended;
}

function getTimelineAnchor() {
  const now = new Date();
  now.setMinutes(Math.floor(now.getMinutes() / 15) * 15, 0, 0);

  if (state.timelineOffsetHours === null) {
    const anchor = new Date(now);
    anchor.setHours(anchor.getHours() - 1, 0, 0, 0);
    return anchor;
  }

  const anchor = new Date(now);
  anchor.setHours(anchor.getHours() - 1 + state.timelineOffsetHours, 0, 0, 0);
  return anchor;
}

function shiftTimeline(hours) {
  if (state.timelineOffsetHours === null) state.timelineOffsetHours = 0;
  state.timelineOffsetHours += hours;
  renderTimeline();
}

function statusLabel(ep, meeting) {
  if (ep.live) return 'LIVE';
  if ((meeting.timeline_status || meeting.status) === 'started') return 'NOT CONNECTED';
  return ep.status || 'scheduled';
}


function canEditMeeting(meeting) {
  return !meeting.started_at && !(meeting.timeline_status === 'ended' || meeting.status === 'ended');
}

function meetingQueueRank(meeting) {
  const status = meeting.timeline_status || meeting.status;
  if (status === 'started') return 0;
  if (status === 'about_to_start') return 1;
  if (status === 'scheduled') return 2;
  if (status === 'ended') return 3;
  return 4;
}

function sortedMeetingQueue() {
  return [...state.meetings].sort((a, b) => {
    const rankDiff = meetingQueueRank(a) - meetingQueueRank(b);
    if (rankDiff !== 0) return rankDiff;
    return new Date(a.start_time) - new Date(b.start_time);
  });
}

function getAdjustmentMinutes(meetingId) {
  return state.adjustmentMinutesByMeeting[meetingId] ?? 15;
}

function setAdjustmentMinutes(meetingId, minutes) {
  state.adjustmentMinutesByMeeting[meetingId] = Number(minutes);
  document.querySelectorAll(`[data-adjust-label="${meetingId}"]`).forEach((label) => {
    const value = Number(minutes);
    label.textContent = value > 0 ? `+${value} min` : `${value} min`;
  });
}

function adjustmentControl(meetingId) {
  const value = getAdjustmentMinutes(meetingId);
  const label = value > 0 ? `+${value} min` : `${value} min`;
  return `
    <div class="adjust-control">
      <label>Adjust end time <strong data-adjust-label="${meetingId}">${label}</strong></label>
      <input type="range" min="-120" max="180" step="15" value="${value}" data-action="adjust-range" data-meeting-id="${meetingId}" />
      <button type="button" data-action="adjust-apply" data-meeting-id="${meetingId}">Apply</button>
    </div>
  `;
}

function renderTimeline() {
  const hours = $('#timelineHours');
  const canvas = $('#timelineCanvas');
  const dayPicker = $('#dayPicker');
  const headerDate = $('#headerDate');

  hours.innerHTML = '';
  canvas.innerHTML = '';

  const selectedDate = new Date(`${dayPicker.value}T00:00:00`);
  headerDate.textContent = fullDateFmt.format(selectedDate);

  const windowStart = getTimelineAnchor();
  const windowEnd = new Date(windowStart.getTime() + 3 * 60 * 60 * 1000);

  for (let i = 0; i < 3; i += 1) {
    const cell = document.createElement('div');
    const labelTime = new Date(windowStart.getTime() + i * 60 * 60 * 1000);
    cell.textContent = labelTime.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    hours.appendChild(cell);
  }

  const grid = document.createElement('div');
  grid.className = 'timeline-grid quarter-hour-grid';
  for (let i = 0; i < 12; i += 1) {
    const span = document.createElement('span');
    span.className = i % 4 === 0 ? 'hour-mark' : 'quarter-mark';
    grid.appendChild(span);
  }
  canvas.appendChild(grid);

  const now = new Date();
  if (now >= windowStart && now <= windowEnd) {
    const nowLine = document.createElement('div');
    nowLine.className = 'time-now';
    nowLine.style.left = `${((now - windowStart) / (3 * 60 * 60 * 1000)) * 100}%`;
    canvas.appendChild(nowLine);
  }

  const visibleMeetings = state.meetings
    .map((m) => ({ ...m, _start: new Date(m.start_time), _end: new Date(m.end_time) }))
    .filter((m) => m._end > windowStart && m._start < windowEnd)
    .sort((a, b) => a._start - b._start);

  const rowEndTimes = [];
  const rowHeight = 72;
  const topOffset = 16;

  visibleMeetings.forEach((m) => {
    const clippedStart = m._start < windowStart ? windowStart : m._start;
    const clippedEnd = m._end > windowEnd ? windowEnd : m._end;
    const left = ((clippedStart - windowStart) / (3 * 60 * 60 * 1000)) * 100;
    const width = Math.max(3, ((clippedEnd - clippedStart) / (3 * 60 * 60 * 1000)) * 100);

    let rowIndex = rowEndTimes.findIndex((rowEnd) => m._start >= rowEnd);
    if (rowIndex === -1) {
      rowIndex = rowEndTimes.length;
      rowEndTimes.push(m._end);
    } else {
      rowEndTimes[rowIndex] = m._end;
    }

    const liveNames = (m.live_participants || []).map((p) => {
      const label = p.display_name || p.remote_alias || 'Unknown';
      const ip = (p.remote_ip || '').trim();
      return ip ? `${label} (${ip})` : label;
    }).join(', ') || 'No live participants';

    const block = document.createElement('div');
    block.className = `meeting-block ${m.timeline_status || m.status}`;
    block.style.left = `${left}%`;
    block.style.width = `${width}%`;
    block.style.top = `${topOffset + rowIndex * rowHeight}px`;
    block.innerHTML = `
      <strong>${escapeHtml(m.title)}</strong>
      <div class="meeting-meta">${fmt.format(m._start)} – ${fmt.format(m._end)} • ${escapeHtml(m.meeting_alias)}</div>
      <div class="meeting-hover-card">
        <div><strong>${escapeHtml(m.title)}</strong></div>
        <div>${fmt.format(m._start)} – ${fmt.format(m._end)}</div>
        <div>${escapeHtml(m.meeting_alias)}</div>
        <div><strong>Assigned:</strong> ${(m.endpoints || []).map((ep) => `${escapeHtml(ep.display_name || ep.endpoint_alias)} • ${escapeHtml(statusLabel(ep, m))}`).join(', ') || 'None'}</div>
        <div><strong>Live participants:</strong> ${escapeHtml(liveNames)}</div>
        <div>${escapeHtml(m.notes || 'No notes entered.')}</div>
        <div class="popup-actions">
          ${(m.timeline_status || m.status) !== 'ended' ? adjustmentControl(m.id) : ''}
          ${canEditMeeting(m) ? `<button type="button" data-action="edit" data-meeting-id="${m.id}">Edit</button>` : ''}
          ${(m.timeline_status || m.status) === 'ended' ? `<a class="tiny-btn" href="${API_BASE}/meetings/${m.id}/export" target="_blank" rel="noopener noreferrer">Export details</a>` : ''}
          <button type="button" data-action="delete" data-meeting-id="${m.id}">Delete</button>
        </div>
      </div>
    `;
    canvas.appendChild(block);
  });

  canvas.style.minHeight = `${Math.max(150, topOffset + Math.max(1, rowEndTimes.length) * rowHeight + 24)}px`;

  canvas.querySelectorAll('[data-action]').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = Number(btn.dataset.meetingId);
      const action = btn.dataset.action;
      if (action === 'adjust-range') setAdjustmentMinutes(id, btn.value);
      if (action === 'adjust-apply') adjustMeeting(id, getAdjustmentMinutes(id));
      if (action === 'delete') deleteMeeting(id);
      if (action === 'edit') openEdit(id);
    });
  });
}

function renderCards() {
  const wrap = $('#meetingCards');
  wrap.innerHTML = '';

  if (!state.meetings.length) {
    wrap.innerHTML = '<div class="empty">No meetings scheduled for this day yet.</div>';
    return;
  }

  sortedMeetingQueue().forEach((m) => {
    const timelineState = m.timeline_status || m.status;
    const card = document.createElement('div');
    card.className = 'card';

    const start = new Date(m.start_time);
    const end = new Date(m.end_time);

    card.innerHTML = `
      <div class="card-top">
        <div>
          <h3>${escapeHtml(m.title)}</h3>
          <p>${fmt.format(start)} – ${fmt.format(end)} • ${escapeHtml(m.meeting_alias)}</p>
        </div>
        <span class="pill ${timelineState}">${escapeHtml(String(timelineState).replaceAll('_', ' '))}</span>
      </div>
      <div class="muted">${escapeHtml(m.notes || 'No notes entered.')}</div>

      <div class="subhead">Assigned endpoints</div>
      <div class="endpoint-chips">
        ${(m.endpoints || []).map((ep) => `
          <div class="chip-row">
            <span class="chip">${escapeHtml(ep.display_name || ep.endpoint_alias)} • ${escapeHtml(statusLabel(ep, m))}</span>
            ${!ep.live && timelineState === 'started' ? `<button type="button" class="tiny-btn redial-btn" data-meeting-id="${m.id}" data-endpoint-alias="${escapeHtml(ep.endpoint_alias)}">Dial again</button>` : ''}
          </div>
        `).join('')}
      </div>

      <div class="subhead">Live participants</div>
      <div class="endpoint-chips">
        ${(m.live_participants || []).length
          ? (m.live_participants || []).map((p) => {
              const label = escapeHtml(p.display_name || p.remote_alias || 'Unknown');
              const ip = (p.remote_ip || '').trim();
              const ipHtml = ip
                ? ` <a class="ip-link" href="https://${encodeURI(ip)}" target="_blank" rel="noopener noreferrer">${escapeHtml(ip)}</a>`
                : '';
              return `<span class="chip live-chip">${label}${ipHtml}</span>`;
            }).join('')
          : '<span class="muted">No live participants</span>'}
      </div>

      <div class="subhead">WebRTC email participants</div>
      <div class="endpoint-chips">
        ${renderInviteeChips(m.invitees || [], m.id)}
      </div>

      <div class="card-actions">
        ${timelineState !== 'ended' ? adjustmentControl(m.id) : ''}
        ${canEditMeeting(m) ? '<button type="button" data-action="edit">Edit</button>' : ''}
        ${timelineState === 'ended' ? `<a class="tiny-btn" href="${API_BASE}/meetings/${m.id}/export" target="_blank" rel="noopener noreferrer">Export details</a>` : ''}
        <button type="button" data-action="delete">Delete</button>
      </div>
    `;

    card.querySelectorAll('[data-action]').forEach((btn) => {
      btn.onclick = () => {
        const action = btn.dataset.action;
        if (action === 'adjust-range') setAdjustmentMinutes(m.id, btn.value);
        if (action === 'adjust-apply') adjustMeeting(m.id, getAdjustmentMinutes(m.id));
        if (action === 'delete') deleteMeeting(m.id);
        if (action === 'edit') openEdit(m.id);
      };
    });

    card.querySelectorAll('.redial-btn').forEach((btn) => {
      btn.onclick = (e) => {
        e.stopPropagation();
        redialEndpoint(btn.dataset.meetingId, btn.dataset.endpointAlias);
      };
    });

    card.querySelectorAll('.resend-invite-btn').forEach((btn) => {
      btn.onclick = (e) => {
        e.stopPropagation();
        resendInvite(btn.dataset.meetingId, btn.dataset.inviteeId);
      };
    });

    wrap.appendChild(card);
  });
}

async function createMeeting(e) {
  e.preventDefault();
  rememberEndpointSelections();
  const checked = [...document.querySelectorAll('.endpoint-check:checked')].map((box) => ({
    alias: box.value,
    display_name: box.dataset.displayName,
    role: 'host',
  }));

  const payload = {
    title: $('#title').value.trim(),
    meeting_alias: $('#meetingAlias').value.trim(),
    start_time: new Date($('#startTime').value).toISOString(),
    end_time: new Date($('#endTime').value).toISOString(),
    notes: $('#notes').value.trim(),
    endpoints: checked,
    invitees: state.invitees,
  };

  try {
    await api('/meetings', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    showToast('Meeting scheduled.');
    document.querySelectorAll('.endpoint-check').forEach((c) => { c.checked = false; });
    state.selectedEndpointAliases.clear();
    $('#meetingAlias').value = '';
    state.invitees = [];
    renderInvitees();
    await loadMeetings();
  } catch (err) {
    showToast(err.message);
  }
}

async function adjustMeeting(id, minutes) {
  const value = Number(minutes);
  if (!value) {
    showToast('Select a non-zero adjustment.');
    return;
  }
  try {
    await api(`/meetings/${id}/extend`, {
      method: 'POST',
      body: JSON.stringify({ minutes: value }),
    });
    showToast(value > 0 ? `Meeting extended by ${value} minutes.` : `Meeting shortened by ${Math.abs(value)} minutes.`);
    state.adjustmentMinutesByMeeting[id] = 15;
    await loadMeetings();
  } catch (err) {
    showToast(err.message);
  }
}

async function deleteMeeting(id) {
  try {
    await api(`/meetings/${id}/delete`, { method: 'POST' });
    showToast('Meeting deleted.');
    await loadMeetings();
  } catch (err) {
    showToast(err.message);
  }
}

async function redialEndpoint(meetingId, endpointAlias) {
  try {
    await api(`/meetings/${meetingId}/redial_endpoint`, {
      method: 'POST',
      body: JSON.stringify({ endpoint_alias: endpointAlias }),
    });
    showToast(`Dial again requested for ${endpointAlias}`);
    await loadMeetings();
  } catch (err) {
    showToast(err.message);
  }
}

function setDefaultTimes() {
  const now = new Date();
  now.setMinutes(Math.ceil(now.getMinutes() / 15) * 15, 0, 0);
  const later = new Date(now.getTime() + 60 * 60 * 1000);
  $('#startTime').value = toLocalInputValue(now);
  $('#endTime').value = toLocalInputValue(later);
}

function setToday() {
  const today = new Date();
  $('#dayPicker').value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
}

function openEdit(meetingId) {
  const meeting = state.meetings.find((m) => m.id === meetingId);
  if (!meeting) return;

  $('#editMeetingId').value = String(meetingId);
  $('#editNotes').value = meeting.notes || '';

  const list = $('#editEndpointList');
  list.innerHTML = '';
  const assigned = new Set((meeting.endpoints || []).map((ep) => ep.endpoint_alias));
  window.currentEditInvitees = (meeting.invitees || []).map((inv) => ({
    email: inv.email,
    display_name: inv.display_name || '',
  }));

  state.endpoints.forEach((ep) => {
    const row = document.createElement('label');
    row.className = 'endpoint-item light-item';
    row.innerHTML = `
      <input type="checkbox" class="edit-endpoint-check" value="${escapeHtml(ep.alias)}" data-display-name="${escapeHtml(ep.display_name || ep.alias)}" ${assigned.has(ep.alias) ? 'checked' : ''} />
      <div>
        <strong>${escapeHtml(ep.display_name || ep.alias)}</strong>
        <div class="endpoint-sub">${escapeHtml(ep.alias || '')}</div>
      </div>
    `;
    list.appendChild(row);
  });

  let editInviteeWrap = $('#editInviteeWrap');
  if (!editInviteeWrap) {
    editInviteeWrap = document.createElement('div');
    editInviteeWrap.id = 'editInviteeWrap';
    editInviteeWrap.innerHTML = `
      <div class="section-head slim">
        <h3>WebRTC email participants</h3>
      </div>
      <label>
        Participant email
        <div class="inline-input">
          <input type="email" id="editInviteeEmail" placeholder="participant@example.com" />
          <button type="button" id="addEditInvitee" class="secondary">Add</button>
        </div>
      </label>
      <div id="editInviteeList" class="invitee-list"></div>
    `;
    $('#editEndpointList').insertAdjacentElement('afterend', editInviteeWrap);
  }

  function renderEditInvitees() {
    const editList = $('#editInviteeList');
    if (!editList) return;
    if (!window.currentEditInvitees.length) {
      editList.innerHTML = '<div class="empty">No WebRTC email participants added.</div>';
      return;
    }
    editList.innerHTML = window.currentEditInvitees.map((item) => `
      <div class="invitee-row">
        <span>${escapeHtml(item.email)}</span>
        <button type="button" class="tiny-btn remove-edit-invitee" data-email="${escapeHtml(item.email)}">Remove</button>
      </div>
    `).join('');
    editList.querySelectorAll('.remove-edit-invitee').forEach((btn) => {
      btn.onclick = () => {
        window.currentEditInvitees = window.currentEditInvitees.filter((item) => item.email !== btn.dataset.email);
        renderEditInvitees();
      };
    });
  }

  $('#addEditInvitee').onclick = () => {
    const input = $('#editInviteeEmail');
    const email = (input?.value || '').trim();
    if (!isValidEmail(email)) {
      showToast('Enter a valid email address.');
      return;
    }
    if (window.currentEditInvitees.some((item) => item.email.toLowerCase() === email.toLowerCase())) {
      showToast('That email is already added.');
      return;
    }
    window.currentEditInvitees.push({ email, display_name: '' });
    input.value = '';
    renderEditInvitees();
  };

  renderEditInvitees();

  const dlg = $('#editDialog');
  if (dlg.showModal) dlg.showModal();
  else dlg.setAttribute('open', 'open');
}

async function saveEdit() {
  const meetingId = Number($('#editMeetingId').value);
  const endpoints = [...document.querySelectorAll('.edit-endpoint-check:checked')].map((box) => ({
    alias: box.value,
    display_name: box.dataset.displayName,
    role: 'host',
  }));

  try {
    await api(`/meetings/${meetingId}/update`, {
      method: 'POST',
      body: JSON.stringify({
        endpoints,
        invitees: window.currentEditInvitees || [],
        notes: $('#editNotes').value.trim(),
      }),
    });
    showToast('Meeting updated.');
    closeEdit();
    await loadMeetings();
  } catch (err) {
    showToast(err.message);
  }
}

function closeEdit() {
  const dlg = $('#editDialog');
  if (dlg.close) dlg.close();
  else dlg.removeAttribute('open');
}


function dateStringLocal(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function getExportRangeDates(range) {
  const selected = $('#dayPicker')?.value;
  const now = new Date();

  if (range === 'selected_day') {
    return { start: selected, end: selected };
  }

  if (range === 'today') {
    const today = dateStringLocal(now);
    return { start: today, end: today };
  }

  if (range === 'last_7') {
    const end = new Date(now);
    const start = new Date(now);
    start.setDate(start.getDate() - 6);
    return { start: dateStringLocal(start), end: dateStringLocal(end) };
  }

  if (range === 'this_month') {
    const start = new Date(now.getFullYear(), now.getMonth(), 1);
    const end = new Date(now.getFullYear(), now.getMonth() + 1, 0);
    return { start: dateStringLocal(start), end: dateStringLocal(end) };
  }

  return null;
}

function exportMeetingsRange(start, end) {
  if (!start || !end) {
    showToast('Choose a start and end date.');
    return;
  }

  const url = `${API_BASE}/export/meetings?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`;
  window.location.href = url;
}

function openExportDialog() {
  const selected = $('#dayPicker')?.value || dateStringLocal(new Date());
  $('#exportStartDate').value = selected;
  $('#exportEndDate').value = selected;

  const dlg = $('#exportDialog');
  if (dlg.showModal) dlg.showModal();
  else dlg.setAttribute('open', 'open');
}

function closeExportDialog() {
  const dlg = $('#exportDialog');
  if (dlg.close) dlg.close();
  else dlg.removeAttribute('open');
}

function handleExportClick() {
  const range = $('#exportRange')?.value || 'selected_day';
  if (range === 'custom') {
    openExportDialog();
    return;
  }

  const dates = getExportRangeDates(range);
  exportMeetingsRange(dates.start, dates.end);
}

async function init() {
  $('#generateAlias').onclick = () => { $('#meetingAlias').value = randomAlias(); };

  $('#refreshEndpoints').onclick = async () => {
    try {
      await loadEndpoints();
      showToast('Endpoints refreshed.');
    } catch (err) {
      showToast(err.message);
    }
  };

  $('#meetingForm').addEventListener('submit', createMeeting);

  if ($('#exportMeetings')) {
    $('#exportMeetings').onclick = handleExportClick;
  }

  if ($('#runCustomExport')) {
    $('#runCustomExport').onclick = () => {
      exportMeetingsRange($('#exportStartDate').value, $('#exportEndDate').value);
    };
  }

  if ($('#closeExport')) {
    $('#closeExport').onclick = closeExportDialog;
  }
  $('#jumpToday').onclick = async () => {
    setToday();
    await loadMeetings();
  };
  $('#dayPicker').addEventListener('change', loadMeetings);

  $('#timelineBack').onclick = () => shiftTimeline(-1);
  $('#timelineForward').onclick = () => shiftTimeline(1);
  $('#timelineNow').onclick = () => {
    state.timelineOffsetHours = null;
    renderTimeline();
  };

  $('#saveEdit').onclick = saveEdit;
  $('#closeEdit').onclick = closeEdit;

  $('#startTime').addEventListener('change', renderEndpoints);
  $('#endTime').addEventListener('change', renderEndpoints);

  setToday();
  setDefaultTimes();
  injectInviteeSection();

  try {
    await loadConfig();
    await loadEndpoints();
    await loadMeetings();
  } catch (err) {
    showToast(err.message);
  }

  setInterval(async () => {
    try {
      await loadMeetings();
    } catch (err) {
      console.error(err);
    }
  }, 3000);

  setInterval(async () => {
    try {
      await loadEndpoints();
    } catch (err) {
      console.error(err);
    }
  }, 5 * 60 * 1000);

  setInterval(() => {
    if (state.timelineOffsetHours === null) {
      renderTimeline();
    }
  }, 15 * 60 * 1000);
}

init();