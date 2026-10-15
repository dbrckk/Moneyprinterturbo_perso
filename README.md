# MoneyPrinterTurbo Perso — Free Studio

Générateur de vidéos courtes **gratuit, auto-hébergeable et utilisable depuis GitHub Actions sur Android**. Le projet comporte deux chemins complémentaires :

1. **Free Studio** (`free_studio.py`) : produit des MP4 avec FFmpeg + eSpeak, sans API ni paiement. Lorsqu'Ollama est installé, un modèle local génère le véritable script IA. Sans Ollama, un texte déterministe est utilisé et **n'est pas présenté comme de l'IA**.
2. **MoneyPrinterTurbo original** (`upstream/`, import automatisé) : fonctionnalités étendues de l'amont MIT, dont montage, sous-titres, médias, WebUI et API. Certaines options cloud de l'amont sont **payantes** et ne font pas partie du mode gratuit.

> Note importante : les logiciels n'ont pas de frais de licence, mais le matériel, l'électricité, le stockage et un serveur disponible en permanence peuvent avoir un coût. GitHub Actions a des règles et quotas d'utilisation ; ne l'utilisez pas comme un service de rendu continu illimité.

## Utilisation gratuite sur Android (sans ordinateur)

1. Ouvrir **Actions → Générer une vidéo gratuite → Run workflow** dans ce dépôt.
2. Saisir `topic`, choisir `lang`, `aspect` et `resolution`, puis **Run workflow**.
3. Ouvrir l'exécution terminée et télécharger l'artefact **free-studio-video** (MP4 + métadonnées JSON).

Le rendu fonctionne sans compte externe, clé API ou modèle payant. **Le scénario par défaut est un modèle de texte fixe**, pas un LLM. Pour la génération réelle par IA, utiliser Ollama localement.

## Installation locale (Linux / Debian / Ubuntu)

```bash
sudo apt-get update && sudo apt-get install -y ffmpeg espeak
python3 free_studio.py --topic 'Les économies d’énergie' --lang fr --output outputs/energie.mp4
python3 free_studio.py --topic 'Le solaire' --script 'Le soleil produit une énergie renouvelable. Un panneau transforme la lumière en électricité.' --output outputs/solaire.mp4
```

