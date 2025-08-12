// ----- Code.gs -----
// 1) Configuration
var SPREADSHEET_ID = '1LkCQH5mK_Gc9JIfd6vxsDXphgwrrgT8mo91SzW6ib3E';
var SS             = SpreadsheetApp.openById(SPREADSHEET_ID);
var SHEET_PARAMS   = SS.getSheetByName('Paramètres');
var SHEET_RESAS    = SS.getSheetByName('Réservations');

// URL /exec de la Web App (utilisée dans les e-mails pour le lien Caisse)
var WEBAPP_URL     = 'https://script.google.com/macros/s/AKfycbxT7RSfN3AsU6McW7Jeay5Ynvrc1Uurke6TbhZ13VO1ULzAaoihV1qVkzydqiRHvGMZ/exec';

// 2) Tarifs & fond de caisse
var PRICES     = { ELEVE: 8, PROF: 12, SANDWICH: 6, BOISSON: 2, CHOCOLAT: 1.5 };
var CASH_FLOAT = 150; // fond de caisse

// ---------- Helpers ----------
function getWebAppUrl_() {
  if (WEBAPP_URL) return WEBAPP_URL;
  try { return ScriptApp.getService().getUrl(); } catch (e) { return ''; }
}
function getTodayIso_() {
  var tz = 'Europe/Zurich', d = new Date();
  return Utilities.formatDate(new Date(d.getFullYear(), d.getMonth(), d.getDate()), tz, 'yyyy-MM-dd');
}
function prettyFrHeader_(iso) { // "Lundi 11.08"
  var p = (iso||'').split('-').map(Number);
  if (p.length !== 3) return iso || '';
  var d = new Date(p[0], p[1]-1, p[2]);
  var days = ['Dimanche','Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi'];
  var dd = String(p[2]).padStart(2,'0');
  var mm = String(p[1]).padStart(2,'0');
  return days[d.getDay()] + ' ' + dd + '.' + mm;
}
function normName_(s) { return (s||'').toString().trim().replace(/\s+/g,' ').toUpperCase(); }
function toIso_(val, tz) {
  if (val instanceof Date) {
    return Utilities.formatDate(new Date(val.getFullYear(), val.getMonth(), val.getDate()), tz, 'yyyy-MM-dd');
  }
  var s = (val||'').toString().trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  var d = new Date(s);
  if (!isNaN(d.getTime())) return Utilities.formatDate(new Date(d.getFullYear(), d.getMonth(), d.getDate()), tz, 'yyyy-MM-dd');
  return '';
}
function getOrCreateCaisseSheet_() {
  var sh = SS.getSheetByName('Caisse');
  if (!sh) {
    sh = SS.insertSheet('Caisse');
    sh.appendRow(['date','nom','type','base','boisson','chocolat','total','timestamp']);
  }
  return sh;
}
function isCaisseClosed_(targetIso) {
  var tz = 'Europe/Zurich';
  var data = getOrCreateCaisseSheet_().getDataRange().getValues();
  for (var i = 1; i < data.length; i++) {
    if (toIso_(data[i][0], tz) === targetIso && (data[i][2]+'') === 'Closed') return true;
  }
  return false;
}

