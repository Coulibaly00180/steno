# Suivi : qualité mesurée, séries de réunions, extraits, accès réseau, installation Windows

| | |
|---|---|
| Statut | Implémenté (2026-09-24) |
| Origine | Feuille de route n° 2 : points 4, 6, 7, 15 et 19 (dernier lot) |
| Migration | `0013_series_clips_quality` |

## n°4 — Suivi de qualité

- **Principe** (`app/quality.py`) : le corpus de référence (`data/corpus`, voir son README) est rejoué avec le code des analyses. Le calcul du résumé est isolé dans `worker.compose_summary`, qui n'écrit rien en base : un FULL et une évaluation appellent le même code.
- **Mesures par enregistrement** (méthode de `data/corpus/score.py`) :
  - couverture des sujets attendus (expressions régulières tirées des descriptions officielles, jamais de nos résumés) ;
  - longueur par rapport au budget de mots de `analysis_options.word_budget` ;
  - débuts de chapitres attendus retrouvés, à ± 2 min ;
  - langue détectée ;
  - temps de transcription et de résumé.

  Le score d'une évaluation est la couverture moyenne, en pourcentage.
- **Comparaison** avec l'évaluation précédente de même portée. Chaque recul est signalé : un sujet perdu, une longueur qui sort de 50 à 150 % du budget, un début de chapitre perdu, une langue mal détectée.
- **Portées** :
  - rapide : `clip-fr`, `reunion-fr`, `podcast-en` ;
  - complète : ajoute `cours-fr` et `conference-en`, soit environ 5 h d'enregistrement.
- **Cache des transcriptions** dans `data/corpus/.cache/`, une par modèle Whisper et largeur de faisceau : changer un prompt ou le LLM ne refait que les résumés.
- **Déclenchement automatique**, réglable :
  - l'empreinte couvre le source de `llm.py` et de `analysis_options.py`, le template par défaut, les templates du corpus, le LLM, le modèle Whisper et la fenêtre de contexte ;
  - une évaluation rapide démarre quand l'empreinte diffère de celle de la dernière évaluation, au démarrage du worker (prompts modifiés puis image reconstruite) ou quand un modèle change dans la page Modèles ;
  - l'évaluation passe sur la file secondaire : elle ne retarde jamais une analyse ;
  - sans corpus sur la machine, rien ne se passe.
- **Interface** : section « Suivi de qualité » de la page Modèles.
  - Elle montre la configuration actuelle, évaluée ou non, et l'historique : score, écart avec l'évaluation précédente, nombre de reculs.
  - Le détail par enregistrement donne les sujets manquants, la longueur, les chapitres et les temps.
  - On peut lancer une évaluation rapide ou complète, et l'annuler.
- **Mesure réelle** (qwen3:8b, large-v3-turbo, prompts `870afa71`) :
  - le premier passage automatique a démarré au redéploiement, puisqu'aucune évaluation n'existait encore ; il a été annulé en cours de route (arrêt immédiat) ;
  - l'évaluation rapide relancée a obtenu **56,8 %**, en réutilisant les transcriptions déjà en cache.

  | Enregistrement | Sujets couverts | Longueur / budget | Chapitres |
  |---|---|---|---|
  | `reunion-fr` | 6/6 | 585 / 540 mots | 22 |
  | `podcast-en` | 5/11 | 555 / 602 mots | 23 |
  | `clip-fr` | 1/4 | 172 / 250 mots | 5 |

  C'est cohérent avec le calibrage de la phase 1 (5/6, 6/11 et 1/4). Cette évaluation sert de référence aux suivantes.

## n°6 — Séries de réunions

- **Table** `meeting_series`, avec `videos.series_id` (`SET NULL` : supprimer une série garde ses réunions). L'ordre des réunions est celui de leur import.
- **Suggestions** (`series.title_key`) : deux réunions sans série dont les titres coïncident, dates, numéros, jours et petits mots de date mis à part, sont proposées comme série. « Comité budget 2026-09-24 » et « Comité budget du 17 septembre » donnent tous deux « comite budget ». Si le titre correspond à une série existante, la proposition est d'y ajouter la réunion.
- **Page d'une série** (`/series/{id}`) : les réunions sur une frise, les actions encore ouvertes de toutes les réunions (avec leur réunion d'origine), et les décisions dans l'ordre. La page Actions accepte aussi `?series_id=`.
- **« Depuis la dernière réunion »** (`GET /videos/{id}/series/changes`, sous le résumé) :
  - un appel au LLM, au format JSON imposé, compare le compte-rendu avec celui de la réunion précédente ;
  - il relève ce qui est nouveau, ce qui a évolué et ce qui n'est plus mentionné ;
  - il reçoit aussi les actions encore ouvertes des réunions précédentes, numérotées, et dit lesquelles la réunion déclare faites, avec la phrase qui le montre. Un numéro inexistant est écarté.
  - Rien n'est fermé automatiquement : l'utilisateur confirme par « Marquer faite ».
  - Garde-fou mesuré : le modèle a donné comme preuve d'une relance faite la phrase « Sophie n'a pas encore relancé le fournisseur, elle le fera avant vendredi ». Une preuve négative ou au futur (« pas encore », « doit », « fera », « not yet »…) est donc refusée par le code.
  - Le prompt signale que la transcription peut écrire un même nom différemment d'une réunion à l'autre.
  - Résultat sur deux réunions de test (voix de synthèse, où Whisper a écrit « Benolé » pour « Benally »), identique sur deux passages : le devis envoyé et la présentation faite sont reconnus, la relance non faite ne l'est pas.
  - Le résultat est gardé dans `videos.series_changes`. La clé couvre les deux résumés, les actions ouvertes et le modèle : cocher une action ou régénérer un résumé relance la comparaison. « Recomparer » la force.
