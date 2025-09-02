#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# DNS Jumper — polished build
# Fixes:
# - Immediate display of test results (ListView refreshed after tests finish)
# - Header labels aligned with columns
# - Sort disabled until tests run and only sorts when there are valid latencies
# - Delete icon uses user-trash-symbolic and deletes immediately + persists
# - IP validation enforced (primary + secondary required)
# - Single pkexec invocation to reduce password prompts
#
import os
import json
import subprocess
import threading
import time
import sys
import shlex
import re
import shutil

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio, GObject, Gdk

Adw.init()

try:
    import dns.resolver
except Exception:
    dns = None

APP_ID = "com.example.dnsjumper"
CONFIG_DIR = os.path.expanduser("~/.config/dnsjumper-linux")
CONFIG_FILE = os.path.join(CONFIG_DIR, "servers.json")

def ensure_config_dir():
    if not os.path.isdir(CONFIG_DIR):
        os.makedirs(CONFIG_DIR, exist_ok=True)
ensure_config_dir()

# ----------------------------- Persistence -----------------------------
def load_profiles():
    if not os.path.exists(CONFIG_FILE):
        return []
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            out = []
            for p in data:
                name = (p.get("name") or "").strip()
                servers = p.get("servers") or []
                if isinstance(servers, str):
                    servers = [s.strip() for s in servers.split(",") if s.strip()]
                out.append({"name": name, "servers": servers})
            return out
    except Exception:
        return []

def save_profiles(profiles):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(profiles, f, indent=2, ensure_ascii=False)

# ----------------------------- Validation -----------------------------
_ipv4_re = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
_ipv6_re = re.compile(r"^[0-9A-Fa-f:]+$")  # permissive IPv6

def is_valid_ip(addr: str) -> bool:
    a = addr.strip()
    if not a:
        return False
    if _ipv4_re.match(a):
        parts = a.split(".")
        try:
            return all(0 <= int(p) <= 255 for p in parts)
        except ValueError:
            return False
    if ":" in a:
        return bool(_ipv6_re.match(a))
    return False

def sanitize_servers(servers):
    out = []
    for s in servers:
        s = s.strip()
        if not s:
            continue
        if not is_valid_ip(s):
            raise ValueError(f"Invalid IP address: {s}")
        out.append(s)
    if len(out) < 2:
        raise ValueError("Provide two valid servers (primary & secondary).")
    return out

# ----------------------------- Latency -----------------------------
def measure_dns_latency(servers, timeout=2.0):
    if dns is None or not servers:
        return None
    latencies = []
    for srv in servers:
        try:
            resolver = dns.resolver.Resolver(configure=False)
            resolver.timeout = timeout
            resolver.lifetime = timeout
            resolver.nameservers = [srv]
            t0 = time.monotonic()
            resolver.resolve("example.com", "A")
            latencies.append((time.monotonic() - t0) * 1000.0)
        except Exception:
            pass
    return (sum(latencies) / len(latencies)) if latencies else None

# ----------------------------- Apply DNS (single pkexec) -----------------------------
def get_active_connection_and_device():
    try:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "UUID,TYPE,DEVICE", "connection", "show", "--active"],
            text=True,
        )
        lines = [l for l in out.splitlines() if l.strip()]
        for ln in lines:
            parts = ln.split(":")
            if len(parts) >= 3:
                uuid, typ, dev = parts[0], parts[1], parts[2]
                if dev and typ in ("wifi", "ethernet"):
                    return uuid, dev
        if lines:
            parts = lines[0].split(":")
            uuid = parts[0] if len(parts) >= 1 else None
            dev = parts[2] if len(parts) >= 3 else None
            return uuid, dev
    except Exception:
        pass
    return None, None