// 3) Router : Page / Caisse / Closed
function doGet(e) {
  var page = e && e.parameter && e.parameter.page;
  if (page === 'caisse') {
    var iso = (e.parameter && /^\d{4}-\d{2}-\d{2}$/.test(e.parameter.date)) ? e.parameter.date : getTodayIso_();
    if (isCaisseClosed_(iso)) page = 'closed';
  }
  var tpl = page === 'caisse' ? 'Caisse' : (page === 'closed' ? 'Closed' : 'Page');
  return HtmlService.createTemplateFromFile(tpl)
    .evaluate()
    .setTitle('Réservation Cafétéria')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

// ======== API Inscription (Page.html) ========

function getInitialData() {
  var days = getAvailableDays();
  var rows = SHEET_RESAS.getDataRange().getValues().slice(1);

  var map = {};
  days.forEach(function(d){ map[d.date] = []; });
  rows.forEach(function(r){
    var raw = r[0] instanceof Date ? r[0] : new Date(r[0]);
    var dd  = Utilities.formatDate(raw, 'Europe/Zurich', 'dd.MM.yyyy');
    if (map[dd]) map[dd].push(r[1]);
  });

  return { days: days, reservations: map };
}

function getAvailableDays() {
  const tz = 'Europe/Zurich';
  const today = new Date(); today.setHours(0,0,0,0);
  const wanted = [
    { nom: 'Lundi',    dow: 1 },
    { nom: 'Mardi',    dow: 2 },
    { nom: 'Jeudi',    dow: 4 },
    { nom: 'Vendredi', dow: 5 }
  ];

  const sheet   = SHEET_PARAMS;
  const lastRow = sheet.getLastRow();
  const range   = sheet.getRange(2, 1, Math.max(0, lastRow - 1), 4);
  const values  = lastRow > 1 ? range.getValues()     : [];
  const colors  = lastRow > 1 ? range.getFontColors() : [];

  const rows = values.map((r,i) => {
    const d   = r[0] instanceof Date ? r[0] : new Date(r[0]);
    const red = (colors[i] && colors[i][2] ? colors[i][2] : '').toLowerCase().startsWith('#ff');
    return { date:d, jour:r[1], menu:r[2], open:r[3]===true, disabled:red };
  });

  return wanted.map(w => {
    const openCandidates = rows.filter(e => e.jour===w.nom && e.open && e.date>=today).sort((a,b)=>a.date-b.date);
    if (openCandidates.length) {
      const e = openCandidates[0];
      return {
        date: Utilities.formatDate(e.date, tz, 'dd.MM.yyyy'),
        jour: w.nom, menu: e.menu, open: true, disabled: e.disabled
      };
    }
    const d = new Date(today);
    do { d.setDate(d.getDate()+1); } while (d.getDay()!==w.dow);
    return { date: Utilities.formatDate(d, tz, 'dd.MM.yyyy'), jour: w.nom, menu: '', open: false, disabled: false };
  });
}

// Robuste (accepte dd.MM.yyyy / yyyy-MM-dd / Date ; défaut = aujourd’hui)
function getReservations(dateStr) {
  var tz = 'Europe/Zurich', d;
  if (!dateStr) {
    var now = new Date(); d = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  } else if (dateStr instanceof Date) {
    d = new Date(dateStr.getFullYear(), dateStr.getMonth(), dateStr.getDate());
  } else if (/^\d{2}\.\d{2}\.\d{4}$/.test(dateStr)) {
    var p = dateStr.split('.'); d = new Date(parseInt(p[2],10), parseInt(p[1],10)-1, parseInt(p[0],10));
  } else if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
    var q = dateStr.split('-'); d = new Date(parseInt(q[0],10), parseInt(q[1],10)-1, parseInt(q[2],10));
  } else {
    var tmp = new Date(dateStr);
    if (isNaN(tmp.getTime())) throw new Error("Paramètre 'dateStr' invalide. Utilisez 'dd.MM.yyyy', 'yyyy-MM-dd' ou une Date.");
    d = new Date(tmp.getFullYear(), tmp.getMonth(), tmp.getDate());
  }

  var isoDate = Utilities.formatDate(d, tz, 'yyyy-MM-dd');
  var data = SHEET_RESAS.getDataRange().getValues();
  var names = [];
  for (var i = 1; i < data.length; i++) {
    var raw = data[i][0];
    var dt  = raw instanceof Date ? raw : new Date(raw);
    var f   = Utilities.formatDate(new Date(dt.getFullYear(), dt.getMonth(), dt.getDate()), tz, 'yyyy-MM-dd');
    if (f === isoDate) names.push(data[i][1]);
  }
  return names;
}

