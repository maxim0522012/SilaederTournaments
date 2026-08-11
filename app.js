const ELO_START = 1000;
const ELO_K = 24;

let db = {users:[],matches:[],requests:[],tournaments:[],currentUserId:null};
let ratingCache = {};
let opponentOptions = [];
let selectedTournamentId = null;

async function api(path,options={}){
  const method=options.method||'GET', headers=options.body?{'Content-Type':'application/json'}:{};
  if(!['GET','HEAD','OPTIONS'].includes(method)&&db.csrfToken) headers['X-CSRF-Token']=db.csrfToken;
  const response=await fetch(path,{method,headers,body:options.body?JSON.stringify(options.body):undefined,credentials:'same-origin'});
  const data=await response.json().catch(()=>({})); if(!response.ok) throw new Error(data.error||'Ошибка сервера.'); return data;
}
async function refreshState(){ db=await api('/api/state'); ratingCache=calculateRatings(); }
async function mutate(path,body){ const result=await api(path,{method:'POST',body}); await refreshState(); render(); return result; }
function currentUser(){ return db.users.find(user=>user.id===db.currentUserId) || null; }
function userById(id){ return db.users.find(user=>user.id===id); }
function displayName(user){ return user ? `${user.firstName} ${user.lastName}` : 'Неизвестный игрок'; }
function publicName(user){ return escapeHtml(displayName(user)); }
function visibleName(user){ return displayName(user); }
function initials(user){ return user ? `${user.firstName[0]}${user.lastName[0]}` : '?'; }
function dateKey(timestamp){ const d=new Date(timestamp); return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; }
function formatDate(timestamp){ return new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'short',year:new Date(timestamp).getFullYear()!==new Date().getFullYear()?'numeric':undefined}).format(timestamp); }
function formatDateTime(timestamp){ return new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}).format(timestamp); }
function localDateTimeValue(timestamp){ const date=new Date(timestamp); return new Date(date.getTime()-date.getTimezoneOffset()*60000).toISOString().slice(0,16); }
function uid(prefix){ return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`; }
function escapeHtml(value=''){ const el=document.createElement('div'); el.textContent=String(value); return el.innerHTML; }

function calculateRatings(){
  const ratings={};
  db.users.filter(user=>user.isPlayer).forEach(user=>ratings[user.id]={rating:ELO_START,wins:0,losses:0,games:0,history:[]});
  [...db.matches].filter(match=>match.active).sort((a,b)=>a.createdAt-b.createdAt).forEach(match=>{
    const a=ratings[match.playerOne], b=ratings[match.playerTwo]; if(!a||!b) return;
    const aWon=match.scoreOne>match.scoreTwo;
    const expected=1/(1+10**((b.rating-a.rating)/400));
    const delta=Math.round(ELO_K*((aWon?1:0)-expected));
    a.rating+=delta; b.rating-=delta; a.games++; b.games++;
    if(aWon){ a.wins++; b.losses++; } else { b.wins++; a.losses++; }
    a.history.push({matchId:match.id,rating:a.rating,delta});
    b.history.push({matchId:match.id,rating:b.rating,delta:-delta});
    match._delta=Math.abs(delta);
  });
  return ratings;
}

function rankedPlayers(includeInactive=false){
  return db.users.filter(user=>user.isPlayer && (includeInactive || user.status==='active')).map(user=>({...user,...ratingCache[user.id]})).sort((a,b)=>b.rating-a.rating || b.wins-a.wins || a.createdAt-b.createdAt);
}

function render(){
  ratingCache=calculateRatings();
  renderAccount(); renderHome(); renderHistory(); renderNotifications(); renderTournaments(); renderAdmin();
  const requested=location.pathname.startsWith('/confirm/')?'home':(location.hash||'#home').slice(1);
  showView(requested,false);
  if(location.pathname.startsWith('/confirm/')) setTimeout(()=>handleConfirmationLink(location.pathname.split('/').pop()),0);
}

function renderAccount(){
  const me=currentUser();
  document.querySelector('#admin-nav').hidden=me?.role!=='admin';
  document.querySelector('#history-nav').hidden=!me?.isPlayer;
  const area=document.querySelector('#account-area');
  if(!me){ area.innerHTML='<button class="login-button" data-action="open-auth">Войти</button>'; return; }
  area.innerHTML=`<button class="account-button" data-profile="${me.id}" title="Открыть профиль"><span class="account-avatar">${escapeHtml(initials(me))}</span><span class="account-name">${escapeHtml(me.firstName)}<small>${me.status==='pending'?'Ожидает подтверждения':me.role==='admin'?'Администратор':me.role==='teacher'?'Учитель':escapeHtml(me.className)}</small></span></button><button class="text-action" data-action="logout">Выйти</button>`;
}

function renderHome(){
  const players=rankedPlayers();
  renderPlayerSummary(players);
  document.querySelector('#simple-rating').innerHTML=players.map((player,index)=>`<tr><td><span class="rank-number">${index+1}</span></td><td><button class="player-link" data-profile="${player.id}">${publicName(player)}</button></td><td>${player.games}</td><td>${player.wins}</td><td>${player.losses}</td><td><span class="rating-number">${player.rating}</span></td></tr>`).join('')||'<tr><td colspan="6" class="empty">Игроков пока нет</td></tr>';
}

function renderPlayerSummary(players){
  const summary=document.querySelector('#player-summary');
  const me=currentUser();
  if(!me?.isPlayer){ summary.hidden=true; summary.innerHTML=''; return; }

  const stats=ratingCache[me.id]||{rating:ELO_START,games:0};
  const place=players.findIndex(player=>player.id===me.id);
  const recentMatches=db.matches
    .filter(match=>match.active&&(match.playerOne===me.id||match.playerTwo===me.id))
    .sort((a,b)=>b.createdAt-a.createdAt)
    .slice(0,3);
  const recentHtml=recentMatches.map(match=>{
    const isFirst=match.playerOne===me.id;
    const ownScore=isFirst?match.scoreOne:match.scoreTwo;
    const opponentScore=isFirst?match.scoreTwo:match.scoreOne;
    const opponent=userById(isFirst?match.playerTwo:match.playerOne);
    const won=ownScore>opponentScore;
    return `<div class="player-summary-game ${won?'win':'loss'}">
      <span class="summary-game-result">${won?'Победа':'Поражение'}</span>
      <span class="summary-game-opponent">${publicName(opponent)}</span>
      <strong>${ownScore}:${opponentScore}</strong>
      <time datetime="${new Date(match.createdAt).toISOString()}">${formatDate(match.createdAt)}</time>
    </div>`;
  }).join('')||'<p class="player-summary-empty">Матчей пока нет — подайте первый результат.</p>';

  summary.innerHTML=`
    <div class="player-summary-identity">
      <span class="player-summary-avatar">${escapeHtml(initials(me))}</span>
      <div><span class="player-summary-label">Ваш профиль</span><button class="player-summary-name" data-profile="${me.id}">${publicName(me)}</button></div>
    </div>
    <div class="player-summary-stat"><strong>${place>=0?`#${place+1}`:'—'}</strong><span>место</span></div>
    <div class="player-summary-stat"><strong>${stats.rating}</strong><span>рейтинг Elo</span></div>
    <div class="player-summary-recent"><span class="player-summary-label">Последние игры</span><div class="player-summary-games">${recentHtml}</div></div>`;
  summary.hidden=false;
}

function renderHistory(){
  const content=document.querySelector('#history-content');
  const me=currentUser();
  if(!me?.isPlayer){
    content.innerHTML=`<div class="history-empty panel"><h2>${me?'Нет профиля игрока':'Войдите в аккаунт'}</h2><p>${me?'Для этого аккаунта не создан профиль участника.':'История матчей доступна после входа.'}</p>${me?'':'<button class="button primary" data-action="open-auth">Войти</button>'}</div>`;
    return;
  }

  const stats=ratingCache[me.id]||{games:0,wins:0,losses:0};
  const matches=db.matches
    .filter(match=>match.active&&(match.playerOne===me.id||match.playerTwo===me.id))
    .sort((a,b)=>b.createdAt-a.createdAt);
  const rows=matches.map(match=>{
    const isFirst=match.playerOne===me.id;
    const ownScore=isFirst?match.scoreOne:match.scoreTwo;
    const opponentScore=isFirst?match.scoreTwo:match.scoreOne;
    const opponent=userById(isFirst?match.playerTwo:match.playerOne);
    const won=ownScore>opponentScore;
    const delta=(won?1:-1)*(match._delta||0);
    return `<tr>
      <td><time datetime="${new Date(match.createdAt).toISOString()}">${formatDate(match.createdAt)}</time></td>
      <td><span class="history-result ${won?'win':'loss'}">${won?'Победа':'Поражение'}</span></td>
      <td><button class="player-link" data-profile="${opponent?.id||''}">${publicName(opponent)}</button></td>
      <td><strong class="history-score">${ownScore}:${opponentScore}</strong></td>
      <td><span class="history-kind">${match.tournamentMatchId?'Турнир':'Обычный матч'}</span></td>
      <td><span class="history-delta ${delta>=0?'positive':'negative'}">${delta>=0?'+':''}${delta}</span></td>
    </tr>`;
  }).join('')||'<tr><td colspan="6" class="empty">Сыгранных матчей пока нет</td></tr>';

  content.innerHTML=`
    <div class="history-stats">
      <div class="history-stat"><strong>${stats.games}</strong><span>Матчей</span></div>
      <div class="history-stat wins"><strong>${stats.wins}</strong><span>Побед</span></div>
      <div class="history-stat losses"><strong>${stats.losses}</strong><span>Поражений</span></div>
    </div>
    <div class="data-table-wrap history-table-wrap">
      <table class="data-table history-table">
        <thead><tr><th>Дата</th><th>Результат</th><th>Соперник</th><th>Счёт</th><th>Тип</th><th>Elo</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function tournamentStatus(tournament){
  if(tournament.status==='registration'&&Date.now()>=tournament.registrationDeadline) return ['Регистрация завершена','closed'];
  return ({registration:['Идёт регистрация','registration'],active:['Идёт сейчас','active'],completed:['Завершён','completed'],cancelled:['Отменён','cancelled']})[tournament.status]||[tournament.status,''];
}
function tournamentStageLabel(stage,round){
  if(stage==='upper') return `Верхняя сетка · Раунд ${round}`;
  if(stage==='lower') return `Нижняя сетка · Раунд ${round}`;
  if(stage==='final') return 'Гранд-финал';
  return 'Повторный финал · оба игрока имеют по одному поражению';
}
function tournamentMatchById(id){
  for(const tournament of db.tournaments||[]){ const match=tournament.matches.find(item=>item.id===id); if(match) return match; }
  return null;
}
function tournamentMatchAction(match){
  const me=currentUser();
  if(!me||match.status!=='pending'||![match.playerOne,match.playerTwo].includes(me.id)) return '';
  const resultRequest=db.requests.find(item=>item.tournamentMatchId===match.id&&item.status==='pending');
  if(!resultRequest) return `<button class="button small primary" data-tournament-result="${match.id}">Внести результат</button>`;
  if(resultRequest.requester===me.id) return `<button class="button small secondary" data-show-qr="${resultRequest.id}">Показать QR</button>`;
  return `<button class="button small primary" data-confirm-request="${resultRequest.id}">Проверить результат</button>`;
}
function tournamentPlayerColors(tournament){
  const colors={};
  tournament.participants.forEach((participant,index)=>{ colors[participant.userId]=`hsl(${Math.round((index*137.508+18)%360)} 68% 40%)`; });
  return colors;
}
function bracketMatchHtml(match,allMatches,playerColors){
  const one=userById(match.playerOne),two=userById(match.playerTwo),result=db.matches.find(item=>item.id===match.resultMatchId);
  const oneColor=playerColors[match.playerOne]||'var(--green)',twoColor=playerColors[match.playerTwo]||'var(--muted)';
  if(match.status==='bye') return `<div class="bracket-match bye" data-match-id="${escapeHtml(match.id)}"><div class="bracket-player winner" style="--player-color:${oneColor}" data-player-id="${escapeHtml(match.playerOne)}"><span>${publicName(one)}<small>Проход без игры</small></span><strong>→</strong></div></div>`;
  const scoreOne=result?(result.playerOne===match.playerOne?result.scoreOne:result.scoreTwo):null;
  const scoreTwo=result?(result.playerOne===match.playerTwo?result.scoreOne:result.scoreTwo):null;
  const nextMatch=playerId=>allMatches.find(item=>item.sequence>match.sequence&&[item.playerOne,item.playerTwo].includes(playerId));
  const oneNext=nextMatch(match.playerOne),twoNext=nextMatch(match.playerTwo);
  const oneEliminated=match.loser===match.playerOne&&!oneNext;
  const twoEliminated=match.loser===match.playerTwo&&!twoNext;
  const transferLabel=next=>{
    if(!next||next.stage===match.stage) return '';
    if(next.stage==='lower') return `↓ Нижняя сетка · раунд ${next.roundNumber}`;
    if(next.stage==='final') return '→ Гранд-финал';
    return '→ Повторный финал';
  };
  const oneTransfer=transferLabel(oneNext),twoTransfer=transferLabel(twoNext);
  return `<div class="bracket-match ${match.status}" data-match-id="${escapeHtml(match.id)}">
    <div class="bracket-player ${match.winner===match.playerOne?'winner':''} ${oneEliminated?'eliminated':''}" style="--player-color:${oneColor}" data-player-id="${escapeHtml(match.playerOne)}"><span>${publicName(one)}${oneEliminated?'<small>Выбыл</small>':oneTransfer?`<small class="bracket-transfer">${oneTransfer}</small>`:''}</span><strong>${scoreOne??'—'}</strong></div>
    <div class="bracket-player ${match.winner===match.playerTwo?'winner':''} ${twoEliminated?'eliminated':''}" style="--player-color:${twoColor}" data-player-id="${escapeHtml(match.playerTwo||'')}"><span>${publicName(two)}${twoEliminated?'<small>Выбыл</small>':twoTransfer?`<small class="bracket-transfer">${twoTransfer}</small>`:''}</span><strong>${scoreTwo??'—'}</strong></div>
    <div class="bracket-action">${tournamentMatchAction(match)}</div>
  </div>`;
}
function bracketTransitions(matches){
  const ordered=[...matches].sort((a,b)=>a.sequence-b.sequence||a.position-b.position);
  const transitions=[];
  ordered.forEach(match=>{
    if(!['completed','bye'].includes(match.status)) return;
    [{playerId:match.winner,type:'winner'},{playerId:match.loser,type:'loser'}].forEach(({playerId,type})=>{
      if(!playerId) return;
      const target=ordered.find(item=>item.sequence>match.sequence&&[item.playerOne,item.playerTwo].includes(playerId));
      if(target) transitions.push({fromId:match.id,toId:target.id,playerId,type});
    });
  });
  return transitions;
}
function addBracketConnector(layer,x,y,width,height,className,color){
  const line=document.createElement('span');
  line.className=`bracket-connector ${className}`;
  Object.assign(line.style,{left:`${x}px`,top:`${y}px`,width:`${Math.max(width,2)}px`,height:`${Math.max(height,2)}px`});
  line.style.setProperty('--connector-color',color);
  layer.appendChild(line);
}
function drawTournamentConnections(tournament){
  const canvases=[...document.querySelectorAll('[data-bracket-id]')].filter(item=>item.dataset.bracketId===tournament.id);
  const playerColors=tournamentPlayerColors(tournament);
  canvases.forEach(canvas=>{
    const layer=canvas.querySelector('.bracket-connectors');
    layer.innerHTML='';
    const canvasRect=canvas.getBoundingClientRect();
    bracketTransitions(tournament.matches).forEach(transition=>{
      const sourceMatch=[...canvas.querySelectorAll('[data-match-id]')].find(item=>item.dataset.matchId===transition.fromId);
      const targetMatch=[...canvas.querySelectorAll('[data-match-id]')].find(item=>item.dataset.matchId===transition.toId);
      const sourceRow=[...(sourceMatch?.querySelectorAll('[data-player-id]')||[])].find(item=>item.dataset.playerId===transition.playerId);
      const targetRow=[...(targetMatch?.querySelectorAll('[data-player-id]')||[])].find(item=>item.dataset.playerId===transition.playerId);
      if(!sourceRow||!targetRow) return;
      const sourceRect=sourceRow.getBoundingClientRect(),targetRect=targetRow.getBoundingClientRect();
      const startX=sourceRect.right-canvasRect.left+3,startY=sourceRect.top+sourceRect.height/2-canvasRect.top;
      const endX=targetRect.left-canvasRect.left-9,endY=targetRect.top+targetRect.height/2-canvasRect.top;
      if(endX<=startX) return;
      const middleX=startX+(endX-startX)*.52;
      const color=playerColors[transition.playerId]||'var(--green)';
      addBracketConnector(layer,startX,startY-1,middleX-startX,2,`horizontal ${transition.type}`,color);
      addBracketConnector(layer,middleX-1,Math.min(startY,endY),2,Math.abs(endY-startY),`vertical ${transition.type}`,color);
      addBracketConnector(layer,middleX,endY-1,endX-middleX,2,`horizontal arrow ${transition.type}`,color);
    });
  });
}
function tournamentBracketLaneHtml(tournament,lane,title,playerColors){
  const laneMatches=tournament.matches.filter(match=>lane==='finals'?['final','reset'].includes(match.stage):match.stage===lane);
  if(!laneMatches.length) return '';
  const sequences=[...new Set(laneMatches.map(match=>match.sequence))];
  const maxRoundSize=Math.max(1,...sequences.map(sequence=>laneMatches.filter(match=>match.sequence===sequence).length));
  return `<section class="bracket-lane ${lane}"><div class="bracket-lane-heading"><h4>${title}</h4><span>${laneMatches.length} матч${laneMatches.length===1?'':laneMatches.length<5?'а':'ей'}</span></div><div class="bracket-board"><div class="bracket-canvas" data-bracket-id="${escapeHtml(tournament.id)}" data-bracket-lane="${lane}" style="--bracket-height:${Math.max(170,maxRoundSize*145)}px"><div class="bracket-connectors" aria-hidden="true"></div><div class="bracket-rounds">${sequences.map(sequence=>{const matches=laneMatches.filter(match=>match.sequence===sequence),first=matches[0];return `<section class="bracket-round"><h3>${lane==='finals'?tournamentStageLabel(first.stage,first.roundNumber):`Раунд ${first.roundNumber}`}</h3><div class="bracket-match-list">${matches.map(match=>bracketMatchHtml(match,tournament.matches,playerColors)).join('')}</div></section>`}).join('')}</div></div></div></section>`;
}
function renderTournaments(){
  const list=document.querySelector('#tournament-list'),detail=document.querySelector('#tournament-detail');
  const order={active:0,registration:1,completed:2,cancelled:3};
  const tournaments=[...(db.tournaments||[])].sort((a,b)=>(order[a.status]??9)-(order[b.status]??9)||b.startAt-a.startAt);
  if(!tournaments.length){ list.innerHTML='<div class="panel empty">Турниров пока нет</div>'; detail.innerHTML=''; return; }
  if(!tournaments.some(item=>item.id===selectedTournamentId)) selectedTournamentId=tournaments[0].id;
  list.innerHTML=tournaments.map(tournament=>{const [label,state]=tournamentStatus(tournament);return `<button class="tournament-card ${tournament.id===selectedTournamentId?'selected':''}" data-open-tournament="${tournament.id}">
    <span class="tournament-card-top"><span class="tournament-state ${state}">${label}</span><span>${tournament.participants.length}/${tournament.maxPlayers}</span></span>
    <strong>${escapeHtml(tournament.name)}</strong><span>${formatDateTime(tournament.startAt)}</span>
  </button>`}).join('');
  const tournament=tournaments.find(item=>item.id===selectedTournamentId),me=currentUser();
  const participant=tournament.participants.find(item=>item.userId===me?.id),registrationOpen=tournament.status==='registration'&&Date.now()<tournament.registrationDeadline&&tournament.participants.length<tournament.maxPlayers;
  let registrationAction='';
  if(!me&&tournament.status==='registration') registrationAction='<button class="button primary" data-action="open-auth">Войти для участия</button>';
  else if(participant&&tournament.status==='registration') registrationAction=`<button class="button secondary" data-leave-tournament="${tournament.id}">Отменить участие</button>`;
  else if(me?.isPlayer&&registrationOpen) registrationAction=`<button class="button primary" data-join-tournament="${tournament.id}">Участвовать</button>`;
  const podiumPlace=userId=>tournament.podium?.first===userId?'1 место':tournament.podium?.second===userId?'2 место':tournament.podium?.third===userId?'3 место':'';
  const participants=tournament.participants.map(item=>{const user=userById(item.userId),place=podiumPlace(item.userId);return `<div class="tournament-participant ${item.eliminated&&!place?'eliminated':''}"><span class="participant-seed">${item.seed?`#${item.seed}`:'•'}</span><button class="player-link" data-profile="${item.userId}">${publicName(user)}</button>${tournament.status==='active'||tournament.status==='completed'?`<span>${place||(item.eliminated?'Выбыл':`${item.losses} пораж.`)}</span>`:''}</div>`}).join('')||'<div class="empty">Пока никто не зарегистрировался</div>';
  const hasBracket=tournament.matches.length>0;
  const playerColors=tournamentPlayerColors(tournament);
  const playerColorKey=tournament.participants.map(participant=>`<span><i style="--player-color:${playerColors[participant.userId]}"></i>${publicName(userById(participant.userId))}</span>`).join('');
  const bracket=hasBracket?`<div class="bracket-legend"><span class="solid">Сплошная — победитель</span><span class="dashed">Пунктир — проигравший</span></div><div class="bracket-player-key">${playerColorKey}</div><div class="bracket-stack">${tournamentBracketLaneHtml(tournament,'upper','Верхняя сетка',playerColors)}${tournamentBracketLaneHtml(tournament,'lower','Нижняя сетка',playerColors)}${tournamentBracketLaneHtml(tournament,'finals','Финалы',playerColors)}</div>`:'<div class="empty bracket-empty">Сетка появится после запуска турнира администратором</div>';
  const podium=tournament.podium;
  const podiumHtml=podium?`<section class="tournament-podium"><div class="podium-heading"><p class="kicker">ИТОГИ ТУРНИРА</p><h3>Призовые места</h3></div><div class="podium-places"><div class="podium-place first"><span>1</span><div><strong>${publicName(userById(podium.first))}</strong><small>Победитель финала</small></div></div><div class="podium-place second"><span>2</span><div><strong>${publicName(userById(podium.second))}</strong><small>Финалист</small></div></div>${podium.third?`<div class="podium-place third"><span>3</span><div><strong>${publicName(userById(podium.third))}</strong><small>Третье место</small></div></div>`:''}</div></section>`:'';
  detail.innerHTML=`<section class="panel tournament-detail-panel">
    <div class="tournament-detail-heading"><div><p class="kicker">${escapeHtml(tournamentStatus(tournament)[0].toUpperCase())}</p><h2>${escapeHtml(tournament.name)}</h2><p>${escapeHtml(tournament.description||'Турнир по настольному теннису с двойным выбыванием.')}</p></div>${registrationAction}</div>
    <div class="tournament-meta"><span><strong>${formatDateTime(tournament.startAt)}</strong>Начало</span><span><strong>${formatDateTime(tournament.registrationDeadline)}</strong>Регистрация до</span><span><strong>${tournament.participants.length} / ${tournament.maxPlayers}</strong>Участники</span>${tournament.championId?`<span><strong>${publicName(userById(tournament.championId))}</strong>Победитель</span>`:''}</div>${podiumHtml}
    <div class="tournament-layout"><aside><h3>Участники</h3><div class="participants-list">${participants}</div></aside><section class="tournament-bracket"><h3>Турнирная сетка</h3>${bracket}</section></div>
  </section>`;
  if(hasBracket) requestAnimationFrame(()=>drawTournamentConnections(tournament));
}

function renderNotifications(){
  const container=document.querySelector('#notifications-panel'), me=currentUser();
  if(!me||me.status!=='active'){ container.innerHTML=''; return; }
  const incoming=db.requests.filter(r=>r.status==='pending'&&r.opponent===me.id&&r.notified);
  const outgoing=db.requests.filter(r=>r.status==='pending'&&r.requester===me.id);
  if(!incoming.length&&!outgoing.length){ container.innerHTML=''; return; }
  container.innerHTML=`<section class="notification-section"><h2>Заявки на результаты</h2>
    ${incoming.map(r=>{const sender=userById(r.requester);return `<div class="notification-card"><div><strong>${publicName(sender)} предлагает результат ${r.scoreRequester}:${r.scoreOpponent}</strong><p>${formatDate(r.createdAt)} · требуется ваше подтверждение</p></div><div class="notification-actions"><button class="button small danger" data-reject-request="${r.id}">Отклонить</button><button class="button small primary" data-confirm-request="${r.id}">Проверить</button></div></div>`}).join('')}
    ${outgoing.map(r=>{const opponent=userById(r.opponent);return `<div class="notification-card pending-card"><div><strong>Заявка для ${publicName(opponent)}: ${r.scoreRequester}:${r.scoreOpponent}</strong><p>${r.notified?'Уведомление отправлено, ожидается ответ':'Ожидается подтверждение по QR-коду'}</p></div><div class="notification-actions"><button class="button small secondary" data-show-qr="${r.id}">Показать QR</button></div></div>`}).join('')}
  </section>`;
}

function renderAdmin(){
  const me=currentUser(); if(me?.role!=='admin') return;
  const pending=db.users.filter(u=>u.status==='pending'), disputes=db.matches.filter(m=>m.active&&m.dispute), active=db.users.filter(u=>u.status==='active'&&u.isPlayer),running=(db.tournaments||[]).filter(t=>t.status==='active'||t.status==='registration');
  document.querySelector('#admin-stats').innerHTML=`<div class="admin-stat"><strong>${pending.length}</strong><span>заявок ожидают решения</span></div><div class="admin-stat"><strong>${disputes.length}</strong><span>жалоб требуют проверки</span></div><div class="admin-stat"><strong>${active.length}</strong><span>активных игроков</span></div><div class="admin-stat"><strong>${running.length}</strong><span>открытых турниров</span></div>`;
  document.querySelector('#pending-users').innerHTML=pending.map(u=>`<div class="request-row"><div><div class="request-name">${escapeHtml(u.firstName)} ${escapeHtml(u.lastName)}</div><div class="request-meta">${escapeHtml(u.className)} класс · @${escapeHtml(u.login)}</div></div><div class="row-actions"><button class="button small primary" data-approve="${u.id}">Подтвердить</button><button class="button small danger" data-reject="${u.id}">Отклонить</button></div></div>`).join('')||'<div class="empty">Новых заявок нет</div>';
  document.querySelector('#disputes').innerHTML=disputes.map(m=>{const a=userById(m.playerOne),b=userById(m.playerTwo),reporter=userById(m.dispute.userId);return `<div class="request-row"><div><div class="request-name">${publicName(a)} ${m.scoreOne}:${m.scoreTwo} ${publicName(b)}</div><div class="request-meta">${publicName(reporter)}: ${escapeHtml(m.dispute.reason)}</div></div><div class="row-actions"><button class="button small secondary" data-resolve="${m.id}">Оставить</button><button class="button small danger" data-cancel-match="${m.id}">Отменить матч</button></div></div>`}).join('')||'<div class="empty">Жалоб нет</div>';
  document.querySelector('#users-body').innerHTML=db.users.map(u=>`<tr><td>${escapeHtml(u.firstName)} ${escapeHtml(u.lastName)}${u.className?` · ${escapeHtml(u.className)}`:''}</td><td>${u.login?`@${escapeHtml(u.login)}`:'ЛК Силаэдра'}</td><td><span class="state ${u.status}">${statusLabel(u.status)}</span></td><td>${u.role==='admin'?'Администратор':u.role==='teacher'?'Учитель':'Ученик'}</td><td>${u.role!=='admin'&&u.status!=='pending'?`<button class="button small secondary" data-toggle-user="${u.id}">${u.status==='active'?'Сделать неактивным':'Активировать'}</button>`:''}</td></tr>`).join('');
  document.querySelector('#admin-tournaments-list').innerHTML=(db.tournaments||[]).map(t=>{const [label,state]=tournamentStatus(t);return `<div class="admin-tournament-row"><div><div class="request-name">${escapeHtml(t.name)}</div><div class="request-meta">${label} · ${t.participants.length}/${t.maxPlayers} участников · ${formatDateTime(t.startAt)}</div></div><div class="row-actions"><button class="button small secondary" data-open-tournament="${t.id}" data-go-tournaments>Открыть</button>${t.status==='registration'?`<button class="button small primary" data-start-tournament="${t.id}">Сформировать сетку</button><button class="button small danger" data-cancel-tournament="${t.id}">Отменить</button>`:''}</div></div>`}).join('')||'<div class="empty">Турниров пока нет</div>';
}
function statusLabel(status){ return ({active:'Активен',pending:'Ожидает',inactive:'Неактивен',rejected:'Отклонён'})[status]||status; }

function showView(name,updateHash=true){
  const me=currentUser();
  if(name==='admin'&&me?.role!=='admin') name='home';
  if(me?.status==='pending'&&name!=='pending') name='pending';
  if(name==='pending'&&me?.status!=='pending') name='home';
  if(!document.querySelector(`[data-view="${name}"]`)) name='home';
  document.querySelectorAll('[data-view]').forEach(view=>view.hidden=view.dataset.view!==name);
  document.querySelectorAll('[data-route]').forEach(link=>link.classList.toggle('active',link.dataset.route===name));
  if(name==='tournaments') requestAnimationFrame(()=>{const tournament=(db.tournaments||[]).find(item=>item.id===selectedTournamentId);if(tournament)drawTournamentConnections(tournament)});
  if(updateHash) history.pushState(null,'',`#${name}`); window.scrollTo({top:0,behavior:'smooth'});
}

function openAuth(){
  const button=document.querySelector('#oidc-login-button'),message=document.querySelector('#oidc-message');
  button.classList.toggle('disabled',!db.oidcEnabled); button.setAttribute('aria-disabled',String(!db.oidcEnabled));
  message.textContent=db.oidcEnabled?'':'Школьный вход будет доступен после настройки OIDC-клиента.';
  document.querySelector('#auth-dialog').showModal();
}
function openMatchForm(tournamentMatch=null){
  const me=currentUser();
  if(!me){ openAuth(); return; }
  if(me.status!=='active'){ toast('Сначала дождитесь подтверждения администратора'); return; }
  if(!me.isPlayer){ toast('Администратор без профиля игрока не может подать результат'); return; }
  const players=rankedPlayers().filter(p=>p.id!==me.id); const form=document.querySelector('#match-form');
  opponentOptions=players; form.reset(); form.opponent.value=''; form.tournamentMatchId.value=tournamentMatch?.id||''; form.scoreRequester.value=11; form.scoreOpponent.value=7;
  document.querySelector('#opponent-search').value=''; document.querySelector('#student-picker').classList.remove('selected');
  document.querySelector('#student-suggestions').hidden=true; document.querySelector('#opponent-search').setAttribute('aria-expanded','false');
  document.querySelector('#requester-name').textContent=displayName(me);
  const search=document.querySelector('#opponent-search');
  search.readOnly=Boolean(tournamentMatch);
  document.querySelector('#match-kicker').textContent=tournamentMatch?'ТУРНИРНЫЙ МАТЧ':'НОВАЯ ЗАЯВКА';
  document.querySelector('#match-title').textContent=tournamentMatch?'Внести результат турнира':'Подать результат матча';
  document.querySelector('#match-description').textContent=tournamentMatch?'Соперник уже выбран сеткой. После подтверждения система автоматически распределит игроков по следующему раунду.':'Укажите соперника и счёт. После этого соперник должен подтвердить результат по QR-коду или через уведомление.';
  if(tournamentMatch){
    const opponentId=tournamentMatch.playerOne===me.id?tournamentMatch.playerTwo:tournamentMatch.playerOne;
    const opponent=players.find(player=>player.id===opponentId);
    if(!opponent){ toast('Соперник недоступен'); return; }
    opponentOptions=[opponent]; selectOpponent(opponent.id);
  }
  form.querySelector('[data-form-message]').textContent=''; document.querySelector('#match-dialog').showModal();
}

function renderStudentSuggestions(query=''){
  const box=document.querySelector('#student-suggestions'), normalized=query.trim().toLowerCase();
  const matches=opponentOptions.filter(player=>`${player.firstName} ${player.lastName} ${player.className}`.toLowerCase().includes(normalized)).slice(0,8);
  box.innerHTML=matches.length?matches.map(player=>`<button class="student-option" type="button" role="option" data-student="${player.id}"><strong>${publicName(player)}</strong><span>${player.rating} Elo</span></button>`).join(''):'<div class="student-empty">Подходящие ученики не найдены</div>';
  box.hidden=false; document.querySelector('#opponent-search').setAttribute('aria-expanded','true');
}

function selectOpponent(id){
  const player=opponentOptions.find(item=>item.id===id); if(!player) return;
  const form=document.querySelector('#match-form'), search=document.querySelector('#opponent-search'), box=document.querySelector('#student-suggestions');
  form.opponent.value=player.id; search.value=displayName(player); document.querySelector('#student-picker').classList.add('selected');
  box.hidden=true; search.setAttribute('aria-expanded','false'); form.querySelector('[data-form-message]').textContent='';
}

function validScore(a,b){ const high=Math.max(a,b),low=Math.min(a,b); if(a===b||high<11) return false; if(low<10) return high===11; return high-low===2; }
function validateDailyRules(playerOne,playerTwo){
  const today=dateKey(Date.now()); const todays=db.matches.filter(m=>m.active&&dateKey(m.createdAt)===today);
  const duplicate=todays.some(m=>[m.playerOne,m.playerTwo].sort().join('|')===[playerOne,playerTwo].sort().join('|'));
  if(duplicate) return 'Эта пара уже сыграла рейтинговый матч сегодня.';
  const pending=db.requests.some(r=>r.status==='pending'&&dateKey(r.createdAt)===today&&[r.requester,r.opponent].sort().join('|')===[playerOne,playerTwo].sort().join('|'));
  if(pending) return 'Для этой пары уже есть заявка на результат сегодня.';
  return '';
}

function requestById(id){ return db.requests.find(request=>request.id===id); }
function showQr(request){
  const opponent=userById(request.opponent), requester=userById(request.requester);
  document.querySelector('#request-qr').src=`/api/requests/${encodeURIComponent(request.id)}/qr?v=${Date.now()}`;
  document.querySelector('#request-summary').textContent=`${displayName(requester)} ${request.scoreRequester}:${request.scoreOpponent} ${displayName(opponent)}`;
  const button=document.querySelector('#send-notification'); button.dataset.requestId=request.id; button.disabled=request.notified; button.textContent=request.notified?'Уведомление отправлено':'Отправить на сайте и по email';
  document.querySelector('#notification-status').textContent=request.notified?'Соперник увидит заявку при входе в аккаунт.':'Уведомление появится на сайте и придёт на почту из ЛК.';
  document.querySelector('#qr-dialog').showModal();
}

function openConfirmation(request){
  const me=currentUser(); if(!request||request.status!=='pending'){ toast('Эта заявка уже обработана'); return; }
  if(!me){ openAuth(); return; }
  if(me.id!==request.opponent){ toast('Подтвердить результат может только указанный соперник'); return; }
  const sender=userById(request.requester);
  document.querySelector('#confirm-result').textContent=`${displayName(sender)} ${request.scoreRequester}:${request.scoreOpponent} ${displayName(me)}`;
  document.querySelector('#accept-request').dataset.requestId=request.id; document.querySelector('#reject-request').dataset.requestId=request.id;
  document.querySelector('#confirm-dialog').showModal();
}

async function handleConfirmationLink(token){
  if(!currentUser()){ if(!document.querySelector('#auth-dialog').open) openAuth(); return; }
  try { const result=await api(`/api/confirm/${encodeURIComponent(token)}`); const request=requestById(result.id); if(!request) throw new Error('Заявка не найдена.'); if(!document.querySelector('#confirm-dialog').open) openConfirmation(request); }
  catch(error){ toast(error.message); }
}

async function acceptRequest(request){
  const me=currentUser(); if(!request||request.status!=='pending'||me?.id!==request.opponent) return;
  try { await mutate(`/api/requests/${request.id}/accept`,{}); document.querySelector('#confirm-dialog').close(); const target=request.tournamentMatchId?'tournaments':'home';history.replaceState(null,'',`#${target}`);showView(target,false);toast(request.tournamentMatchId?'Результат подтверждён, сетка обновлена':'Результат подтверждён, рейтинг обновлён'); }
  catch(error){ toast(error.message); }
}
async function rejectRequest(request){
  const me=currentUser(); if(!request||request.status!=='pending'||me?.id!==request.opponent) return;
  try { await mutate(`/api/requests/${request.id}/reject`,{}); if(document.querySelector('#confirm-dialog').open) document.querySelector('#confirm-dialog').close(); const target=request.tournamentMatchId?'tournaments':'home';history.replaceState(null,'',`#${target}`);showView(target,false);toast('Заявка отклонена'); }
  catch(error){ toast(error.message); }
}
function validateAcceptedPair(playerOne,playerTwo){
  const today=dateKey(Date.now());
  return db.matches.some(m=>m.active&&dateKey(m.createdAt)===today&&[m.playerOne,m.playerTwo].sort().join('|')===[playerOne,playerTwo].sort().join('|'))?'Эта пара уже имеет подтверждённый матч сегодня.':'';
}

function openProfile(id){
  const user=userById(id), stats=ratingCache[id]; if(!user||!stats) return;
  const ranking=rankedPlayers(true), place=ranking.findIndex(p=>p.id===id)+1, games=db.matches.filter(m=>m.active&&(m.playerOne===id||m.playerTwo===id)).sort((a,b)=>b.createdAt-a.createdAt);
  const rate=stats.games?Math.round(stats.wins/stats.games*100):0;
  const email=user.email&&(currentUser()?.id===id||currentUser()?.role==='admin')?`<p>Почта ЛК: ${escapeHtml(user.email)}</p>`:'';
  document.querySelector('#profile-content').innerHTML=`<div class="profile-header"><span class="profile-avatar">${escapeHtml(initials(user))}</span><div><h2>${escapeHtml(visibleName(user))}</h2>${email}${user.status==='inactive'?'<p>Неактивный игрок</p>':''}</div></div><div class="profile-stats"><div class="profile-stat"><strong>${stats.rating}</strong><span>Elo</span></div><div class="profile-stat"><strong>${place||'—'}</strong><span>Место</span></div><div class="profile-stat"><strong>${stats.wins}/${stats.losses}</strong><span>Победы / поражения</span></div><div class="profile-stat"><strong>${rate}%</strong><span>Побед</span></div></div><div class="profile-history"><h3>Последние матчи</h3>${games.slice(0,6).map(m=>{const opponent=userById(m.playerOne===id?m.playerTwo:m.playerOne), own=m.playerOne===id?m.scoreOne:m.scoreTwo,other=m.playerOne===id?m.scoreTwo:m.scoreOne;return `<div class="profile-game"><span>${formatDate(m.createdAt)} · ${publicName(opponent)}</span><span>${own}:${other}</span></div>`}).join('')||'<div class="empty">Матчей пока нет</div>'}</div>`;
  document.querySelector('#profile-dialog').showModal();
}

function toast(message){ const el=document.querySelector('#toast'); el.textContent=message; el.classList.add('show'); clearTimeout(toast.timer); toast.timer=setTimeout(()=>el.classList.remove('show'),2500); }

document.addEventListener('click',async event=>{
  const student=event.target.closest('[data-student]'); if(student){ selectOpponent(student.dataset.student); return; }
  const route=event.target.closest('[data-route]'); if(route){ event.preventDefault(); showView(route.dataset.route); return; }
  if(event.target.closest('[data-action="open-auth"]')) openAuth();
  if(event.target.closest('[data-action="logout"]')){ try{if(db.oidcSession){const result=await api('/auth/silaeder/logout',{method:'POST',body:{}});location.href=result.redirect}else{await mutate('/api/logout',{});toast('Вы вышли из аккаунта')}}catch(error){toast(error.message)} }
  if(event.target.closest('[data-action="add-match"]')) openMatchForm();
  const createTournament=event.target.closest('[data-action="create-tournament"]'); if(createTournament){ const form=document.querySelector('#tournament-form'),now=Date.now();form.reset();form.maxPlayers.value=8;form.registrationDeadline.value=localDateTimeValue(now+24*60*60*1000);form.startAt.value=localDateTimeValue(now+48*60*60*1000);form.querySelector('[data-form-message]').textContent='';document.querySelector('#tournament-dialog').showModal(); }
  const openTournament=event.target.closest('[data-open-tournament]'); if(openTournament){ selectedTournamentId=openTournament.dataset.openTournament;renderTournaments();if(openTournament.hasAttribute('data-go-tournaments'))showView('tournaments'); }
  const joinTournament=event.target.closest('[data-join-tournament]'); if(joinTournament){ try{await mutate(`/api/tournaments/${joinTournament.dataset.joinTournament}/join`,{});toast('Вы зарегистрированы на турнир')}catch(error){toast(error.message)} }
  const leaveTournament=event.target.closest('[data-leave-tournament]'); if(leaveTournament){ try{await mutate(`/api/tournaments/${leaveTournament.dataset.leaveTournament}/leave`,{});toast('Участие отменено')}catch(error){toast(error.message)} }
  const startTournament=event.target.closest('[data-start-tournament]'); if(startTournament&&confirm('Сформировать сетку и закрыть регистрацию?')){ try{await mutate(`/api/admin/tournaments/${startTournament.dataset.startTournament}/start`,{});selectedTournamentId=startTournament.dataset.startTournament;toast('Участники автоматически распределены по сетке')}catch(error){toast(error.message)} }
  const cancelTournament=event.target.closest('[data-cancel-tournament]'); if(cancelTournament&&confirm('Отменить этот турнир?')){ try{await mutate(`/api/admin/tournaments/${cancelTournament.dataset.cancelTournament}/cancel`,{});toast('Турнир отменён')}catch(error){toast(error.message)} }
  const tournamentResult=event.target.closest('[data-tournament-result]'); if(tournamentResult) openMatchForm(tournamentMatchById(tournamentResult.dataset.tournamentResult));
  const profile=event.target.closest('[data-profile]'); if(profile) openProfile(profile.dataset.profile);
  const close=event.target.closest('[data-close]'); if(close) close.closest('dialog').close();
  const oidcButton=event.target.closest('#oidc-login-button'); if(oidcButton&&!db.oidcEnabled){event.preventDefault();toast('OIDC-клиент ещё не настроен')}
  const approve=event.target.closest('[data-approve]'); if(approve){ try{await mutate(`/api/admin/users/${approve.dataset.approve}/approve`,{});toast('Регистрация подтверждена')}catch(error){toast(error.message)} }
  const reject=event.target.closest('[data-reject]'); if(reject){ try{await mutate(`/api/admin/users/${reject.dataset.reject}/reject`,{});toast('Заявка отклонена')}catch(error){toast(error.message)} }
  const toggle=event.target.closest('[data-toggle-user]'); if(toggle){ event.preventDefault(); const u=userById(toggle.dataset.toggleUser),action=u.status==='active'?'deactivate':'activate'; toggle.disabled=true; toggle.textContent='Сохраняем…'; try{await mutate(`/api/admin/users/${u.id}/${action}`,{});toast(action==='deactivate'?`${displayName(u)} теперь неактивен`:`${displayName(u)} снова активен`);showView('admin',false)}catch(error){toast(error.message)} return; }
  const resolve=event.target.closest('[data-resolve]'); if(resolve){ try{await mutate(`/api/admin/matches/${resolve.dataset.resolve}/resolve`,{});toast('Жалоба закрыта')}catch(error){toast(error.message)} }
  const cancel=event.target.closest('[data-cancel-match]'); if(cancel){ try{await mutate(`/api/admin/matches/${cancel.dataset.cancel}/cancel`,{});toast('Матч отменён, рейтинг пересчитан')}catch(error){toast(error.message)} }
  const dispute=event.target.closest('[data-dispute]'); if(dispute){ const form=document.querySelector('#dispute-form');form.matchId.value=dispute.dataset.dispute;form.reset();form.matchId.value=dispute.dataset.dispute;document.querySelector('#dispute-dialog').showModal(); }
  const showQrButton=event.target.closest('[data-show-qr]'); if(showQrButton) showQr(requestById(showQrButton.dataset.showQr));
  const confirmRequest=event.target.closest('[data-confirm-request]'); if(confirmRequest) openConfirmation(requestById(confirmRequest.dataset.confirmRequest));
  const rejectRequestButton=event.target.closest('[data-reject-request]'); if(rejectRequestButton) rejectRequest(requestById(rejectRequestButton.dataset.rejectRequest));
  if(!event.target.closest('#student-picker')){ const suggestions=document.querySelector('#student-suggestions'); suggestions.hidden=true; document.querySelector('#opponent-search').setAttribute('aria-expanded','false'); }
});

document.querySelector('#send-notification').addEventListener('click',async event=>{
  const request=requestById(event.currentTarget.dataset.requestId); if(!request||request.status!=='pending') return;
  try {
    const result=await mutate(`/api/requests/${request.id}/notify`,{}); event.currentTarget.disabled=true; event.currentTarget.textContent='Уведомление отправлено';
    const messages={sent:'Уведомление отправлено на сайте и по email.',no_email:'Уведомление отправлено на сайте. В ЛК соперника не указана почта.',not_configured:'Уведомление отправлено на сайте. Отправка email не настроена на сервере.',failed:'Уведомление отправлено на сайте, но письмо доставить не удалось.'};
    const message=messages[result.emailStatus]||'Уведомление отправлено на сайте.'; document.querySelector('#notification-status').textContent=message; toast(message);
  }
  catch(error){ toast(error.message); }
});
document.querySelector('#accept-request').addEventListener('click',event=>acceptRequest(requestById(event.currentTarget.dataset.requestId)));
document.querySelector('#reject-request').addEventListener('click',event=>rejectRequest(requestById(event.currentTarget.dataset.requestId)));

document.querySelector('#login-form').addEventListener('submit',async event=>{
  event.preventDefault(); const form=event.currentTarget,message=form.querySelector('[data-form-message]');
  try { await api('/api/login',{method:'POST',body:{login:form.login.value.trim(),password:form.password.value}}); await refreshState(); form.reset(); message.textContent=''; document.querySelector('#auth-dialog').close(); render(); toast('Вход выполнен'); }
  catch(error){ message.textContent=error.message; }
});

document.querySelector('#match-form').addEventListener('submit',async event=>{
  event.preventDefault(); const form=event.currentTarget, me=currentUser(), opponent=form.opponent.value,s1=Number(form.scoreRequester.value),s2=Number(form.scoreOpponent.value),message=form.querySelector('[data-form-message]');
  if(!me||me.status!=='active'){ message.textContent='Аккаунт не подтверждён.'; return; }
  if(!opponent){ message.textContent='Выберите соперника из предложенного списка.'; return; }
  if(opponent===me.id){ message.textContent='Выберите другого игрока.'; return; }
  if(!validScore(s1,s2)){ message.textContent='Некорректный счёт. Победа — от 11 очков с преимуществом в два.'; return; }
  try { const created=await api('/api/requests',{method:'POST',body:{opponent,scoreRequester:s1,scoreOpponent:s2,tournamentMatchId:form.tournamentMatchId.value||null}}); await refreshState(); const result=requestById(created.id); document.querySelector('#match-dialog').close();render();showQr(result);toast('Заявка создана'); }
  catch(error){ message.textContent=error.message; }
});

document.querySelector('#tournament-form').addEventListener('submit',async event=>{
  event.preventDefault();const form=event.currentTarget,message=form.querySelector('[data-form-message]');
  const registrationDeadline=new Date(form.registrationDeadline.value).getTime(),startAt=new Date(form.startAt.value).getTime();
  try{const created=await api('/api/admin/tournaments',{method:'POST',body:{name:form.name.value.trim(),description:form.description.value.trim(),registrationDeadline,startAt,maxPlayers:Number(form.maxPlayers.value)}});await refreshState();selectedTournamentId=created.id;form.reset();document.querySelector('#tournament-dialog').close();render();showView('tournaments');toast('Турнир создан, регистрация открыта')}
  catch(error){message.textContent=error.message}
});

document.querySelector('#opponent-search').addEventListener('focus',event=>{if(!event.currentTarget.readOnly)renderStudentSuggestions(event.currentTarget.value.includes('·')?'':event.currentTarget.value)});
document.querySelector('#opponent-search').addEventListener('input',event=>{
  if(event.currentTarget.readOnly)return;
  const form=document.querySelector('#match-form'); form.opponent.value=''; document.querySelector('#student-picker').classList.remove('selected'); renderStudentSuggestions(event.currentTarget.value);
});
document.querySelector('#opponent-search').addEventListener('keydown',event=>{
  const box=document.querySelector('#student-suggestions');
  if(event.key==='Escape'){ box.hidden=true; event.currentTarget.setAttribute('aria-expanded','false'); return; }
  const options=[...box.querySelectorAll('.student-option')]; if(!options.length) return;
  const active=box.querySelector('.student-option.active'); let index=options.indexOf(active);
  if(event.key==='ArrowDown'){ event.preventDefault(); index=(index+1)%options.length; }
  else if(event.key==='ArrowUp'){ event.preventDefault(); index=(index-1+options.length)%options.length; }
  else if(event.key==='Enter'&&active){ event.preventDefault(); selectOpponent(active.dataset.student); return; }
  else return;
  options.forEach(option=>option.classList.remove('active')); options[index].classList.add('active'); options[index].scrollIntoView({block:'nearest'});
});

document.querySelector('#dispute-form').addEventListener('submit',async event=>{
  event.preventDefault(); const form=event.currentTarget, match=db.matches.find(m=>m.id===form.matchId.value), me=currentUser(); if(!match||!me)return;
  try { await mutate(`/api/matches/${match.id}/dispute`,{reason:form.reason.value.trim()}); document.querySelector('#dispute-dialog').close();toast('Жалоба отправлена администратору'); }
  catch(error){ form.querySelector('[data-form-message]').textContent=error.message; }
});

window.addEventListener('hashchange',()=>showView((location.hash||'#home').slice(1),false));
let bracketResizeTimer;
window.addEventListener('resize',()=>{ clearTimeout(bracketResizeTimer);bracketResizeTimer=setTimeout(()=>{const tournament=(db.tournaments||[]).find(item=>item.id===selectedTournamentId);if(tournament)drawTournamentConnections(tournament)},120); });
refreshState().then(()=>{render();if(db.authMessage)toast(db.authMessage)}).catch(error=>toast(`Не удалось подключиться к серверу: ${error.message}`));
setInterval(async()=>{ try{await refreshState();render()}catch{} },15000);
