#define _POSIX_C_SOURCE 200809L
/* SPDX-License-Identifier: MIT
 * Read-only desktop appearance probe. This is deliberately a separate native
 * process: it never loads host GTK into the app's bundled Qt/Python process.
 * Build dependencies: a C compiler and libc/libdl. Runtime GTK is optional.
 */
#include <dlfcn.h>
#include <locale.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Host libraries may write to stdout, including from dlopen constructors.
 * Keep machine JSON on its original descriptor and send library chatter to
 * stderr. Tests that exercise serializers directly need no protocol setup. */
static FILE *json_output;
static FILE *json_stream(void) { return json_output ? json_output : stdout; }

typedef struct { double red, green, blue, alpha; } Rgba;
typedef struct {
    const char *family;
    double size;
    int weight;
    int italic;
    int absolute;
} Font;

static const char *errors[12];
static unsigned int error_count;

static void error(const char *code) {
    if (error_count < sizeof(errors) / sizeof(errors[0])) errors[error_count++] = code;
}

static void json_string(const char *value) {
    if (!value) { fputs("null", json_stream()); return; }
    fputc('"', json_stream());
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p) {
        switch (*p) {
        case '"': fputs("\\\"", json_stream()); break;
        case '\\': fputs("\\\\", json_stream()); break;
        case '\b': fputs("\\b", json_stream()); break;
        case '\f': fputs("\\f", json_stream()); break;
        case '\n': fputs("\\n", json_stream()); break;
        case '\r': fputs("\\r", json_stream()); break;
        case '\t': fputs("\\t", json_stream()); break;
        default:
            if (*p < 0x20) fprintf(json_stream(), "\\u%04x", (unsigned int)*p);
            else fputc(*p, json_stream());
        }
    }
    fputc('"', json_stream());
}

static const char *bounded_string(const char *value, const char *failure) {
    if (!value || !*value) return NULL;
    if (strlen(value) > 512) { error(failure); return NULL; }
    return value;
}

static int valid_color(const Rgba *value) {
    return isfinite(value->red) && isfinite(value->green) && isfinite(value->blue)
        && isfinite(value->alpha) && value->red >= 0 && value->red <= 1
        && value->green >= 0 && value->green <= 1 && value->blue >= 0
        && value->blue <= 1 && value->alpha >= 0 && value->alpha <= 1;
}

static int opaque(const Rgba *value) {
    return valid_color(value) && value->alpha >= 0.999;
}

static double brightness(const Rgba *value) {
    /* Sufficient for classifying a theme's paired background/foreground;
       actual UI text contrast remains the application's responsibility. */
    return .2126 * value->red + .7152 * value->green + .0722 * value->blue;
}

static const char *scheme_from_colors(const Rgba *background, const Rgba *foreground) {
    if (!valid_color(background) || !valid_color(foreground)
            || background->alpha < .99 || foreground->alpha <= 0) return NULL;
    /* Theme text may intentionally use translucent white or black (Mint-Y
       uses 87%). Classify its effective color on the known window surface;
       requiring an opaque foreground incorrectly discards such valid themes. */
    double alpha = foreground->alpha;
    Rgba effective = {
        foreground->red * alpha + background->red * (1 - alpha),
        foreground->green * alpha + background->green * (1 - alpha),
        foreground->blue * alpha + background->blue * (1 - alpha),
        1,
    };
    double difference = brightness(&effective) - brightness(background);
    if (fabs(difference) <= .1) return NULL;
    return difference > 0 ? "dark" : "light";
}

