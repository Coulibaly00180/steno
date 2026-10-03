# Extension « Envoyer à Sténo »

Envoie la vidéo ou le podcast de l'onglet ouvert à votre Sténo, en un clic. Chrome, Edge (Manifest V3) et Firefox 128 ou plus. Spécification : `docs/specs/envoyer-a-steno.md`.

## Installer

L'extension n'est pas publiée sur les magasins : elle se charge depuis ce dossier.

- **Chrome** : `chrome://extensions` › activer le « Mode développeur » › « Charger l'extension non empaquetée » › choisir ce dossier `extension`.
- **Edge** : `edge://extensions` › « Mode développeur » › « Charger l'extension décompressée » › ce dossier.
- **Firefox** : `about:debugging#/runtime/this-firefox` › « Charger un module complémentaire temporaire… » › `manifest.json`. Firefox l'oublie à chaque redémarrage (une extension permanente doit être signée par Mozilla).

À l'installation, la page d'options s'ouvre :

1. **Adresse de Sténo** : `http://127.0.0.1:3000` sur l'ordinateur de Sténo ; depuis un autre ordinateur, l'adresse HTTPS du réseau local (`https://192.168.1.20:8443`, voir `docs/acces-reseau.md`). En HTTPS, l'autorité locale de Sténo doit être installée sur cet ordinateur.
2. **Jeton d'accès** : à créer dans Sténo, Paramètres › Accès et sécurité › Jetons d'accès. Nécessaire depuis le réseau, ou si le mot de passe est demandé sur l'ordinateur de Sténo.
3. Cocher la confirmation des droits, puis « Enregistrer et tester ». Le navigateur demande l'autorisation d'accéder à cette adresse seulement.

## Utiliser

- **Clic sur l'icône** (ou Alt+Maj+S) : l'onglet part dans la file de Sténo, avec les options par défaut réglées dans les options de l'extension.
- **Clic droit sur l'icône ou sur la page** › « Envoyer à Sténo avec des options… » : choisir le template et les intervenants, puis « Envoyer ».
- **Clic droit sur un lien** › « Envoyer ce lien à Sténo ».
- Une notification confirme l'envoi, puis signale la fin de l'analyse ; un clic dessus ouvre la vidéo dans Sténo.

Les pages YouTube et autres plateformes vidéo demandent l'option de Sténo « Paramètres › Import de liens ». Un flux de podcast ouvre Sténo pour choisir les épisodes.

## Ce que fait l'extension, et ce qu'elle ne fait pas

- Elle ne lit pas les pages visitées : aucun script dans les pages, seulement l'adresse et le titre de l'onglet au moment du clic.
- Elle ne contacte que l'adresse de Sténo réglée, sous `/api/` (`stenoFetch` dans `lib.js`), sans cookie et sans suivre de redirection. L'autorisation d'hôte n'est accordée que pour cette adresse ; changer d'adresse retire la précédente.
- Le jeton reste dans le stockage local du navigateur, jamais dans le stockage synchronisé.

## Tests

`node --test extension/tests/lib.test.js`, lancé dans Docker par `docker compose -f compose.test.yaml run --rm frontend-tests`.
