import sys
import os
import re
import json
import platform
import html
import traceback
import time
import ctypes
import subprocess
from datetime import datetime
from PyQt6.QtCore import (
    Qt, QRectF, QPointF, QPropertyAnimation, QEasingCurve, QTimer, QThread,
    QObject, QUrl, pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import QPainter, QColor, QPen, QPixmap, QFont, QIcon, QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QFrame,
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QPlainTextEdit, QLineEdit,
)

ORANGE = QColor("#FF8C00")  # orange néon
RADIUS = 22

APP_VERSION = "1.0"
DISCORD_URL = "https://discord.gg/y9cBRFAMPw"
GITHUB_URL = "https://github.com/HeroTweaks"

# Vérification des mises à jour : "propriétaire/dépôt" du GitHub où tu publies les versions
# (onglet Releases). Exemple : "HeroTweaks/nom-du-depot". Laisse vide pour la désactiver.
UPDATE_REPO = ""
UPDATE_API = "https://api.github.com/repos/{repo}/releases/latest"
# Résultat de la dernière vérification : (niveau, texte, lien)
UPDATE_STATE = ("off", "Update checks aren't set up yet.", "")


# ----------------------------------------------------------------------------
#  Moteur Game Mode (Windows uniquement ; nécessite : pip install psutil)
# ----------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "herotweaks_gamemode_state.json")

# Applis gardées ouvertes en plus de l'app elle-même.
KEEP_EXES = {"discord.exe"}
# Ton jeu s'il est déjà lancé en fenêtré (ex. {"RobloxPlayerBeta.exe"}).
# Les jeux en plein écran / fenêtré sans bordure sont gardés automatiquement.
GAME_EXES = set()

# Jamais fermés : système, terminaux, pilotes, antivirus, lanceurs et anti-triche.
PROTECTED_NAMES = {
    "explorer.exe", "dwm.exe", "csrss.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "svchost.exe", "smss.exe", "wininit.exe", "fontdrvhost.exe",
    "sihost.exe", "taskhostw.exe", "ctfmon.exe", "runtimebroker.exe",
    "searchhost.exe", "searchapp.exe", "searchindexer.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "textinputhost.exe", "applicationframehost.exe", "systemsettings.exe",
    "securityhealthsystray.exe", "conhost.exe", "cmd.exe", "powershell.exe",
    "pwsh.exe", "windowsterminal.exe", "openconsole.exe",
    "python.exe", "pythonw.exe", "py.exe", "pyw.exe",
}
PROTECTED_PARTS = (
    "nvidia", "nvcontainer", "nvdisplay", "nvspcap", "geforce", "amd", "radeon",
    "realtek", "rtkaud", "igfx", "intel", "logi", "razer", "steelseries",
    "corsair", "msiafterburner", "rtss", "voicemeeter", "defender", "msmpeng",
    "antimalware", "avast", "avg", "kaspersky", "bitdefender", "eset",
    "malwarebytes", "norton", "mcafee", "steam", "epicgames", "epicwebhelper",
    "easyanticheat", "battleye", "vgc", "vgtray", "riot", "roblox",
    "eadesktop", "eabackground", "battle.net", "ubisoft", "uplay",
)

HKCU_TWEAKS = [
    # Mode Jeu de Windows : activé
    (r"Software\Microsoft\GameBar", "AutoGameModeEnabled", 1),
    (r"Software\Microsoft\GameBar", "AllowAutoGameMode", 1),
    # Xbox Game Bar : désactivée
    (r"Software\Microsoft\GameBar", "UseNexusForGameBarEnabled", 0),
    (r"Software\Microsoft\GameBar", "ShowStartupPanel", 0),
    (r"Software\Microsoft\GameBar", "GamepadNexusChordEnabled", 0),
    (r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", 0),
    (r"System\GameConfigStore", "GameDVR_Enabled", 0),
    # Notifications : désactivées + Ne pas déranger
    (r"Software\Microsoft\Windows\CurrentVersion\PushNotifications", "ToastEnabled", 0),
    (r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings",
     "NOC_GLOBAL_SETTING_TOASTS_ENABLED", 0),
    # Effets de transparence : désactivés
    (r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency", 0),
    # Applis en arrière-plan : interdites
    (r"Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications",
     "GlobalUserDisabled", 1),
    (r"Software\Microsoft\Windows\CurrentVersion\Search", "BackgroundAppGlobalToggle", 0),
    # Accessibilité > Effets visuels
    (r"Control Panel\Accessibility", "DynamicScrollbars", 1),            # barres de défilement : plus toujours affichées
    (r"Software\Microsoft\Accessibility", "MaximizeWindowsByDefault", 0),  # applis en mode agrandi : désactivé
]
UPDATE_SERVICES = ("wuauserv", "UsoSvc")
START_MODES = {2: "auto", 3: "demand", 4: "disabled"}
WM_CLOSE = 0x0010


class LogBus(QObject):
    """Relaie les messages du journal vers la console (même depuis un autre thread)."""
    message = pyqtSignal(object)


LOG_BUS = LogBus()
LOG_BUFFER = []
COUNT = {"ok": 0, "warn": 0, "err": 0}   # total depuis l'ouverture / le dernier Clear
RUN = {"ok": 0, "warn": 0, "err": 0}     # compteurs de l'action en cours
ERR_WORDS = ("error", "failed", "could not", "missing", "traceback", "exception", "denied")
WARN_WORDS = ("not disabled", "still disabled", "administrator")


def log(msg, level=None, count=True):
    """Ajoute une ligne à la console. Niveaux : info, ok, warn, err, cmd, head, input, cmderr."""
    text = str(msg)
    if level is None:
        low = text.lower()
        if any(w in low for w in WARN_WORDS):
            level = "warn"
        elif any(w in low for w in ERR_WORDS):
            level = "err"
        elif low.startswith("ok"):
            level = "ok"
        else:
            level = "info"
    if count and level in COUNT:
        COUNT[level] += 1
        RUN[level] += 1
    entry = (datetime.now().strftime("%H:%M:%S"), level, text)
    LOG_BUFFER.append(entry)
    del LOG_BUFFER[:-3000]
    LOG_BUS.message.emit(entry)


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def game_mode_active():
    return sys.platform == "win32" and bool(load_state().get("active"))


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def reg_read(path, name):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as k:
            value, typ = winreg.QueryValueEx(k, name)
            return {"v": value, "t": typ}
    except OSError:
        return None


def reg_write(path, name, value, typ=None):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_DWORD if typ is None else typ, value)


def reg_delete(path, name):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except OSError:
        pass


def service_start_type(svc):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            rf"SYSTEM\CurrentControlSet\Services\{svc}", 0,
                            winreg.KEY_READ) as k:
            return winreg.QueryValueEx(k, "Start")[0]
    except OSError:
        return None


