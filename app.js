const ELO_START = 1000;
const ELO_K = 24;

let db = {users:[],matches:[],requests:[],currentUserId:null};
let ratingCache = {};
let opponentOptions = [];

async function api(path,options={}){
  const method=options.method||'GET', headers=options.body?{'Content-Type':'application/json'}:{};
  if(!['GET','HEAD','OPTIONS'].includes(method)&&db.csrfToken) headers['X-CSRF-Token']=db.csrfToken;
  const response=await fetch(path,{method,headers,body:options.body?JSON.stringify(options.body):undefined,credentials:'same-origin'});
  const data=await response.json().catch(()=>({})); if(!response.ok) throw new Error(data.error||'Ошибка сервера.'); return data;
}
async function refreshState(){ db=await api('/api/state'); ratingCache=calculateRatings(); }
async function mutate(path,body){ await api(path,{method:'POST',body}); await refreshState(); render(); }
function currentUser(){ return db.users.find(user=>user.id===db.currentUserId) || null; }
function userById(id){ return db.users.find(user=>user.id===id); }
function displayName(user){ return user ? `${user.firstName} ${user.lastName}` : 'Неизвестный игрок'; }
function publicName(user){ return escapeHtml(displayName(user)); }
function visibleName(user){ return displayName(user); }
function initials(user){ return user ? `${user.firstName[0]}${user.lastName[0]}` : '?'; }
function dateKey(timestamp){ const d=new Date(timestamp); return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; }
function formatDate(timestamp){ return new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'short',year:new Date(timestamp).getFullYear()!==new Date().getFullYear()?'numeric':undefined}).format(timestamp); }
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
  renderAccount(); renderHome(); renderNotifications(); renderAdmin();
  const requested=location.pathname.startsWith('/confirm/')?'home':(location.hash||'#home').slice(1);
  showView(requested,false);
  if(location.pathname.startsWith('/confirm/')) setTimeout(()=>handleConfirmationLink(location.pathname.split('/').pop()),0);
}

function renderAccount(){
  const me=currentUser();
  document.querySelector('#admin-nav').hidden=me?.role!=='admin';
  const area=document.querySelector('#account-area');
  if(!me){ area.innerHTML='<button class="login-button" data-action="open-auth">Войти</button>'; return; }
  area.innerHTML=`<button class="account-button" data-profile="${me.id}" title="Открыть профиль"><span class="account-avatar">${escapeHtml(initials(me))}</span><span class="account-name">${escapeHtml(me.firstName)}<small>${me.status==='pending'?'Ожидает подтверждения':me.role==='admin'?'Администратор':me.role==='teacher'?'Учитель':escapeHtml(me.className)}</small></span></button><button class="text-action" data-action="logout">Выйти</button>`;
}

function renderHome(){
  const players=rankedPlayers(); const leader=players[0]; const matches=db.matches.filter(m=>m.active).sort((a,b)=>b.createdAt-a.createdAt);
  const adminMode=currentUser()?.role==='admin';
  document.querySelector('#simple-home').hidden=adminMode;
  document.querySelector('#admin-home').hidden=!adminMode;
  document.querySelector('#simple-rating').innerHTML=players.map((player,index)=>`<tr><td><span class="rank-number">${index+1}</span></td><td><button class="player-link" data-profile="${player.id}">${publicName(player)}</button></td><td>${player.games}</td><td>${player.wins}</td><td>${player.losses}</td><td><span class="rating-number">${player.rating}</span></td></tr>`).join('')||'<tr><td colspan="6" class="empty">Игроков пока нет</td></tr>';
  document.querySelector('#summary-players').textContent=players.length;
  document.querySelector('#summary-matches').textContent=matches.length;
  document.querySelector('#summary-rating').textContent=leader?.rating ?? ELO_START;
  document.querySelector('#leader-card').innerHTML=leader?`<span class="leader-label">ЛИДЕР РЕЙТИНГА</span><div class="leader-person"><strong>${publicName(leader)}</strong><span>${leader.wins} побед</span></div><div class="leader-value">${leader.rating}<small>Elo</small></div>`:'<div class="empty">Игроков пока нет</div>';
  document.querySelector('#home-rating').innerHTML=players.map((p,i)=>`<div class="compact-row"><span class="compact-rank">${i+1}</span><span><button class="player-link" data-profile="${p.id}">${publicName(p)}</button><span class="player-sub">${p.wins} побед</span></span><span class="compact-rating">${p.rating}</span></div>`).join('') || '<div class="empty">Игроков пока нет</div>';
  document.querySelector('#home-matches').innerHTML=matches.map(match=>matchMiniHtml(match)).join('') || '<div class="empty">Матчей пока нет</div>';
}

