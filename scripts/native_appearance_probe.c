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
    if (!value) { fputs("null", stdout); return; }
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)value; *p; ++p) {
        switch (*p) {
        case '"': fputs("\\\"", stdout); break;
        case '\\': fputs("\\\\", stdout); break;
        case '\b': fputs("\\b", stdout); break;
        case '\f': fputs("\\f", stdout); break;
        case '\n': fputs("\\n", stdout); break;
        case '\r': fputs("\\r", stdout); break;
        case '\t': fputs("\\t", stdout); break;
        default:
            if (*p < 0x20) printf("\\u%04x", (unsigned int)*p);
            else putchar(*p);
        }
    }
    putchar('"');
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
    fputs("{\"schema\":1,\"font\":", stdout);
    if (!font || !font->family) fputs("null", stdout);
    else {
        fputs("{\"family\":", stdout); json_string(font->family);
        printf(",\"point_size\":%.8g,\"weight\":%d,\"italic\":%s,\"absolute_size\":%s}",
               font->size, font->weight, font->italic ? "true" : "false",
               font->absolute ? "true" : "false");
    }
    fputs(",\"font_dpi\":", stdout);
    if (isfinite(dpi) && dpi > 0) printf("%.8g", dpi); else fputs("null", stdout);
    printf(",\"widget_scale\":%d,\"xft_dpi\":%d,\"theme\":", scale, xft_dpi);
    json_string(theme); fputs(",\"color_scheme\":", stdout); json_string(scheme);
    fputs(",\"accent\":", stdout); json_string(accent);
    fputs(",\"errors\":[", stdout);
    for (unsigned int i = 0; i < error_count; ++i) {
        if (i) putchar(',');
        json_string(errors[i]);
    }
    fputs("]}\n", stdout);
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

int main(void) {
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
