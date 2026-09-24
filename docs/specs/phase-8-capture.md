# Capture : enregistrer depuis le navigateur, transcription en direct, import d'un lien

| | |
|---|---|
| Statut | Implémenté (2026-09-24) |
| Origine | Feuille de route n° 2 : points 10, 11 et 12 (le point 9, dossier surveillé, est livré en phase 7) |
| Migration | `0010_recordings_links` |

## n°10 — Enregistrer depuis le navigateur

- Page **Enregistrer** (`/record`) : micro, son d'un onglet ou d'une fenêtre (une visioconférence dans le navigateur), ou les deux mélangés (`AudioContext`). Minuteur, vumètre, pause, « Arrêter et analyser ». Mêmes options que l'import (langues, template, longueur, intervenants, règle des médias).
- **Envoi au fil de l'eau** : `MediaRecorder` produit un morceau toutes les 4 s, envoyé dans l'ordre (`PUT /recordings/{id}/chunks/{n}`). Un morceau renvoyé après une coupure réseau est reconnu et ignoré, un trou est refusé (409). Si l'onglet se ferme, ce qui a été envoyé reste sur le serveur : la page propose ensuite « Analyser ce qui a été enregistré » ou « Abandonner » (`GET /recordings`, `DELETE /recordings/{id}`).
- **À l'arrêt** (`POST /recordings/{id}/finish`) : le flux (WebM ou Ogg Opus, MP4 AAC sous Safari), qui n'indique pas sa durée, est réécrit sans réencodage en `.ogg` ou `.m4a`, puis importé comme un fichier (`create_import`). La page ouvre la vidéo et suit son traitement.
- Formats : Opus (Chrome, Edge, Firefox) et AAC (Safari). Le navigateur n'autorise le micro que sur `http://127.0.0.1`, `http://localhost` ou en HTTPS.

## n°11 — Transcription en direct

- Service `live` (`python -m app.live`, sur le GPU avec la surcouche) : pour chaque enregistrement en cours avec l'option, il lit les nouveaux octets du fichier et les passe à un processus ffmpeg (les morceaux forment un seul flux), qui les décode en PCM 16 kHz.
- Toutes les ~3 s de nouvel audio, la partie non validée est transcrite par un petit modèle (`LIVE_WHISPER_MODEL`, `small` par défaut), avec l'horodatage des mots. Sont validés les mots jusqu'à la dernière fin de phrase, hors des 2 dernières secondes ; le reste est retranscrit avec la suite. Une ligne par phrase (`live_segments`), avec environ une phrase de retard. Au-delà de 24 s sans fin de phrase, le texte stable est validé quand même ; un silence est sauté.
- La page lit les lignes en SSE (`GET /recordings/{id}/live`).
- C'est un **aperçu** : à l'arrêt, le worker retranscrit tout avec le modèle principal, puis résume. Sur la voix de test, l'aperçu entend « Donia » là où la transcription finale écrit « Donyana », et coupe parfois un mot en début de fenêtre.
- Le modèle est chargé au premier enregistrement puis libéré après 2 minutes sans enregistrement. Un échec (mémoire GPU…) arrête le direct seulement : l'enregistrement continue (`live_error`).
- L'état du système affiche le service (« Transcription en direct »). S'il est arrêté, la case est désactivée.

## n°12 — Import d'un lien

