# Ouvrir Sténo aux autres appareils du réseau local

Par défaut, Sténo n'écoute que sur l'ordinateur où il est installé (`127.0.0.1`). Pour l'utiliser depuis un téléphone ou un autre ordinateur du réseau local :

1. **Définissez un mot de passe** dans Paramètres › Accès et sécurité. Sans mot de passe, l'API refuse toutes les requêtes venant du réseau (erreur 403).
2. **Démarrez le proxy HTTPS** :

   ```sh
   docker compose -f compose.yaml -f compose.gpu.yaml --profile reseau up -d
   ```

3. Depuis l'autre appareil, ouvrez `https://<LAN_ADDRESS>:8443`. L'installateur Windows note cette adresse dans `.env`. Sinon, renseignez `LAN_ADDRESS` à la main (et `HTTPS_PORT` pour changer le port).
4. Le navigateur signale un certificat inconnu : il vient de l'**autorité locale** de Sténo. Téléchargez-la depuis Paramètres › Accès et sécurité et installez-la une fois sur chaque appareil.

Pour refermer l'accès : `docker compose --profile reseau rm -sf https`, ou supprimez le mot de passe.

## Fonctionnement

- Le service `https` (Caddy, `caddy/Caddyfile`) chiffre la connexion avec des certificats de sa propre autorité (`tls internal`). Aucun service extérieur n'est contacté. L'autorité et les certificats sont dans `data/https/`.
- Caddy transmet chaque requête au service `web` en ajoutant l'en-tête `X-Steno-Remote: 1`. Il remplace tout en-tête du même nom envoyé par le navigateur.
- L'API (`AccessGuard` dans `main.py`, règles dans `app/auth.py`) décide ainsi :

  | Requête | Pas de mot de passe | Mot de passe défini |
  |---|---|---|
  | De cet ordinateur | acceptée | acceptée, sauf si « le demander aussi sur cet ordinateur » est coché |
  | Du réseau (marquée) | refusée (403) | acceptée avec une session valide, sinon 401 |

  `/health`, `/ready`, `/auth/*` et `/network/certificate` restent accessibles, pour se connecter et installer l'autorité.
- **Mot de passe** : haché avec scrypt (N = 2¹⁴, r = 8, p = 1, sel aléatoire), dans la table `app_settings` (clé `access`, jamais renvoyée par l'API).
- **Session** : cookie `steno_session`, `HttpOnly`, `SameSite=Lax`, et `Secure` derrière HTTPS. Il dure 30 jours et est signé en HMAC-SHA256 avec une clé tirée au hasard. Il porte la version du mot de passe : changer le mot de passe ferme toutes les sessions ouvertes avant.
- **Essais limités** : 10 tentatives par appareil (adresse IP transmise par le proxy) toutes les 15 minutes, comptées dans Redis. Au-delà, l'API répond 429.
- **Changer ou supprimer le mot de passe** : sur l'ordinateur de Sténo, le mot de passe actuel n'est pas demandé, car qui est devant l'ordinateur en est le propriétaire. Il l'est depuis le réseau, ou quand le mot de passe est aussi demandé sur l'ordinateur. Le premier mot de passe ne peut pas être défini depuis le réseau.

## Limites

- Le port 8443 ne doit pas être ouvert vers Internet. Le proxy est prévu pour un réseau local de confiance.
- Il n'y a qu'un compte : toute personne qui connaît le mot de passe voit toute la bibliothèque.
- Les ports 3000 (interface) et 8000 (API) restent liés à `127.0.0.1`. Les publier sur le réseau contournerait le proxy, et donc le mot de passe.
