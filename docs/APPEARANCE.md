# Desktop appearance

Every interface text surface uses the same effective UI font and size; headings use weight. Actual document pages, photo content and exported typography are preserved. Window decorations remain controlled by the operating system.

Settings opens a fitted dialog directly. System matching is the default. Optional custom font/size and accent choices apply throughout the interface; Reset appearance to System changes appearance only. Refresh system settings preserves explicit overrides. No diagnostic-copy button is shown in production.

Linux appearance reads supported desktop/portal, KDE and explicitly selected qt6ct preferences independently. A small isolated native helper can query optional host GTK settings without system Python or PyGObject. Missing integration retains readable Qt/application fallbacks; a fallback is not proof that system matching succeeded. Windows retains native font and personalization settings.

Display scaling remains Qt's responsibility. Provider-specific font normalization avoids applying text scaling twice. Theme changes preserve the resolved font and accent. If the desktop sends no change notification, use Refresh system settings after changing its preferences.

Primary actions, selected controls/rows and progress use the resolved accent with readable contrasting text. Secondary actions remain neutral, and disabled controls and focus remain distinct. Content viewers may scroll; ordinary forms fit on adequate screens and reserve scrolling for constrained work areas. Main-window minimums combine measured controls with a useful preview/list reserve.

Local appearance diagnostics record the build, providers, effective values and bounded fallback reasons. They exclude document names, home paths and full environment dumps. Nothing is uploaded automatically.
