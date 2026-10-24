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
python studio_server.py --doctor
python studio_server.py
```

3. Dans le navigateur du **même téléphone**, ouvrir
   **http://127.0.0.1:8765**. Le serveur est accessible seulement sur cet
   appareil (pas sur Internet). Il faut garder le processus Termux actif
   pendant l'import et l'encodage ; Android peut arrêter les tâches longues
   en arrière-plan.

**Diagnostic Android hors ligne :** avant le premier rendu, la commande
`python studio_server.py --doctor` vérifie la version de Python, FFmpeg,
FFprobe, les filtres nécessaires, un **véritable mini-MP4 H.264/AAC**,
une voix eSpeak française et l'espace disponible. Le test n'accède ni à
la galerie ni au réseau : il utilise des médias de synthèse dans un
répertoire temporaire, puis les supprime. Si moins de 1 Gio est libre,
un avertissement invite à libérer du stockage. Le diagnostic ne garantit
pas à lui seul un rendu long ou la résistance aux interruptions Android.
Pour un résultat automatisable : `python studio_server.py --doctor-json`.

**Test complet avant utilisation (recommandé) :**

```sh
python studio_server.py --doctor-full
```

Cette commande exécute tous les contrôles précédents **puis réalise un vrai
montage vertical à deux scènes** à partir d'une photo et d'une vidéo
synthétiques. Elle vérifie le rendu H.264/AAC, la voix française, la présence
de sous-titres SRT et les images/codes du MP4 final. Les originaux personnels
ne sont **jamais lus ni modifiés** : toutes les sources et sorties de test
sont générées dans un répertoire temporaire puis automatiquement effacées.
Cette vérification est gratuite et hors ligne, mais peut demander plus de temps
et d'énergie qu'un simple diagnostic sur téléphone. Pour le rapport JSON :
`python studio_server.py --doctor-full-json`. Un succès confirme seulement
la chaîne de rendu synthétique sur l'appareil, pas la fiabilité de tout format
importé ni la résistance aux interruptions Android en arrière-plan.
Si des dépendances manquent, installer ou réparer les paquets avec :
`pkg install -y python ffmpeg espeak`. Aucun PC n'est nécessaire.

**État des vérifications :** la chaîne d'import HTTP et le rendu FFmpeg
sont testés sur Linux dans GitHub Actions. Les performances, la disponibilité
exacte des paquets et l'encodage sur chaque modèle de téléphone Android
doivent encore être validés sur appareil réel.

## Utiliser les vidéos de la galerie

1. Renseigner un sujet et la narration (ou cliquer sur « Générer un brouillon »).
2. Dans **Vidéos et photos de mon téléphone**, choisir jusqu'à **24** fichiers.
   L'import démarre **automatiquement dès la sélection**, sans second appui.
3. Le studio affiche **le pourcentage d'avancement et une barre de progression**
   pour chaque fichier, puis indique sa vérification avant de sélectionner
   automatiquement « Médias locaux ». Les transferts restent privés et locaux.
   En cas de coupure réseau ou de délai dépassé, le message explique l'échec,
   l'ancien lot reste sélectionné et le bouton **Réessayer l'import sélectionné**
   relance un import propre sans mélanger les lots précédents. Si le serveur
   ne peut pas confirmer la suppression d'un lot incomplet, il invite à
   vérifier le stockage au lieu d'affirmer qu'il a été effacé.
4. Appuyer sur **Générer la vidéo**. Le studio affiche maintenant l'étape
   réelle du rendu : voix/sous-titres, sélection des médias, préparation des
   scènes, encodage MP4, contrôle du montage et finalisation. Il indique
   également le temps écoulé. La barre avance selon les **étapes terminées**,
   et non selon un faux pourcentage de temps restant : certaines phases,
   notamment l'encodage 1080p, sont beaucoup plus longues. Le MP4 et ses
   sous-titres sont téléchargeables depuis le même écran.

Le suivi fonctionne localement sans service externe. Les états sont transmis
par un fichier JSON temporaire et privé qui ne contient ni noms de fichiers,
ni scénario, ni jeton ; il est supprimé après réussite, erreur ou annulation.
Un redémarrage de Termux n'entraîne pas la reprise automatique d'un montage
interrompu, et cette indication ne constitue pas une estimation du temps restant.

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
Chaque import appartient à un lot distinct. Le transfert des gros fichiers
fonctionne maintenant sans monopoliser le verrou du stockage : l'historique,
l'espace disponible et les autres lots restent consultables pendant l'envoi.
Le moteur réserve dès le début la taille déclarée de chaque fichier, même
si seulement une partie des octets est arrivée ; deux imports concurrents
ne peuvent donc pas dépasser ensemble la limite totale de **800 Mo**.
Un même lot ne peut recevoir qu'un fichier à la fois ; supprimer ce lot ou
l'utiliser pour un rendu est interdit tant qu'un upload est en cours. En
cas de transfert incomplet ou de déconnexion, le fichier temporaire est
effacé et sa réservation est libérée. Le serveur ferme les connexions POST
après réponse pour éviter de réinterpréter les octets non lus d'une requête.
Chaque lecture d'un morceau de fichier doit progresser en moins de **90 s**,
faute de quoi l'import est considéré comme interrompu.

Chaque import appartient à un lot distinct. Un lot est figé dès qu'un
rendu l'utilise. **Un nouvel import ne supprime jamais automatiquement un
ancien lot figé** : il peut servir à relancer un montage. Un brouillon non
figé est supprimé uniquement après validation complète de son remplacement.
Si la vérification du nouveau lot échoue, l'ancien import reste sélectionné
et les fichiers incomplets du nouvel import sont supprimés. Une consultation
impossible de l'ancien lot conduit à le conserver par précaution.
Le bouton **Effacer l'import** supprime les médias du lot sélectionné,
sauf pendant un rendu en cours. Les MP4 finalisés restent disponibles
dans `outputs/web/<identifiant-de-rendu>/`. Le navigateur mémorise uniquement l'identifiant du lot, pas les vidéos :
après rechargement de la page, il retrouve automatiquement les fichiers qui
existent encore dans le moteur local. En cas de suppression du lot ou de
changement de serveur, un nouvel import est nécessaire.

## Prévisualisation MP4 à faible mémoire sur Android

Une fois le rendu terminé, l'aperçu vidéo local s'ouvre maintenant **en
lecture progressive** sans copier l'intégralité du MP4 dans un objet Blob du
navigateur. Le moteur répond aux requêtes HTTP `Range` standard avec `206
Partial Content` : déplacer le curseur du lecteur récupère seulement les
parties utiles, et les vidéos longues peuvent être lues plus tôt en utilisant
moins de mémoire JavaScript sur le téléphone. La vidéo reste stockée sur
l'appareil ; aucune plateforme externe n'intervient.

Le bouton **Télécharger MP4** utilise également le téléchargement natif du
navigateur en mode local, sans charger d'abord tout le fichier en mémoire.
Le lecteur relâche ses ressources quand on sélectionne un autre projet ou
qu'on quitte la page. Les téléchargements des sous-titres et métadonnées
fonctionnent toujours.

**Sécurité :** la lecture partielle conserve les mêmes contrôles de connexion,
d'origine et d'autorisation que les autres fichiers privés du studio. En mode
réseau protégé par jeton, la lecture et le téléchargement conservent le
chargement authentifié historique, car une balise vidéo native ne peut pas
envoyer un en-tête `Authorization`. Ce mode reste plus consommateur de
mémoire ; le gain principal concerne le serveur local sans jeton. Les tests
vérifient les sauts dans un véritable MP4 H.264, les plages invalides,
l'interdiction d'accès aux projets non finalisés et les sessions protégées.

## Annuler un rendu long sans laisser FFmpeg actif

Le bouton **Annuler** arrête le projet en cours et ses sous-processus
FFmpeg/eSpeak : le serveur signale d'abord le groupe de processus, puis
force son arrêt si nécessaire après un délai de grâce de **3 secondes**.
Il vérifie aussi les annulations reçues pendant le lancement ou juste avant
la publication pour éviter de marquer à tort un projet annulé comme terminé.
Un dépassement de la limite de durée du rendu est signalé comme une
**erreur explicite**, et non comme une annulation volontaire.

Les fichiers temporaires du moteur sont isolés dans le sous-dossier privé
`.render-scratch` du projet et nettoyés à la fin, en cas d'échec ou d'annulation.
Les vidéos d'origine importées et les exports déjà achevés ne sont pas
supprimés. Arrêter normalement le serveur avec **Ctrl+C** ou un signal
**SIGTERM** déclenche la même séquence d'arrêt pour les projets encore actifs.
Une fermeture forcée de toute l'application Android par le système (SIGKILL)
ne laisse pas à Python l'occasion d'effectuer ces nettoyages immédiatement.
Au redémarrage, le serveur récupère les projets connus et supprime leurs
anciens fichiers temporaires **uniquement dans** `.render-scratch`, ainsi que
leur indicateur d'étape devenu obsolète. Il ne supprime ni les médias importés,
ni les MP4 exportés, ni les scripts conservés, et ne suit pas les liens
symboliques vers des dossiers extérieurs. Les fichiers temporaires de projets
inconnus ne sont pas supprimés automatiquement.

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
  marqué **interrompu** et n'est jamais relancé automatiquement. **Exception
  de sécurité des exports :** si Android a arrêté le serveur juste après la
  publication complète d'une vidéo mais avant l'enregistrement du statut
  final, le redémarrage récupère automatiquement ce **MP4 déjà terminé**,
  sans recalculer la vidéo. Cette récupération exige les quatre fichiers
  finaux (MP4, sous-titres SRT, crédits et manifeste JSON), des métadonnées
  cohérentes et un flux H.264 lisible par FFprobe. Un rendu partiel ou
  corrompu reste marqué interrompu ; ses fichiers temporaires ne sont
  jamais présentés comme une vidéo terminée.
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

## Mouvement photo continu sur les scènes longues

Les photos JPEG utilisées comme plan visuel bénéficient désormais d'un
**zoom progressif de durée adaptative**. L'ancienne vitesse fixe pouvait
atteindre son plafond avant la fin d'une scène longue, créant une image
immobile au milieu de la narration. Le zoom suit maintenant les images
réellement encodées sur toute la durée du plan (8 % maximum). Les photos
panoramiques conservées intégralement sur un fond flouté évoluent aussi
jusqu'à la dernière image, avec un mouvement plus discret (4 % maximum).
La logique est identique dans le moteur rapide et le moteur classique,
y compris pour les images additionnelles de chevauchement des transitions.
Les originaux, sous-titres, fréquences d'images et codecs sont préservés.

## Cadrage complet des vidéos panoramiques du téléphone

Lorsqu'une vidéo locale est beaucoup plus large ou plus haute que le format
de sortie, un recadrage centré peut éliminer la majeure partie du contenu.
Par exemple, un plan paysage **16:9** au milieu d'un montage **9:16**
ne conserverait normalement qu'environ **32 %** de l'image d'origine.

Le studio peut désormais conserver **l'intégralité des images vidéo**
dans un premier plan non recadré, posé sur un **fond flouté animé**
issu de la même vidéo. Ce cadrage s'active seulement quand la géométrie
indique que moins de **40 %** de la surface source serait conservée.

- Les dimensions affichées proviennent du FFprobe **déjà utilisé lors de
  l'import** : la rotation vidéo de 90°/270° est respectée pour éviter de
  traiter à tort un film portrait comme un paysage. Aucun décodage
  supplémentaire n'est lancé.
- Un unique flux vidéo est normalisé à la cadence cible **avant** d'être
  partagé entre le premier plan et le fond : pas de décalage ni de
  congélation causée par le zoom des photographies.
- Le flou est calculé sur un fond réduit avant l'agrandissement, ce qui
  limite les ressources demandées à FFmpeg sur Android.
- Les vidéos presque au bon format, les dimensions non fiables et les
  photos panoramiques conservent leur stratégie de cadrage précédente.
  Ce choix repose sur le **ratio géométrique**, pas sur une analyse de
  visages ou du sujet présent à l'écran.

La correction fonctionne avec le montage direct à un seul encodage et
la préparation des scènes du moteur classique. Elle conserve les
transitions, les sous-titres, l'audio de narration et les sources originales.
Elle n'ajoute aucun service payant. Le fond flouté est un compromis visuel
volontaire : il évite de couper les côtés, mais donne une image centrale
moins grande qu'un cadrage pleine largeur.

## Cadrage guidé par le mouvement sur les vidéos locales

Pour les clips dont le format est **modérément** plus large que la sortie,
le recadrage central peut exclure le seul élément qui bouge près d'un bord.
Le studio teste désormais une méthode conservatrice :

- L'analyseur lit **au maximum six minuscules images 120 × 68**
  d'une fenêtre vidéo de trois secondes, à 2 images/s. Il ne traite
  **jamais plus de quatre fenêtres vidéo** par montage.
- Il recherche un **mouvement localisé, important, excentré et stable**
  dans plusieurs intervalles. Il choisit alors **un cadrage fixe**
  légèrement décentré, pour éviter les à-coups et garder un
  élément en mouvement dans l'image.
- Le cadrage existant reste inchangé lorsque le signal est trop faible,
  trop réparti, contradictoire ou non lisible ; lorsqu'une vidéo
  possède déjà un format voisin ; ou lorsque l'on utilise une photo.
- Pour les formats très éloignés, la protection précédente conserve
  **l'image vidéo entière sur fond flouté** au lieu de déplacer le crop.
- La même expression FFmpeg est utilisée par le rendu direct
  et le rendu classique, y compris sur les scènes avec transitions.
  Les originaux et l'ordre des clips ne changent pas.

**Limites :** ce n'est **pas** de la reconnaissance d'objets, des
visages ou de la composition artistique par IA. Des sujets immobiles,
un mouvement de caméra global, des plans très agités ou deux sujets
opposés ne déclenchent pas de correction fiable : le centre reste
alors l'option la plus sûre. L'analyse locale peut coûter quelques
secondes de FFmpeg avant le rendu selon le processeur Android.

### Séquences longues : préserver les sujets qui changent de côté

Une scène de **7 secondes ou plus** ne doit plus être cadrée d'après
ses trois premières secondes uniquement. Le détecteur vérifie maintenant
un second échantillon de **trois secondes à la fin de la scène** avant
de déplacer le recadrage.

- Si les deux fenêtres confirment un déplacement sur le **même côté**
  avec un cadrage compatible (écart inférieur ou égal à 0,10), le studio
  applique **un cadrage fixe moyen**, sans créer de panoramique artificiel.
- Si deux déplacements nets apparaissent sur **les côtés opposés**,
  aucun recadrage fixe ne peut protéger les deux sujets. Le moteur utilise
  la vidéo complète sur son **fond flouté animé**, même lorsque le clip
  est seulement modérément plus large que la sortie. Cela préserve les
  deux extrémités de l'image au prix d'une image centrale plus petite.
- Si les observations sont ambiguës, contradictoires sans preuve forte
  ou impossibles à lire, le **recadrage centré** est conservé.

Le budget reste strictement plafonné à **quatre petites lectures vidéo
par export**, jamais plus. Une longue scène nécessite deux lectures
distinctes ; si le budget n'est pas disponible, l'analyse est ignorée
plutôt que d'appliquer un décentrement non vérifié.

Les scénarios de régression comprennent désormais une vraie vidéo MP4
synthétique de **9 secondes**, avec un bloc mobile passant du bord droit
au bord gauche. Les tests décodent les MP4 issus **des deux moteurs
d'encodage**, contrôlent les **108 images**, la visibilité du marqueur
à deux instants et l'intégrité de la source. Il ne s'agit pas de
reconnaissance d'athlètes ou d'un suivi par IA.

Le manifeste JSON d'export indique `render_framing` pour chaque
média : `motion-guided-horizontal-crop`, `soft-matte-full-video`,
`soft-matte-full-image` ou `cover-center-crop`.
Lorsqu'un décentrement est appliqué, `crop_horizontal_anchor`
indique sa position (0 = tout à gauche, 0,5 = centre, 1 = tout à
droite). Les profils d'encodage H.264 ne sont pas modifiés.

## Audit A/B du cadrage avec huit vidéos reproductibles

Le script autonome et facultatif `framing_ab_benchmark.py` compare
**le recadrage centré et la décision automatique** sur **huit scènes
synthétiques**. Il produit 16 vrais MP4 H.264, des mesures JSON et
un rapport HTML autonome à ouvrir sur Android :

```bash
python framing_ab_benchmark.py --output outputs/framing-ab-benchmark
```

Les scènes couvrent : déplacement à droite et à gauche (sport simulé),
mouvement de caméra global, deux sujets mobiles, faible éclairage,
enseigne lumineuse qui clignote sans bouger, et panorama très large.
Sept scènes durent 2,5 secondes ; une huitième scène dure **9 secondes**
et représente un sujet passant du bord droit au bord gauche. Toutes les
vidéos sortent en 360 × 640 pixels et 12 images/s. La scène longue doit
conserver les deux côtés sur fond flouté : les marqueurs sont vérifiés
au début **et** à la fin des MP4 réellement décodés.

L'audit compare la **surface réellement visible d'un marqueur rouge
synthétique** après décodage de chaque MP4. Cela permet de vérifier
objectivement que la correction récupère un élément préalablement coupé.
Le benchmark contrôle aussi le codec, la durée en images et l'intégrité
SHA-256 des sources de test. Les cas difficiles doivent conserver leur
cadrage central. Les panoramas gardent le rendu vidéo sur fond flouté.

**Correction de faux positif :** une enseigne fixe qui clignote sur un
côté génère des changements de luminosité mais pas de déplacement.
L'analyseur exige désormais que la zone où se produisent les changements
se déplace réellement d'au moins **trois pixels sur les miniatures
120 pixels**. En l'absence de cette preuve, le moteur conserve le centre.

Un workflow **Offline Framing A/B** permet aussi de produire ces
fichiers sur GitHub Actions, sans PC. Le rapport mobile, les MP4
avant/après et `metrics.json` sont téléchargeables comme artefacts
du workflow. GitHub mesure la performance de son propre serveur,
**pas** celle du Redmi ni sa consommation de batterie.

Le nombre de cas vérifiés constitue une **régression de test
synthétique, pas un score d'intelligence visuelle ni une précision
sur des vidéos réelles**. Aucun visage, objet ou activité sportive
n'est reconnu sémantiquement ; la décision reste heuristique.

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

### Rythme des plans et mouvement dans le MP4 réellement exporté

La commande d'audit `python video_qa.py outputs/video.mp4` ajoute maintenant
`motion_cadence_review` au rapport `video.quality.json`. Elle calcule
la durée médiane des plans et signale **à titre consultatif** les
montages contenant plusieurs scènes très courtes (moins de 1,25 s)
ou très longues (plus de 9 s).

Pour les plans issus de vraies **sources vidéo** d'au moins 4 secondes,
l'audit mesure le mouvement en décodant **une seule fois** le MP4
à **3 images/s**, en luminance 48 × 27 pixels. La comparaison se limite
à la région centrale de l'image, hors bandeaux et sous-titres inférieurs,
et aux instants éloignés des raccords. La lecture est plafonnée à
**75 secondes et 18 scènes** ; au-delà, la mesure de mouvement est
ignorée, mais les statistiques de durée restent disponibles.
Aucune image ni vidéo n'est téléversée vers un service distant.

Un résultat `very_low_motion` signifie uniquement que les images du
fichier exporté bougent très peu ; **un plan fixe est parfaitement
légitime**. Le signal aide à vérifier les vidéos accidentellement
figées ou les longueurs de plans peu dynamiques. Les photographies et
graphismes synthétiques sont exclus de cette alerte. En cas d'échec,
le champ `status` indique que l'analyse est indisponible, sans
invalider le MP4 ni écraser l'export.

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

### Contrôle automatique avant tout nouveau montage

Avant de créer un projet ou de figer son lot de médias, le studio vérifie
l'**espace réellement libre sur le volume des rendus** (celui qui héberge
à la fois les MP4 finaux et le dossier temporaire FFmpeg). Il applique des
seuils de précaution différents selon la résolution : **256 Mio en 360p**,
**512 Mio en 720p** et **1 024 Mio en 1080p**. Juste avant le lancement
effectif, le contrôle est **répété** : un montage en attente peut sinon
démarrer alors que d'autres applications ont rempli le téléphone.

Si le seuil n'est plus respecté, le nouveau rendu est refusé avec un message
précisant l'espace disponible et une invitation à libérer du stockage.
Les médias sources **ne sont ni supprimés ni figés** lors d'un refus à la
création ; un projet déjà en attente passe en erreur sans lancer FFmpeg.
Le studio n'efface jamais de vidéos pour forcer un rendu. Si la mesure du
stockage échoue, le rendu est refusé plutôt que d'affirmer que l'espace suffit.

Ces valeurs sont des **planchers prudents**, pas une estimation réelle de la
taille d'un montage : la durée, les rushs et les effets peuvent consommer
bien davantage. Elles ne garantissent pas qu'un encodage finira sans saturer
le disque et n'empêchent pas Android d'arrêter Termux en arrière-plan.

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

## Calibration depuis l'interface Android (sans commandes)

Dans le studio local (`http://127.0.0.1:8765`), choisir le format
**Vertical 9:16**, puis une résolution **360p ou 720p**.
Cliquer sur **Calibrer sur ce téléphone** pour lancer volontairement
une comparaison FFmpeg *sur le processeur du téléphone*, sans transmettre
les vidéos personnelles à GitHub. Le moteur encode huit séquences
**synthétiques de deux secondes** à 24 images/s puis calcule la
recommandation `Standard`, `Détails` ou `Premium` sur la base
du cas le plus lent et des limites de ressources du moteur.

