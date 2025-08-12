# -*- coding: utf-8 -*-
"""
App standalone (1 fichier) qui remplace ton Google Apps Script
- Lance un petit serveur web local (FastAPI + Uvicorn)
- Stocke les données en SQLite (fichier "cafeteria.db" à côté de l'exécutable)
- Reprend la logique de ton GAS : inscriptions, liste, caisse, fermeture, quotas, produits
- Pas d'installation sur le PC cible : on fabriquera un exécutable Linux avec GitHub Actions (PyInstaller)

👉 Comment l'utiliser en dev (sur une machine où tu peux tester) :
    pip install fastapi uvicorn sqlmodel jinja2 pydantic typing_extensions python-multipart
    python app.py
    # puis ouvrir http://127.0.0.1:8000/

👉 Sur le PC verrouillé, on passera par l'exécutable (voir workflow fourni plus bas dans ce message)

Notes
- E-mails : si tu veux envoyer le récap par mail à la fermeture, renseigne la variable d'env SMTP_* (facultatif). Sinon, l'app ne tente pas d'envoyer.
- Paramètres (jours/menus/ouvertures) : page /admin (upload CSV) pour importer tes Paramètres et Réservations initiales.
- Quota : 40 inscriptions max (affichage 4×10) et 45 menus servis max (comme dans ton GAS).
- Prix : alignés à ton GAS (Élève 8, Prof 12, Sandwich 6, Boisson 2, Chocolat 1.5). Fond de caisse 150 CHF.
"""
from __future__ import annotations
import os
from pathlib import Path
from datetime import date, datetime, time
from typing import Optional, Dict, List

from fastapi import FastAPI, Request, HTTPException, UploadFile, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import SQLModel, Field, create_engine, Session, select

APP_TITLE = "CAFÉTÉRIA CO FLORENCE"
DB_PATH = Path(__file__).with_name("cafeteria.db")
PRICES = {"ELEVE": 8.0, "PROF": 12.0, "SANDWICH": 6.0, "BOISSON": 2.0, "CHOCOLAT": 1.5}
CASH_FLOAT = 150.0