static void output(const Font *font, double dpi, int scale, int xft_dpi,
                   const char *theme, const char *scheme, const char *accent) {
    fputs("{\"schema\":1,\"font\":", json_stream());
    if (!font || !font->family) fputs("null", json_stream());
    else {
        fputs("{\"family\":", json_stream()); json_string(font->family);
        fprintf(json_stream(), ",\"point_size\":%.8g,\"weight\":%d,\"italic\":%s,\"absolute_size\":%s}",
               font->size, font->weight, font->italic ? "true" : "false",
               font->absolute ? "true" : "false");
    }
    fputs(",\"font_dpi\":", json_stream());
    if (isfinite(dpi) && dpi > 0) fprintf(json_stream(), "%.8g", dpi); else fputs("null", json_stream());
    fprintf(json_stream(), ",\"widget_scale\":%d,\"xft_dpi\":%d,\"theme\":", scale, xft_dpi);
    json_string(theme); fputs(",\"color_scheme\":", json_stream()); json_string(scheme);
    fputs(",\"accent\":", json_stream()); json_string(accent);
    fputs(",\"errors\":[", json_stream());
    for (unsigned int i = 0; i < error_count; ++i) {
        if (i) fputc(',', json_stream());
        json_string(errors[i]);
    }
    fputs("]}\n", json_stream());
}

#define DECLARE(result, name, arguments) static result (*name) arguments
#define LOAD(name) do { \
    void *address = dlsym(gtk, #name); \
    memcpy(&name, &address, sizeof(name)); \
    if (!name) { error("gtk-api-unavailable"); output(NULL, 0, 1, -1, NULL, NULL, NULL); return 0; } \
} while (0)

DECLARE(int, gtk_init_check, (int *, char ***));
DECLARE(void *, gtk_settings_get_default, (void));
DECLARE(void *, gtk_window_new, (int));
DECLARE(void *, gtk_button_new, (void));
DECLARE(void, gtk_container_add, (void *, void *));
DECLARE(void, gtk_widget_realize, (void *));
DECLARE(void, gtk_widget_destroy, (void *));
DECLARE(void *, gtk_widget_get_style_context, (void *));
DECLARE(void *, gtk_widget_get_pango_context, (void *));
DECLARE(int, gtk_widget_get_scale_factor, (void *));
DECLARE(int, gtk_style_context_lookup_color, (void *, const char *, Rgba *));
DECLARE(void, gtk_style_context_get, (void *, unsigned int, ...));
DECLARE(void, gtk_style_context_get_background_color, (void *, unsigned int, Rgba *));
DECLARE(void, gtk_style_context_get_color, (void *, unsigned int, Rgba *));
DECLARE(void, g_object_get, (void *, const char *, ...));
DECLARE(void, g_free, (void *));
DECLARE(void *, pango_font_description_from_string, (const char *));
DECLARE(void, pango_font_description_free, (void *));
DECLARE(const char *, pango_font_description_get_family, (const void *));
DECLARE(int, pango_font_description_get_size, (const void *));
DECLARE(int, pango_font_description_get_size_is_absolute, (const void *));
DECLARE(int, pango_font_description_get_weight, (const void *));
DECLARE(int, pango_font_description_get_style, (const void *));
DECLARE(double, pango_cairo_context_get_resolution, (void *));

static int lookup(void *context, const char *const *names, Rgba *value) {
    for (unsigned int i = 0; names[i]; ++i) {
        if (gtk_style_context_lookup_color(context, names[i], value) && opaque(value)) return 1;
    }
    return 0;
}

