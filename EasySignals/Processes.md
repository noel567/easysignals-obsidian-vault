# Processes & Workflows

> Owner: [[Livio]] | Plattform: [[TeleTrade/CRM-Flows|TeleTrade CRM]] | Produkte: [[EasySignals/Products|Products]]

---

## Signal Generation
1. [[Livio]] analysiert XAU/USD täglich
2. Erstellt 2–3 High-Probability Trades
3. Postet in Telegram/WhatsApp
4. LAT System auto-executed → [[EasySignals/Products#2. LAT System|LAT]]

## Passing Service Flow
1. Kunde bucht Challenge → [[SOPs/Sales|Sales SOP]]
2. [[Livio]] executed Trades
3. Challenge abgeschlossen <48h
4. Funds auf Client Account transferiert
5. Abschluss dokumentiert → [[TeleTrade/CRM-Flows|CRM]]

## LTI Traffic Flow
```
Ads → Telegram Channel → [[TeleTrade/CRM-Flows|CRM]] → [[Lencjs|Closer]] → Deposit → [[Subaffiliates/LTI-Partnership|15% Rev Share]]
```

## Content Calendar
- Daily: 1-2 Marktanalysen
- 3x/Woche: Educational Content
- 2x/Woche: Case Studies / Testimonials
- 1x/Woche: Community Updates

---

## Tags
#processes #workflows #easysignals #signals


### 2026-04-18 04:16

# 2026-04-18 Memory Log ## Session 1: Early Morning (01:00-02:50 GMT+2) ### Major Accomplishments #### ✅ Web App Deployment (Node + Express) - **Status:** LIVE on VPS (72.62.35.65:3000) - **Stack:** Node.js v18.17.0 + Express + systemd service 1. Downloaded Node.js to `/opt/easysignals-app` 2. Created `server.js` (Express API) 3. Deployed via systemd service (`/etc/systemd/system/easysignals.service`) 4. Server runs permanently (auto-restart on failure) - **Test:** `curl http://72.62.35.65:3000`

_Source: Auto-sync from chat_