def run_quiet(cmd):
    return run_output(cmd)[0]


GUID_RE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
POWER_PLAN_NAME = "HeroTweak"
POWER_PLAN_DESC = "Plan d'alimentation HeroTweaks : performances maximales"
# Plans de départ, par ordre de préférence : Ultimate Performance, High Performance, plan actuel
POWER_SOURCES = (
    "e9a42b02-d5df-448d-aa00-03f14749eb61",
    "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c",
    "381b4222-f694-41f0-9685-ff5bb260df2e",
)
BALANCED_PLAN = "381b4222-f694-41f0-9685-ff5bb260df2e"
# (sous-groupe, réglage, valeur) appliqués sur secteur ET sur batterie
POWER_SETTINGS = (
    ("SUB_PROCESSOR", "PROCTHROTTLEMIN", 100),   # processeur toujours à fond (min 100 %)
    ("SUB_PROCESSOR", "PROCTHROTTLEMAX", 100),   # max 100 %
    ("SUB_PROCESSOR", "PERFBOOSTMODE", 2),       # boost agressif
    ("SUB_PROCESSOR", "SYSCOOLPOL", 1),          # refroidissement actif
    ("SUB_DISK", "DISKIDLE", 0),                 # disque jamais mis en veille
    ("SUB_USB", "USBSELECTIVESUSPEND", 0),       # USB : pas de suspension sélective
    ("SUB_PCIEXPRESS", "ASPM", 0),               # PCIe : pas d'économie d'énergie
    ("SUB_SLEEP", "STANDBYIDLE", 0),             # jamais de mise en veille
    ("SUB_SLEEP", "HIBERNATEIDLE", 0),           # jamais d'hibernation
    ("SUB_VIDEO", "VIDEOIDLE", 0),               # écran jamais éteint
)


def run_output(cmd):
    """Lance une commande, la note dans la console et retourne (code, sortie)."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log("> " + " ".join(cmd), "cmd")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", creationflags=flags)
    except OSError as ex:
        log(f"Error: command failed to start ({ex})", "err")
        return -1, ""
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        first = out.strip().splitlines()[0] if out.strip() else "aucun détail"
        log(f"exit code {r.returncode}: {first}", "err")
    return r.returncode, out


def active_power_scheme():
    rc, out = run_output(["powercfg", "/getactivescheme"])
    m = GUID_RE.search(out) if rc == 0 else None
    return m.group(0) if m else None


def find_power_plan():
    rc, out = run_output(["powercfg", "/list"])
    for line in out.splitlines():
        if f"({POWER_PLAN_NAME})" in line:
            m = GUID_RE.search(line)
            if m:
                return m.group(0)
    return None


def setup_power_plan(state):
    """Crée (ou réutilise) le plan « HeroTweak », règle ses options et l'active."""
    original = active_power_scheme()
    custom = find_power_plan()
    if original and original != custom:
        state.setdefault("power_original", original)

    if not custom:
        for source in POWER_SOURCES:
            rc, out = run_output(["powercfg", "/duplicatescheme", source])
            m = GUID_RE.search(out) if rc == 0 else None
            if m:
                custom = m.group(0)
                break
    if not custom:
        log("Error: could not create the power plan", "err")
        return

    run_output(["powercfg", "/changename", custom, POWER_PLAN_NAME, POWER_PLAN_DESC])
    for sub, setting, value in POWER_SETTINGS:
        for kind in ("/setacvalueindex", "/setdcvalueindex"):
            run_output(["powercfg", kind, custom, sub, setting, str(value)])
    rc, _ = run_output(["powercfg", "/setactive", custom])
    state["power_custom"] = custom
    if rc == 0:
        log(f"OK  Power plan {POWER_PLAN_NAME} activated", "ok")
    else:
        log("Error: could not activate the power plan", "err")


def restore_power_plan(state):
    """Remet le plan d'alimentation d'origine (le plan HeroTweak reste dans la liste)."""
    original = state.get("power_original")
    if not original:
        return
    rc, _ = run_output(["powercfg", "/setactive", original])
    if rc != 0:
        run_output(["powercfg", "/setactive", BALANCED_PLAN])


def get_client_animation():
    flag = ctypes.c_int(0)
    ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(flag), 0)
    return int(flag.value)


def set_client_animation(on):
    """Effets d'animation de Windows (SPI_SETCLIENTAREAANIMATION)."""
    ctypes.windll.user32.SystemParametersInfoW(0x1043, 0, ctypes.c_void_p(1 if on else 0), 0x3)