static int appearance_main(void) {
    setlocale(LC_NUMERIC, "C");
    void *gtk = dlopen("libgtk-3.so.0", RTLD_NOW | RTLD_LOCAL);
    if (!gtk) { error("gtk-library-unavailable"); output(NULL, 0, 1, -1, NULL, NULL, NULL); return 0; }
    LOAD(gtk_init_check); LOAD(gtk_settings_get_default); LOAD(gtk_window_new);
    LOAD(gtk_button_new); LOAD(gtk_container_add); LOAD(gtk_widget_realize);
    LOAD(gtk_widget_destroy); LOAD(gtk_widget_get_style_context);
    LOAD(gtk_widget_get_pango_context); LOAD(gtk_widget_get_scale_factor);
    LOAD(gtk_style_context_lookup_color); LOAD(gtk_style_context_get);
    LOAD(gtk_style_context_get_background_color); LOAD(gtk_style_context_get_color);
    LOAD(g_object_get); LOAD(g_free); LOAD(pango_font_description_from_string);
    LOAD(pango_font_description_free); LOAD(pango_font_description_get_family);
    LOAD(pango_font_description_get_size); LOAD(pango_font_description_get_size_is_absolute);
    LOAD(pango_font_description_get_weight); LOAD(pango_font_description_get_style);
    LOAD(pango_cairo_context_get_resolution);
    int argc = 0;
    char **argv = NULL;
    if (!gtk_init_check(&argc, &argv)) {
        error("desktop-display-unavailable"); output(NULL, 0, 1, -1, NULL, NULL, NULL); return 0;
    }
    void *settings = gtk_settings_get_default();
    if (!settings) { error("gtk-settings-unavailable"); output(NULL, 0, 1, -1, NULL, NULL, NULL); return 0; }
    char *font_name = NULL, *theme_name = NULL;
    int xft_dpi = -1;
    g_object_get(settings, "gtk-font-name", &font_name, "gtk-theme-name", &theme_name,
                 "gtk-xft-dpi", &xft_dpi, NULL);
    const char *theme = bounded_string(theme_name, "theme-name-too-long");
    void *window = gtk_window_new(0);
    void *widget = gtk_button_new();
    if (!window || !widget) { error("gtk-widget-unavailable"); output(NULL, 0, 1, xft_dpi, theme, NULL, NULL); return 0; }
    gtk_container_add(window, widget);
    /* Realizing an unmapped window initializes monitor scale/Pango resolution;
       the probe never shows or raises a window on the user's desktop. */
    gtk_widget_realize(window);
    gtk_widget_realize(widget);
    void *context = gtk_widget_get_style_context(widget);
    void *pango_context = gtk_widget_get_pango_context(widget);
    void *description = NULL;
    gtk_style_context_get(context, 0, "font", &description, NULL);
    if (!description && font_name && strlen(font_name) <= 512)
        description = pango_font_description_from_string(font_name);
    Font font = {0};
    if (description) {
        font.family = bounded_string(pango_font_description_get_family(description), "font-family-too-long");
        font.size = pango_font_description_get_size(description) / 1024.0;
        font.weight = pango_font_description_get_weight(description);
        font.italic = pango_font_description_get_style(description) != 0;
        font.absolute = pango_font_description_get_size_is_absolute(description) != 0;
        if (!isfinite(font.size) || font.size <= 0 || font.size > 512) font.family = NULL;
    }
    if (!font.family) error("desktop-font-unavailable");
    double dpi = pango_context ? pango_cairo_context_get_resolution(pango_context) : -1;
    if (!isfinite(dpi) || dpi <= 0) {
        if (xft_dpi > 0) dpi = xft_dpi / 1024.0;
        else { dpi = 0; error("desktop-font-dpi-unavailable"); }
    }
    int scale = gtk_widget_get_scale_factor(widget);
    if (scale < 1 || scale > 16) { scale = 1; error("desktop-widget-scale-invalid"); }
    const char *accent_names[] = {"accent_bg_color", "theme_selected_bg_color", "selected_bg_color", NULL};
    const char *background_names[] = {"window_bg_color", "theme_bg_color", "bg_color", NULL};
    const char *foreground_names[] = {"window_fg_color", "theme_fg_color", "fg_color", NULL};
    Rgba accent_value = {0}, background = {0}, foreground = {0};
    char accent[8];
    const char *accent_result = NULL;
    if (lookup(context, accent_names, &accent_value)) {
        snprintf(accent, sizeof(accent), "#%02x%02x%02x", (int)(accent_value.red * 255 + .5),
                 (int)(accent_value.green * 255 + .5), (int)(accent_value.blue * 255 + .5));
        accent_result = accent;
    } else error("desktop-accent-unavailable");
    const char *scheme = NULL;
    /* Query the actual GtkWindow CSS first. Some themes (including Mint-Y)
       define window colors without exporting the legacy named color aliases;
       a button's colors can also differ from the surrounding window. */
    void *window_context = gtk_widget_get_style_context(window);
    gtk_style_context_get_background_color(window_context, 0, &background);
    gtk_style_context_get_color(window_context, 0, &foreground);
    if (!valid_color(&background) || background.alpha < .99)
        lookup(window_context, background_names, &background);
    if (!valid_color(&foreground) || foreground.alpha <= 0)
        lookup(window_context, foreground_names, &foreground);
    scheme = scheme_from_colors(&background, &foreground);
    if (!scheme) error("desktop-color-scheme-unavailable");
    output(font.family ? &font : NULL, dpi, scale, xft_dpi, theme, scheme, accent_result);
    if (description) pango_font_description_free(description);
    g_free(font_name); g_free(theme_name);
    gtk_widget_destroy(window);
    return 0;
}

