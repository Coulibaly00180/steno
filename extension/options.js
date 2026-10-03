/* global StenoLib */
"use strict";

const $ = id => document.getElementById(id);
const status = (text, kind) => { $("status").textContent = text; $("status").className = kind || ""; };

async function loadTemplates(config, selected) {
  const templates = await StenoLib.stenoFetch(config, "/api/templates", { method: "GET" });
  const select = $("template");
  select.replaceChildren(new Option("Template par défaut de Sténo", ""));
  for (const template of templates || []) select.append(new Option(template.name, template.id, false, template.id === selected));
}

async function init() {
  const stored = await chrome.storage.local.get(["origin", "token", "rightsConfirmed", "templateId", "diarize", "numSpeakers"]);
  $("origin").value = stored.origin || StenoLib.DEFAULT_ORIGIN;
  $("token").value = stored.token || "";
  $("rights").checked = !!stored.rightsConfirmed;
  $("diarize").checked = !!stored.diarize;
  $("speakers").value = stored.numSpeakers || "";
  if (!stored.origin) { status("Indiquez l'adresse de Sténo, puis « Enregistrer et tester ».", "warn"); return; }
  const granted = await chrome.permissions.contains({ origins: [StenoLib.permissionPattern(stored.origin)] });
  if (!granted) { status("Le navigateur doit encore autoriser l'accès à Sténo : « Enregistrer et tester ».", "warn"); return; }
  try { await loadTemplates({ origin: stored.origin, token: stored.token }, stored.templateId); }
  catch (error) { status(error.message, "error"); }
}

$("settings").addEventListener("submit", event => {
  event.preventDefault();
  let origin;
  try { origin = StenoLib.normalizeOrigin($("origin").value); }
  catch (error) { status(error.message, "error"); return; }
  const pattern = StenoLib.permissionPattern(origin);
  // Asked inside the click: browsers grant permissions to a user gesture only.
  chrome.permissions.request({ origins: [pattern] }).then(async granted => {
    if (!granted) { status("Accès refusé par le navigateur : l'extension ne peut pas contacter Sténo.", "error"); return; }
    const previous = (await chrome.storage.local.get("origin")).origin;
    if (previous && StenoLib.permissionPattern(previous) !== pattern) {
      // The old address is no longer reachable by the extension.
      await chrome.permissions.remove({ origins: [StenoLib.permissionPattern(previous)] }).catch(() => {});
    }
    const config = { origin, token: $("token").value.trim() };
    await chrome.storage.local.set({
      origin, token: config.token, rightsConfirmed: $("rights").checked, templateId: $("template").value,
      diarize: $("diarize").checked, numSpeakers: $("speakers").value,
    });
    $("origin").value = origin;
    status("Test de la connexion…");
    try {
      const imports = await StenoLib.stenoFetch(config, "/api/settings/url-import", { method: "GET" });
      await loadTemplates(config, $("template").value);
      status(imports && imports.platforms
        ? "Connecté à Sténo. Les pages des plateformes vidéo (YouTube…) sont acceptées."
        : "Connecté à Sténo. Pour envoyer des pages YouTube, activez Paramètres › Import de liens dans Sténo.", imports && imports.platforms ? "ok" : "warn");
    } catch (error) { status(error.message, "error"); }
  });
});

void init();
