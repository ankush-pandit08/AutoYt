// AutoYT Clips Studio — Frontend Application Engine

const $ = id => document.getElementById(id);
const esc = val => String(val ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let state = null;
let token = null;
let connected = false;
let activeFilter = 'all';
let currentModalClip = null;
let toastTimer = null;

// API communication
async function api(path, options = {}, timeoutMs = 25000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(path, { ...options, cache: 'no-store', signal: controller.signal });
    let data;
    try { data = await res.json(); } catch { throw new Error('Unreadable response from local server.'); }
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
    return data;
  } catch (err) {
    if (err.name === 'AbortError') throw new Error('Request timed out.');
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

const post = body => api('/api/action', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-AutoYT-Token': token
  },
  body: JSON.stringify(body)
});

// Toast notification
function toast(text, tone = 'success') {
  clearTimeout(toastTimer);
  const t = $('toast');
  const txt = $('toastText');
  const ic = $('toastIcon');
  txt.textContent = text;
  ic.textContent = tone === 'error' ? '❌' : tone === 'info' ? 'ℹ️' : '✅';
  t.dataset.tone = tone;
  t.hidden = false;
  t.style.display = 'flex';
  toastTimer = setTimeout(() => {
    t.hidden = true;
    t.style.display = 'none';
  }, tone === 'error' ? 9000 : 5000);
}

function dismissToast() {
  clearTimeout(toastTimer);
  const t = $('toast');
  if (t) {
    t.hidden = true;
    t.style.display = 'none';
  }
}

$('dismissToast').addEventListener('click', (e) => {
  e.stopPropagation();
  dismissToast();
});