- Onglet **Lien** de l'accueil : « Vérifier le lien » (`POST /imports/url/preview`) reconnaît un fichier audio ou vidéo (nom, taille) ou un flux RSS/Atom de podcast (épisodes avec date, durée, taille ; 30 affichés). Chaque élément choisi devient un import (`POST /imports/url`) : la vidéo est créée tout de suite et le worker télécharge le fichier en première étape (« Téléchargement »), puis vérifie sa durée. Une vidéo dont le téléchargement a échoué peut être relancée.
- **Plateformes vidéo (option, désactivée par défaut)** : Paramètres › Import de liens (`/settings/url-import`). Activée, une page de plateforme (YouTube, Vimeo, Dailymotion, PeerTube…) est lue avec `yt-dlp` : aperçu (titre, chaîne, durée, licence), listes de lecture présentées comme un flux, **audio seul** téléchargé (`bestaudio`, M4A de préférence). Seuls les sites connus de yt-dlp sont acceptés (extracteur « generic » exclu : il suivrait n'importe quelle page). Durée vérifiée avant le téléchargement ; messages de la plateforme repris (« Private video »…). Désactivée, une page web est refusée avec un renvoi vers l'option.
- **Droits** : les conditions des plateformes interdisent en général le téléchargement ; c'est un choix de l'utilisateur, averti dans les Paramètres. Avant chaque import, il coche « J'ai le droit d'utiliser ce contenu », et le lien d'origine est conservé et affiché sur la vidéo (`source_url`).
- **Dépendances** : `yt-dlp[default]` (avec les scripts `yt-dlp-ejs`) et `deno` (moteur JavaScript que YouTube exige désormais), versions figées dans `requirements.txt`. Les plateformes changent souvent : quand les imports échouent, mettre à jour ces versions et reconstruire.
- **Sécurité réseau** : l'API et le worker côtoient PostgreSQL, Redis et Ollama. Les adresses privées ou locales sont refusées, à chaque redirection (5 au plus), sauf `URL_IMPORT_ALLOW_PRIVATE=true` (un NAS). Les flux sont lus avec `defusedxml` (pas d'entités XML) et limités à 5 Mo, les fichiers à `MAX_DOWNLOAD_BYTES` (4 Go).

## Défaut lié corrigé : flux SSE retenus par le proxy

Le serveur Next.js (proxy `/api`) compressait en gzip les réponses `text/event-stream` et les retenait jusqu'à leur fin. Cela touchait la transcription en direct, et aussi le suivi des traitements (masqué par le rafraîchissement de secours toutes les 3 s) et les réponses du chat en flux. Toutes les réponses SSE portent maintenant `Cache-Control: no-cache, no-transform`, que la compression respecte.

## Vérifications

- 334 tests backend (29 nouveaux, dont 8 pour les plateformes avec un faux yt-dlp) : ordre et reprise des morceaux, réécriture ffmpeg réelle d'un WebM Opus, décodeur ffmpeg alimenté par morceaux, découpage en phrases avec des mots horodatés, service `live` sur un vrai fichier, échec du direct sans perte de l'enregistrement ; liens avec un faux Internet (fichier, RSS, page web, redirection vers une adresse interne, boucle de redirections, entités XML, taille maximale), téléchargement par le worker et relance.
- Sur la vraie pile GPU (données de test supprimées ensuite) :
  - enregistrement simulé, 33 s envoyées en 9 morceaux en temps réel : 11 lignes de direct, puis vidéo `.ogg` de 33 s transcrite par le grand modèle ;
  - dans Chromium, un faux micro (voix de synthèse injectée à la place de `getUserMedia`, jamais le vrai micro du poste) : première ligne à 7,5 s, 5 lignes à 25 s, puis analyse ;
  - lien Wikimedia Commons (`Example.ogg`) téléchargé et analysé ; flux NPR News Now listé ; `http://ollama:11434` refusé ;
  - YouTube, option désactivée : refusé avec le renvoi vers l'option ; activée : *Big Buck Bunny* (Blender Foundation, CC-BY) prévisualisé avec sa licence, audio de 10 min téléchargé et analysé (« Aucun contenu parlé détecté » : le film n'a pas de dialogues). Option remise à « désactivée » après le test.

## Limites

- La transcription en direct ne concerne que les enregistrements faits dans Sténo, pas une réunion captée par un autre logiciel.
- Un enregistrement interrompu ne peut pas être prolongé : on analyse ce qui a été reçu.
- L'aperçu direct et la transcription finale peuvent se disputer le GPU si une longue analyse tourne en même temps : le petit modèle limite ce risque.
- Import d'un lien : pas d'authentification (vidéos privées, comptes). Les plateformes peuvent bloquer yt-dlp du jour au lendemain.