Le navigateur affiche l'état en cours, une erreur éventuelle ou le
résultat mesuré. Le bouton **Choisir le profil conseillé** doit être
activé manuellement pour sélectionner le réglage ; il ne lance aucun
rendu et ne modifie aucun fichier source. La recommandation 360p ne
s'applique ni à 720p ni à 1080p ; le format paysage et le carré
ne sont pas couverts par ce banc d'essai vertical.

Pour éviter une concurrence CPU excessive, le studio refuse
une calibration pendant un rendu actif, et inversement. Une seule
calibration peut fonctionner simultanément. Le processus de mesure
est interrompu à 5 minutes ou à l'arrêt du serveur.
Les petits MP4 de test et leurs métriques sont conservés dans
`outputs/web/_encoder_calibration` (ou dans le dossier de sortie
personnalisé), et ne sont pas publiés parmi vos vidéos.

**Attention :** 2 secondes par séquence ne caractérisent pas la
température ni l'autonomie après plusieurs minutes de rendu. La
mesure peut échouer si l'appareil manque d'espace ou si le téléphone
est trop lent. Le profil `Standard` demeure le repli sûr.

## Calibration H.264 locale sur Android (facultative)

Le benchmark synthétique existant peut maintenant produire un **avis
conservateur** sur le profil final adapté au téléphone, sans modifier
automatiquement les réglages. Dans Termux, avec FFmpeg installé :

