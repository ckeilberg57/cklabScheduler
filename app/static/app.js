const state = {
  endpoints: [],
  meetings: [],
  config: { about_to_start_minutes: 1, poll_seconds: 3 },
  timelineOffsetHours: null,
  invitees: [],
  selectedEndpointAliases: new Set(),
  adjustmentMinutesByMeeting: {},
};

const APP_ROOT = (document.querySelector('meta[name="app-root"]')?.content || '').replace(/\/$/, '');
const API_BASE = `${APP_ROOT}/api`;
const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]')?.content || '';

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

function safeErrorMessage(error) {
  if (error instanceof Error) {
    return error.message || 'An unexpected error occurred.';
  }
  return String(error || 'An unexpected error occurred.');
}

function showToast(message) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = String(message ?? '');
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

function safeHref(url) {
  try {
    const parsed = new URL(String(url || ''));
    return parsed.protocol === 'https:' ? parsed.href : null;
  } catch {
    return null;
  }
}

async function api(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (method !== 'GET' && method !== 'HEAD') {
    headers['X-CSRFToken'] = CSRF_TOKEN;
  }

  const resp = await fetch(`${API_BASE}${path}`, { ...options, headers });

  if (resp.status === 401) {
    window.location.href = `${APP_ROOT}/login`;
    return;
  }

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

  const head = document.createElement('div');
  head.className = 'section-head slim';
  const headH3 = document.createElement('h3');
  headH3.textContent = 'WebRTC email participants';
  head.appendChild(headH3);
  section.appendChild(head);

  const emailLabel = document.createElement('label');
  emailLabel.textContent = 'Participant email';
  const inlineDiv = document.createElement('div');
  inlineDiv.className = 'inline-input';
  const emailInput = document.createElement('input');
  emailInput.type = 'email';
  emailInput.id = 'inviteeEmail';
  emailInput.placeholder = 'participant@example.com';
  const addBtn = document.createElement('button');
  addBtn.type = 'button';
  addBtn.id = 'addInvitee';
  addBtn.className = 'secondary';
  addBtn.textContent = 'Add';
  inlineDiv.appendChild(emailInput);
  inlineDiv.appendChild(addBtn);
  emailLabel.appendChild(inlineDiv);
  section.appendChild(emailLabel);

  const listDiv = document.createElement('div');
  listDiv.id = 'inviteeList';
  listDiv.className = 'invitee-list';
  section.appendChild(listDiv);

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

  list.textContent = '';
  if (!state.invitees.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'No WebRTC email participants added.';
    list.appendChild(empty);
    return;
  }

  state.invitees.forEach((item) => {
    const row = document.createElement('div');
    row.className = 'invitee-row';
    const span = document.createElement('span');
    span.textContent = item.email;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'tiny-btn remove-invitee';
    btn.dataset.email = item.email;
    btn.textContent = 'Remove';
    btn.onclick = () => removeInvitee(item.email);
    row.appendChild(span);
    row.appendChild(btn);
    list.appendChild(row);
  });
}

function buildInviteeChips(container, invitees, meetingId) {
  container.replaceChildren();
  if (!invitees || !invitees.length) {
    const muted = document.createElement('span');
    muted.className = 'muted';
    muted.textContent = 'No WebRTC email participants';
    container.appendChild(muted);
    return;
  }
  invitees.forEach((inv) => {
    const row = document.createElement('div');
    row.className = 'chip-row';

    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = `${inv.email} • ${inv.email_status || 'pending'}`;
    row.appendChild(chip);

    const resendBtn = document.createElement('button');
    resendBtn.type = 'button';
    resendBtn.className = 'tiny-btn resend-invite-btn';
    resendBtn.dataset.meetingId = meetingId;
    resendBtn.dataset.inviteeId = inv.id;
    resendBtn.textContent = 'Resend invite';
    row.appendChild(resendBtn);

    const joinHref = safeHref(inv.join_url);
    if (joinHref) {
      const link = document.createElement('a');
      link.className = 'tiny-btn';
      link.href = joinHref;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = 'Open URL';
      row.appendChild(link);
    }

    container.appendChild(row);
  });
}

async function resendInvite(meetingId, inviteeId) {
  try {
    await api(`/meetings/${meetingId}/invitees/${inviteeId}/resend`, { method: 'POST' });
    showToast('Invite resent.');
    await loadMeetings();
  } catch (err) {
    showToast(safeErrorMessage(err));
  }
}

async function loadConfig() {
  const data = await api('/config');
  state.config = { ...state.config, ...data };
}

