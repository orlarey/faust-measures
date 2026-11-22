# Panier v13, clang++ — mesure ancienne (panier12.py)

Mesure de travail de la session « Faust migration setup », 2026-11-10 (09:30-09:50 UTC), master-dev contre la branche après 3f.3 (8d993a344) et après 3f.5, ordonnancement ocpp à sauts (4d3616576).

## Le protocole

- harnais : `libraries/arch/cpu_arch.cpp` de faust (le meilleur ns par échantillon sur 30 répétitions de 200 blocs de 512, bruit sur les entrées, contrôles à leurs valeurs par défaut) ;
- compilateur C++ : Ubuntu clang 18.1.3 ;
- master-dev (côté `md`) : `-O3 -ffast-math -march=native -std=c++17` ;
- la branche (côtés `b0`, `b1`, `b2`) : `-O3 -march=native -fno-fast-math -ffp-contract=fast -fno-math-errno -std=c++17`, et `-include ftz.h` (FTZ et DAZ du MXCSR au démarrage) ;
- configurations : `cpp` et `ocpp`, `-single` (`f`) et `-double` (`d`), options Faust par défaut ;
- 9 tours, programme après programme (P4_BYPROG), les variantes d'un programme dans un ordre décalé d'un cran à chaque tour ; `results.json` garde tous les tours (`times`), la médiane (`ns_per_frame`), le minimum (`best_ns_per_frame`) et l'écart relatif (`spread`) ;
- programmes : 12 (2dKirchhoffThinPlate, FFT, StiffString, dx7, englishBell, karplus32, multibandfilter, oneSourceToStereo, parametric_eq, virtualAnalog, vocoder, zitaRev) ;
- machine : machine du nuage, Intel Xeon @ 2,10 GHz, 4 cœurs ; le conteneur a redémarré deux fois dans la nuit du 9 au 10 novembre (vers 01:15 et 07:55 UTC), l'hôte peut différer d'une mesure à l'autre ;
- les rapports de la session ont été faits sur le minimum, chaque configuration de la branche rapportée à la **même configuration** de master-dev (ocpp à ocpp) : ce n'est pas la référence $R$ de LA-MESURE-CONTRE-MASTER-DEV.md ; master-dev cpp (`md-cpp-f`, `md-cpp-d`) est dans les temps bruts, et $R$ peut en être recalculée.

## Les binaires

côté	version	commit	empreinte d'arbre	ce que déclare le binaire
md	master-dev d8a62f5bf	d8a62f5bf	74d186222bf6d4683852910fe1aeab355b067ba7	Source commit: d8a62f5bf
b0	3f.3, 8d993a344	8d993a344	7682a10788c8adbf36f4f87320ab2959b4cabdf2	Source commit: 73642f744-modified (l'arbre de 8d993a344 avant son commit ; sur tout le corpus, il produit le code de la tête sous -nolt)
b1	3f.5, 4d3616576	4d3616576	9298ddd47e63ac0ab602b2325e1944d0f95841c9	Source commit: 09cd1e554-modified (l'arbre de 4d3616576 avant son commit)

master-dev : grame-cncm/faust ; la branche : grame-cncm/faust, `master-dev-certification-3`. Les binaires ont été construits depuis l'arbre de travail juste avant leur commit (marqués `-modified`), sauf master-dev et `bin-s3f11`.
