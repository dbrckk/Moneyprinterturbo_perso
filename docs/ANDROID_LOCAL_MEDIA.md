# Importer et monter ses vidéos sur Android sans PC (mode local)

Ce mode fonctionne dans le **studio local** (`studio_server.py`), distinct de
l'interface statique HTTPS sur GitHub Pages. Les fichiers sélectionnés ne
sont **jamais** déposés dans le dépôt GitHub ni transférés vers GitHub Actions.

## Installer gratuitement sur Android avec Termux

1. Installer Termux depuis [F-Droid](https://f-droid.org/packages/com.termux/)
   ou sa page de [versions GitHub](https://github.com/termux/termux-app/releases).
2. Dans Termux, exécuter :

```sh
pkg update -y
pkg install -y python ffmpeg espeak git
git clone https://github.com/dbrckk/Moneyprinterturbo_perso.git
cd Moneyprinterturbo_perso
python studio_server.py
```

3. Dans le navigateur du **même téléphone**, ouvrir
   **http://127.0.0.1:8765**. Le serveur est accessible seulement sur cet
   appareil (pas sur Internet). Il faut garder le processus Termux actif
   pendant l'import et l'encodage ; Android peut arrêter les tâches longues
   en arrière-plan.

**État des vérifications :** la chaîne d'import HTTP et le rendu FFmpeg
sont testés sur Linux dans GitHub Actions. Les performances, la disponibilité
exacte des paquets et l'encodage sur chaque modèle de téléphone Android
doivent encore être validés sur appareil réel.

## Utiliser les vidéos de la galerie

1. Renseigner un sujet et la narration (ou cliquer sur « Générer un brouillon »).
2. Dans **Vidéos et photos de mon téléphone**, choisir jusqu'à **24** fichiers.
3. Appuyer sur **Importer les médias sélectionnés** : le studio affiche la
   progression et sélectionne automatiquement « Médias locaux ».
4. Appuyer sur **Générer la vidéo**. Le MP4 et ses sous-titres seront
   téléchargeables depuis le même écran.

Formats acceptés : MP4, MOV, WebM, MKV, JPEG, PNG, WebP. Chaque fichier
est vérifié par FFprobe et enregistré de façon atomique (aucun demi-fichier
considéré comme import réussi). Taille maximale : **350 Mo par vidéo**,
**18 Mo par image**, **800 Mo au total** pour les médias importés dans
l'installation, au plus **12 lots** avant nettoyage.

Le navigateur classe les fichiers avec des nombres comme
`clip1.mp4`, `clip2.mp4`, `clip10.mp4`, puis le serveur préfixe
`001_`, `002_`, etc. Le moteur réutilise les clips dans leur ordre
avec une position de lecture progressive lorsqu'une même vidéo couvre
plusieurs scènes. **Ce classement n'analyse pas le sens des événements ni
les vraies dates de capture.** Renommer les clips pour imposer un ordre reste
possible.

Les fichiers importés vivent dans `outputs/web/_uploaded_media/`.
Chaque import appartient à un lot distinct. Un lot est figé dès qu'un
rendu l'utilise. Le bouton **Effacer l'import** supprime les médias du
lot, sauf pendant un rendu en cours. Les MP4 finalisés restent disponibles
dans `outputs/web/<identifiant-de-rendu>/`. Le navigateur mémorise uniquement l'identifiant du lot, pas les vidéos :
après rechargement de la page, il retrouve automatiquement les fichiers qui
existent encore dans le moteur local. En cas de suppression du lot ou de
changement de serveur, un nouvel import est nécessaire.

## Récupération des projets après fermeture de Termux

Lorsqu'Android coupe le moteur ou que le terminal se ferme, ouvrir
Termux et redémarrer le serveur avec :

```sh
cd ~/Moneyprinterturbo_perso
python studio_server.py
```

Revenir ensuite à **http://127.0.0.1:8765** et consulter
**Derniers projets**. Les MP4 déjà terminés, leurs sous-titres et leurs
métadonnées restent disponibles sans être recalculés : le moteur reconstruit
l'historique depuis les fichiers locaux au démarrage.

- Un rendu encore **en attente** ou **en cours** au moment de l'arrêt est
  marqué **interrompu**. Il n'est pas repris automatiquement car son
  processus FFmpeg a disparu ; il faut le relancer explicitement.
- Un rendu marqué terminé sans fichier MP4/JSON valide n'est pas proposé
  au téléchargement.
- L'historique de l'interface est limité aux 20 projets les plus récents.
  Les anciens exports ne sont pas supprimés par ce mécanisme et restent
  dans `outputs/web/` jusqu'au nettoyage manuel.
- Chaque projet dispose d'un petit `.job_state.json` atomique. Ce
  checkpoint ne copie ni la narration ni les secrets d'authentification.

## Gestion sécurisée de l'espace de stockage

Le studio affiche maintenant l'espace réellement occupé par les imports,
le plafond de **800 Mo** et le volume récupérable. Les fichiers
`.receiving-*` provenant de transferts HTTP interrompus sont comptabilisés
dans l'occupation, pour ne pas masquer un risque de saturation du téléphone.

- **Automatique (sans perte d'images utilisateur) :** lors de la création
  d'un nouveau lot, suppression des imports *vides* de plus de 24 heures et
  des fragments temporaires de transfert âgés de plus de 24 heures.
- **Sur confirmation uniquement :** le bouton **Libérer les imports déjà
  montés** supprime les sources importées des projets dont le rendu est
  **terminé** et dont le MP4 et le manifeste existent encore. Les projets
  en attente, en cours, les imports en brouillon et ceux liés à des rendus
  échoués sont exclus.
- Les MP4, SRT, fichiers JSON, crédits et historiques de montage **ne sont
  jamais supprimés par ce bouton**. Pour remonter ensuite une vidéo à partir
  des mêmes sources, il faudra toutefois les réimporter depuis la galerie.
- Le nettoyage est accessible seulement via l'interface locale autorisée ;
  un site tiers n'est pas autorisé à demander une suppression.
- **Aucun nettoyage automatique des médias valides ou exports finaux.**
  L'utilisateur décide quand libérer la place des sources déjà montées.
- **Anciens brouillons et rendus échoués :** le bouton
  **Nettoyer les anciens brouillons et échecs** propose, après confirmation,
  les imports inactifs depuis **plus de 7 jours**. Seuls les lots sans rendu
  connu, non utilisés et non scellés, ou les lots dont *tous* les rendus
  référencés se sont soldés par un échec/une annulation, sont concernés.
  Le serveur consulte aussi les anciens fichiers de suivi des projets
  (pas uniquement les vingt projets visibles).
- Le **lot actuellement sélectionné dans l'interface** est exclu du
  nettoyage des anciens brouillons ou échecs, même s'il dépasse sept jours.
  Aucun média ne disparaît tant que l'utilisateur n'a pas confirmé.
- Un lot lié à un rendu terminé, un rendu actif ou un état inconnu n'est
  jamais considéré comme un échec sûr à effacer. La période de sept jours
  est mesurée depuis la dernière modification des sources ou du suivi du rendu.

## Limites importantes

- Le site distant `https://dbrckk.github.io/Moneyprinterturbo_perso/`
  utilise GitHub Actions **sans vos vidéos locales** : un site statique
  ne peut pas envoyer directement une vidéo du téléphone à ce runner.
- Le studio local encode sur le téléphone et consomme CPU, énergie et
  espace disque. Préférer quelques clips courts en 360p pour le premier
  essai ; passer en 720p après un test réussi.
- Les données locales sont soumises à la protection du téléphone et du
  processus local. Éviter d'activer `--host 0.0.0.0` sans comprendre
  l'accès réseau et sans le jeton obligatoire du serveur.
- Le logiciel ne vérifie pas la propriété ou les droits à l'image :
  importer seulement des médias que l'on a le droit d'utiliser.