def apply_dns_with_one_pkexec(servers):
    servers = sanitize_servers(servers)
    uuid, device = get_active_connection_and_device()
    if not uuid:
        raise RuntimeError("No active NetworkManager connection found")

    has_ipv6 = any(":" in s for s in servers)
    ipv4_list = [s for s in servers if ":" not in s]
    ipv6_list = [s for s in servers if ":" in s]

    cmd_parts = []
    if ipv4_list:
        cmd_parts.append("nmcli connection modify " + shlex.quote(uuid) + " ipv4.ignore-auto-dns yes ipv4.dns " + shlex.quote(" ".join(ipv4_list)))
    else:
        cmd_parts.append("nmcli connection modify " + shlex.quote(uuid) + " ipv4.dns ''")

    if ipv6_list:
        cmd_parts.append("nmcli connection modify " + shlex.quote(uuid) + " ipv6.ignore-auto-dns yes ipv6.dns " + shlex.quote(" ".join(ipv6_list)))
    else:
        cmd_parts.append("nmcli connection modify " + shlex.quote(uuid) + " ipv6.dns ''")

    cmd_parts.append("nmcli connection up " + shlex.quote(uuid))

    if device:
        resolvectl_cmd = "resolvectl dns " + shlex.quote(device)
        for s in servers:
            resolvectl_cmd += " " + shlex.quote(s)
        cmd_parts.append(resolvectl_cmd)
        cmd_parts.append("resolvectl flush-caches")

    shell_cmd = " && ".join(cmd_parts)

    try:
        subprocess.check_call(["pkexec", "bash", "-c", shell_cmd])
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Privileged command failed: {e}")

    try:
        out4 = subprocess.check_output(["nmcli", "-g", "ipv4.dns", "connection", "show", uuid], text=True).strip()
        out6 = subprocess.check_output(["nmcli", "-g", "ipv6.dns", "connection", "show", uuid], text=True).strip()
        if not (out4 or out6):
            raise RuntimeError("DNS not visible via nmcli after applying.")
    except subprocess.CalledProcessError:
        raise RuntimeError("Failed to verify nmcli connection settings after apply")

