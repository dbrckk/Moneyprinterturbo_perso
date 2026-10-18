**Accès HTTPS dernière version : https://dbrckk.github.io/Moneyprinterturbo_perso/** (site statique permanent sur compte gratuit, moteur vidéo sur GitHub Actions).

# Remote Studio — Android sans serveur payant

Le site `remote/` fonctionne avec HTML/CSS/JavaScript natif. Il pilote le workflow GitHub Actions `free-video.yml`, qui effectue le vrai rendu FFmpeg sur un runner public standard. Le navigateur ne réalise pas l'encodage vidéo.

## Mode d'emploi Android

1. Ouvrir l'adresse HTTPS du studio distant (indiquée dans le README principal).
2. Depuis [GitHub fine-grained tokens](https://github.com/settings/personal-access-tokens/new), créer un jeton personnel à **expiration courte**.
3. Choisir **Only select repositories → dbrckk/Moneyprinterturbo_perso**.
4. Dans **Repository permissions**, attribuer **Actions: Read and write**, sans permissions supplémentaires au-delà de Metadata: Read (automatique).
5. Coller le jeton dans le champ « Connexion GitHub », renseigner sujet, narration facultative, paramètres et éventuelles recherches séparées par `|` (une par scène), puis appuyer sur « Générer ma vidéo ».
6. Une fois la production réussie, toucher **« Ouvrir le MP4 · sans ZIP »** dans le studio. Le lien ouvre directement l'artefact vidéo natif de GitHub (une connexion GitHub peut être requise). Sur certains navigateurs Android, la vidéo s'ouvre dans un lecteur et le menu du navigateur permet de l'enregistrer. Le bouton **« Fichiers et logs GitHub »** conserve l'archive `free-studio-video` contenant le MP4, les sous-titres et les rapports. Les anciens rendus sans artefact direct restent téléchargeables via le ZIP.

**Ne pas partager le jeton.** Il reste dans le champ en mémoire du navigateur (ni `localStorage`, ni `sessionStorage`, ni cookie, ni requête vers l'hébergeur du site). Il est envoyé en HTTPS uniquement à `https://api.github.com`. Le jeton disparaît au rechargement de la page. Le code exécuté est auditable sous `remote/` ; contrôler l'URL avant de saisir un jeton.

Seuls le brouillon (sujet, narration, paramètres) et les identifiants des runs sont enregistrés dans `localStorage`, sur l'appareil. Pour les effacer, effacer les données du site dans Android/Chrome.

## Fonctionnement et limites

- `workflow_dispatch` permet d'envoyer la demande. Le champ `request_id` et le `run-name` assurent le suivi distinct des exécutions ; la recherche automatique est limitée à une consultation toutes les 20 secondes pendant au maximum 20 minutes.
- Par défaut, une narration vide produit un **canevas déterministe**, et non une IA. Sélectionner **« Vraie IA locale »** avec narration vide pour déclencher Ollama/Qwen2.5 sur le runner GitHub, sans PC personnel ni API payante. Le scénario IA a été validé avec un vrai modèle sur GitHub Actions (run 38788999318). Un modèle indisponible entraîne un échec explicite, pas un faux résultat IA.
- Les images Wikimedia sont sélectionnées avec restrictions domaine public/CC0. Absence d'image : scène graphique de repli. Respecter aussi droit à l'image et marques.
- Les artefacts vidéo sont conservés **7 jours**, selon les paramètres actuels du workflow.
- Selon la documentation GitHub, les runners standards des dépôts publics sont gratuits, mais les contraintes techniques, règles d'utilisation, politiques de facturation et de stockage restent applicables. Cela ne représente pas une promesse de capacité illimitée.
- Une page web statique n'a aucun serveur de rendu : les vidéos ne sont pas stockées sur son hébergeur et se récupèrent sur GitHub.

## Hébergement et déploiement GitHub Pages

Le répertoire `remote/` est auto-suffisant et peut être publié tel quel sur un hébergeur statique HTTPS. Pour publier également via GitHub Pages : sur le dépôt, ouvrir **Settings → Pages → Build and deployment → GitHub Actions**, puis lancer le workflow manuel **Déployer Remote Studio sur GitHub Pages**. Le workflow n'est pas exécuté sur chaque push tant que Pages n'est pas configuré afin d'éviter des échecs de déploiement.

## Tests

```bash
node --check remote/main.mjs
node --test tests/test_remote_core.mjs
python3 -m unittest discover -s tests -v
```

Tests des API fondés sur la documentation officielle : [GitHub REST CORS](https://docs.github.com/en/rest/using-the-rest-api/using-cors-and-jsonp-to-make-cross-origin-requests) et [workflow dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event).

## Voix française neuronale vérifiée

Choisir « Neurale · Piper SIWIS (français) » dans le studio. GitHub Actions télécharge une voix ONNX épinglée et vérifie son SHA-256. Le test réel 38791826584 a généré un MP4 H.264/AAC, un SRT et un fichier de crédits. Le modèle SIWIS requiert attribution CC BY 4.0, dont les détails figurent dans `docs/PIPER_VOICE.md`. Les autres langues utilisent eSpeak.


## Habillage cinématique vérifié

Depuis le studio Android, choisir `cinematic` (dégradés animés, scènes indexées,
titres à apparition progressive) ou `classic` (habillage simple). Le moteur se trouve
dans `cinematic.py` ; il fonctionne sans GPU ni API payante.

Le commit `bdab80a` a passé la CI générale GitHub ainsi que le smoke test réel
avec voix française Piper. Le jeton GitHub reste uniquement en mémoire du navigateur.


## Sous-titres avec alignement acoustique

Choisir « Sous-titres animés : Progressifs » puis « Précision : Alignement audio Whisper sur CPU ».
Le workflow n'installe `stable-ts` que si cette option est sélectionnée. Un alignement correct
exige un audio de narration et peut échouer sur des mots propres, des pauses et des noms ; les
scènes en repli sont clairement signalées dans `video.alignment.json`. Voir `docs/WORD_ALIGNMENT.md`.

## MP4 sans ZIP et réutilisation des modèles

Depuis GitHub Actions `actions/upload-artifact@v7`, le MP4 est déposé comme artefact individuel `video.mp4` avec `archive: false`. Le studio interroge la liste d'artefacts de **l'exécution suivie** puis construit un lien HTTPS GitHub, sans jamais inclure le jeton dans l'adresse. Ce lien ouvre GitHub : le studio statique n'héberge ni ne télécharge le MP4 lui-même. Le clic peut afficher un lecteur intégré plutôt que déclencher le téléchargement selon le navigateur. L'archive ZIP des projets reste disponible en parallèle. Le lien direct expire avec l'artefact (7 jours).

Le workflow met en cache `~/.ollama/models`, `models/` (Piper SIWIS) et `~/.cache/whisper` (modèle d'alignement) via `actions/cache@v4`, **uniquement lorsque l'option correspondante est utilisée**. Une première exécution doit toujours installer ou télécharger les composants. Les caches peuvent être évincés et ne constituent ni un hébergement permanent ni une garantie de rapidité. Chaque utilisation du modèle Piper vérifie toujours son empreinte SHA-256.


## Aperçu visuel sur Android, sans ZIP

Après une génération réussie, le studio affiche **« Voir toutes les scènes · PNG »** si `video.storyboard.png` est disponible. Le workflow publie cette planche de contrôle avec `actions/upload-artifact@v7`, sans archive ZIP. Le lien ouvre l'artefact GitHub (une connexion peut être requise), jamais le jeton personnel. Les anciens rendus ou les artefacts expirés ne présentent pas de bouton de prévisualisation ; les autres fichiers restent accessibles via l'archive complète.


### Accès direct aux sous-titres et aux contrôles sur Android

Les nouveaux rendus GitHub Actions publient quatre fichiers **séparés, sans ZIP** : le MP4 H.264, la planche PNG des scènes, le SRT éditable et le rapport `video.quality.json` (synchronisation, codec, LUFS mesurés et avertissements). Dans le panneau « Production » du studio HTTPS, ces liens s'affichent uniquement pour les artefacts GitHub réellement présents, non expirés et non vides. Les anciens rendus continuent d'afficher seulement les fichiers disponibles. Les crédits et fichiers avancés restent également dans l'archive complète.

GitHub peut demander une connexion pour servir le fichier ; aucun jeton personnel n'est ajouté aux URL. Les artefacts de production expirent après 7 jours. Les fichiers autonomes sont facultatifs à la restauration du suivi, sans empêcher l'accès au MP4.


## Reprendre un rendu ancien

Le suivi Android interroge désormais directement l'API GitHub par identifiant de run
quand une demande historique possède déjà son numéro. Cela fonctionne même
lorsqu'elle n'apparaît plus dans les 30 dernières exécutions du workflow.
Avant d'afficher un résultat, le studio compare le titre exact de la demande,
la branche et le type de déclenchement : un numéro erroné ne peut pas
être interprété comme une vidéo appartenant à cette demande. Les demandes
n'ayant pas encore de numéro utilisent toujours la recherche récente.
Le jeton GitHub n'est jamais inclus dans les liens de téléchargement.


## Récupération automatique après fermeture Android

Les demandes de rendu en cours datant de moins de 48 heures sont suivies automatiquement lorsque le navigateur revient au premier plan, à la réouverture ou au retour du réseau. Le suivi s'interrompt quand la page est masquée pour préserver les appels GitHub. Le jeton GitHub reste en mémoire du formulaire uniquement ; aucun secret n'est enregistré dans l'historique.

Un état local « Réussi » n'autorise plus directement l'affichage des fichiers. Le studio lit d'abord l'exécution GitHub correspondante et contrôle son identifiant, son titre, son type, sa branche, ainsi que le dépôt et le chemin du workflow lorsqu'ils sont fournis. Les fichiers n'apparaissent qu'après validation du résultat par GitHub. Le suivi actif s'arrête toujours après 20 minutes, sans interrompre la création côté GitHub.


## Adresse fixe facultative (GitHub Pages)

Un workflow de déploiement GitHub Pages se déclenche automatiquement quand un fichier de `remote/` change sur `main`. Il vérifie si le dépôt est configuré avec **GitHub Actions** comme source Pages. Tant que cette option n'est pas activée, il termine sans prétendre avoir déployé le site et affiche une instruction dans son récapitulatif. Pour l'activer une seule fois : **GitHub → Moneyprinterturbo_perso → Settings → Pages → Build and deployment → GitHub Actions**. Après le premier déploiement réussi, l'URL devrait être `https://dbrckk.github.io/Moneyprinterturbo_perso/` et restera stable entre mises à jour. Le navigateur conserve les brouillons uniquement par origine : importer un brouillon de ShipStatic sur GitHub Pages requiert d'utiliser les contrôles de copie/restauration prévus dans le studio.

La création d'un sous-domaine fixe ShipStatic a été essayée, mais refusée par la limite de domaines du compte. Nous ne redirigeons aucun autre projet existant, et GitHub Pages ne demande pas de jeton d'IA ni d'abonnement.


Le suivi et les téléchargements ignorent maintenant les réponses réseau arrivées après un changement de rendu sélectionné dans l'historique Android, y compris lors du retour au même rendu.


### Crédits et fiche technique accessibles directement depuis Android

Chaque nouveau rendu publie désormais **six fichiers individuels sans ZIP** : MP4, planche PNG, sous-titres SRT, rapport de qualité JSON, **crédits TXT** et **métadonnées des scènes JSON**. Les crédits sont générés même pour une vidéo muette sur fonds animés originaux : ce fichier indique explicitement qu'aucune image externe et aucune voix n'ont été utilisées. Les sorties eSpeak précisent la source de synthèse vocale sans attribuer à tort la licence SIWIS de Piper. Les liens n'apparaissent que si GitHub confirme que l'artefact est présent, non vide et non expiré. Les anciens rendus restent compatibles.


## Reprise automatique après coupure pendant l'envoi (Android)

Une réponse de GitHub interrompue pendant `workflow_dispatch` ne garantit **ni**
que la demande a été reçue, **ni** qu'elle a été rejetée. Le studio ne refait
donc jamais automatiquement un POST. Sur expiration du délai réseau, erreur
de connexion ou HTTP 408/429/5xx, il mémorise *uniquement* l'identifiant
aléatoire de la demande, son sujet et son état « Envoi non confirmé » (jamais
le jeton GitHub). Il lance la recherche sur les exécutions existantes et
reprend après une reconnexion. Une tentative identique portant sur le même
sujet dans les cinq minutes relance la **recherche**, pas la génération.
L'utilisateur peut effacer l'historique pour forcer une nouvelle demande.
Une réponse explicite 401/403/404/422 n'est pas présentée comme acceptée.

Cette protection évite certains doublons de rendus et les frais de temps de
calcul, mais ne prouve pas qu'un workflow a démarré tant que l'API GitHub
n'a pas renvoyé le nom exact `Free Studio · studio-…`.


### Limites API GitHub

Une réponse 429 ou une erreur 403 signalant explicitement une limite de requêtes n'est plus affichée comme un échec de génération. Le suivi automatique s'arrête pour éviter de solliciter davantage GitHub et peut être relancé manuellement. Les erreurs 401/403 d'autorisation, 404 d'identifiant inaccessible et les indisponibilités réseau ont des messages distincts. Les jetons restent uniquement en mémoire.


## Validation gratuite avant un rendu GitHub Actions

Le workflow `free-video.yml` valide désormais les paramètres **avant**
l’installation de FFmpeg, Ollama, Piper, Whisper ou CLIP. Un scénario trop long,
plus de 18 scènes, des mots-clés mal répartis, une langue incompatible avec la
voix française Piper, un alignement Whisper sans sous-titres progressifs ou
un modèle CLIP sans Commons sont immédiatement refusés. Les messages d’erreur
sont visibles dans la première étape du workflow GitHub Actions.

Cette validation est hors connexion et ne traite ni jeton GitHub ni données
personnelles externes. Elle n'affiche pas le texte de narration dans les logs.
Une narration générée ultérieurement par Ollama ne peut pas être contrôlée
avant l'exécution du modèle ; ses scènes restent validées ensuite par le moteur.
Ce contrôle réduit les installations et minutes GitHub Actions inutiles, sans
promettre une utilisation illimitée du service gratuit.


## Recherche de rendus et canevas multilingues

Le studio Android retrouve les demandes dans les **100 dernières exécutions**
GitHub Actions. Lorsqu'une page entière est occupée et que la demande n'y
figure pas, la recherche consulte jusqu'à deux pages supplémentaires
(**300 exécutions maximum**). Les pages suivantes ne sont pas chargées si la
première suffit, afin de limiter les appels API et les blocages de quota.
La recherche est abandonnée si l'utilisateur sélectionne un autre rendu.
Lorsqu'un identifiant de run a déjà été vérifié et enregistré localement,
le studio consulte directement cet identifiant plutôt que les listes.

Le bouton « Créer un canevas » emploie maintenant le vrai modèle de phrases
déterministe dans la langue choisie : français, anglais, espagnol, allemand,
italien ou portugais. Ce texte n'est **pas** généré par un modèle IA.
Les brouillons et historiques restent dans le stockage local du navigateur
et les jetons GitHub ne sont jamais sauvegardés par le site.


### Mise à jour du studio Android (octobre 2026)

**[Ouvrir le studio Android mis à jour](https://dbrckk.github.io/Moneyprinterturbo_perso/)** — hébergement GitHub Pages HTTPS à adresse stable et gratuite, avec canevas disponibles en six langues, récupération des demandes sur plusieurs pages GitHub et téléchargement direct des artefacts vidéo.

L'[ancien studio](https://cyber-nanobot-g5pbj9v.shipstatic.com) reste disponible. Les navigateurs Android isolent les données locales par nom de domaine : pour récupérer la narration et les réglages d'un ancien studio, utiliser **Copier mon brouillon** sur l'ancien site, puis **Restaurer mon brouillon** sur le nouveau. Le jeton GitHub n'est jamais inclus dans cette sauvegarde et doit être saisi à nouveau. Les historiques ne sont pas importés entre domaines ; les anciens rendus restent consultables dans [GitHub Actions](https://github.com/dbrckk/Moneyprinterturbo_perso/actions/workflows/free-video.yml) tant que leurs artefacts n'ont pas expiré.


**Miroir de secours permanent :** [MoneyPrinter sur ShipStatic](https://turbo-gate-h3jnc94.shipstatic.com). Ce miroir dispose des mêmes 11 fichiers statiques du commit `b5d9d75`. Le lien GitHub Pages est désormais recommandé, car les changements ultérieurs de `remote/` y sont automatiquement déployés **à la même adresse**, sans créer de nouveau domaine à chaque version. Publication confirmée par le [workflow GitHub Pages](https://github.com/dbrckk/Moneyprinterturbo_perso/actions/runs/39135225026). La vérification indépendante de l'ouverture de la page sur tous les réseaux mobiles reste à effectuer.


## Publication sécurisée des fichiers vidéo

Lors d'un rendu local, le MP4, les sous-titres SRT/ASS, la fiche JSON
et les crédits sont **préparés intégralement avant publication**. Un échec
ordinaire de copie ou renommage restaure les fichiers du précédent rendu,
plutôt que de laisser une nouvelle vidéo associée à un ancien sous-titre.

Une nouvelle génération invalide systématiquement les anciens rapports de
contrôle qualité (JSON) et planches de contact (PNG). Le passage de
sous-titres progressifs à classiques supprime les anciens fichiers ASS
et d'alignement, qui ne décrivent plus le montage.

Les renommages de chaque fichier sont atomiques sur un même système de
fichiers ; **la publication simultanée de plusieurs fichiers n'est pas
atomique en cas de coupure brutale du système**. Le dossier de travail
temporaire est automatiquement nettoyé après une opération normale.