// Formatting helpers
function formatDuration(sec) {
  if (!sec) return '0s';
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// Data synchronization & refresh
async function refresh() {
  try {
    if (!token) {
      const session = await api('/api/session');
      token = session.token;
    }
    state = await api('/api/status');
    connected = true;
    $('connDot').className = 'status-dot online';
    $('connText').textContent = 'Connected';
    renderDashboard();
  } catch (err) {
    connected = false;
    $('connDot').className = 'status-dot';
    $('connText').textContent = 'Disconnected';
    console.error('Refresh error:', err);
  }
}

// Master Render Function
function renderDashboard() {
  const p = state?.podcast || {};
  const stats = p.stats || {};
  const episodes = p.episodes || [];
  const clips = p.clips || [];
  const sources = p.sources || [];

  // 1. Top Metrics
  $('statDiscovered').textContent = stats.episodes_discovered ?? episodes.length;
  $('statAuthorized').textContent = stats.episodes_authorized ?? 0;
  $('statGenerated').textContent = stats.clips_generated ?? clips.filter(c => c.output_path).length;
  const awaitingCount = clips.filter(c => c.status === 'candidate').length;
  $('statAwaiting').textContent = awaitingCount;
  $('badgeQueueCount').textContent = awaitingCount;
  $('statUploaded').textContent = stats.videos_uploaded ?? clips.filter(c => c.status === 'uploaded').length;

  // 2. Filter Tab Badges
  $('countAll').textContent = clips.length;
  $('countCandidate').textContent = awaitingCount;
  $('countApproved').textContent = clips.filter(c => c.status === 'approved').length;
  $('countUploaded').textContent = clips.filter(c => c.status === 'uploaded').length;

  // 3. Render Clips Studio Gallery
  renderClipsGallery(clips);

  // 4. Render Discovered Episodes Feed
  renderEpisodesTable(episodes);

  // 5. Render Creators Sources & YouTube Connection
  renderSources(sources);
  renderYouTubeStatus();

  // 6. Render Daily 5-Shorts Automation Schedule
  renderAutomationSchedule(state?.podcast?.schedule);

  // 7. Render Captions Studio History
  renderSubtitlesHistory(state?.podcast?.subtitles_studio?.jobs || []);
}

// Render Clips Grid (Compact Minimized Box Layout)
function renderClipsGallery(clips) {
  const container = $('clipsGrid');
  const filtered = clips.filter(c => {
    if (activeFilter === 'all') return true;
    return c.status === activeFilter;
  });

  if (!filtered.length) {
    container.innerHTML = `
      <div class="empty-state">
        <span class="empty-icon">🎬</span>
        <h3>No clips in this view</h3>
        <p>Paste a YouTube podcast link above or run "Auto-Clip Top Episode" to create high-impact Shorts.</p>
      </div>`;
    return;
  }

  container.innerHTML = filtered.map(clip => {
    const hasVideo = !!clip.output_path;
    const isUploaded = clip.status === 'uploaded';
    const isApproved = clip.status === 'approved';
    const isCandidate = clip.status === 'candidate';
    const ytUrl = clip.youtube_url || (clip.youtube_video_id ? `https://www.youtube.com/shorts/${clip.youtube_video_id}` : null);
    const score = clip.ai_score ? Math.round(clip.ai_score) : 75;
    const dur = formatDuration(clip.duration_seconds);

    return `
      <div class="clip-box" data-clip-id="${esc(clip.id)}">
        <!-- Compact Preview Thumbnail -->
        <div class="clip-mini-thumb-wrap clip-media-box" data-id="${esc(clip.id)}" title="Click to preview video">
          <img src="/media/podcast/clip/${esc(clip.id)}/thumbnail.png" alt="Preview" class="clip-mini-thumb" loading="lazy">
          <div class="clip-mini-play">▶</div>
        </div>

        <!-- Main Info Box -->
        <div class="clip-box-main">
          <div class="clip-box-title-row">
            <h4 class="clip-box-title" title="${esc(clip.generated_title || 'Untitled Short')}">
              ${esc(clip.generated_title || 'Untitled Short')}
            </h4>
            <span class="badge badge-${esc(clip.status || 'candidate')}">${esc(clip.status || 'candidate')}</span>
          </div>

          <div class="clip-box-meta-row">
            <span class="meta-item">🎙️ ${esc(clip.source_id || 'Podcast')} • #${esc(clip.clip_index || 1)}</span>
            <span class="meta-sep">•</span>
            <span class="meta-item">⏱ ${dur}</span>
            <span class="meta-sep">•</span>
            <span class="meta-item meta-score">★ ${score}/100</span>
            ${ytUrl ? `
              <span class="meta-sep">•</span>
              <a href="${esc(ytUrl)}" target="_blank" rel="noopener noreferrer" class="clip-box-yt-link" title="Open YouTube Shorts in new tab">
                <svg viewBox="0 0 24 24" width="13" height="13" fill="#ef4444"><path d="M10 15l5-3-5-3v6z"/><path d="M21.58 7.19a2.7 2.7 0 0 0-1.9-1.9C18 4.8 12 4.8 12 4.8s-6 0-7.68.49a2.7 2.7 0 0 0-1.9 1.9C2 8.87 2 12 2 12s0 3.13.42 4.81a2.7 2.7 0 0 0 1.9 1.9c1.68.49 7.68.49 7.68.49s6 0 7.68-.49a2.7 2.7 0 0 0 1.9-1.9C22 15.13 22 12 22 12s0-3.13-.42-4.81zM10 15V9l5 3-5 3z"/></svg>
                ${esc(clip.youtube_video_id ? 'shorts/' + clip.youtube_video_id : 'Watch on YouTube')} ↗
              </a>
            ` : ''}
          </div>
        </div>

        <!-- Compact Actions -->
        <div class="clip-box-actions">
          <button class="btn btn-secondary btn-sm edit-clip-btn" data-id="${esc(clip.id)}" title="Preview video">
            ▶ Preview
          </button>
          ${isUploaded ? `
            <a href="${esc(ytUrl)}" target="_blank" rel="noopener noreferrer" class="btn btn-danger btn-sm" title="View on YouTube">
              YouTube ↗
            </a>
          ` : ''}
          ${isCandidate ? `
            <button class="btn btn-success btn-sm approve-clip-btn" data-id="${esc(clip.id)}">✓ Approve</button>
          ` : ''}
          ${isApproved ? `
            <button class="btn btn-primary btn-sm upload-clip-btn" data-id="${esc(clip.id)}">🚀 Upload</button>
          ` : ''}
          <button class="btn btn-secondary btn-sm delete-clip-btn" data-id="${esc(clip.id)}" title="Delete Clip">✕</button>
        </div>
      </div>
    `;
  }).join('');
}

// Render Discovered Episodes Table
function renderEpisodesTable(episodes) {
  const tbody = $('episodesTableBody');
  if (!episodes.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="table-loading">No episodes discovered yet. Click "Discover New Episodes" to scan feeds.</td></tr>`;
    return;
  }

  tbody.innerHTML = episodes.map(ep => {
    const isAuth = ep.rights_status === 'authorized';
    const isProc = ep.status === 'processed';
    const isProcIng = ep.status === 'processing';
    const score = ep.discovery_score ? Math.round(ep.discovery_score * 100) : 50;
    const dur = formatDuration(ep.duration_seconds);

    return `
      <tr>
        <td class="ep-title">
          <a href="${esc(ep.video_url)}" target="_blank" rel="noopener noreferrer" class="ep-link">
            ${esc(ep.title)} ↗
          </a>
        </td>
        <td>${esc(ep.channel_name || ep.source_id)}</td>
        <td>${dur}</td>
        <td><strong class="ep-score">${score} / 100</strong></td>
        <td>
          <span class="badge ${isAuth ? 'badge-success' : 'badge-candidate'}">
            ${esc(ep.rights_status || 'unknown')}
          </span>
        </td>
        <td class="text-right">
          ${isProc ? `
            <span class="badge badge-success">Processed</span>
          ` : isProcIng ? `
            <span class="badge badge-candidate">Clipping...</span>
          ` : isAuth ? `
            <button class="btn btn-primary btn-sm clip-ep-btn" data-id="${esc(ep.id)}">⚡ Clip Now</button>
          ` : `
            <button class="btn btn-secondary btn-sm auth-ep-btn" data-id="${esc(ep.id)}" data-title="${esc(ep.title)}">Authorize</button>
          `}
        </td>
      </tr>
    `;
  }).join('');
}

// Render Creators List
function renderSources(sources) {
  const container = $('sourcesList');
  if (!sources || !sources.length) {
    container.innerHTML = `<p class="table-loading">No podcast sources configured. Click "➕ Add Creator" above to add one.</p>`;
    return;
  }

  container.innerHTML = sources.map(src => {
    const isEnabled = src.enabled !== false;
    return `
      <div class="source-item ${isEnabled ? 'source-active' : 'source-disabled'}" data-id="${esc(src.id)}">
        <div class="source-info">
          <div class="source-header-row">
            <h4 class="source-name">${esc(src.name)}</h4>
            <span class="source-show-badge">${esc(src.show_name || 'Podcast')}</span>
            <span class="badge ${isEnabled ? 'badge-success' : 'badge-candidate'}">${isEnabled ? 'Active' : 'Disabled'}</span>
          </div>
          <div class="source-meta">
            <a href="${esc(src.youtube_channel_url || '#')}" target="_blank" rel="noopener noreferrer" class="source-yt-link">
              ${esc(src.youtube_channel_url || 'YouTube Channel')} ↗
            </a>
            ${src.latest_episode ? `<span class="source-last-ep">Latest: ${esc(src.latest_episode.title?.slice(0, 42))}…</span>` : ''}
          </div>
        </div>
        <div class="source-actions">
          <button class="btn btn-secondary btn-sm scan-source-btn" data-id="${esc(src.id)}" data-name="${esc(src.name)}" title="Scan this channel now for new episodes">
            <span>🔍</span> Scan
          </button>
          <button class="btn ${isEnabled ? 'btn-secondary' : 'btn-primary'} btn-sm toggle-source-btn" data-id="${esc(src.id)}" data-name="${esc(src.name)}" title="${isEnabled ? 'Disable automatic discovery' : 'Enable automatic discovery'}">
            ${isEnabled ? '✓ Enabled' : '✕ Disabled'}
          </button>
          <button class="btn btn-danger-outline btn-sm delete-source-btn" data-id="${esc(src.id)}" data-name="${esc(src.name)}" title="Remove creator from registry">
            <span>🗑️</span>
          </button>
        </div>
      </div>
    `;
  }).join('');
}

// Render YouTube Connection Status
function renderYouTubeStatus() {
  const yt = state?.youtube_authorization || {};
  const isAuth = yt.authenticated || state?.settings?.token_present;
  $('ytBadge').textContent = isAuth ? 'Connected' : 'Needs Setup';
  $('ytBadge').className = `badge ${isAuth ? 'badge-success' : 'badge-candidate'}`;
  $('ytChannelName').textContent = yt.channel_name || 'Authorized YouTube Channel';
  $('ytStatusNote').textContent = isAuth 
    ? 'Authenticated via OAuth. Shorts upload to your channel in private review mode.'
    : 'Not authenticated. Click Reconnect to link your channel.';
}

// Render Daily 5-Shorts Automation Schedule
function renderAutomationSchedule(schedule) {
  if (!schedule) return;

  if ($('currentTimeDisplay')) $('currentTimeDisplay').textContent = schedule.current_time_ist || 'Asia/Kolkata (IST)';
  if ($('nextSlotDisplay')) $('nextSlotDisplay').textContent = schedule.next_slot?.time_display || '19:00 IST';
  if ($('nextSlotLabel')) $('nextSlotLabel').textContent = `${schedule.next_slot?.label || 'Slot 1/5'} (${schedule.next_slot?.status || 'Scheduled'})`;
  if ($('bannerNextSlot')) $('bannerNextSlot').textContent = `Next: ${schedule.next_slot?.label || 'Slot 1/5'} at ${schedule.next_slot?.time_display || '7:00 PM IST'}`;
  
  const readyCount = schedule.ready_queue_count || 0;
  if ($('queueCountDisplay')) $('queueCountDisplay').textContent = `${readyCount} Ready`;
  if ($('queueHealthLabel')) $('queueHealthLabel').textContent = readyCount >= 5 ? '✓ Queue ready for tonight' : 'Preparing queue in background';

  const slotsContainer = $('scheduleSlotsList');
  if (slotsContainer && schedule.slots) {
    slotsContainer.innerHTML = schedule.slots.map(s => {
      const isNext = schedule.next_slot && schedule.next_slot.slot === s.slot;
      const statusBadge = isNext ? '<span class="badge badge-candidate">Next Up</span>' :
                          s.status === 'completed' ? '<span class="badge badge-uploaded">Done Today</span>' :
                          '<span class="badge badge-approved">Scheduled</span>';
      return `
        <div class="schedule-slot-item ${isNext ? 'slot-next' : ''}">
          <div class="slot-time-col">
            <span class="slot-time-text">${esc(s.time_display)}</span>
            <span class="slot-num">${esc(s.label)}</span>
          </div>
          <div class="slot-status-col">
            ${statusBadge}
            <span class="slot-desc">Auto-approves top scored clip and uploads to YouTube Shorts in Private Review Mode</span>
          </div>
          <div class="slot-action-col">
            <button class="btn btn-primary btn-sm run-slot-btn" data-slot="${esc(s.slot)}">
              ⚡ Publish Slot ${esc(s.slot)} Now
            </button>
          </div>
        </div>
      `;
    }).join('');
  }
}

// NAVIGATION TABS
const tabTitles = {
  clipsStudio: ['Channel overview & Clips Studio', 'Review AI-extracted 9:16 vertical Shorts and publish to YouTube in private mode'],
  captionsStudio: ['Reels & Shorts Captions Studio', 'Upload 60-second video to transcribe and burn animated subtitles with viral creator fonts and styles'],
  automationSchedule: ['Daily Shorts Automation & Schedule', 'Automated background publisher running 5 Shorts every evening between 7:00 PM and 9:00 PM IST'],
  manualCreator: ['Create Shorts Clip', 'Paste any podcast or video URL to automatically extract vertical Shorts with animated captions'],
  episodesFeed: ['Discovered Episodes Feed', 'Podcast episodes discovered from your creators, ranked by topic relevance'],
  sourcesSettings: ['Creators & Channel Settings', 'Configure podcast source channels and YouTube publishing settings']
};

function switchTab(tab) {
  if (!tabTitles[tab]) tab = 'clipsStudio';
  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tab);
  });

  $('clipsStudioSection').hidden = tab !== 'clipsStudio';
  $('captionsStudioSection').hidden = tab !== 'captionsStudio';
  $('manualCreatorSection').hidden = tab !== 'manualCreator' && tab !== 'clipsStudio';
  $('episodesFeedSection').hidden = tab !== 'episodesFeed';
  $('sourcesSettingsSection').hidden = tab !== 'sourcesSettings';
  $('automationScheduleSection').hidden = tab !== 'automationSchedule';

  $('pageTitle').textContent = tabTitles[tab][0];
  $('pageSubtitle').textContent = tabTitles[tab][1];

  if (window.location.hash !== `#${tab}`) {
    history.replaceState(null, '', `#${tab}`);
  }
}

document.querySelectorAll('.nav-item').forEach(item => {
  item.onclick = e => {
    e.preventDefault();
    switchTab(item.dataset.tab);
  };
});

window.addEventListener('hashchange', () => {
  const hash = window.location.hash.replace('#', '');
  if (hash && tabTitles[hash]) switchTab(hash);
});

// CLIPS FILTER PILLS
document.querySelectorAll('.filter-pill').forEach(pill => {
  pill.onclick = () => {
    document.querySelectorAll('.filter-pill').forEach(p => p.classList.remove('active'));
    pill.classList.add('active');
    activeFilter = pill.dataset.filter;
    renderClipsGallery(state?.podcast?.clips || []);
  };
});

// FORM: MANUAL LINK SUBMISSION
$('manualClipForm').onsubmit = async e => {
  e.preventDefault();
  const url = $('videoUrlInput').value.trim();
  const clips = parseInt($('clipsCountSelect').value, 10);
  const style = $('subtitleStyleSelect').value;
  const bgmVibe = $('bgmSelect').value;
  const autoUpload = $('autoUploadCheck').checked;

  if (!url) {
    toast('Please enter a valid YouTube video URL.', 'error');
    return;
  }

  $('startClippingBtn').disabled = true;
  $('startClippingBtn').textContent = 'Extracting Details...';

  try {
    toast('Ingesting video and launching transcription pipeline...', 'info');
    const res = await post({
      action: 'podcast_manual_clip',
      url,
      clips,
      style,
      bgm_vibe: bgmVibe,
      auto_upload: autoUpload
    });
    toast(res.message || 'Clipping started in background! Check back in 1-2 minutes.', 'success');
    $('videoUrlInput').value = '';
    setTimeout(refresh, 2000);
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    $('startClippingBtn').disabled = false;
    $('startClippingBtn').innerHTML = '<span>🚀</span> Generate 9:16 Shorts';
  }
};

// BUTTON: AUTO-CLIP TOP EPISODE
$('autoClipBtn').onclick = async () => {
  $('autoClipBtn').disabled = true;
  try {
    toast('Discovering and selecting the highest-scored podcast episode...', 'info');
    const res = await post({
      action: 'podcast_auto_clip_top',
      clips: 3,
      style: 'dynamic'
    });
    toast(res.message || 'Auto-clip started for top episode!', 'success');
    setTimeout(refresh, 2500);
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    $('autoClipBtn').disabled = false;
  }
};

// BUTTON: DISCOVER EPISODES
$('discoverEpisodesBtn').onclick = async () => {
  $('discoverEpisodesBtn').disabled = true;
  try {
    toast('Scanning feeds and channels for new podcast episodes...', 'info');
    const res = await post({ action: 'podcast_discover' });
    toast(res.message || 'Discovery complete!', 'success');
    await refresh();
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    $('discoverEpisodesBtn').disabled = false;
  }
};

// REFRESH BUTTON
$('refreshBtn').onclick = async () => {
  toast('Refreshing dashboard...', 'info');
  await refresh();
};

// DELEGATED EVENT HANDLERS (CLIPS & EPISODES)
document.addEventListener('click', async e => {
  // 0. Quick load sample URL
  if (e.target.matches('.sample-pill')) {
    const sampleUrl = e.target.dataset.url;
    if (sampleUrl) {
      $('videoUrlInput').value = sampleUrl;
      $('videoUrlInput').focus();
      toast(`Loaded URL for ${e.target.textContent.trim()}!`, 'info');
    }
    return;
  }

  // 1. Edit / Preview Clip or Click Media Poster
  const mediaBox = e.target.closest('.clip-media-box');
  if (e.target.matches('.edit-clip-btn') || mediaBox) {
    const clipId = (mediaBox || e.target).dataset.id;
    const clip = state?.podcast?.clips?.find(c => c.id === clipId);
    if (!clip) return;
    openClipModal(clip);
    return;
  }

  // 2. Approve Clip
  if (e.target.matches('.approve-clip-btn')) {
    const clipId = e.target.dataset.id;
    try {
      await post({ action: 'podcast_approve_clip', clip_id: clipId });
      toast('Clip approved! Ready to publish to YouTube.', 'success');
      await refresh();
    } catch (err) {
      toast(err.message, 'error');
    }
    return;
  }

  // 3. Upload to YouTube
  if (e.target.matches('.upload-clip-btn')) {
    const clipId = e.target.dataset.id;
    e.target.disabled = true;
    e.target.textContent = 'Uploading...';
    try {
      toast('Uploading Short to your YouTube channel in private mode...', 'info');
      const res = await post({ action: 'podcast_upload_clip', clip_id: clipId, privacy: 'private' });
      toast(res.message || 'Short uploaded successfully to YouTube!', 'success');
      await refresh();
    } catch (err) {
      toast(err.message, 'error');
      e.target.disabled = false;
      e.target.textContent = '🚀 Upload to YouTube';
    }
    return;
  }

  // 4. Delete Clip
  if (e.target.matches('.delete-clip-btn')) {
    const clipId = e.target.dataset.id;
    if (!confirm('Are you sure you want to remove this clip record?')) return;
    try {
      await post({ action: 'podcast_delete_clip', clip_id: clipId });
      toast('Clip removed.', 'success');
      await refresh();
    } catch (err) {
      toast(err.message, 'error');
    }
    return;
  }

  // 5. Clip Discovered Episode (Instant 1-Click)
  const clipBtn = e.target.closest('.clip-ep-btn');
  if (clipBtn) {
    const epId = clipBtn.dataset.id;
    clipBtn.disabled = true;
    clipBtn.textContent = 'Clipping...';
    try {
      toast(`Started clipping ${epId} in background...`, 'info');
      await post({ action: 'podcast_process', episode_id: epId, clips: 3, style: 'dynamic' });
      toast('Processing underway! Check the Clips Studio in 1-2 minutes.', 'success');
      setTimeout(refresh, 2000);
    } catch (err) {
      toast(err.message, 'error');
      clipBtn.disabled = false;
      clipBtn.textContent = '⚡ Clip Now';
    }
    return;
  }

  // 6. Open Authorize Dialog
  const authBtn = e.target.closest('.auth-ep-btn');
  if (authBtn) {
    const epId = authBtn.dataset.id;
    const epTitle = authBtn.dataset.title;
    $('authEpisodeId').value = epId;
    $('authEpisodeTitleDisplay').textContent = epTitle;
    $('authModal').showModal();
    return;
  }

  // 7. Toggle Creator Source
  const toggleBtn = e.target.closest('.toggle-source-btn');
  if (toggleBtn) {
    const srcId = toggleBtn.dataset.id;
    const srcName = toggleBtn.dataset.name || srcId;
    toggleBtn.disabled = true;
    toggleBtn.textContent = 'Updating...';
    try {
      const res = await post({ action: 'podcast_toggle_source', source_id: srcId });
      toast(res.message || 'Source updated.', 'success');
      await refresh();
    } catch (err) {
      toast(err.message, 'error');
      toggleBtn.disabled = false;
      toggleBtn.textContent = 'Retry';
    }
    return;
  }

  // 7b. Delete Creator Source
  const delSourceBtn = e.target.closest('.delete-source-btn');
  if (delSourceBtn) {
    const srcId = delSourceBtn.dataset.id;
    const srcName = delSourceBtn.dataset.name || srcId;
    if (!confirm(`Are you sure you want to remove creator "${srcName}" from your sources?`)) return;
    delSourceBtn.disabled = true;
    try {
      toast(`Removing creator ${srcName}...`, 'info');
      const res = await post({ action: 'podcast_remove_source', source_id: srcId });
      toast(res.message || 'Creator removed.', 'success');
      await refresh();
    } catch (err) {
      toast(err.message, 'error');
      delSourceBtn.disabled = false;
    }
    return;
  }

  // 7c. Scan Creator Channel
  const scanSourceBtn = e.target.closest('.scan-source-btn');
  if (scanSourceBtn) {
    const srcId = scanSourceBtn.dataset.id;
    const srcName = scanSourceBtn.dataset.name || srcId;
    scanSourceBtn.disabled = true;
    scanSourceBtn.innerHTML = '<span>⏳</span> Scanning...';
    try {
      toast(`Scanning ${srcName} for latest episodes...`, 'info');
      const res = await post({ action: 'podcast_scan_source', source_id: srcId });
      toast(res.message || 'Scan completed!', 'success');
      await refresh();
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      scanSourceBtn.disabled = false;
      scanSourceBtn.innerHTML = '<span>🔍</span> Scan';
    }
    return;
  }

  // 8. Run Specific Scheduled Slot
  const runSlotBtn = e.target.closest('.run-slot-btn');
  if (runSlotBtn) {
    const slotNum = runSlotBtn.dataset.slot;
    runSlotBtn.disabled = true;
    runSlotBtn.textContent = 'Publishing...';
    try {
      toast(`Triggering automated publish for Slot ${slotNum}...`, 'info');
      const res = await post({ action: 'podcast_publish_slot', slot: slotNum, privacy: 'private' });
      toast(res.message || `Slot ${slotNum} published!`, 'success');
      await refresh();
    } catch (err) {
      toast(err.message, 'error');
      runSlotBtn.disabled = false;
      runSlotBtn.textContent = `⚡ Publish Slot ${slotNum} Now`;
    }
    return;
  }
});

// MODAL: CLIP PREVIEW & EDIT
function openClipModal(clip) {
  currentModalClip = clip;
  $('modalClipTitle').textContent = clip.generated_title || 'Clip Preview';
  $('editTitleInput').value = clip.generated_title || '';
  $('editDescInput').value = clip.generated_description || '';
  $('modalClipScore').textContent = `${Math.round(clip.ai_score || 75)} / 100`;
  $('modalClipDuration').textContent = formatDuration(clip.duration_seconds);
  $('modalClipStatus').textContent = clip.status?.toUpperCase() || 'CANDIDATE';

  const player = $('modalVideoPlayer');
  if (clip.output_path) {
    player.src = `/media/podcast/clip/${clip.id}/final.mp4`;
    player.poster = `/media/podcast/clip/${clip.id}/thumbnail.png`;
    player.hidden = false;
  } else {
    player.hidden = true;
  }

  const ytUrl = clip.youtube_url || (clip.youtube_video_id ? `https://www.youtube.com/shorts/${clip.youtube_video_id}` : null);
  if (ytUrl) {
    $('modalYoutubeRow').hidden = false;
    $('modalYoutubeLink').href = ytUrl;
  } else {
    $('modalYoutubeRow').hidden = true;
  }

  $('modalApproveBtn').hidden = clip.status === 'approved' || clip.status === 'uploaded';
  $('modalUploadBtn').hidden = clip.status === 'uploaded';

  $('clipModal').showModal();
}

$('closeClipModal').onclick = () => {
  $('modalVideoPlayer').pause();
  $('clipModal').close();
};

// Modal Actions
$('modalSaveMetaBtn').onclick = async () => {
  if (!currentModalClip) return;
  const title = $('editTitleInput').value.trim();
  const desc = $('editDescInput').value.trim();
  try {
    await post({
      action: 'podcast_update_clip',
      clip_id: currentModalClip.id,
      title,
      description: desc
    });
    toast('Clip title and description saved!', 'success');
    await refresh();
  } catch (err) {
    toast(err.message, 'error');
  }
};

$('modalApproveBtn').onclick = async () => {
  if (!currentModalClip) return;
  try {
    await post({ action: 'podcast_approve_clip', clip_id: currentModalClip.id });
    toast('Clip approved!', 'success');
    $('modalApproveBtn').hidden = true;
    await refresh();
  } catch (err) {
    toast(err.message, 'error');
  }
};

$('modalUploadBtn').onclick = async () => {
  if (!currentModalClip) return;
  $('modalUploadBtn').disabled = true;
  $('modalUploadBtn').textContent = 'Uploading...';
  try {
    toast('Uploading Short to YouTube...', 'info');
    const res = await post({
      action: 'podcast_upload_clip',
      clip_id: currentModalClip.id,
      privacy: 'private'
    });
    toast(res.message || 'Uploaded successfully!', 'success');
    $('clipModal').close();
    await refresh();
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    $('modalUploadBtn').disabled = false;
    $('modalUploadBtn').textContent = '🚀 Upload to YouTube';
  }
};

// MODAL: AUTHORIZE RIGHTS
$('closeAuthModal').onclick = () => $('authModal').close();
$('cancelAuthModalBtn').onclick = () => $('authModal').close();

$('authForm').onsubmit = async e => {
  e.preventDefault();
  const epId = $('authEpisodeId').value;
  const basis = $('authBasisSelect').value;
  const notes = $('authNotesInput').value.trim();

  try {
    await post({
      action: 'podcast_authorize',
      episode_id: epId,
      basis,
      notes
    });
    toast('Episode authorized! You can now clip it.', 'success');
    $('authModal').close();
    await refresh();
  } catch (err) {
    toast(err.message, 'error');
  }
};

$('blockEpBtn').onclick = async () => {
  const epId = $('authEpisodeId').value;
  try {
    await post({ action: 'podcast_block', episode_id: epId, reason: 'Manually blocked in dashboard' });
    toast('Episode blocked from processing.', 'info');
    $('authModal').close();
    await refresh();
  } catch (err) {
    toast(err.message, 'error');
  }
};

// ADD CREATOR FORM CONTROLS
const btnToggleAddSource = $('btnToggleAddSource');
const addSourceForm = $('addSourceForm');
const btnCancelAddSource = $('btnCancelAddSource');

if (btnToggleAddSource && addSourceForm) {
  btnToggleAddSource.onclick = () => {
    addSourceForm.hidden = !addSourceForm.hidden;
    if (!addSourceForm.hidden && $('newSourceName')) $('newSourceName').focus();
  };
}

if (btnCancelAddSource && addSourceForm) {
  btnCancelAddSource.onclick = () => {
    addSourceForm.reset();
    addSourceForm.hidden = true;
  };
}

if (addSourceForm) {
  addSourceForm.onsubmit = async e => {
    e.preventDefault();
    const name = ($('newSourceName')?.value || '').trim();
    const show_name = ($('newSourceShow')?.value || '').trim() || name;
    const channel_url = ($('newSourceUrl')?.value || '').trim();

    if (!name || !channel_url) {
      toast('Creator name and YouTube channel URL are required.', 'error');
      return;
    }

    const submitBtn = $('btnSubmitAddSource');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Adding...';
    }

    try {
      toast(`Adding creator "${name}"...`, 'info');
      const res = await post({
        action: 'podcast_add_source',
        name,
        show_name,
        channel_url,
      });
      toast(res.message || `Creator '${name}' added successfully!`, 'success');
      addSourceForm.reset();
      addSourceForm.hidden = true;
      await refresh();
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Save Creator';
      }
    }
  };
}

