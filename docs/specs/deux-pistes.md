# Deux pistes à l'enregistrement : vous d'un côté, les autres de l'autre

| | |
|---|---|
| Statut | Livré (2026-09-27) |
| Origine | Feuille de route n° 3 : phase 4 |
| Migration | `0015_sides` |

## Ce qui change pour l'utilisateur

En mode **« Les deux »** (micro + onglet) de la page Enregistrer, le navigateur garde votre micro et le son de la visioconférence sur deux pistes, au lieu de les mélanger. À l'analyse :

- vos phrases sont marquées **« Vous »** et celles de l'autre côté **« Participants »**, sans rien deviner : chaque ligne vient de sa piste ;
- avec « Distinguer plusieurs voix de chaque côté », les voix sont séparées à l'intérieur de chaque côté : « Vous », « Vous 2 » s'il y a plusieurs personnes à votre micro, puis « Participant 1 », « Participant 2 »… Les noms se changent comme avant ;
- sans casque, **l'écho** de vos interlocuteurs dans votre micro est retiré ;
- deux personnes qui parlent en même temps, une de chaque côté, sont **gardées toutes les deux**, car chaque côté est transcrit à part.

La transcription en direct ne change pas : elle écoute le mélange des deux pistes.

## Mesure (banc « voix », 2026-09-27)

Deux cas nouveaux, en stéréo comme un enregistrement « Les deux ». Claire est au micro, Pierre et Sophie de l'autre côté, avec deux passages où deux personnes parlent en même temps (`backend/bench/cases/appel*`).

| Cas | Bon côté | Lignes d'écho | Erreur de personne | Voix | Bonne personne | Mots |
|---|---|---|---|---|---|---|
| `appel` (casque) | **100 %** | **0** | 0,7 % | 3/3 | 100 % | 97,0 % |
| `appel-haut-parleurs` (l'autre côté revient dans le micro, 60 ms plus tard, au tiers de son niveau, étouffé) | **100 %** | **0** | 0,5 % | 3/3 | 100 % | 97,6 % |

Le critère de sortie (au moins 95 % des mots du bon côté, aucune ligne d'écho) est atteint.

**Ce que la mesure ne montre pas.** Sur ces deux cas, l'ancienne méthode, qui mélange les pistes puis laisse Nemotron séparer les voix, obtient aussi 100 % (`--mixed`). Nemotron y distingue très bien trois voix. Le gain des deux pistes est donc :

- une **certitude** : le côté vient de la piste, il ne dépend plus de la ressemblance des voix ;
- les libellés « Vous » et « Participants » ;
- les paroles simultanées gardées.

Ce n'est pas un meilleur score sur ce banc.

### Les deux filtres d'écho, l'un sans l'autre (`appel-haut-parleurs`)

| Filtres | Bon côté | Lignes d'écho |
|---|---|---|
| Niveau + texte (livré) | 100 % | 0 |
| Niveau seul | 100 % | 0 |
| Texte seul | 95,9 % | 1 |
| Aucun | 62,6 % | **10** (toutes les phrases de Pierre et Sophie une seconde fois, de votre côté) |

### Un écho plus fort (cas `appel` avec écho ajouté)

| Écho, par rapport à leur voix | Bon côté | Lignes d'écho | Mots de Claire retrouvés |
|---|---|---|---|
| 0,33 | 100 % | 0 | 98 % |
| 0,6 | 100 % | 0 | 98 % |
| 1,0 (haut-parleurs forts) | 100 % | 0 | 98 % |

## Comment ça marche

- **Navigateur** (`frontend/lib/recorder.ts`) : en mode « Les deux », chaque source est ramenée en mono, puis un `ChannelMergerNode` met le micro sur le canal gauche et l'onglet sur le canal droit. L'enregistrement déclare `sides` à sa création (`POST /recordings`). La vidéo créée à l'arrêt reçoit `audio_layout = "sides"`.
- **Worker** (`backend/app/sides.py`, `run_pipeline`) : si la vidéo est marquée `sides` et que le fichier est bien en stéréo, le traitement est le suivant ; sinon il reste celui d'avant.
  1. Les deux canaux sont séparés (ffmpeg `channelsplit`) dans un dossier temporaire, supprimé à la fin.
  2. **Écho, par le niveau** : une trame de 30 ms du micro compte comme votre voix si elle est au moins moitié aussi forte que le son le plus fort de l'autre côté dans les ±90 ms, et seulement par séries d'au moins 3 trames. Les deux pistes sont d'abord ramenées à un niveau de parole voisin. Le gain du micro se calcule là où l'autre côté se tait : calculé sur tout le son, l'écho d'une personne qui parle peu devenait sa « voix » (test `test_the_echo_of_someone_who_barely_speaks…`). Le reste du micro est remis à zéro avant Whisper.
  3. Chaque côté est transcrit à part, celui qui contient le plus de parole d'abord : sa langue vaut pour les deux quand elle n'est pas imposée.
     - Les minutages par mot sont toujours demandés. Sans eux, sur une piste surtout silencieuse, Whisper étirait une ligne jusqu'à la suivante (1,0 à 3,2 s dites, 0,6 à 11,3 s données).
     - Une ligne est coupée là où deux de ses mots sont séparés de plus de 1,5 s. Sinon, deux répliques à 8 s d'intervalle ne formaient qu'une ligne, placée avant ce que l'autre côté avait dit entre-temps.
  4. **Écho, par le texte** : une ligne à vous qui reprend au moins la moitié des triplets de mots de l'autre côté à ±2 s est retirée (un ou deux mots : repris mot pour mot).
  5. Les deux côtés sont fusionnés par ordre chronologique. Chaque ligne garde son côté (`transcript_segments.side`).
  6. Intervenants (`speakers.apply_sides`) :
     - un intervenant par côté ;
     - ou, avec l'identification, Nemotron sur la piste de chaque côté. Sur votre piste, seule votre voix, l'écho retiré, est écoutée.
- **Relancer l'identification** d'une vidéo à deux côtés refait la séparation depuis le fichier stéréo d'origine. Si ce fichier a été supprimé (règle « texte seul »), la tâche échoue avec un message clair ; la transcription reste intacte.

Les règles d'écho reprennent celles d'omarchy-meeting-recorder (MIT), à une différence près : le calcul du gain du micro.

## Limites

- Le son d'un onglet ne se partage que dans **Chrome et Edge**. Celui d'une application de bureau (Teams, Zoom) ne se partage qu'avec **l'écran entier et le son du système**, sous Windows. La page Enregistrer l'explique.
- **La partie navigateur n'a pas été vérifiée avec un vrai appel.** Les cas du banc sont des fichiers stéréo construits comme ceux du navigateur, et l'API a été vérifiée de bout en bout avec l'un d'eux. Un premier essai réel, « Les deux » sur une visioconférence, reste à faire.
- L'annulation d'écho du navigateur reste active sur le micro. Elle s'ajoute aux deux filtres ; son effet sur un vrai appel n'est pas mesuré ici.
- Deux personnes à votre micro ne sont séparées que si l'identification des voix est demandée.
