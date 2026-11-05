# Changes

## 1.2.2 - 2026-11-05

- Update image and PDF dependencies to Pillow 13.0.0 and pypdf 6.21.4.
- Point the Linux download instructions to the latest stable release.

## 1.2.1 — 2026-11-04

- Open a fitted Appearance settings dialog directly, with system/custom font and accent choices and a reset to System. Linux desktop matching no longer needs system Python or PyGObject.
- Resolve desktop preferences independently through native/portal settings, KDE or explicit qt6ct preferences, with honest readable fallbacks. Refresh preserves explicit choices.
- Measure minimum windows from controls and a useful workspace instead of multiplying the complete window size by the font size. Long content and small-screen overflow can scroll while actions stay visible.
- Keep source/prepared-page inspection and Prepare → inspect → Save behavior, with measured page rows and monitor-bounded inspection/help windows.

## 1.1.0 — 2026-11-03

All interface text follows the configured desktop UI size, including headings, Help, tables and status. Document pages, exported captions and watermarks retain their own content typography.

Primary actions, enabled toggles, selections and progress use the system accent, with readable contrasting text. Secondary actions stay neutral. Desktop settings helpers use the host library environment, including in portable packages.

Compact controls preserve more vertical working space. File Renamer keeps its rule controls, Rename and Undo together: Rename shifts horizontally when needed, and resizing stops before the row collides. Layouts adapt to enlarged fonts and constrained monitor work areas.

## 1.0.5 — 2026-11-03

- Restore a useful minimum window size so the preview, everyday controls, task actions and status remain visible together.
- Measure the layout at the system font size; adapt the minimum to the current monitor and use scrolling when its work area is genuinely too small.
- Repair undersized restored windows while keeping the system-font improvements.

## 1.0.3

- Match the operating system UI font family and configured text size, including Linux Mint Cinnamon and Ubuntu GNOME desktop settings. Reopen the app after changing desktop font settings.
- Keep controls readable at larger text sizes: toolbar and task actions wrap, while configuration and page previews scroll when needed.
- Preserve the desktop font through Light, Dark and System appearance changes.

## 1.0.1

- Keep file chooser navigation and view icons visible in Light and Dark appearance. Compact icon buttons now retain space for their icons and keyboard focus, while the main window keeps its existing control sizes.
- Draw file chooser navigation arrows with the active palette so enabled arrows stay readable in Dark appearance.
- Preserve Linux launcher and desktop-entry line endings in source archives created on Windows.