// YOUTUBE RECONNECT
const ytReconnectBtn = $('ytReconnectBtn');
if (ytReconnectBtn) {
  ytReconnectBtn.onclick = async () => {
    ytReconnectBtn.disabled = true;
    ytReconnectBtn.textContent = 'Connecting...';
    try {
      toast('Generating Google OAuth link...', 'info');
      const res = await post({ action: 'youtube_reconnect' });
      const authUrl = res.authorization_url || res.auth_url;
      if (authUrl) {
        const continueLink = $('ytAuthContinueLink');
        continueLink.href = authUrl;
        continueLink.hidden = false;
        continueLink.style.display = 'inline-flex';
        window.open(authUrl, '_blank');
        toast('Opening Google sign-in. Click "Complete in Google ↗" if popup was blocked.', 'info');
      } else {
        toast(res.message || 'Reconnect request generated.', 'info');
      }
    } catch (err) {
      toast(err.message, 'error');
    } finally {
      ytReconnectBtn.disabled = false;
      ytReconnectBtn.textContent = 'Reconnect Channel';
    }
  };
}

// AUTOMATION & SCHEDULE ACTIONS
$('btnViewScheduleTab').onclick = () => {
  const scheduleNav = document.querySelector('[data-tab="automationSchedule"]');
  if (scheduleNav) scheduleNav.click();
};

