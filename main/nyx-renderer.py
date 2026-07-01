#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Fernando Magalhães <fm4lloc@gmail.com>
#
# NyxHud — renderer Wayland-only.
#
# Lê *.render escritos por coletores externos e desenha o texto
# com Cairo/PangoCairo sobre uma superfície GtkLayerShell. Todo
# objeto caro (Cairo Context, Pango.Layout, FontDescription) é
# alocado uma única vez e reutilizado — nenhum é recriado a cada
# atualização ou a cada frame. Sem polling: conteúdo é acionado
# por Gio.FileMonitor; TTL é um único timer reagendado para o
# próximo instante de expiração real.

import os
import sys
import html
import time
import atexit
import signal

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("GtkLayerShell", "0.1")
    gi.require_version("PangoCairo", "1.0")

    from gi.repository import Gtk, GLib, Gio, GtkLayerShell, Pango, PangoCairo
    import cairo

except (ImportError, ValueError) as exc:
    sys.stderr.write(f"[nyxhud] dependência ausente: {exc}\n")
    sys.exit(1)

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

MAX_RENDER_SIZE = 32768
MAX_LINES = 256
MAX_LINE_LENGTH = 256
RENDER_TTL = 15
WINDOW_MARGIN = 40
PADDING = 24
FONT = "Iosevka Term 12"
TEXT_COLOR = "#E0E0E0"
HEADER_COLOR = "#1793d1"

XDG_RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
RUNTIME_DIR = os.path.join(XDG_RUNTIME_DIR, "nyxhud")
RENDER_DIR = os.path.join(RUNTIME_DIR, "render")
LOCK_DIR = os.path.join(RUNTIME_DIR, "renderer.lock")

# --------------------------------------------------------------------------
# Markup
# --------------------------------------------------------------------------

def _is_header(line):
    s = line.strip()
    return (
        bool(s)
        and len(s) <= 32
        and s.upper() == s
        and any(c.isalpha() for c in s)
        and not any(c.isdigit() for c in s)
        and ":" not in s
    )


def build_markup(text):
    lines = [
        f"<span foreground='{HEADER_COLOR}' weight='bold'>{line}</span>" if _is_header(line) else line
        for line in html.escape(text).splitlines()
    ]
    return f"<span foreground='{TEXT_COLOR}' weight='bold'>{chr(10).join(lines)}</span>"


# --------------------------------------------------------------------------
# Cache — leitura isolada por coletor, com mtime + TTL
# --------------------------------------------------------------------------

