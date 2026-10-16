**Accès HTTPS dernière version : https://auroral-synth-t69srk2.shipstatic.com** (site statique permanent sur compte gratuit, moteur vidéo sur GitHub Actions).

# Remote Studio — Android sans serveur payant

Le site `remote/` fonctionne avec HTML/CSS/JavaScript natif. Il pilote le workflow GitHub Actions `free-video.yml`, qui effectue le vrai rendu FFmpeg sur un runner public standard. Le navigateur ne réalise pas l'encodage vidéo.

## Mode d'emploi Android

1. Ouvrir l'adresse HTTPS du studio distant (indiquée dans le README principal).
2. Depuis [GitHub fine-grained tokens](https://github.com/settings/personal-access-tokens/new), créer un jeton personnel à **expiration courte**.
3. Choisir **Only select repositories → dbrckk/Moneyprinterturbo_perso**.
4. Dans **Repository permissions**, attribuer **Actions: Read and write**, sans permissions supplémentaires au-delà de Metadata: Read (automatique).
5. Coller le jeton dans le champ « Connexion GitHub », renseigner sujet, narration facultative, paramètres et éventuelles recherches séparées par `|` (une par scène), puis appuyer sur « Générer ma vidéo ».
6. Une fois la production réussie, ouvrir le lien vers le run GitHub, puis télécharger `free-studio-video` dans **Artifacts**. Le ZIP contient la vidéo MP4, le SRT, le JSON et éventuellement les crédits médias.

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
