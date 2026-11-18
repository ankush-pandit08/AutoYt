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
}

// Render Clips Grid
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
      <div class="clip-card" data-clip-id="${esc(clip.id)}">
        <div class="clip-media-box" data-id="${esc(clip.id)}" title="Click to preview video in Cinema player">
          <img src="/media/podcast/clip/${esc(clip.id)}/thumbnail.png" alt="Short preview" class="clip-poster" loading="lazy">
          <div class="play-overlay">
            <div class="play-circle">▶</div>
            <span class="play-caption">Play Preview</span>
          </div>
          <span class="clip-score-badge">★ ${score}</span>
          <span class="clip-duration-badge">⏱ ${dur}</span>
          <div class="clip-status-banner">
            <span class="badge badge-${esc(clip.status || 'candidate')}">${esc(clip.status || 'candidate')}</span>
          </div>
        </div>

        <div class="clip-info">
          <span class="clip-creator">🎙️ ${esc(clip.source_id || 'Podcast')} • Clip #${esc(clip.clip_index || 1)}</span>
          <h4 class="clip-title" title="${esc(clip.generated_title || 'Untitled Short')}">
            ${esc(clip.generated_title || 'Untitled Short')}
          </h4>

          <div class="clip-actions">
            ${isUploaded ? `
              <a href="${esc(ytUrl)}" target="_blank" rel="noopener noreferrer" class="clip-yt-link">
                ▶ View on YouTube Shorts ↗
              </a>
            ` : `
              <button class="btn btn-secondary btn-sm edit-clip-btn" data-id="${esc(clip.id)}">✨ Preview</button>
              ${isCandidate ? `
                <button class="btn btn-success btn-sm approve-clip-btn" data-id="${esc(clip.id)}">✓ Approve</button>
              ` : ''}
              ${isApproved ? `
                <button class="btn btn-primary btn-sm upload-clip-btn btn-full" data-id="${esc(clip.id)}">🚀 Upload to YouTube</button>
              ` : ''}
            `}
            <button class="btn btn-secondary btn-sm delete-clip-btn" data-id="${esc(clip.id)}" title="Delete Clip">Delete</button>
          </div>
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
  if (!sources.length) {
    container.innerHTML = `<p class="table-loading">No podcast sources configured.</p>`;
    return;
  }

  container.innerHTML = sources.map(src => {
    const isEnabled = src.enabled !== false;
    return `
      <div class="source-item">
        <div class="source-info">
          <h4>${esc(src.name)} (${esc(src.show_name)})</h4>
          <p>${esc(src.youtube_channel_url || 'YouTube Channel')}</p>
        </div>
        <button class="btn ${isEnabled ? 'btn-secondary' : 'btn-primary'} btn-sm toggle-source-btn" data-id="${esc(src.id)}">
          ${isEnabled ? 'Enabled ✓' : 'Disabled'}
        </button>
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
document.querySelectorAll('.nav-item').forEach(item => {
  item.onclick = e => {
    e.preventDefault();
    const tab = item.dataset.tab;
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    item.classList.add('active');

    // Hide all sections
    $('clipsStudioSection').hidden = tab !== 'clipsStudio';
    $('manualCreatorSection').hidden = tab !== 'manualCreator' && tab !== 'clipsStudio';
    $('episodesFeedSection').hidden = tab !== 'episodesFeed';
    $('sourcesSettingsSection').hidden = tab !== 'sourcesSettings';
    $('automationScheduleSection').hidden = tab !== 'automationSchedule';

    const titles = {
      clipsStudio: ['Channel overview & Clips Studio', 'Review AI-extracted 9:16 vertical Shorts and publish to YouTube in private mode'],
      automationSchedule: ['Daily Shorts Automation & Schedule', 'Automated background publisher running 5 Shorts every evening between 7:00 PM and 9:00 PM IST'],
      manualCreator: ['Create Shorts Clip', 'Paste any podcast or video URL to automatically extract vertical Shorts with animated captions'],
      episodesFeed: ['Discovered Episodes Feed', 'Podcast episodes discovered from your creators, ranked by topic relevance'],
      sourcesSettings: ['Creators & Channel Settings', 'Configure podcast source channels and YouTube publishing settings']
    };

    if (titles[tab]) {
      $('pageTitle').textContent = titles[tab][0];
      $('pageSubtitle').textContent = titles[tab][1];
    }
  };
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
  if (e.target.matches('.toggle-source-btn')) {
    const srcId = e.target.dataset.id;
    try {
      const res = await post({ action: 'podcast_toggle_source', source_id: srcId });
      toast(res.message || 'Source updated.', 'success');
      await refresh();
    } catch (err) {
      toast(err.message, 'error');
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

// YOUTUBE RECONNECT
$('ytReconnectBtn').onclick = async () => {
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
  }
};

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

// INITIALIZATION & PERIODIC REFRESH
refresh();
setInterval(refresh, 6000);
