import {datasets,recon} from '../api/entities.js';
import {workspace} from '../stores/workspace.js';
import {session} from '../stores/session.js';
import {heading} from '../components/common/shell.js';
import {esc,date,notify} from '../utils/dom.js';
import {submit} from '../hooks/forms.js';

const STATUS={waiting:'缺源待收',matched:'两源一致',conflict:'异值待裁定',decided:'已裁定',open:'核对中',sealed:'已封单'};
const DECISION={primary:'采用主值',backup:'采用备值',exclude:'排除该点',same:'两源一致自动采用'};
const KIND={repeat_mismatch:'重复读数与首值不同',post_decision:'裁定后晚到读数',post_seal:'封单后晚到读数',unexpected_key:'来源键不在核对单'};
const canWrite=()=>['admin','analyst'].includes(session.user.role);

function splitRows(text){
 const rows=[];let row=[],cell='',quote=false;
 for(let i=0;i<text.length;i++){
   const c=text[i];
   if(c==='"'){if(quote&&text[i+1]==='"'){cell+='"';i++;}else quote=!quote;}
   else if((c===','||c==='\t')&&!quote){row.push(cell);cell='';}
   else if((c==='\n'||c==='\r')&&!quote){
     if(c==='\r'&&text[i+1]==='\n')i++;
     row.push(cell);if(row.some(v=>String(v).trim()))rows.push(row.map(v=>String(v).trim()));
     row=[];cell='';
   }
   else cell+=c;
 }
 row.push(cell);if(row.some(v=>String(v).trim()))rows.push(row.map(v=>String(v).trim()));
 return rows;
}

function tableFor(text,columns){
 const raw=String(text||'').trim();
 if(!raw)return [];
 if(raw.startsWith('[')){
   const rows=JSON.parse(raw);if(!Array.isArray(rows))throw new Error('JSON 须为数组');
   return rows;
 }
 const rows=splitRows(raw);
 if(!rows.length)return [];
 const header=rows[0].map(v=>v.replace(/^﻿/,''));
 const known=new Set(columns.flatMap(c=>[c.key,c.label,...(c.aliases||[])]));
 if(header.every(v=>!known.has(v)))return rows.map(r=>Object.fromEntries(columns.slice(0,r.length).map((c,i)=>[c.key,r[i]??''])));
 const keys=header.map(v=>{
   const column=columns.find(c=>c.key===v||c.label===v||(c.aliases||[]).includes(v));
   return column?column.key:v;
 });
 return rows.slice(1).map(r=>Object.fromEntries(keys.map((k,i)=>[k,r[i]??''])));
}

function readingsFor(text){
 const rows=tableFor(text,READING_COLUMNS);
 if(!rows.length)throw new Error('请粘贴至少一条读数');
 return rows.map(r=>({key:String(r.key??r['来源键']??'').trim(),value:Number(r.value??r['读数'])}));
}

function pointsFor(text){
 const rows=tableFor(text,POINT_COLUMNS);
 if(!rows.length)throw new Error('请填写预期点位');
 return rows.map(r=>({sample:String(r.sample??r['采样点']??''),route:String(r.route??r['路线']??''),minute:Number(r.minute??r['发车后分钟']),primary_key:String(r.primary_key??r['主记录器键']??''),backup_key:String(r.backup_key??r['备记录器键']??'')}));
}
const READING_COLUMNS=[{key:'key',label:'来源键',aliases:['sample','采样点']},{key:'value',label:'读数',aliases:['temperature','温度']}];
const POINT_COLUMNS=[{key:'sample',label:'采样点'},{key:'route',label:'路线'},{key:'minute',label:'发车后分钟'},{key:'primary_key',label:'主记录器键',aliases:['主源键']},{key:'backup_key',label:'备记录器键',aliases:['备源键']}];