def refresh_windows_settings():
    """Prévient Windows que les réglages ont changé (transparence, thème…)."""
    try:
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "ImmersiveColorSet", 2, 1000, ctypes.byref(ctypes.c_ulong()))
    except Exception:
        pass


def _visible_windows():
    """{pid: [(hwnd, plein_ecran), ...]} pour les fenêtres visibles avec un titre."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    ws_caption = 0x00C00000
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    found = {}
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            style = user32.GetWindowLongW(hwnd, -16)
            borderless = (style & ws_caption) != ws_caption
            full = (rect.right - rect.left >= sw and rect.bottom - rect.top >= sh and borderless)
            found.setdefault(pid.value, []).append((hwnd, full))
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    return found


def _is_protected(name, exe, keep):
    n = (name or "").lower()
    if n in PROTECTED_NAMES or n in keep:
        return True
    windir = os.environ.get("WINDIR", r"C:\Windows").lower()
    if exe and exe.lower().startswith(windir):
        return True
    key = n + " " + os.path.basename(os.path.dirname(exe or "")).lower()
    return any(part in key for part in PROTECTED_PARTS)


def close_apps():
    """Ferme les applis (sauf Discord, le jeu et les protégées). Retourne (fermées, arrêtées)."""
    import psutil

    keep = {n.lower() for n in (KEEP_EXES | GAME_EXES)}
    me = psutil.Process()
    safe = {me.pid} | {p.pid for p in me.parents()}
    try:
        my_user = me.username()
    except psutil.Error:
        my_user = None

    def tree(p):
        pids = {p.pid}
        try:
            pids |= {c.pid for c in p.children(recursive=True)}
            pids |= {a.pid for a in p.parents()}
        except psutil.Error:
            pass
        return pids

    windows = _visible_windows()

    # Processus à ne jamais toucher : Discord / jeu déclaré, et tout jeu plein écran
    for p in psutil.process_iter(["pid", "name"]):
        if (p.info["name"] or "").lower() in keep:
            safe |= tree(p)
    for pid, wins in windows.items():
        if any(full for _, full in wins):
            try:
                safe |= tree(psutil.Process(pid))
            except psutil.Error:
                pass

    user32 = ctypes.windll.user32
    closed, stopped = [], []

    # 1) Applis avec fenêtre : fermeture propre (l'appli peut demander d'enregistrer)
    for pid, wins in windows.items():
        if pid in safe:
            continue
        try:
            p = psutil.Process(pid)
            name, exe = p.name(), p.exe()
        except (psutil.Error, OSError):
            continue
        if _is_protected(name, exe, keep):
            continue
        for hwnd, _ in wins:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        closed.append(name)

    time.sleep(1.5)

    # 2) Applis en arrière-plan (sans fenêtre) lancées par l'utilisateur
    for p in psutil.process_iter(["pid", "name", "exe", "username"]):
        info = p.info
        if info["pid"] in safe or info["pid"] in windows:
            continue
        if not info["exe"] or (my_user and info["username"] != my_user):
            continue
        if _is_protected(info["name"], info["exe"], keep):
            continue
        try:
            p.terminate()
            stopped.append(info["name"])
        except (psutil.Error, OSError):
            pass
    return closed, stopped


def apply_game_mode():
    if sys.platform != "win32":
        raise RuntimeError("Game Mode only works on Windows.")

    log("--- Enabling Game Mode ---", "head")
    state = load_state()
    if not state.get("active"):
        state = {
            "active": True,
            "services_changed": False,
            "registry": {},
            "services": {svc: service_start_type(svc) for svc in UPDATE_SERVICES},
        }
    # On mémorise les valeurs d'origine (une seule fois) pour pouvoir tout remettre
    for path, name, _ in HKCU_TWEAKS:
        state["registry"].setdefault(f"{path}|{name}", reg_read(path, name))
    if "client_animation" not in state:
        state["client_animation"] = get_client_animation()
    save_state(state)

    try:
        set_client_animation(False)
    except Exception as ex:
        log(f"Error: animations: {ex}", "err")

    try:
        setup_power_plan(state)
    except Exception as ex:
        log(f"Error: power plan: {ex}", "err")
    save_state(state)

    for path, name, value in HKCU_TWEAKS:
        try:
            reg_write(path, name, value)
            log(f"OK  {name} = {value}", "ok")
        except OSError as ex:
            log(f"Error: registry {name}: {ex}", "err")

    if is_admin():
        for svc in UPDATE_SERVICES:
            run_quiet(["sc", "config", svc, "start=", "disabled"])
            run_quiet(["sc", "stop", svc])
        state["services_changed"] = True
        save_state(state)
        log("OK  Windows Update disabled", "ok")
    else:
        log("Windows Update not disabled: run the app as administrator", "warn")

    refresh_windows_settings()

    try:
        closed, stopped = close_apps()
        log(f"Windows closed: {', '.join(closed) or '-'}")
        log(f"Background apps stopped: {', '.join(stopped) or '-'}")
    except ImportError:
        log("Error: psutil module not found (no apps were closed)", "err")
    log("Game Mode enabled", "ok")
    return "ok"


def restore_game_mode():
    if sys.platform != "win32":
        return "ok"
    state = load_state()
    if not state:
        return "ok"

    log("--- Disabling Game Mode ---", "head")

    for key, original in state.get("registry", {}).items():
        path, name = key.split("|", 1)
        try:
            if original is None:
                reg_delete(path, name)
            else:
                reg_write(path, name, original["v"], original["t"])
            log(f"OK  {name} restored", "ok")
        except OSError as ex:
            log(f"Error restoring {name}: {ex}", "err")
    try:
        restore_power_plan(state)
    except Exception as ex:
        log(f"Error restoring the power plan: {ex}", "err")
    if state.get("client_animation") is not None:
        try:
            set_client_animation(bool(state["client_animation"]))
        except Exception as ex:
            log(f"Error restoring animations: {ex}", "err")
    refresh_windows_settings()

    if state.get("services_changed"):
        if is_admin():
            for svc, start in state.get("services", {}).items():
                mode = START_MODES.get(start)
                if mode:
                    run_quiet(["sc", "config", svc, "start=", mode])
            state["services_changed"] = False
            log("OK  Windows Update re-enabled", "ok")
        else:
            log("Windows Update still disabled: restart the app as administrator, "
                "then turn the switch on and off again to restore it", "warn")

    state["active"] = False
    state["registry"] = {}
    if state.get("services_changed"):
        save_state(state)
    else:
        try:
            os.remove(STATE_FILE)
        except OSError:
            pass
    log("Game Mode disabled", "ok")
    return "ok"


class GameModeWorker(QThread):
    """Applique ou annule le Game Mode sans bloquer l'interface."""

    finished_ok = pyqtSignal(bool, str)

    def __init__(self, enable, parent=None):
        super().__init__(parent)
        self.enable = enable

    def run(self):
        t0 = time.time()
        RUN.update(ok=0, warn=0, err=0)
        ok, msg = True, ""
        try:
            msg = apply_game_mode() if self.enable else restore_game_mode()
        except Exception as ex:
            ok, msg = False, str(ex)
            log("Error:\n" + traceback.format_exc(), "err")
        n_ok, n_warn, n_err = RUN["ok"], RUN["warn"], RUN["err"]
        level = "err" if n_err else ("warn" if n_warn else "ok")
        log(f"Done in {time.time() - t0:.1f}s: {n_ok} OK, "
            f"{n_warn} warning{'s' if n_warn != 1 else ''}, "
            f"{n_err} error{'s' if n_err != 1 else ''}", level, count=False)
        remaining = 1.2 - (time.time() - t0)
        if remaining > 0:
            time.sleep(remaining)
        self.finished_ok.emit(ok, msg)


