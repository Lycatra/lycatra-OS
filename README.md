# Lycatra OS

Lycatra OS is a modular studio desktop for Omarchy. Super+Space searches installed commands, local files, and optionally the signed-in Lycatra wiki. The studio catalog includes individual phone, media, dictation, developer, keyboard layout popup, update indicator, web app, and style components. Run commands as the desktop user in an Omarchy session. The menu adapter targets the reviewed 4.5.0-3 menu source.

## Signed installation

The first distribution is a preview for Omarchy 4.5.0-3. To test a published preview, set `LYCATRA_DESKTOP_RELEASE` to its exact tag. The bootstrap verifies a Sigstore signature from the release workflow in this repository using GitHub OIDC before unpacking the archive.

```bash
LYCATRA_DESKTOP_RELEASE=v0.1.0-preview.2 bash <(curl -fsSL https://raw.githubusercontent.com/Lycatra/lycatra-OS/v0.1.0-preview.2/release/bootstrap.sh)
```

## Install from this checkout

Run `./install.sh` and select components. For unattended setup use `./install.sh search` or `./install.sh studio`. The installer installs its manager under ~/.local/bin. Search downloads a checksummed multilingual E5 model (~487 MB) and installs its pinned CPU runtime in a private Python environment.

## Agent setup

1. Run `./lycatra-desktop catalog` and `./lycatra-desktop inspect studio --json`. Explain what each selected integration does and which existing files conflict. Keep personal configuration and resolve each reported conflict with the owner.
2. Use `./lycatra-desktop plan COMPONENT... --json` to preview the selection. Interactive setup offers to install missing packages through Omarchy. Agents can explicitly select `--install-deps` after showing the package plan. Phone tools are provided by kdeconnect, android-tools and scrcpy. Media uses libpulse, python-gobject and PipeWire. Dictation requires the Omarchy Superwhisper installation. The developer selection uses Zed and the Codex CLI. Search uses Python 3.14 with venv support and poppler for PDF extraction.
3. Run `./install.sh COMPONENT...`, then `lycatra-desktop verify --json`. Ask the person to try Super+Space, a file, a command and a wiki page. Use `lycatra-desktop status` to monitor the first crawl. For a staging home use `--stage` to install files without starting services, downloading a model or enabling plugins.
4. Offer wiki search after signing in to the Lycatra CLI and the Lycatra web app. Explain that ordinary search phrases will be sent to Lycatra when this is enabled. Enable with `lycatra-desktop configure --wiki on`; disable with `--wiki off`. A `files:` prefix keeps the query on the computer. `wiki:` searches only the wiki. The web app uses the existing Omarchy launcher and browser profile. Changing the CLI sign-in clears visible wiki results and requires a new opt-in.
5. Pair the user's own phone through KDE Connect. The phone panel supports normal paired devices without ADB. For optional call controls, configure LYCATRA_PHONE_ID, LYCATRA_PHONE_ADB and LYCATRA_PHONE_BLUETOOTH in the shell session environment using the user's own device. The remotePhones panel setting maps paired device IDs to screen-mirroring endpoints. Dictation requires the user to choose a microphone and complete any provider sign-in.
6. Run `lycatra-desktop apply COMPONENT...` to add features, `remove COMPONENT...` to uninstall, and `rollback` to restore the last file transaction. `update` downloads and verifies the newest release in the current channel. Use `update --channel stable` or `update --channel preview` to select a channel. Rollback restores the previous managed files and manager version. Existing local edits cause the operation to stop and list conflicts.

## Search settings

Press **Super+Space**, type **Search settings**, and press Enter. The same controls are under **Setup → Search settings** and available with `lycatra-desktop settings`.

Choose which Omarchy applications and commands appear alongside files and wiki pages. **Local files and folders** controls on-device search and indexing. **Local semantic matches** enables passage retrieval alongside filename and keyword search. **Offline mode** suppresses wiki requests. Wiki search requires a separate opt-in for the signed-in account.

Agents can apply these switches with `lycatra-desktop configure --help`.

Choose **Embedding model** to select multilingual E5 Small or the smaller, English-only quantized E5 Small v2. The model worker verifies the download and builds an independent index before switching. The current model continues serving searches during preparation. Open **Index status** to see progress, and use **Indexed folders**, **Excluded folders**, or **Pause indexing** to control coverage.

## Index coverage and privacy

The default root is the current user's home. Configure explicit roots and exclusions with repeatable `--root PATH` and `--exclude PATH` on the configure command. Hidden entries, symlinks, other mounted filesystems, generated directories and common credential files are excluded. Visible names of supported regular files and folders appear first. Content coverage includes Markdown, text, common source files, PDFs with text, DOCX and ODT. Large documents have bounded extraction and embedding coverage. Wiki attachments match by filename and open the owning wiki page.

The index and receipt directory is private to the Unix account. Disk encryption is provided by the host. Filename scans and content indexing run independently. Background content indexing pauses at a 2 GiB index budget or 512 MiB of free disk. The indexer service can open Unix sockets only. Use the pause, resume and reindex commands to control indexing. Search phrases and file contents are excluded from application logs. Operational messages are available in `journalctl --user -u lycatra-search.service`.

## Release qualification

Run `agent-workload python -m unittest discover -s tests -v`. Run `tests/semantic_smoke.py` under the installed model environment. Verify release signatures with release/bootstrap.sh using the exact repository workflow and release tag identity. Qualify each new Omarchy menu hash on a clean installation and an upgraded machine. Validate the app-library search, keyboard selection, account switching, disabled network, file opening, phone pairing, audio focus, upgrades and uninstall before promoting the release.

Full-library hardware benchmarks, clean-machine qualification and semantic retrieval inside wiki attachments are release milestones. See CONTRIBUTING.md to promote a private experiment into a distributable component.
