# Transcription par lots (écartée) et catalogue des modèles Ollama

| | |
|---|---|
| Statut | Mesuré (2026-09-25) : transcription par lots écartée, catalogue livré |
| Origine | Axes d'amélioration : rapidité de la transcription ; téléchargement de modèles sans saisie à l'aveugle |
| Migration | aucune |

## Transcription Whisper par lots : écartée

La piste : utiliser `BatchedInferencePipeline` de faster-whisper à la place du décodage segment par segment.

**Vitesse et mémoire**, mesurées sur une RTX 5080 avec qwen3:8b chargé à côté (10 min d'une réunion en français) :

| Mode | Durée | Mémoire vidéo au pic |
|---|---|---|
| Séquentiel (actuel) | 7,7 s | 14,2 Go |
| Par lots de 8 | 2,4 s | 14,9 Go |
| Par lots de 16 | 2,5 s | 15,8 Go |

Le mode par lots accepte le vocabulaire (`initial_prompt`) et la confiance par mot (`word_timestamps`). Ses segments suivent les morceaux de la VAD (environ 35 s). Ils ont été redécoupés en phrases grâce aux horodatages des mots.

**Pourquoi il est écarté : il perd des phrases entières.** Mesure sur les fichiers entiers du corpus, en comparant avec la transcription séquentielle :

| Mode | Podcast 59 min | Réunion 51 min |
|---|---|---|
| Par lots, réglage par défaut | 19 s, **351 mots perdus** | 12 s, **100 mots perdus** |
| Par lots, avec horodatages (`without_timestamps=False`) | 42 s, **395 mots perdus** | 26 s, **176 mots perdus** |

- Les passages perdus sont du vrai contenu : citations (« …the ownership and control of Greenland is an absolute necessity »), présentation d'une émission (« I'm Amy Goodman. We look now at… »).
- Aucun réglage n'a corrigé le problème : seuils désactivés, VAD plus souple, morceaux de 15 s. Avec les horodatages, une phrase revient sur un extrait isolé, mais le fichier entier perd davantage et le gain de vitesse disparaît.
- L'évaluation de qualité est passée de 56,8 % à 53,8 %. Ce recul-là vient du résumé et non de la transcription : les sujets manquants apparaissaient autant de fois dans les deux transcriptions.

Pour un outil de transcription, perdre des phrases sans le dire coûte plus que la vitesse ne rapporte. **La transcription reste séquentielle.** À retester si une version de faster-whisper corrige cette perte, en refaisant la comparaison mot à mot sur le corpus.

### Problème connu, non traité (décision du 2026-09-26)

**Cause probable, non vérifiée dans le code de faster-whisper.** Le mode par lots décode chaque morceau de parole (30 s au plus) en une seule passe. Quand le modèle croit le morceau terminé trop tôt, par exemple après une musique, une citation ou un changement de voix, la fin du morceau est perdue. Le mode séquentiel, lui, repart après le dernier mot reconnu, et recommence quand le résultat est douteux.

**Correction envisagée, non réalisée : les lots, puis un rattrapage ciblé.**

1. Pour chaque morceau, comparer sa durée de parole (VAD) à la fin du dernier mot reconnu.
2. S'il reste plus de 2 à 3 s de parole sans texte, retranscrire ce morceau en mode séquentiel.
3. Garder la version la plus complète.

Gain espéré : environ 2 fois plus rapide que le séquentiel, au lieu de 3 fois.

**Critère de réussite** : presque aucun mot perdu, sur les trois fichiers du corpus, dans la comparaison mot à mot avec la transcription séquentielle. Puis aucun recul à l'évaluation de qualité.

**Pourquoi ce n'est pas fait** : le gain est d'environ 30 s par heure de média, alors que le modèle de langage prend au moins autant de temps. Ça ne vaut que pour de gros volumes de vidéos.

Le chargement du modèle Whisper coûte 2,5 s par vidéo : un service qui le garderait en mémoire ne vaut pas sa complexité.

## Catalogue des modèles Ollama

**Avant** : un champ de texte où l'on tapait un nom, sans savoir si le modèle existait encore.

**Maintenant** (`app/ollama_catalog.py`, `/models/catalog`, `OllamaCatalog.tsx`, page Modèles) :

- **Liste** : lue sur `ollama.com/library`. Ollama ne documente aucune API pour son catalogue, et `ollama.com/api/tags` ne renvoie que les modèles du cloud.
  - Chaque modèle vient avec sa description, ses capacités (outils, raisonnement, images, embeddings), ses tailles et son nombre de téléchargements.
  - Les modèles servis uniquement par le cloud d'Ollama sont exclus, car Sténo fait tout tourner sur l'ordinateur.
  - Recherche par mots et filtre par capacité.
  - Mesuré : 226 modèles proposés, les modèles installés sont marqués.
- **Variantes** : lues sur `ollama.com/library/<modèle>/tags`, avec pour chacune la taille, le contexte et le type d'entrée.
  - La mémoire vidéo nécessaire au contexte de 32 000 jetons de Sténo est estimée à : taille du fichier × 1,3 + 4 Go. qwen3:8b donne 11,05 Go, contre 11,3 Go mesurés.
  - Le résultat est comparé à la mémoire de la carte, moins 2 Go de réserve, pour afficher « tient dans la carte » ou « trop grand ».
- **Mise à jour** : le catalogue est relu toutes les 6 heures (cache Redis) et peut être actualisé à la main. Un modèle retiré d'ollama.com disparaît de la liste.
  - Sans réseau, la dernière lecture reste affichée, signalée comme ancienne.
  - Si la page change de mise en page, l'erreur est affichée au lieu d'une liste vide.
- **Contrôle avant téléchargement** : tout nom est vérifié dans le registre d'Ollama (`registry.ollama.ai`). Un modèle absent, retiré ou mal saisi est refusé tout de suite (422), au lieu d'un téléchargement qui échoue. Si le registre ne répond pas, Ollama tranche lui-même.
- **Tests** : des extraits réels des deux pages (`tests/ollama_library.html`, `tests/ollama_qwen3_tags.html`). Si ollama.com change de mise en page, ces tests continueront de passer mais l'interface affichera l'erreur « La page du catalogue d'Ollama a changé ». Il faudra alors mettre à jour les extraits et le module.
- **Données envoyées** : rien de l'utilisateur. Seules des pages publiques sont lues sur ollama.com, comme pour un téléchargement de modèle.
