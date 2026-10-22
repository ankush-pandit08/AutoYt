/* Pure presentation rules shared by the dashboard and native Node tests. */
export const TERMINAL = new Set(['PUBLISHED','AWAITING_REVIEW','FAILED','SKIPPED','READY_TO_UPLOAD']);
export const STAGES = ['Find topic','Research','Write script','Voiceover','Find visuals','Render','Quality check','Upload'];
export const MODE_NAMES = {normal:'Normal',motion_graphics:'Motion Graphics',hybrid:'Hybrid'};
export const nice = value => value==='openrouter'?'OpenRouter':String(value ?? '').replaceAll('_',' ').toLowerCase().replace(/^./, c=>c.toUpperCase());
export const sameSelection = (a,b) => !!a && !!b && Object.keys(a).length===Object.keys(b).length && Object.keys(a).every(key=>a[key]===b[key]);

export function jobStatus(job) {
  if(job.state==='READY_TO_UPLOAD') return {label:job.dry_run ? 'Preview ready' : 'Ready for upload',tone:'neutral'};
  if(job.state==='AWAITING_REVIEW') return {label:'Uploaded privately',tone:'success'};
  if(job.state==='PUBLISHED') return {label:'Published',tone:'success'};
  if(job.state==='FAILED') return {label:'Failed',tone:'error'};
  if(job.state==='SKIPPED') return {label:'Skipped',tone:'warning'};
  return {label:nice(job.state),tone:'working'};
}

export function filterJobs(jobs, filter='all', query='') {
  const search=query.trim().toLowerCase();
  return jobs.filter(job => {
    const matches = filter==='all' || (filter==='active' ? !TERMINAL.has(job.state) :
      filter==='previews' ? !!job.dry_run : job.state===filter);
    return matches && (!search || [job.title,job.topic,job.id].some(value=>String(value || '').toLowerCase().includes(search)));
  });
}

export function runIntent({kind,preview,manualInput,modeOverride,savedMode}) {
  if(!['automatic','manual'].includes(kind)) throw new Error('Choose an automatic or manual run');
  const mode=kind==='manual' && modeOverride && modeOverride!=='default' ? modeOverride : savedMode;
  if(!Object.hasOwn(MODE_NAMES,mode)) throw new Error('Choose an available video style');
  if(kind==='manual' && !manualInput) throw new Error('Provide a topic or script');
  return {action:preview ? 'preview':'run', render_mode:mode,
    ...(kind==='manual' ? {manual_input:structuredClone(manualInput)} : {})};
}

export function validateManual(input) {
  const count=input.content.trim().split(/\s+/).filter(Boolean).length;
  if(input.mode==='script' && (count<12 || count>180)) return 'Use 12–180 words for your narration.';
  if(input.mode==='topic' && (input.content.trim().length<3 || input.content.length>180)) return 'Use 3–180 characters for your topic.';
  if(input.mode==='script' && (!input.topic.trim() || input.topic.length>180)) return 'Add a short video topic, up to 180 characters.';
  if(input.source_urls.length>6) return 'Add up to six source links.';
  for(const url of input.source_urls) {
    try {const parsed=new URL(url);if(parsed.protocol!=='https:' || parsed.username || parsed.password) return 'Use public HTTPS source links without credentials.';}
    catch {return 'One or more source links are invalid. Add one HTTPS link per line.';}
  }
  return '';
}

export function confirmation(intent, settings) {
  const preview=intent.action==='preview', retry=intent.action==='retry';
  const isPublic=!preview && settings.uploads_enabled && settings.visibility==='public';
  const manual=!!intent.manual_input;
  return {isPublic,
    title:preview ? 'Create a preview?' : retry ? 'Retry this run?' : isPublic ? 'Create and publish publicly?' : 'Create a video?',
    button:preview ? 'Create preview' : retry ? 'Retry run' : isPublic ? 'Create & publish' : settings.uploads_enabled ? 'Create & upload privately' : 'Create video',
    text:preview ? 'Creates a local preview. Nothing will be uploaded.' : retry ?
      'Resumes the saved checkpoint and original settings. A live run may upload after validation; a preview stays local.' :
      `${manual ? 'Uses your submitted input.' : 'AutoYT chooses a technology topic using your saved settings.'} ${settings.uploads_enabled ?
      `After validation, this video uploads ${isPublic ? 'PUBLICLY' : 'privately'} to your authorized YouTube channel.` : 'Uploads are disabled. The finished video stays local.'}`};
}

export function evidenceFor(detail) {
  return {sources:detail.research?.sources || detail.source_brief?.evidence?.sources || [],
    inspiration:detail.source_brief?.videos || [],
    status:detail.source_brief?.fact_check_status || (detail.research ? 'source_supported':'not_checked')};
}

export function mergeSnapshot(previous,next) {
  if(!previous || previous.started_at!==next.started_at) return next;
  const merged={...next};
  for(const key of ['script_settings','render_settings','voice_settings','schedule_settings']) {
    if(previous[key]?.version>next[key]?.version) merged[key]=previous[key];
  }
  return merged;
}

export function nextRunCountdown(next,now,enabled=true) {
  if(!enabled) return 'Automatic mode paused';
  if(!next) return 'No upcoming automatic run';
  const remaining=Math.max(0,Math.ceil((Date.parse(next)-now)/1000));
  if(!Number.isFinite(remaining)) return 'Updating schedule…';
  if(!remaining) return 'Run due · checking availability';
  const hours=Math.floor(remaining/3600),minutes=Math.floor(remaining%3600/60),seconds=remaining%60;
  return `Starts in ${String(hours).padStart(2,'0')}:${String(minutes).padStart(2,'0')}:${String(seconds).padStart(2,'0')}`;
}