$('btnQuickPublishSlot').onclick = async () => {
  const nextSlotNum = state?.podcast?.schedule?.next_slot?.slot || 1;
  $('btnQuickPublishSlot').disabled = true;
  $('btnQuickPublishSlot').textContent = 'Publishing...';
  try {
    toast(`Triggering automated publish for Slot ${nextSlotNum}...`, 'info');
    const res = await post({ action: 'podcast_publish_slot', slot: nextSlotNum, privacy: 'private' });
    toast(res.message || 'Slot published successfully!', 'success');
    await refresh();
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    $('btnQuickPublishSlot').disabled = false;
    $('btnQuickPublishSlot').innerHTML = '<span>🚀</span> Publish Next Slot Now';
  }
};

$('btnPrepareQueueNow').onclick = async () => {
  $('btnPrepareQueueNow').disabled = true;
  try {
    toast('Checking queue and clipping episodes if needed...', 'info');
    const res = await post({ action: 'podcast_prepare_queue' });
    toast(res.message || 'Queue verified!', 'success');
    await refresh();
  } catch (err) {
    toast(err.message, 'error');
  } finally {
    $('btnPrepareQueueNow').disabled = false;
  }
};

$('btnRefreshSchedule').onclick = async () => {
  toast('Refreshing schedule and queue status...', 'info');
  await refresh();
};