```bash
python encoder_profile_benchmark.py --resolution 360 --fps 12 --seconds 3
python encoder_local_advice.py --width 360 --height 640 --fps 12 --duration 15 --scenes 3
```

Le second programme lit `outputs/encoder-benchmark/metrics.json`,
compare les cadences d'encodage **mesurées sur cet appareil** et propose
`balanced`, `detail` ou `premium`. Il exige une marge de vitesse
d'au moins deux fois la cadence demandée et ne recommande jamais un
profil interdit par les limites de charge de production. Il refuse
d'extrapoler un benchmark 360p vers un export 720p, ou 12 i/s vers 24 i/s.

Le champ `applies_automatically: false` est intentionnel : pour
appliquer le profil recommandé, le sélectionner dans le studio ou
passer `--encoder-profile <profil>`. Une mesure sur GitHub Actions
**ne calibre pas** un téléphone Android. Un test de 2 à 4 secondes
ne mesure pas l'échauffement prolongé, la batterie, la mémoire vive
ni la qualité visuelle subjective. Si le téléphone chauffe ou ralentit,
revenir au mode `balanced`.

## Benchmark local qualité/performance de l'encodage H.264

Pour comparer plusieurs réglages FFmpeg **sans modifier les exports existants** :

```bash
python encoder_profile_benchmark.py --resolution 360 --fps 12 --seconds 3
```