async function loadEndpoints() {
  const list = $('#endpointList');

  if (!state.endpoints.length) {
    const loading = document.createElement('div');
    loading.className = 'empty';
    loading.textContent = 'Loading registered endpoints...';
    list.replaceChildren(loading);
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

  list.replaceChildren();

  if (!state.endpoints.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'No registered endpoints were returned from Pexip.';
    list.appendChild(empty);
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

  const wrap = document.createElement('div');
  wrap.className = 'adjust-control';

  const lbl = document.createElement('label');
  lbl.appendChild(document.createTextNode('Adjust end time '));
  const strong = document.createElement('strong');
  strong.dataset.adjustLabel = meetingId;
  strong.textContent = label;
  lbl.appendChild(strong);

  const range = document.createElement('input');
  range.type = 'range';
  range.min = '-120';
  range.max = '180';
  range.step = '15';
  range.value = String(value);
  range.dataset.action = 'adjust-range';
  range.dataset.meetingId = meetingId;

  const applyBtn = document.createElement('button');
  applyBtn.type = 'button';
  applyBtn.dataset.action = 'adjust-apply';
  applyBtn.dataset.meetingId = meetingId;
  applyBtn.textContent = 'Apply';

  wrap.appendChild(lbl);
  wrap.appendChild(range);
  wrap.appendChild(applyBtn);
  return wrap;
}

function renderTimeline() {
  const hours = $('#timelineHours');
  const canvas = $('#timelineCanvas');
  const dayPicker = $('#dayPicker');
  const headerDate = $('#headerDate');

  hours.replaceChildren();
  canvas.replaceChildren();

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

    const titleStrong = document.createElement('strong');
    titleStrong.textContent = m.title;
    block.appendChild(titleStrong);

    const metaDiv = document.createElement('div');
    metaDiv.className = 'meeting-meta';
    metaDiv.textContent = `${fmt.format(m._start)} – ${fmt.format(m._end)} • ${m.meeting_alias}`;
    block.appendChild(metaDiv);

    const hoverCard = document.createElement('div');
    hoverCard.className = 'meeting-hover-card';

    const hDiv = document.createElement('div');
    const hStrong = document.createElement('strong');
    hStrong.textContent = m.title;
    hDiv.appendChild(hStrong);
    hoverCard.appendChild(hDiv);

    const timeDiv = document.createElement('div');
    timeDiv.textContent = `${fmt.format(m._start)} – ${fmt.format(m._end)}`;
    hoverCard.appendChild(timeDiv);

    const aliasDiv = document.createElement('div');
    aliasDiv.textContent = m.meeting_alias;
    hoverCard.appendChild(aliasDiv);

    const assignedDiv = document.createElement('div');
    const assignedLabel = document.createElement('strong');
    assignedLabel.textContent = 'Assigned: ';
    assignedDiv.appendChild(assignedLabel);
    assignedDiv.appendChild(document.createTextNode(
      (m.endpoints || []).map((ep) => `${ep.display_name || ep.endpoint_alias} • ${statusLabel(ep, m)}`).join(', ') || 'None'
    ));
    hoverCard.appendChild(assignedDiv);

    const liveDiv = document.createElement('div');
    const liveLabel = document.createElement('strong');
    liveLabel.textContent = 'Live participants: ';
    liveDiv.appendChild(liveLabel);
    liveDiv.appendChild(document.createTextNode(liveNames));
    hoverCard.appendChild(liveDiv);

    const notesDiv = document.createElement('div');
    notesDiv.textContent = m.notes || 'No notes entered.';
    hoverCard.appendChild(notesDiv);

    const popupActions = document.createElement('div');
    popupActions.className = 'popup-actions';
    const blockState = m.timeline_status || m.status;
    if (blockState !== 'ended') {
      popupActions.appendChild(adjustmentControl(m.id));
    }
    if (canEditMeeting(m)) {
      const editBtn = document.createElement('button');
      editBtn.type = 'button';
      editBtn.dataset.action = 'edit';
      editBtn.dataset.meetingId = m.id;
      editBtn.textContent = 'Edit';
      popupActions.appendChild(editBtn);
    }
    if (blockState === 'ended') {
      const exportLink = document.createElement('a');
      exportLink.className = 'tiny-btn';
      exportLink.href = `${API_BASE}/meetings/${m.id}/export`;
      exportLink.target = '_blank';
      exportLink.rel = 'noopener noreferrer';
      exportLink.textContent = 'Export details';
      popupActions.appendChild(exportLink);
    }
    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.dataset.action = 'delete';
    deleteBtn.dataset.meetingId = m.id;
    deleteBtn.textContent = 'Delete';
    popupActions.appendChild(deleteBtn);

    hoverCard.appendChild(popupActions);
    block.appendChild(hoverCard);
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
  wrap.replaceChildren();

  if (!state.meetings.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'No meetings scheduled for this day yet.';
    wrap.appendChild(empty);
    return;
  }

  sortedMeetingQueue().forEach((m) => {
    const timelineState = m.timeline_status || m.status;
    const card = document.createElement('div');
    card.className = 'card';

    const start = new Date(m.start_time);
    const end = new Date(m.end_time);

    // Card top: title, time, status pill
    const cardTop = document.createElement('div');
    cardTop.className = 'card-top';
    const topLeft = document.createElement('div');
    const h3 = document.createElement('h3');
    h3.textContent = m.title;
    const timeP = document.createElement('p');
    timeP.textContent = `${fmt.format(start)} – ${fmt.format(end)} • ${m.meeting_alias}`;
    topLeft.appendChild(h3);
    topLeft.appendChild(timeP);
    const pill = document.createElement('span');
    pill.className = `pill ${timelineState}`;
    pill.textContent = String(timelineState).replaceAll('_', ' ');
    cardTop.appendChild(topLeft);
    cardTop.appendChild(pill);
    card.appendChild(cardTop);

    const notesDiv = document.createElement('div');
    notesDiv.className = 'muted';
    notesDiv.textContent = m.notes || 'No notes entered.';
    card.appendChild(notesDiv);

    // Assigned endpoints
    const assignedHead = document.createElement('div');
    assignedHead.className = 'subhead';
    assignedHead.textContent = 'Assigned endpoints';
    card.appendChild(assignedHead);
    const assignedChips = document.createElement('div');
    assignedChips.className = 'endpoint-chips';
    (m.endpoints || []).forEach((ep) => {
      const row = document.createElement('div');
      row.className = 'chip-row';
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = `${ep.display_name || ep.endpoint_alias} • ${statusLabel(ep, m)}`;
      row.appendChild(chip);
      if (!ep.live && timelineState === 'started') {
        const redialBtn = document.createElement('button');
        redialBtn.type = 'button';
        redialBtn.className = 'tiny-btn redial-btn';
        redialBtn.dataset.meetingId = m.id;
        redialBtn.dataset.endpointAlias = ep.endpoint_alias;
        redialBtn.textContent = 'Dial again';
        redialBtn.onclick = (e) => {
          e.stopPropagation();
          redialEndpoint(redialBtn.dataset.meetingId, redialBtn.dataset.endpointAlias);
        };
        row.appendChild(redialBtn);
      }
      assignedChips.appendChild(row);
    });
    card.appendChild(assignedChips);

    // Live participants
    const liveHead = document.createElement('div');
    liveHead.className = 'subhead';
    liveHead.textContent = 'Live participants';
    card.appendChild(liveHead);
    const liveChips = document.createElement('div');
    liveChips.className = 'endpoint-chips';
    const liveParts = m.live_participants || [];
    if (liveParts.length) {
      liveParts.forEach((p) => {
        const chipSpan = document.createElement('span');
        chipSpan.className = 'chip live-chip';
        chipSpan.textContent = p.display_name || p.remote_alias || 'Unknown';
        const ip = (p.remote_ip || '').trim();
        if (ip) {
          const ipLink = document.createElement('a');
          ipLink.className = 'ip-link';
          ipLink.href = `https://${encodeURI(ip)}`;
          ipLink.target = '_blank';
          ipLink.rel = 'noopener noreferrer';
          ipLink.textContent = ip;
          chipSpan.appendChild(ipLink);
        }
        liveChips.appendChild(chipSpan);
      });
    } else {
      const noLive = document.createElement('span');
      noLive.className = 'muted';
      noLive.textContent = 'No live participants';
      liveChips.appendChild(noLive);
    }
    card.appendChild(liveChips);

    // WebRTC invitees
    const inviteeHead = document.createElement('div');
    inviteeHead.className = 'subhead';
    inviteeHead.textContent = 'WebRTC email participants';
    card.appendChild(inviteeHead);
    const inviteeChips = document.createElement('div');
    inviteeChips.className = 'endpoint-chips';
    buildInviteeChips(inviteeChips, m.invitees || [], m.id);
    card.appendChild(inviteeChips);

    // Card actions
    const cardActions = document.createElement('div');
    cardActions.className = 'card-actions';
    if (timelineState !== 'ended') {
      cardActions.appendChild(adjustmentControl(m.id));
    }
    if (canEditMeeting(m)) {
      const editBtn = document.createElement('button');
      editBtn.type = 'button';
      editBtn.dataset.action = 'edit';
      editBtn.textContent = 'Edit';
      editBtn.onclick = () => openEdit(m.id);
      cardActions.appendChild(editBtn);
    }
    if (timelineState === 'ended') {
      const exportLink = document.createElement('a');
      exportLink.className = 'tiny-btn';
      exportLink.href = `${API_BASE}/meetings/${m.id}/export`;
      exportLink.target = '_blank';
      exportLink.rel = 'noopener noreferrer';
      exportLink.textContent = 'Export details';
      cardActions.appendChild(exportLink);
    }
    const deleteBtn = document.createElement('button');
    deleteBtn.type = 'button';
    deleteBtn.dataset.action = 'delete';
    deleteBtn.textContent = 'Delete';
    deleteBtn.onclick = () => deleteMeeting(m.id);
    cardActions.appendChild(deleteBtn);

    card.querySelectorAll('[data-action="adjust-range"]').forEach((el) => {
      el.addEventListener('input', () => setAdjustmentMinutes(m.id, el.value));
    });
    card.querySelectorAll('[data-action="adjust-apply"]').forEach((el) => {
      el.addEventListener('click', () => adjustMeeting(m.id, getAdjustmentMinutes(m.id)));
    });
    card.querySelectorAll('.resend-invite-btn').forEach((btn) => {
      btn.onclick = (e) => {
        e.stopPropagation();
        resendInvite(btn.dataset.meetingId, btn.dataset.inviteeId);
      };
    });

    card.appendChild(cardActions);
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
    showToast(safeErrorMessage(err));
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
    showToast(safeErrorMessage(err));
  }
}

async function deleteMeeting(id) {
  try {
    await api(`/meetings/${id}/delete`, { method: 'POST' });
    showToast('Meeting deleted.');
    await loadMeetings();
  } catch (err) {
    showToast(safeErrorMessage(err));
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
    showToast(safeErrorMessage(err));
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
  list.replaceChildren();
  const assigned = new Set((meeting.endpoints || []).map((ep) => ep.endpoint_alias));
  window.currentEditInvitees = (meeting.invitees || []).map((inv) => ({
    email: inv.email,
    display_name: inv.display_name || '',
  }));

  state.endpoints.forEach((ep) => {
    const row = document.createElement('label');
    row.className = 'endpoint-item light-item';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.className = 'edit-endpoint-check';
    checkbox.value = ep.alias;
    checkbox.dataset.displayName = ep.display_name || ep.alias;
    checkbox.checked = assigned.has(ep.alias);
    const infoDiv = document.createElement('div');
    const strong = document.createElement('strong');
    strong.textContent = ep.display_name || ep.alias;
    const sub = document.createElement('div');
    sub.className = 'endpoint-sub';
    sub.textContent = ep.alias || '';
    infoDiv.appendChild(strong);
    infoDiv.appendChild(sub);
    row.appendChild(checkbox);
    row.appendChild(infoDiv);
    list.appendChild(row);
  });

  let editInviteeWrap = $('#editInviteeWrap');
  if (!editInviteeWrap) {
    editInviteeWrap = document.createElement('div');
    editInviteeWrap.id = 'editInviteeWrap';
    const sectionHead = document.createElement('div');
    sectionHead.className = 'section-head slim';
    const sectionH3 = document.createElement('h3');
    sectionH3.textContent = 'WebRTC email participants';
    sectionHead.appendChild(sectionH3);
    editInviteeWrap.appendChild(sectionHead);
    const emailLabel = document.createElement('label');
    emailLabel.appendChild(document.createTextNode('Participant email'));
    const inlineInput = document.createElement('div');
    inlineInput.className = 'inline-input';
    const emailInput = document.createElement('input');
    emailInput.type = 'email';
    emailInput.id = 'editInviteeEmail';
    emailInput.placeholder = 'participant@example.com';
    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.id = 'addEditInvitee';
    addBtn.className = 'secondary';
    addBtn.textContent = 'Add';
    inlineInput.appendChild(emailInput);
    inlineInput.appendChild(addBtn);
    emailLabel.appendChild(inlineInput);
    editInviteeWrap.appendChild(emailLabel);
    const inviteeListDiv = document.createElement('div');
    inviteeListDiv.id = 'editInviteeList';
    inviteeListDiv.className = 'invitee-list';
    editInviteeWrap.appendChild(inviteeListDiv);
    $('#editEndpointList').insertAdjacentElement('afterend', editInviteeWrap);
  }

  function renderEditInvitees() {
    const editList = $('#editInviteeList');
    if (!editList) return;
    editList.textContent = '';
    if (!window.currentEditInvitees.length) {
      const empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = 'No WebRTC email participants added.';
      editList.appendChild(empty);
      return;
    }
    window.currentEditInvitees.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'invitee-row';
      const span = document.createElement('span');
      span.textContent = item.email;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'tiny-btn remove-edit-invitee';
      btn.dataset.email = item.email;
      btn.textContent = 'Remove';
      btn.onclick = () => {
        window.currentEditInvitees = window.currentEditInvitees.filter((i) => i.email !== item.email);
        renderEditInvitees();
      };
      row.appendChild(span);
      row.appendChild(btn);
      editList.appendChild(row);
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
    showToast(safeErrorMessage(err));
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
      showToast(safeErrorMessage(err));
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
    showToast(safeErrorMessage(err));
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