class RenderCache:
    """Cada arquivo .render é lido de forma independente: sumiu,
    expirou ou deu erro -> some do resultado, os demais seguem."""

    def __init__(self, directory):
        self.directory = directory
        self._entries = {}  # path -> (mtime, texto)

    def collect(self):
        try:
            names = sorted(f for f in os.listdir(self.directory) if f.endswith(".render"))
        except OSError:
            names = []

        seen = set()
        blocks = []
        for name in names:
            path = os.path.join(self.directory, name)
            seen.add(path)
            text = self._read(path)
            if text:
                blocks.append(text)

        for stale in set(self._entries) - seen:
            del self._entries[stale]

        return "\n\n".join(blocks)

    def next_expiry(self):
        deadlines = [mtime + RENDER_TTL for mtime, _ in self._entries.values()]
        return min(deadlines) if deadlines else None

    def _read(self, path):
        try:
            mtime = int(os.stat(path).st_mtime)
        except OSError:
            self._entries.pop(path, None)
            return None

        if time.time() - mtime > RENDER_TTL:
            self._entries.pop(path, None)
            return None

        cached = self._entries.get(path)
        if cached and cached[0] == mtime:
            return cached[1]

        text = self._load(path)
        self._entries[path] = (mtime, text)
        return text

    @staticmethod
    def _load(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                data = f.read(MAX_RENDER_SIZE)
        except OSError:
            return ""
        lines = [ln[:MAX_LINE_LENGTH] for ln in data.splitlines()[:MAX_LINES]]
        return "\n".join(lines).strip()


# --------------------------------------------------------------------------
# HudArea — widget, geometria, Pango/Cairo persistentes, FileMonitor, TTL
# --------------------------------------------------------------------------

class HudArea(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()

        self._cache = RenderCache(RENDER_DIR)
        self._width = 1
        self._height = 1
        self._ttl_timer = None

        # Alocados uma única vez, para a vida toda do processo.
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        self._measure_cr = cairo.Context(surface)
        self._layout = PangoCairo.create_layout(self._measure_cr)
        self._layout.set_font_description(Pango.FontDescription(FONT))

        gfile = Gio.File.new_for_path(RENDER_DIR)
        self._monitor = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self._monitor.connect("changed", lambda *_: self.refresh())

        self.refresh()

    def refresh(self):
        try:
            text = self._cache.collect()
        except Exception as exc:
            sys.stderr.write(f"[nyxhud] erro ao coletar: {exc}\n")
            text = ""

        self._layout.set_markup(build_markup(text), -1)
        PangoCairo.update_layout(self._measure_cr, self._layout)

        w, h = self._layout.get_pixel_size()
        self._width, self._height = w + 2 * PADDING, h + 2 * PADDING

        # Único gatilho existente no GTK3 para invalidar a requisição
        # de tamanho já negociada e forçar realoc com a nova geometria.
        self.queue_resize()
        self.queue_draw()

        self._schedule_ttl()

    def _schedule_ttl(self):
        if self._ttl_timer is not None:
            GLib.source_remove(self._ttl_timer)
            self._ttl_timer = None

        deadline = self._cache.next_expiry()
        if deadline is None:
            return

        delay_ms = max(0, int((deadline - time.time()) * 1000)) + 50
        self._ttl_timer = GLib.timeout_add(delay_ms, self._on_ttl)

    def _on_ttl(self):
        self._ttl_timer = None
        self.refresh()
        return False

    def do_get_preferred_width(self):
        return self._width, self._width

    def do_get_preferred_height(self):
        return self._height, self._height

    def do_draw(self, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        cr.translate(PADDING, PADDING)

        # Mesmo Pango.Layout de sempre, só resincronizado com o
        # Cairo Context deste frame — nada é recriado aqui.
        PangoCairo.update_layout(cr, self._layout)
        PangoCairo.show_layout(cr, self._layout)
        return False


# --------------------------------------------------------------------------
# Janela layer-shell
# --------------------------------------------------------------------------

def build_window():
    if not GtkLayerShell.is_supported():
        sys.stderr.write("[nyxhud] requer Wayland com wlr-layer-shell\n")
        sys.exit(1)

    window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
    window.set_decorated(False)
    window.set_resizable(False)
    window.set_app_paintable(True)

    visual = window.get_screen().get_rgba_visual()
    if visual:
        window.set_visual(visual)

    GtkLayerShell.init_for_window(window)
    GtkLayerShell.set_layer(window, GtkLayerShell.Layer.BOTTOM)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.LEFT, True)
    GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, True)
    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.LEFT, WINDOW_MARGIN)
    GtkLayerShell.set_margin(window, GtkLayerShell.Edge.BOTTOM, WINDOW_MARGIN)
    GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)
    GtkLayerShell.set_exclusive_zone(window, -1)
    return window


# --------------------------------------------------------------------------
# Instância única + encerramento limpo
# --------------------------------------------------------------------------

def _release_lock():
    try:
        os.rmdir(LOCK_DIR)
    except OSError:
        pass


def acquire_lock():
    try:
        os.makedirs(RENDER_DIR, exist_ok=True)
        os.mkdir(LOCK_DIR)
    except FileExistsError:
        sys.stderr.write("[nyxhud] renderer already running\n")
        sys.exit(1)
    except OSError as exc:
        sys.stderr.write(f"[nyxhud] falha ao preparar runtime dir: {exc}\n")
        sys.exit(1)
    atexit.register(_release_lock)


def main():
    acquire_lock()

    window = build_window()
    window.add(HudArea())
    window.connect("destroy", Gtk.main_quit)
    window.show_all()

    signal.signal(signal.SIGINT, lambda *_: Gtk.main_quit())
    signal.signal(signal.SIGTERM, lambda *_: Gtk.main_quit())

    Gtk.main()


if __name__ == "__main__":
    main()