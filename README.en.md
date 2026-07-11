<div align="center">

# 🏷️ Tag Manager

[![Русский](https://img.shields.io/badge/Русский-e1e4e8?style=for-the-badge)](README.md)&nbsp;[![English](https://img.shields.io/badge/English-0969da?style=for-the-badge)](README.en.md)

**Generate and edit captions for image-model (LoRA) training datasets**

A local [Streamlit](https://streamlit.io/) web app: it captions a folder of images
through any multimodal LLM with an OpenAI-compatible API — and helps you clean up a finished dataset.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)
![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)

</div>

---

## 📑 Contents

- [Why](#-why)
- [Screenshots](#-screenshots)
- [Features](#-features)
- [Quick start](#-quick-start)
- [How to use](#-how-to-use)
- [Caption format](#-caption-format)
- [Project structure](#-project-structure)
- [FAQ](#-faq)
- [License](#-license)

---

## 🎯 Why

When training a LoRA / fine-tune for generative models (Stable Diffusion, Anima, Pony, etc.),
every image needs a text caption next to it (`image.jpg` → `image.txt`). Captioning hundreds
of images by hand is slow. **Tag Manager** does it for you:

1. **Generation** — runs a folder through a local vision model and writes captions using your prompt.
2. **Cleanup** — once the dataset is ready, it helps you bulk-edit tags, add a
   trigger word and review the result in a gallery.

Everything runs **locally** — images and captions never leave your machine, no keys or cloud needed.

---

## 🖼 Screenshots

<div align="center">

<b>Main screen — caption generation</b><br>
<img src="docs/screenshot-main.png" alt="Main screen" width="760">

<br><br>

<details>
<summary><b>📸 More screenshots (click to expand)</b></summary>
<br>

<table>
<tr>
<td align="center" width="50%"><b>“Tags” tab</b><br><sub>bulk dataset edits</sub><br><img src="docs/screenshot-tags.png" alt="Tags tab"></td>
<td align="center" width="50%"><b>“Gallery” tab</b><br><sub>view and edit</sub><br><img src="docs/screenshot-gallery.png" alt="Gallery tab"></td>
</tr>
</table>

<b>Sidebar — API settings and generation parameters</b><br>
<img src="docs/screenshot-api.png" alt="API settings" width="280">

</details>

</div>

---

## ✨ Features

### Caption generation
- 📁 Folder picker: type a path **or** use the native “Browse” dialog; recursive or top level only.
- 🖼 Formats `.jpg`, `.jpeg`, `.png`, `.webp`; one same-named `.txt` per image.
- 🔀 Processing modes: resume (only files this app hasn't done yet), overwrite all, only missing captions, skip by `.txt` date.
- 🧩 Multimodal image upload (base64 `image_url`), strictly one file at a time.
- ♻️ Auto-retry on a “bad” caption (up to 3 times) with a reinforced prompt + network-error retry with backoff (1s → 2s → 4s).
- ⏳ Understands `503 Loading model` — waits patiently while the server loads the model.
- ⏯ Interruptible processing: pause / resume / stop; on streaming servers stop reacts within a couple of seconds.
- 🎛 Built-in prompt presets + save your own to `presets.json`.
- 🏷 Optional style **trigger word** — prepended as the first line of every `.txt`.
- 💾 Progress in `progress.json` — you can continue after a restart.
- 👁 Per-file manual actions: Accept / Regenerate / Edit / Skip.

### Finished-dataset management
- 📊 **Tag statistics** — frequency of each tag by number of files (spot rare and junk tags).
- 🔧 **Bulk tag operations** with preview and backup (`.bak`): remove, add, replace a whole tag, substring replace (typos). Prose descriptions are left untouched.
- 🎯 **Trigger-word retrofit** — add/remove the trigger across all `.txt` at once (idempotent).
- ↩️ **Undo** the last bulk operation from `.bak`.
- 🖼 **Gallery** — view image by image, “missing caption” filter, search by tag/substring, manual edit and delete.
- 🩺 **Dataset “health” summary**: how many images, captions, missing captions, with/without trigger, available backups.

---

## 🚀 Quick start

### 1. Get the app

You need **Python 3.10+** installed. Pick either way:

<details>
<summary><b>📦 Option A — download ZIP (no Git, simpler)</b></summary><br>

1. Open the [**Releases**](https://github.com/OrcPoin/tag-manager/releases/latest) page
   and download **Source code (zip)** (or use `Code → Download ZIP` on the repo's main page).
2. Extract the archive into a folder of your choice.
3. Open the folder in a terminal and install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```
</details>

<details>
<summary><b>🔧 Option B — clone with Git (easy to update)</b></summary><br>

```bash
git clone https://github.com/OrcPoin/tag-manager.git
cd tag-manager
pip install -r requirements.txt
```

Updating to a newer version later — a single `git pull`.
</details>

### 2. Start a local OpenAI-compatible server

With a **multimodal (vision)** model loaded — e.g. Qwen2-VL, LLaVA, Pixtral,
MiniCPM-V, Gemma 3, Llama 3.2 Vision:

<table>
<tr><th>Server</th><th>Example command</th><th>API</th></tr>
<tr>
<td><a href="https://github.com/ggerganov/llama.cpp">llama.cpp</a></td>
<td><code>llama-server -m model.gguf --mmproj mmproj.gguf --port 5005</code></td>
<td><code>http://127.0.0.1:5005/v1</code></td>
</tr>
<tr>
<td><a href="https://github.com/oobabooga/text-generation-webui">oobabooga</a></td>
<td><code>python server.py --api --auto-launch</code></td>
<td><code>http://127.0.0.1:5000/v1</code></td>
</tr>
</table>

### 3. Run the app

```bash
streamlit run app.py
```

On Windows you can just double-click **`run.bat`** — it opens in your browser.

---

## 📖 How to use

1. In the sidebar, set the **API URL** and **model name**, click “Check connection”
   (you can pull the active model name with the 🔄 button).
2. Choose a **folder** of images and a processing **mode**.
3. Pick a **preset** or write your own system/user prompt; optionally set a **trigger word**.
4. Click **Start**. Watch the progress and log; pause and edit captions by hand if needed.
5. When the dataset is ready — go to the dataset tabs: check tag statistics, clean up junk, apply the trigger, browse the gallery.

---

## 🧾 Caption format

The app doesn't force a format — it's entirely defined by your prompt. The default
preset produces a **hybrid** of “tags + prose”, convenient for a style LoRA:

```
1girl, blue hair, smile, school uniform, outdoors, day

A medium shot with the subject centered.

(blue hair, on the left: she waves at the viewer, smiling.)
```

Bulk tag operations understand this format: they only touch the **tag lines**
(comma-separated short fragments) and don't corrupt prose or parenthesized character blocks.

---

## 🗂 Project structure

```
app.py                 UI and orchestration
config.py              settings and default prompts
run.bat                one-click launch (Windows)
requirements.txt       dependencies
presets.example.json   example of a user preset
core/
  image_scanner.py     find images and filter by mode
  caption_client.py    client to the server: base64, streaming, retry/backoff
  worker.py            background worker (start/pause/resume/stop)
  quality.py           “bad caption” evaluation
  presets.py           built-in + user presets
  dataset.py           tag stats, bulk edits, trigger, gallery
  state.py             progress, flags, persistence
  registry.py          registry of files processed by this app
  logger.py            file log + buffer for the UI
  app_settings.py      “sticky” UI settings between sessions
  folder_dialog.py     native folder-picker dialog (tkinter)
```

---

## ❓ FAQ

<details>
<summary><b>Do I need internet / an API key?</b></summary><br>

No. The app only talks to your local server (`127.0.0.1`). No key is required —
the API key field is filled with a placeholder. Images and captions are never sent anywhere.
</details>

<details>
<summary><b>Which model do I need?</b></summary><br>

Any <b>multimodal (vision)</b> model exposed over an OpenAI-compatible
<code>/v1/chat/completions</code> endpoint with <code>image_url</code> support.
A plain text model won't work — it will simply ignore the image.
</details>

<details>
<summary><b>Processing is very slow / generation takes 8–10 minutes — is that normal?</b></summary><br>

For reasoning/thinking models on complex scenes — yes. In <code>config.py</code> the timeout
and <code>Max tokens</code> are set generously so a long but correct analysis doesn't get cut off.
Simple images are much faster.
</details>

<details>
<summary><b>What does the “trigger word” do?</b></summary><br>

It's a fixed style-activation word that must be <b>byte-for-byte identical</b>
across the whole dataset. The app prepends it as the first line of every <code>.txt</code>
so the model doesn't mangle it during generation. It can be left empty.
</details>

<details>
<summary><b>I accidentally broke tags with a bulk operation. How do I undo?</b></summary><br>

Before every bulk edit a <code>.bak</code> backup is created next to the file.
The dataset tab has an undo button — it restores the <code>.txt</code> from <code>.bak</code>.
</details>

<details>
<summary><b>Can I interrupt processing and continue later?</b></summary><br>

Yes. Progress is written to <code>progress.json</code>. After a restart, in “resume” mode
the app finishes only the unprocessed files and won't touch other people's old <code>.txt</code>.
</details>

<details>
<summary><b>Where are my presets and settings stored?</b></summary><br>

In the app folder: presets — <code>presets.json</code>, UI settings — <code>settings.json</code>,
log — <code>processing_log.txt</code>. All of them are in <code>.gitignore</code> (local) and never committed.
</details>

---

## 📜 License

[MIT](LICENSE) © OrcPoin

<div align="center">
<sub>Made for those tired of captioning datasets by hand.</sub>
</div>
