# Keyboard environment context

Use this reference when planning navigation, text entry, or batches in Omarchy,
Chromium, Obsidian, Ghostty, Nautilus, Herdr, OBS, LazyVim, Neovim, or DaVinci Resolve.
The catalogs are available to search within their declared coverage;
load the desktop context and the apps needed for the user's task. A catalog lookup
establishes a candidate input, while current UI evidence establishes its target
and whether the command is applicable.

From the plugin or repository root, collect current bindings and merge a vault's
Obsidian overrides:

```bash
python3 scripts/keyboard_context.py refresh --vault /path/to/vault
python3 scripts/keyboard_context.py show
python3 scripts/keyboard_context.py show --environment omarchy --search browser
python3 scripts/keyboard_context.py show --environment chromium --search focus
python3 scripts/keyboard_context.py show --environment obsidian --search new-file
python3 scripts/keyboard_context.py show --environment ghostty --search clipboard
python3 scripts/keyboard_context.py show --environment herdr --search split
python3 scripts/keyboard_context.py show --environment obs --search recording
python3 scripts/keyboard_context.py show --environment lazyvim --search picker
python3 scripts/keyboard_context.py show --environment neovim --search 'window split'
python3 scripts/keyboard_context.py show --environment davinci-resolve --search multicam
```

Omit `--vault` when Obsidian is not involved. Default output is the ignored local
`.dev/keyboard-context/` directory: `index.md`, one complete Markdown table per
environment, and corresponding JSON catalogs. `show --environment NAME` returns
every binding in that catalog; `--search` returns all matching records without a
top-N cutoff and reports total and returned counts. The JSON files in
[keyboard/](keyboard/) contain portable source references. Machine-specific
bindings and vault paths stay in the generated local directory.

Refresh also reads Herdr's `config.toml`, OBS's selected profile and scene
collection, and Neovim's configuration manifests. Override their locations with
`--herdr-config FILE`, `--obs-config DIRECTORY`, and `--nvim-config DIRECTORY`.
`HERDR_CONFIG_PATH` and `XDG_CONFIG_HOME` are respected. It inventories Neovim Lua
files by checksum without executing startup code. OBS stream settings and Herdr
custom-command bodies are not copied into the generated catalogs.

Refresh at the start of a task that depends on keyboard bindings, and after a
configuration or app change. Check `errors`, `collected_at`, `evidence`,
`version_matches`, `coverage`, and any `compositor_matches`. Refresh is read-only
with respect to the desktop: it never launches apps, dispatches keys, reloads
configuration, or edits preferences. Hyprland queries require desktop socket
access. A failed query is recorded explicitly; stale runtime catalogs are removed.

## What each catalog covers

| Environment | Source and completeness boundary |
|---|---|
| Omarchy 4 / Hyprland | Every active record from `hyprctl binds`, including duplicates, press/release bindings, repeat flags and submaps. Captures current overrides; event-created bindings can appear or disappear later. |
| Chromium | Every Linux mapping in Chromium 151.0.7922.173's `chrome/browser/ui/accelerator_table.cc`, including qualified debug/printing mappings and standard clipboard mappings, plus nine page/text navigation shortcuts. Website handlers, extension commands, DevTools-internal bindings and the full embedded editor keymaps are outside that source. |
| Obsidian | All 34 assigned application-command hotkey declarations in the installed 1.13.7 `app.js`, expanded to 43 Linux bindings. Vault `hotkeys.json` replaces defaults, including empty arrays that disable a binding. This is source/config evidence, not a live enumeration of registered commands. |
| Obsidian editor | Documented Linux editing and selection bindings. Normal editor mode only; Vim and modal controls require their own context. |
| Ghostty | Every binding returned by `ghostty +list-keybinds --plain`, which reads effective configuration on disk. Already-running windows can still have older configuration. Terminal applications and shell keymaps are separate. |
| Nautilus | All 61 entries in the installed 50.2.2 keyboard-shortcuts dialog: 47 literal accelerators and 14 action bindings resolved from matching Nautilus and libadwaita sources. Extension actions and inherited GTK editing bindings are separate. |
| Herdr | All 59 scalar/action defaults from 0.8.2's `KeysConfig`, including unassigned actions and expanded indexed bindings, plus navigation and copy-mode controls. Refresh merges configured keys and exposes competing sequences. This is declared configuration; Herdr's current help resolves validation, remote-client settings and active bindings. Other dialogs, resize-mode internals and plugin-added bindings are separate. |
| OBS Studio | 50 default shortcuts/gestures from 32.2.2 frontend forms, Linux shortcut assignments and the official preview reference. Refresh adds hotkeys from the selected profile and scene collection, retaining source identity and unassigned entries. Inherited Qt editing keys, arbitrary widget handlers and plugin defaults are separate. |
| LazyVim | All 491 mapping rows in the official generated keymap reference captured September 7, with modes and optional-extra module names. This is a documentation snapshot, not an installed-version match. Refresh records the installed lockfile revision, configured extras and Lua checksums; it does not resolve arbitrary Lua or live buffer mappings. |
| Neovim | All 719 help-tagged keyboard/mouse command rows before the Ex-command section of installed 0.12.5's `index.txt`, plus two terminal-mode escape sequences. Includes text objects, aliases and argument placeholders; excludes colon commands, untagged completion-popup rows and custom mappings. |
| DaVinci Resolve | 248 reviewed core bindings from the official 21.1 manual, translated to Linux key names: project/media commands, Edit playback and track controls, multicam defaults for angles 1-9, Photo, and selected Fusion, Color and Fairlight operations. Each row includes its manual page, panel and conditions. This is a bounded documentation snapshot; it does not enumerate all commands or read the installed version, active preset or user overrides. See [Resolve coverage and validation](keyboard/davinci-resolve.md). |