# ----------------------------- Sound -----------------------------
def play_success_sound():
    if shutil.which("canberra-gtk-play"):
        try:
            subprocess.Popen(["canberra-gtk-play", "-i", "complete"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

# ----------------------------- GObject Model -----------------------------
class Profile(GObject.Object):
    name = GObject.Property(type=str, default="")
    servers = GObject.Property(type=str, default="")
    latency = GObject.Property(type=float, default=0.0)

# ----------------------------- Dialogs -----------------------------
class AddProfileDialog(Gtk.Dialog):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True)
        self.set_title("Add DNS Profile")
        box = self.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(12); box.set_margin_bottom(12); box.set_margin_start(12); box.set_margin_end(12)

        intro = Gtk.Label(label="Enter profile name and two DNS servers (primary + secondary):", xalign=0)
        intro.add_css_class("title-4")
        box.append(intro)

        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name_lbl = Gtk.Label(label="Name", xalign=0)
        name_lbl.add_css_class("dim-label")
        self.name_entry = Gtk.Entry(placeholder_text="e.g. Cloudflare")
        self.name_entry.set_hexpand(True)
        name_row.append(name_lbl); name_row.append(self.name_entry)
        box.append(name_row)

        def make_dns_entry(ph):
            entry = Gtk.Entry(placeholder_text=ph)
            entry.set_hexpand(True)
            entry.connect("changed", self._on_dns_changed)
            entry.set_input_hints(Gtk.InputHints.NO_SPELLCHECK)
            return entry

        dns_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.primary_entry = make_dns_entry("Primary DNS (e.g. 1.1.1.1)")
        self.secondary_entry = make_dns_entry("Secondary DNS (e.g. 1.0.0.1)")
        dns_row.append(self.primary_entry); dns_row.append(self.secondary_entry)
        box.append(dns_row)

        self.error_label = Gtk.Label(xalign=0)
        self.error_label.add_css_class("error")
        box.append(self.error_label)

        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        ok_btn = self.add_button("Add", Gtk.ResponseType.OK)
        ok_btn.add_css_class("suggested-action")

    def _on_dns_changed(self, entry):
        text = entry.get_text().strip()
        if not text:
            entry.remove_css_class("error")
            self.error_label.set_text("")
            return
        if is_valid_ip(text):
            entry.remove_css_class("error")
            self.error_label.set_text("")
        else:
            entry.add_css_class("error")
            self.error_label.set_text("Invalid IP format")

    def get_values(self):
        name = (self.name_entry.get_text() or "").strip()
        p = (self.primary_entry.get_text() or "").strip()
        s = (self.secondary_entry.get_text() or "").strip()
        servers = [x for x in (p, s) if x]
        return name, servers

# ----------------------------- Main Window -----------------------------
class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("DNS Jumper")
        self.set_default_size(820, 520)
        self._latency_measured = False

        self.profiles = load_profiles()
        self.store = Gio.ListStore.new(Profile)
        for p in self.profiles:
            obj = Profile()
            obj.set_property("name", p.get("name", ""))
            obj.set_property("servers", ", ".join(p.get("servers", [])))
            obj.set_property("latency", 0.0)
            self.store.append(obj)

        header = Adw.HeaderBar()
        add_btn = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add_btn.set_tooltip_text("Add DNS profile")
        add_btn.connect("clicked", self.on_add_profile)
        self.test_btn = Gtk.Button.new_with_label("Test All")
        self.test_btn.set_tooltip_text("Measure DNS latency for all profiles")
        self.test_btn.connect("clicked", self.on_test_all)
        self.set_btn = Gtk.Button.new_with_label("Set DNS")
        self.set_btn.set_tooltip_text("Apply selected DNS via NetworkManager")
        self.set_btn.connect("clicked", self.on_set_selected)
        header.pack_start(add_btn); header.pack_start(self.test_btn); header.pack_end(self.set_btn)

        # column headers aligned with list columns
        header_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header_row.set_margin_top(8); header_row.set_margin_bottom(8); header_row.set_margin_start(8); header_row.set_margin_end(8)
        name_h = Gtk.Label(label="Profile", xalign=0); name_h.set_hexpand(True); name_h.add_css_class("heading")
        servers_h = Gtk.Label(label="Servers", xalign=0); servers_h.set_hexpand(True); servers_h.add_css_class("heading")
        latency_h = Gtk.Label(label="Latency", xalign=1); latency_h.set_width_chars(8); latency_h.add_css_class("heading")
        header_row.append(name_h); header_row.append(servers_h); header_row.append(latency_h)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self.on_factory_setup)
        factory.connect("bind", self.on_factory_bind)

        self.selection = Gtk.SingleSelection(model=self.store)
        self.listview = Gtk.ListView(model=self.selection, factory=factory)
        self.listview.set_vexpand(True); self.listview.set_hexpand(True)

        scroller = Gtk.ScrolledWindow(); scroller.set_child(self.listview); scroller.set_vexpand(True); scroller.set_hexpand(True)

        self.sort_btn = Gtk.Button.new_with_label("Sort by Latency")
        self.sort_btn.set_sensitive(False)
        self.sort_btn.set_tooltip_text("Run “Test All” first")
        self.sort_btn.connect("clicked", self.on_sort_latency)

        actions_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions_row.set_margin_top(6); actions_row.set_margin_bottom(8); actions_row.set_margin_start(8); actions_row.set_margin_end(8)
        actions_row.append(self.sort_btn)

        self.status_page = Adw.StatusPage(icon_name="network-workgroup-symbolic", title="No DNS profiles yet", description="Click + to add a profile.")
        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content_box.append(header_row); content_box.append(scroller); content_box.append(actions_row)
        stack.add_named(self.status_page, "empty"); stack.add_named(content_box, "list")
        self._stack = stack
        self._update_stack_visibility()

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(stack)
        overlay = Adw.ToastOverlay(); overlay.set_child(toolbar_view); self._toast_overlay = overlay
        self.set_content(overlay)

        self._sort_asc = True

    # ----------------- List item UI -----------------
    def on_factory_setup(self, _factory, list_item):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_top(6); row.set_margin_bottom(6); row.set_margin_start(8); row.set_margin_end(8)

        name_lbl = Gtk.Label(xalign=0); name_lbl.set_hexpand(True); name_lbl.add_css_class("title-5")
        servers_lbl = Gtk.Label(xalign=0); servers_lbl.set_hexpand(True); servers_lbl.add_css_class("monospace")
        latency_lbl = Gtk.Label(xalign=1); latency_lbl.set_width_chars(8)

        del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        del_btn.set_tooltip_text("Delete profile"); del_btn.add_css_class("destructive-action")

        row.append(name_lbl); row.append(servers_lbl); row.append(latency_lbl); row.append(del_btn)
        list_item.set_child(row)

        list_item.name_lbl = name_lbl; list_item.servers_lbl = servers_lbl; list_item.latency_lbl = latency_lbl; list_item.del_btn = del_btn

        def on_delete_clicked(_btn):
            item = list_item.get_item()
            if not item: return
            idx = self._index_of_item(item)
            if idx is None: return
            try:
                self.profiles.pop(idx)
                save_profiles(self.profiles)
                self.store.remove(idx)
                self._update_stack_visibility()
                self._info_toast("Profile deleted")
            except Exception as e:
                self._info_toast(f"Delete failed: {e}")

        del_btn.connect("clicked", on_delete_clicked)

    def on_factory_bind(self, _factory, list_item):
        item = list_item.get_item()
        if not item: return
        name = item.get_property("name") or ""
        servers = item.get_property("servers") or ""
        latency = item.get_property("latency") or 0.0
        list_item.name_lbl.set_text(name)
        list_item.servers_lbl.set_text(servers)
        list_item.latency_lbl.set_text(f"{latency:.0f} ms" if latency > 0 else "—")

    def _index_of_item(self, item_obj):
        n = self.store.get_n_items()
        for i in range(n):
            if self.store.get_item(i) is item_obj:
                return i
        return None

    def _get_selected_index(self):
        sel = self.listview.get_model()
        if isinstance(sel, Gtk.SingleSelection):
            return sel.get_selected()
        return -1

    def _info_toast(self, text):
        try:
            t = Adw.Toast.new(text)
            self._toast_overlay.add_toast(t)
        except Exception:
            print(text)

    def _update_stack_visibility(self):
        if self.store.get_n_items() == 0:
            self._stack.set_visible_child_name("empty")
        else:
            self._stack.set_visible_child_name("list")

    # ----------------- Actions -----------------
    def on_add_profile(self, *_):
        dlg = AddProfileDialog(self)
        dlg.connect("response", self._on_add_profile_response)
        dlg.present()

    def _on_add_profile_response(self, dialog, response):
        try:
            if response == Gtk.ResponseType.OK:
                name, servers = dialog.get_values()
                if not name:
                    self._info_toast("Enter a profile name.")
                    return
                try:
                    servers = sanitize_servers(servers)
                except Exception as e:
                    self._info_toast(str(e))
                    return
                self.profiles.append({"name": name, "servers": servers})
                save_profiles(self.profiles)
                obj = Profile(); obj.set_property("name", name); obj.set_property("servers", ", ".join(servers)); obj.set_property("latency", 0.0)
                self.store.append(obj)
                self._update_stack_visibility()
                self._info_toast("Profile added")
        finally:
            dialog.destroy()

    def on_test_all(self, *_):
        if dns is None:
            self._info_toast("Install dnspython: sudo apt install python3-dnspython")
            return

        self.test_btn.set_sensitive(False); self.test_btn.set_label("Testing…")
        self.sort_btn.set_sensitive(False)
        self.sort_btn.set_tooltip_text("Run “Test All” first")
        def worker():
            any_latency = False
            n = self.store.get_n_items()
            for i in range(n):
                try:
                    obj = self.store.get_item(i)
                    servers = [s.strip() for s in (obj.get_property("servers") or "").split(",") if s.strip()]
                    latency = measure_dns_latency(servers) or 0.0
                    if latency and latency > 0:
                        any_latency = True
                    def update(idx=i, val=latency):
                        try:
                            o = self.store.get_item(idx)
                            o.set_property("latency", float(val))
                        except Exception:
                            pass
                    GLib.idle_add(update)
                except Exception:
                    pass
            def finish():
                self._latency_measured = any_latency
                self.test_btn.set_label("Test All"); self.test_btn.set_sensitive(True)
                if any_latency:
                    self.sort_btn.set_sensitive(True); self.sort_btn.set_tooltip_text("Sort profiles by measured latency")
                    self._info_toast("Tests completed")
                else:
                    self.sort_btn.set_sensitive(False); self.sort_btn.set_tooltip_text("No latency results — check network or servers")
                    self._info_toast("No latency results (network/servers?)")
                # Force ListView to rebind and show updated latencies
                self.selection = Gtk.SingleSelection(model=self.store)
                self.listview.set_model(self.selection)
            GLib.idle_add(finish)
        threading.Thread(target=worker, daemon=True).start()

    def on_sort_latency(self, *_):
        if not self._latency_measured:
            self._info_toast("Run “Test All” first to get latency results")
            return
        rows = []
        n = self.store.get_n_items()
        for i in range(n):
            o = self.store.get_item(i)
            rows.append((o.get_property("name"), o.get_property("servers"), o.get_property("latency") or 0.0))
        if all((r[2] or 0.0) <= 0.0 for r in rows):
            self._info_toast("No valid latency to sort")
            return
        rows.sort(key=lambda r: ((r[2] if r[2] > 0 else 1e9), r[0]), reverse=not self._sort_asc)
        self._sort_asc = not self._sort_asc
        new_profiles = []; new_store = Gio.ListStore.new(Profile)
        for name, servers, latency in rows:
            p = Profile(); p.set_property("name", name); p.set_property("servers", servers); p.set_property("latency", latency)
            new_store.append(p)
            servers_list = [s.strip() for s in servers.split(",") if s.strip()]
            new_profiles.append({"name": name, "servers": servers_list})
        self.profiles = new_profiles; save_profiles(self.profiles)
        self.store = new_store
        self.selection = Gtk.SingleSelection(model=self.store)
        self.listview.set_model(self.selection)
        self._info_toast("Sorted")

    def on_set_selected(self, *_):
        idx = self._get_selected_index()
        if idx < 0:
            self._info_toast("Select a profile first")
            return
        profile_obj = self.store.get_item(idx)
        servers_str = profile_obj.get_property("servers") or ""
        servers = [s.strip() for s in servers_str.split(",") if s.strip()]

        self.set_btn.set_sensitive(False); self.set_btn.set_label("Applying…")
        def worker():
            try:
                apply_dns_with_one_pkexec(servers)
                GLib.idle_add(lambda: self._info_toast("DNS applied"))
                GLib.idle_add(play_success_sound)
            except Exception as e:
                GLib.idle_add(lambda: self._info_toast(str(e)))
            finally:
                GLib.idle_add(lambda: (self.set_btn.set_label("Set DNS"), self.set_btn.set_sensitive(True)))
        threading.Thread(target=worker, daemon=True).start()

# ----------------------------- Application -----------------------------
class DNSJumperApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
    def do_activate(self):
        win = MainWindow(self)
        win.present()

if __name__ == "__main__":
    app = DNSJumperApp()
    sys.exit(app.run(sys.argv))
