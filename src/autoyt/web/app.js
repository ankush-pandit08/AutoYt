import {TERMINAL,STAGES,MODE_NAMES,nice,jobStatus,filterJobs,runIntent,validateManual,confirmation,evidenceFor,mergeSnapshot,sameSelection,nextRunCountdown} from './ui.mjs';

const $=id=>document.getElementById(id);
const esc=value=>String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const formIds={script:['scriptModelForm','scriptModelMessage','scriptSaveState','saveScriptModel','resetScript'],render:['renderForm','renderMessage','renderSaveState','saveRender','resetRender'],voice:['voiceForm','voiceMessage','voiceSaveState','saveVoice','resetVoice']};
formIds.schedule=['scheduleForm','scheduleMessage','scheduleSaveState','saveSchedule','resetSchedule'];
const forms=Object.fromEntries(Object.keys(formIds).map(key=>[key,{initialized:false,dirty:false,saving:false,version:null,base:null}]));
let state=null,token=null,connected=false,refreshPromise=null,requestPending=false,pending=null,pendingContext=null;
let serverClockOffset=0;
let jobsSignature='',toastTimer=null,detailId=null,detailRequest=0,detailJob=null;
let voiceLoading=false,voicePage=null,voiceTestingId=null,voiceSubmitting=false,voiceProvider='';
let voiceCatalogRequested=false,voiceCatalogLoaded=false;
const draftVoices={};
const time=value=>value ? new Date(value).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',timeZone:state?.settings.timezone || 'Asia/Kolkata'}) : '—';
const date=value=>value ? new Date(value).toLocaleDateString('en-IN',{day:'numeric',month:'short',timeZone:state?.settings.timezone || 'Asia/Kolkata'}) : '—';
const language=()=>state?.settings.language==='en' ? 'English' : state?.settings.script_style==='hinglish' ? 'Hinglish':'Hindi';
const youtubeMode=()=>state?.settings.content_mode==='youtube_insights';
const busy=()=>!!state?.busy || requestPending;
const canAct=()=>connected && !!token && !!state;
const badge=job=>{const status=jobStatus(typeof job==='string' ? {state:job}:job);return `<span class="badge ${status.tone}">${esc(status.label)}</span>`;};
const link=(url,label)=>{try{if(new URL(url).protocol!=='https:') return esc(label);}catch{return esc(label);}return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`;};

async function api(path,options={},timeoutMs=15000) {
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeoutMs);
  try {
    const response=await fetch(path,{...options,cache:'no-store',signal:controller.signal});
    let data;try{data=await response.json();}catch{throw new Error('The server returned an unreadable response. Reconnect and try again.');}
    if(!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
    return data;
  } catch(error) {
    if(error.name==='AbortError') throw new Error(timeoutMs===25000?'Voice list timed out. Use Refresh voices to try again.':'Request timed out. Check the video library before starting another run.');
    throw error;
  } finally {clearTimeout(timer);}
}
const post=body=>api('/api/action',{method:'POST',headers:{'Content-Type':'application/json','X-AutoYT-Token':token},body:JSON.stringify(body)},body.action==='voice_list'?25000:15000);
function message(key,text='',tone='') {const el=$(formIds[key][1]);el.textContent=text;el.dataset.tone=tone;}
function toast(text,tone='success') {
  clearTimeout(toastTimer);$('toastText').textContent=text;$('toast').dataset.tone=tone;$('toast').setAttribute('role',tone==='error'?'alert':'status');$('toast').hidden=false;
  toastTimer=setTimeout(()=>$('toast').hidden=true,tone==='error'?10000:6500);
}
$('dismissToast').onclick=()=>{$('toast').hidden=true;clearTimeout(toastTimer);};
$('reconnect').onclick=()=>refresh();

function settingValue(key) {
  if(key==='schedule') return {times:Array.from({length:6},(_,i)=>$('scheduleTime'+(i+1)).value),timezone:$('scheduleTimezone').value.trim()};
  if(key==='script') return {provider:$('scriptProvider').value,...($('scriptProvider').value==='ollama'?{model:$('localScriptModel').value}:{})};
  if(key==='render') return {render_mode:$('renderMode').value,render_fallback:$('renderFallback').checked};
  return {provider:$('voiceProvider').value,language:$('scriptLanguage').value,
    voice:$('voiceProvider').value==='elevenlabs' ? $('elevenVoice').value:$('localVoice').value.trim()};
}
function applySetting(key,value) {
  if(key==='schedule') {value.times.forEach((time,i)=>$('scheduleTime'+(i+1)).value=time);$('scheduleTimezone').value=value.timezone;}
  else if(key==='script') {$('scriptProvider').value=value.provider;$('localScriptModel').value=value.model || 'gemma3:4b';}
  else if(key==='render') {$('renderMode').value=value.render_mode;$('renderFallback').checked=value.render_fallback;}
  else {
    Object.assign(draftVoices,state.voice_settings.voices);
    $('voiceProvider').value=value.provider;$('scriptLanguage').value=value.language;$('localVoice').value=value.voice;voiceProvider=value.provider;
    if(value.provider==='elevenlabs') ensureVoiceOption(value.voice);
    showVoiceFields();
  }
}
function syncForm(key,force=false) {
  const form=forms[key],record=state[`${key}_settings`];
  if(!record) return;
  if(force || (!form.dirty && !form.saving && (!form.initialized || record.version!==form.version))) {
    applySetting(key,record.selection);Object.assign(form,{initialized:true,dirty:false,version:record.version,base:structuredClone(record.selection)});
  }
  const conflict=form.dirty && record.version!==form.version;
  const status=$(formIds[key][2]);
  status.textContent=form.saving?'Saving…':conflict?'Changed elsewhere':form.dirty?'Unsaved changes':'Saved';
  status.className=`badge ${conflict || form.dirty?'warning':form.saving?'working':'success'}`;
  if(conflict && !form.saving) message(key,'Settings changed in another session. Discard your draft to load the latest saved settings, then edit again.','warning');
}
function dirty(key) {
  const form=forms[key];form.dirty=!sameSelection(settingValue(key),form.base);
  message(key);if(state) syncForm(key);updateControls();updateSummary();
}
function updateControls() {
  const unavailable=!canAct();
  for(const id of ['run','preview','customRun','customPreview']) $(id).disabled=unavailable || busy();
  $('toggle').disabled=unavailable || requestPending;
  $('reconnect').disabled=!!refreshPromise;
  $('refreshDetails').disabled=unavailable || !detailId;
  for(const key of Object.keys(forms)) {
    const form=forms[key],ids=formIds[key];
    $(ids[3]).disabled=unavailable || !form.dirty || form.saving;
    $(ids[3]).textContent=form.saving?'Saving…':{script:'Save script provider',render:'Save video style',voice:'Save voice settings',schedule:'Save schedule'}[key];
    $(ids[4]).disabled=unavailable || !form.dirty || form.saving;
    $(ids[0]).querySelectorAll('input,select').forEach(el=>el.disabled=unavailable || form.saving);
    $(ids[0]).setAttribute('aria-busy',String(form.saving));
  }
  const voiceUnavailable=unavailable || busy() || forms.voice.saving || voiceLoading || voiceSubmitting || !!voiceTestingId;
  $('testVoice').disabled=voiceUnavailable;
  $('testVoice').textContent=voiceSubmitting || voiceTestingId ? 'Generating sample…':'Test voice';
  const missingEleven=state && !state.voice_keys?.elevenlabs;
  $('elevenVoice').disabled=unavailable || voiceLoading || forms.voice.saving || missingEleven;
  $('loadVoices').disabled=$('moreVoices').disabled=unavailable || voiceLoading || forms.voice.saving || missingEleven;
  const changed=pendingContext && state && (pendingContext.script!==state.script_settings.version || pendingContext.voice!==state.voice_settings.version || pendingContext.visibility!==state.settings.visibility || pendingContext.uploads!==state.settings.uploads_enabled || pendingContext.server!==state.started_at);
  $('confirmAction').disabled=unavailable || busy() || !!changed;
  $('confirmPendingNote').hidden=!(busy() || changed);
  $('confirmPendingNote').textContent=changed?'Production settings changed. Cancel this confirmation and review the current settings before continuing.':busy()?'Another task is running. Wait for it to finish, or cancel this confirmation.':'';
}
async function saveSetting(key) {
  if(!canAct() || forms[key].saving || !forms[key].dirty) return;
  const form=forms[key],element=$(formIds[key][0]);if(!element.reportValidity()) return;
  const selection=settingValue(key);form.saving=true;message(key,'Saving your settings…');syncForm(key);updateControls();
  try {
    const result=await post({action:{script:'save_script',render:'save_render',voice:'save_voice',schedule:'save_schedule'}[key],
      ...(key==='voice'?{selection,version:form.version}:{manual_input:{selection,version:form.version}})});
    const record=result[`${key}_settings`];state[`${key}_settings`]=record;
    if(key==='schedule') applySetting(key,record.selection);
    Object.assign(form,{version:record.version,base:structuredClone(record.selection),dirty:false});
    message(key,result.message,'success');await refresh();
  } catch(error) {message(key,error.message,'error');}
  finally {form.saving=false;syncForm(key);updateControls();}
}
for(const key of Object.keys(forms)) {
  $(formIds[key][0]).onsubmit=event=>{event.preventDefault();saveSetting(key);};
  $(formIds[key][4]).onclick=()=>{if(key==='voice') clearVoiceSample();syncForm(key,true);message(key,'Draft discarded. Showing saved settings.');updateControls();updateSummary();updateScriptModelInfo();};
}
$('scheduleForm').oninput=()=>dirty('schedule');
$('scriptProvider').onchange=$('localScriptModel').onchange=()=>{dirty('script');updateScriptModelInfo();};
$('renderMode').onchange=$('renderFallback').onchange=()=>dirty('render');
function updateScriptModelInfo() {
  const info=state?.script_providers?.[$('scriptProvider').value];
  $('localScriptModelRow').hidden=$('scriptProvider').value!=='ollama';
  if(info?.local) {$('scriptModelInfo').textContent=`${$('localScriptModel').value} · Runs on this PC; no API key or cloud quota. CPU generation can take several minutes. Ollama and the selected model must be installed; checked when saving.`;return;}
  $('scriptModelInfo').textContent=info?`${info.model}${info.free_only?' · Free-only; OpenRouter request limits apply':''} · ${info.key_present?'API key configured. Quota is checked when used.':'API key missing. Add it to the local environment before saving.'}`:'Choose Groq, Gemini, OpenRouter or Ollama local models.';
}
function updateSummary() {
  if(!state) return;
  const cfg=state.settings,provider=state.script_settings.selection.provider,override=$('runStyle').value;
  const mode=override==='default'?state.render_settings.selection.render_mode:override;
  $('summaryModel').textContent=`${nice(provider)} · ${cfg.model}`;
  $('summaryVoice').textContent=`${language()} · ${cfg.voice}`;
  $('summaryStyle').textContent=MODE_NAMES[mode] || 'Choose a style';
  $('summaryUpload').textContent=cfg.uploads_enabled?`${nice(cfg.visibility)} upload after confirmation`:'Uploads disabled · local video only';
  $('runStyle').options[0].textContent=`Use saved default · ${MODE_NAMES[state.render_settings.selection.render_mode]}`;
}
$('runStyle').onchange=updateSummary;

function renderJobs() {
  if(!state) return;
  const jobs=filterJobs(state.jobs,$('filter').value,$('searchJobs').value),signature=JSON.stringify(jobs);
  $('libraryCount').textContent=`${jobs.length} shown · ${state.jobs.length} recent runs${state.jobs.length===100?' (latest 100)':''}`;
  if(signature===jobsSignature) return;jobsSignature=signature;
  const focused=document.activeElement?.dataset?.job;
  $('jobs').innerHTML=jobs.length?jobs.map(job=>{
    const progress=['PUBLISHED','AWAITING_REVIEW','READY_TO_UPLOAD'].includes(job.state)?100:Math.min(100,job.progress);
    const stage=STAGES[Math.min(job.stage,7)] || 'Preparing';
    const note=job.state==='FAILED'?`Stopped · ${stage}`:job.state==='READY_TO_UPLOAD'?'Complete':job.state==='SKIPPED'?'No video created':`${progress}%`;
    return `<tr><td><div class="video-cell">${job.has_cover?`<img class="video-thumb" loading="lazy" src="/media/${job.id}/cover.png" alt="">`:'<span class="video-thumb video-placeholder" aria-hidden="true">▶</span>'}<div><div class="video-title">${esc(job.title)}</div><div class="video-id">${esc(job.id)}${job.dry_run?' · Preview only':''}</div></div></div></td><td>${badge(job)}</td><td><progress max="100" value="${progress}" aria-label="${esc(note)}">${progress}%</progress><div class="progress-label">${esc(note)}</div></td><td class="date">${esc(date(job.created_at))}<br>${esc(time(job.created_at))}</td><td><button class="text-button" data-job="${job.id}" aria-label="View details: ${esc(job.title)}">View →</button></td></tr>`;
  }).join(''):`<tr><td colspan="5" class="empty"><strong>${state.jobs.length?'No matching videos':'Your first video starts here'}</strong>${state.jobs.length?'Try a different status or search.':'Create a preview to try your production settings.'}<br>${state.jobs.length?'<button class="button secondary" id="clearSearch">Clear filters</button>':'<a class="button secondary" href="#create">Create a preview</a>'}</td></tr>`;
  if(focused) $('jobs').querySelector(`[data-job="${focused}"]`)?.focus({preventScroll:true});
}
function render() {
  if(!state) return;
  const s=state,cfg=s.settings;
  for(const key of Object.keys(forms)) syncForm(key);
  updateScriptModelInfo();showVoiceFields();renderVoiceResult();updateSummary();
  $('connection').textContent='Connected to localhost';$('connection').dataset.state='online';$('offline').hidden=true;
  $('autoBadge').textContent=s.auto_enabled?'ON':'PAUSED';$('autoBadge').className=`badge ${s.auto_enabled?'success':'neutral'}`;
  $('autoDescription').textContent=`${cfg.slots.length} slots per day · Up to ${s.daily.limit} automatic attempts · ${cfg.uploads_enabled?cfg.visibility+' uploads':'uploads disabled'}`;
  $('toggle').textContent=s.auto_enabled?'Pause automatic runs':'Resume automatic runs';
  $('run').textContent=cfg.uploads_enabled?cfg.visibility==='public'?'Find topic & publish':'Find topic & upload privately':'Find topic & create';
  $('customRun').textContent=cfg.uploads_enabled?cfg.visibility==='public'?'Create & publish':'Create & upload privately':'Create video';
  $('automaticNote').textContent=`Uses saved ${MODE_NAMES[s.render_settings.selection.render_mode]} style and ${nice(s.script_settings.selection.provider)}. Previews stay local.`;
  $('customUploadNote').textContent=cfg.uploads_enabled?`Create publishes ${cfg.visibility==='public'?'publicly':'privately'} after confirmation. Preview never uploads.`:'Both actions create local videos. Uploads are disabled.';
  $('uploaded').textContent=s.daily.uploaded;$('attempts').textContent=s.daily.attempts;$('failed').textContent=s.daily.failed;
  $('target').textContent=`${s.daily.auto_attempts ?? 0} / ${s.daily.limit} automatic attempts · manual unlimited`;
  $('next').textContent=s.next_run?time(s.next_run):s.auto_enabled?'No upcoming slot':'Paused';
  updateCountdown();
  $('timezone').textContent=s.next_run?`${date(s.next_run)} · ${cfg.timezone}`:cfg.timezone;
  const focus=s.jobs.find(job=>!TERMINAL.has(job.state)),testing=s.voice_preview?.state==='GENERATING';
  $('liveTitle').textContent=s.busy?(focus?.title || (testing?'Generating your voice sample':'Preparing a new run…')):'Ready when you are';
  $('liveBadge').textContent=s.busy?(testing && !focus?'VOICE TEST':'IN PROGRESS'):'IDLE';$('liveBadge').className=`badge ${s.busy?'working':'neutral'}`;
  const stages=youtubeMode()?['Find topic','Research sources','Write script',...STAGES.slice(3)]:STAGES;
  $('pipeline').innerHTML=stages.map((label,index)=>`<li class="step ${s.busy&&focus?(index<focus.stage?'done':index===focus.stage?'current':''):''}" ${s.busy&&focus&&index===focus.stage?'aria-current="step"':''}><i aria-hidden="true">${s.busy&&focus&&index<focus.stage?'✓':index+1}</i><span>${esc(label)}</span></li>`).join('');
  const researchProgress=s.busy && focus?.state==='RESEARCHING' ? focus.research_progress:null;
  const elapsed=researchProgress?Math.max(0,Math.floor((new Date(s.now)-new Date(researchProgress.started_at))/1000)):0;
  $('liveNote').textContent=researchProgress?`${researchProgress.message} · ${elapsed}s elapsed · ${researchProgress.limit_seconds}s research budget`:s.notice || 'Generated scripts use retrieved evidence. User-supplied narration stays unchanged; media and license checks still apply.';
  $('disk').textContent=`${s.disk_free_gb} GB available`;
  $('scheduleCaption').textContent=`${cfg.slots.length} daily slots · ${cfg.timezone}${cfg.daily_script_batch?' · Scripts prepared at '+cfg.morning_plan_time:''}`;
  const nextSlot=s.next_run?new Date(s.next_run).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',timeZone:cfg.timezone}):null;
  $('slots').innerHTML=cfg.slots.map(slot=>`<span class="slot ${slot===nextSlot?'next':''}">${esc(slot)}${slot===nextSlot?'<small>Next</small>':''}</span>`).join('');
  const config=[['Topic discovery',youtubeMode()?'YouTube inspiration + web research':'RSS / Trends + source checks'],['Script model',`${nice(s.script_settings.selection.provider)} · ${cfg.model}`],['Narration',`${language()} · ${cfg.voice}`],['Video style',MODE_NAMES[s.render_settings.selection.render_mode]],['Topic focus',cfg.topic_focus==='ai_coding'?'AI, LLMs & coding':'Technology insights'],['Visuals',[cfg.public_images?'Wikimedia Commons':'',cfg.pixabay?'Pixabay':'',cfg.pexels?'Pexels':'','Original graphics'].filter(Boolean).join(' · ')],['Google authorization',cfg.token_present?'Token saved · validity checked on use':'Setup required'],['Publishing',cfg.uploads_enabled?`${nice(cfg.visibility)} uploads`:'Disabled']];
  $('configuration').innerHTML=config.map(([key,value])=>`<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('');
  $('renderAvailability').textContent=s.motion_renderer.ready?'Local motion renderer ready. Hybrid keeps available B-roll.':'Motion renderer needs setup. Normal works without it; enabled fallback is recorded per run.';
  $('events').innerHTML=s.events.length?s.events.map(event=>`<div class="event"><time>${esc(date(event.created_at))}<br>${esc(time(event.created_at))}</time><div><p>${badge(event.state)} ${esc(event.message)}</p><small>${esc(event.job_id)}</small></div></div>`).join(''):'<p class="help">Run updates will appear here.</p>';
  $('morningScripts').hidden=!cfg.daily_script_batch;
  const plan=s.daily_plan;
  $('planSummary').textContent=plan?`${plan.entries.length}/${cfg.slots.length} prepared · ${nice(plan.state)} · ${plan.date}`:`Next preparation: ${cfg.morning_plan_time} · ${cfg.timezone}`;
  $('planEntries').innerHTML=plan?.entries.length?plan.entries.map(entry=>`<details class="event"><summary>${esc(entry.slot)} · ${esc(entry.title)}</summary><p>${esc(entry.script)}</p></details>`).join(''):'<p class="help">No prepared scripts for today. Scheduled slots use their saved morning plan.</p>';
  if(plan?.error) $('planEntries').innerHTML+=`<p class="error-text">${esc(plan.error)}</p>`;
  if(plan?.rejected?.length) $('planEntries').innerHTML+=`<details><summary>${plan.rejected.length} topics did not qualify</summary><ul>${plan.rejected.map(item=>`<li>${esc(item.topic)} — ${esc(item.reason)}</li>`).join('')}</ul></details>`;
  $('refreshed').textContent=`Updated ${time(s.now)}`;$('writingWarning').textContent=s.writing_warning;
  updateCustomInput();renderJobs();updateControls();
}
async function refresh() {
  if(refreshPromise) return refreshPromise;
  refreshPromise=(async()=>{
    try {
      const next=await api('/api/status');
      if(!token || (state && next.started_at!==state.started_at)) token=(await api('/api/session')).token;
      state=mergeSnapshot(state,next);serverClockOffset=Date.parse(next.now)-Date.now();connected=true;render();
    } catch {
      connected=false;$('connection').textContent='Disconnected';$('connection').dataset.state='offline';$('offline').hidden=false;
    } finally {refreshPromise=null;updateControls();}
  })();
  updateControls();return refreshPromise;
}