function sheetButton(s){
 const sMeta=STATUS[s.status]||s.status,pending=s.summary.pending;
 return '<button class="recon-item '+(workspace.reconSelected===s.id?'active':'')+'" data-sheet="'+s.id+'"><span class="recon-item-top"><strong>'+esc(s.name)+'</strong><em class="tag '+(s.status==='sealed'?'sealed':'open')+'">'+esc(sMeta)+'</em></span><span>'+s.point_count+' 点 · 一致 '+s.summary.matched+' · 裁定 '+s.summary.decided+' · 待处理 '+pending+'</span><small>'+date(s.created_at)+(s.batch?' · 批次 '+esc(s.batch.name):'')+'</small></button>';
}

function pointRow(p,sealed){
 const writable=canWrite()&&(!sealed||session.user.role==='admin');
 const canDecide=writable&&(p.status==='waiting'||p.status==='conflict'||p.status==='matched'||p.status==='decided');
 const buttons=canDecide?[
   ['primary','采主',p.primary_value===null],['backup','采备',p.backup_value===null],['exclude','排除',false]
 ].map(([v,label,disabled])=>'<button class="button small'+(p.decision===v?' chosen':'')+'" data-decide="'+v+'" data-point="'+p.id+'" '+(disabled?'disabled':'')+'>'+label+'</button>').join(''):'<span class="muted">'+esc(sealed?'已封单':'只读')+'</span>';
 return '<tr class="'+(p.status==='conflict'?'row-conflict':p.status==='waiting'?'row-waiting':p.status==='decided'?'row-decided':'')+'"><td>'+p.seq+'</td><td><strong>'+esc(p.sample)+'</strong><br><small>'+esc(p.route)+' · '+esc(p.minute)+' 分</small></td><td class="mono">'+esc(p.primary_value??'—')+'</td><td class="mono">'+esc(p.backup_value??'—')+'</td><td>'+esc(STATUS[p.status]||p.status)+'</td><td class="mono adopted-cell">'+esc(p.adopted_value??'—')+'</td><td>'+(p.decision?esc(DECISION[p.decision]||p.decision):'—')+'</td><td class="point-actions">'+buttons+'<input class="note-input" data-note="'+p.id+'" placeholder="裁定依据（排除必填）" value="'+esc(p.note)+'"><button class="text-button" data-trace="'+p.id+'">依据 ↗</button></td></tr>';
}

function tracePanel(trace){
 if(!trace)return '';
 const p=trace.point,pl=trace.sheet.primary_label||'主源',bl=trace.sheet.backup_label||'备源';
 const label=s=>s==='primary'?pl:bl;
 return '<section class="panel trace-panel"><div class="section-title"><h2>采用依据 · '+esc(p.sample)+'</h2><span>核对单 '+esc(trace.sheet.name)+' · R'+trace.sheet.revision+' · '+(STATUS[trace.sheet.status]||trace.sheet.status)+'</span></div><div class="trace-grid"><div><p class="eyebrow">当前原值与采用</p><div class="trace-values"><div><span>'+esc(pl)+'</span><strong>'+esc(p.primary_value??'缺读')+'</strong></div><div><span>'+esc(bl)+'</span><strong>'+esc(p.backup_value??'缺读')+'</strong></div><div class="adopted"><span>采用值</span><strong>'+esc(p.adopted_value??'未采用')+'</strong></div></div><p class="hint">'+esc(p.decision?('裁定：'+DECISION[p.decision]+(p.note?' · '+p.note:'')):'该点尚无裁定。')+'</p></div><div><p class="eyebrow">裁定历史</p>'+(trace.adoptions.length?'<ul class="timeline">'+trace.adoptions.map(a=>'<li><strong>'+esc(DECISION[a.decision]||a.decision)+' · 采用 '+esc(a.adopted_value??'—')+'</strong><span>R'+a.revision+' · 原值 '+esc(a.primary_value??'缺')+' / '+esc(a.backup_value??'缺')+' · '+esc(a.decided_by?.name||'系统')+' · '+date(a.decided_at)+'</span></li>').join('')+'</ul>':'<p class="empty">尚无裁定记录。</p>')+'</div><div><p class="eyebrow">差异记录</p>'+(trace.differences.length?'<ul class="timeline">'+trace.differences.map(d=>'<li><strong>'+esc(KIND[d.kind]||d.kind)+' · '+esc(d.value)+'</strong><span>'+esc(label(d.source))+' · 当时采用 '+esc(d.adopted_value??'—')+' · '+date(d.at)+'</span></li>').join('')+'</ul>':'<p class="empty">无差异记录。</p>')+'</div></div></section>';
}