/* SPDX-License-Identifier: MIT
 * Read-only optional desktop work-area reader. No X11 headers or link dependency.
 */
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

typedef unsigned long Atom;
typedef unsigned long Window;
typedef struct _XDisplay Display;
#define API(ret, name, args) static ret (*name) args
API(Display *, XOpenDisplay, (const char *));
API(int, XCloseDisplay, (Display *));
API(Window, XDefaultRootWindow, (Display *));
API(Atom, XInternAtom, (Display *, const char *, int));
API(int, XGetWindowProperty, (Display *, Window, Atom, long, long, int, Atom,
                            Atom *, int *, unsigned long *, unsigned long *, unsigned char **));
API(int, XGetGeometry, (Display *, Window, Window *, int *, int *, unsigned int *, unsigned int *, unsigned int *, unsigned int *));
API(int, XFree, (void *));
typedef struct { Atom name; int primary, automatic, noutput, x, y, width, height, mwidth, mheight; unsigned long *outputs; } Monitor;
API(Monitor *, XRRGetMonitors, (Display *, Window, int, int *));
API(void, XRRFreeMonitors, (Monitor *));

static void fail(const char *code) {
    fprintf(json_stream(), "{\"schema\":1,\"workarea\":null,\"errors\":[\"%s\"]}\n", code);
}

static int property(Display *display, Window root, Atom atom, long limit,
                    unsigned long **value, unsigned long *count) {
    Atom type = 0;
    int format = 0;
    unsigned long remaining = 0;
    unsigned char *data = NULL;
    if (!atom) return 0;
    int status = XGetWindowProperty(display, root, atom, 0, limit, 0, 6,
            &type, &format, count, &remaining, &data);
    if (!status && !type) { if (data) XFree(data); return 0; }
    if (status || type != 6 || format != 32
            || remaining || !*count || *count > (unsigned long)limit || !data) {
        if (data) XFree(data);
        return -1;
    }
    *value = (unsigned long *)data;
    return 1;
}

