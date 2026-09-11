# Translated READMEs

The English `README.md` in the repository root is the **source of truth**. The
seven files below are maintained translations of it, published so that people who
do not read English can still land on the repository and understand what Jarvis
is — and so search engines index the project in those languages.

| File | Language | Switcher label |
| --- | --- | --- |
| `README.md` | English (source) | English |
| `README.es.md` | Español | Español |
| `README.fr.md` | Français | Français |
| `README.de.md` | Deutsch | Deutsch |
| `README.pt-BR.md` | Português do Brasil | Português (BR) |
| `README.zh-CN.md` | 简体中文 | 简体中文 |
| `README.ja.md` | 日本語 | 日本語 |

## Why they live in the repository root

GitHub renders `README.<lang>.md` from the root, and a root-level path is the one
that gets linked, crawled and indexed most reliably. Every translation keeps the
same structure as the English original — same headings in the same order, the
same images in the same places — so the pages look identical apart from the
language.

## Keeping them in sync

Edit the English `README.md` first, then port the change to every translation in
the same pull request. A translation that silently falls behind is worse than no
translation, because it is read as current.

What must survive translation unchanged:

- The product name **Jarvis**, plus **OpenAI**, **Windows**, **MIT**, **Python**
  and the model names `gpt-transcribe` and `gpt-live-1`.
- Every `code span`, every fenced code block, every file path, every URL and
  every HTML tag or attribute. In the root README the `<img>` tags carry the
  English `alt` text deliberately — they are byte-identical in every file.
- The prices and the cost arithmetic: `$0.0045 / min`, `$0.05 / min`, about 27
  hours per dollar, about 20 minutes per dollar, and roughly 45 cents a month at
  twenty minutes a day, five days a week. Those numbers are verified facts, not
  marketing copy. If they ever change, `jarvis/core/pricing.py` and
  `docs/VERIFIED_API.md` change first — then translate.
- The relative links, which always point at the repository root
  (`CONTRIBUTING.md`, `LICENSE`, `SECURITY.md`, `ARCHITECTURE.md`,
  `docs/images/…`). Translations do not link to each other's folders.

The badge image URLs are part of the published badge markup and are never
translated — only the alt text around them is. The badge *link* targets,
however, must match the heading slug of the page they are on: a translated
heading produces a translated anchor, so `#platform-support` points at nothing
on a page whose heading is `## Plattformunterstützung`, and GitHub silently falls
back to the top of the page.

Every translated file therefore points its two in-page badges at its own
translated headings, e.g.

| File | Platform badge | Languages badge |
| --- | --- | --- |
| `README.de.md` | `#plattformunterstützung` | `#spricht-deine-sprache` |
| `README.es.md` | `#compatibilidad-de-plataformas` | `#habla-tu-idioma` |
| `README.fr.md` | `#plateformes-prises-en-charge` | `#parle-votre-langue` |
| `README.ja.md` | `#対応プラットフォーム` | `#あなたの言語に対応` |
| `README.pt-BR.md` | `#suporte-de-plataforma` | `#fala-o-seu-idioma` |
| `README.zh-CN.md` | `#平台支持` | `#会说你的语言` |

When you translate a heading that a badge points at, update the badge's anchor in
the same commit. A quick check for every file at once:

```bash
python - <<'EOF'
import re, pathlib
def slug(h):
    s = re.sub(r'[^\w\s-]', '', h.strip().lower(), flags=re.UNICODE)
    return re.sub(r'\s+', '-', s.strip())
for f in sorted(pathlib.Path('.').glob('README*.md')):
    t = f.read_text(encoding='utf-8')
    heads = {slug(m.group(1)) for m in re.finditer(r'^#{1,6}\s+(.*)$', t, re.M)}
    bad = [a for a in set(re.findall(r'\]\(#([^)]+)\)', t)) if a not in heads]
    print(f.name, 'BROKEN:', bad or 'none')
EOF
```

## Adding a language

1. Copy `README.md` (the English one) to `README.<code>.md` in the repository
   root. Use the language's own conventional tag: `pt-BR`, `zh-CN`, `es`, `fr`,
   `de`, `ja`.
2. Translate all prose — headings, body copy, table cells, image captions, badge
   alt text, roadmap checkboxes. Write it the way a native technical writer
   would, not word for word.
3. Update the switcher line at the top of the new file *and* at the top of every
   other translated file, so all seven entries exist in all seven files. The
   entry for the language you are reading is bold; English is always listed as
   `[English](README.md)`.
4. Add the new row to the table in this file.
5. Check the links resolve before you open the pull request:

   ```bat
   .venv\Scripts\python.exe -c "import re,pathlib; [print(p, m) for p in pathlib.Path('.').glob('README*.md') for m in re.findall(r'\]\(([^)#][^)]*)\)', p.read_text(encoding='utf-8')) if not (pathlib.Path(m)).exists()]"
   ```

   Nothing printed means every relative link points at a real file.

6. Confirm the English README's own tests still pass — they do not depend on the
   translations, but they do assert facts about the pricing and privacy copy:

   ```bat
   .venv\Scripts\python.exe tests\test_scripts.py
   ```

7. Do not add a line to the root `README.md` for the new language beyond the
   switcher itself (Task: keep the English page's structure stable). The
   in-app UI languages are a separate thing entirely — those live in
   `jarvis/ui/web/locales.js` and are documented in `CONTRIBUTING.md`.

## Status

Every file in the table above is a complete translation of the English README as
of the commit that added it. Anything not covered by a translation is, by
definition, English-only.
