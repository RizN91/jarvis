<div align="center">

<img src="docs/images/hero.webp" alt="Jarvis — a floating glass pill that shows what you said and what it's doing" width="820">

# Jarvis

**Parlez à votre PC. Il écoute, répond et tape pour vous.**

Maintenez une touche, parlez, relâchez : vos mots apparaissent au curseur, dans
l'application qui a le focus. Ou ouvrez une conversation vocale qui peut
vraiment *faire des choses* : chercher sur le web, ouvrir vos applications,
exécuter des tâches sur votre machine.

Sans abonnement. Apportez votre propre clé OpenAI. **À partir de $0.0045 par
minute.**

[![Licence : MIT](https://img.shields.io/badge/License-MIT-3b82f6.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3b82f6.svg)](pyproject.toml)
[![Plateforme : Windows 10/11](https://img.shields.io/badge/platform-Windows%2010%2F11-3b82f6.svg)](#plateformes-prises-en-charge)
[![Tests : plus de 480 réussis](https://img.shields.io/badge/tests-489%20passing-22c55e.svg)](CONTRIBUTING.md)
[![Langues : 15](https://img.shields.io/badge/languages-15-8b5cf6.svg)](#parle-votre-langue)

**Lire ce document en :** [English](README.md) · [Español](README.es.md) · **Français** · [Deutsch](README.de.md) · [Português (BR)](README.pt-BR.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

</div>

---

## Installation

**Windows 10 ou 11.** Une seule ligne dans PowerShell suffit pour toute l'installation :

```powershell
irm https://raw.githubusercontent.com/RizN91/jarvis/main/install.ps1 | iex
```

Il trouve votre Python, clone Jarvis dans `%LOCALAPPDATA%\Jarvis\app`, crée l'environnement virtuel, installe les dépendances figées, lance un test de fumée et ajoute un raccourci au menu Démarrer.

Il est par utilisateur et réversible : il n'élève jamais les privilèges, n'installe jamais Python à votre place et ne modifie jamais votre stratégie d'exécution, vos paramètres Defender ni votre Python global. `-DryRun` affiche exactement ce qu'il ferait et ne change rien ; `-Uninstall` le supprime à nouveau, et ne touchera pas à votre dossier de données sans une confirmation saisie.

Le premier lancement ouvre un assistant de configuration — micro, haut-parleurs, raccourcis, mot d'éveil et budget — et demande votre clé d'API OpenAI, qui va directement dans le **Gestionnaire d'identification Windows** et jamais dans un fichier.

Rien n'est envoyé nulle part avant que vous n'ayez terminé l'assistant et commencé à dicter, à l'exception des tests de connexion que vous choisissez explicitement de lancer.

<details>
<summary>Ou installer à la main</summary>

```bat
git clone https://github.com/RizN91/jarvis.git
cd jarvis
setup.cmd
run.cmd
```

Ou directement depuis l'arborescence des sources :

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m jarvis
```

macOS et Linux ne sont pas encore pris en charge. `install.sh` le dit plutôt que de faire semblant, et [Plateformes prises en charge](#plateformes-prises-en-charge) explique pourquoi.

</details>

## Deux façons de le lancer

Vous n'avez jamais besoin de cliquer quoi que ce soit.

<table>
<tr>
<td width="50%" valign="top">

**Dites « Hey Jarvis »**

Activez le mot d'éveil et parlez, tout simplement. Il est détecté **sur votre machine** par un petit modèle local, donc pendant qu'il écoute la phrase **aucun son ne quitte votre ordinateur**. « Hey GPT » fonctionne aussi, et vous pouvez définir votre propre phrase.

</td>
<td width="50%" valign="top">

**Ou maintenez un bouton**

Par défaut, c'est le **bouton latéral** de votre souris (XButton2) : maintenez-le, parlez, relâchez — les mots sont déjà au curseur. Vous préférez le clavier ? Appuyez sur **F8**.

</td>
</tr>
</table>

| Vous appuyez sur | Ce qui se passe |
| --- | --- |
| **Bouton latéral** de la souris (maintenu) | Dicte dans l'application qui a le focus |
| `F8` | Démarre et arrête la dictée, mains libres |
| `Ctrl` + `Alt` + `Space` | Ouvre une conversation vocale |
| `Ctrl` + `Alt` + `Pause` | Arrêt d'urgence — met fin à la session et à tout outil en cours |
| `Esc` | Annule ce qui se passe à l'instant |

Chacun d'eux est réassignable dans l'assistant de configuration — vers une autre touche, un autre bouton de souris, ou rien du tout.

## Voyez-le en action

[![Jarvis demo](docs/media/jarvis-preview.webp)](https://cdn.jsdelivr.net/gh/RizN91/jarvis@main/docs/media/jarvis-demo.mp4)

**[▶ Voir la démo de 88 secondes avec le son](https://cdn.jsdelivr.net/gh/RizN91/jarvis@main/docs/media/jarvis-demo.mp4)** · 1080p, 12 MB

---

## Pourquoi

Vous pensez plus vite que vous ne tapez. Et vous vous répétez toute la journée :
le même type d'e-mail, le même message de commit, la même demande pour résumer
un document.

Les outils de dictée résolvent la moitié du problème, et sont soit un
abonnement, soit un jouet, soit appuient discrètement sur Entrée et envoient
votre message à moitié écrit.

Jarvis fait les deux moitiés, coûte quelques centimes et reste à sa place. Pas
d'abonnement, pas de compte, pas de télémétrie, pas de serveur qui lui soit
propre.

<table>
<tr>
<td width="50%">

**Vous le dites, c'est tapé**
> *« Bonjour Sarah, je reviens vers vous au sujet de la facture de mardi :
> pouvez-vous me confirmer le numéro de bon de commande pour que je puisse la
> traiter aujourd'hui ? »*

…et le texte arrive dans votre e-mail, votre éditeur, votre ticket, partout où
se trouve le curseur.

</td>
<td width="50%">

**Ou demandez quelque chose**
> *« Jarvis, où en est la sortie de Vue 3.6 ? »*

…il cherche, puis répond à voix haute, dans une conversation que vous pouvez
interrompre.

</td>
</tr>
</table>

## Captures d'écran

<div align="center">

<img src="docs/images/pill-states.webp" alt="The Jarvis pill in all six states: idle, dictating, working, listening, speaking, error" width="900">

<sub>La pastille dans ses six états — au repos, dictée, travail, écoute, parole,
erreur. Elle ne vole jamais le focus et reste au repos quand rien ne se passe.</sub>

<br><br>

<img src="docs/images/settings-dark.webp" alt="Jarvis setup wizard, dark theme" width="880">

<sub>Assistant de configuration en douze étapes : il vérifie votre micro, vos
haut-parleurs, vos raccourcis et votre clé avant que vous ne comptiez sur lui.</sub>

<br><br>

<img src="docs/images/settings-light.webp" alt="Jarvis settings, light theme" width="880">

<sub>Chaque écran a un thème clair, et toute l'interface est traduite — voir
plus bas.</sub>

</div>

## Ce que ça coûte

Vous apportez votre propre clé OpenAI, donc vous payez OpenAI directement : pas
de marge, pas d'abonnement, pas de licence par poste. Deux moteurs, et le choix
vous appartient :

| Moteur | Modèle | Coût | Sensation |
| --- | --- | --- | --- |
| **Economy** *(par défaut pour la dictée)* | `gpt-transcribe` | **$0.0045 / min** | environ **27 heures par dollar** ; le plus précis pour le texte mot à mot |
| **Live** | `gpt-live-1` | **$0.05 / min** | environ 20 minutes par dollar ; conversationnel, gère les interruptions et les tours de parole |

Pour donner un ordre de grandeur : **vingt minutes de dictée par jour, cinq jours
par semaine, coûtent environ 45 centimes par mois** avec Economy. L'assistant est
la partie chère, et seulement pendant que vous lui parlez vraiment.

Jarvis mesure aussi sa propre dépense et s'arrête aux plafonds quotidiens et
mensuels que vous fixez. Ces plafonds sont les compteurs internes de Jarvis — il
ne peut pas lire le budget de votre compte OpenAI, et ne prétend jamais le
contraire.

## Fonctionnalités

**Dictée**
- Tape dans **n'importe quelle** application : éditeurs, navigateurs, messageries,
  terminaux, outils de ticketing.
- **N'appuie jamais sur Entrée**, donc impossible d'envoyer un message à moitié
  rédigé.
- Refuse les champs de mot de passe ; dans les terminaux, il garde le texte pour
  relecture au lieu de le taper automatiquement dans un shell.
- Votre propre **vocabulaire** — noms, jargon, noms de produits — transmis au
  transcripteur sous forme d'indices, ce qui améliore réellement la précision.
- Un annuler qui ne mange pas ce que vous avez tapé vous-même.

**Assistant vocal**
- `Ctrl+Alt+Space` ouvre une conversation avec une vraie interruption : parlez
  par-dessus et il se tait.
- Il peut chercher sur le web et agir sur votre machine via une couche d'outils
  où chaque action exige **votre Oui explicite**, avec les arguments exacts
  affichés.
- Des agents de code locaux (Codex, Claude Code) peuvent recevoir de longues
  tâches, et Jarvis indique lequel il a utilisé.

**Toujours**
- **Mots d'éveil locaux** — « Hey Jarvis », « Hey GPT ». Ils tournent sur votre
  machine (`sherpa-onnx`) ; **aucun son ne quitte l'appareil pendant l'écoute** de
  la phrase d'éveil.
- **La pastille.** Une surcouche flottante en alpha par pixel qui montre ce qu'il
  a entendu et ce qu'il fait, réellement traversable par les clics et
  non-activante.
- **Confidentialité par construction** — pas de télémétrie, pas de base de
  données dans le cloud, pas d'analyse, pas de compte. L'audio brut n'est pas
  conservé.
- Thèmes clair et sombre. Prise en charge de la réduction des animations.

## Parle votre langue

Toute la configuration et l'interface des réglages sont livrées en **15 langues**,
avec un sélecteur dans les Réglages :

`English` · `Español` · `Français` · `Deutsch` · `Italiano` · `Nederlands` ·
`Polski` · `Português (BR)` · `Русский` · `Türkçe` · `العربية` (RTL) ·
`हिन्दी` · `中文 (简体)` · `日本語` · `한국어`

Votre langue est détectée depuis votre système au premier lancement. Il en manque
une ? C'est un fichier et une pull request — voir
[CONTRIBUTING.md](CONTRIBUTING.md).

## Plateformes prises en charge

**Windows 10 et 11 sont pleinement pris en charge.** Le cœur est bâti sur Win32 —
hooks d'entrée globaux, insertion de texte par `SendInput`, fenêtres en couches
pour la pastille, DPAPI pour la clé, WTS pour détecter le verrouillage et la
mise en veille. C'est ce qui lui donne cette sensation native, et c'est aussi
pourquoi il n'est pas encore portable.

**macOS et Linux ne sont pas encore pris en charge.** Les y lancer se termine par
un message clair plutôt qu'une trace d'appel. Un portage est au programme ; c'est
un vrai projet, pas un drapeau à activer.

## Feuille de route

Ce qui arrive ensuite, en gros dans l'ordre :

- [ ] **Plus de modèles, pas seulement OpenAI** — Claude et Gemini comme cerveau
      de l'assistant, et des modèles locaux via Ollama pour un mode entièrement
      hors ligne.
- [ ] **De meilleures voix** et un sélecteur de voix, y compris votre propre
      profil vocal.
- [ ] **Thèmes et habillages** — plus que clair/sombre, et une pastille assortie
      à votre bureau.
- [ ] **Une vraie dictée en flux** — aujourd'hui, Live enregistre puis rejoue :
      le délai entre le relâchement et le résultat inclut donc la durée de ce que
      vous avez dit.
- [ ] **Portage macOS** (Accessibility API + Quartz) et **Linux** (X11).
- [ ] **Des extensions** — pour que chacun puisse ajouter ses propres commandes
      vocales et ses outils.
- [ ] **Un installeur signé**, pour que SmartScreen cesse d'avertir.

Les idées, les votes et les réclamations sont bienvenus dans les
[Issues](https://github.com/RizN91/jarvis/issues) — la feuille de route suit ce
que les gens demandent réellement.

## Comment ça marche

Une version courte ; le détail réel est dans [ARCHITECTURE.md](ARCHITECTURE.md).

- Un petit processus de zone de notification gère les hooks globaux, l'audio et
  la pastille. Au repos : **2 processus, ~53 Mo et ~0,3 % d'un cœur**.
- La fenêtre de réglages est un processus **séparé**, donc WebView2 (~440 Mo)
  n'existe que pendant que vous l'avez ouverte.
- La parole part directement vers OpenAI via un WebSocket. **Pas de serveur
  Jarvis et aucun port localhost** — rien à attaquer, rien qui puisse tomber.
- Vos données (historique, vocabulaire, dépense) sont dans SQLite sur votre
  propre disque. Rien n'est synchronisé où que ce soit.

## Sécurité et vie privée

- Votre clé d'API est stockée **uniquement** dans le Gestionnaire d'identification
  Windows, enveloppée par DPAPI, et jamais écrite dans un fichier de
  configuration, une base de données, un journal, une ligne de commande ou
  l'interface.
- Un filtre de masquage efface les chaînes ayant la forme d'une clé de chaque
  enregistrement de journal.
- **Pas de télémétrie. Pas d'analyse. Pas de compte.**
- L'audio brut n'est pas écrit sur le disque.
- Le lancement à l'ouverture de session est optionnel et réversible ; la
  désinstallation supprime tout.

Voir [SECURITY.md](SECURITY.md) pour savoir comment signaler une vulnérabilité.

## Contribuer

Rapports de bugs, traductions, thèmes et nouveaux verbes d'outils sont tous les
bienvenus — commencez par [CONTRIBUTING.md](CONTRIBUTING.md). Il explique les
suites de tests, comment ajouter une langue, et la poignée de règles qui ont déjà
évité de vrais bugs (aucun secret dans les journaux, ne jamais synthétiser
Entrée, ne jamais laisser le texte dicté devenir des instructions).

La suite de tests représente ~486 assertions réparties en 13 suites gratuites, et
elle tape dans de vraies applications plutôt que dans des mocks. Il y a beaucoup
à construire si vous voulez aider.

## Licence

[MIT](LICENSE) Dependency and model licences: [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). — faites-en ce que vous voulez.

Jarvis n'est ni affilié à OpenAI, ni approuvé, ni sponsorisé par OpenAI. Vous
fournissez votre propre clé d'API et êtes responsable de votre usage.

---

<div align="center">

**Si Jarvis vous fait gagner du temps, une ⭐ aide d'autres personnes à le
trouver.**

</div>
