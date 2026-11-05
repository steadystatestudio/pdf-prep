"""The host appearance reader is a bounded native process, not a Python script."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/native_appearance_probe.c"


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def compiler():
    if sys.platform != "linux" or not shutil.which("cc"):
        pytest.skip("Native probe execution requires a Linux C compiler")
    return shutil.which("cc")


def test_missing_compiler_explains_build_failure(monkeypatch):
    builder = load_script("build_appearance_probe")
    monkeypatch.setattr(builder.sys, "platform", "linux")
    monkeypatch.setattr(builder.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="native C compiler"):
        builder.build()


def test_probe_exits_with_structured_failure_without_display_or_python(compiler, tmp_path):
    executable = tmp_path / "appearance-probe"
    subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", str(SOURCE),
                    "-ldl", "-o", str(executable)], check=True)
    environment = dict(os.environ, PATH="", GDK_BACKEND="x11")
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "PYTHONHOME", "PYTHONPATH", "GI_TYPELIB_PATH"):
        environment.pop(key, None)
    result = subprocess.run([str(executable)], env=environment, capture_output=True, text=True, timeout=2)
    assert result.returncode == 0
    record = json.loads(result.stdout)
    assert record["schema"] == 1
    assert record["font"] is None and record["accent"] is None
    assert record["color_scheme"] is None
    assert any(code in record["errors"] for code in (
        "gtk-library-unavailable", "desktop-display-unavailable", "gtk-api-unavailable"))
    assert len(result.stdout.encode()) < 16384


def test_native_output_escapes_desktop_strings(compiler, tmp_path):
    # Exercise the actual C serializer independently of installed desktop themes.
    harness = tmp_path / "serializer.c"
    harness.write_text(
        '#define main appearance_probe_main\n'
        f'#include "{SOURCE.as_posix()}"\n'
        '#undef main\n'
        'int main(void) { Font font = {"Quoted\\\"Font\\\\Name\\n", 12.5, 400, 0, 0};\n'
        'output(&font, 144, 2, 147456, "Theme\\tName", "dark", "#a94dc1"); return 0; }\n',
        encoding="utf-8",
    )
    executable = tmp_path / "serializer"
    subprocess.run([compiler, "-std=c11", str(harness), "-ldl", "-o", str(executable)], check=True)
    record = json.loads(subprocess.check_output([str(executable)], text=True))
    assert record["font"]["family"] == 'Quoted"Font\\Name\n'
    assert record["font"]["point_size"] == 12.5
    assert record["theme"] == "Theme\tName"
    assert record["font_dpi"] == 144 and record["widget_scale"] == 2


@pytest.mark.parametrize("library,arguments,failure", [
    ("libgtk-3.so.0", [], "gtk-api-unavailable"),
    ("libX11.so.6", ["--workarea"], "x11-api-unavailable"),
])
def test_host_library_stdout_cannot_corrupt_json_protocol(compiler, tmp_path, library, arguments, failure):
    """Actual dlopen constructors exercise stdio and raw descriptor writes."""
    noisy = tmp_path / "noisy-host-library.c"
    noisy.write_text(
        '#include <stdio.h>\n#include <unistd.h>\n'
        '__attribute__((constructor)) static void host_warning(void) {\n'
        'fputs("host-library-stdio-warning\\n", stdout); fflush(stdout);\n'
        'const char warning[] = "host-library-descriptor-warning\\n";\n'
        'if (write(STDOUT_FILENO, warning, sizeof(warning)-1) < 0) _exit(3); }\n',
        encoding="utf-8",
    )
    subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-shared", "-fPIC",
                    str(noisy), "-o", str(tmp_path / library)], check=True)
    executable = tmp_path / "appearance-probe"
    subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", str(SOURCE),
                    "-ldl", "-o", str(executable)], check=True)
    environment = dict(os.environ, LD_LIBRARY_PATH=str(tmp_path), PATH="")
    environment.pop("LD_PRELOAD", None)
    result = subprocess.run([str(executable), *arguments], env=environment, capture_output=True,
                            text=True, timeout=2)
    assert result.returncode == 0
    record = json.loads(result.stdout)
    assert record["schema"] == 1 and failure in record["errors"]
    assert result.stdout.count("\n") == 1 and len(result.stdout.encode()) < 16384
    assert "host-library-stdio-warning" in result.stderr
    assert "host-library-descriptor-warning" in result.stderr


def test_protocol_descriptor_failure_exits_without_host_library_io(compiler, tmp_path):
    harness = tmp_path / "descriptor-failure.c"
    harness.write_text(
        '#define dup appearance_test_dup\n#define main appearance_probe_main\n'
        f'#include "{SOURCE.as_posix()}"\n'
        '#undef main\n#undef dup\n'
        'int appearance_test_dup(int descriptor) { (void)descriptor; return -1; }\n'
        'int main(void) { char *arguments[] = {"appearance-probe", NULL};\n'
        'return appearance_probe_main(1, arguments); }\n',
        encoding="utf-8",
    )
    executable = tmp_path / "descriptor-failure"
    subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", str(harness),
                    "-ldl", "-o", str(executable)], check=True)
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=2)
    assert result.returncode != 0
    assert result.stdout == ""


@pytest.mark.parametrize("background,foreground,scheme", [
    ("rgba(46,46,51,0.999)", "rgba(255,255,255,0.87)", "dark"),
    ("rgba(244,244,244,0.999)", "rgba(0,0,0,0.87)", "light"),
])
def test_actual_window_css_controls_scheme_without_theme_name_inference(
    compiler, tmp_path, background, foreground, scheme,
):
    """Window CSS must beat stale/missing named theme colors on GTK themes."""
    xvfb = shutil.which("xvfb-run")
    if not xvfb:
        pytest.skip("GTK CSS regression requires Xvfb")
    harness = tmp_path / "window-css.c"
    harness.write_text(
        '#define main appearance_probe_main\n'
        f'#include "{SOURCE.as_posix()}"\n'
        '#undef main\n'
        'int main(void) {\n'
        'void *gtk = dlopen("libgtk-3.so.0", RTLD_NOW | RTLD_LOCAL); if (!gtk) return 77;\n'
        'int (*init)(int *, char ***) = dlsym(gtk, "gtk_init_check");\n'
        'void *(*new_provider)(void) = dlsym(gtk, "gtk_css_provider_new");\n'
        'int (*load_css)(void *, const char *, long, void *) = dlsym(gtk, "gtk_css_provider_load_from_data");\n'
        'void *(*screen)(void) = dlsym(gtk, "gdk_screen_get_default");\n'
        'void (*add_provider)(void *, void *, unsigned int) = '
        'dlsym(gtk, "gtk_style_context_add_provider_for_screen");\n'
        'if (!init || !new_provider || !load_css || !screen || !add_provider) return 77;\n'
        'int argc = 0; char **argv = NULL; if (!init(&argc, &argv)) return 77;\n'
        'void *provider = new_provider();\n'
        f'const char *css = "window {{ background-color: {background}; color: {foreground}; }}";\n'
        'if (!load_css(provider, css, -1, NULL)) return 2;\n'
        'add_provider(screen(), provider, 900); char *probe_argv[] = {"appearance-probe", NULL};\n'
        'return appearance_probe_main(1, probe_argv); }\n',
        encoding="utf-8",
    )
    executable = tmp_path / "window-css"
    subprocess.run([compiler, "-std=c11", str(harness), "-ldl", "-o", str(executable)], check=True)
    # This harness initializes GTK before entering probe main; its CSS check
    # needs no accessibility connection or pre-main host diagnostic chatter.
    environment = dict(os.environ, GDK_BACKEND="x11", NO_AT_BRIDGE="1")
    environment.pop("GTK_THEME", None)
    result = subprocess.run([xvfb, "-a", str(executable)], env=environment, capture_output=True,
                            text=True, timeout=10)
    if result.returncode == 77:
        pytest.skip("Host GTK 3 runtime is not installed")
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record["color_scheme"] == scheme, record
    assert "desktop-color-scheme-unavailable" not in record["errors"]


@pytest.mark.parametrize("background,foreground,expected", [
    (".18,.18,.2,.999", "1,1,1,.87", "dark"),
    (".95,.95,.95,.999", "0,0,0,.87", "light"),
    (".18,.18,.2,0", "1,1,1,.87", None),
    (".18,.18,.2,1", "1,1,1,0", None),
    (".18,.18,.2,1", "NAN,1,1,.87", None),
])
def test_native_scheme_composites_valid_translucent_text(compiler, tmp_path, background, foreground, expected):
    harness = tmp_path / "composite.c"
    harness.write_text(
        '#define main appearance_probe_main\n'
        f'#include "{SOURCE.as_posix()}"\n'
        '#undef main\n'
        f'int main(void) {{ Rgba bg = {{{background}}}, fg = {{{foreground}}};\n'
        'output(NULL, 96, 1, 98304, NULL, scheme_from_colors(&bg, &fg), NULL); return 0; }\n',
        encoding="utf-8",
    )
    executable = tmp_path / "composite"
    subprocess.run([compiler, "-std=c11", str(harness), "-ldl", "-o", str(executable)], check=True)
    result = json.loads(subprocess.check_output([str(executable)], text=True))
    assert result["color_scheme"] == expected


def test_dependency_metadata_names_actual_build_distribution(monkeypatch):
    dependencies = load_script("linux_dependencies")
    monkeypatch.setattr(dependencies.platform, "freedesktop_os_release",
                        lambda: {"NAME": "Test Linux", "VERSION_ID": "42"})
    assert dependencies.build_distribution() == "Test Linux 42"