Le script crée automatiquement deux courts échantillons synthétiques
(mouvement/couleurs et traits fins) en référence FFV1 sans perte, puis
**8 MP4 H.264** : quatre profils sur chaque échantillon.

| Profil | Preset x264 | CRF | Usage étudié |
|---|---|---:|---|
| `current` | `veryfast` | 23 | Réglage actuel, témoin |
| `crisper` | `veryfast` | 20 | Davantage de détails, taille potentiellement plus élevée |
| `compact` | `faster` | 23 | Analyse du compromis compression/vitesse |
| `detail` | `faster` | 20 | Analyse qualité/vitesse/taille |

Résultats sur le **matériel où la commande s'exécute** :
- `outputs/encoder-benchmark/metrics.json` : SSIM FFmpeg comparé à la
  référence sans perte, durée d'encodage mesurée, temps CPU si disponible,
  taille en octets, cadence et nombre d'images vérifiés via FFprobe ;
- `outputs/encoder-benchmark/report.html` : comparaison mobile avec
  lecture des huit MP4, tableaux et compromis mesurés ;
- `outputs/encoder-benchmark/videos/` : MP4 pour inspection humaine.

Le script est limité à 360p ou 720p, 12/24/30 ips et 2 à 4 secondes par
échantillon, avec un plafond global pour préserver l'appareil. Il ne
télécharge **aucune vidéo personnelle**, n'appelle **aucun service distant**
et conserve un rapport précédent complet si la nouvelle analyse échoue.
Un SSIM plus élevé n'est **pas** un certificat de qualité artistique.

