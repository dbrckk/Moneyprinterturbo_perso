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
   L'import démarre **automatiquement dès la sélection**, sans second appui.
3. Le studio affiche le fichier transféré, puis sélectionne automatiquement
   « Médias locaux ». En cas d'interruption, le bouton **Réessayer l'import
   sélectionné** relance un import propre sans mélanger les lots précédents.
4. Appuyer sur **Générer la vidéo**. Le MP4 et ses sous-titres seront
   téléchargeables depuis le même écran.

Formats acceptés : MP4, MOV, WebM, MKV, JPEG, PNG, WebP. Chaque fichier
est vérifié par FFprobe et enregistré de façon atomique (aucun demi-fichier
considéré comme import réussi). Taille maximale : **350 Mo par vidéo**,
**18 Mo par image**, **800 Mo au total** pour les médias importés dans
l'installation, au plus **12 lots** avant nettoyage.

L'import commence par classer naturellement les fichiers numérotés
(`clip1.mp4`, `clip2.mp4`, `clip10.mp4`) ; le serveur préfixe
`001_`, `002_`, etc. Le moteur cherche ensuite les **dates de capture
intégrées** aux vidéos (tags FFprobe `creation_time` / QuickTime
`creationdate`) et aux photos **JPEG** (`DateTimeOriginal` EXIF).