// CAPTIONS STUDIO ENGINE
let uploadedVideoJobId = null;

function renderSubtitlesHistory(jobs) {
  const container = $('subtitlesHistoryList');
  if (!container) return;
  if (!jobs || !jobs.length) {
    container.innerHTML = `<p class="table-loading">No uploaded shorts subtitled yet.</p>`;
    return;
  }
  container.innerHTML = jobs.map(j => {
    const isReady = j.status === 'ready';
    const dur = j.duration ? formatDuration(j.duration) : '--';
    return `
      <div class="clip-box" data-job-id="${esc(j.id)}">
        <div class="clip-mini-thumb-wrap ${isReady ? 'preview-sub-thumb' : ''}" data-video-url="${esc(j.video_url || '')}">
          ${j.thumb_url ? `<img src="${esc(j.thumb_url)}" alt="Preview" class="clip-mini-thumb" loading="lazy">` : '<div style="background:#000;width:100%;height:100%"></div>'}
          <div class="clip-mini-play">▶</div>
        </div>

        <div class="clip-box-main">
          <div class="clip-box-title-row">
            <h4 class="clip-box-title">${esc(j.filename || 'Subtitled Reel')}</h4>
            <span class="badge ${isReady ? 'badge-success' : 'badge-candidate'}">${esc(j.status || 'processing')}</span>
          </div>
          <div class="clip-box-meta-row">
            <span class="meta-item">🔤 ${esc(j.font || 'Default Font')}</span>
            <span class="meta-sep">•</span>
            <span class="meta-item">🎨 ${esc(j.style_key || 'Style')}</span>
            <span class="meta-sep">•</span>
            <span class="meta-item">⏱ ${dur}</span>
            ${j.words_count ? `<span class="meta-sep">•</span><span class="meta-item">${j.words_count} words</span>` : ''}
          </div>
        </div>

        <div class="clip-box-actions">
          ${isReady ? `
            <button type="button" class="btn btn-secondary btn-sm preview-sub-video-btn" data-video-url="${esc(j.video_url)}" data-thumb-url="${esc(j.thumb_url || '')}">
              ▶ Preview
            </button>
            <a href="${esc(j.download_url)}" class="btn btn-primary btn-sm" download="subtitled_${esc(j.id)}.mp4">
              ⬇️ Download
            </a>
          ` : `
            <span class="badge badge-candidate">Processing</span>
          `}
        </div>
      </div>
    `;
  }).join('');
}