function matchMiniHtml(match){
  const a=userById(match.playerOne), b=userById(match.playerTwo);
  return `<div class="recent-match"><div class="recent-date">${formatDate(match.createdAt)}</div><div class="recent-score"><span>${publicName(a)} — ${publicName(b)}</span><strong>${match.scoreOne}:${match.scoreTwo}</strong></div><div class="recent-witness">Результат подтверждён обоими игроками · ±${match._delta||0} Elo</div></div>`;
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
  const pending=db.users.filter(u=>u.status==='pending'), disputes=db.matches.filter(m=>m.active&&m.dispute), active=db.users.filter(u=>u.status==='active'&&u.isPlayer);
  document.querySelector('#admin-stats').innerHTML=`<div class="admin-stat"><strong>${pending.length}</strong><span>заявок ожидают решения</span></div><div class="admin-stat"><strong>${disputes.length}</strong><span>жалоб требуют проверки</span></div><div class="admin-stat"><strong>${active.length}</strong><span>активных игроков</span></div>`;
  document.querySelector('#pending-users').innerHTML=pending.map(u=>`<div class="request-row"><div><div class="request-name">${escapeHtml(u.firstName)} ${escapeHtml(u.lastName)}</div><div class="request-meta">${escapeHtml(u.className)} класс · @${escapeHtml(u.login)}</div></div><div class="row-actions"><button class="button small primary" data-approve="${u.id}">Подтвердить</button><button class="button small danger" data-reject="${u.id}">Отклонить</button></div></div>`).join('')||'<div class="empty">Новых заявок нет</div>';
  document.querySelector('#disputes').innerHTML=disputes.map(m=>{const a=userById(m.playerOne),b=userById(m.playerTwo),reporter=userById(m.dispute.userId);return `<div class="request-row"><div><div class="request-name">${publicName(a)} ${m.scoreOne}:${m.scoreTwo} ${publicName(b)}</div><div class="request-meta">${publicName(reporter)}: ${escapeHtml(m.dispute.reason)}</div></div><div class="row-actions"><button class="button small secondary" data-resolve="${m.id}">Оставить</button><button class="button small danger" data-cancel-match="${m.id}">Отменить матч</button></div></div>`}).join('')||'<div class="empty">Жалоб нет</div>';
  document.querySelector('#users-body').innerHTML=db.users.map(u=>`<tr><td>${escapeHtml(u.firstName)} ${escapeHtml(u.lastName)}${u.className?` · ${escapeHtml(u.className)}`:''}</td><td>${u.login?`@${escapeHtml(u.login)}`:'ЛК Силаэдра'}</td><td><span class="state ${u.status}">${statusLabel(u.status)}</span></td><td>${u.role==='admin'?'Администратор':u.role==='teacher'?'Учитель':'Ученик'}</td><td>${u.role!=='admin'&&u.status!=='pending'?`<button class="button small secondary" data-toggle-user="${u.id}">${u.status==='active'?'Сделать неактивным':'Активировать'}</button>`:''}</td></tr>`).join('');
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
  if(updateHash) history.pushState(null,'',`#${name}`); window.scrollTo({top:0,behavior:'smooth'});
}

