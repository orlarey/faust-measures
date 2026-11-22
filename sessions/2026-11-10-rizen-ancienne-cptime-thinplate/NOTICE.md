# Mesure ancienne : cptime, 2dKirchhoffThinPlate seul, trois copies

Protocole `cptime` (faust-migration `tools/cptime.py`, commit 6b17ba8), le 10 novembre 2026, sur rizen (AMD Ryzen AI 9 HX 370, 24 cœurs), épinglé sur `taskset -c 0-3,12-15`, bibliothèques 42fab48.

- A, la référence : master-dev `523288afc` (« JuceGUI: darken the slider tracks with translucent black (#631) »), dans le générateur de la ligne (cpp contre cpp, ocpp contre ocpp), compilée en `-O3 -ffast-math`.
- B, la mesure : master-dev-certification-3 `4ca9b5278`, compilée en `-O3`, sans `-ffast-math` et sans `ftz.h` : ce n'est pas le contrat d'exécution de la branche.
- Simple précision (`-single`), harnais `cpu_arch.cpp`, 9 tours, 3 copies de chaque exécutable ; le rapport B / A est celui des médianes des tours.
- Programmes : `2dKirchhoffThinPlate`.

Un répertoire par juge et générateur : `results.json` (temps de A et de B en ns par échantillon, rapport, dispersion de chaque programme). À côté : le script de la campagne et son journal.

Cette mesure ne se compare pas aux mesures du protocole actuel (LA-MESURE-CONTRE-MASTER-DEV.md) : sa référence est master-dev dans le même générateur, et non R (master-dev cpp sous `-ffast-math`), et la branche n'y est pas compilée sous son contrat.

Moyennes géométriques de B / A :

```tsv
juge et générateur	compilateur	programmes	B / A
clang21-cpp	clang 21.1.8	1	0,598
clang21-ocpp	clang 21.1.8	1	0,515
clang22-cpp	clang 22.1.8	1	0,575
clang22-ocpp	clang 22.1.8	1	0,778
```