export async function reconPage(id){
 const [sheets,allDatasets]=await Promise.all([recon.list(),datasets.list()]);
 workspace.reconSheets=sheets;workspace.datasets=allDatasets;
 const selected=(id?sheets.find(s=>String(s.id)===String(id)):sheets.find(s=>s.id===workspace.reconSelected))||sheets[0];
 workspace.reconSelected=selected?.id||null;
 const detail=selected?await recon.get(selected.id):null;
 const trace=workspace.reconTrace&&detail&&workspace.reconTrace.point?workspace.reconTrace:null;
 const m=workspace.meta;
 const writer=canWrite();
 const openForm='<section class="panel recon-open"><div class="section-title"><h2>开单选点</h2><span>开单即固定两源预期点位</span></div><form id="recon-open-form"><label>核对单名称<input name="name" required minlength="2" placeholder="例如：11 月主备记录器核对"></label><div class="parameter-grid"><label>主源名称<input name="primary_label" value="主记录器" maxlength="40" required></label><label>备源名称<input name="backup_label" value="备记录器" maxlength="40" required></label></div><label>从已有批次预填对应关系<select id="recon-prefill"><option value="">不预填，手动粘贴</option>'+allDatasets.map(d=>'<option value="'+d.id+'">'+esc(d.name)+' · '+d.rows+' 点</option>').join('')+'</select></label><label>预期点位（CSV/JSON，两源来源键逐一对齐）<textarea name="points" rows="7" required placeholder="sample,route,minute,primary_key,backup_key\n'+esc((m.columns||[]).map(c=>c.key).join(','))+'"></textarea></label><label>开单说明<input name="note" maxlength="500" placeholder="记录器导出时间、交接人"></label><button type="submit" class="primary" '+(writer?'':'disabled')+'>固定点位并开单</button><p class="hint">点位与两源键开单后不可增删或改写；相同来源键读数只采用一次。</p></form></section>';
 const header=detail?'<div class="recon-head"><div><p class="eyebrow">SHEET '+detail.id+' · R'+detail.revision+'</p><h2>'+esc(detail.name)+'</h2><p class="description">'+esc(detail.primary_label)+' ↔ '+esc(detail.backup_label)+' · '+detail.point_count+' 个预期点位 · 定义指纹 '+esc(detail.definition_checksum.slice(0,16))+'…</p></div><div class="recon-metrics">'+[['两源一致',detail.summary.matched],['异值待裁定',detail.summary.conflict],['缺源待收',detail.summary.waiting],['已裁定',detail.summary.decided]].map(([k,v])=>'<div><span>'+k+'</span><strong>'+v+'</strong></div>').join('')+'</div></div>'+'<div class="recon-statusline"><span class="tag '+(detail.status==='sealed'?'sealed':'open')+'">'+(STATUS[detail.status]||detail.status)+'</span>'+(detail.batch?'<span>可调查批次：<a href="#/workbench/'+detail.batch.dataset_id+'">'+esc(detail.batch.name)+'</a> · '+detail.batch.rows+' 点 · SHA256 '+esc(detail.batch.checksum.slice(0,16))+'…</span>':detail.status==='sealed'?'<span class="warning">批次尚未写入，可重新封单恢复原单。</span>':'<span>全部点位有结论后才能封单生成可调查批次。</span>')+'<input id="seal-note" class="inline-input" maxlength="500" placeholder="封单说明（可选）" '+(detail.status==='sealed'?'disabled':'')+'><button class="primary" id="seal-sheet" '+((writer&&(detail.can_seal||detail.status==='sealed'))?'':'disabled')+'>'+(detail.status==='sealed'?(detail.batch?'已封单':'恢复原单并补写批次'):'封单并生成可调查批次')+'</button></div>':'<div class="empty">尚无核对单。先在左侧开单，固定两源预期点位。</div>';
 const intake=detail?'<div class="source-grid"><section class="panel source-block"><div class="section-title"><h2>接收 '+esc(detail.primary_label)+' 读数</h2><span>逐源接收</span></div><form id="intake-primary" data-source="primary"><label>读数（来源键,数值）<textarea name="readings" rows="4" placeholder="'+esc(detail.points[0]?.primary_key||'P-001')+',5.4"></textarea></label><label>接收说明<input name="note" maxlength="300" placeholder="导出批次 / 交接人"></label><button type="submit" class="primary" '+(writer?'':'disabled')+'>接收主源读数</button></form></section><section class="panel source-block"><div class="section-title"><h2>接收 '+esc(detail.backup_label)+' 读数</h2><span>逐源接收</span></div><form id="intake-backup" data-source="backup"><label>读数（来源键,数值）<textarea name="readings" rows="4" placeholder="'+esc(detail.points[0]?.backup_key||'B-001')+',5.4"></textarea></label><label>接收说明<input name="note" maxlength="300" placeholder="导出批次 / 交接人"></label><button type="submit" class="primary" '+(writer?'':'disabled')+'>接收备源读数</button></form></section></div><p class="hint">'+(detail.status==='sealed'?'本单已封单：此处接收的读数只写入差异记录，不改已采用值。':'缺少任一侧读数或两源数值不同即进入待裁定；重复且相同的读数不重复计数；封单后到达的读数只记差异。')+'</p>':'';
 const points=detail?'<section class="panel"><div class="section-title"><h2>逐点核对与裁定</h2><span>'+detail.points.length+' 点 · 相同读数只采用一次</span></div>'+(detail.pending_samples.length?'<p class="warning">待处理点位 '+detail.summary.pending+' 个，例如：'+esc(detail.pending_samples.join('、'))+'</p>':'')+'<div class="table-wrap"><table><thead><tr><th>#</th><th>采样点</th><th>'+esc(detail.primary_label)+'</th><th>'+esc(detail.backup_label)+'</th><th>状态</th><th>采用值</th><th>裁定</th><th>操作</th></tr></thead><tbody>'+detail.points.map(p=>pointRow(p,detail.status==='sealed')).join('')+'</tbody></table></div></section>'+tracePanel(trace):'';
 const runs=detail&&detail.runs.length?'<section class="panel"><div class="section-title"><h2>执行记录</h2><span>旧执行不覆盖新裁定</span></div><div class="schedule-list">'+detail.runs.map(r=>'<article><div><strong>R'+r.revision+' · '+(r.status==='completed'?'已写入':r.status==='superseded'?'已被新裁定取代':'执行中')+'</strong><p>'+esc(r.detail||'—')+'</p><small>'+date(r.created_at)+(r.dataset_id?' · 批次 #'+r.dataset_id:'')+'</small></div></article>').join('')+'</div></section>':'';
 const differences=detail&&detail.differences.length?'<section class="panel"><div class="section-title"><h2>差异记录</h2><span>不覆盖已采用值</span></div><div class="table-wrap"><table><thead><tr><th>时间</th><th>采样点</th><th>来源</th><th>读数</th><th>类型</th><th>当时采用值</th><th>说明</th></tr></thead><tbody>'+detail.differences.map(d=>'<tr><td>'+date(d.at)+'</td><td>'+esc(d.sample)+'</td><td>'+esc(d.source==='primary'?'主源':'备源')+'</td><td class="mono">'+esc(d.value)+'</td><td>'+esc(KIND[d.kind]||d.kind)+'</td><td class="mono">'+esc(d.adopted_value??'—')+'</td><td>'+esc(d.note||'—')+'</td></tr>').join('')+'</tbody></table></div></section>':'';
 return heading('03 / DUAL-SOURCE','双源核对单','主备记录器读数不一致时，先按固定点位逐源核对；相同读数只算一次，缺失或异值裁定后才生成可调查批次。')+'<div class="recon-layout"><section class="panel recon-list"><div class="section-title"><h2>核对单</h2><span>'+sheets.length+' 单</span></div><div class="recon-items">'+sheets.map(sheetButton).join('')+(!sheets.length?'<p class="empty">尚无核对单。</p>':'')+'</div></section>'+openForm+'</div>'+(detail?header+intake+points+runs+differences:'');
}

