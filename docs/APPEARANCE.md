# Desktop appearance

All interface text uses the configured desktop UI font family and effective size. Headings use weight for emphasis. This includes buttons, tables, menus, tooltips, dialogs, Help and status. Typography inside actual documents, photos and exported content is unchanged. Window decorations belong to the operating system.

Primary actions, enabled toggles, selected items and progress use the native personalization/selection accent. An accent can differ from the titlebar close-button color. Normal secondary actions remain neutral. Text on accent fills uses contrasting black or white; focus and disabled states remain distinct.

Windows uses the native Qt font integration and Windows personalization settings. Native appearance notifications refresh the app. Cinnamon, GNOME and MATE preferences are read from the host desktop, including when the app is packaged. Reopen the app after changing GTK desktop font, text scaling or accent/theme preferences. Changing Light/Dark inside the app preserves its resolved font.

Display scaling belongs to Qt. X11 text scaling carried by Xft DPI is not multiplied again. Wayland text scaling is resolved separately from display scaling. Layouts measure the resulting text; they do not shrink it to fit. On a monitor too small for the useful minimum, configuration can reflow and work areas scroll while actions and status remain available.

If desktop integration is unavailable, the captured native palette is retained, then a readable Studio blue is the final fallback. The app records appearance sources and fallback reasons in `appearance.json` in its local application-data folder. That diagnostic contains appearance settings, not document paths or content. A fallback is not proof of a verified system match.
