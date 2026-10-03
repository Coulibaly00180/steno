// "Envoyer à Sténo": one click on the icon sends the open tab to Sténo's import by link;
// the context menu opens the options first (template, speakers). Feuille de route n° 4, phase 1.
//
// Chrome and Edge run this file as a service worker (lib.js imported below);
// Firefox loads lib.js then this file as background scripts (manifest.json).
/* global StenoLib */
if (typeof importScripts === "function" && typeof StenoLib === "undefined") importScripts("lib.js");

const MENU_OPTIONS = "send-with-options";
const MENU_LINK = "send-link";
const ALARM = "steno-jobs";
const FOLLOW_HOURS = 48;
const ICON = "icons/icon-128.png";

async function getConfig() {
  // Local storage only, never the synced one: the token stays on this computer.
  const stored = await chrome.storage.local.get(["origin", "token", "rightsConfirmed", "templateId", "diarize", "numSpeakers"]);
  return { origin: stored.origin || "", token: stored.token || "", rightsConfirmed: !!stored.rightsConfirmed,
    defaults: { templateId: stored.templateId || "", diarize: !!stored.diarize, numSpeakers: stored.numSpeakers || "" } };
}

function notify(id, title, message) {
  chrome.notifications.create(id, { type: "basic", iconUrl: ICON, title, message: String(message).slice(0, 300) });
}

function badge(tabId, text, color) {
  const target = tabId ? { tabId } : {};
  chrome.action.setBadgeText(Object.assign({ text }, target));
  if (color) chrome.action.setBadgeBackgroundColor(Object.assign({ color }, target));
}

/** Preview the link, then queue it. Resolves to { ok, message, videoId? }. */
async function send(url, title, options, tabId) {
  const config = await getConfig();
  if (!config.origin || !config.rightsConfirmed) {
    chrome.runtime.openOptionsPage();
    return { ok: false, message: "Réglez d'abord l'adresse de Sténo dans les options de l'extension." };
  }
  if (!StenoLib.isSendable(url, config.origin)) {
    return fail(tabId, "Cette page ne peut pas être envoyée : seuls les liens http:// et https:// le peuvent.");
  }
  badge(tabId, "…", "#6366f1");
  try {
    const preview = await StenoLib.stenoFetch(config, "/api/imports/url/preview", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url }),
    });
    if (preview && preview.kind === "feed") {
      // A podcast feed: its episodes are chosen in Sténo.
      chrome.tabs.create({ url: `${StenoLib.normalizeOrigin(config.origin)}/envoyer?url=${encodeURIComponent(url)}` });
      badge(tabId, "");
      return { ok: true, message: "Flux de podcast : choisissez les épisodes dans Sténo." };
    }
    const name = (preview && preview.title) || title;
    const job = await StenoLib.stenoFetch(config, "/api/imports/url", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(StenoLib.importBody(preview && preview.url ? preview.url : url, name, options || config.defaults)),
    });
    await follow(job, StenoLib.cleanTitle(name));
    badge(tabId, "✓", "#16a34a");
    setTimeout(() => badge(tabId, ""), 5000);
    const message = `« ${StenoLib.cleanTitle(name) || url} » est en file d'attente. Une notification vous préviendra à la fin de l'analyse.`;
    notify(`video:${job.video_id}`, "Envoyé à Sténo", message);
    return { ok: true, message, videoId: job.video_id };
  } catch (error) {
    return fail(tabId, error.message || String(error));
  }
}

function fail(tabId, message) {
  badge(tabId, "!", "#dc2626");
  setTimeout(() => badge(tabId, ""), 8000);
  notify(`error:${Date.now()}`, "Sténo n'a pas reçu la page", message);
  return { ok: false, message };
}

// --- "Analyse terminée": the queued jobs are followed until they end ------------------------------

async function follow(job, title) {
  const { jobs = [] } = await chrome.storage.local.get("jobs");
  jobs.push({ id: job.id, videoId: job.video_id, title, since: Date.now() });
  await chrome.storage.local.set({ jobs });
  chrome.alarms.create(ALARM, { periodInMinutes: 1 });
}

async function checkJobs() {
  const config = await getConfig();
  const { jobs = [] } = await chrome.storage.local.get("jobs");
  const still = [];
  for (const item of jobs) {
    if (Date.now() - item.since > FOLLOW_HOURS * 3600 * 1000 || !config.origin) continue;
    let job;
    try {
      job = await StenoLib.stenoFetch(config, `/api/jobs/${encodeURIComponent(item.id)}`, { method: "GET" });
    } catch {
      still.push(item); // Sténo stopped or unreachable for now: try again at the next alarm.
      continue;
    }
    if (job.status === "COMPLETED") notify(`video:${item.videoId}`, "Analyse terminée", `« ${item.title || "Vidéo"} » : ouvrez le compte-rendu dans Sténo.`);
    else if (job.status === "FAILED") notify(`video:${item.videoId}`, "Analyse en échec", `« ${item.title || "Vidéo"} » : ${job.error || "voir Sténo"}`);
    else if (job.status !== "CANCELLED") still.push(item);
  }
  await chrome.storage.local.set({ jobs: still });
  if (!still.length) chrome.alarms.clear(ALARM);
}

// --- events -----------------------------------------------------------------------------------------

chrome.runtime.onInstalled.addListener(async details => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: MENU_OPTIONS, title: "Envoyer à Sténo avec des options…", contexts: ["action", "page"] });
    chrome.contextMenus.create({ id: MENU_LINK, title: "Envoyer ce lien à Sténo", contexts: ["link"] });
  });
  if (details.reason === "install") chrome.runtime.openOptionsPage();
});

chrome.action.onClicked.addListener(tab => { void send(tab.url, tab.title, null, tab.id); });

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === MENU_LINK && info.linkUrl) { void send(info.linkUrl, "", null, tab && tab.id); return; }
  if (info.menuItemId !== MENU_OPTIONS) return;
  const url = info.pageUrl || (tab && tab.url) || "";
  const params = new URLSearchParams({ url, title: (tab && tab.title) || "", tab: String((tab && tab.id) || "") });
  chrome.windows.create({ url: chrome.runtime.getURL(`send.html?${params}`), type: "popup", width: 460, height: 560 });
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Only the extension's own pages talk to it (no content script, no external page).
  if (!message || message.type !== "send" || sender.id !== chrome.runtime.id) return false;
  send(message.url, message.title, message.options, Number(message.tabId) || undefined).then(sendResponse);
  return true;
});

chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === ALARM) void checkJobs(); });

chrome.notifications.onClicked.addListener(async id => {
  chrome.notifications.clear(id);
  const config = await getConfig();
  if (id.startsWith("video:") && config.origin) {
    chrome.tabs.create({ url: `${StenoLib.normalizeOrigin(config.origin)}/videos/${encodeURIComponent(id.slice(6))}` });
  } else if (id.startsWith("error:")) {
    chrome.runtime.openOptionsPage();
  }
});