- **Page vidéo** : sous le titre, la série, la position de la réunion (« réunion 3 sur 5 »), la réunion précédente et la suivante. Sans série, une suggestion ou « Ajouter à une série de réunions ».
- **Archive portable** : le nom de la série et les actions (n°5) voyagent avec chaque vidéo. À l'import, la vidéo rejoint la série de même nom, créée si besoin.

## n°7 — Extraits vidéo

- **Tâche `CLIP`** (file principale, `worker.run_clip`, `app/clips.py`).
  - ffmpeg réencode l'extrait en H.264 et AAC (MP4, `faststart`). Une copie de flux commencerait à l'image clé précédente, des secondes avant le moment choisi.
  - La recherche placée avant `-i` est exacte en réencodage, et remet les horodatages à zéro : les sous-titres de la plage sont décalés d'autant.
- **Sous-titres** : ceux de la transcription, avec les noms d'intervenants, ou ceux de la traduction.
  - « Incrustés » dessine le texte sur l'image (filtre `subtitles`, DejaVu Sans) : lisible dans les messageries, mais impossible à retirer.
  - « Piste activable » ajoute une piste `mov_text` avec sa langue.
- **Source sans image** (import audio, média converti en audio seul) : l'extrait est un fichier `.m4a`. La source supprimée mais la piste de travail restante suffit aussi.
- **Fichiers** dans `data/exports/<vidéo>/clips/`, supprimés avec la vidéo. Ils sont comptés dans l'espace disque de la vidéo. Supprimer un extrait en cours de découpe annule d'abord sa tâche.
- **Une tâche CLIP n'est jamais « le traitement de la vidéo »** (`BACKGROUND_KINDS`) : elle ne bloque ni les corrections ni l'affichage. Son état est lu sur la tâche tant que le worker n'a pas enregistré la fin (tâche annulée ou échouée).
- **Interface** : ciseaux au survol de chaque chapitre (la plage du chapitre, avec son titre), carte « Extraits » (début et fin, bouton « Ici » pour la position de lecture, sous-titres, progression), puis « Voir » et « Télécharger ».
- **Vérifié** : un chapitre de 16 s, découpé avec sous-titres incrustés, donne un MP4 H.264 + AAC de 16,0 s. Une image prise à 4 s montre le compteur de la mire à 19 s (coupe exacte) et le bon sous-titre.

## n°15 — Accès depuis le réseau local

Guide : `docs/acces-reseau.md`.

- **Service optionnel `https`** (Caddy, profil `reseau`, port 8443) : autorité de certification locale, aucun service extérieur. Il marque les requêtes qu'il transmet avec `X-Steno-Remote: 1`.
- **Règles de l'API** (`auth.decide`, middleware ASGI `AccessGuard`) :
  - une requête de l'ordinateur passe, sauf si le mot de passe y est aussi demandé ;
  - une requête du réseau est refusée (403) tant qu'aucun mot de passe n'est défini ; ensuite il lui faut une session (401 sinon) ;
  - `/health`, `/ready`, `/auth/*` et `/network/certificate` restent publics.
- **Mot de passe** : scrypt, dans `app_settings` sous la clé `access`, jamais renvoyée par l'API.
- **Session** : cookie signé HMAC valable 30 jours, `Secure` derrière HTTPS. Il porte la version du mot de passe : un changement ferme toutes les sessions.
- **Essais** : 10 par appareil et par tranche de 15 minutes (Redis).
- **Interface** :
  - page `/login`, que toute réponse 401 déclenche, puis retour à la page d'origine (chemin interne uniquement) ;
  - « Se déconnecter » dans la barre latérale ;
  - Paramètres › Accès et sécurité : mot de passe, option « le demander aussi sur cet ordinateur », étapes de l'accès réseau avec l'adresse, téléchargement de l'autorité.
- **Vérifié sur la vraie pile** :
  - sans mot de passe, le proxy renvoie 403, y compris par l'adresse IP et avec un en-tête `X-Steno-Remote: 0` forgé ;
  - avec mot de passe, 401 sans cookie, puis connexion et accès aux pages et à l'API ;
  - l'ordinateur local passe toujours.

  Le mot de passe de test a été supprimé et le proxy arrêté.

## n°19 — Installation Windows et premier lancement

Guide : `docs/installation-windows.md`.

- **`Installer Steno.cmd`** appelle `windows/steno.ps1 install` :
  - Docker Desktop : présence (installation proposée par `winget`), puis démarrage attendu ;
  - carte NVIDIA : `nvidia-smi` sur l'hôte, puis dans l'image Ollama avec `--gpus all`, pour prouver que Docker y accède ;
  - choix du LLM selon la mémoire vidéo ;
  - `.env` (conservé s'il existe) et `LAN_ADDRESS` ;
  - construction et démarrage, modèles, raccourcis du Bureau, assistant.

  Autres actions : `start`, `stop` (jamais `down`), `update`, `status`.
- **Assistant `/bienvenue`** : services, carte graphique, modèles manquants téléchargés sur place, accès réseau (facultatif), premier import. L'accueil y redirige une seule fois (`/settings/onboarding`). Une bibliothèque qui a déjà des vidéos ne le voit jamais.
- **Vérifié** : `install -NoShortcuts -NoBrowser` exécuté sur ce poste (RTX 5080) a détecté la carte et l'accès de Docker, complété `.env`, reconstruit la pile, appliqué la migration 0013 et vérifié les modèles. `status` affiche les services avec leurs accents.