Every source catalog identifies its version and source, with checksums for
extracted application data. Refresh checks installed package versions and flags
mismatches; it does not silently claim older source defaults describe a new build.
Coverage varies by catalog: some enumerate a source table, while Resolve provides
a reviewed core subset. None inventories every possible command or UI state in
an extensible application.

`version_matches: null` means a version match was not established. In particular,
the LazyVim documentation snapshot must not be treated as a verified installed
keymap. Optional extras listed as false are reference material; a listed extra
still requires the plugin and appropriate buffer/LSP capabilities.

## Precedence and applicability

Check desktop bindings before choosing an app shortcut. `compositor_matches`
identifies possible interception, including release bindings. It is advisory:
physical keycodes, keyboard layout, active submaps, remapping, input methods,
remote-desktop interception and non-consuming bindings need separate evidence.
Multiple records for one chord may be intentional; preserve them all.

Then check the app, vault/profile, enabled plugins, editing mode, focused
container and control. Obsidian custom keys replace defaults for a command rather
than adding to them. A disabled or unregistered command does not gain a usable
shortcut just because a default appears in the catalog. In a terminal, distinguish
Ghostty's bindings from those of Bash/Readline, a shell editor mode, Tmux or Neovim.

For a terminal inside Herdr, check the input path in order: compositor, terminal,
Herdr mode/prefix, then the application inside its pane. Herdr `prefix+c` means
press and release the configured prefix, then press `c`. The local September 7
configuration uses `Ctrl+Space`, while the source default uses `Ctrl+B`. Indexed
`1..9` entries expand to nine separate bindings. Empty bindings disable an action;
unknown settings and colliding candidates require Herdr's help, opened with its
configured help sequence. `herdr config check` validates the file without reload.

OBS recording, streaming, scene-switching and mute hotkeys are configurable.
`configurable_commands` lists identifiers, not assigned shortcuts. Check
`configured_unassigned` and the selected profile/collection before choosing an
input. A source mute binding applies to that source, not every microphone.

Vim notation is case-sensitive: `n` and `N` differ. `<leader>ff` is a sequence;
`<C-w>v` is a chord followed by a letter. Defaults are Space for LazyVim's leader
and backslash for its local leader, but executable configuration may change them.
Normal, Insert, Visual, Select, operator-pending, command-line and terminal modes
must be distinguished. Table mode codes are `n`, `i`, `x` (Visual), `s` (Select),
`v` (Visual and Select), `o`, `c`, and `t`. `{motion}`, `{register}` and similar
placeholders require arguments. The collector deliberately skips automatic chord
collision matching for Vim notation and gestures; translate them first.

In an existing editor, use `:verbose nmap`, `:verbose imap`, `:verbose xmap`,
`:verbose omap`, `:verbose cmap`, or `:verbose tmap` to inspect relevant mappings
and their origin. For a specific candidate, `:verbose nmap <Space>ff` narrows the
result. LazyVim's which-key popup and keymap picker provide local exploration when
their own bindings are confirmed. A fresh headless editor can differ from the
user's buffer and may run installation hooks; it is not a substitute for live
buffer evidence.