Lorsque **tous les médias d'un lot** disposent de dates valides et
comparables, leurs scènes sont classées par date de capture croissante.
Si un fichier n'a pas de date, si les formats ne sont pas pris en charge
(PNG/WebP n'ont pas encore de lecture de date EXIF ici), si une date paraît
invalide ou si certaines dates JPEG n'ont pas de fuseau alors que d'autres
sont en UTC, le moteur **conserve entièrement l'ordre des fichiers** :
aucun classement chronologique partiel ne risque de mélanger les plans.

L'interface indique si le montage utilise « dates de capture » ou « noms
des fichiers ». **Ce n'est pas une analyse visuelle du contenu** : deux
enregistrements portant la mauvaise horloge ou des métadonnées modifiées
peuvent toujours induire un ordre incorrect. Le fonctionnement est gratuit,
sans IA cloud ni transfert des médias privés.

Lorsqu'une vidéo remplit plusieurs scènes, la lecture reprend à une
position progressive. Pour imposer un ordre manuel, donner des noms
numérotés aux clips et éviter des dates de capture contradictoires (le
tri temporel vérifié prévaut si toutes les dates sont disponibles).

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

## Suppression automatique des introductions noires

Pour les **vidéos locales**, le moteur inspecte uniquement les premières
secondes de chaque fichier, au moyen de FFmpeg déjà installé. Il lit au
maximum **10 secondes** à **4 images/s en 160 × 90**, donc sans modèle IA,
connexion cloud ou nouvel abonnement.

Lorsqu'une séquence **presque entièrement noire** d'au moins 0,45 seconde
est suivie d'images visiblement plus claires, le moteur commence le plan
après cette introduction. Il vérifie chaque source une seule fois par rendu,
même lorsque le clip sert à plusieurs scènes. Les périodes d'écran noir
intégrales, les clips trop courts et les vidéos impossibles à analyser sont
conservés tels quels. Le rapport mentionne les introductions effectivement
écartées et leur durée.

Ce contrôle n'est pas une compréhension des événements filmés : une scène
volontairement presque noire peut être reconnue comme une introduction
technique. Le classement par date de capture demeure inchangé, et les
fichiers originaux sont **jamais modifiés**.

## Alignement automatique sur les vrais changements de plans

Quand **une même vidéo** est réutilisée pour plusieurs scènes de narration,
MoneyPrinter effectue maintenant une reconnaissance **légère des coupes
franches** : FFmpeg examine au maximum les **30 premières secondes** du clip,
à **4 images/s**, sur des images réduites à **48 × 28 pixels**. Deux plans
stables séparés par un changement visuel suffisamment marqué fournissent
un indice de coupe, sans IA cloud ni envoi de vidéos.

Au lieu de commencer systématiquement la scène suivante au milieu d'un
plan, le moteur peut décaler son départ vers une coupe identifiée au plus
**0,85 seconde plus loin**. Il ne recule pas dans l'enregistrement et ne
choisit pas une coupe si elle empêche de lire la scène complète avant la
fin du fichier. Le premier emploi d'un clip conserve son début (sauf si
l'introduction noire a été préalablement identifiée).

Les transitions faibles, fondus, mouvements de caméra et événements filmés
restent difficiles à caractériser. Le mécanisme est volontairement prudent :
sans coupe assez fiable ou en cas d'échec de FFmpeg, **l'ancien montage
chronologique est conservé**. Il s'agit d'un alignement de scènes, et non
d'une analyse sémantique du contenu. Les sources originales restent intactes.
Le manifeste du montage indique le nombre de scènes alignées.

## Choix automatique d'images plus nettes dans un clip réutilisé

Lorsqu'un même clip fournit plusieurs scènes, l'analyse des coupes franches
mesure maintenant aussi un **indicateur local de détail visuel**. Les deux
mesures utilisent **un seul décodage FFmpeg** (96 × 54 pixels, 4 images par
seconde, 30 secondes maximum) afin de limiter le coût sur le téléphone.

Si une scène répétée commence sur des images sensiblement moins détaillées
que les suivantes, le montage peut avancer de **0,75 seconde au maximum**
vers deux images consécutives plus nettes. Ce déplacement est refusé s'il
traverse une coupe franche, raccourcit la scène au-delà de la fin du fichier
ou perturbe le premier emploi d'un clip. Les dates de capture et l'ordre des
fichiers restent inchangés ; les fichiers originaux ne sont jamais modifiés.

Cette méthode estime **les détails de l'image, pas la qualité artistique**.
Elle ne reconnaît pas les sujets filmés et n'établit pas une stabilité
professionnelle de caméra. En cas de manque de preuves ou d'échec FFmpeg,
elle conserve exactement les points de départ déjà calculés.

## Moins de secousses visuelles et de passages répétés

Le même **profil vidéo local** utilisé pour les coupes et la netteté
mesure également les changements d'image entre les échantillons. Le coût
reste limité au scan FFmpeg existant : **4 images/s**, **96 × 54 pixels**,
**30 secondes maximum** par clip réutilisé.

- Lorsqu'une scène réutilise une vidéo, le studio peut décaler son début
  de **0,75 seconde maximum** si une très courte agitation visuelle est
  suivie d'au moins deux échantillons nettement plus calmes. Un mouvement
  continu (sport, panoramique, caméra en déplacement) n'est **pas**
  automatiquement supprimé. La détection repose sur des différences de
  pixels, **pas sur un véritable gyroscope ou stabilisateur vidéo**.
- En cas de retour au début d'un même clip, le moteur recherche un
  chevauchement important avec les moments déjà utilisés. Il ne décale
  la scène que si une courte avance peut réellement diminuer ce
  chevauchement, sans franchir une coupe, dépasser la fin du fichier
  ni modifier l'ordre des vidéos.
- Si le clip ne contient simplement pas assez de matière pour plusieurs
  scènes distinctes, le moteur **ne fabrique pas de nouvelles images** :
  il conserve le plan et signale le risque de répétition dans le rapport.

Les vrais débuts de plans détectés restent prioritaires sur les
optimisations de netteté et d'agitation. Le comportement d'origine reste
disponible automatiquement si les mesures sont absentes, ambiguës ou
indisponibles. Les médias du téléphone ne sont pas envoyés dans le cloud.

## Harmonisation prudente de l'exposition entre vidéos

Le studio peut mesurer la luminosité moyenne de **2 à 8 vidéos locales
distinctes** et atténuer les petits écarts visibles entre elles. FFmpeg
échantillonne au maximum **3 secondes à 1 image/seconde en 48 × 27 pixels**
pour chaque passage temporel distinct du fichier ; aucun contenu privé
n'est envoyé vers un service distant. Pour un rush réutilisé à différents
moments, ces passages sont mesurés séparément (au maximum **12 fenêtres**
pour tout le montage). Si la lumière varie fortement dans le même rush,
aucune correction globale n'est appliquée, afin de préserver les intentions
de prise de vue. Les mesures strictement identiques sont mises en cache.

L'analyse accepte les sources dont la luminosité est dans une plage
intermédiaire et dont l'écart entre la plus sombre et la plus claire n'est
pas excessif. Les vidéos de nuit, les plans surexposés, les écarts
extrêmes, les fichiers illisibles et les ensembles de plus de huit vidéos
restent **sans correction automatique**. L'outil évite donc de faire
ressembler artificiellement une scène nocturne à un plan tourné en plein
jour.

Lorsqu'une correction est justifiée, le filtre FFmpeg ajuste faiblement
la luminosité autour de la médiane des vidéos sélectionnées, avec une
limite de **±0,05** sur le paramètre `brightness`. Les profils et les
réglages de saturation existants restent inchangés. Le même calcul est
appliqué dans les moteurs de rendu rapide et classique, y compris lorsque
le rendu rapide bascule en mode de secours.

Le manifeste JSON indique la valeur `exposure_shift` appliquée à chaque
scène. Aucune photo, illustration Wikimedia ou vidéo originale n'est
modifiée. Cela ne constitue pas un étalonnage HDR ou une correction
scientifique des couleurs : les vidéos sous des éclairages très différents
nécessitent encore une intervention créative.

## Harmonisation prudente de la balance des blancs

Pour les lots de **2 à 8 vidéos locales**, MoneyPrinter peut corriger une
petite différence de dominante chaude ou froide entre les plans. Le moteur
échantillonne jusqu'à 3 secondes par passage distinct d'une vidéo, à 1 image/s
et 48 × 27 pixels (12 fenêtres au total maximum),
en ne retenant que les **tons moyens peu saturés** présents dans au moins
un tiers des pixels de chaque image.

Si toutes les vidéos contiennent des références de couleur utilisables et
comparables, les gains rouge, vert et bleu sont rapprochés de leur médiane
commune, sans modifier intentionnellement la luminosité. Chaque gain est
plafonné à **±6 %**. Le filtre FFmpeg `colorchannelmixer` est partagé
par les deux moteurs de montage, rapide et classique. Les corrections
réellement appliquées apparaissent sous `white_balance_gains` dans
le manifeste JSON.

**Protection des ambiances :** si la même vidéo passe d'une scène chaude
à une scène froide à des positions de lecture différentes, le moteur refuse
un gain unique pour tout le clip. Aucune correction n'est apportée en cas
de couleurs trop saturées, de références neutres insuffisantes, de mesures
manquantes ou douteuses, de très faible luminosité ou de dominantes
incomparables. Les photos et médias Wikimedia sont exclus de cette
fonction. Il ne s'agit ni d'un étalonnage professionnel ni d'un contrôle
de la colorimétrie HDR ; un coucher de soleil volontairement chaud peut
rester inchangé. Les fichiers originaux restent intacts, sans cloud.

## Transitions intelligentes entre plans locaux

Le mode `--transition auto` (style cinématique) décide maintenant
**pour chaque frontière entre deux vidéos locales** si une coupe nette
ou un fondu convient le mieux, sans modifier l'ordre du montage.
Il lit seulement deux petites images de 48 × 27 pixels autour de chaque
frontière. L'analyse compare désormais à la fois **les couleurs moyennes et
la disposition des zones lumineuses/sombres** sur une grille de 6 × 3 zones,
obtenue depuis ces mêmes images, sans nouveau décodage FFmpeg. Deux scènes
avec la même couleur moyenne mais des compositions très différentes sont
ainsi séparées par un fondu plutôt que par une coupe brutale. Les prises
d'un même enregistrement qui se suivent immédiatement peuvent toujours
être assemblées sans fondu. Sinon, le fondu reste utilisé.

L'analyse est limitée à **8 frontières** (jusqu'à 9 scènes) afin de
protéger les ressources Android. Une lecture FFmpeg impossible, une
image excessivement sombre, des médias téléchargés, des photos, une
transition explicitement choisie (`cut`, `fade`, `wipeleft`)
ou une séquence très courte conservent les réglages existants.
Le style classique automatique conserve ses coupes traditionnelles.