function formatFileSize(bytes) {
  if (!bytes) return '0 B';
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(1)} MB`;
}

function handleVideoFileSelected(file) {
  if (!file) return;
  if (!file.type.startsWith('video/') && !file.name.match(/\.(mp4|mov|webm|m4v|mkv)$/i)) {
    toast('Please select a valid video file (MP4, MOV, WEBM).', 'error');
    return;
  }
  if (file.size > 250 * 1024 * 1024) {
    toast('Video file exceeds 250MB limit.', 'error');
    return;
  }

  $('uploadFileName').textContent = file.name;
  $('uploadFileSize').textContent = formatFileSize(file.size);
  $('dropzonePrompt').hidden = true;
  $('uploadFileInfo').hidden = false;
  $('captionProgressText').textContent = 'Uploading video file...';

  const uploadBtn = $('btnGenerateCaptions');
  uploadBtn.disabled = true;
  uploadBtn.innerHTML = '<span>⏳</span> Uploading...';

  fetch('/api/subtitles/upload', {
    method: 'POST',
    headers: {
      'Content-Type': file.type || 'video/mp4',
      'X-Filename': encodeURIComponent(file.name),
      'X-AutoYT-Token': token
    },
    body: file
  })
  .then(res => {
    if (!res.ok) throw new Error(`Upload failed (${res.status})`);
    return res.json();
  })
  .then(data => {
    uploadedVideoJobId = data.job_id;
    toast('Video uploaded! Choose your font and style, then click Generate.', 'success');
    $('captionProgressText').textContent = 'Video ready for transcription and subtitle styling.';
    uploadBtn.disabled = false;
    uploadBtn.innerHTML = '<span>✨</span> Generate &amp; Burn Subtitles';
  })
  .catch(err => {
    toast(`Upload failed: ${err.message}`, 'error');
    $('captionProgressText').textContent = `Upload error: ${err.message}`;
    uploadBtn.disabled = true;
    uploadBtn.innerHTML = '<span>✨</span> Generate &amp; Burn Subtitles';
  });
}

$('btnSelectVideo').onclick = e => {
  e.stopPropagation();
  $('videoUploadInput').click();
};

$('videoDropZone').onclick = e => {
  if (e.target.id === 'btnChangeVideo' || e.target.closest('#btnChangeVideo')) return;
  $('videoUploadInput').click();
};

const topbarUploadBtn = $('topbarUploadVideoBtn');
if (topbarUploadBtn) {
  topbarUploadBtn.onclick = () => {
    switchTab('captionsStudio');
    setTimeout(() => $('videoUploadInput').click(), 150);
  };
}

const heroUploadBtn = $('heroSwitchToUploadBtn');
if (heroUploadBtn) {
  heroUploadBtn.onclick = () => {
    switchTab('captionsStudio');
    setTimeout(() => $('videoUploadInput').click(), 150);
  };
}

$('btnChangeVideo').onclick = () => {
  $('videoUploadInput').value = '';
  $('uploadFileInfo').hidden = true;
  $('dropzonePrompt').hidden = false;
  $('btnGenerateCaptions').disabled = true;
  $('captionProgressText').textContent = '';
  uploadedVideoJobId = null;
};

const dropZone = $('videoDropZone');
dropZone.ondragover = e => {
  e.preventDefault();
  dropZone.classList.add('dragover');
};
dropZone.ondragleave = () => dropZone.classList.remove('dragover');
dropZone.ondrop = e => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files && e.dataTransfer.files.length) {
    handleVideoFileSelected(e.dataTransfer.files[0]);
  }
};
$('videoUploadInput').onchange = e => {
  if (e.target.files && e.target.files.length) {
    handleVideoFileSelected(e.target.files[0]);
  }
};

$('btnGenerateCaptions').onclick = async () => {
  if (!uploadedVideoJobId) {
    toast('Please upload a video file first.', 'error');
    return;
  }

  const btn = $('btnGenerateCaptions');
  btn.disabled = true;
  btn.innerHTML = '<span>⚙️</span> Transcribing &amp; Burning...';
  $('captionProgressText').textContent = 'AI Whisper is generating word timestamps and burning viral subtitles...';

  const font = $('captionFontSelect').value;
  const styleKey = $('captionStyleSelect').value;
  const position = $('captionPositionSelect').value;
  const chunkSize = parseInt($('captionChunkSelect').value, 10);
  const fontSize = parseInt($('captionFontSizeSelect').value, 10);
  const textCase = $('captionCaseSelect').value;

  try {
    toast('Starting AI transcription and video styling...', 'info');
    const res = await post({
      action: 'subtitles_process',
      job_id: uploadedVideoJobId,
      font,
      style_key: styleKey,
      position,
      chunk_size: chunkSize,
      font_size: fontSize,
      text_case: textCase
    }, 240000);

    toast('Subtitled video ready!', 'success');
    $('captionProgressText').textContent = 'Done! Your subtitled video is ready to preview and download.';

    const job = res.job || {};
    $('captionResultCard').hidden = false;
    const player = $('captionResultVideoPlayer');
    player.src = job.video_url;
    if (job.thumb_url) player.poster = job.thumb_url;
    player.load();

    $('btnDownloadCaptionedVideo').href = job.download_url;
    $('btnDownloadCaptionedVideoSecondary').href = job.download_url;

    $('resultFontUsed').textContent = job.font || font;
    $('resultStyleUsed').textContent = job.style_key || styleKey;
    $('resultPositionUsed').textContent = job.position || position;
    $('resultWordsCount').textContent = `${job.words_count || 0} words (${formatDuration(job.duration || 0)})`;

    $('captionResultCard').scrollIntoView({ behavior: 'smooth' });
    await refresh();
  } catch (err) {
    toast(`Failed to burn subtitles: ${err.message}`, 'error');
    $('captionProgressText').textContent = `Error: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span>✨</span> Generate &amp; Burn Subtitles';
  }
};

$('btnReapplyStyle').onclick = () => {
  $('captionsStudioSection').scrollIntoView({ behavior: 'smooth' });
};

// Preview Subtitled Video in Result Card when clicked in history
document.addEventListener('click', e => {
  const prevBtn = e.target.closest('.preview-sub-video-btn') || e.target.closest('.preview-sub-thumb');
  if (prevBtn) {
    const videoUrl = prevBtn.dataset.videoUrl;
    if (videoUrl) {
      $('captionResultCard').hidden = false;
      const player = $('captionResultVideoPlayer');
      player.src = videoUrl;
      player.load();
      player.play().catch(() => {});
      $('captionResultCard').scrollIntoView({ behavior: 'smooth' });
    }
  }
});

// INITIALIZATION & PERIODIC REFRESH
if (window.location.hash) {
  const initHash = window.location.hash.replace('#', '');
  if (tabTitles[initHash]) switchTab(initHash);
}
refresh();
setInterval(refresh, 6000);