function openAuth(){
  const button=document.querySelector('#oidc-login-button'),message=document.querySelector('#oidc-message');
  button.classList.toggle('disabled',!db.oidcEnabled); button.setAttribute('aria-disabled',String(!db.oidcEnabled));
  message.textContent=db.oidcEnabled?'':'Школьный вход будет доступен после настройки OIDC-клиента.';
  document.querySelector('#auth-dialog').showModal();
}
function openMatchForm(){
  const me=currentUser();
  if(!me){ openAuth(); return; }
  if(me.status!=='active'){ toast('Сначала дождитесь подтверждения администратора'); return; }
  if(!me.isPlayer){ toast('Администратор без профиля игрока не может подать результат'); return; }
  const players=rankedPlayers().filter(p=>p.id!==me.id); const form=document.querySelector('#match-form');
  opponentOptions=players; form.reset(); form.opponent.value=''; form.scoreRequester.value=11; form.scoreOpponent.value=7;
  document.querySelector('#opponent-search').value=''; document.querySelector('#student-picker').classList.remove('selected');
  document.querySelector('#student-suggestions').hidden=true; document.querySelector('#opponent-search').setAttribute('aria-expanded','false');
  document.querySelector('#requester-name').textContent=displayName(me);
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
  const button=document.querySelector('#send-notification'); button.dataset.requestId=request.id; button.disabled=request.notified; button.textContent=request.notified?'Уведомление отправлено':'Отправить уведомление сопернику';
  document.querySelector('#notification-status').textContent=request.notified?'Соперник увидит заявку при входе в аккаунт.':'Можно использовать любой из двух способов подтверждения.';
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
  try { await mutate(`/api/requests/${request.id}/accept`,{}); document.querySelector('#confirm-dialog').close(); history.replaceState(null,'/','#home'); toast('Результат подтверждён, рейтинг обновлён'); }
  catch(error){ toast(error.message); }
}
async function rejectRequest(request){
  const me=currentUser(); if(!request||request.status!=='pending'||me?.id!==request.opponent) return;
  try { await mutate(`/api/requests/${request.id}/reject`,{}); if(document.querySelector('#confirm-dialog').open) document.querySelector('#confirm-dialog').close(); history.replaceState(null,'/','#home'); toast('Заявка отклонена'); }
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
  document.querySelector('#profile-content').innerHTML=`<div class="profile-header"><span class="profile-avatar">${escapeHtml(initials(user))}</span><div><h2>${escapeHtml(visibleName(user))}</h2>${user.status==='inactive'?'<p>Неактивный игрок</p>':''}</div></div><div class="profile-stats"><div class="profile-stat"><strong>${stats.rating}</strong><span>Elo</span></div><div class="profile-stat"><strong>${place||'—'}</strong><span>Место</span></div><div class="profile-stat"><strong>${stats.wins}/${stats.losses}</strong><span>Победы / поражения</span></div><div class="profile-stat"><strong>${rate}%</strong><span>Побед</span></div></div><div class="profile-history"><h3>Последние матчи</h3>${games.slice(0,6).map(m=>{const opponent=userById(m.playerOne===id?m.playerTwo:m.playerOne), own=m.playerOne===id?m.scoreOne:m.scoreTwo,other=m.playerOne===id?m.scoreTwo:m.scoreOne;return `<div class="profile-game"><span>${formatDate(m.createdAt)} · ${publicName(opponent)}</span><span>${own}:${other}</span></div>`}).join('')||'<div class="empty">Матчей пока нет</div>'}</div>`;
  document.querySelector('#profile-dialog').showModal();
}

function toast(message){ const el=document.querySelector('#toast'); el.textContent=message; el.classList.add('show'); clearTimeout(toast.timer); toast.timer=setTimeout(()=>el.classList.remove('show'),2500); }

document.addEventListener('click',async event=>{
  const student=event.target.closest('[data-student]'); if(student){ selectOpponent(student.dataset.student); return; }
  const route=event.target.closest('[data-route]'); if(route){ event.preventDefault(); showView(route.dataset.route); return; }
  if(event.target.closest('[data-action="open-auth"]')) openAuth();
  if(event.target.closest('[data-action="logout"]')){ try{if(db.oidcSession){const result=await api('/auth/silaeder/logout',{method:'POST',body:{}});location.href=result.redirect}else{await mutate('/api/logout',{});toast('Вы вышли из аккаунта')}}catch(error){toast(error.message)} }
  if(event.target.closest('[data-action="add-match"]')) openMatchForm();
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
  try { await mutate(`/api/requests/${request.id}/notify`,{}); event.currentTarget.disabled=true; event.currentTarget.textContent='Уведомление отправлено'; document.querySelector('#notification-status').textContent='Соперник увидит заявку при входе в аккаунт.'; toast('Уведомление отправлено сопернику'); }
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
  try { const created=await api('/api/requests',{method:'POST',body:{opponent,scoreRequester:s1,scoreOpponent:s2}}); await refreshState(); const result=requestById(created.id); document.querySelector('#match-dialog').close();render();showQr(result);toast('Заявка создана'); }
  catch(error){ message.textContent=error.message; }
});

document.querySelector('#opponent-search').addEventListener('focus',event=>renderStudentSuggestions(event.currentTarget.value.includes('·')?'':event.currentTarget.value));
document.querySelector('#opponent-search').addEventListener('input',event=>{
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
document.querySelectorAll('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close()}));

refreshState().then(()=>{render();if(db.authMessage)toast(db.authMessage)}).catch(error=>toast(`Не удалось подключиться к серверу: ${error.message}`));
setInterval(async()=>{ try{await refreshState();render()}catch{} },15000);