class CloseButton(QPushButton):
    """Croix blanche dessinée à la main, fine et nette."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(30, 38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("white"), 2.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        cx, cy, ax, ay = 15, 22, 6, 6
        p.drawLine(cx - ax, cy - ay, cx + ax, cy + ay)
        p.drawLine(cx - ax, cy + ay, cx + ax, cy - ay)


class CmdButton(QPushButton):
    """Bouton avec l'image cmd.png : ouvre la console."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(30, 38)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pix = QPixmap(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cmd.png"))
        if not self._pix.isNull():
            self._pix = self._pix.scaled(22, 22, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._pix.isNull():
            p.drawPixmap((self.width() - self._pix.width()) // 2,
                         22 - self._pix.height() // 2, self._pix)
            return
        # Secours si cmd.png est absent : petit terminal dessiné
        pen = QPen(QColor("white"), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawRoundedRect(QRectF(4, 12, 22, 20), 3, 3)
        p.drawLine(9, 19, 13, 22)
        p.drawLine(13, 22, 9, 25)
        p.drawLine(15, 26, 21, 26)


class LinkButton(QPushButton):
    """Petit bouton avec une image (discord.png, github.png…) qui ouvre un lien."""

    def __init__(self, image, url, tooltip, fallback_letter):
        super().__init__()
        self._url = url
        self._letter = fallback_letter
        self._hover = False
        self.setFixedSize(30, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self._pix = QPixmap(os.path.join(os.path.dirname(os.path.abspath(__file__)), image))
        if not self._pix.isNull():
            self._pix = self._pix.scaled(22, 22, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation)
        self.clicked.connect(self._open)

    def _open(self):
        if not self._url:
            log(f"{self.toolTip()}: no link set (edit GITHUB_URL at the top of the script)", "warn")
            return
        QDesktopServices.openUrl(QUrl(self._url))

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(1.0 if self._hover else 0.8)
        if not self._pix.isNull():
            p.drawPixmap((self.width() - self._pix.width()) // 2,
                         (self.height() - self._pix.height()) // 2, self._pix)
            return
        # Secours si l'image est absente : pastille avec une lettre
        p.setPen(QPen(ORANGE, 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(3, 2, 24, 24))
        f = QFont("Segoe UI", 9)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("white"))
        p.drawText(QRectF(3, 2, 24, 24), Qt.AlignmentFlag.AlignCenter, self._letter)


def active_power_plan_name():
    """Nom du plan d'alimentation actif (sans rien écrire dans la console)."""
    try:
        r = subprocess.run(["powercfg", "/getactivescheme"], capture_output=True, text=True,
                           errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return None
    m = re.search(r"\((.+)\)\s*$", (r.stdout or "").strip())
    return m.group(1) if m else None


def system_info():
    """Lignes (libellé, valeur, niveau) affichées à l'ouverture de la console."""
    rows = [
        ("App", f"HeroTweaks Game Mode v{APP_VERSION}", "info"),
        ("Updates", UPDATE_STATE[1], {"ok": "ok", "new": "warn", "err": "warn"}.get(UPDATE_STATE[0], "info")),
        ("System", f"{platform.system()} {platform.release()} ({platform.version()})", "info"),
    ]
    if sys.platform == "win32":
        admin = is_admin()
        rows.append(("Administrator", "yes" if admin else "no (Windows Update can't be disabled)",
                     "ok" if admin else "warn"))
    on = game_mode_active()
    rows.append(("Game Mode", "ON" if on else "OFF", "ok" if on else "info"))
    plan = active_power_plan_name() if sys.platform == "win32" else None
    if plan:
        rows.append(("Power plan", plan, "info"))
    return rows


def status_text():
    if sys.platform != "win32":
        return "Not available: Game Mode only works on Windows."
    start = service_start_type("wuauserv")
    wu = {2: "automatic", 3: "manual", 4: "disabled"}.get(start, "unknown")
    return "\n".join((
        f"Game Mode       {'ON' if game_mode_active() else 'OFF'}",
        f"Administrator   {'yes' if is_admin() else 'no'}",
        f"Power plan      {active_power_plan_name() or 'unknown'}",
        f"Windows Update  {wu}",
    ))


def _version_tuple(text):
    nums = re.findall(r"\d+", str(text))
    return tuple(int(n) for n in nums[:4]) or (0,)


def check_for_updates():
    """Interroge GitHub (dernière Release). Retourne (niveau, texte, lien).

    Niveaux : ok (à jour), new (mise à jour dispo), info (aucune release), err (échec), off.
    """
    if not UPDATE_REPO:
        return "off", "Update checks aren't set up yet.", ""
    import urllib.request
    import urllib.error
    releases_page = f"https://github.com/{UPDATE_REPO}/releases"
    req = urllib.request.Request(
        UPDATE_API.format(repo=UPDATE_REPO),
        headers={"User-Agent": "HeroTweaks-GameMode", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            return "info", "No published release found yet.", releases_page
        return "err", "Couldn't check for updates right now. Please try again later.", ""
    except (OSError, ValueError):
        return "err", "Couldn't check for updates. Check your internet connection.", ""

    tag = str(data.get("tag_name") or "").strip()
    url = data.get("html_url") or releases_page
    if tag and _version_tuple(tag) > _version_tuple(APP_VERSION):
        return "new", f"Update available: {tag}. Type update to open the download page.", url
    return "ok", f"You're up to date. Running the latest version (v{APP_VERSION}).", url


class UpdateChecker(QThread):
    """Vérifie les mises à jour sans bloquer l'interface."""

    result = pyqtSignal(str, str, str)

    def run(self):
        try:
            self.result.emit(*check_for_updates())
        except Exception as ex:
            self.result.emit("err", "Couldn't check for updates right now.", "")


class HistoryLineEdit(QLineEdit):
    """Champ de saisie avec historique (flèches haut / bas)."""

    def __init__(self):
        super().__init__()
        self.history = []
        self.index = 0

    def remember(self, text):
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
        self.index = len(self.history)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Up and self.history:
            self.index = max(0, self.index - 1)
            self.setText(self.history[self.index])
        elif e.key() == Qt.Key.Key_Down and self.history:
            self.index = min(len(self.history), self.index + 1)
            self.setText(self.history[self.index] if self.index < len(self.history) else "")
        else:
            super().keyPressEvent(e)


class ConsoleWindow(QWidget):
    """Console maison : étapes, erreurs, filtres et petites commandes."""

    COLORS = {
        "info": "#E6E6E6", "ok": "#7DDC8C", "warn": "#FFB347", "err": "#FF6B6B",
        "cmd": "#8A8A8A", "head": "#FF8C00", "input": "#FFFFFF", "cmderr": "#FF6B6B",
    }
    HELP = (
        "Available commands:\n"
        "  help      show this list\n"
        "  status    Game Mode, admin, power plan, updates\n"
        "  system    system information\n"
        "  update    check for updates\n"
        "  clear     clear the console\n"
        "  copy      copy the console content\n"
        "  discord   open the Discord server\n"
        "  github    open the GitHub page\n"
        "  exit      close the console"
    )

    def __init__(self):
        super().__init__()
        self.setWindowTitle("HeroTweaks Console")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(640, 480)
        self._drag_pos = None
        self._filter = "all"
        self._sysinfo = system_info()

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 12, 18)
        root.setSpacing(0)

        # --- En-tête : titre + Discord / GitHub / Copy / Clear + croix ---
        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("HeroTweaks Console")
        title.setStyleSheet("color: white; background: transparent;")
        tf = QFont("Segoe UI", 11)
        tf.setBold(True)
        title.setFont(tf)
        top.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch()

        btn_style = (
            "QPushButton { color: white; background: transparent;"
            " border: 1px solid rgba(255, 140, 0, 130); border-radius: 8px; padding: 3px 12px; }"
        )
        self.btn_discord = LinkButton("discord.png", DISCORD_URL, "Discord", "D")
        self.btn_github = LinkButton("github.png", GITHUB_URL, "GitHub", "G")
        top.addWidget(self.btn_discord, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.btn_github, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addSpacing(4)

        self.btn_copy = QPushButton("Copy")
        self.btn_clear = QPushButton("Clear")
        for b in (self.btn_copy, self.btn_clear):
            b.setStyleSheet(btn_style)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFont(QFont("Segoe UI", 9))
            top.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        self.btn_copy.clicked.connect(self._copy)
        self.btn_clear.clicked.connect(self._clear)

        close = CloseButton()
        close.clicked.connect(self.close)
        top.addWidget(close, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(top)

        root.addSpacing(6)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #3a3a3a; border: none;")
        root.addWidget(sep)
        root.addSpacing(10)

        # --- Filtres + compteurs ---
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.setContentsMargins(0, 0, 8, 0)
        self._chips = {}
        for key, text in (("all", "All"), ("warn", "Warnings"), ("err", "Errors")):
            chip = QPushButton(text)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setFont(QFont("Segoe UI", 9))
            chip.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            self._chips[key] = chip
            bar.addWidget(chip)
        bar.addStretch()
        self.counters = QLabel()
        self.counters.setFont(QFont("Segoe UI", 9))
        self.counters.setStyleSheet("background: transparent;")
        bar.addWidget(self.counters)
        root.addLayout(bar)
        root.addSpacing(8)

        # --- Zone de texte façon terminal ---
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(3000)
        self.view.setFont(QFont("Consolas", 10))
        self.view.setStyleSheet("""
            QPlainTextEdit {
                background: #050505; color: #E6E6E6;
                border: 1px solid #2a2a2a; border-radius: 10px; padding: 8px;
                selection-background-color: #FF8C00; selection-color: black;
            }
            QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 2px 4px 0; }
            QScrollBar::handle:vertical { background: rgba(255, 140, 0, 160); border-radius: 3px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        """)
        root.addWidget(self.view, 1)
        root.addSpacing(8)

        # --- Ligne de commande ---
        prompt_row = QHBoxLayout()
        prompt_row.setSpacing(8)
        prompt_row.setContentsMargins(0, 0, 8, 0)
        prompt = QLabel("hero>")
        prompt.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        prompt.setStyleSheet("color: #FF8C00; background: transparent;")
        prompt_row.addWidget(prompt)
        self.input = HistoryLineEdit()
        self.input.setFont(QFont("Consolas", 10))
        self.input.setPlaceholderText("Type help to list the commands")
        self.input.setStyleSheet("""
            QLineEdit {
                background: #050505; color: white;
                border: 1px solid #2a2a2a; border-radius: 10px; padding: 6px 10px;
                selection-background-color: #FF8C00; selection-color: black;
            }
            QLineEdit:focus { border: 1px solid rgba(255, 140, 0, 200); }
        """)
        self.input.returnPressed.connect(self._submit)
        prompt_row.addWidget(self.input, 1)
        root.addLayout(prompt_row)

        self._updater = None
        self._manual_check = False
        self._render_all()
        LOG_BUS.message.connect(self._append)
        QTimer.singleShot(300, self._start_update_check)   # vérification automatique à l'ouverture

    # ---------------------------------------------------------------- affichage
    def _passes(self, level):
        if self._filter == "warn":
            return level in ("warn", "err")
        if self._filter == "err":
            return level == "err"
        return True

    def _banner(self):
        self.view.appendHtml(
            '<span style="color:#FF8C00;font-weight:bold">HeroTweaks Console</span>')
        self.view.appendHtml(
            '<span style="color:#8A8A8A">Every Game Mode step and error shows up here.</span>')
        self.view.appendHtml("&nbsp;")
        for label, value, level in self._sysinfo:
            self.view.appendHtml(
                '<span style="color:#8A8A8A">%s</span><span style="color:%s">%s</span>' % (
                    html.escape(label.ljust(15)).replace(" ", "&nbsp;"),
                    self.COLORS[level], html.escape(value)))
        self.view.appendHtml("&nbsp;")
        self.view.appendHtml('<span style="color:#8A8A8A">Type help to list the commands.</span>')
        self.view.appendHtml("&nbsp;")

    def _render_entry(self, entry):
        ts, level, msg = entry
        color = self.COLORS.get(level, self.COLORS["info"])
        weight = "bold" if level == "head" else "normal"
        for i, line in enumerate(str(msg).splitlines() or [""]):
            prefix = f"[{ts}] " if i == 0 else " " * (len(ts) + 3)
            self.view.appendHtml(
                '<span style="color:#5C5C5C">%s</span>'
                '<span style="color:%s;font-weight:%s">%s</span>' % (
                    html.escape(prefix).replace(" ", "&nbsp;"), color, weight,
                    html.escape(line).replace(" ", "&nbsp;"),
                )
            )

    def _scroll_down(self):
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _render_all(self):
        self.view.clear()
        self._banner()
        for entry in list(LOG_BUFFER):
            if self._passes(entry[1]):
                self._render_entry(entry)
        self._update_chips()
        self._update_counters()
        self._scroll_down()

    def _append(self, entry):
        if self._passes(entry[1]):
            self._render_entry(entry)
            self._scroll_down()
        self._update_counters()

    def _update_chips(self):
        for key, chip in self._chips.items():
            if key == self._filter:
                chip.setStyleSheet(
                    "QPushButton { color: white; background: rgba(255, 140, 0, 50);"
                    " border: 1px solid #FF8C00; border-radius: 9px; padding: 2px 12px; }")
            else:
                chip.setStyleSheet(
                    "QPushButton { color: #B5B5B5; background: transparent;"
                    " border: 1px solid #333333; border-radius: 9px; padding: 2px 12px; }"
                    "QPushButton:hover { border: 1px solid rgba(255, 140, 0, 130); color: white; }")

    def _update_counters(self):
        e, w = COUNT["err"], COUNT["warn"]
        self.counters.setText(
            '<span style="color:#FF6B6B">●</span> '
            '<span style="color:#CFCFCF">%d error%s</span>&nbsp;&nbsp;&nbsp;'
            '<span style="color:#FFB347">●</span> '
            '<span style="color:#CFCFCF">%d warning%s</span>' % (
                e, "" if e == 1 else "s", w, "" if w == 1 else "s"))

    def _set_filter(self, key):
        self._filter = key
        self._render_all()

    # ---------------------------------------------------------------- commandes
    def _submit(self):
        text = self.input.text().strip()
        self.input.clear()
        if not text:
            return
        self.input.remember(text)
        if self._filter != "all":
            self._filter = "all"
            self._render_all()
        log(f"hero> {text}", "input", count=False)
        self._run_command(text.lower().split()[0])

    def _run_command(self, cmd):
        if cmd == "help":
            log(self.HELP, "info", count=False)
        elif cmd == "status":
            log(status_text(), "info", count=False)
        elif cmd == "system":
            self._sysinfo = system_info()
            lines = "\n".join(f"{label:<15}{value}" for label, value, _ in self._sysinfo)
            log(lines, "info", count=False)
        elif cmd == "update":
            self._start_update_check(manual=True)
        elif cmd == "clear":
            self._clear()
        elif cmd == "copy":
            self._copy()
            log("Console content copied to the clipboard.", "ok", count=False)
        elif cmd == "discord":
            self.btn_discord.click()
        elif cmd == "github":
            self.btn_github.click()
        elif cmd in ("exit", "quit"):
            self.close()
        else:
            log(f"'{cmd}' is not a known command. Type help to list the commands.",
                "cmderr", count=False)

    def _start_update_check(self, manual=False):
        global UPDATE_STATE
        if getattr(self, "_updater", None) is not None and self._updater.isRunning():
            return
        self._manual_check = manual
        if UPDATE_REPO:
            UPDATE_STATE = ("checking", "Checking for updates...", "")
            if manual:
                log("Checking for updates...", "info", count=False)
            self._sysinfo = system_info()
            self._render_all()
        self._updater = UpdateChecker(self)
        self._updater.result.connect(self._on_update_result)
        self._updater.start()

    def _on_update_result(self, level, text, url):
        global UPDATE_STATE
        UPDATE_STATE = (level, text, url)
        self._sysinfo = system_info()
        self._render_all()
        if self._manual_check:
            log(text, {"ok": "ok", "new": "warn", "err": "warn"}.get(level, "info"), count=False)
            if level == "new" and url:
                QDesktopServices.openUrl(QUrl(url))
        self._updater = None

    def _copy(self):
        QApplication.clipboard().setText(self.view.toPlainText())

    def _clear(self):
        LOG_BUFFER.clear()
        COUNT.update(ok=0, warn=0, err=0)
        self._sysinfo = system_info()
        self._render_all()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(5, 5, -5, -5)
        p.setBrush(QColor("#000000"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, RADIUS, RADIUS)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for width, alpha in ((7, 16), (5, 32), (3, 70)):
            g = QColor(ORANGE)
            g.setAlpha(alpha)
            p.setPen(QPen(g, width))
            p.drawRoundedRect(rect, RADIUS, RADIUS)
        p.setPen(QPen(QColor("#FFB347"), 1.2))
        p.drawRoundedRect(rect, RADIUS, RADIUS)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None


class Spinner(QWidget):
    """Petit cercle de chargement orange qui tourne."""

    def __init__(self, size=22):
        super().__init__()
        self._size = size
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self):
        self._angle = 0
        self.show()
        self.raise_()
        self._timer.start(16)

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 8) % 360
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = max(2.4, self._size / 12)
        m = w * 2.2
        rect = QRectF(m, m, self._size - 2 * m, self._size - 2 * m)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor("#2a2a2a"), w))
        p.drawEllipse(rect)
        start = int(-self._angle * 16)
        span = 100 * 16
        # Lueur néon sous l'arc
        for gw, alpha in ((w * 2.6, 35), (w * 1.8, 70)):
            g = QColor(ORANGE)
            g.setAlpha(alpha)
            gpen = QPen(g, gw)
            gpen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(gpen)
            p.drawArc(rect, start, span)
        pen = QPen(ORANGE, w)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(rect, start, span)


class Toggle(QWidget):
    """Switch gris ; orange néon avec lueur quand il est activé."""

    def __init__(self, checked=False):
        super().__init__()
        self.setFixedSize(58, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._checked = checked
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"pos_value", self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)

    def get_pos(self):
        return self._pos

    def set_pos(self, v):
        self._pos = v
        self.update()

    pos_value = pyqtProperty(float, get_pos, set_pos)
    toggled = pyqtSignal(bool)

    def isChecked(self):
        return self._checked

    def _animate_to(self, checked):
        self._checked = checked
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def setChecked(self, checked):
        """Change l'état sans émettre le signal toggled."""
        if checked != self._checked:
            self._animate_to(checked)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._animate_to(not self._checked)
            self.toggled.emit(self._checked)
        e.accept()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._pos
        off = QColor("#3a3a3a")
        col = QColor(
            int(off.red() + (ORANGE.red() - off.red()) * t),
            int(off.green() + (ORANGE.green() - off.green()) * t),
            int(off.blue() + (ORANGE.blue() - off.blue()) * t),
        )
        track = QRectF(6, 6, 46, 20)
        # Lueur néon (visible seulement quand le switch est activé)
        if t > 0:
            p.setBrush(Qt.BrushStyle.NoBrush)
            for width, alpha in ((10, 14), (7, 28), (4, 60)):
                g = QColor(ORANGE)
                g.setAlpha(int(alpha * t))
                p.setPen(QPen(g, width))
                p.drawRoundedRect(track, 10, 10)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(track, 10, 10)
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(16 + t * 26, 16), 8, 8)


class GameModeWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("HeroTweaks Game Mode")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(420, 440)
        self._drag_pos = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 26, 14, 18)
        root.setSpacing(0)

        # --- Haut : logo + titre à gauche, croix à droite ---
        top = QHBoxLayout()
        top.setSpacing(12)

        self.logo = QLabel()
        self.logo.setFixedSize(56, 56)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "HEROTWEAKSLOGO.png")
        pix = QPixmap(path)
        if not pix.isNull():
            self.logo.setPixmap(
                pix.scaled(56, 56, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.SmoothTransformation)
            )
        top.addWidget(self.logo, 0, Qt.AlignmentFlag.AlignTop)

        self.title = QLabel("HeroTweaks Game Mode")
        self.title.setStyleSheet("color: white; background: transparent;")
        font = QFont("Segoe UI", 12)
        font.setBold(True)
        self.title.setFont(font)
        top.addWidget(self.title, 0, Qt.AlignmentFlag.AlignVCenter)

        top.addStretch()

        self._console = None
        buttons = QHBoxLayout()
        buttons.setSpacing(2)
        buttons.setContentsMargins(0, 0, 6, 0)
        self.btn_cmd = CmdButton()
        self.btn_cmd.clicked.connect(self._open_console)
        buttons.addWidget(self.btn_cmd, 0, Qt.AlignmentFlag.AlignTop)
        self.btn_close = CloseButton()
        self.btn_close.clicked.connect(self.close)
        buttons.addWidget(self.btn_close, 0, Qt.AlignmentFlag.AlignTop)
        top.addLayout(buttons)

        root.addLayout(top)

        # --- Barre de séparation grise ---
        root.addSpacing(10)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: #3a3a3a; border: none;")
        root.addWidget(sep)
        root.addSpacing(16)

        # --- Section : engrenage + "General Tweaks" en orange néon ---
        section = QHBoxLayout()
        section.setContentsMargins(0, 0, 0, 0)
        section.setSpacing(8)

        gear = QLabel()
        gear.setFixedSize(30, 30)
        gear.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gear_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "herotweaks_gear.png")
        gear_pix = QPixmap(gear_path)
        if not gear_pix.isNull():
            gear.setPixmap(
                gear_pix.scaled(30, 30, Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
            )
        section.addWidget(gear)

        section_title = QLabel("General Tweaks")
        section_title.setStyleSheet("color: #FF8C00; background: transparent;")
        sfont = QFont("Segoe UI", 12)
        sfont.setBold(True)
        section_title.setFont(sfont)
        glow = QGraphicsDropShadowEffect(section_title)
        glow.setBlurRadius(14)
        glow.setOffset(0, 0)
        glow.setColor(QColor(255, 140, 0, 210))
        section_title.setGraphicsEffect(glow)
        section.addWidget(section_title)
        section.addStretch()

        root.addLayout(section)
        root.addSpacing(14)

        # --- Ligne : "Max Performance" en blanc + switch à droite ---
        perf_row = QHBoxLayout()
        perf_row.setContentsMargins(0, 0, 0, 0)
        perf_label = QLabel("Max Performance")
        perf_label.setStyleSheet("color: white; background: transparent;")
        perf_label.setFont(QFont("Segoe UI", 11))
        perf_row.addWidget(perf_label)
        perf_row.addStretch()
        # Animation de chargement : posée au milieu de la fenêtre, hors mise en page
        self.perf_spinner = Spinner(64)
        self.perf_spinner.setParent(self)
        self.perf_spinner.move((self.width() - 64) // 2, (self.height() - 64) // 2)
        self.perf_spinner.hide()
        self.perf_toggle = Toggle(game_mode_active())
        perf_row.addWidget(self.perf_toggle)

        self._worker = None
        self.perf_toggle.toggled.connect(self._on_perf_toggled)

        root.addLayout(perf_row)
        root.addStretch()

    def _open_console(self):
        if self._console is None:
            self._console = ConsoleWindow()
        c = self._console
        if not c.isVisible():
            geo = self.frameGeometry()
            screen = QApplication.primaryScreen().availableGeometry()
            x = geo.right() + 12          # à droite de la fenêtre principale
            if x + c.width() > screen.right():
                x = max(screen.left(), geo.left() - c.width() - 12)   # sinon à gauche
            c.move(x, geo.top())
            c.show()
        c.raise_()
        c.activateWindow()
        c.input.setFocus()

    def _on_perf_toggled(self, on):
        # Le switch est bloqué pendant l'application, l'animation tourne au centre
        self.perf_toggle.setEnabled(False)
        self.perf_spinner.start()
        self._worker = GameModeWorker(on, self)
        self._worker.finished_ok.connect(self._on_worker_done)
        self._worker.start()

    def _on_worker_done(self, ok, msg):
        self.perf_spinner.stop()
        self.perf_toggle.setEnabled(True)
        if not ok:
            # Échec : le switch revient à son état précédent
            self.perf_toggle.setChecked(not self.perf_toggle.isChecked())
        self._worker = None

    def closeEvent(self, event):
        if self._worker is not None:
            self._worker.wait()
        if self._console is not None:
            self._console.close()
        super().closeEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        inset = 5
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        p.setBrush(QColor("#000000"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(rect, RADIUS, RADIUS)
        # Lueur néon : plusieurs traits de plus en plus larges et transparents
        p.setBrush(Qt.BrushStyle.NoBrush)
        for width, alpha in ((7, 16), (5, 32), (3, 70)):
            glow = QColor(ORANGE)
            glow.setAlpha(alpha)
            p.setPen(QPen(glow, width))
            p.drawRoundedRect(rect, RADIUS, RADIUS)
        # Trait central très lumineux
        p.setPen(QPen(QColor("#FFB347"), 1.2))
        p.drawRoundedRect(rect, RADIUS, RADIUS)

    # --- Déplacement de la fenêtre en glissant ---
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None


def _excepthook(exc_type, exc, tb):
    log("Unhandled error:\n" + "".join(traceback.format_exception(exc_type, exc, tb)).rstrip(), "err")


if __name__ == "__main__":
    sys.excepthook = _excepthook
    # Sous Windows, il faut un identifiant d'application propre pour que la
    # barre des tâches affiche l'icône de l'app et non celle de Python.
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HeroTweaks.GameMode")
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "HEROTWEAKSLOGO.png")
    icon = QIcon(icon_path)
    app.setWindowIcon(icon)
    w = GameModeWindow()
    w.setWindowIcon(icon)
    w.show()
    sys.exit(app.exec())
