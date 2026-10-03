/* global StenoLib */
"use strict";

// The second click: options for this page only, then the background sends it.
const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const url = params.get("url") || "";
const title = params.get("title") || "";
const status = (text, kind) => { $("status").textContent = text; $("status").className = kind || ""; };

async function init() {
  $("page").textContent = StenoLib.cleanTitle(title) || url;
  const stored = await chrome.storage.local.get(["origin", "token", "templateId", "diarize", "numSpeakers"]);
  $("diarize").checked = !!stored.diarize;
  $("speakers").value = stored.numSpeakers || "";
  if (!stored.origin) { status("Réglez d'abord l'adresse de Sténo dans les options de l'extension.", "error"); return; }
  try {
    const templates = await StenoLib.stenoFetch({ origin: stored.origin, token: stored.token }, "/api/templates", { method: "GET" });
    for (const template of templates || []) $("template").append(new Option(template.name, template.id, false, template.id === stored.templateId));
  } catch (error) { status(error.message, "error"); }
  $("submit").focus();
}

$("send").addEventListener("submit", async event => {
  event.preventDefault();
  $("submit").disabled = true;
  status("Envoi…");
  const options = { templateId: $("template").value, diarize: $("diarize").checked, numSpeakers: $("speakers").value };
  const result = await chrome.runtime.sendMessage({ type: "send", url, title, options, tabId: params.get("tab") });
  if (result && result.ok) { status(result.message, "ok"); setTimeout(() => window.close(), 1500); }
  else { status((result && result.message) || "Échec de l'envoi", "error"); $("submit").disabled = false; }
});

void init();