# ---------- Modèles SQL ----------
class ParamRow(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date_iso: str
    jour: str
    menu: str = ""
    open: bool = False
    disabled: bool = False  # remplace "texte rouge" dans GAS

class Reservation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date_iso: str
    name: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

class TillRow(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date_iso: str
    name: str = ""
    type: str  # Eleve (CASH) / Eleve (CARD) / Prof (...) / Sandwich / Boisson / Chocolat / Closed
    base: float = 0.0
    beverage: float = 0.0
    chocolate: float = 0.0
    total: float = 0.0  # cash reçu pour la ligne
    created_at: datetime = Field(default_factory=datetime.utcnow)

engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, connect_args={"check_same_thread": False})
SQLModel.metadata.create_all(engine)

# ---------- Utils ----------
DAYS = ["Dimanche","Lundi","Mardi","Mercredi","Jeudi","Vendredi","Samedi"]

def today_iso() -> str:
    tz_now = datetime.now()  # PC local
    return date(tz_now.year, tz_now.month, tz_now.day).isoformat()

def pretty_fr_header(iso: str) -> str:
    y, m, d = map(int, iso.split("-"))
    dt = date(y, m, d)
    return f"{DAYS[dt.weekday()+1 if dt.weekday()<6 else 0]} {d:02d}.{m:02d}"

def to_iso_any(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return today_iso()
    # dd.MM.yyyy
    if len(s) == 10 and s[2] == "." and s[5] == ".":
        dd, mm, yyyy = s.split(".")
        return f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d}"
    # yyyy-MM-dd
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    # fallback parse
    try:
        dt = datetime.fromisoformat(s)
        return dt.date().isoformat()
    except Exception:
        return today_iso()

def norm_name(s: str) -> str:
    return " ".join((s or "").strip().upper().split())

# ---------- App & templates ----------
app = FastAPI(title=APP_TITLE)
TEMPLATES_DIR = Path(__file__).with_name("templates")
TEMPLATES_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- Templates (importe tes HTML quasi à l'identique, version fetch() au lieu de google.script.run) ---
PAGE_HTML = r"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"><title>CAFÉTÉRIA CO FLORENCE</title>
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache"><meta http-equiv="Expires" content="0">
  <style>
  {{ css_common|safe }}
  </style>
</head>
<body>
  <h1>CAFÉTÉRIA CO FLORENCE</h1>
  <div id="jours"></div>
  <div id="list" style="display:none;"></div>
  <div id="modalOverlay"><div id="modalBox"></div></div>
  <script>
  // Version fetch() de tes appels GAS
  let availableDays = [], reservationsMap = {};

  function scheduleDailyReload(hour, minute){
    function msUntil(h,m){const now=new Date();const t=new Date(now.getFullYear(),now.getMonth(),now.getDate(),h,m,0,0);if(t<=now)t.setDate(t.getDate()+1);return t-now}
    setTimeout(()=>{location.replace(location.pathname+'?_r='+Date.now())}, msUntil(hour,minute));
  }

  async function loadInitialData(){
    const r = await fetch('/api/initial');
    const data = await r.json();
    availableDays = data.days; reservationsMap = data.reservations; renderHome();
  }

  function renderHome(){
    const joursDiv=document.getElementById('jours'), listDiv=document.getElementById('list');
    listDiv.style.display='none'; joursDiv.style.display='block'; joursDiv.innerHTML='';
    availableDays.forEach(d=>{
      const div=document.createElement('div'); const cls=['jour'];
      if(!d.open)cls.push('closed'); if(d.disabled)cls.push('disabled');
      div.className=cls.join(' ');
      div.innerHTML = '<strong>'+d.jour+'</strong>'+'<span class="date">'+d.date+'</span>'+'<div class="menu">'+(d.menu||'')+'</div>';
      if(d.open && !d.disabled){ div.onclick=()=>showList(d.date); }
      joursDiv.appendChild(div);
    });
  }

  function showList(date){
    const joursDiv=document.getElementById('jours'), listDiv=document.getElementById('list');
    joursDiv.style.display='none'; listDiv.style.display='block'; listDiv.innerHTML='';
    const dayObj = availableDays.find(d=>d.date===date);
    const h2=document.createElement('h2'); h2.textContent='Inscriptions pour '+(dayObj?dayObj.jour+' ':'')+date; listDiv.appendChild(h2);
    if(dayObj){ const menuDiv=document.createElement('div'); menuDiv.className='menu'; menuDiv.textContent=dayObj.menu||''; listDiv.appendChild(menuDiv); }

    const list = (reservationsMap[date]||[]);
    const actions=document.createElement('div'); actions.className='top-actions';
    const backBtn=document.createElement('button'); backBtn.textContent='← Retour'; backBtn.onclick=renderHome; actions.appendChild(backBtn);
    if(list.length<40){ const b=document.createElement('button'); b.textContent="S'inscrire"; b.onclick=()=>openRegisterModal(date); actions.appendChild(b); }
    listDiv.appendChild(actions);

    const container=document.createElement('div'); container.className='participants-container';
    for(let col=0; col<4; col++){
      const colDiv=document.createElement('div'); colDiv.className='participants-column';
      const slice=list.slice(col*10, col*10+10);
      if(!slice.length){ const empty=document.createElement('div'); empty.style.visibility='hidden'; empty.textContent='—'; colDiv.appendChild(empty); }
      slice.forEach(name=>{
        const p=document.createElement('div'); p.className='participant'; p.textContent=name; p.onclick=()=>openUnregisterModal(name,date); colDiv.appendChild(p);
      });
      container.appendChild(colDiv);
    }
    listDiv.appendChild(container);
  }

  function showModal(html, bind){ const ov=document.getElementById('modalOverlay'), box=document.getElementById('modalBox'); box.innerHTML=html; ov.style.display='flex'; bind(); }
  function closeModal(){ document.getElementById('modalOverlay').style.display='none'; }
  function showTemp(msg){ const t=document.createElement('div'); t.textContent=msg; t.style.cssText='position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,0.8);color:#fff;padding:12px 24px;border-radius:6px;font-size:1em;z-index:1001;'; document.body.appendChild(t); setTimeout(()=>{ t.remove(); loadInitialData(); }, 1200); }

  function initKeyboard(inputId){
    const layout=[["1","2","3","4","5","6","7","8","9","0"],["Q","W","E","R","T","Z","U","I","O","P"],["A","S","D","F","G","H","J","K","L","M"],["Y","X","C","V","B","N","-","'","."],["Space","Backspace"]];
    const kb=document.getElementById('virtualKeyboard'); kb.innerHTML='';
    layout.forEach(row=>{ const rowDiv=document.createElement('div'); rowDiv.className='row'; row.forEach(k=>{ const key=document.createElement('div'); key.className='key'+(k==='Space'?' wide':''); key.textContent=(k==='Space'?'⎵':k); key.onclick=()=>{ const inp=document.getElementById(inputId); if(k==='Backspace') inp.value=inp.value.slice(0,-1); else if(k==='Space') inp.value+=' '; else inp.value+=k; }; rowDiv.appendChild(key); }); kb.appendChild(rowDiv); });
  }

  function openRegisterModal(date){
    showModal('<input id="regName" placeholder="Votre nom" readonly><div id="virtualKeyboard"></div><div class="buttons"><button id="regCancel">Annuler</button><button id="regOk">Valider</button></div>', ()=>{
      initKeyboard('regName');
      document.getElementById('regCancel').onclick=closeModal;
      document.getElementById('regOk').onclick=async ()=>{
        const name=document.getElementById('regName').value.trim(); if(!name) return;
        const r=await fetch('/api/reserve', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name, dateStr: date})});
        const msg= await r.text(); showTemp(msg); closeModal();
      };
    });
  }

  function openUnregisterModal(name,date){
    showModal('<h3>Désinscrire '+name+'</h3><p>Le '+date+' ?</p><div class="buttons"><button id="unregCancel">Annuler</button><button id="unregOk">Confirmer</button></div>', ()=>{
      document.getElementById('unregCancel').onclick=closeModal;
      document.getElementById('unregOk').onclick=async ()=>{
        const r=await fetch('/api/unreserve', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name, dateStr: date})});
        const msg=await r.text(); showTemp(msg); closeModal();
      };
    });
  }

  window.addEventListener('load', loadInitialData);
  try{ scheduleDailyReload(7,0); scheduleDailyReload(12,0);}catch(e){}
  </script>
  <div id="footer"><p><strong>PRIX</strong><br>ÉLÈVE: CHF 8.-    ADULTE: CHF 12.-    SANDWICHES: CHF 6.-</p></div>