static int workarea_main(void) {
    void *library = dlopen("libX11.so.6", RTLD_NOW | RTLD_LOCAL);
    if (!library) { fail("x11-library-unavailable"); return 0; }
#define LOAD_X11(name) do { void *symbol = dlsym(library, #name); memcpy(&name, &symbol, sizeof(name)); \
    if (!name) { fail("x11-api-unavailable"); dlclose(library); return 0; } } while (0)
    LOAD_X11(XOpenDisplay); LOAD_X11(XCloseDisplay); LOAD_X11(XDefaultRootWindow); LOAD_X11(XInternAtom);
    LOAD_X11(XGetWindowProperty); LOAD_X11(XGetGeometry); LOAD_X11(XFree);
    Display *display = XOpenDisplay(NULL);
    if (!display) { fail("x11-display-unavailable"); dlclose(library); return 0; }
    Window root = XDefaultRootWindow(display), geometry_root;
    int x, y;
    unsigned int width, height, border, depth;
    unsigned long *areas = NULL, *desktops = NULL, count = 0, desktops_count = 0, selected = 0;
    void *randr = NULL;
    const char *failure = NULL, *selection = "current-desktop";
    if (!XGetGeometry(display, root, &geometry_root, &x, &y, &width, &height, &border, &depth)
            || width < 1 || height < 1 || width > 262144 || height > 262144) {
        failure = "x11-root-geometry-invalid";
        goto done;
    }
    if (property(display, root, XInternAtom(display, "_NET_WORKAREA", 1), 256, &areas, &count) != 1
            || count % 4) {
        failure = "ewmh-workarea-unavailable-or-invalid";
        goto done;
    }
    for (unsigned long i = 0; i < count; i += 4) {
        if (!areas[i+2] || !areas[i+3] || areas[i] >= width || areas[i+1] >= height
                || areas[i+2] > width-areas[i] || areas[i+3] > height-areas[i+1]) {
            failure = "ewmh-workarea-outside-root";
            goto done;
        }
    }
    int desktop_status = property(display, root, XInternAtom(display, "_NET_CURRENT_DESKTOP", 1), 1,
                                  &desktops, &desktops_count);
    if (desktop_status < 0) { failure = "ewmh-current-desktop-invalid"; goto done; }
    if (desktop_status == 1 && desktops_count == 1) {
        selected = desktops[0];
        if (selected >= count / 4) { failure = "ewmh-current-desktop-invalid"; goto done; }
    } else {
        selection = "identical-workareas";
        for (unsigned long i = 4; i < count; i++) {
            if (areas[i] != areas[i % 4]) { failure = "ewmh-current-desktop-unavailable"; goto done; }
        }
    }
    {
        unsigned long *area = areas + selected * 4;
        fprintf(json_stream(), "{\"schema\":1,\"source\":\"x11-ewmh\",\"coordinate_space\":\"x11-root\","
               "\"root\":[0,0,%u,%u],\"workarea\":[%lu,%lu,%lu,%lu],"
               "\"desktop_selection\":\"%s\",\"monitors\":",
               width, height, area[0], area[1], area[2], area[3], selection);
        randr = dlopen("libXrandr.so.2", RTLD_NOW | RTLD_LOCAL);
        int number = 0, valid = 0;
        Monitor *monitors = NULL;
        if (randr) {
            void *symbol = dlsym(randr, "XRRGetMonitors"); memcpy(&XRRGetMonitors, &symbol, sizeof(XRRGetMonitors));
            symbol = dlsym(randr, "XRRFreeMonitors"); memcpy(&XRRFreeMonitors, &symbol, sizeof(XRRFreeMonitors));
            if (XRRGetMonitors && XRRFreeMonitors) {
                monitors = XRRGetMonitors(display, root, 1, &number);
                valid = monitors && number > 0 && number <= 64;
                for (int i = 0; valid && i < number; i++) {
                    Monitor m = monitors[i];
                    valid = m.x >= 0 && m.y >= 0 && m.width > 0 && m.height > 0
                        && (unsigned int)m.x < width && (unsigned int)m.y < height
                        && (unsigned int)m.width <= width-(unsigned int)m.x
                        && (unsigned int)m.height <= height-(unsigned int)m.y;
                }
            }
        }
        if (!valid) fputs("null", json_stream());
        else {
            fputc('[', json_stream());
            for (int i = 0; i < number; i++) {
                if (i) fputc(',', json_stream());
                fprintf(json_stream(), "[%d,%d,%d,%d]", monitors[i].x, monitors[i].y, monitors[i].width, monitors[i].height);
            }
            fputc(']', json_stream());
        }
        if (monitors && XRRFreeMonitors) XRRFreeMonitors(monitors);
        fputs(",\"errors\":[]}\n", json_stream());
    }
done:
    if (failure) fail(failure);
    if (areas) XFree(areas);
    if (desktops) XFree(desktops);
    XCloseDisplay(display);
    /* XRandR registers per-display Xlib cleanup hooks. Keep its code loaded
       until XCloseDisplay has finished invoking those hooks. */
    if (randr) dlclose(randr);
    dlclose(library);
    return 0;
}


int main(int argc, char **argv) {
    int original = dup(STDOUT_FILENO);
    if (original < 0) return 3;
    FILE *protocol = fdopen(original, "w");
    if (!protocol) { close(original); return 3; }
    if (fflush(stdout) == EOF || dup2(STDERR_FILENO, STDOUT_FILENO) < 0) {
        fclose(protocol);
        return 3;
    }
    json_output = protocol;
    int status;
    if (argc == 2 && !strcmp(argv[1], "--workarea")) status = workarea_main();
    else if (argc != 1) { fail("unsupported-probe-mode"); status = 2; }
    else status = appearance_main();
    if (fclose(protocol) == EOF) status = 3;
    json_output = NULL;
    /* Leave fd1 on stderr so late library/atexit messages cannot reach JSON. */
    return status;
}
