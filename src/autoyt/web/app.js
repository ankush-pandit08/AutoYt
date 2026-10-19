/* Local UI: no CDNs, analytics, remote assets or stored credentials. */
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = ['Find topic','Verify facts','Write script','Voiceover','Find visuals','Edit & captions','Quality check','Upload'];
const terminal = new Set(['PUBLISHED','AWAITING_REVIEW','FAILED','SKIPPED','READY_TO_UPLOAD']);
let state, token, pending, refreshing = false;
const time = value => value ? new Date(value).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',timeZone:state?.settings.timezone || 'Asia/Kolkata'}) : '—';
const date = value => new Date(value).toLocaleDateString('en-IN',{day:'numeric',month:'short',timeZone:state?.settings.timezone || 'Asia/Kolkata'});
const nice = value => String(value).replaceAll('_',' ').toLowerCase().replace(/^./, c => c.toUpperCase());
function badge(value) {
  const kind = ['PUBLISHED','AWAITING_REVIEW'].includes(value) ? 'success' : value === 'FAILED' ? 'error' : value === 'SKIPPED' ? 'warning' : value === 'READY_TO_UPLOAD' ? 'neutral' : 'working';
  return `<span class="badge ${kind}">${esc(value === 'AWAITING_REVIEW' ? 'Private upload' : nice(value))}</span>`;
}
function link(url, label) {
  try { if(new URL(url).protocol !== 'https:') return esc(label); } catch { return esc(label); }
  return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`;
}
async function api(path, options) {
  const res = await fetch(path, {...options, cache:'no-store'});
  const data = await res.json();
  if(!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; }
function renderJobs() {
  if(!state) return;
  const filter = $('filter').value;
  const jobs = state.jobs.filter(j => filter === 'all' || (filter === 'active' ? !terminal.has(j.state) : j.state === filter));
  $('jobs').innerHTML = jobs.length ? jobs.map(j => `<tr><td><div class="video-cell">${j.has_cover ? `<img class="video-thumb" loading="lazy" src="/media/${j.id}/cover.png" alt="">` : '<span class="video-thumb video-placeholder">▶</span>'}<div><div class="video-title">${esc(j.title)}</div><div class="video-id">${esc(j.id)}${j.dry_run ? ' · PREVIEW ONLY' : ''}</div></div></div></td><td>${badge(j.state)}</td><td><div class="progress-track"><div class="progress-fill"></div></div><div class="progress-label">${j.progress}%</div></td><td class="date">${esc(date(j.created_at))}<br>${esc(time(j.created_at))}</td><td><button class="text-button" data-job="${j.id}">View →</button></td></tr>`).join('') : '<tr><td colspan="5" class="empty">No runs in this view. Your next run will appear here.</td></tr>';
  // CSP keeps inline styles disabled, so set progress widths through the DOM.
  $('jobs').querySelectorAll('.progress-fill').forEach((el,i) => { el.removeAttribute('style'); el.style.width = jobs[i].progress + '%'; });
}
function render() {
  const s = state, cfg = s.settings;
  $('connection').textContent = 'Connected to localhost';
  $('offline').hidden = true;
  $('autoBadge').textContent = s.auto_enabled ? 'ON' : 'PAUSED';
  $('autoBadge').className = `badge ${s.auto_enabled ? 'success' : 'neutral'}`;
  $('autoDescription').textContent = `${cfg.slots.length} daily slots · Up to ${s.daily.limit} automatic attempts/day · ${cfg.visibility} uploads ${cfg.uploads_enabled ? 'enabled' : 'disabled'}`;
  $('toggle').textContent = s.auto_enabled ? 'Pause auto mode' : 'Resume auto mode';
  $('toggle').disabled = !token;
  $('run').textContent = cfg.uploads_enabled ? `＋ Run now · ${cfg.visibility}` : '＋ Run now';
  $('run').disabled = $('preview').disabled = s.busy || !token;
  $('customRun').disabled = $('customPreview').disabled = s.busy || !token;
  $('customUploadNote').textContent = `Voice: ${cfg.tts_engine || ''} · ${cfg.voice}. ${cfg.uploads_enabled ? `Create uploads ${cfg.visibility === 'public' ? 'PUBLICLY' : 'privately'} after all checks pass.` : 'Uploads disabled.'} Preview never uploads.`;
  $('uploaded').textContent = s.daily.uploaded;
  $('target').textContent = `Auto: up to ${s.daily.limit}/day · manual runs have no app limit`;
  $('attempts').textContent = s.daily.attempts;
  $('failed').textContent = s.daily.failed;
  $('next').textContent = s.next_run ? time(s.next_run) : 'Paused';
  $('timezone').textContent = cfg.timezone;
  const active = s.jobs.find(j => !terminal.has(j.state));
  const focus = s.busy ? active : null;
  $('liveTitle').textContent = focus ? focus.title : 'Ready for the next technology story';
  $('liveBadge').textContent = s.busy ? 'IN PROGRESS' : 'IDLE';
  $('liveBadge').className = `badge ${s.busy ? 'working' : 'neutral'}`;
  $('pipeline').innerHTML = labels.map((label,i) => `<div class="step ${focus ? (i < focus.stage ? 'done' : i === focus.stage ? 'current' : '') : ''}"><i>${focus && i < focus.stage ? '✓' : i+1}</i><span>${label}</span></div>`).join('');
  $('liveNote').textContent = s.notice || 'Every upload must pass evidence, license and media checks.';
  $('disk').textContent = `${s.disk_free_gb} GB available`;
  $('scheduleCaption').textContent = `${cfg.slots.length} daily slots · ${cfg.timezone}${cfg.daily_script_batch ? ` · Scripts prepared at ${cfg.morning_plan_time}` : ''}`;
  const nextSlot = s.next_run ? new Date(s.next_run).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',timeZone:cfg.timezone}) : null;
  $('slots').innerHTML = cfg.slots.map(slot => `<span class="slot ${slot === nextSlot ? 'next' : ''}">${esc(slot)}</span>`).join('');
  const config = [['Narration',`${cfg.script_style === 'hinglish' ? 'Hinglish' : cfg.language === 'hi' ? 'Hindi' : 'English'} · ${cfg.voice}`],['AI model',cfg.model],['Topic focus',`${cfg.topic_focus === 'ai_coding' ? 'AI · LLMs · Coding updates' : cfg.prefer_tech_insights ? 'Fresh tech insights' : 'Technology news'} · ${cfg.creator_reference_count || 0} creator references`],['Media',`${cfg.public_images ? 'Wikimedia Commons · ' : ''}${cfg.pixabay ? 'Pixabay · ' : ''}${cfg.pexels ? 'Pexels · ' : ''}Original graphics`],['Google authorization',cfg.token_present ? 'Token saved locally' : 'Setup needed'],['Upload mode',cfg.uploads_enabled ? cfg.visibility.toUpperCase() : 'Disabled']];
  $('configuration').innerHTML = config.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
  $('events').innerHTML = s.events.length ? s.events.map(e=>`<div class="event"><time>${esc(date(e.created_at))}<br>${esc(time(e.created_at))}</time><div><p>${badge(e.state)} ${esc(e.message)}</p><small>${esc(e.job_id)}</small></div></div>`).join('') : '<p class="small-note">Stage changes will appear when a run starts.</p>';
  $('morningScripts').hidden = !cfg.daily_script_batch;
  const plan = s.daily_plan;
  $('planSummary').textContent = plan ? `${plan.entries.length}/${cfg.slots.length} verified scripts · ${nice(plan.state)} · ${plan.date}` : `Next morning preparation: ${cfg.morning_plan_time} · ${cfg.timezone}`;
  $('planEntries').innerHTML = plan?.entries.length ? plan.entries.map(e=>`<details class="event"><summary>${esc(e.slot)} · ${esc(e.title)}</summary><p>${esc(e.script)}</p></details>`).join('') : '<p class="small-note">No prepared scripts for today. Scheduled video runs use this morning’s plan; they do not search for replacement stories.</p>';
  if(plan?.error) $('planEntries').innerHTML += `<p class="error-text">${esc(plan.error)}</p>`;
  if(plan?.rejected?.length) $('planEntries').innerHTML += `<details><summary>${plan.rejected.length} stories did not pass checks</summary><ul>${plan.rejected.map(e=>`<li>${esc(e.topic)} — ${esc(e.reason)}</li>`).join('')}</ul></details>`;
  $('refreshed').textContent = `Updated ${time(s.now)}`;
  $('writingWarning').textContent = s.writing_warning;
  renderJobs();
}
async function refresh() {
  if(refreshing) return;
  refreshing = true;
  try {
    const next = await api('/api/status');
    if(!token || (state && next.started_at !== state.started_at)) token = (await api('/api/session')).token;
    state = next; render();
  }
  catch { $('connection').textContent = 'Disconnected'; $('offline').hidden = false; $('customRun').disabled = $('customPreview').disabled = $('run').disabled = $('preview').disabled = $('toggle').disabled = true; }
  finally { refreshing = false; }
}
async function action(name, job_id, manual_input) {
  try {
    const result = await api('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-AutoYT-Token':token},body:JSON.stringify({action:name,job_id,manual_input})});
    toast(result.message); await refresh();
  } catch(error) { toast(error.message); }
}
function confirmAction(name, job_id, manual_input) {
  pending = {name,job_id,manual_input};
  const preview = name === 'preview';
  $('confirmTitle').textContent = preview ? 'Generate a preview?' : name === 'retry' ? 'Retry this failed run?' : 'Start a new run?';
  $('confirmText').textContent = preview ? 'Runs the full local pipeline without uploading. This can take several minutes.' : name === 'retry' ? 'Resumes saved progress and keeps the visibility recorded for this job. If it passes every check, a live job can upload to YouTube.' : `Finds a fresh tech story and runs the full pipeline. ${state.settings.uploads_enabled ? `If it passes every check, the video uploads ${state.settings.visibility === 'public' ? 'PUBLICLY' : 'privately'} to your pinned YouTube channel.` : 'Uploads are currently disabled.'} Manual runs have no app daily limit. Provider quotas and quality checks still apply.`;
  $('confirmAction').textContent = preview ? 'Generate preview' : name === 'retry' ? 'Retry run' : 'Start run';
  if(manual_input) {
    $('confirmTitle').textContent = preview ? 'Preview your input?' : 'Create from your input?';
    $('confirmText').textContent = `${manual_input.mode === 'script' ? 'Uses your exact narration, with fact checks and scene planning.' : 'Researches only your chosen topic and writes a Hinglish script.'} No unrelated topic will be substituted. ${preview ? 'This creates a preview without uploading.' : state.settings.uploads_enabled ? `If all checks pass, the video uploads ${state.settings.visibility === 'public' ? 'PUBLICLY' : 'privately'} to your pinned channel.` : 'Uploads are disabled.'}`;
  }
  $('confirm').showModal();
}
async function details(id) {
  $('detailBody').textContent = 'Loading run…'; $('details').showModal();
  try {
    const d = await api(`/api/jobs/${id}`), j = state.jobs.find(j=>j.id===id);
    const assets = d.media_assets || [], sources = d.research?.sources || [];
    $('detailBody').innerHTML = `<p class="detail-title">${esc(d.script?.title || d.topic || 'Untitled run')}</p><div class="detail-status">${badge(d.state)}<span class="detail-code">${esc(id)}${d.dry_run ? ' · PREVIEW ONLY' : ''}</span></div>${d.error ? `<p class="error-text">${esc(d.error)}</p>` : ''}${j?.has_video ? `<video class="detail-video" controls preload="metadata" src="/media/${id}/final.mp4"></video>` : ''}<div class="detail-links">${d.youtube?.url ? link(d.youtube.url,'Open on YouTube') : ''}${d.state==='FAILED' ? `<button class="button secondary" data-retry="${id}" ${state.busy?'disabled':''}>Retry from checkpoint</button>` : ''}</div><div class="detail-section"><h3>Script</h3><p>${esc(d.script?.script || 'No completed script yet.')}</p></div>${d.research_attempts?.length ? `<div class="detail-section"><h3>Other stories checked</h3><ul>${d.research_attempts.map(a=>`<li>${esc(a.topic)}<br>${esc(a.reason)}${a.sources?.length ? `<ul>${a.sources.map(source=>`<li>${link(source.url,source.result)}</li>`).join('')}</ul>` : ''}</li>`).join('')}</ul></div>` : ''}<div class="detail-section"><h3>Verified sources</h3><ul>${sources.length ? sources.map(s=>`<li>${link(s.url,s.title || s.publisher || 'Source')}${s.primary ? ' · Primary' : ''}</li>`).join('') : '<li>Research has not completed.</li>'}</ul></div><div class="detail-section"><h3>Visual credits & free licenses</h3><p>${d.media_license_audit?.passed ? 'Recorded license policy check passed.' : 'License audit has not completed for this run.'} Provider restrictions and third-party rights still apply.</p><ul>${assets.length ? assets.map(a=>`<li>${esc(a.attribution || a.provider || 'Original graphics')}<br>${esc(a.license)} ${a.source_url ? link(a.source_url,'Source') : ''}</li>`).join('') : '<li>No visuals selected yet.</li>'}</ul></div><div class="detail-section"><h3>Media quality</h3><p>${d.validation?.passed ? 'Passed automated video, audio, subtitle and duration checks.' : 'No completed quality validation.'}</p></div>`;
  } catch(error) { $('detailBody').textContent = error.message; }
}
$('filter').addEventListener('change',renderJobs);
$('jobs').addEventListener('click',e=>{const b=e.target.closest('[data-job]');if(b) details(b.dataset.job);});
$('detailBody').addEventListener('click',e=>{const b=e.target.closest('[data-retry]');if(b){$('details').close();confirmAction('retry',b.dataset.retry);}});
$('closeDetails').onclick=()=>{$('details').querySelector('video')?.pause();$('details').close();};
$('details').addEventListener('close',()=>{$('details').querySelector('video')?.pause();});
$('preview').onclick=()=>$('customContent').value.trim() ? customRun(true) : confirmAction('preview');
$('run').onclick=()=>$('customContent').value.trim() ? customRun(false) : confirmAction('run');
$('toggle').onclick=()=>action(state.auto_enabled?'pause':'resume');
$('cancelAction').onclick=()=>$('confirm').close();
$('confirmAction').onclick=()=>{const task=pending;$('confirm').close();action(task.name,task.job_id,task.manual_input);};
function updateCustomInput() {
  const script = $('customMode').value === 'script', content = $('customContent');
  $('customTopicRow').hidden = !script;
  $('customTopic').required = script;
  content.maxLength = script ? 5000 : 180;
  content.rows = script ? 6 : 3;
  $('customLabel').textContent = script ? 'Paste your narration' : 'What should this video be about?';
  $('customHelp').textContent = script ? '60–115 words, at least six complete sentences. Hindi words in Devanagari, English tech words as normal. Your words stay unchanged; unsupported facts stop the run.' : 'We research only this topic and write an engaging Hinglish script. If it cannot be verified, the run stops.';
  $('customCount').textContent = script ? `${content.value.trim().split(/\s+/).filter(Boolean).length} / 60–115 words` : `${content.value.length} / 180 characters`;
}
function customRun(preview) {
  if(!state || state.busy || !$('customForm').reportValidity()) return;
  const input = {mode:$('customMode').value, content:$('customContent').value.trim(), topic:$('customTopic').value.trim(), source_urls:$('customSources').value.split(/\n/).map(s=>s.trim()).filter(Boolean)};
  const count = input.content.split(/\s+/).length;
  if(input.mode === 'script' && (count < 60 || count > 115)) return toast('Use 60–115 words for your script.');
  if(input.source_urls.length > 6) return toast('Add up to six source links.');
  confirmAction(preview ? 'preview' : 'run', undefined, input);
}
$('customMode').addEventListener('change', updateCustomInput);
$('customContent').addEventListener('input', updateCustomInput);
$('customForm').addEventListener('submit', e=>{e.preventDefault();customRun(false);});
$('customPreview').onclick=()=>customRun(true);
async function boot(){try{token=(await api('/api/session')).token;}catch{}await refresh();setInterval(refresh,5000);}
boot();
