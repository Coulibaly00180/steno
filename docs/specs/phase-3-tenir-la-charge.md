# Phase 3 : Tenir la charge — choix d'implémentation

| | |
|---|---|
| Statut | Implémenté (2026-09-23), sans spécification préalable : ce document consigne les choix faits |
| Périmètre | n°13 position dans la file et temps restant · n°14 annuler un traitement · n°18 import par lots · n°16 chat en streaming · n°17 bibliothèque (recherche plein texte, filtres, tags) |
| Migration | `0005_queue_library` |
| Critère de sortie (roadmap) | 20 fichiers importés d'un coup ; chacun affiche sa position et son temps estimé et peut être annulé ; la bibliothèque s'affiche en un seul appel. **Vérifié** (voir « Vérification en conditions réelles ») |

## Comportement

| Évolution | Ce que voit l'utilisateur |
|---|---|
| n°13 File et temps restant | Bibliothèque, accueil et page vidéo : « 3ᵉ dans la file · fin dans ~4 min », puis « Fin estimée dans ~2 min » pendant le traitement, puis « Bientôt terminé » si l'estimation est dépassée. Tant qu'aucun traitement du même type n'est terminé, seule la position s'affiche (« estimation après un premier traitement »). La liste se rafraîchit toute seule toutes les 4 s tant qu'un traitement est actif. |
| n°14 Annuler | Bouton « Annuler » dans la carte de progression, et « Annuler le traitement » dans le menu de chaque ligne. Une confirmation est demandée pour un traitement en cours ; un traitement en attente est annulé directement. La vidéo passe à « Annulé » et peut être relancée ou supprimée. Une régénération annulée laisse la vidéo « Terminé » avec son résumé précédent. Une vidéo en attente peut être supprimée directement ; une vidéo en cours de traitement doit d'abord être annulée. |
| n°18 Import par lots | Sélection ou dépôt de plusieurs fichiers (50 au plus), avec les mêmes réglages pour tous. Les envois se font l'un après l'autre et l'état de chaque fichier s'affiche (« Envoi… », « Ajouté à la file », message d'erreur). Un fichier refusé n'arrête pas les autres. Avec un seul fichier, le comportement ne change pas : ouverture de la page de la vidéo. |
| n°16 Chat en streaming | La réponse s'affiche au fil de l'écriture. Le rafraîchissement toutes les 5 s est supprimé. En cas d'échec, la question revient dans le champ de saisie et rien n'est enregistré. |
| n°17 Bibliothèque | Recherche dans les titres, les transcriptions et les traductions, insensible aux accents et par début de mot (« prévision » trouve « prévisionnel »), avec tri par pertinence. Filtres : statut, langue parlée, période d'import, tag. Tags libres ajoutés depuis la page vidéo, avec suggestions ; un clic sur un tag dans la liste filtre dessus. L'accueil et la bibliothèque se chargent en un seul appel (auparavant : un appel `/videos/{id}` par ligne). |

## Choix techniques

- **Estimations** (`backend/app/queue_info.py`) :
  - La durée de chaque traitement terminé est enregistrée dans `job_durations`, avec le type (FULL ou SUMMARY), la durée du média et la présence d'une traduction. Ces lignes survivent à la suppression de la vidéo, et la migration reprend les traitements déjà terminés.
  - Le modèle est une droite, temps ≈ fixe + pente × durée du média, calculée par moindres carrés sur les 30 derniers traitements du même type. Avec moins de 3 points, ou une pente incohérente, on utilise un ratio médian.
  - Justification : un simple ratio se trompe aux deux extrêmes. Sur le corpus GPU, un clip de 3 min prend 30 s et une conférence de 3 h, 6 min 30.
  - Un seul worker traite les jobs dans leur ordre de création. L'attente d'un job en file est donc le temps restant du job en cours plus la durée estimée des jobs placés avant lui.
- **Annulation** :
  - L'API passe le job à `CANCELLED` et, pour un job FULL, la vidéo aussi. Elle envoie ensuite `send_stop_job_command` à RQ pour un job en cours, qui arrête immédiatement le processus de travail et ffmpeg ; Ollama s'interrompt quand la connexion se ferme. Pour un job en attente, elle appelle `Job.cancel()`.
  - Filet de sécurité si la commande se perd : le worker lève `JobCancelled` à sa prochaine mise à jour de progression, puis revérifie le statut avant d'enregistrer la transcription ou le résumé. Un job annulé n'est jamais enregistré comme un échec.
- **Recherche plein texte** : colonne générée `videos.search_vector` avec un index GIN, **uniquement sur PostgreSQL**. Elle ne figure pas dans les modèles ORM, et `app.schema.include_object` l'exclut de la comparaison modèles/migrations.
  - Configuration `simple` : pas de racinisation, car la bibliothèque mélange les langues.
  - Accents ignorés grâce à `unaccent`, enveloppé dans une fonction `IMMUTABLE`, condition nécessaire pour une colonne générée.
  - Les horodatages `[hh:mm:ss]` sont retirés du texte indexé : sinon, tout nombre (« 07 », « 2024 ») trouvait toutes les transcriptions.
  - La requête ne garde que les mots de la saisie (`\w+`, 8 au plus), chacun comme préfixe. Aucune syntaxe utilisateur n'atteint `to_tsquery`.
  - Avec SQLite (tests unitaires seulement), la recherche se fait par sous-chaîne.
- **Tags** : tables `tags` (nom unique sans tenir compte de la casse) et `video_tags`. `PUT /videos/{id}/tags` remplace l'ensemble des tags de la vidéo. La première graphie d'un tag est conservée. Les tags qui ne sont plus utilisés sont supprimés. Limites : 40 caractères par tag, 20 tags par vidéo.
- **Chat en streaming** : `POST /videos/{id}/chat/stream` renvoie des Server-Sent Events. Ce flux passe par un POST, car `EventSource` ne sait faire que des GET ; le navigateur le lit donc avec `fetch` et un `ReadableStream`. Les deux messages ne sont enregistrés qu'une fois la réponse complète. Si l'utilisateur quitte la page pendant la réponse, la génération s'arrête et rien n'est enregistré ; l'ancienne route `POST /chat/messages` reste disponible.
- **Liste de la bibliothèque** : `load_only` exclut les transcriptions (environ 400 ko pour 6 h). Le dernier job de chaque vidéo est obtenu par une seule requête groupée, et les tags par `selectinload`.

## API ajoutée ou modifiée

| Route | Rôle |
|---|---|
| `GET /videos?q=&status=&language=&tag=&created_after=&created_before=&limit=` | Bibliothèque filtrée, avec `tags` et le dernier `job` (position, estimation). `status` ∈ `ACTIVE`, `COMPLETED`, `FAILED`, `CANCELLED` |
| `POST /jobs/{id}/cancel` | Annule un job en attente ou en cours (409 si terminé) |
| `GET /jobs/{id}`, `GET /videos/{id}`, `GET /jobs/{id}/events` | Ajoutent `queue_position` et `estimated_seconds_remaining` pour un job actif |
| `POST /videos/{id}/retry` | Accepte aussi une vidéo annulée |
| `DELETE /videos/{id}` | Accepte une vidéo en attente (son job est abandonné) ; 409 pour un traitement en cours |
| `POST /videos/{id}/chat/stream` | Réponse en SSE : `delta`, puis `done` ou `error` |
| `GET /tags`, `PUT /videos/{id}/tags` | Tags et nombre de vidéos, remplacement des tags d'une vidéo |

## Vérifications

- 198 tests dans la pile Docker `compose.test.yaml`, dont 26 nouveaux :
  - modèle de durée et file (positions, cumul, sans historique, dépassement, traduction) ;
  - annulation en attente, en cours et d'une régénération ; filet de sécurité du worker ;
  - suppression d'une vidéo en attente ; relance d'une vidéo annulée ;
  - filtres de la bibliothèque et tags ;
  - streaming, avec les cas d'échec et de vidéo sans transcription.
- Recherche plein texte testée sur PostgreSQL 17 : accents, préfixes, traduction, opérateurs neutralisés, horodatages non indexés.

## Vérification en conditions réelles (2026-09-23, pile Docker GPU)

20 médias de synthèse de 8 à 47 s (espeak-ng), importés en un seul lot depuis l'interface puis supprimés :

- **Import** : les 20 envois sont acceptés, avec le message « 20 analyses ajoutées à la file ».
- **File** : chaque ligne affiche sa position (« 19ᵉ dans la file · fin dans ~3 min »). La bibliothèque se charge en un seul appel `/api/videos`.
- **Annulation** :
  - un job en attente, depuis le menu d'une ligne ;
  - un job en cours pendant la transcription, depuis sa page : processus arrêté par RQ dans la seconde, puis vidéo « Annulé » avec « Relancer » et « Supprimer ». La file a continué normalement.
- **Streaming** : première portion après 0,38 s ; 135 portions en 1 s environ, aussi bien sur l'API que via le proxy Next : aucune mise en tampon.
- **Recherche et tags** : « reunion prevision » trouve la bonne vidéo. Un clic sur un tag filtre la liste, et les tags orphelins disparaissent avec la vidéo supprimée.

Défauts trouvés et corrigés à cette occasion :

| Défaut | Correction |
|---|---|
| Aucun fichier ajouté quand plusieurs étaient choisis : la `FileList` est vidée par la remise à zéro du champ avant l'exécution de la mise à jour React | Copie de la liste avant la remise à zéro |
| Tout nombre trouvait toutes les transcriptions (horodatages indexés) | Horodatages retirés de la colonne indexée (migration 0005 corrigée et réappliquée) |
| « Résumé en cours… » affiché sur une vidéo annulée | Message selon l'état : annulé, échec, en cours |

## Limites connues

- **Estimations** : elles apprennent sur la machine et n'extrapolent pas bien.
  - Après les 18 clips du test (4 à 26 s de traitement), le modèle donne 2 s + 0,29 s par seconde de média : juste pour des clips, mais environ 55 min pour une conférence de 3 h qui en prend 6 min 30 sur GPU.
  - Elles se corrigent dès que des contenus longs ont été traités.
  - Le calcul suppose un seul worker.
- Une réponse du chat interrompue (page quittée) n'est pas enregistrée.
- La recherche porte sur les titres, les transcriptions et les traductions, pas sur les résumés, qui dérivent de la transcription. Aucun extrait n'est encore affiché dans les résultats ; la recherche sémantique relève de la phase 4.
- Import par lots : pas de barre de progression par octet pendant l'envoi d'un fichier volumineux ; l'état affiché reste « Envoi… ».
