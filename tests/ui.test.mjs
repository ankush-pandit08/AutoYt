import test from 'node:test';
import assert from 'node:assert/strict';
import {confirmation,evidenceFor,filterJobs,jobStatus,mergeSnapshot,runIntent,sameSelection,validateManual,nextRunCountdown} from '../src/autoyt/web/ui.mjs';

test('preview-ready status never implies a pending upload',()=>{
  assert.equal(jobStatus({state:'READY_TO_UPLOAD',dry_run:1}).label,'Preview ready');
  assert.equal(jobStatus({state:'READY_TO_UPLOAD',dry_run:0}).label,'Ready for upload');
});
test('automatic actions never inherit a typed manual script or style override',()=>{
  const intent=runIntent({kind:'automatic',preview:true,savedMode:'hybrid',modeOverride:'normal',manualInput:{content:'Unsubmitted draft'}});
  assert.deepEqual(intent,{action:'preview',render_mode:'hybrid'});
});
test('manual style overrides are explicit and the confirmation owns a copy of the input',()=>{
  const input={mode:'topic',content:'Coding tools',source_urls:[]};
  const intent=runIntent({kind:'manual',preview:false,savedMode:'hybrid',modeOverride:'normal',manualInput:input});
  input.content='Changed after opening confirmation';
  assert.equal(intent.render_mode,'normal');assert.equal(intent.manual_input.content,'Coding tools');
  assert.throws(()=>runIntent({kind:'manual',savedMode:'unknown',manualInput:input}));
});
test('public output and local preview confirmations are unambiguous',()=>{
  const cfg={uploads_enabled:true,visibility:'public'};
  assert.match(confirmation({action:'run'},cfg).text,/PUBLICLY/);
  assert.equal(confirmation({action:'preview'},cfg).isPublic,false);
  assert.match(confirmation({action:'preview'},cfg).text,/Nothing will be uploaded/);
  assert.match(confirmation({action:'retry'},cfg).text,/preview stays local/);
});
test('saved settings equality ignores JSON field ordering but preserves types',()=>{
  assert.ok(sameSelection({provider:'kokoro',voice:'am_michael',language:'en'},{language:'en',provider:'kokoro',voice:'am_michael'}));
  assert.equal(sameSelection({render_fallback:true},{render_fallback:'true'}),false);
});
test('an older poll cannot overwrite a newly saved form version',()=>{
  const old={started_at:'server-a',render_settings:{version:4},script_settings:{version:2}};
  const next=mergeSnapshot(old,{started_at:'server-a',render_settings:{version:3},script_settings:{version:3}});
  assert.equal(next.render_settings.version,4);assert.equal(next.script_settings.version,3);
  assert.equal(mergeSnapshot(old,{started_at:'server-b',render_settings:{version:0}}).render_settings.version,0);
});
test('creator topic references are not presented as evidence sources',()=>{
  const detail={source_brief:{videos:[{url:'https://youtube.com/watch?v=reference'}],evidence:{sources:[{url:'https://official.example/docs'}]}}};
  const result=evidenceFor(detail);
  assert.equal(result.sources[0].url,'https://official.example/docs');assert.equal(result.inspiration.length,1);
  assert.deepEqual(evidenceFor({source_brief:{videos:detail.source_brief.videos}}).sources,[]);
});
test('library filters support previews, active runs and case-insensitive searching',()=>{
  const jobs=[{id:'a',title:'AI coding',state:'READY_TO_UPLOAD',dry_run:1},{id:'b',title:'Phone update',state:'RENDERING',dry_run:0}];
  assert.deepEqual(filterJobs(jobs,'previews','AI'),[jobs[0]]);
  assert.deepEqual(filterJobs(jobs,'active'),[jobs[1]]);
  assert.equal(filterJobs(jobs,'all','missing').length,0);
});
test('manual validation rejects invalid lengths, too many links and embedded credentials',()=>{
  const input={mode:'topic',content:'AI tools',topic:'',source_urls:[]};
  assert.equal(validateManual(input),'');
  assert.match(validateManual({...input,content:'AI'}),/3–180/);
  assert.match(validateManual({...input,source_urls:['https://user:secret@example.com']}),/without credentials/);
  assert.match(validateManual({...input,source_urls:['http://example.com']}),/HTTPS/);
  assert.match(validateManual({...input,source_urls:Array(7).fill('https://example.com')}),/six/);
  assert.match(validateManual({...input,mode:'script',content:'too short'}),/12–180/);
});

test('countdown uses absolute instants and handles paused, due and next-day runs',()=>{
  const now=Date.parse('2026-10-22T04:00:00Z');
  assert.equal(nextRunCountdown('2026-10-22T09:31:30+05:30',now),'Starts in 00:01:30');
  assert.equal(nextRunCountdown('2026-10-23T09:30:00+05:30',now),'Starts in 24:00:00');
  assert.equal(nextRunCountdown('2026-10-22T09:30:00+05:30',now),'Run due · checking availability');
  assert.equal(nextRunCountdown(null,now,false),'Automatic mode paused');
});


test('schedule selections compare saved times by value when drafts are reverted',()=>{
  const saved={times:['09:30','11:18','13:06','14:54','16:42','18:30'],timezone:'Asia/Kolkata'};
  assert.equal(sameSelection(saved,structuredClone(saved)),true);
  const draft=structuredClone(saved);draft.times[0]='10:00';
  assert.equal(sameSelection(saved,draft),false);
  draft.times[0]='09:30';assert.equal(sameSelection(saved,draft),true);
  assert.equal(sameSelection(saved,{...saved,times:{...saved.times}}),false);
});