function reserve(name, dateStr) {
  const parts = dateStr.split('.');
  const d = new Date(parseInt(parts[2],10), parseInt(parts[1],10)-1, parseInt(parts[0],10));
  const isoDate = Utilities.formatDate(d, 'Europe/Zurich', 'yyyy-MM-dd');

  const params = SHEET_PARAMS.getDataRange().getValues().slice(1);
  const match = params.find(r=>{
    const cd = r[0] instanceof Date ? r[0] : new Date(r[0]);
    const f  = Utilities.formatDate(new Date(cd.getFullYear(), cd.getMonth(), cd.getDate()), 'Europe/Zurich', 'yyyy-MM-dd');
    return f === isoDate && r[3] === true;
  });
  if (!match) throw 'Le ' + dateStr + ' est fermé, impossible de réserver.';

  const all = SHEET_RESAS.getDataRange().getValues().slice(1);
  const existing = all.filter(row=>{
    const cd = row[0] instanceof Date ? row[0] : new Date(row[0]);
    const f  = Utilities.formatDate(new Date(cd.getFullYear(), cd.getMonth(), cd.getDate()), 'Europe/Zurich', 'yyyy-MM-dd');
    return f === isoDate;
  }).length;
  if (existing >= 40) throw 'Quota de 40 atteint pour le ' + dateStr + '.';

  SHEET_RESAS.appendRow([isoDate, name, new Date()]);
  return 'Merci ' + name + ', réservation confirmée pour le ' + dateStr + ' !';
}

function unreserve(name, dateStr) {
  var parts = dateStr.split('.');
  var d     = new Date(parseInt(parts[2],10), parseInt(parts[1],10)-1, parseInt(parts[0],10));
  var isoDate = Utilities.formatDate(d, 'Europe/Zurich', 'yyyy-MM-dd');
  var target  = name.toString().trim();

  var all = SHEET_RESAS.getDataRange().getValues();
  for (var i = 1; i < all.length; i++) {
    var raw     = all[i][0];
    var cd      = raw instanceof Date ? raw : new Date(raw);
    var f       = Utilities.formatDate(new Date(cd.getFullYear(), cd.getMonth(), cd.getDate()), 'Europe/Zurich', 'yyyy-MM-dd');
    var rowName = all[i][1] ? all[i][1].toString().trim() : '';
    if (f === isoDate && rowName === target) {
      SHEET_RESAS.deleteRow(i+1);
      return 'Vous êtes désinscrit pour le ' + dateStr + '.';
    }
  }
  throw 'Pas de réservation trouvée pour "' + name + '" le ' + dateStr + '.';
}

// ======== Envoi liste (4×10) + lien CAISSE, puis fermeture inscriptions ========

