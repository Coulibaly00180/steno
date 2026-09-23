# Phase 4 : La mémoire — choix d'implémentation

| | |
|---|---|
| Statut | Implémenté (2026-09-23), sans spécification préalable : ce document consigne les choix faits |
| Périmètre | n°6 chat sur toute la vidéo (RAG local) · n°19 questions sur plusieurs vidéos |
| Migration | `0006_semantic_search` (tables `passages` et `video_indexes`, extension `vector`) |
| Critère de sortie (roadmap) | Une question sur la 3ᵉ heure d'une vidéo de 4 h obtient une réponse juste, avec le bon horodatage. **Vérifié** (voir « Vérification en conditions réelles ») |

## Comportement

| Évolution | Ce que voit l'utilisateur |
|---|---|
| n°6 Chat sur toute la vidéo | Pour une transcription de plus de 30 000 caractères (environ 1 h de parole), le chat ne lit plus seulement le début : il retrouve dans toute la vidéo les passages les plus proches de la question et cite leurs horodatages. Les vidéos courtes restent lues en entier, ce qui est plus fiable. Tant qu'une longue vidéo n'est pas indexée, un bandeau prévient que les réponses ne portent que sur le début. |
| n°19 Questions sur plusieurs vidéos | Nouvelle page « Questions » (menu), ouverte aussi depuis la bibliothèque avec ses filtres (« Questions sur ces vidéos »). La réponse s'écrit au fil de l'eau et cite ses sources [1], [2]… Chaque citation ouvre la vidéo au moment cité (`/videos/{id}?t=…`). La liste des passages consultés est affichée, ainsi que le nombre de vidéos pas encore indexées. La conversation n'est pas enregistrée. |

## Choix techniques

- **Modèle d'embeddings** : `bge-m3` via Ollama (1,2 Go, 1024 dimensions, multilingue), configurable par `EMBEDDING_MODEL`. La bibliothèque mêle français et anglais : mesurée, la similarité est de 0,84 entre « Le budget est validé » et « The budget was approved », contre 0,46 pour une phrase sans rapport. Le service `model-pull` télécharge désormais le LLM et ce modèle.
- **pgvector sans changer de base** : l'image `postgres/Dockerfile` repart de `postgres:17-alpine` et compile pgvector 0.8.6, sans `-march=native` et sans bitcode JIT.
  - Pourquoi pas les images `pgvector/pgvector` : elles sont basées sur Debian. Reprendre le volume existant, créé sous Alpine (musl), aurait changé la collation du texte et corrompu silencieusement les index de texte.
  - La pile de test utilise la même image.
- **Passages** (`app/retrieval.py`) :
  - Segments consécutifs regroupés jusqu'à environ 900 caractères, sans dépasser 2 minutes par passage.
  - Chaque passage reprend le dernier segment du précédent, pour qu'une réponse à cheval reste trouvable.
  - Le texte stocké porte les horodatages `[hh:mm:ss]`, que le LLM cite ; le texte envoyé au modèle d'embeddings n'en a pas.
- **Recherche exacte**, colonne `vector` sans dimension fixe et sans index approché.
  - Le calcul exact porte sur une seule vidéo, ou sur quelques dizaines de milliers de passages pour une bibliothèque.
  - Changer de modèle d'embeddings ne demande qu'une réindexation, pas de migration.
  - SQLite, utilisé seulement par les tests unitaires, fait le même calcul avec numpy.
- **État de l'index** (`video_indexes`) : READY, STALE ou FAILED, avec le modèle et l'empreinte de la transcription.
  - Une correction de transcription marque l'index STALE et met une réindexation en file.
  - Un job d'indexation n'enregistre rien si la transcription a changé pendant son calcul.
- **Quand indexer** :
  - À la fin de chaque traitement complet (étape « INDEXING », 6 s pour 4 h sur GPU). Un échec n'empêche pas la vidéo d'être « Terminé » : le chat revient alors à l'ancien mode.
  - Au démarrage du worker, rattrapage des vidéos traitées avant cette phase, ou dont l'index manque, est périmé ou provient d'un autre modèle.
  - Les jobs `INDEX` passent par une **file secondaire** (`video-ai-index`), que le worker ne sert que lorsque la file principale est vide. Une indexation de rattrapage ne retarde donc jamais une analyse, et les estimations de la phase 3 en tiennent compte.
  - Ces jobs ne sont jamais présentés comme « le traitement de la vidéo » : ils ne bloquent ni les corrections, ni les régénérations, ni la suppression.
- **Chat d'une vidéo** : contexte = dernier résumé + 12 passages au plus, fusionnés quand ils se chevauchent et remis dans l'ordre chronologique, avec la consigne de dire si la réponse n'y figure pas. Une relance courte (« et ensuite ? ») est recherchée avec la question précédente.
- **Questions sur plusieurs vidéos** : `POST /library/chat/stream` reçoit les identifiants des vidéos en périmètre (500 au plus).
  - 12 passages au plus, dont 4 au plus par vidéo, pour ne pas laisser une seule vidéo occuper toute la réponse.
  - Les passages sont numérotés avec le titre de leur vidéo et leur plage horaire. L'événement `sources` précède la réponse.