</body></html>
"""

CAISSE_HTML = r"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Caisse — {{ title }}</title>
  <style>
  {{ css_common|safe }}
  </style></head>
<body>
  <header>
    <h1 id="title">Caisse</h1>
    <div class="actions" id="actions">
      <button id="walkinBtn" class="btn primary">+ Menu spontané</button>
      <button id="sandBtn" class="btn secondary">+ Sandwich (CHF 6.-)</button>
      <button id="bevBtn"  class="btn secondary">+ Boisson (CHF 2.-)</button>
      <button id="chocBtn" class="btn secondary">+ Chocolat (CHF 1.50)</button>
      <button id="closeBtn" class="btn danger">Fermer la caisse</button>
    </div>
  </header>
  <section class="totals" id="totals"></section>
  <section class="names-grid" id="names"></section>

  <!-- Modals -->
  <div id="overlay" class="overlay"><div id="modal" class="modal">
    <h3 id="who"></h3>
    <div class="row" id="nameRow" style="display:none;"><input type="text" id="nameInput" placeholder="Nom (facultatif)"></div>
    <div class="row" role="radiogroup"><label><input type="radio" name="rtype" value="ELEVE" checked> Élève (CHF 8.-)</label><label><input type="radio" name="rtype" value="PROF"> Prof (CHF 12.-)</label></div>
    <div class="row" role="radiogroup"><label><input type="radio" name="rpay" value="CASH" checked> Cash</label><label><input type="radio" name="rpay" value="CARD"> Carte abo (10 repas)</label></div>
    <div class="row"><label><input type="checkbox" id="bev"> Boisson +CHF 2.-</label><label><input type="checkbox" id="choc"> Chocolat +CHF 1.50</label></div>
    <div class="modal-actions"><button class="btn sm" id="cancel">Annuler</button><button class="btn sm primary" id="ok">Valider</button></div>
  </div></div>

  <div id="qtyOverlay" class="overlay"><div id="qtyModal" class="modal">
    <h3 id="qtyTitle">Ajouter</h3>
    <div class="qty-wrap"><div class="qty-display" id="qtyDisplay">1</div><div class="qty-btn" id="plus1">+1</div></div>
    <div class="modal-actions"><button class="btn sm" id="qtyCancel">Annuler</button><button class="btn sm primary" id="qtyOk">Ajouter</button></div>
  </div></div>

  <div id="confirmOverlay" class="overlay"><div id="confirmModal" class="modal">
    <h3>Fermer la caisse ?</h3><p style="margin:.25rem 0 .5rem 0">Un e-mail de comptabilité va être envoyé et la caisse sera verrouillée.</p>
    <div class="row" style="justify-content:center"><button class="btn sm" id="confirmCancel">Annuler</button><button class="btn sm danger" id="confirmOk">Confirmer l'envoi</button></div>
  </div></div>

  <div id="toast"></div>

<script>
  function chf(n){ return (Math.round(n*100)/100).toFixed(2)+' CHF'; }
  function showToast(msg){ const t=document.getElementById('toast'); t.textContent=msg; t.style.display='block'; setTimeout(()=>{ t.style.display='none'; }, 1400); }
  function formatHeaderDate(iso){ if(!iso) return ''; const p=iso.split('-').map(Number); const d=new Date(p[0],p[1]-1,p[2]); const days=['Dimanche','Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi']; return 'Caisse - '+days[d.getDay()]+' '+String(p[2]).padStart(2,'0')+'.'+String(p[1]).padStart(2,'0'); }

  let dateIso=null, currentName=null, isWalkIn=false, qty=1, currentAddType=null;

  async function refresh(){ const r=await fetch('/api/caisse?date='+encodeURIComponent(dateIso||'')); const data=await r.json(); updateFromData(data); }

  function updateFromData(data){ dateIso=data.date||dateIso; document.getElementById('title').textContent = formatHeaderDate(dateIso);
    const T=data.totals||{menus:0,eleves:0,profs:0,sandwiches:0,beverages:0,chocolates:0,amount:0};
    const tdiv=document.getElementById('totals'); tdiv.innerHTML='';
    const items=['Menus: '+T.menus+' (élèves '+(T.eleves||0)+', profs '+(T.profs||0)+')','Sandwiches: '+(T.sandwiches||0),'Boissons: '+(T.beverages||0),'Chocolats: '+(T.chocolates||0),'<strong>Total cash: '+chf(T.amount||0)+'</strong>'];
    items.forEach((txt,i)=>{ const s=document.createElement('span'); s.className='pill'+(i===items.length-1?' total':''); if(i===items.length-1) s.innerHTML=txt; else s.textContent=txt; tdiv.appendChild(s); });

    const list=(data.names||[]).slice(0,40); const box=document.getElementById('names'); box.innerHTML='';
    for(let col=0; col<4; col++){ const colDiv=document.createElement('div'); colDiv.className='col'; const slice=list.slice(col*10, col*10+10);
      for(let i=0;i<10;i++){ const name=slice[i]; const row=document.createElement('div'); row.className='person'+(name?'':' empty'); if(name){ const left=document.createElement('div'); left.className='name'; left.textContent=name; const btn=document.createElement('button'); btn.className='btn sm primary'; btn.textContent='Valider'; btn.onclick=(()=>nm=>()=>openModal(nm))(name)(); row.appendChild(left); row.appendChild(btn);} else { row.textContent='—'; } colDiv.appendChild(row); }
      box.appendChild(colDiv);
    }
  }

  function openModal(name){ isWalkIn=false; currentName=name; document.getElementById('who').textContent=name; document.getElementById('nameRow').style.display='none'; document.getElementById('nameInput').value=''; document.getElementById('bev').checked=false; document.getElementById('choc').checked=false; document.querySelector('input[name="rtype"][value="ELEVE"]').checked=true; document.querySelector('input[name="rpay"][value="CASH"]').checked=true; document.getElementById('overlay').style.display='flex'; }
  function openWalkIn(){ isWalkIn=true; currentName=null; document.getElementById('who').textContent='Menu spontané'; document.getElementById('nameRow').style.display='flex'; document.getElementById('nameInput').value=''; document.getElementById('bev').checked=false; document.getElementById('choc').checked=false; document.querySelector('input[name="rtype"][value="ELEVE"]').checked=true; document.querySelector('input[name="rpay"][value="CASH"]').checked=true; document.getElementById('overlay').style.display='flex'; }
  function closeModal(){ document.getElementById('overlay').style.display='none'; }

  document.getElementById('cancel').onclick = closeModal;
  document.getElementById('ok').onclick = async function(){
    const t=document.querySelector('input[name="rtype"]:checked').value; const pay=document.querySelector('input[name="rpay"]:checked').value; const bev=document.getElementById('bev').checked; const choc=document.getElementById('choc').checked; const name=isWalkIn ? (document.getElementById('nameInput').value.trim()||'Anonyme') : currentName;
    try{ const r=await fetch('/api/checkout', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name, type:t, beverage: bev, chocolate: choc, dateIso, method: pay})}); if(!r.ok){ const t=await r.text(); alert(t); await refresh(); return;} const data=await r.json(); closeModal(); showToast('Validé ✔'); updateFromData(data);}catch(e){ alert('Erreur: '+e); }
  };

  function openQtyModal(kind){ currentAddType=kind; qty=1; document.getElementById('qtyDisplay').textContent=qty; document.getElementById('qtyTitle').textContent = kind==='sand' ? 'Ajouter des Sandwiches' : kind==='bev' ? 'Ajouter des Boissons' : 'Ajouter des Chocolats'; document.getElementById('qtyOverlay').style.display='flex'; }
  function closeQtyModal(){ document.getElementById('qtyOverlay').style.display='none'; }
  document.getElementById('plus1').onclick=()=>{ qty=Math.min(999, qty+1); document.getElementById('qtyDisplay').textContent=qty; };
  document.getElementById('qtyCancel').onclick=closeQtyModal;
  document.getElementById('qtyOk').onclick=async function(){ const f=currentAddType==='sand'?'sandwich': currentAddType==='bev'?'beverage':'chocolate'; try{ const r=await fetch('/api/add/'+f, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({qty, dateIso})}); const data=await r.json(); closeQtyModal(); showToast('+ '+(currentAddType==='sand'?'Sandwich': currentAddType==='bev'?'Boisson':'Chocolat')); updateFromData(data);}catch(e){ alert('Erreur: '+e); }
  };

  function openConfirm(){ document.getElementById('confirmOverlay').style.display='flex'; }
  function closeConfirm(){ document.getElementById('confirmOverlay').style.display='none'; }
  document.getElementById('confirmCancel').onclick=closeConfirm;
  document.getElementById('confirmOk').onclick=async function(){ const r=await fetch('/api/close', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({dateIso})}); const data=await r.json(); closeConfirm(); window.location.replace('/closed'); };

  document.getElementById('walkinBtn').onclick=openWalkIn;
  document.getElementById('sandBtn').onclick=()=>openQtyModal('sand');
  document.getElementById('bevBtn').onclick=()=>openQtyModal('bev');
  document.getElementById('chocBtn').onclick=()=>openQtyModal('choc');
  document.getElementById('closeBtn').onclick=openConfirm;

  window.addEventListener('load', async ()=>{ const params=new URLSearchParams(location.search); dateIso=params.get('date')||''; await refresh(); });
</script>
</body></html>
"""