**Sans ordinateur :** dans GitHub, ouvrir l'onglet **Actions**, sélectionner
`Offline encoder profile benchmark`, puis **Run workflow**. Après
exécution, télécharger l'artifact `encoder-profile-benchmark` qui contient
le rapport et les MP4. Les mesures obtenues sur les machines GitHub ne
représentent pas les performances, la chaleur ou la batterie du Redmi :
pour mesurer le téléphone, lancer le script localement sous Termux.

Les profils ne changent **jamais automatiquement** les paramètres de rendu
actuels. Le benchmark permet de prendre une décision après avoir inspecté
les résultats sur le matériel et les contenus pertinents.

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

## Limitation mémoire des exports 1080p

L'encodage H.264 des vidéos verticales Full HD utilise désormais **2 threads
libx264** au maximum, afin de limiter la mémoire réservée par le codec et de
réduire le risque de fermeture Android pendant les projets exigeants. Ce
choix s'applique également au rendu classique et aux tentatives de
réparation d'une transition. Les exports 360p et 720p restent inchangés.
L'export reste sur le téléphone, sans modèle distant. Une mesure sur Linux
a observé une baisse de mémoire pour un court échantillon synthétique ; la
consommation réelle sur Redmi dépend de la durée, des médias, des sous-titres,
de la température et des autres applications ouvertes.

