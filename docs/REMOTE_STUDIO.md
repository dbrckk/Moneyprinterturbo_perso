**Accès HTTPS dernière version : https://infinite-link-65wdd6s.shipstatic.com** (site statique permanent sur compte gratuit, moteur vidéo sur GitHub Actions).

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