CLOSED_HTML = r"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Caisse — Fermée</title>
  <style>html{font-size:calc(16px + (24 - 16) * ((100vw - 320px) / (1920 - 320)));}body{margin:0;padding:0;font-family:sans-serif;background:#fff;color:#111;display:flex;align-items:center;justify-content:center;min-height:100vh}.box{text-align:center;max-width:800px;padding:2rem}h1{margin:0 0 .5rem 0;font-size:2rem}p{margin:.25rem 0;font-size:1.25rem}</style>
</head><body><div class="box"><h1>La caisse est désormais fermée</h1><p>La comptabilité a été transmise.</p></div></body></html>"""

CSS_COMMON = r"""
  body{margin:0;padding:0;font-family:sans-serif;text-align:center}
  h1{font-size:2em;color:red;margin:1em 0 .25em}
  h2{font-size:1.4em;margin:1em 0 .5em}
  .jour{display:inline-block;vertical-align:top;width:15vw;margin:1vw;padding:1.5vw;border:.2vw solid #ccc;border-radius:1vw;cursor:pointer;transition:opacity .3s;font-size:1.1em;color:inherit}
  .jour.closed{opacity:.4;cursor:not-allowed}
  .jour.disabled{border-color:red;color:red}
  .jour strong{display:block;font-size:1.2em;margin-bottom:.5vw}
  .jour .date{display:block;font-weight:bold;margin-bottom:1vw}
  .menu{white-space:pre-line;margin-top:1vw;font-size:1em}
  .top-actions{margin:2vw 0;display:flex;justify-content:center;gap:2vw}
  .top-actions button{padding:1vw 2vw;font-size:1.1em;font-weight:bold;background:#007bff;color:#fff;border:none;border-radius:1vw;cursor:pointer}
  .participants-container{display:flex;justify-content:center;gap:1vw;margin:2vw auto;max-width:80vw}
  .participants-column{flex:1;display:flex;flex-direction:column;gap:.25vw}
  .participant{padding:.3vw 1vw;font-size:.9em;border:.1vw solid #ccc;border-radius:1vw;cursor:pointer;text-align:center}
  #modalOverlay{position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);display:none;align-items:center;justify-content:center;z-index:1000}
  #modalBox{display:inline-block;background:#fff;padding:2vw;border-radius:1vw;box-shadow:0 .5vw 2vw rgba(0,0,0,.3);max-width:95vw;max-height:95vh;overflow:auto;text-align:center;box-sizing:border-box}
  #modalBox input{width:100%;padding:1vw;font-size:1em;margin-bottom:1vw;box-sizing:border-box}
  #virtualKeyboard{display:flex;flex-direction:column;gap:1vw;margin-bottom:1vw}
  #virtualKeyboard .row{display:flex;justify-content:center;gap:1vw}
  #virtualKeyboard .key{flex:1;min-width:8vw;padding:2vw 0;background:#e0e0e0;border-radius:1vw;text-align:center;font-size:1.5em;cursor:pointer;user-select:none}
  #virtualKeyboard .key.wide{flex:4}
  #footer{position:fixed;bottom:.5vw;left:0;width:100%;text-align:center;font-size:1em;color:red;padding:.2vw 0;background:rgba(255,255,255,.8)}

  header{position:sticky;top:0;z-index:10;display:flex;align-items:center;justify-content:space-between;gap:.75rem;padding:.75rem 1.25rem;background:#f7f7f7;border-bottom:1px solid #e5e5e5}
  header h1{margin:0;font-size:1.35em}
  .btn{border:0;border-radius:12px;padding:.5rem .75rem;cursor:pointer;font-size:.95rem}
  .btn.sm{padding:.35rem .6rem;font-size:.9rem;border-radius:10px}
  .primary{background:#007bff;color:#fff}
  .secondary{background:#e0e0e0}
  .danger{background:#dc3545;color:#fff}
  .actions{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
  .totals{display:flex;flex-wrap:wrap;gap:.4rem;padding:8px 12px;align-items:center}
  .pill{border:1px solid #ddd;border-radius:999px;padding:.25rem .6rem;font-size:.9rem;background:#fff}
  .pill.total{font-weight:700}
  .names-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:8px 12px}
  .col{display:flex;flex-direction:column;gap:6px}
  .person{display:flex;align-items:center;justify-content:space-between;padding:6px 8px;border:1px solid #ddd;border-radius:10px;background:#fff}
  .name{font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.95rem;padding-right:8px}
  .person.empty{visibility:hidden}
  .overlay{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:1000}
  .modal{background:#fff;border-radius:12px;width:min(92vw,560px);padding:18px;box-shadow:0 10px 30px rgba(0,0,0,.2)}
  .modal h3{margin:0 0 8px 0;font-size:1.05rem}
  .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:8px 0}
  .row label{display:flex;gap:8px;align-items:center;cursor:pointer;border:1px solid #ddd;border-radius:10px;padding:8px 10px;background:#fafafa;font-size:.95rem}
  .row input[type=radio],.row input[type=checkbox]{transform:scale(1.15)}
  .modal-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:12px}
  .modal input[type=text]{width:100%;padding:10px 12px;border:1px solid #ccc;border-radius:10px;font-size:1rem}
  .qty-wrap{display:flex;gap:12px;align-items:center;justify-content:center;margin:12px 0}
  .qty-btn{width:88px;height:64px;border-radius:12px;border:1px solid #ccc;background:#f1f1f1;font-size:1.6rem;font-weight:700;cursor:pointer;user-select:none;display:flex;align-items:center;justify-content:center}
  .qty-display{min-width:100px;text-align:center;font-size:1.9rem;font-weight:800;border:1px solid #ddd;border-radius:12px;padding:6px 12px;background:#fff}
  #toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,.85);color:#fff;padding:10px 14px;border-radius:10px;font-size:.95rem;z-index:1100;display:none}
"""

# Écrit les templates sur disque (utile pour PyInstaller --add-data)
(TEMPLATES_DIR / "page.html").write_text(PAGE_HTML, encoding="utf-8")
(TEMPLATES_DIR / "caisse.html").write_text(CAISSE_HTML, encoding="utf-8")
(TEMPLATES_DIR / "closed.html").write_text(CLOSED_HTML, encoding="utf-8")

# ---------- Routes pages ----------
@app.get("/", response_class=HTMLResponse)
def home(req: Request):
    return templates.TemplateResponse("page.html", {"request": req, "css_common": CSS_COMMON})

@app.get("/caisse", response_class=HTMLResponse)
def caisse(req: Request, date: Optional[str] = None):
    return templates.TemplateResponse("caisse.html", {"request": req, "title": APP_TITLE, "css_common": CSS_COMMON})

@app.get("/closed", response_class=HTMLResponse)
def closed(req: Request):
    return templates.TemplateResponse("closed.html", {"request": req})

# ---------- API (équivalents GAS) ----------
class ReserveIn(BaseModel):
    name: str
    dateStr: str

class UnreserveIn(BaseModel):
    name: str
    dateStr: str

class CheckoutIn(BaseModel):
    name: str
    type: str
    beverage: bool = False
    chocolate: bool = False
    dateIso: Optional[str] = None
    method: str = "CASH"  # CASH | CARD

class QtyIn(BaseModel):
    qty: int
    dateIso: Optional[str] = None

@app.get("/api/initial")
def api_initial():
    """Retourne {days:[{date:'dd.MM.yyyy', jour, menu, open, disabled}], reservations: { 'dd.MM.yyyy': [names...] }}"""
    tz_today = date.today()
    wanted = [
        {"nom": "Lundi", "dow": 1},
        {"nom": "Mardi", "dow": 2},
        {"nom": "Jeudi", "dow": 4},
        {"nom": "Vendredi", "dow": 5},
    ]
    with Session(engine) as s:
        rows = s.exec(select(ParamRow)).all()
        # tri date
        rows.sort(key=lambda r: r.date_iso)
        # map pour la réservation
        all_res = s.exec(select(Reservation)).all()
    # Résas par dd.MM.yyyy
    reservations_map: Dict[str, List[str]] = {}
    for r in all_res:
        y, m, d = map(int, r.date_iso.split("-"))
        key = f"{d:02d}.{m:02d}.{y:04d}"
        reservations_map.setdefault(key, []).append(r.name)

    def first_open_for(day_name: str):
        candidates = [r for r in rows if r.jour == day_name and r.open and date.fromisoformat(r.date_iso) >= tz_today]
        candidates.sort(key=lambda r: r.date_iso)
        if candidates:
            e = candidates[0]
            y, m, d = map(int, e.date_iso.split("-"))
            return {"date": f"{d:02d}.{m:02d}.{y:04d}", "jour": e.jour, "menu": e.menu, "open": True, "disabled": e.disabled}
        # sinon prochain même jour de la semaine à venir (fermé)
        return {"date": next_weekday_str(tz_today, day_name), "jour": day_name, "menu": "", "open": False, "disabled": False}

    days_out = [first_open_for(w["nom"]) for w in wanted]
    return {"days": days_out, "reservations": reservations_map}

def next_weekday_str(today: date, day_name: str) -> str:
    mapping = {"Lundi": 0, "Mardi": 1, "Mercredi": 2, "Jeudi": 3, "Vendredi": 4, "Samedi": 5, "Dimanche": 6}
    target = mapping.get(day_name, 0)
    delta = (target - today.weekday()) % 7 or 7
    d = today.toordinal() + delta
    dt = date.fromordinal(d)
    return f"{dt.day:02d}.{dt.month:02d}.{dt.year:04d}"

@app.post("/api/reserve", response_class=PlainTextResponse)
def api_reserve(inp: ReserveIn):
    # inp.dateStr = dd.MM.yyyy
    iso = to_iso_any(inp.dateStr)
    with Session(engine) as s:
        # journée ouverte ?
        p = s.exec(select(ParamRow).where(ParamRow.date_iso == iso, ParamRow.open == True)).first()
        if not p:
            raise HTTPException(400, detail=f"Le {inp.dateStr} est fermé, impossible de réserver.")
        # quota 40
        cnt = s.exec(select(Reservation).where(Reservation.date_iso == iso)).all()
        if len(cnt) >= 40:
            raise HTTPException(400, detail=f"Quota de 40 atteint pour le {inp.dateStr}.")
        s.add(Reservation(date_iso=iso, name=inp.name.strip()))
        s.commit()
    return f"Merci {inp.name}, réservation confirmée pour le {inp.dateStr} !"

@app.post("/api/unreserve", response_class=PlainTextResponse)
def api_unreserve(inp: UnreserveIn):
    iso = to_iso_any(inp.dateStr)
    target = (inp.name or "").strip()
    with Session(engine) as s:
        rows = s.exec(select(Reservation).where(Reservation.date_iso == iso)).all()
        for r in rows:
            if (r.name or "").strip() == target:
                s.delete(r); s.commit();
                return f"Vous êtes désinscrit pour le {inp.dateStr}."
    raise HTTPException(400, detail=f"Pas de réservation trouvée pour \"{target}\" le {inp.dateStr}.")

# ---- CAISSE helpers ----
class Totals(BaseModel):
    menus: int = 0
    eleves: int = 0
    profs: int = 0
    sandwiches: int = 0
    beverages: int = 0
    chocolates: int = 0
    amount: float = 0.0

class CaisseOut(BaseModel):
    date: str
    closed: bool
    names: List[str]
    totals: Totals

def is_closed(iso: str) -> bool:
    with Session(engine) as s:
        x = s.exec(select(TillRow).where(TillRow.date_iso == iso, TillRow.type == "Closed")).first()
        return bool(x)

def build_totals(iso: str):
    t = Totals()
    paidCount: Dict[str, int] = {}
    with Session(engine) as s:
        rows = s.exec(select(TillRow).where(TillRow.date_iso == iso)).all()
    for row in rows:
        typ = (row.type or "")
        if typ == "Closed":
            continue
        elif typ == "Sandwich":
            t.sandwiches += 1
        elif typ == "Boisson":
            t.beverages += 1
        elif typ == "Chocolat":
            t.chocolates += 1
        else:
            t.menus += 1
            if "eleve" in typ.lower(): t.eleves += 1
            else: t.profs += 1
            key = norm_name(row.name)
            if key: paidCount[key] = paidCount.get(key, 0) + 1
            if row.beverage > 0: t.beverages += 1
            if row.chocolate > 0: t.chocolates += 1
        t.amount += float(row.total)
    return t, paidCount

@app.get("/api/caisse")
def api_caisse(date: Optional[str] = None):
    iso = date if (date and len(date)==10) else today_iso()
    if is_closed(iso):
        t,_ = build_totals(iso)
        return CaisseOut(date=iso, closed=True, names=[], totals=t)
    # ordre d'inscription
    with Session(engine) as s:
        ordered = s.exec(select(Reservation).where(Reservation.date_iso == iso).order_by(Reservation.id)).all()
    t, paidCount = build_totals(iso)
    remaining: List[str] = []
    paid_left = dict(paidCount)
    for r in ordered:
        k = norm_name(r.name)
        if paid_left.get(k, 0) > 0:
            paid_left[k] -= 1
        else:
            remaining.append(r.name)
    return CaisseOut(date=iso, closed=False, names=remaining, totals=t)

# contraintes
MAX_MENUS = 45

def assert_open(iso: str):
    if is_closed(iso):
        raise HTTPException(400, f"Caisse fermée pour {iso}.")

def assert_capacity(iso: str):
    t,_ = build_totals(iso)
    if t.menus >= MAX_MENUS:
        raise HTTPException(400, f"Limite de 45 menus servis atteinte pour {pretty_fr_header(iso)}.")

@app.post("/api/checkout")
def api_checkout(inp: CheckoutIn):
    iso = inp.dateIso or today_iso()
    assert_open(iso)
    assert_capacity(iso)
    typ = (inp.type or "PROF").upper()
    method = (inp.method or "CASH").upper()  # CASH | CARD
    base = PRICES["ELEVE"] if typ == "ELEVE" else PRICES["PROF"]
    bev = PRICES["BOISSON"] if inp.beverage else 0.0
    choc = PRICES["CHOCOLAT"] if inp.chocolate else 0.0
    total_cash = (bev + choc) if method == "CARD" else (base + bev + choc)
    type_label = ("Eleve" if typ == "ELEVE" else "Prof") + (" (CARD)" if method == "CARD" else " (CASH)")
    with Session(engine) as s:
        s.add(TillRow(date_iso=iso, name=inp.name.strip() or "Anonyme", type=type_label, base=base, beverage=bev, chocolate=choc, total=total_cash))
        s.commit()
    return api_caisse(iso)

@app.post("/api/add/sandwich")
def api_add_sandwich(inp: QtyIn):
    iso = inp.dateIso or today_iso()
    assert_open(iso)
    n = max(1, int(inp.qty or 1))
    with Session(engine) as s:
        for _ in range(n):
            s.add(TillRow(date_iso=iso, type="Sandwich", base=PRICES["SANDWICH"], total=PRICES["SANDWICH"]))
        s.commit()
    return api_caisse(iso)

@app.post("/api/add/beverage")
def api_add_beverage(inp: QtyIn):
    iso = inp.dateIso or today_iso()
    assert_open(iso)
    n = max(1, int(inp.qty or 1))
    with Session(engine) as s:
        for _ in range(n):
            s.add(TillRow(date_iso=iso, type="Boisson", beverage=PRICES["BOISSON"], total=PRICES["BOISSON"]))
        s.commit()
    return api_caisse(iso)

@app.post("/api/add/chocolate")
def api_add_chocolate(inp: QtyIn):
    iso = inp.dateIso or today_iso()
    assert_open(iso)
    n = max(1, int(inp.qty or 1))
    with Session(engine) as s:
        for _ in range(n):
            s.add(TillRow(date_iso=iso, type="Chocolat", chocolate=PRICES["CHOCOLAT"], total=PRICES["CHOCOLAT"]))
        s.commit()
    return api_caisse(iso)

class CloseIn(BaseModel):
    dateIso: Optional[str] = None

@app.post("/api/close")
def api_close(inp: CloseIn):
    iso = inp.dateIso or today_iso()
    with Session(engine) as s:
        # envoyer mail si config SMTP présente (facultatif)
        t,_ = build_totals(iso)
        try:
            smtp_to = os.environ.get("SMTP_TO")
            if smtp_to:
                send_summary_mail(iso, t)
        except Exception:
            pass
        # marquer fermeture
        s.add(TillRow(date_iso=iso, type="Closed"))
        s.commit()
    return {"ok": True}

# ---------- Import CSV (Paramètres/Réservations) ----------
@app.get("/admin", response_class=HTMLResponse)
def admin(req: Request):
    html = """
    <h2>Import CSV</h2>
    <p>Importe <code>Paramètres</code> (date_iso;jour;menu;open;disabled) et <code>Réservations</code> (date_iso;name)</p>
    <form method="post" enctype="multipart/form-data" action="/admin/import">
      <p><input type="file" name="params"> Paramètres.csv</p>
      <p><input type="file" name="resas"> Réservations.csv</p>
      <p><button type="submit">Importer</button></p>
    </form>
    <p><a href="/">← Page d'inscription</a> — <a href="/caisse">Caisse</a></p>
    """
    return HTMLResponse(html)

@app.post("/admin/import")
def admin_import(params: Optional[UploadFile] = None, resas: Optional[UploadFile] = None):
    import csv, io
    with Session(engine) as s:
        if params and params.filename:
            text = params.file.read().decode("utf-8")
            r = csv.DictReader(io.StringIO(text), delimiter=';')
            s.exec(SQLModel.__table__.delete().where(False))  # no-op to keep import symmetrical
            for row in r:
                s.add(ParamRow(date_iso=row['date_iso'], jour=row['jour'], menu=row.get('menu',''), open=row.get('open','').lower() in ('1','true','vrai','yes'), disabled=row.get('disabled','').lower() in ('1','true','vrai','yes')))
        if resas and resas.filename:
            text = resas.file.read().decode("utf-8")
            r = csv.DictReader(io.StringIO(text), delimiter=';')
            for row in r:
                s.add(Reservation(date_iso=row['date_iso'], name=row['name']))
        s.commit()
    return RedirectResponse("/admin", status_code=303)

# ---------- Mail (facultatif) ----------
def send_summary_mail(iso: str, t: Totals):
    import smtplib
    from email.mime.text import MIMEText
    to_addr = os.environ.get("SMTP_TO")
    if not to_addr:
        return
    body = [
        f"Date : {pretty_fr_header(iso)}",
        "",
        f"Menus : {t.menus} (élèves {t.eleves}, profs {t.profs})",
        f"Sandwiches : {t.sandwiches}",
        f"Boissons : {t.beverages}",
        f"Chocolats : {t.chocolates}",
        "",
        f"Fond de caisse initial : {CASH_FLOAT:.2f} CHF",
        f"Encaissements cash : {t.amount:.2f} CHF",
        f"Total en caisse attendu : {(CASH_FLOAT + t.amount):.2f} CHF",
    ]
    msg = MIMEText("\n".join(body))
    msg['Subject'] = f"Comptabilité cafétéria — {pretty_fr_header(iso)}"
    msg['From'] = os.environ.get("SMTP_FROM", "cafeteria@local")
    msg['To'] = to_addr
    host = os.environ.get("SMTP_HOST", "localhost")
    port = int(os.environ.get("SMTP_PORT", "25"))
    with smtplib.SMTP(host, port) as s:
        if os.environ.get("SMTP_STARTTLS"):
            s.starttls()
        user = os.environ.get("SMTP_USER"); pwd = os.environ.get("SMTP_PASS")
        if user and pwd:
            s.login(user, pwd)
        s.send_message(msg)

# ---------- Dev server ----------
if __name__ == "__main__":
    import uvicorn
    # écrit CSS commun dans le contexte
    templates.env.globals['css_common'] = CSS_COMMON
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