function openConfirmation(intent) {
  if(!canAct() || busy()) return toast('Wait for the current task to finish and check your connection.','error');
  if($('confirm').open) return;
  pending=structuredClone(intent);
  pendingContext={script:state.script_settings.version,voice:state.voice_settings.version,visibility:state.settings.visibility,uploads:state.settings.uploads_enabled,server:state.started_at};
  const copy=confirmation(pending,state.settings);
  $('confirmTitle').textContent=copy.title;$('confirmText').textContent=copy.text;$('confirmText').dataset.public=String(copy.isPublic);$('confirmAction').textContent=copy.button;
  const rows=intent.action==='retry'?[['Run',intent.job_id],['Settings','Original saved selection']]:[
    ['Input',intent.manual_input?intent.manual_input.mode==='script'?'Your narration':'Your topic':'Automatic topic'],
    ['Script',nice(state.script_settings.selection.provider)],['Voice',`${language()} · ${state.settings.voice}`],['Style',MODE_NAMES[intent.render_mode]],['Output',intent.action==='preview'?'Local preview only':state.settings.uploads_enabled?nice(state.settings.visibility)+' upload':'Local video only']];
  $('confirmSummary').innerHTML=rows.map(([key,value])=>`<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('');
  $('confirm').showModal();updateControls();
}
function automatic(preview) {
  if(!state) return;
  openConfirmation(runIntent({kind:'automatic',preview,savedMode:state.render_settings.selection.render_mode}));
}
function updateCustomInput() {
  const script=$('customMode').value==='script',content=$('customContent');
  $('customTopicRow').hidden=!script;$('customTopic').required=script;$('customScriptFormat').hidden=!script;
  content.maxLength=script?5000:180;content.rows=script?6:4;
  $('customLabel').textContent=script?'Your narration':'What should this video explain?';
  $('customHelp').textContent=script?`12–180 words. ${language()} is the saved narration language. Your words won't be rewritten.`:`Researches this topic and writes an original ${language()} script using retrieved sources.`;
  $('customCount').textContent=script?`${content.value.trim().split(/\s+/).filter(Boolean).length} words · 12–180 allowed`:`${content.value.length} / 180 characters`;
  $('customSourceLabel').textContent=youtubeMode()?'YouTube topic references':'Official documentation and independent coverage';
  $('customSourceHelp').textContent=youtubeMode()?script?'Source links are optional; supplied narration is not independently checked.':'Up to six YouTube video links, one per line. Web research establishes the script facts.':'Up to six HTTPS source links, one per line.';
}
function clearCustomError() {$('customError').hidden=true;$('customContent').removeAttribute('aria-invalid');}
function customRun(preview) {
  if(!canAct() || busy()) return;
  clearCustomError();if(!$('customForm').reportValidity()) return;
  const manualInput={mode:$('customMode').value,content:$('customContent').value.trim(),topic:$('customTopic').value.trim(),source_urls:$('customSources').value.split(/\n/).map(v=>v.trim()).filter(Boolean)};
  const error=validateManual(manualInput);
  if(error) {$('customError').textContent=error;$('customError').hidden=false;$('customContent').setAttribute('aria-invalid','true');$('customContent').focus();return;}
  openConfirmation(runIntent({kind:'manual',preview,manualInput,modeOverride:$('runStyle').value,savedMode:state.render_settings.selection.render_mode}));
}
async function performAction(intent) {
  if(!canAct() || requestPending) return;
  requestPending=true;updateControls();
  try {const result=await post(intent);toast(result.message);await refresh();}
  catch(error) {toast(error.message,'error');}
  finally {requestPending=false;updateControls();}
}
$('preview').onclick=()=>automatic(true);$('run').onclick=()=>automatic(false);
$('customForm').onsubmit=event=>{event.preventDefault();customRun(false);};$('customPreview').onclick=()=>customRun(true);
$('customMode').onchange=()=>{clearCustomError();updateCustomInput();};$('customContent').oninput=()=>{clearCustomError();updateCustomInput();};
$('customTopic').oninput=$('customSources').oninput=clearCustomError;
$('toggle').onclick=()=>{if(state) performAction({action:state.auto_enabled?'pause':'resume'});};
$('closeConfirm').onclick=$('cancelAction').onclick=()=>$('confirm').close();
$('confirm').addEventListener('close',()=>{pending=null;pendingContext=null;});
$('confirmAction').onclick=()=>{const task=pending;if(!task) return;$('confirm').close();pending=null;performAction(task);};
$('filter').onchange=$('searchJobs').oninput=renderJobs;
$('jobs').onclick=event=>{const button=event.target.closest('[data-job]');if(button) details(button.dataset.job);if(event.target.closest('#clearSearch')){$('searchJobs').value='';$('filter').value='all';renderJobs();$('searchJobs').focus();}};

async function details(id) {
  detailId=id;detailJob=state?.jobs.find(job=>job.id===id) || detailJob;
  const request=++detailRequest;
  $('detailBody').innerHTML='<p class="help" role="status">Loading saved run details…</p>';
  if(!$('details').open) $('details').showModal();
  $('details').setAttribute('aria-busy','true');updateControls();
  try {
    const data=await api(`/api/jobs/${id}`);
    if(request!==detailRequest || detailId!==id || !$('details').open) return;
    const evidence=evidenceFor(data),assets=data.media_assets || [],report=data.render_report;
    const unchecked=data.script?.source_basis==='user_input' || data.source_brief?.basis==='user_input';
    const sourceNote=unchecked?'Your supplied narration is not independently fact checked.':evidence.status==='official_source_only'?'Official company claims are attributed; independent corroboration was not established.':evidence.sources.length?'These retrieved sources support the script. Topic references are listed separately.':'No completed evidence record is available yet.';
    $('detailBody').innerHTML=`<p class="detail-title">${esc(data.script?.title || data.topic || 'Preparing a technology story')}</p><div class="detail-status">${badge(data)}<span class="detail-code">${esc(id)}${data.dry_run?' · Local preview only':''}</span></div>${data.error?`<p class="error-text">${esc(data.error)}</p>`:''}${detailJob?.has_video?`<video class="detail-video" controls preload="metadata" src="/media/${id}/final.mp4" aria-label="Saved video preview"></video>`:''}<div class="detail-links">${data.youtube?.url?link(data.youtube.url,'Open uploaded video on YouTube'):''}${data.state==='FAILED'?`<button class="button secondary" data-retry="${id}" ${busy() || !canAct()?'disabled':''}>Retry saved checkpoint</button>`:''}</div><div class="detail-section"><h3>Production settings</h3><p>Requested style: ${esc(MODE_NAMES[data.render_settings?.render_mode || 'normal'])}<br>Rendered style: ${report?.status==='completed'?esc(MODE_NAMES[report.effective_mode]):'Not completed yet'}${data.script_settings?`<br>Script model: ${esc(nice(data.script_settings.provider))} · ${esc(data.script_settings.model)}`:''}</p>${report?.fallback_reason?`<p class="notice warning">${esc(report.fallback_reason)}</p>`:''}</div><div class="detail-section"><h3>Narration</h3><p>${esc(data.script?.script || 'The script has not completed yet.')}</p></div><div class="detail-section"><h3>Evidence sources</h3><p class="detail-source-note">${esc(sourceNote)}</p><ul>${evidence.sources.map(source=>`<li>${link(source.url,source.title || source.publisher || 'Source')}${source.primary?' · Official source':''}</li>`).join('')}</ul></div>${evidence.inspiration.length?`<div class="detail-section"><h3>Topic inspiration</h3><p class="detail-source-note">Creator video metadata suggests the topic; it is separate from web evidence.</p><ul>${evidence.inspiration.map(source=>`<li>${link(source.url,source.title || 'Topic reference')}</li>`).join('')}</ul></div>`:''}${data.research_attempts?.length?`<details class="detail-section"><summary>Other topics checked (${data.research_attempts.length})</summary><ul>${data.research_attempts.map(item=>`<li>${esc(item.topic)}<br>${esc(item.reason)}</li>`).join('')}</ul></details>`:''}<details class="detail-section"><summary>Visual credits & licenses</summary><p>${data.media_license_audit?.passed?'Recorded license checks passed.':'License checks have not completed.'} Provider restrictions and third-party rights still apply.</p><ul>${assets.length?assets.map(asset=>`<li>${esc(asset.attribution || asset.provider || 'Original graphics')}<br>${esc(asset.license)} ${asset.source_url?link(asset.source_url,'Source'):''}</li>`).join(''):'<li>No visuals have been selected.</li>'}</ul></details><div class="detail-section"><h3>Media quality</h3><p>${data.validation?.passed?'Passed automated dimensions, codec, duration, audio and caption checks.':'No completed media validation.'}</p><p class="detail-source-note">Caption phrases use estimated timing within measured narration scenes.</p></div>`;
  } catch(error) {
    if(request===detailRequest && $('details').open) $('detailBody').innerHTML=`<p class="error-text">${esc(error.message)}</p><p class="help">Use Refresh to load these run details again.</p>`;
  } finally {if(request===detailRequest) $('details').removeAttribute('aria-busy');}
}
$('closeDetails').onclick=()=>$('details').close();
$('refreshDetails').onclick=()=>{if(detailId) details(detailId);};
$('details').addEventListener('close',()=>{$('details').querySelector('video')?.pause();detailId=null;detailRequest++;});
$('detailBody').onclick=event=>{const button=event.target.closest('[data-retry]');if(button){const id=button.dataset.retry;$('details').close();openConfirmation({action:'retry',job_id:id});}};

function ensureVoiceOption(value) {
  if(value && ![...$('elevenVoice').options].some(option=>option.value===value)) $('elevenVoice').add(new Option('Saved voice',value));
  $('elevenVoice').value=value || '';
}
function showVoiceFields() {
  const provider=$('voiceProvider').value,eleven=provider==='elevenlabs';
  $('elevenVoiceRow').hidden=!eleven;$('localVoiceRow').hidden=eleven;$('elevenNotice').hidden=!eleven;
  $('localVoice').required=!eleven;$('elevenVoice').required=eleven;
  const missingEleven=!state?.voice_keys?.elevenlabs;
  $('elevenVoice').disabled=!canAct() || missingEleven || voiceLoading || forms.voice.saving;
  $('loadVoices').textContent=voiceLoading?'Loading voices…':voiceCatalogRequested?'Refresh voices':'Load voices';
  $('elevenVoiceHelp').textContent=missingEleven?'ElevenLabs API key is missing. Add ELEVENLABS_API_KEY to D:\\AutoYt\\.env, then restart AutoYT.':voiceLoading?'Loading voices from your ElevenLabs account…':voiceCatalogLoaded?'Choose an available voice. Indian-accented male voices are listed first.':'Voices load from your ElevenLabs account. If loading fails, use Refresh voices to retry.';
  if(eleven && !missingEleven && canAct() && !voiceCatalogRequested) loadVoiceList();
  const hints={kokoro:'Local model. English male voice: am_michael. Hindi/Hinglish requires a Hindi voice such as hm_omega.',gemini:'API voice, for example Puck. Tests and narration use your Gemini quota.',piper:'Uses the configured local Piper voice model.',sapi:'Uses an installed Windows voice. Language support depends on the installed voices.',indicf5:'Uses the configured local model and reference voice.'};
  $('voiceProviderHelp').textContent=hints[provider] || 'Choose a voice provider.';
  const apiVoice=['gemini','elevenlabs'].includes(provider),missing=apiVoice && !state?.voice_keys?.[provider];
  $('voiceTestHelp').textContent=missing?`${nice(provider)} API key is missing. Add it to the local environment before testing.`:apiVoice?'Tests use a short sample and consume provider quota. Testing does not save settings.':'Generates a short local sample without voice API credits. Testing does not save settings.';
  $('localVoice').setCustomValidity(provider==='kokoro' && $('scriptLanguage').value!=='en' && !$('localVoice').value.startsWith('h')?'Hindi or Hinglish needs a Hindi Kokoro voice such as hm_omega.':'');
}
function clearVoiceSample() {
  if(voiceTestingId || !$('voiceAudio').hidden) {
    voiceTestingId=null;$('voiceAudio').pause();$('voiceAudio').removeAttribute('src');$('voiceAudio').hidden=true;
  }
}
function voiceChanged(providerChanged=false) {
  clearVoiceSample();
  if(providerChanged) {
    if(voiceProvider && voiceProvider!=='elevenlabs') draftVoices[voiceProvider]=$('localVoice').value;
    voiceProvider=$('voiceProvider').value;
    if(voiceProvider!=='elevenlabs') $('localVoice').value=draftVoices[voiceProvider] || state?.voice_settings.voices[voiceProvider] || (voiceProvider==='gemini'?'Puck':voiceProvider==='kokoro'?'am_michael':'default');
    else ensureVoiceOption(state?.voice_settings.voices.elevenlabs || '');
  }
  showVoiceFields();dirty('voice');
}
$('voiceProvider').onchange=()=>voiceChanged(true);
$('scriptLanguage').onchange=$('elevenVoice').onchange=()=>voiceChanged();
$('localVoice').oninput=()=>voiceChanged();
async function loadVoiceList(more=false) {
  if(!canAct() || voiceLoading || !state.voice_keys.elevenlabs) return;
  voiceCatalogRequested=true;voiceLoading=true;message('voice','Loading available voices…');updateControls();showVoiceFields();
  try {
    const result=await post({action:'voice_list',page:more?voicePage:null}),selected=$('elevenVoice').value;
    const existing=more?[...$('elevenVoice').options].filter(option=>option.value).map(option=>({id:option.value,name:option.text})):[];
    const voices=new Map([...existing,...result.voices].map(voice=>[voice.id,voice]));
    $('elevenVoice').innerHTML='<option value="">Choose a voice</option>'+[...voices.values()].map(voice=>`<option value="${esc(voice.id)}">${esc(voice.name)}${voice.labels?' · '+esc([voice.labels.accent,voice.labels.gender,voice.labels.language].filter(Boolean).join(' / ')):''}${voice.recommended?' (Indian male)':''}</option>`).join('');
    ensureVoiceOption(selected);voicePage=result.next_page;$('moreVoices').hidden=!voicePage;
    voiceCatalogLoaded=true;
    message('voice',voices.size?'Voices loaded. Choose one, then test it before saving.':'No voices found. Check your ElevenLabs voice library.');
  } catch(error) {message('voice',error.message,'error');}
  finally {voiceLoading=false;updateControls();showVoiceFields();}
}
$('loadVoices').onclick=()=>loadVoiceList();$('moreVoices').onclick=()=>loadVoiceList(true);
$('testVoice').onclick=async()=>{
  if(!canAct() || busy() || voiceSubmitting || voiceTestingId || !$('voiceForm').reportValidity()) return;
  clearVoiceSample();voiceSubmitting=true;message('voice','Generating a short sample. Local models may take a few minutes.');updateControls();
  try {
    const result=await post({action:'test_voice',selection:settingValue('voice')});voiceTestingId=result.preview_id;
    message('voice','Voice sample is generating. You can keep browsing this dashboard.');await refresh();
  } catch(error) {message('voice',error.message,'error');}
  finally {voiceSubmitting=false;updateControls();}
};
function renderVoiceResult() {
  const result=state.voice_preview;
  if(!voiceTestingId || result?.id!==voiceTestingId) return;
  if(result.state==='READY') {
    $('voiceAudio').src=`/api/voice-preview/${result.id}.wav`;$('voiceAudio').hidden=false;voiceTestingId=null;
    message('voice','Sample ready. Listen before saving. The sample expires after 9 minutes.','success');
  } else if(result.state==='FAILED') {voiceTestingId=null;message('voice',result.error,'error');}
}
$('voiceAudio').onerror=()=>{if(!$('voiceAudio').hidden) message('voice','This sample could not be played or has expired. Generate a new voice test.','error');};

function updateNavigation() {
  const target=location.hash || '#overview';
  document.querySelectorAll('.sidebar nav a').forEach(anchor=>{
    if(anchor.getAttribute('href')===target) anchor.setAttribute('aria-current','location');else anchor.removeAttribute('aria-current');
  });
}
window.addEventListener('hashchange',updateNavigation);updateNavigation();
function updateCountdown() {
  if(state) $('nextCountdown').textContent=nextRunCountdown(state.next_run,Date.now()+serverClockOffset,state.auto_enabled);
}
async function boot() {
  $('connection').dataset.state='loading';updateControls();
  await refresh();
  setInterval(()=>{if(!document.hidden) refresh();},5000);
  setInterval(updateCountdown,1000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden) refresh();});
}
boot();
