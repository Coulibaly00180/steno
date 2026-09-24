# Installer Sténo sous Windows

Le lanceur `windows/steno.ps1` installe et pilote Sténo sans taper de commande Docker. Il faut Windows 10 ou 11 (64 bits), 16 Go de mémoire et 40 Go d'espace libre. Une carte NVIDIA est recommandée, mais pas obligatoire.

## Première installation

1. Téléchargez le dossier de Sténo et placez-le à un endroit définitif (par exemple `Documents\Sténo`).
2. Double-cliquez sur **`Installer Steno.cmd`**.

Le lanceur fait, dans l'ordre :

| Étape | Ce qui se passe |
|---|---|
| Docker Desktop | Le lanceur vérifie que Docker est installé et démarré. S'il est absent, il propose de l'installer avec `winget`, sinon il ouvre la page de téléchargement. Il faut ensuite redémarrer l'ordinateur, ouvrir Docker Desktop une fois, puis relancer l'installation. |
| Carte graphique | Le lanceur cherche une carte NVIDIA (`nvidia-smi`) et vérifie que Docker y accède. Avec 10 Go de mémoire vidéo ou plus, il choisit `qwen3:8b`, en dessous `qwen3:4b`. Sans carte utilisable, tout tourne sur le processeur (`qwen3:4b`, Whisper `small`). |
| Configuration | Le lanceur crée `.env` s'il n'existe pas (un `.env` existant est conservé) et note l'adresse de l'ordinateur sur le réseau local (`LAN_ADDRESS`). Il enregistre ses choix dans `.steno-launcher.json`. |
| Construction | `docker compose … up -d --build`, avec la surcouche GPU si la carte est utilisable. Comptez 10 à 20 minutes la première fois. |
| Modèles | Le lanceur télécharge le modèle de langage et celui de la recherche (plusieurs Go). Le modèle de transcription se télécharge à la première analyse. |
| Raccourcis | Il crée « Sténo » (démarrer et ouvrir) et « Arrêter Sténo » sur le Bureau. |
| Assistant | Le navigateur s'ouvre sur `/bienvenue`. |

L'**assistant de premier lancement** (`/bienvenue`) vérifie les services, la carte graphique et les modèles. Un bouton télécharge ceux qui manquent. L'assistant propose ensuite de régler l'accès depuis le réseau (facultatif), puis mène au premier import. Il ne s'affiche qu'une fois, et jamais sur une installation qui a déjà des vidéos.

## Au quotidien

| Action | Comment |
|---|---|
| Démarrer | Raccourci « Sténo » : démarre Docker Desktop si besoin, puis Sténo, et ouvre http://127.0.0.1:3000 |
| Arrêter | Raccourci « Arrêter Sténo » : `docker compose stop`. Les données restent en place. |
| Mettre à jour | `powershell -ExecutionPolicy Bypass -File windows\steno.ps1 update` : `git pull`, reconstruction, modèles manquants. |
| Diagnostiquer | `powershell -ExecutionPolicy Bypass -File windows\steno.ps1 status` : Docker, carte graphique, services, état de chaque composant. |

Options :

- `-Cpu` ignore la carte graphique ;
- `-NoShortcuts` ne crée pas les raccourcis ;
- `-NoBrowser` n'ouvre pas le navigateur.

## Détails techniques

- Le script est enregistré en UTF-8 avec BOM : Windows PowerShell 5.1 lit sinon les accents de travers. `.gitattributes` impose les fins de ligne CRLF pour `*.ps1` et `*.cmd`.
- Le lanceur écrit `.env` en UTF-8 sans BOM, car Docker Compose lirait le BOM dans le nom de la première variable.
- Il lit la réponse de `/status` comme de l'UTF-8 : PowerShell 5.1 la lirait sinon en Latin-1.
- Pour vérifier l'accès de Docker à la carte, il lance `nvidia-smi` dans l'image Ollama, déjà nécessaire à Sténo. Aucune image supplémentaire n'est téléchargée.
- « Arrêter » utilise `docker compose stop`, jamais `down` : les conteneurs et les volumes restent intacts.
