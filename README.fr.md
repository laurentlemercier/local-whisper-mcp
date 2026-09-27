# local-whisper-mcp

[English](README.md) | [Français](README.fr.md)

Service de transcription vocale **local**, en un seul processus : API REST FastAPI et
serveur MCP Streamable HTTP partagent le même `JobManager`, la même file d'attente et le
même cache de modèles Whisper.

Aucun appel à une API cloud. L'audio est téléchargé, transcrit puis les métadonnées de
job conservées sur le disque local.

Copyright (C) 2026 Laurent Lemercier. Sous licence [GNU AGPL v3.0](LICENSE).

---

## Sommaire

- [Fonctionnalités](#fonctionnalités)
- [Prérequis](#prérequis)
- [Installation](#installation)
- [Configuration](#configuration)
- [Démarrage](#démarrage)
- [API REST](#api-rest)
- [Outils MCP](#outils-mcp)
- [Modèles](#modèles)
- [Formats audio acceptés](#formats-audio-acceptés)
- [Docker](#docker)
- [Tests](#tests)
- [Sécurité](#sécurité)
- [Structure du projet](#structure-du-projet)
- [Limites connues](#limites-connues)
- [Licence](#licence)

---

## Fonctionnalités

- **Soumission asynchrone** : chaque transcription renvoie immédiatement un `job_id`.
  L'appel REST répond `202 Accepted` ; le client interroge l'état ensuite.
- **Trois sources d'audio** : URL distante, téléversement multipart, ou Base64.
- **Deux interfaces** : REST et MCP Streamable HTTP, sur le même port, sans second processus.
- **Webhooks** : à la fin d'un job (succès ou échec), le service POST l'état du job vers
  une URL de rappel.
- **Métriques de transcription** : durée de l'audio, durée de traitement, et facteur
  de temps réel (`real_time_factor`).
- **Protection SSRF** : les URL de téléchargement et de rappel sont validées, y compris
  à chaque redirection.
- **Persistance des jobs** sur disque, relisible après redémarrage.

## Prérequis

- **Python 3.11** ou supérieur.
- **FFmpeg** n'est requis que pour les conteneurs Docker. En local, la durée de l'audio
  est déterminée par [PyAV](https://github.com/PyAV-Org/PyAV) (bibliothèque native
  embarquée) puis, en repli, par [Mutagen](https://mutagen.readthedocs.io/) — aucun
  binaire externe n'est installé.
- **Espace disque** : les poids des modèles sont téléchargés au premier usage
  (environ 500 Mo pour `small`, environ 1,5 Go pour `medium`).

## Installation

```bash
git clone https://github.com/laurentlemercier/local-whisper-mcp.git
cd local-whisper-mcp

python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1

# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

## Configuration

Toutes les variables sont lues depuis **l'environnement du processus** au démarrage
(`app/config.py`). L'application **ne charge pas de fichier `.env`** : le fichier
`.env.example` de ce dépôt sert de référence, il n'est pas appliqué automatiquement.

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `API_TOKEN` | *(vide)* | Jeton Bearer exigé par REST et MCP. **Vide = aucune authentification.** |
| `DATA_DIR` | `./data` | Racine des données : entrées audio et état des jobs. |
| `HOST` | `0.0.0.0` | Interface d'écoute. |
| `PORT` | `8000` | Port d'écoute. |
| `WHISPER_DEVICE` | `cpu` | Périphérique passé à `faster-whisper` (`cpu`, `cuda`, `auto`). |
| `WHISPER_COMPUTE_TYPE` | `int8` | Quantification (`int8`, `float16`, `float32`). |
| `MAX_INPUT_SIZE` | `524288000` | Taille maximale d'un fichier audio, en octets (500 Mio). |
| `HTTP_TIMEOUT` | `60` | Délai maximal pour télécharger l'audio, en secondes. |
| `CALLBACK_TIMEOUT` | `30` | Délai maximal du POST de rappel, en secondes. |
| `ALLOW_PRIVATE_URLS` | `false` | Autoriser les URL d'audio pointant vers des IP privées. |
| `ALLOWED_URL_HOSTS` | *(vide)* | Liste blanche d'hôtes autorisés pour l'audio, séparés par des virgules. |
| `ALLOW_PRIVATE_CALLBACKS` | `false` | Autoriser les URL de rappel pointant vers des IP privées. |
| `ALLOWED_CALLBACK_HOSTS` | *(vide)* | Liste blanche d'hôtes autorisés pour les rappels. |

## Démarrage

```bash
# Définir un jeton, puis lancer
export API_TOKEN="un-jeton-long-aleatoire"     # Linux / macOS
$env:API_TOKEN="un-jeton-long-aleatoire"      # Windows PowerShell

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

- REST : <http://localhost:8000>
- MCP : <http://localhost:8000/mcp>
- Documentation OpenAPI interactive : <http://localhost:8000/docs>

> **Pourquoi `uvicorn app.main:app` et non `python -m app.run` ?**
> `app/run.py` appelle `mount_mcp(app)`, qui monte une seconde fois le serveur MCP sur
> `/mcp` alors que `app/main.py` l'a déjà monté. Le second montage est injoignable : il
> crée un gestionnaire de session inutilisé. Passer par `uvicorn app.main:app` évite ce
> doublon. Voir [Limites connues](#limites-connues).

Pour un développement avec rechargement automatique :

```bash
uvicorn app.main:app --reload
```

## API REST

Toutes les routes sauf `/health` exigent l'en-tête `Authorization: Bearer <API_TOKEN>`.

| Méthode | Route | Description |
| --- | --- | --- |
| `GET` | `/health` | Sonde de disponibilité. **Non authentifiée.** |
| `GET` | `/v1/models` | Modèles pris en charge et modèles chargés en mémoire. |
| `POST` | `/v1/transcriptions` | Soumet une URL d'audio. Répond `202`. |
| `POST` | `/v1/transcriptions/upload` | Soumet un fichier audio en `multipart/form-data`. Répond `202`. |
| `POST` | `/v1/transcriptions/data` | Soumet de l'audio encodé en Base64. Répond `202`. |
| `GET` | `/v1/transcriptions/{job_id}` | État complet du job. |
| `GET` | `/v1/transcriptions/{job_id}/result` | Transcription et métriques. |
| `DELETE` | `/v1/transcriptions/{job_id}` | Supprime le job et son audio. |

### Soumettre une URL

```bash
curl -X POST http://localhost:8000/v1/transcriptions \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "source": { "type": "url", "url": "https://example.com/audio.m4a" },
        "model": "small",
        "language": "fr"
      }'
```

```json
{ "id": "tr_3f9a1c2b8e7d4051", "status": "queued", "model": "small", ... }
```

### Soumettre un fichier

```bash
curl -X POST http://localhost:8000/v1/transcriptions/upload \
  -H "Authorization: Bearer $API_TOKEN" \
  -F "file=@./audio.m4a" \
  -F "model=small" \
  -F "language=fr"
```

### Interroger le résultat

```bash
curl -H "Authorization: Bearer $API_TOKEN" \
  http://localhost:8000/v1/transcriptions/tr_3f9a1c2b8e7d4051/result
```

`409` tant que le job n'est pas terminé, `500` si le job a échoué, `200` une fois
`status` vaut `completed`.

Les états possibles sont `queued`, `downloading`, `running`, `completed` et `failed`.

### Webhook de fin de job

Ajoutez `callback_url` (et, si nécessaire, `callback_headers`) à la requête. Le service
effectue un `POST` JSON avec l'état du job une fois la transcription terminée, réussie
ou non. `callback_url` et `callback_headers` sont exclus du corps envoyé.

## Outils MCP

Serveur MCP Streamable HTTP sur `/mcp`, nommé `local-whisper-speech-to-text`.

| Outil | Rôle |
| --- | --- |
| `transcribe_url` | Soumet une URL d'audio. |
| `transcribe_data` | Soumet de l'audio encodé en Base64. |
| `get_transcription_status` | État complet du job. |
| `get_transcription_result` | Transcription et métriques de durée. |
| `delete_transcription` | Supprime un job. |

Chaque outil prend un paramètre `api_token` optionnel, à renseigner avec la même valeur
que `API_TOKEN`.

Exemple de configuration client MCP :

```json
{
  "mcpServers": {
    "local-whisper": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": { "Authorization": "Bearer un-jeton-long-aleatoire" }
    }
  }
}
```

## Modèles

Deux modèles sont acceptés : `small` (par défaut) et `medium`.

Ils sont chargés à la demande au premier usage et conservés en mémoire pour la durée du
processus. Charger `medium` demande environ trois fois plus de mémoire que `small` ; sur
CPU, `int8` est le bon compromis. `language: "auto"` laisse Whisper détecter la langue.

## Formats audio acceptés

`.m4a` `.mp3` `.wav` `.ogg` `.opus` `.webm` `.flac`

L'extension est contrôlée à la réception. Pour un téléversement, un nom sans extension
correcte est rejeté ; pour une URL ou du Base64, l'extension est déduite du chemin puis,
à défaut, du type MIME de la réponse.

## Docker

Le contexte de build est la racine du dépôt ; l'image n'embarque que `requirements.txt`
et `app/` (voir `.dockerignore`).

```bash
cp docker/.env.example docker/.env

# Renseigner DATA_PATH, MODELS_PATH, CURRENT_UID et CURRENT_GID dans docker/.env
docker compose -f docker/docker-compose.service.yml up -d
```

`docker/.env.example` documente les variables attendues. En particulier :

- `CURRENT_UID` / `CURRENT_GID` évitent que le conteneur n'écrive en root dans `DATA_PATH`.
- `MODELS_PATH` sert de cache HuggingFace persistant, pour ne pas retélécharger les
  poids à chaque recréation.

L'image est basée sur `python:3.11-slim` épinglée par digest, avec `ffmpeg` installé. Son
point d'entrée est `uvicorn app.main:app`, qui n'exécute donc pas le doublon de montage
décrit plus haut.

## Tests

Les tests sont manuels et nécessitent un service démarré ainsi qu'un fichier audio local.

```powershell
cd tests

.\test-speech-service-v2.3.ps1 `
    -ApiToken "un-jeton-long-aleatoire" `
    -AudioFile "C:\chemins\vers\mon\audio.m4a"
```

Le script enchaîne : sonde `/health`, authentification Bearer, téléversement REST,
interrogation de l'état jusqu'au terme, récupération du résultat, puis découverte et
appels des outils MCP. Il supprime les jobs qu'il a créés, sauf si `-KeepJobs` est
fourni.

Il crée aussi automatiquement un environnement virtuel dans `tests/.venv` et y installe
le SDK MCP officiel — c'est pourquoi ce dossier est ignoré par git. Fournissez `-PythonExe`
pour choisir l'interpréteur, `-SkipMcp` ou `-SkipRestUpload` pour réduire la portée.
Sans `-AudioFile`, les tests REST et MCP sont ignorés avec un avertissement.

`tests/mcp_client.py` est un client MCP autonome, utilisable séparément :

```bash
python tests/mcp_client.py \
    --endpoint http://localhost:8000/mcp \
    --api-token "$API_TOKEN" \
    --audio-file ./audio.m4a \
    --model small \
    --language fr
```

## Sécurité

### Authentification

Si `API_TOKEN` est **vide**, le service n'exige aucune authentification et n'en
contrôle aucune. Ne l'exposez pas sur un réseau dans cet état. REST utilise l'en-tête
`Authorization: Bearer` ; les outils MCP attendent le paramètre `api_token`.

### Protection SSRF

Les URL de téléchargement et de rappel passent par le même validateur
(`app/security.py`), qui refuse :

- les schémas autres que `http` et `https` ;
- les identifiants présents dans l'URL (`https://user:pass@hôte/...`) ;
- les hôtes absents d'une liste blanche, si `ALLOWED_URL_HOSTS` est défini ;
- `localhost` ;
- toute adresse_IP vers laquelle l'hôte se résout et qui est privée, de boucle locale,
  liée au réseau local, réservée, multicast ou non spécifiée.

Les redirections sont suivies **manuellement**, au maximum six fois, et chaque cible est
revalidée avant d'être sollicitée. La taille est contrôlée via l'en-tête
`Content-Length` puis sur le corps réellement reçu.

Pour récupérer de l'audio depuis un réseau interne, poser `ALLOW_PRIVATE_URLS=true` — et
privilégier une liste blanche d'hôtes à cet assouplissement.

### Exposition réseau

Le service ne fournit ni TLS ni limitation de débit. Placez-le derrière un reverse proxy
terminant TLS, et limitez les soumissions à des clients de confiance : la transcription
est coûteuse en CPU.

## Structure du projet

```
app/
  config.py     Variables d'environnement et répertoires de données
  models.py     Schémas Pydantic (Job, Source, Résultat, Statuts)
  security.py   Jeton Bearer et validations anti-SSRF
  source.py     Téléchargement d'URL, Base64, contrôle d'extension
  jobs.py       JobManager : file d'attente, persistance JSON, verrous
  whisper.py    Cache de modèles et appel à faster-whisper
  worker.py     Fil de traitement : download, transcription, webhook
  mcp.py        Serveur MCP et ses cinq outils
  main.py       Application FastAPI, routes REST, montage MCP
  run.py        Point d'entrée alternatif (voir Limites connues)
data/
  input/        Audio téléchargé ou téléversé, un dossier par job
  jobs/         État de chaque job au format JSON
docker/         Dockerfile et composition pour un déploiement conteneurisé
tests/          Script de test PowerShell et client MCP Python
```

Un job est réparti ainsi :

```
data/jobs/tr_3f9a1c2b8e7d4051.json          état, métriques, résultat
data/input/tr_3f9a1c2b8e7d4051/audio.m4a    audio source
data/input/tr_3f9a1c2b8e7d4051/source.json  URL d'origine, si source distante
```

## Limites connues

- **File d'attente à un seul fil.** Un seul job est transcrit à la fois. La file est en
  mémoire : elle ne survit pas à un redémarrage, et un job en cours est perdu.
- **Montage MCP en double.** `app/main.py:549` monte le serveur MCP sur `/mcp`, puis
  `app/run.py:5` appelle `mount_mcp(app)`, qui le remonte. Seul le premier montage est
  joignable ; le second crée un gestionnaire de session orphelin. Utilisez
  `uvicorn app.main:app`.
- **Variables mortes dans Docker.** `MODEL` et `LANGUAGE` sont définis dans le Dockerfile
  et la composition, mais l'application ne les lit pas : le modèle et la langue se
  choisissent par requête. Le volume `whisper_models` déclaré dans la composition n'est
  pas non plus utilisé.
- **Pas de limitation de débit ni de contrôle de quota.** Un client autorisé peut
  saturer la file d'attente.
- **Pas d'expiration automatique.** Les jobs et leurs audios occupent le disque jusqu'à
  suppression explicite.
- **Aucune CI.** Les tests exigent un service actif et le téléchargement des poids des
  modèles, ce qui les rend inadaptés à une intégration continue sans cache.

## Licence

GNU Affero General Public License v3.0. Le texte intégral est dans [LICENSE](LICENSE).

L'AGPL v3.0 comporte une **clause réseau** (section 13) : si vous modifiez ce logiciel
et l'exposez à des utilisateurs qui y accèdent à distance, vous devez leur proposer le
code source de votre version modifiée. C'est la différence essentielle avec la GPL
classique, qui ne couvre que la distribution.

Copyright (C) 2026 Laurent Lemercier.