IA gratuite et locale : installer [Ollama](https://ollama.com/), lancer `ollama pull llama3.2:1b`, puis :

```bash
python3 free_studio.py --topic 'Les économies d’énergie' --ollama-model llama3.2:1b --output outputs/energie-ia.mp4
```

Options : `--aspect 9:16|16:9|1:1`, `--resolution 360|720|1080`, `--mute`, `--fps`, `--script-file`. La résolution 720 signifie le petit côté à 720 px. Les métadonnées et les sous-titres temporels sont enregistrés à côté du MP4 dans le fichier JSON de sortie.

## Développement et tests

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q free_studio.py tests
```

Les tests couvrent le rendu vidéo réel avec FFmpeg, une narration réelle avec eSpeak et les validations du pipeline. Le CI installe les dépendances libres nécessaires.

## Provenance et licences

L'amont est [harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo), sous licence MIT (Copyright © 2024 Harry). L'import est conservé dans `upstream/` avec son `LICENSE`. Le dossier `upstream/resource/songs` n'est pas importé à cause des incertitudes sur les droits musicaux ; `upstream/resource/fonts` ne l'est pas non plus car les licences individuelles doivent être vérifiées.

`star-list` ([dbrckk/star-list](https://github.com/dbrckk/star-list)) est utilisé comme catalogue de sélection de composants. **L'objectif n'est pas de promettre une parité complète du mode gratuit dès cette première version.** Les médias externes, l'éditeur web dédié et les workflows avancés nécessiteront des intégrations et tests supplémentaires.

## Sécurité

- Le mode gratuit ne charge aucune clé API ni service facturé.
- L'intégration Ollama refuse les destinations non locales.
- Tous les appels FFmpeg sont réalisés sans shell, avec entrée contrôlée.
- Aucun actif musical tiers ou police d'auteur incertain n'est importé automatiquement.
- Les sorties `outputs/` sont ignorées par Git, et les artefacts Actions expirent automatiquement.

## Vraies images et vidéos — nouveauté

Le moteur **Free Studio** sait désormais monter des images et vidéos locales réelles,
avec un lent zoom sur les photos (effet Ken Burns) et recadrage aux formats Shorts.
Sans média disponible, il utilise automatiquement les fonds graphiques précédents.

**Sources locales (aucune connexion, aucune API) :**

```bash
mkdir -p mes-medias
# Copiez vos photos JPEG/PNG/WebP et/ou vos clips MP4/MOV/WebM/MKV dans mes-medias
python3 free_studio.py --topic 'Les paysages de France' \
  --media-dir mes-medias --lang fr --output outputs/paysages.mp4
```

**Wikimedia Commons (connexion internet requise, aucune clé API) :**

```bash
python3 free_studio.py --topic 'architecture Paris' \
  --commons --output outputs/architecture.mp4
```

L'option `--commons` sélectionne uniquement les images dont les métadonnées
Wikimedia Commons annoncent une licence **CC0** ou **domaine public**. Les images
CC BY/CC BY-SA/non commerciales ou dont la licence n'a pu être vérifiée ne sont
**pas** reprises. Le résultat inclut `video.json` (scènes, avertissements, sources)
et, pour chaque média utilisé, `video.credits.txt` (auteurs, licences, liens).
Les résultats dépendent des fichiers disponibles : une recherche ne garantit pas
une correspondance pertinente à chaque scène. Vérifiez les notices de droits
avant toute publication commerciale, notamment les marques, personnes et droits
de la personnalité même lorsqu'une photo est dans le domaine public.

Le workflow **Actions → Générer une vidéo gratuite** recherche désormais ces
images par défaut (choix `visuels: commons`), accepte un texte de narration
personnalisé facultatif et joint le relevé de crédits aux artefacts. Si la
recherche échoue, la vidéo reste générée avec des fonds abstraits et le JSON
signale explicitement ce repli. **L'absence de texte personnalisé et d'Ollama
n'active pas une vraie IA : le scénario reste le modèle déterministe.**

Les images/vidéos locales doivent être autorisées par leur propriétaire :
le mode local ne prétend pas en vérifier automatiquement les droits.

En présence d’une vraie photo/vidéo, la narration n’est plus dupliquée en un
grand titre au milieu de l’image : seuls les sous-titres et les éléments
d’habillage restent affichés. Les scènes sans médias conservent le titre.


## Interface web mobile — studio local

Le **studio web** partage le même moteur de rendu gratuit (`free_studio.py`). Il ne dépend ni de Node, ni de FastAPI, ni d’un fournisseur cloud. Il permet : édition de scénario, brouillon explicite non-IA (ou Ollama local si configuré), recherche Wikimedia par scène, voix eSpeak ou Piper installé, format, résolution, suivi des rendus, annulation, aperçu et téléchargement MP4/SRT/JSON/crédits. Les rendus sont traités **un par un** et les fichiers sont conservés dans `outputs/web/`.

Sur un ordinateur Linux (Python 3.11+), depuis la racine du dépôt :

```bash
sudo apt-get update && sudo apt-get install -y ffmpeg espeak
python3 studio_server.py
```

Ouvrir ensuite **http://127.0.0.1:8765** depuis cet ordinateur. Pour écrire les brouillons avec une vraie IA **locale**, installer Ollama et un modèle, puis démarrer avec `python3 studio_server.py --ollama-model llama3.2:1b`. Sans ce paramètre, le brouillon est déterministe (non-IA). La présence d'un modèle Piper local peut être signalée avec `--piper-model /chemin/voix.onnx` (fichier `.onnx.json` adjacent obligatoire).

### Depuis Android sur le même réseau Wi-Fi

L'ordinateur doit rester **allumé et accessible sur le même réseau local** : une page web statique ne peut pas rendre un MP4 sans moteur actif. Pour activer l'accès LAN, générer un jeton aléatoire de 32 caractères minimum et lancer :

```bash
export MPT_STUDIO_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf 'Jeton de cette session : %s\n' "$MPT_STUDIO_TOKEN"
python3 studio_server.py --host 0.0.0.0 --port 8765
```

Depuis Android, ouvrir `http://IP_LOCALE_DU_PC:8765`, puis coller le jeton dans l'interface. Vérifier au besoin l'adresse IP du PC avec `hostname -I` (Linux). **N'ouvrez pas le port 8765 sur Internet** : le mode LAN utilise HTTP sans chiffrement ; il est réservé à un Wi-Fi de confiance. Le jeton est conservé uniquement dans le stockage de session du navigateur, jamais dans les URLs. Par défaut, l'interface écoute seulement sur la boucle locale.

Pour utiliser vos images et clips autorisés, ajouter `--media-dir /chemin/vers/medias` au lancement et choisir « Médias locaux » dans l'interface. Les fichiers ne sont jamais envoyés vers un fournisseur externe. Pour exécuter le rendu **uniquement depuis Android, sans ordinateur ni serveur**, le workflow GitHub Actions « Générer une vidéo gratuite » reste disponible, mais le studio web local n'est pas hébergé en permanence sur GitHub Pages.

### Tests

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q free_studio.py media_assets.py studio_server.py tests
node --check web/app.js   # si Node.js est installé
```

Les tests web utilisent de vraies requêtes HTTP et vérifient la validation des paramètres, l'authentification, le contrôle d'origine, les protections contre la traversée de chemins et la création d'un MP4 téléchargeable. Il n'y a pas de service cloud caché.

## Studio distant Android — interface web gratuite

Une interface HTTPS indépendante du serveur Python local est disponible dans [`remote/`](remote/), avec édition du scénario, choix des formats et des voix eSpeak, découpage des scènes, recherche d'images libres et suivi des rendus GitHub Actions.

**Le site distant ne peut pas monter une vidéo sans connexion GitHub** : il pilote le workflow gratuit du dépôt public. Le scénario et les paramètres sont conservés sur le navigateur, mais **le jeton GitHub ne l'est jamais**. Un jeton fine-grained avec la seule permission de dépôt `Actions: Read and write` est requis pour démarrer un rendu, sauf si tu préfères lancer directement les workflows depuis le site GitHub. Les MP4 sont à récupérer dans les artefacts de l'exécution. Voir [instructions Android, sécurité et hébergement](docs/REMOTE_STUDIO.md).

L'interface ne constitue pas encore un éditeur vidéo avec timeline et preview d'images en temps réel : elle est un **portail de production distant fonctionnel** raccordé au moteur existant. La voie locale `studio_server.py` reste disponible pour la prévisualisation vidéo interactive depuis un PC.

## Nouveau : scénario généré par une vraie IA depuis Android

Le studio distant propose **« Vraie IA locale · Ollama sur GitHub Actions »**. En laissant la narration vide, le workflow téléchargera `qwen2.5:1.5b` sur le runner Linux standard public, puis `free_studio.py` l'interrogera sur `127.0.0.1`. Aucun PC personnel, jeton d'API commerciale ni GPU payant n'est requis. Le téléchargement du modèle peut rallonger fortement le temps de génération et peut échouer en cas de limitation réseau/ressources ; **un échec de la vraie IA ne sera pas maquillé en génération IA réussie**.

L'utilisateur peut également lancer manuellement le workflow dans GitHub Actions en sélectionnant `script_mode=local-ai`. Un texte de narration personnalisé exige `script_mode=template`. Les paramètres sont volontairement explicites pour éviter toute fausse revendication de génération IA.

Voir [l'audit du code réellement incorporé et des meilleurs dépôts de star-list](docs/STAR_LIST_PROVENANCE.md).

Nouvelle version Android (scénario IA gratuit via Ollama) : https://phased-stream-788heth.shipstatic.com