function sendListAndClose() {
  var tz       = 'Europe/Zurich';
  var todayIso = getTodayIso_();

  var all  = SHEET_RESAS.getDataRange().getValues();
  var rows = [];
  for (var i = 1; i < all.length; i++) {
    var raw = all[i][0];
    var d   = raw instanceof Date ? raw : new Date(raw);
    var f   = Utilities.formatDate(new Date(d.getFullYear(), d.getMonth(), d.getDate()), tz, 'yyyy-MM-dd');
    if (f === todayIso) rows.push(all[i]);  // [date, nom, timestamp]
  }

  var bodyText = '', htmlBody = '';

  if (rows.length === 0) {
    bodyText = 'Aucune réservation pour aujourd’hui.';
    htmlBody = '<p>Aucune réservation pour aujourd\u2019hui.</p>';
  } else {
    var max  = Math.min(rows.length, 40);   // 4 colonnes x 10
    var cols = [[], [], [], []];

    for (var idx = 0; idx < max; idx++) {
      var c = Math.floor(idx / 10);     // 0..3
      var r = idx % 10;                 // 0..9
      cols[c][r] = rows[idx][1] || '';
    }

    function pad(s, w){ s = (s||'').toString(); return (s + Array(w+1).join(' ')).slice(0, w); }
    var lines = [];
    for (var ri = 0; ri < 10; ri++) {
      lines.push([0,1,2,3].map(function(ci){ return pad(cols[ci][ri],22); }).join('  |  '));
    }
    bodyText = 'Réservations (' + max + ')\n' + lines.join('\n');

    function esc(x){
      return String(x||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
        .replace(/\\"/g,'&quot;').replace(/'/g,'&#39;');
    }
    var html = [];
    html.push('<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;">');
    html.push('<h3 style="margin:0 0 8px 0;">Liste des réservations pour ' + prettyFrHeader_(todayIso) + '</h3>');
    html.push('<table style="border-collapse:collapse;width:100%;max-width:700px;">');
    for (var ri = 0; ri < 10; ri++) {
      html.push('<tr>');
      for (var ci = 0; ci < 4; ci++) {
        var cell = cols[ci][ri] ? esc(cols[ci][ri]) : '&nbsp;';
        html.push('<td style="border:1px solid #ddd;padding:6px 8px;">' + cell + '</td>');
      }
      html.push('</tr>');
    }
    html.push('</table></div>');
    htmlBody = html.join('');
  }

  var webUrl = getWebAppUrl_();
  if (webUrl) {
    var caisseUrl = webUrl + '?page=caisse&date=' + todayIso;
    htmlBody += '<p style="margin-top:12px;"><a href="' + caisseUrl + '">Ouvrir la page CAISSE pour ' + prettyFrHeader_(todayIso) + '</a></p>';
    bodyText += '\n\nOuvrir la page CAISSE : ' + caisseUrl;
  }

  MailApp.sendEmail({
    to:      'frank.bader@etat.ge.ch',
    subject: 'Liste des réservations — ' + prettyFrHeader_(todayIso),
    body:    bodyText,
    htmlBody: htmlBody
  });

  // Fermer le jour (Paramètres!D = FALSE)
  var params = SHEET_PARAMS.getDataRange().getValues();
  for (var k = 1; k < params.length; k++) {
    var dp = params[k][0] instanceof Date ? params[k][0] : new Date(params[k][0]);
    var fp = Utilities.formatDate(new Date(dp.getFullYear(), dp.getMonth(), dp.getDate()), tz, 'yyyy-MM-dd');
    if (fp === todayIso) { SHEET_PARAMS.getRange(k+1, 4).setValue(false); break; }
  }
}

// ======== Back-end CAISSE ========

function buildCaisseStats_(targetIso) {
  var tz = 'Europe/Zurich';
  var sh = getOrCreateCaisseSheet_();
  var data = sh.getDataRange().getValues();

  var totals = {
    menus:0, eleves:0, profs:0,
    sandwiches:0, beverages:0, chocolates:0,
    amount:0 // CASH encaissé (menus cash + extras + produits)
  };
  var paidCount = {};

  for (var r = 1; r < data.length; r++) {
    var row = data[r];
    if (toIso_(row[0], tz) !== targetIso) continue;

    var type = (row[2]||'').toString(); // Eleve (CASH) / Eleve (CARD) / Prof (...) / Sandwich / Boisson / Chocolat / Closed
    var base = Number(row[3])||0, bev = Number(row[4])||0, choc = Number(row[5])||0, tot = Number(row[6])||0;

    if (type === 'Closed') {
      // ignore
    } else if (type === 'Sandwich') {
      totals.sandwiches += 1;
    } else if (type === 'Boisson') {
      totals.beverages += 1;
    } else if (type === 'Chocolat') {
      totals.chocolates += 1;
    } else {
      // Menu (cash ou carte)
      totals.menus += 1;
      if (type.toLowerCase().indexOf('eleve') >= 0) totals.eleves += 1; else totals.profs += 1;

      // Compter le passage (réduction file d'attente)
      var nkey = normName_(row[1]||'');
      if (nkey) paidCount[nkey] = (paidCount[nkey]||0) + 1;

      // boisson/choco inclus dans "menu" (cochés) → déjà dans tot
      if (bev > 0) totals.beverages += 1;
      if (choc > 0) totals.chocolates += 1;
    }
    // tot = CASH encaissé pour cette ligne (pour carte : base exclue, tot = extras)
    totals.amount += tot;
  }

  return { totals: totals, paidCount: paidCount, closed: isCaisseClosed_(targetIso) };
}

// IMPORTANT : ordre identique à la liste d'inscription
function getCaisseData(dateIso) {
  var tz = 'Europe/Zurich';
  var targetIso = (dateIso && /^\d{4}-\d{2}-\d{2}$/.test(dateIso)) ? dateIso : getTodayIso_();

  var stats = buildCaisseStats_(targetIso);
  if (stats.closed) {
    return { date: targetIso, closed: true, names: [], totals: stats.totals };
  }

  // 1) Réservations du jour DANS L’ORDRE DES LIGNES (file d’attente)
  var data = SHEET_RESAS.getDataRange().getValues();
  var ordered = []; // [{name, key}]
  for (var i = 1; i < data.length; i++) {
    var raw  = data[i][0];
    var name = (data[i][1] || '').toString().trim();
    if (!name) continue;
    var iso  = toIso_(raw, tz);
    if (iso !== targetIso) continue;
    ordered.push({ name: name, key: normName_(name) });
  }

  // 2) Soustraire les validations existantes (file d’attente)
  var paidLeft = Object.assign({}, stats.paidCount);
  var remaining = [];
  for (var j = 0; j < ordered.length; j++) {
    var k = ordered[j].key;
    if (paidLeft[k] > 0) paidLeft[k]--; else remaining.push(ordered[j].name);
  }

  return { date: targetIso, closed: false, names: remaining, totals: stats.totals };
}

function assertOpenOrThrow_(targetIso) { if (isCaisseClosed_(targetIso)) throw 'Caisse fermée pour ' + targetIso + '.'; }

// Limite de 45 menus servis (inscrits + spontanés)
function assertMenuCapacity_(targetIso) {
  var stats = buildCaisseStats_(targetIso).totals;
  if (stats.menus >= 45) throw 'Limite de 45 menus servis atteinte pour ' + prettyFrHeader_(targetIso) + '.';
}

// Checkout (avec méthode de paiement : 'CASH' ou 'CARD')
// Pour 'CARD' : base du menu n’est PAS encaissée ; seuls les extras (boisson/chocolat) entrent dans le cash.
function checkout(name, type, beverage, chocolate, dateIso, method) {
  name = (name||'').toString().trim() || 'Anonyme';
  var targetIso = (dateIso && /^\d{4}-\d{2}-\d{2}$/.test(dateIso)) ? dateIso : getTodayIso_();
  assertOpenOrThrow_(targetIso);
  assertMenuCapacity_(targetIso);

  type = (type||'PROF').toString().toUpperCase();
  method = (method||'CASH').toString().toUpperCase(); // 'CASH' | 'CARD'

  var base = (type === 'ELEVE') ? PRICES.ELEVE : PRICES.PROF;
  var bev  = beverage  ? PRICES.BOISSON   : 0;
  var choc = chocolate ? PRICES.CHOCOLAT  : 0;
  var totalCash = (method === 'CARD') ? (bev + choc) : (base + bev + choc);

  var typeLabel = (type==='ELEVE'?'Eleve':'Prof') + (method==='CARD'?' (CARD)':' (CASH)');

  var sh = getOrCreateCaisseSheet_();
  sh.appendRow([targetIso, name, typeLabel, base, bev, choc, totalCash, new Date()]);
  return getCaisseData(targetIso);
}

// Produits rapides (toujours cash)
function addSandwich(qty, dateIso) {
  var n = Math.max(1, parseInt(qty,10)||1);
  var targetIso = (dateIso && /^\d{4}-\d{2}-\d{2}$/.test(dateIso)) ? dateIso : getTodayIso_();
  assertOpenOrThrow_(targetIso);
  var sh = getOrCreateCaisseSheet_();
  for (var i=0; i<n; i++) sh.appendRow([targetIso, '', 'Sandwich', PRICES.SANDWICH, 0, 0, PRICES.SANDWICH, new Date()]);
  return getCaisseData(targetIso);
}
function addBeverage(qty, dateIso) {
  var n = Math.max(1, parseInt(qty,10)||1);
  var targetIso = (dateIso && /^\d{4}-\d{2}-\d{2}$/.test(dateIso)) ? dateIso : getTodayIso_();
  assertOpenOrThrow_(targetIso);
  var sh = getOrCreateCaisseSheet_();
  for (var i=0; i<n; i++) sh.appendRow([targetIso, '', 'Boisson', 0, PRICES.BOISSON, 0, PRICES.BOISSON, new Date()]);
  return getCaisseData(targetIso);
}
function addChocolate(qty, dateIso) {
  var n = Math.max(1, parseInt(qty,10)||1);
  var targetIso = (dateIso && /^\d{4}-\d{2}-\d{2}$/.test(dateIso)) ? dateIso : getTodayIso_();
  assertOpenOrThrow_(targetIso);
  var sh = getOrCreateCaisseSheet_();
  for (var i=0; i<n; i++) sh.appendRow([targetIso, '', 'Chocolat', 0, 0, PRICES.CHOCOLAT, PRICES.CHOCOLAT, new Date()]);
  return getCaisseData(targetIso);
}

// Fermer la caisse : e-mail + drapeau "Closed" + URL de redirection
function closeCaisse(dateIso) {
  var targetIso = (dateIso && /^\d{4}-\d{2}-\d{2}$/.test(dateIso)) ? dateIso : getTodayIso_();

  if (!isCaisseClosed_(targetIso)) {
    var stats = buildCaisseStats_(targetIso).totals;

    var subject = 'Comptabilité cafétéria — ' + prettyFrHeader_(targetIso);

    // Cash encaissé = stats.amount (menus cash + extras + produits)
    var cashIn = Math.round(stats.amount*100)/100;
    var totalInTill = Math.round((CASH_FLOAT + cashIn)*100)/100;

    var body = [
      'Date : ' + prettyFrHeader_(targetIso),
      '',
      'Menus : ' + stats.menus + ' (élèves ' + stats.eleves + ', profs ' + stats.profs + ')',
      'Sandwiches : ' + stats.sandwiches,
      'Boissons : ' + stats.beverages,
      'Chocolats : ' + stats.chocolates,
      '',
      'Fond de caisse initial : ' + CASH_FLOAT.toFixed(2) + ' CHF',
      'Encaissements cash : ' + cashIn.toFixed(2) + ' CHF',
      'Total en caisse attendu : ' + totalInTill.toFixed(2) + ' CHF'
    ].join('\n');

    var html = [];
    html.push('<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;">');
    html.push('<h3 style="margin:0 0 10px 0;">Comptabilité — ' + prettyFrHeader_(targetIso) + '</h3>');
    html.push('<table style="border-collapse:collapse;">');
    function row(label,val){ html.push('<tr><td style="border:1px solid #ddd;padding:6px 10px;">'+label+'</td><td style="border:1px solid #ddd;padding:6px 10px;text-align:right;">'+val+'</td></tr>'); }
    row('Menus (total)', String(stats.menus));
    row('— Élèves',     String(stats.eleves));
    row('— Profs',      String(stats.profs));
    row('Sandwiches',   String(stats.sandwiches));
    row('Boissons',     String(stats.beverages));
    row('Chocolats',    String(stats.chocolates));
    row('<b>Fond de caisse initial</b>', '<b>'+CASH_FLOAT.toFixed(2)+' CHF</b>');
    row('<b>Encaissements cash</b>',     '<b>'+cashIn.toFixed(2)+' CHF</b>');
    row('<b>Total en caisse attendu</b>','<b>'+totalInTill.toFixed(2)+' CHF</b>');
    html.push('</table></div>');

    MailApp.sendEmail({
      to: 'frank.bader@etat.ge.ch',
      subject: subject,
      body: body,
      htmlBody: html.join('')
    });

    // Marqueur de fermeture
    getOrCreateCaisseSheet_().appendRow([targetIso, '', 'Closed', 0, 0, 0, 0, new Date()]);
  }

  var url = getWebAppUrl_();
  var closedUrl = url ? (url + '?page=closed') : '';
  return { closedUrl: closedUrl };
}