La coupe utilise une transition FFmpeg de type `custom`, centrée
sur la frontière de narration : **aucune suppression de durée, aucun
décalage des sous-titres ou de la voix**. La même logique est appliquée
dans le moteur rapide à encodage unique, le chemin cinématique
intermédiaire et le mode de secours classique.

Le manifeste JSON du MP4 contient désormais `scene_transitions` :
une entrée (`cut` ou `fade`) pour chacune des frontières réelles.
Ce choix examine la ressemblance visuelle générale, pas le sens
de l'action filmée ; il ne prétend pas remplacer le jugement artistique
d'un monteur professionnel.

## Réparation automatique d'une coupe défectueuse (montages locaux courts)

Le mode cinématique `--transition auto` dispose d'une correction
**strictement conditionnelle** après l'encodage : si le contrôle des
vrais pixels du MP4 signale un **flash noir ou blanc isolé de confiance
élevée** précisément sur une coupe décidée automatiquement entre deux
vidéos locales, le studio peut tenter de remplacer cette coupe par un
fondu court. La décision de départ reste identique pour les autres plans.

Protection du téléphone et des créations :

- Jusqu'à **8 scènes et 60 secondes maximum**, en 360p ou 720p,
  avec 8 à 30 images/s et encodage FFmpeg déjà fonctionnel.
  Pour 5 à 8 scènes, le moteur réutilise les séquences vidéo déjà
  normalisées : il ne recalcule pas leurs corrections, leurs découpes
  ni leur adaptation au format.
