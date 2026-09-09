# DaVinci Resolve shortcut catalog

[davinci-resolve.json](davinci-resolve.json) contains 248 core bindings from the
official [DaVinci Resolve 21.1 manual](https://documents.blackmagicdesign.com/UserManuals/DaVinci_Resolve_21.1_Reference_Manual.pdf).
The file records the manual checksum and each binding's page URL. Commands are
human-readable catalog labels, not scripting API or exported preset identifiers.

The catalog covers project/media operations, editable metadata cells, Edit
playback and markers, selection and editing tools, track destinations/locks/auto
select, multicam, Photo, and selected Fusion, Color and Fairlight operations.
The main tables reviewed visually are on pages 617, 864, 871, 889-890, 967-968
and 1043-1044; additional prose references are identified in individual rows.
This is a core subset, not an exhaustive export of Keyboard Customization.

The manual specifies Command -> CTRL and Option -> ALT for Linux on pages 54
and 68. Bindings use XKB names such as `slash`, `backslash`, `bracketleft`,
`bracketright`, `equal`, `minus` and `grave`; letter keys are lowercase and SHIFT
is explicit. Key ranges are expanded without inventing chords beyond the
documented range. Physical key positions and non-US layouts are not inferred.
Ambiguous Delete/Forward Delete translations and held-key or pointer gestures
are excluded from the assigned bindings.

Resolve's UI context matters. For example, `ALT+1` selects a track destination
in the Edit timeline and switches an angle in a multicam viewer. The catalog
keeps these actions distinct. `modes` are planning labels for observed contexts,
such as `edit-timeline`, `edit-multicam-viewer`, `photo-inspector` and
`fusion-node-editor`; they are not native Resolve mode IDs or automatic page
detection. The current observer does not establish these facts by itself.
Verify the current page, panel, selection and applicable command before input.

The metadata list-view documentation on page 410 names Tab and Enter without
specifying each key's row/column direction. Both are references for exploration;
learn and verify the actual transition before forming a navigation recipe. Photo
arrow directions follow the table on page 617 and need checking against the
current album order. Toggles such as playback, track controls, tagging and viewer
display require checking their existing state.

The multicam defaults cover angles 1-9. Page 1044 describes assigning dedicated
shortcuts through angle 25; the remaining chords are not guessed. Track defaults
are limited to the numbered ranges in the reviewed tables. Dedicated Enable Clip
and Disable Clip commands are retained as configurable entries without assigned
keys, separately from the `d` toggle.

No installed version or custom preset is read by this initial collector. The
catalog uses `version_kind: documentation_snapshot`, so refresh keeps version
compatibility unknown. Page 122 explains that custom presets created in 21.1 can
inherit updated non-conflicting defaults, making version and preset validation
necessary after an upgrade. Use Resolve's Keyboard Customization window to inspect
the active preset and export it before implementing an override importer. Do not
interpret absent entries in an unexamined export as disabled defaults.

Refresh and inspect the entire local table, or search it:

```bash
python3 scripts/keyboard_context.py refresh
python3 scripts/keyboard_context.py show --environment davinci-resolve --search multicam
```

For `context_for_task`, use `exact.app: davinci-resolve`, optionally with a
human-readable `command_id` or an exact case-sensitive `shortcut`. For example:

```json
{"intent":"switch multicam angle","exact":{"app":"davinci-resolve","command_id":"Clip > Multicam Switch > Angle 1"},"max_bytes":8192}
```

The existing normalizer, immutable snapshot store, compositor-interception checks
and applicability gate process this catalog. Default bindings remain exploration
references until the required evidence is established. Neither a source default
nor caller-supplied facts prove that a key is currently available. No Resolve
command or desktop input is executed by loading or refreshing the catalog.