- **Affichage** : le gras Markdown (`**…**`) des réponses est rendu ; un horodatage mis en gras reste un bouton de lecture.

## API ajoutée ou modifiée

| Route | Rôle |
|---|---|
| `POST /library/chat/stream` | Question sur plusieurs vidéos (SSE) : `sources` {sources, searched, skipped}, puis `delta`, puis `done` {answer} ou `error` |
| `GET /videos/{id}` | Ajoute `chat_mode` : `full`, `passages`, `partial` ou `none` |
| `POST /videos/{id}/chat/…` | Utilise les passages pour une longue transcription indexée |
| `GET /status` | Ajoute le service `embedding` (modèle présent ou non) |

## Vérifications

- 219 tests dans la pile Docker :
  - découpage en passages ;
  - indexation, correction pendant l'indexation, échec sans conséquence sur la vidéo ;
  - rattrapage ; priorité de la file d'indexation ;
  - jobs d'indexation invisibles et non bloquants ;
  - mode du chat, contexte d'une question sur la 3ᵉ heure ;
  - questions sur plusieurs vidéos (numérotation, plafond par vidéo, vidéos non indexées).
- Test d'intégration sur PostgreSQL avec pgvector (tri par distance cosinus en SQL).
- Les tests unitaires n'appellent jamais Ollama : ils utilisent des embeddings factices déterministes (sac de mots haché).

## Vérification en conditions réelles (2026-09-23, pile Docker GPU)

- **Migration de la vraie base** :
  - sauvegarde, puis passage à l'image avec pgvector sur le même volume ;
  - données intactes : 1 vidéo, 198 segments, 1 résumé ; la recherche plein texte fonctionne toujours ;
  - la vidéo existante a été indexée automatiquement au démarrage (18 passages, 7,5 s).
- **Critère de sortie** : fichier de 4 h 10 (conférence de 3 h 11, puis podcast de 59 min).
  - Traitement complet : 7 min 28 s, dont 6 s d'indexation.
  - La 3ᵉ heure (2:00–3:00) couvre une attaque par injection de fautes sur un traceur Bluetooth.
  - Questions portant sur des détails absents du résumé, avec un historique vide :

| Question | Ancien mode (début de la transcription) | Nouveau mode (passages) |
|---|---|---|
| Quel mot allemand décrit le montage de test ? | « Ce n'est pas mentionné dans la vidéo » | « Kabel Salat », [02:12:13] (exact) |
| Comment ont-ils obtenu les registres sans fichier SVD ? | « Ce n'est pas mentionné dans la vidéo » | En analysant le PDF de la fiche technique, script Ghidra, [02:13:45] (exact) |
| Quelle puce a été attaquée, dans quel appareil ? | Réponse juste (le fait figure dans le résumé) | DA14580 de Dialog, dans un Chipolo, [02:03:13]–[02:27:19] |

  - Première portion de réponse en 1,5 à 2,5 s sur GPU ; les 5,5 s de la toute première question comprennent le chargement de `bge-m3`.
- **Questions sur plusieurs vidéos** (ta vidéo et la vidéo de test) :
  - la réponse combine les deux vidéos, avec des citations vers 02:12:07 de l'une et 06:03 de l'autre ;
  - un clic sur une citation ouvre la bonne vidéo, lecteur positionné à 512 s pour la source [4].
- La vidéo de test et les fichiers temporaires ont été supprimés.

Écueil de méthode noté pendant la vérification : une première comparaison était faussée, parce que l'historique du chat (renvoyé au modèle) contenait déjà la bonne réponse. La comparaison ci-dessus a été refaite avec un historique vide.

## Limites connues

- Un passage retrouvé n'est utile que si la transcription est juste. Par exemple, « Doñana » transcrit « d'Oriana » ne sera pas retrouvé sous son vrai nom.
- La recherche est purement sémantique. Un nom propre rare peut être mieux trouvé par la recherche plein texte de la bibliothèque (phase 3) que par une question.
- Les conversations sur plusieurs vidéos ne sont pas enregistrées.
- Mémoire : `bge-m3` ajoute environ 1,2 Go (VRAM ou RAM) à côté du LLM. Sur GPU (16 Go), les deux tiennent avec Whisper libéré après la transcription. Sur une machine sans GPU, le calcul des embeddings est plus lent, mais il passe par la file secondaire.
- La durée des jobs d'indexation entre dans les estimations de la phase 3. Les mesures faites pendant la vérification (4 h 10 de test) sont conservées dans `job_durations`, car elles améliorent les estimations pour les fichiers longs.
