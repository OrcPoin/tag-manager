<div align="center">

# 🏷️ Tag Manager

[![Русский](https://img.shields.io/badge/Русский-e1e4e8?style=for-the-badge)](README.md)&nbsp;[![English](https://img.shields.io/badge/English-0969da?style=for-the-badge)](README.en.md)

Generate and edit hybrid captions (booru tags + natural language) for LoRA training
datasets — locally, with your own vision models.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

<b>Caption generation</b><br>
<img src="docs/screenshot-main.png" alt="Main screen" width="760">

<details>
<summary>More screenshots</summary>
<br>
<table>
<tr>
<td align="center" width="50%"><b>“Tags” tab</b><br><sub>bulk dataset edits</sub><br><img src="docs/screenshot-tags.png" alt="Tags tab"></td>
<td align="center" width="50%"><b>“Gallery” tab</b><br><sub>view and edit</sub><br><img src="docs/screenshot-gallery.png" alt="Gallery tab"></td>
</tr>
<tr>
<td align="center" width="50%"><b>“Health” tab</b><br><sub>dataset audit before training</sub><br><img src="docs/screenshot-health.png" alt="Health tab"></td>
<td align="center" width="50%"><b>Sidebar</b><br><sub>API and generation settings</sub><br><img src="docs/screenshot-api.png" alt="API settings"></td>
</tr>
</table>
</details>

</div>

## Why

To train a LoRA or a fine-tune, every image needs a text file next to it with a
description: `cat.jpg` → `cat.txt`. Doing that by hand for hundreds of images is a long,
dull evening.

Tag Manager writes the captions for you: point it at a folder, and it runs the folder
through a local vision model and writes captions using your prompt. Once the dataset is
ready, you can bulk-edit the same files: fix tags, add a trigger word, browse the
gallery.

Runs fully locally. All you need is your own vision model behind an OpenAI-compatible API.

## Features

- Caption generation via a local VLM (OpenAI-compatible API)
- Hybrid format: booru tags + natural-language description
- Safe update of existing captions (augment / full regen + merge, manual edit protection)
- Bulk tag editing and cleanup (duplicates, spaces, case) — with preview and a `.bak` backup
- Tag stoplist: auto-removal of unwanted tags during generation and bulk apply to dataset
- Trigger word across the whole dataset at once
- Gallery with tag search, manual edit, batch re-caption of selected images and delete
- Dataset audit before training: broken files, duplicates, orphans, weak captions
- Operation history with one-click rollback of the last bulk action
- Trainer config export (OneTrainer JSON / kohya TOML)
- ETA and generation speed display, browser notification on completion
- Pause and resume on long runs

## Why not WD14

WD14 only produces booru tags. Some modern models (Anima, for example) do better on mixed
captions: tags + a natural-language description. Tag Manager lets you get such captions
from a VLM and then edit them comfortably.

## What you need

- **Python 3.10+**
- A running server with a **vision** model and an OpenAI-compatible API. Tested with
  [oobabooga](https://github.com/oobabooga/text-generation-webui) and
  [llama.cpp](https://github.com/ggerganov/llama.cpp). Any multimodal model works:
  Gemma (MoE), Qwen2.5-VL, LLaVA, Pixtral, MiniCPM-V, Llama 3.2 Vision — see the
  [FAQ](#faq) for which to pick.

You start the model yourself — e.g. in oobabooga on the *Model* tab. Tag Manager doesn't
load it: it just connects to an already-running OpenAI-compatible API. A plain text model
won't do — it will ignore the image. Never run local models before? Start with the
[FAQ](#faq), it walks through everything step by step.

## Install

```bash
git clone https://github.com/OrcPoin/tag-manager.git
cd tag-manager
pip install -r requirements.txt
streamlit run app.py
```

On Windows you can double-click **`run.bat`** instead of the last command. To update to the
latest version, double-click **`update.bat`** (requires git installed).

## How to use

1. In the sidebar, set the API address (e.g. `http://127.0.0.1:5000/v1`) and the model
   name, then click “Check connection”.
2. Pick a folder of images, a processing mode and a prompt (presets are included).
3. Optionally set a trigger word — it goes on the first line of every `.txt`.
4. Click “Start”.

Generation runs in the background, so the UI stays responsive even on long runs: you can
pause and edit captions by hand. Progress is saved to `progress.json` — you can stop and
continue later; in resume mode the app only finishes the unprocessed files.

Once the dataset is ready, the **“Tags”** and **“Gallery”** tabs let you tidy up: check
tag frequencies, bulk-edit tags (with preview and `.bak`), set the trigger word, browse
the gallery with search by tag. Edits only touch the tag lines — prose and parenthesized
character blocks are left alone. Before training itself, the **“Health”** tab surfaces
broken files, duplicates, orphans and weak captions, and moves the junk into quarantine.

## Caption format

You define the format with your prompt. The default preset produces a “tags + prose”
hybrid, handy for a style LoRA:

```
1girl, blue hair, smile, school uniform, outdoors, day

A medium shot with the subject centered.

(blue hair, on the left: she waves at the viewer, smiling.)
```

This format suits models that understand both booru tags and a description of the scene
at once.

Bulk operations understand this format and only edit the tag line, leaving the prose
untouched.

## FAQ

<details>
<summary>Never ran local models. Where do I start?</summary>

Tag Manager doesn't launch the model itself — it connects to an already-running server. So
first you bring up a server with a vision model, then paste its address into the sidebar.

Three things you need:

- **A server** — keeps the model in memory and answers over the network. Either
  `llama-server` from llama.cpp (run from the console) or oobabooga (all via mouse).
  oobabooga is easier for a first run.
- **Model files** — vision models usually come as two GGUFs: the model itself and an
  `mmproj-*.gguf` (the model's "eyes"). More in the mmproj question below.
- **The API address** — the server listens on a port, e.g. `http://127.0.0.1:5000/v1`.
  That's what you paste in, then click "Check connection".

Instructions for oobabooga and llama.cpp are below; model choice is there too.

</details>

<details>
<summary>Running via oobabooga (easier for beginners)</summary>

The model loads and configures via the mouse; MoE and mmproj are picked up automatically.

1. Install oobabooga per the
   [instructions](https://github.com/oobabooga/text-generation-webui#how-to-install)
   (there's a one-click installer for Windows).
2. Put the model files in `text-generation-webui/models/` — the model itself and
   `mmproj-*.gguf` next to it.
3. **Model** tab → pick the model → **Load**.
4. Enable the API: **Session** tab → check `openai` (or launch with `--api`). Default port
   is `5000`.
5. In the Tag Manager sidebar enter `http://127.0.0.1:5000/v1` and click "Check
   connection". The 🔄 button fills in the model name.

</details>

<details>
<summary>Running via llama.cpp (single binary)</summary>

Lighter on resources, but you attach the model and its "eyes" yourself on the command line.
Download [llama.cpp](https://github.com/ggerganov/llama.cpp/releases), the model GGUF and
its `mmproj-*.gguf`, then:

```bash
llama-server -m model.gguf --mmproj mmproj-model.gguf --port 5000 -ngl 99
```

- `--mmproj` — the vision projector file. Without it images won't work (see the mmproj question).
- `--port 5000` — the port; you paste it into Tag Manager as `.../5000/v1`.
- `-ngl 99` — layers offloaded to the GPU (99 = all; lower it if you're short on VRAM).

</details>

<details>
<summary>What is mmproj.gguf and why do I need it?</summary>

Without it the vision model can't see images — the single most common reason for "nothing
works".

A multimodal GGUF model is usually two files: the model itself (`model.gguf`) and a
separate vision projector `mmproj-*.gguf`. They live in the same HuggingFace repo, but you
need to download both. Grab only the main file and the model loads as a text model and
ignores the image.

- In llama.cpp the projector is attached with `--mmproj mmproj-model.gguf`.
- In oobabooga just put `mmproj-*.gguf` in the model folder — it's picked up automatically.

</details>

<details>
<summary>Which model should I pick?</summary>

You need a multimodal (vision) model — a text model will ignore the image.

I personally use **Gemma in its MoE version** (`gemma-4-26B-A4B-it-UD-Q4_K_M.gguf`) for
quality English descriptions. MoE (Mixture of Experts) means only a fraction of the 26B
params run per token — in speed and VRAM appetite it's closer to a small model, in quality
closer to a large one. oobabooga configures MoE automatically; llama.cpp needs no special
flags for a basic launch.

If you want **tags only**, no description, use [WD14](https://github.com/toriato/stable-diffusion-webui-wd14-tagger)
rather than a VLM (see [Why not WD14](#why-not-wd14)). Tag Manager's strength is the
"tags + prose" combo.

Alternatives if Gemma doesn't fit:

- **Qwen2.5-VL** — very detailed, reads text in the image, sizes 3B/7B/72B. Dense (not
  MoE), so large versions are heavier on VRAM.
- **MiniCPM-V** — light and fast, good for weak machines; falls behind on complex scenes.
- **Pixtral** — strong whole-scene understanding, but memory-hungry.
- **LLaVA** — a classic with tons of guides, but older and weaker on detail.
- **Llama 3.2 Vision** — stable, average tag detail.

Take as many params as fit in your VRAM with headroom. And `mmproj` is required for any of them.

</details>

<details>
<summary>What processing modes are available?</summary>

The “Processing mode” dropdown on the generation tab:

- **Resume** — skip what this app has already done (tracked by its registry). Foreign
  `.txt` files are not counted as “done”.
- **All files** — overwrite everything.
- **Only missing** — process images that have no `.txt` at all.
- **Skip by date** — skip if the `.txt` is newer than the image.
- **Update existing** — smart re-run on existing captions, see next question.

</details>

<details>
<summary>How does “Update existing” mode work?</summary>

A mode for refining already-done captions without losing manual edits. Safe to run
repeatedly — if nothing changed, files won't be rewritten.

**Mechanism** — how to produce the new text:

- *Augment existing* — the model sees the image AND the old caption, replies only with
  what's missing/wrong. Faster and cheaper, good for adding tags.
- *Full regen + merge* — the model generates a caption from scratch, then the app merges
  old and new per the strategies below.

**Tag strategy** — what to do with the tag line:

- *Add missing tags* — tags from the new caption that don't exist in the old one are
  appended. No duplicates.
- *Replace tags with new* — tag line is taken entirely from the new caption.
- *Keep old tags* — tags are not touched.

**Prose strategy** — what to do with the descriptive blocks (COMPOSITION / CHARACTERS etc.):

- *Keep old prose* — prose stays as is (safe default).
- *Take new prose* — prose is replaced from the new caption.

**Manual edit policy** — if you edited the `.txt` by hand after generation:

- *Don't touch* — file is skipped entirely (default).
- *Only add tags* — append missing tags, don't change prose.
- *Defer for review* — file is added to a list for manual inspection.
- *Update normally* — edits are not protected.

**Filters** — which files are included in the update:

- *Prompt changed* — caption was made with a different prompt than current.
- *Model changed* — caption was made with a different model.
- *Poor quality* — caption fails the quality check.
- *All files* — update everything that has a `.txt`.

A `.bak` is created next to each file before writing.

</details>

<details>
<summary>Generation takes 8–10 minutes — is that normal?</summary>

For thinking models on complex scenes — yes. The timeout and `Max tokens` in `config.py`
are set generously so a long but correct analysis doesn't get cut off. Simple images are
faster.

</details>

<details>
<summary>I broke tags with a bulk edit. How do I undo?</summary>

Before every bulk operation a `.bak` is created next to the file. The “Tags” tab →
“History” sub-tab lists recent operations and has a “Rollback last” button that restores
`.txt` from backup. If another run happened after the operation, the `.bak` is overwritten
and rollback is no longer possible.

</details>

<details>
<summary>What is the tag stoplist?</summary>

A `stoplist.txt` file (one tag per line, `#` = comment). Tags from the stoplist are
automatically removed from every caption during generation. You can also apply the stoplist
to an existing dataset in bulk (Tags tab). Edit it in the sidebar.

</details>

<details>
<summary>Where are settings and presets stored?</summary>

In the app folder: `settings.json`, `presets.json`, log in `processing_log.txt`. All
local, never committed.

</details>

## License

[MIT](LICENSE) © OrcPoin