On the September 7 reference machine, bare `Home` has dictation press and release
bindings. It cannot be assumed to reach Chromium's page navigation or Obsidian's
line navigation. This is a local example: refresh to establish the current rule.

Shortcut strings are reference notation, not directly executable `press_key`
arguments. GTK `<Primary>` and Obsidian `Mod` mean Ctrl on Linux. GTK tables may
list alternatives separated by spaces. Physical codes, mouse combinations,
hold/release actions and key sequences require the corresponding supported input
operation; do not flatten them into a single chord or guess their layout mapping.

For planning, associate a candidate with its exact target, starting focus/mode,
expected effect, outcome check, and source freshness. A known shortcut can remove
navigation work; it does not establish success. A menu's Tab or arrow behavior
must be observed or established by a relevant widget contract. Stop a batch where
the next action requires interpreting newly revealed content. `focus_until` and
semantic activation remain proposed features, not current `run_steps` actions.

## Extending and refreshing source coverage

Prefer application-provided read-only command/keymap exports. For Obsidian,
the official CLI documents `hotkeys` and command discovery, and Settings → Hotkeys
shows configured bindings. If that interface is already enabled, use its live
output to resolve plugin commands and current bindings. The collector does not
enable the CLI or attach a debugger. Missing `hotkeys.json` means there are no
file overrides; it does not mean the file contains the default keymap.

For browser extensions, inspect the profile's keyboard-shortcut settings
(`chrome://extensions/shortcuts`) when an extension is relevant. Site commands
come from that site's controls, accessible actions, documentation or verified
exploration. For additional apps, add a separate versioned catalog with a stated
source and completeness boundary, rather than mixing them into browser defaults.

The checked-in factual mappings were derived from:

- [Chromium 151.0.7922.173 accelerator source](https://chromium.googlesource.com/chromium/src/+/151.0.7922.173/chrome/browser/ui/accelerator_table.cc), Linux branches with branding disabled. Printing/debug entries retain conditions. The adjacent [license](keyboard/chromium-LICENSE.txt) applies to Chromium-derived data.
- `/usr/lib/obsidian/obsidian.asar!app.js`: literal `hotkeys` declarations, Linux branches and numbered-tab expansion; no app code was executed to extract them.
- `/usr/bin/nautilus` resource `/org/gnome/nautilus/shortcuts-dialog.ui`: every `AdwShortcutsItem`, with action-based accelerators resolved from [Nautilus application actions](https://github.com/GNOME/nautilus/blob/50.2.2/src/nautilus-application.c), [window actions](https://github.com/GNOME/nautilus/blob/50.2.2/src/nautilus-window.c), and [libadwaita 1.9.3's shortcuts action](https://github.com/GNOME/libadwaita/blob/1.9.3/src/adw-application.c).
- [Chrome shortcuts](https://support.google.com/chrome/answer/157179), [Obsidian hotkeys](https://obsidian.md/help/hotkeys), [Obsidian editing shortcuts](https://obsidian.md/help/editing-shortcuts), and [Obsidian CLI](https://obsidian.md/help/cli).
- [Herdr 0.8.2 key configuration](https://github.com/herdrdev/herdr/blob/v0.8.2/src/config/model.rs), its help and copy-mode handlers; [Apache license](keyboard/herdr-LICENSE.txt).
- [OBS 32.2.2 frontend sources](https://github.com/obsproject/obs-studio/tree/32.2.2/frontend) and [preview shortcuts](https://obsproject.com/kb/keyboard-shortcuts). Catalog data consists of factual key/action mappings.
- [LazyVim's generated keymap table](https://github.com/LazyVim/lazyvim.github.io/blob/main/docs/keymaps.md), with each optional-extra requirement retained; [LazyVim license](keyboard/lazyvim-LICENSE.txt).
- Installed Neovim `/usr/share/nvim/runtime/doc/index.txt`; see the adjacent [documentation attribution](keyboard/neovim-NOTICE.md). Mappings retain their help tags for in-editor lookup.

To revise a source catalog, obtain the matching version, enumerate the full named
source, preserve conditions and alternatives, update its checksum and coverage,
and regenerate the local snapshot. Newly installed or updated apps can still be
explored through fresh accessibility and visual evidence while a source catalog
is unavailable or stale.