## Projet long : garder la mémoire disponible

Sur les montages 720p d'au moins cinq scènes, le studio n'utilise deux
encodeurs FFmpeg simultanés **que si au moins 2 Gio de mémoire utilisable**
sont signalés par Android/Linux et les éventuelles limites cgroup. Sous ce
seuil, l'export revient automatiquement à un encodeur séquentiel, sans
réduire les graphismes, la définition, les sous-titres ni la qualité H.264.
Un manque d'information sur la mémoire déclenche également le mode prudent.
Cette mesure ne remplace pas une vérification sur le téléphone : fermer les
autres applications pendant un gros export reste utile.

Pour comparer volontairement les temps d'un montage de six scènes avec des
fichiers synthétiques (sans accès aux médias personnels) :
`python bench/long_mobile_montage.py --resolution 720`. Le rapport précise
les travailleurs réellement choisis et les durées des différentes étapes.
`--compare-workers` effectue deux exports et peut chauffer l'appareil : le
moteur le refuse si la mémoire disponible ne permet pas le mode parallèle.
Le rapport affiche le gain mesuré seulement si les deux MP4 produits ont un
SHA-256 identique ; si les fichiers diffèrent, la comparaison échoue au lieu
de présenter un résultat trompeur. Ce contrôle ne mesure ni température, ni
batterie, ni pic de mémoire Android.

### Confidentialité des fichiers de rendu

Le script de narration (`script.txt`) et le journal de diagnostic FFmpeg
(`render.log`) sont créés avec des permissions locales restrictives (`0600`)
même si le terminal possède un `umask` permissif. Le moteur refuse les liens
symboliques à ces emplacements pour éviter d'écraser un fichier extérieur.
Ces protections n'empêchent pas une personne disposant déjà d'un accès complet
au compte Android/Termux de lire les fichiers ; ne partagez pas le répertoire
des rendus si les scénarios sont confidentiels.