- Un seul réencodage supplémentaire du MP4 final est possible, sans
  réseau ni dépendance payante ; il n'est pas tenté si l'analyse initiale
  est incomplète, si plusieurs anomalies élevées existent ou en cas de
  transition choisie explicitement par l'utilisateur. La correction d'une
  portion H.264 sans réencodage du fichier final n'est **pas** garantie :
  une copie de paquets isolée pourrait provoquer des ruptures de GOP,
  d'images ou de synchronisation.
- Le second MP4 est **rejeté** sauf si une nouvelle inspection des pixels
  réduit effectivement la gravité des anomalies sans en créer d'autres,
  et si FFprobe confirme la présence, les caractéristiques et les durées
  des flux vidéo et audio, la cadence réelle et le **nombre d'images H.264**
  attendu. Ce dernier garde-fou empêche un fichier dont la durée semble
  correcte mais dont les images sont tronquées d'être publié.
  En présence de narration AAC, le système calcule aussi **SHA-256 sur
  les données audio compressées** : une piste dont les échantillons ont
  changé, même à durée et codec identiques, fait rejeter la réparation.
- Pour un montage de **5 à 8 scènes**, le MP4 candidat est d'abord
  inspecté sur **la seule frontière corrigée**. Si elle reste défectueuse,
  les six autres contrôles FFmpeg au maximum sont évités et l'original
  est conservé. Si elle est améliorée, toutes les frontières sont ensuite
  impérativement réinspectées avant toute acceptation.
- Une fois la correction validée aux frontières, le système compare
  les **pixels décodés** de 2 à 4 images situées au milieu des scènes
  avant et après réparation, en incluant les scènes voisines du raccord,
  la première et la dernière. Les échantillons passent à **96 × 54 pixels**
  pour mieux détecter les pertes de finesse. L'algorithme compare aussi
  les **gradients locaux de luminance** : une chute importante de netteté
  sur une scène inchangée fait rejeter le second MP4, même si les
  moyennes de couleur paraissent normales. Les séquences uniformes et
  les petites variations dues à la compression ne sont pas pénalisées.
  Ces mesures réutilisent les mêmes images FFmpeg : **aucun décodage
  supplémentaire** par rapport au contrôle antérieur.
