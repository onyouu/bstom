const $ = id => document.getElementById(id);
const states = {LISTENING:'듣는 중',THINKING:'생각 중',WANTS_TO_SPEAK:'말이 떠올랐어요',SPEAKING:'말하는 중',INTERRUPTED:'잠깐 멈춤',IDLE:'쉬는 중'};
const labels = {running:'대화 중',paused:'잠깐 쉬는 중',ended:'마친 대화',archived:'저장된 대화'};
let current = null, socket = null, reconnect = null, generation = 0, lastSeq = 0;
function node(tag, cls, text) { const n=document.createElement(tag); if(cls)n.className=cls; if(text!==undefined)n.textContent=text; return n; }
async function api(path, body) {
  const response = await fetch(path, body===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok){const result=await response.json();throw new Error(result.detail || '요청에 실패했어요');}return response.json();
}
function notice(text='') {$('notice').textContent=text;}
function controls() {
  const active=current && ['running','paused'].includes(current.status);
  $('startButton').disabled=!!active;
  $('pauseButton').disabled=!active; $('endButton').disabled=!active;
  $('sendButton').disabled=!active; $('userText').disabled=!active;
  $('pauseButton').textContent=current?.status==='paused'?'다시 이야기하기':'잠깐 쉬기';
  $('sessionStatus').textContent=labels[current?.status]||'';
  $('silence').hidden=!current?.silent || current?.status!=='running';
  $('callCount').textContent=`${current?.calls||0}/${current?.call_limit||0} 호출`;
}
function renderAgents() {
  $('agents').replaceChildren(...current.agents.map(a=>{
    const card=node('div','agent'+(a.state==='SPEAKING'?' speaking':''));
    const top=node('div','agenttop');top.append(node('div','avatar',a.name[0]),node('strong','',a.name),node('span','agentstate',states[a.state]||a.state));
    card.append(top,node('p','interest',a.interest||'아직 어떤 얘기가 나올지 듣고 있어요'));return card;
  }));
}
function renderMessages() {
  const list=$('messages'), atBottom=list.scrollTop+list.clientHeight>=list.scrollHeight-90;
  list.replaceChildren(...current.messages.map(m=>{
    const row=node('div','message'+(m.speaker==='user'?' user':''));row.dataset.messageId=m.id;
    const author=node('div','author',m.name);if(m.interrupted)author.append(node('span','tag','말하다 멈춤'));
    row.append(author,node('div','text',m.text || '…'));return row;
  }));if(atBottom)list.scrollTop=list.scrollHeight;
}
function addEvent(event) {
  const log=$('eventLog'), row=node('div','event');
  row.append(node('strong','',event.type),node('span','',event.agent_id?' · '+event.agent_id:''));
  row.append(node('small','',new Date(event.timestamp*1000).toLocaleTimeString()));
  // Speech is already visible in the transcript; keep diagnostics compact.
  if(event.type!=='speech_chunk')row.append(node('div','',JSON.stringify(event.data)));
  log.append(row);while(log.children.length>150)log.firstChild.remove();log.scrollTop=log.scrollHeight;
}
function eventReceived(event) {
  if(event.type==='snapshot') {current=event.data;lastSeq=current.seq;renderMessages();renderAgents();$('eventLog').replaceChildren();current.events.forEach(addEvent);controls();return;}
  if(event.seq<=lastSeq)return;lastSeq=event.seq;addEvent(event);
  current.calls=event.calls??current.calls;
  const data=event.data, agent=current.agents.find(a=>a.id===event.agent_id);
  if(agent && data.state){agent.state=data.state;if(data.decision){agent.interest=data.decision.interest;agent.desire=data.decision.desire;}if(data.interest)agent.interest=data.interest;renderAgents();}
  if(event.type==='agent_speaking'||event.type==='user_spoke')current.messages.push(data.message);
  if(event.type==='speech_chunk'){const m=current.messages.find(m=>m.id===data.message_id);if(m)m.text+=data.text;}
  if(event.type==='agent_finished'||event.type==='agent_interrupted'){const i=current.messages.findIndex(m=>m.id===data.message.id);if(i>=0)current.messages[i]=data.message;}
  if(['agent_speaking','user_spoke','speech_chunk','agent_finished','agent_interrupted'].includes(event.type))renderMessages();
  if(event.type==='silence_started')current.silent=true;
  if(event.type==='silence_ended')current.silent=false;
  if(event.type==='session_paused')current.status='paused';
  if(event.type==='session_resumed')current.status='running';
  if(event.type==='provider_error')notice(`AI 연결 상태: ${data.code}. 잠시 후 다시 시도합니다.`);
  if(event.type==='api_wait')notice(`무료 API 한도를 지키기 위해 약 ${Math.ceil(data.seconds)}초 기다리는 중이에요. 내 발언은 바로 남길 수 있어요.`);
  if(event.type==='speech_chunk')notice();
  if(event.type==='budget_exhausted')notice('설정한 호출 예산을 모두 사용했어요.');
  if(event.type==='session_ended'){current.status='ended';current.calls=data.calls;notice(data.reason==='user'?'대화를 저장했어요.':`세션 한도에 도달해 대화를 마쳤어요 (${data.reason}).`);history();}
  controls();
}
function connect(id, token) {
  socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/${id}`);
  const own=socket;
  own.onopen=()=>{$('connection').textContent='실시간 연결됨';notice();};
  own.onmessage=e=>{if(token===generation)eventReceived(JSON.parse(e.data));};
  own.onclose=()=>{if(token!==generation)return;$('connection').textContent='연결 끊김';if(current.status==='running'||current.status==='paused'){notice('연결을 복구하는 중이에요.');reconnect=setTimeout(()=>connect(id,token),1500);}};
}
function show(session, subscribe=true) {
  generation++;clearTimeout(reconnect);if(socket){socket.onclose=null;socket.close();socket=null;}
  current=session;lastSeq=session.seq;$('workspace').hidden=false;$('sessionTopic').textContent=session.topic;
  renderMessages();renderAgents();controls();$('eventLog').replaceChildren();(session.events||[]).forEach(addEvent);
  $('connection').textContent=subscribe?'연결 중':'저장된 기록';if(subscribe)connect(session.id,generation);
}
async function history() {
  try {const sessions=await api('/api/sessions');$('historyList').replaceChildren(...sessions.reverse().map(s=>{const b=node('button','',s.topic+' · '+(labels[s.status]||s.status));b.onclick=async()=>{if(current && ['running','paused'].includes(current.status) && current.id!==s.id){notice('지금 테이블을 마친 뒤 다른 기록을 열어 주세요.');return;}try{const full=await api('/api/sessions/'+s.id);show(full,s.status!=='archived');notice();}catch(e){notice(e.message);}};return b;}));}catch(e){notice(e.message);}
}
$('startForm').onsubmit=async e=>{e.preventDefault();notice();$('startButton').disabled=true;try {show(await api('/api/sessions',{topic:$('topic').value,mode:$('mode').value}));await history();}catch(error){notice(error.message);controls();}};
$('messageForm').onsubmit=async e=>{e.preventDefault();if(!$('userText').value.trim())return;$('sendButton').disabled=true;try{await api(`/api/sessions/${current.id}/message`,{text:$('userText').value});$('userText').value='';}catch(error){notice(error.message);}controls();};
$('userText').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('messageForm').requestSubmit();}};
for(const [id,action] of [['pauseButton','pause'],['endButton','end']])$(id).onclick=async()=>{try {const verb=action==='pause'&&current.status==='paused'?'resume':action;const snapshot=await api(`/api/sessions/${current.id}/${verb}`,{});current.calls=snapshot.calls;controls();}catch(error){notice(error.message);}};
$('exportButton').onclick=async()=>{try{const snapshot=await api('/api/sessions/'+current.id);const url=URL.createObjectURL(new Blob([JSON.stringify(snapshot,null,2)],{type:'application/json'}));const a=node('a');a.href=url;a.download=`bstom-${current.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){notice(e.message);}};
async function init(){try{const config=await api('/api/config');$('mode').value=config.default_mode;$('mode').options[1].disabled=!config.live_available;$('configText').textContent=`실제 AI: ${config.live_available?'키 연결됨':'로컬 키 설정 필요'} · 세션 최대 ${config.limits.calls}회 호출 / ${config.limits.messages}개 발언 · Mock은 API를 사용하지 않습니다.`;await history();}catch(e){notice(e.message);}}
init();