export function bindRecon(refresh){
 const prefill=document.querySelector('#recon-prefill');
 if(prefill)prefill.onchange=async()=>{
   if(!prefill.value)return;
   try{const d=await datasets.get(prefill.value);const lines=['sample,route,minute,primary_key,backup_key',...d.records.map(r=>[r.sample,r.route,r.minute,r.sample,r.sample].join(','))];document.querySelector('[name=points]').value=lines.join('\n');notify('已按批次点位预填两源对应关系，请核对后开单');}catch(e){notify(e.message,true);}
 };
 const openForm=document.querySelector('#recon-open-form');
 if(openForm)submit(openForm,async f=>{
   const points=pointsFor(f.get('points'));
   const sheet=await recon.create({name:f.get('name'),primary_label:f.get('primary_label'),backup_label:f.get('backup_label'),note:f.get('note'),points});
   workspace.reconSelected=sheet.id;notify('核对单已开立，点位与两源对应关系已固定');location.hash='#/recon/'+sheet.id;
 });
 for(const source of ['primary','backup']){
   const form=document.querySelector('#intake-'+source);
   if(form)submit(form,async f=>{
     const readings=readingsFor(f.get('readings'));
     const result=await recon.readings(workspace.reconSelected,source,readings,f.get('note'));
     notify('已接收 '+result.received+' 条；重复 '+result.duplicates+' 条；异值 '+result.conflicts+' 条；封单后 '+result.post_seal+' 条');
     await refresh();
   });
 }
 document.querySelectorAll('[data-sheet]').forEach(b=>b.onclick=()=>{workspace.reconSelected=Number(b.dataset.sheet);workspace.reconTrace=null;const target='#/recon/'+b.dataset.sheet;if(location.hash===target)refresh();else location.hash=target;});
 const seal=document.querySelector('#seal-sheet');
 if(seal)seal.onclick=async()=>{try{const note=document.querySelector('#seal-note')?.value||'';const result=await recon.seal(workspace.reconSelected,note);notify(result.batch?('已封单，生成可调查批次 '+result.batch.name):'已封单');await refresh();}catch(e){notify(e.message,true);}};
 document.querySelectorAll('[data-decide]').forEach(b=>b.onclick=async()=>{
   const note=document.querySelector('[data-note="'+b.dataset.point+'"]')?.value||'';
   try{await recon.decide(workspace.reconSelected,Number(b.dataset.point),b.dataset.decide,note);notify('裁定已记录，保留两源原值');await refresh();}catch(e){notify(e.message,true);}
 });
 document.querySelectorAll('[data-trace]').forEach(b=>b.onclick=async()=>{
   try{workspace.reconTrace=await recon.point(workspace.reconSelected,Number(b.dataset.trace));await refresh();document.querySelector('.trace-panel')?.scrollIntoView({behavior:'smooth',block:'center'});}catch(e){notify(e.message,true);}
 });
}
