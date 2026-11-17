# A-Level Trainer

Offline study app for **Cambridge International (CAIE) AS & A Level** past papers, for use on a laptop or phone.

- **Pipeline** (`pipeline/papers.py`, runs on the laptop) downloads past papers and mark schemes, splits them into individual questions, crops each question and its mark-scheme section into one image, extracts the text, tags syllabus topics, and bundles everything into a study pack (`data/packs/<code>.zip`).
- **App** (`app/`) is a static PWA with no build step and no server. On its **Subjects** page you pick the subjects you want; each pack is downloaded into the browser's IndexedDB and works fully offline from then on.
  - Multiple-choice questions are marked automatically.
  - Written answers are marked by **AI via OpenRouter** when you add a key in Settings. You can type your answer or take photos of your written working. Without a key you mark yourself against the official mark scheme.
  - Spaced repetition (Leitner boxes), filters by paper, topic, year and status (unseen / due / weak), timed full-paper mode, and per-paper and per-topic progress.

## Setup

All dependencies come from the flake:

```sh
nix develop                      # shell with python + pymupdf + requests + playwright
nix run .#papers -- --help       # pipeline CLI
nix run .#serve                  # app on http://localhost:8000, packs at /packs/
nix run .#test                   # headless end-to-end test (needs packs in data/packs)
```

## Building packs

```sh
nix run .#papers -- all 9702 --years 2018-2024           # fetch + build + pack
nix run .#papers -- fetch 9709 --years 2020-2024 --components 1,3
nix run .#papers -- build 9709 [--only s23_qp_12] [--jobs 4]   # parallel, one process per core by default
nix run .#papers -- pack 9709                              # also refreshes data/packs/index.json
nix run .#papers -- catalog                                # rewrite index.json only
nix run .#papers -- links <papacambridge url>...           # convert links: PDF, session or subject page
nix run .#papers -- links --dry-run <url>                  # just list the papers found
```

- Papers are downloaded from PapaCambridge into `data/raw/<code>/` and cached (re-running only fetches what's new).
- Default components and syllabus topics per subject live in `pipeline/subjects.json`. Currently that's 9702 Physics and 9709 Mathematics. To add a subject, add an entry with its components and topic keywords. Topic tagging is keyword based; questions with no match show up as "Untagged".
- `links` accepts PapaCambridge links: a paper PDF (converted with its mark scheme), a session page such as `.../as-and-a-level-physics-9702-2023-may-june`, or a subject page such as `.../as-and-a-level-chemistry-9701` (every session from 2018). A subject that isn't in `subjects.json` gets a basic entry in `data/subjects-local.json` (all components, no topic tags) so it works straight away; add it to `subjects.json` with topic keywords to tag it.
- Question detection relies on the CAIE layout (bold question numbers in the left margin; landscape "Question / Answer / Marks" mark-scheme tables, 2018+). Watch the build output for papers reporting 0 questions or missing mark-scheme sections.

## Using it

**Laptop:** `nix run .#serve`, open http://localhost:8000 and go to **Subjects**. It lists every pack in `data/packs/index.json` with Install / Update / Remove. A rebuilt pack shows up as *Update available*; updating keeps your progress. Chrome/Edge can install the app.

**Converting from the app:** when the app is served by `nix run .#serve`, the Subjects page also has *Convert from PapaCambridge*: paste links and the laptop runs `papers.py links` in the background, showing the log; the subject then appears ready to install or update. This isn't available on static hosting like GitHub Pages: PapaCambridge doesn't allow browsers to download its PDFs from other sites, and conversion needs the Python pipeline.

**Where packs come from:** the Subjects page tries `packs/` next to the app first (which `nix run .#serve` maps to `data/packs/`), then the published packs at https://santiago-garrote.github.io/A_LEVEL-Packs/. On GitHub Pages it goes straight to the published ones. Under *Other ways to add subjects* you can set a different source (a URL on another site must send CORS headers) or import a `.zip` file directly.

**Publishing packs:** `nix run .#publish` uploads the changed files in `data/packs/` (`index.json` + zips) as assets of the `packs` release in [A_LEVEL-Packs](https://github.com/Santiago-Garrote/A_LEVEL-Packs), removes packs you deleted locally, and redeploys that repo's Pages site. The packs live in release assets rather than git, so updates don't bloat history, and in a separate repo from the app.

**Phone / students:** open https://santiago-garrote.github.io/A_LEVEL-Trainer/, use *Add to Home Screen*, then **Subjects → Install**. It's about 70 MB per subject, then fully offline. A `.zip` can still be imported by hand.

For a quick test without HTTPS: `nix run .#serve -- --bind 0.0.0.0` and open `http://<laptop-ip>:8000` on the phone (works online only; no offline install).

**Progress sync:** Settings → *Export progress* / *Import progress* (merges both ways).

## AI marking

Settings → paste an OpenRouter key (openrouter.ai/keys) and pick a vision model (the list is filtered to models that accept images, cheapest first). Default: `anthropic/claude-sonnet-5.5`, about 1¢ per question. The question image, mark-scheme image and extracted text are sent with your typed answer and/or photos, and the model returns per-part marks with feedback. You can always adjust the score before saving. The key is stored only in the browser on that device.

## Copyright

Past papers and mark schemes are © UCLES (Cambridge University Press & Assessment). This repo contains code only (`data/` is git-ignored). The published packs are a separate repo, offered free for non-commercial study, with a takedown contact in its README.
