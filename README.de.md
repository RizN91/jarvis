<div align="center">

<img src="docs/images/hero.webp" alt="Jarvis — a floating glass pill that shows what you said and what it's doing" width="820">

# Jarvis

**Sprich mit deinem PC. Er hört zu, antwortet und tippt für dich.**

Taste halten, sprechen, loslassen — deine Worte erscheinen am Cursor, in welcher
App auch immer gerade aktiv ist. Oder starte ein Sprachgespräch, das wirklich
*etwas tun* kann: im Web suchen, deine Apps öffnen, Aufgaben auf deinem Rechner
ausführen.

Kein Abo. Bring deinen eigenen OpenAI-Schlüssel mit. **Ab $0.0045 pro Minute.**

[![Lizenz: MIT](https://img.shields.io/badge/License-MIT-3b82f6.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3b82f6.svg)](pyproject.toml)
[![Plattform: Windows 10/11](https://img.shields.io/badge/platform-Windows%2010%2F11-3b82f6.svg)](#platform-support)
[![Tests: über 480 bestanden](https://img.shields.io/badge/tests-489%20passing-22c55e.svg)](CONTRIBUTING.md)
[![Sprachen: 15](https://img.shields.io/badge/languages-15-8b5cf6.svg)](#speaks-your-language)

**Auf Deutsch lesen:** [English](README.md) · [Español](README.es.md) · [Français](README.fr.md) · **Deutsch** · [Português (BR)](README.pt-BR.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

</div>

---

## Sieh es in Aktion

[![Jarvis demo](docs/media/jarvis-preview.webp)](docs/media/jarvis-demo.mp4)

**▶ Die 81-Sekunden-Demo mit Ton ansehen** · 1080p, 11 MB

---

## Warum

Du denkst schneller, als du tippst. Und du wiederholst dich den ganzen Tag: die
gleiche Art E-Mail, die gleiche Commit-Nachricht, die gleiche Bitte, dieses
Dokument doch zusammenzufassen.

Diktier-Tools lösen davon die Hälfte — und sind entweder ein Abo, oder ein
Spielzeug, oder sie drücken still Enter und schicken deine halbfertige Nachricht
ab.

Jarvis macht beide Hälften, kostet Cent-Beträge und hält sich raus. Kein Abo,
kein Konto, keine Telemetrie und kein eigener Server.

<table>
<tr>
<td width="50%">

**Sag es, und es steht da**
> *„Hallo Sarah — ich melde mich zur Rechnung von Dienstag: Kannst du mir die
> Bestellnummer bestätigen, damit ich sie heute bearbeiten kann?"*

…landet in deiner E-Mail, deinem Editor, deinem Ticket — überall dort, wo der
Cursor steht.

</td>
<td width="50%">

**Oder frag nach etwas**
> *„Jarvis, was gibt es Neues zum Release von Vue 3.6?"*

…es sucht, und antwortet dann laut, in einem Gespräch, in das du hineinreden
kannst.

</td>
</tr>
</table>

## Screenshots

<div align="center">

<img src="docs/images/pill-states.webp" alt="The Jarvis pill in all six states: idle, dictating, working, listening, speaking, error" width="900">

<sub>Die Pille in allen sechs Zuständen — inaktiv, diktierend, arbeitend,
zuhörend, sprechend, Fehler. Sie klaut nie den Fokus und bleibt inaktiv, wenn
nichts passiert.</sub>

<br><br>

<img src="docs/images/settings-dark.webp" alt="Jarvis setup wizard, dark theme" width="880">

<sub>Einrichtung in zwölf Schritten: Sie prüft Mikrofon, Lautsprecher, Tastenkürzel
und Schlüssel, bevor du dich auf sie verlässt.</sub>

<br><br>

<img src="docs/images/settings-light.webp" alt="Jarvis settings, light theme" width="880">

<sub>Jeder Bildschirm hat ein helles Theme, und die ganze Oberfläche ist
übersetzt — siehe unten.</sub>

</div>

## Installation

**Windows 10 oder 11.** Du brauchst Python 3.11+ und einen OpenAI-API-Schlüssel.

```bat
git clone https://github.com/RizN91/jarvis.git
cd jarvis
setup.cmd
```

`setup.cmd` sucht ein passendes Python, legt eine virtuelle Umgebung an,
installiert die festgeschriebenen Abhängigkeiten und führt einen Smoke-Check
aus. Danach:

```bat
run.cmd
```

Beim ersten Start öffnet sich der Einrichtungsassistent. Er führt dich durch
Mikrofon, Lautsprecher, Tastenkürzel, Weckwort und Budget und fragt nach deinem
API-Schlüssel — der in der **Windows-Anmeldeinformationsverwaltung** liegt, nie in
einer Datei.

Es wird nichts irgendwohin gesendet, bis du den Assistenten abschließt und mit
dem Diktieren beginnst — außer den Verbindungstests, die du ausdrücklich
startest.

Lieber von Hand?

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m jarvis
```

## Was es kostet

Du bringst deinen eigenen OpenAI-Schlüssel mit, zahlst also direkt an OpenAI —
kein Aufschlag, kein Abo, keine Lizenz pro Arbeitsplatz. Zwei Engines, und die
Wahl liegt bei dir:

| Engine | Modell | Kosten | Fühlt sich an wie |
| --- | --- | --- | --- |
| **Economy** *(Standard beim Diktieren)* | `gpt-transcribe` | **$0.0045 / Min.** | etwa **27 Stunden pro Dollar**; am genauesten für wörtlichen Text |
| **Live** | `gpt-live-1` | **$0.05 / Min.** | etwa 20 Minuten pro Dollar; dialogisch, kommt mit Unterbrechungen und Redeübergaben zurecht |

Zum Einordnen: **zwanzig Minuten Diktat pro Tag, fünf Tage die Woche, kosten mit
Economy etwa 45 Cent im Monat.** Der Assistent ist der teure Teil, und nur,
solange du wirklich mit ihm sprichst.

Jarvis misst außerdem seine eigenen Ausgaben und stoppt an den Tages- und
Monatsgrenzen, die du setzt. Diese Grenzen sind Jarvis' eigene Zähler — er kann
das Budget deines OpenAI-Kontos nicht lesen und behauptet das auch nie.

## Funktionen

**Diktat**
- Tippt in **jede** App — Editoren, Browser, Chats, Terminals, Ticketsysteme.
- **Drückt nie Enter**, kann also keine halb geschriebene Nachricht abschicken.
- Verweigert Passwortfelder; in Terminals hält er den Text zur Prüfung zurück,
  statt automatisch in eine Shell zu tippen.
- Dein eigenes **Vokabular** — Namen, Fachjargon, Produktnamen — das dem
  Transkriptor als Hinweise mitgegeben wird und die Genauigkeit messbar
  verbessert.
- Rückgängig, das dein eigenes Tippen nicht auffrisst.

**Sprachassistent**
- `Ctrl+Alt+Space` öffnet ein Gespräch mit echter Unterbrechung — redest du
  dazwischen, hört er auf.
- Er kann im Web suchen und über eine Werkzeugschicht auf deinem Rechner handeln,
  bei der jede Aktion **dein ausdrückliches Ja** braucht, mit den exakten
  Argumenten sichtbar.
- Lokale Coding-Agenten (Codex, Claude Code) können lange Aufträge bekommen, und
  Jarvis meldet, welchen er benutzt hat.

**Immer**
- **Lokale Weckwörter** — „Hey Jarvis", „Hey GPT". Läuft auf deinem Rechner
  (`sherpa-onnx`); **es verlässt kein Audio das Gerät, während auf die Weckphrase
  gewartet wird**.
- **Die Pille.** Ein schwebendes Overlay mit Per-Pixel-Alpha, das zeigt, was er
  gehört hat und was er gerade tut, wirklich klick-durchlässig und ohne den Fokus
  zu stehlen.
- **Datenschutz durch Konstruktion** — keine Telemetrie, keine Cloud-Datenbank,
  keine Analytics, kein Konto. Roh-Audio wird nicht gespeichert.
- Helles und dunkles Theme. Unterstützung für reduzierte Bewegung.

## Spricht deine Sprache

Die komplette Einrichtung und die Einstellungen gibt es in **15 Sprachen**, mit
einem Umschalter in den Einstellungen:

`English` · `Español` · `Français` · `Deutsch` · `Italiano` · `Nederlands` ·
`Polski` · `Português (BR)` · `Русский` · `Türkçe` · `العربية` (RTL) ·
`हिन्दी` · `中文 (简体)` · `日本語` · `한국어`

Deine Sprache wird beim ersten Start aus dem System erkannt. Eine fehlt? Sie ist
eine Datei und ein Pull Request — siehe [CONTRIBUTING.md](CONTRIBUTING.md).

## Plattformunterstützung

**Windows 10 und 11 werden voll unterstützt.** Der Kern baut auf Win32 auf —
globale Input-Hooks, Texteinfügung über `SendInput`, geschichtete Fenster für die
Pille, DPAPI für den Schlüssel, WTS zur Erkennung von Sperre und Standby. Das
macht ihn so nativ, und deshalb ist er noch nicht portabel.

**macOS und Linux werden noch nicht unterstützt.** Ein Start dort endet mit einer
klaren Meldung statt mit einem Traceback. Ein Port ist auf der Roadmap; es ist
ein echtes Projekt, kein Schalter.

## Roadmap

Was als Nächstes kommt, ungefähr in dieser Reihenfolge:

- [ ] **Mehr Modelle, nicht nur OpenAI** — Claude und Gemini als Gehirn des
      Assistenten, plus lokale Modelle über Ollama für einen vollständig
      Offline-Betrieb.
- [ ] **Bessere Stimmen** und eine Stimmauswahl, inklusive deines eigenen
      Stimmprofils.
- [ ] **Themes und Skins** — mehr als hell/dunkel, und eine Pille, die zu deinem
      Desktop passt.
- [ ] **Echtes Streaming-Diktat** — heute nimmt Live auf und spielt dann ab, die
      Zeit vom Loslassen bis zum Ergebnis enthält also die Länge des Gesagten.
- [ ] **macOS-Port** (Accessibility API + Quartz) und **Linux** (X11).
- [ ] **Plugins** — damit Leute eigene gesprochene Befehle und Werkzeuge
      ergänzen können.
- [ ] **Ein signierter Installer**, damit SmartScreen aufhört zu warnen.

Ideen, Stimmen und Beschwerden sind willkommen in den
[Issues](https://github.com/RizN91/jarvis/issues) — die Roadmap folgt dem, was
die Leute tatsächlich fordern.

## So funktioniert es

Eine kurze Fassung; die echten Details stehen in [ARCHITECTURE.md](ARCHITECTURE.md).

- Ein winziger Tray-Prozess besitzt die globalen Hooks, das Audio und die Pille.
  Im Leerlauf sind das **2 Prozesse, ~53 MB und ~0,3 % eines Kerns**.
- Das Einstellungsfenster ist ein **eigener** Prozess, WebView2 (~440 MB) existiert
  also nur, solange du es offen hast.
- Sprache geht per WebSocket direkt an OpenAI. Es gibt **keinen Jarvis-Server und
  keinen localhost-Port** — nichts anzugreifen, nichts, was ausfallen kann.
- Deine Daten (Verlauf, Vokabular, Ausgaben) liegen als SQLite auf deiner eigenen
  Platte. Nichts wird irgendwohin synchronisiert.

## Sicherheit und Datenschutz

- Dein API-Schlüssel liegt **ausschließlich** in der
  Windows-Anmeldeinformationsverwaltung, DPAPI-geschützt, und wird nie in eine
  Konfigurationsdatei, Datenbank, Logdatei, Kommandozeile oder die Oberfläche
  geschrieben.
- Ein Redaktionsfilter entfernt schlüsselförmige Zeichenketten aus jedem
  Logeintrag.
- **Keine Telemetrie. Keine Analytics. Kein Konto.**
- Roh-Audio wird nicht auf die Platte geschrieben.
- Der Start bei der Anmeldung ist optional und umkehrbar; eine Deinstallation
  entfernt alles.

Wie du eine Sicherheitslücke meldest, steht in [SECURITY.md](SECURITY.md).

## Mitwirken

Bugmeldungen, Übersetzungen, Themes und neue Werkzeug-Verben sind alle willkommen
— beginne mit [CONTRIBUTING.md](CONTRIBUTING.md). Dort stehen die Test-Suites, wie
du eine Sprache hinzufügst, und die Handvoll Regeln, die schon echte Fehler
verhindert haben (keine Geheimnisse in Logs, niemals Enter synthetisieren,
diktierten Text nie zu Anweisungen werden lassen).

Die Test-Suite umfasst rund 486 Assertions in 13 kostenlosen Suites, und sie
tippt in echte Anwendungen statt in Mocks. Es gibt viel zu bauen, wenn du helfen
willst.

## Lizenz

[MIT](LICENSE) — mach damit, was du willst.

Jarvis ist nicht mit OpenAI verbunden, wird von OpenAI weder unterstützt noch
gesponsert. Du stellst deinen eigenen API-Schlüssel bereit und bist für dessen
Nutzung verantwortlich.

---

<div align="center">

**Wenn Jarvis dir Zeit spart, hilft ein ⭐ anderen, ihn zu finden.**

</div>