- Le premier rendu est conservé intact en cas d'échec, d'incertitude ou
  de délai d'encodage dépassé. Aucun changement aux fichiers importés.
- Le rapport indique `postencode_transition_repair` (`accepted`,
  `rejected` ou `failed`), `postencode_transition_repair_pipeline`,
  `repair_target_precheck` (`passed` ou `rejected`, si applicable)
  et `repair_audio_integrity` (preuve AAC ou absence d'audio).
  Les mesures `repair_scene_integrity`, `repair_scene_samples`,
  `repair_scene_worst_pixel_delta`, `repair_scene_min_detail_retention`
  et `repair_sharpness_regression` (en cas de problème) documentent
  le contrôle des scènes non modifiées. Le fichier JSON final indique
  la transition réellement publiée.

Le mécanisme **ne promet pas** de réparer tous les effets artistiques,
transitions de vidéos longues ou flashs provenant directement du
contenu original. Les alertes restent des indications à examiner.

## Vérification des images du MP4 final après encodage

Après le rendu d'un montage contenant **au moins deux scènes** et
une vidéo locale, le studio vérifie les vrais pixels du MP4 exporté.
Il inspecte **jusqu'à 8 frontières** de scènes, réparties du début à
la fin, avec un décodage FFmpeg d'environ **1 seconde par frontière**,
à **12 images/s** et **48 × 27 pixels**.

Pour chaque frontière inspectée, le rapport affiche la luminosité
moyenne avant et après, la différence d'exposition, le plus grand
changement de luminosité entre deux images, le type de transition
et les éventuels avertissements :

- **flash noir inattendu** entre deux scènes suffisamment claires ;
- **écart important de luminosité** entre les plans ;
- **variation brusque pendant un fondu**, à vérifier manuellement.

Les coupes nettes choisies volontairement ne sont pas confondues avec
des défauts de transition. L'analyse est volontairement prudente :
ses observations sont **des indications à examiner**, pas un
certificat de qualité professionnelle ou une correction automatique.

Les mesures sont enregistrées sous `final_visual_review` dans le
manifeste `video.json`, accessible depuis le studio Android.
La commande d'audit `python video_qa.py outputs/video.mp4` ajoute
aussi `boundary_visual_review` au fichier `video.quality.json`.
Si l'inspection échoue ou dépasse ses limites, elle signale que la
mesure est indisponible, **sans supprimer ni invalider le MP4**.
Toutes les opérations se font localement, sans accès au cloud.

### Analyse temporelle des défauts visuels

Le contrôle des transitions échantillonne les images du **MP4 réellement
encodé**, en conservant la limite de 8 frontières et de 12 images/s sur une
fenêtre d'une seconde par frontière. Sans autre passe FFmpeg, il mesure :

- la proportion du changement de luminosité concentrée dans la plus grande
  variation entre deux images et sa proximité de la frontière de narration ;
  une transition `fade` n'est signalée comme brusque que si un saut
  prédominant apparaît à proximité de cette frontière ;
- la présence d'une **impulsion lumineuse très brève**, entourée d'images
  plus sombres de luminosité comparable. Un nouveau diagnostic
  `unexpected-bright-flash` identifie ce cas sans confondre une nouvelle
  scène durablement plus lumineuse avec un flash ;
- les flashs noirs, écarts d'exposition et mesures précédemment disponibles.

Les preuves figurent sous `max_step_fraction`,
`dominant_step_near_boundary` et `brief_bright_pulse_frames` dans
`final_visual_review.boundaries` et le rapport de contrôle qualité.
Une alerte appelle une **vérification humaine** : un flash ou une
transition très contrastée peut être délibéré. Ces analyses ne bloquent
pas la publication d'un MP4 techniquement valide, et elles ne modifient
aucune vidéo source.

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
